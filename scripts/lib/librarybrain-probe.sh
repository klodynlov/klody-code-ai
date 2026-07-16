# shellcheck shell=bash
# ─────────────────────────────────────────────────────────────────────────────
#  Sonde LibraryBrain pour les scripts shell — MÊME contrat que services.py::_probe
# ─────────────────────────────────────────────────────────────────────────────
#  Fichier SOURCÉ, jamais exécuté.
#
#  Contrat (identique au Python) :
#    - GET /api/stats, route AUTHENTIFIÉE côté LibraryBrain (api/auth.py).
#    - 200 STRICT           → up            : joignable ET autorisé.
#    - 401/403              → unauthorized  : le port répond, mais Klody est refusé.
#    - tout le reste (000…) → down          : injoignable ou réponse inexploitable.
#
#  Sonder « / » ou « /health » ne réparerait rien : ces préfixes sont exemptés
#  d'auth (_EXEMPT_PREFIXES dans api/auth.py) et répondent 200 pendant que tout
#  /api/ est fermé — le vert resterait aussi faux, juste par un autre chemin.
#
#  « Port ouvert » n'est PAS une preuve de vie et n'a jamais eu le droit de
#  conclure ici : le process peut tenir :8765 en refusant 100 % des appels de
#  Klody. C'est exactement ce que faisait l'ancien repli `lsof -i :8765`.
#
#  Pourquoi un fichier partagé plutôt qu'une copie par script : les deux sondes
#  précédentes étaient fausses, chacune à sa façon (une GET sur une route
#  POST-only qui repliait sur lsof, une GET sur la page HTML hors auth), et rien
#  ne les reliait au contrat qu'elles étaient censées vérifier. Ici les appelants
#  se trompent ensemble ou pas du tout, et une correction du contrat les touche
#  tous les deux.
# ─────────────────────────────────────────────────────────────────────────────

LB_PROBE_UP="up"
LB_PROBE_UNAUTHORIZED="unauthorized"
LB_PROBE_DOWN="down"

# Racine du dépôt Klody, déduite de l'emplacement de CE fichier (scripts/lib/) —
# les deux appelants ne vivent pas au même niveau.
LB_PROBE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Racine du serveur LibraryBrain, dérivée de LIBRARYBRAIN_URL (même variable que
# côté Python — un seul endroit à repointer).
lb_base_url() {
  local url="${LIBRARYBRAIN_URL:-http://127.0.0.1:8765/api/ask}"
  # Miroir de services.py:271 (`rsplit("/api/", 1)[0]`) : on coupe au DERNIER
  # « /api/ », et non le suffixe littéral « /api/ask ». L'ancien
  # `${LB_ROOT%/api/ask}` ne survivait pas à un LIBRARYBRAIN_URL repointé sur une
  # autre route : le chemin restait collé à la racine et la sonde interrogeait
  # une URL qui n'existe pas.
  printf '%s' "${url%/api/*}"
}

# Token partagé, nettoyé. Vide = auth désactivée côté serveur (cas 100 % local).
lb_token() {
  local t="${LIBRARYBRAIN_TOKEN:-}"
  # .strip(), comme config.librarybrain_headers() : le serveur compare avec
  # `secrets.compare_digest` (exact, aucune tolérance), donc une espace ou un \n
  # traîné depuis .env casserait la comparaison — et un \n dans une valeur
  # d'en-tête ferait échouer curl lui-même.
  t="${t#"${t%%[![:space:]]*}"}"
  t="${t%"${t##*[![:space:]]}"}"
  printf '%s' "$t"
}

# Sonde → LB_PROBE_UP / LB_PROBE_UNAUTHORIZED / LB_PROBE_DOWN sur stdout.
# Retourne TOUJOURS 0 : les appelants font `state="$(lb_probe)"` sous `set -e`,
# où un code de retour non nul tuerait le script au lieu de dire « down ».
lb_probe() {
  local base="${1:-}" timeout="${2:-2}" code token
  [[ -n "$base" ]] || base="$(lb_base_url)"
  token="$(lb_token)"

  # bash 3.2 (celui de macOS) + `set -u` : l'expansion d'un tableau VIDE est une
  # erreur fatale. Celui-ci naît déjà rempli, donc il ne l'est jamais.
  local -a curl_args
  curl_args=( -s -o /dev/null -w '%{http_code}' -m "$timeout" )
  if [[ -n "$token" ]]; then
    # En-tête, JAMAIS l'URL : /health échoie les URLs des serveurs, un token en
    # query string fuiterait dans les sorties de statut (cf. config.py).
    curl_args+=( -H "X-API-Token: $token" )
  fi
  # Pas d'en-tête du tout plutôt qu'un en-tête vide (miroir de
  # librarybrain_headers) : « X-API-Token: "" » compterait comme une tentative
  # RATÉE au lieu d'une absence de token, or les deux pannes n'ont pas le même
  # remède (cf. lb_unauthorized_detail).

  # `|| true` : curl sort 7 sur connexion refusée, 28 au timeout — sous `set -e`
  # l'affectation tuerait le script appelant. Une panne de LibraryBrain doit
  # afficher « down », pas faire disparaître le dashboard.
  code="$(curl "${curl_args[@]}" "$base/api/stats" 2>/dev/null || true)"

  case "$code" in
    200)     printf '%s' "$LB_PROBE_UP" ;;
    401|403) printf '%s' "$LB_PROBE_UNAUTHORIZED" ;;
    *)       printf '%s' "$LB_PROBE_DOWN" ;;
  esac
}

# Message actionnable pour un 401 — « aucun token » vs « mauvais token ».
#
# Le diagnostic n'est pas recopié ici : on va le CHERCHER dans
# config.librarybrain_auth_hint(), qui est sa source unique. Son propre docstring
# dit pourquoi — deux surfaces le rendaient déjà (la sonde de services.py et
# search_books), et une troisième copie garantirait qu'une des trois mente après
# la prochaine évolution. C'est très exactement ce qui est arrivé au message
# « Klody n'envoie pas d'en-tête X-API-Token », devenu faux le jour où l'en-tête
# a été câblé. Le contrat de la sonde, lui, est forcément redit en shell (curl ne
# peut pas appeler _probe) ; cette prose-là, non.
#
# Seule la branche 401 paie l'import (~0,1 s) : `up` et `down` n'appellent jamais
# Python. `load_dotenv()` n'écrase pas l'environnement (override=False), donc le
# sous-processus lit le MÊME token que la sonde qui vient d'échouer.
lb_unauthorized_detail() {
  local py="$LB_PROBE_ROOT/.venv/bin/python" hint
  # Le `cd` (dans le sous-shell de la substitution, donc sans effet sur
  # l'appelant) n'est pas cosmétique : pour `python -c`, sys.path[0] est le
  # RÉPERTOIRE COURANT. Sans lui, `import config` échoue dès que start-ui.sh est
  # lancé depuis ailleurs que la racine — et surtout, un config.py traînant dans
  # le CWD serait importé à la place du nôtre. On irait alors chercher le
  # diagnostic dans le mauvais module, ce qui est encore pire que le recopier.
  if [[ -x "$py" ]] \
    && hint="$(cd "$LB_PROBE_ROOT" && "$py" -c 'import config; print(config.librarybrain_auth_hint())' 2>/dev/null)" \
    && [[ -n "$hint" ]]; then
    printf '%s' "$hint"
    return 0
  fi
  # Repli sans venv exploitable : un POINTEUR vers les deux réglages, pas un
  # diagnostic. Trancher entre les deux pannes sans pouvoir lire le token du
  # côté Python, ce serait refabriquer le message qui ment.
  printf '%s' "401 sur /api/ — comparer LIBRARYBRAIN_TOKEN (.env de Klody) et \`api_token\` (config.yaml de LibraryBrain) ; détail : config.librarybrain_auth_hint()."
}
