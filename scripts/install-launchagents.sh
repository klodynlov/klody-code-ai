#!/bin/sh
# Installe (ou vérifie) les LaunchAgents de klody-code-ai versionnés dans
# launchagents/.
#
# Raison d'être : les scripts de service vivent dans le repo, mais ce qui les
# déclenche vivait uniquement dans ~/Library/LaunchAgents — hors versionnement.
# Sur une machine neuve le script était donc présent et l'agent absent : le
# service ne démarre jamais, sans la moindre erreur. Panne silencieuse.
#
# Usage :
#   install-launchagents.sh            installe / met à jour puis (re)charge
#   install-launchagents.sh --check    signale les écarts, n'écrit rien (CI)
#
# Idempotent : un agent dont le contenu rendu est déjà identique à l'installé
# n'est ni réécrit ni rechargé. Indispensable — un `bootstrap` inconditionnel
# redémarrerait l'API en pleine session de travail.

set -eu

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SRC_DIR="$REPO_ROOT/launchagents"
DEST_DIR="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

# Chemins figés à l'écriture des plists, réécrits pour la machine courante.
# L'ordre compte : le chemin du repo (le plus spécifique) avant le HOME.
ORIG_REPO="/Users/klodynlov/Projets/klody-code-ai"
ORIG_HOME="/Users/klodynlov"

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

[ -d "$SRC_DIR" ] || { echo "introuvable : $SRC_DIR" >&2; exit 1; }

render() {
    # plist versionné -> contenu adapté à cette machine, sur stdout
    sed -e "s|$ORIG_REPO|$REPO_ROOT|g" -e "s|$ORIG_HOME|$HOME|g" "$1"
}

# --- Le code réellement servi ---------------------------------------------
#
# Un plist conforme ne dit RIEN de ce que le service exécute. Les agents
# lancent des scripts de CE dépôt, avec `WorkingDirectory` sur sa racine : le
# code en production est celui de l'ARBRE DE TRAVAIL au moment du démarrage.
# Deux conséquences que la comparaison de plists ne voit pas :
#   1. la branche sortie décide du code servi — « relancer le service pour
#      qu'il prenne main » est faux dès qu'un autre checkout est en cours ;
#   2. un service démarré avant une modification continue de servir l'ancien
#      code, sans que rien ne le signale.
#
# ⚠️ Ces contrôles n'influencent PAS le code de sortie. Diverger d'origin/main
# est l'état NORMAL d'un poste de développement : faire rougir `--check`
# là-dessus le rendrait inutilisable en CI comme en local. Ils ne parlent que
# si des services tournent réellement — donc jamais sur un runner.

service_charge() {
    # « Chargé dans launchd » — à ne pas confondre avec « a un processus ».
    launchctl print "$DOMAIN/$1" >/dev/null 2>&1
}

pid_du_service() {
    # ⚠️ Vide ne veut PAS dire « pas chargé ». Un job `StartInterval` n'a de
    # processus que pendant son tick : entre deux, `launchctl print` n'affiche
    # aucune ligne `pid =`. Avoir confondu les deux m'a fait annoncer que
    # `com.klody.api-watchdog` ne tournait pas, alors qu'il avait 669 exécutions
    # au compteur et un journal d'incidents réels (2026-08-03).
    launchctl print "$DOMAIN/$1" 2>/dev/null |
        awk '/^[[:space:]]*pid = /{print $3; exit}'
}

est_periodique() {
    # $1 = plist. Un job périodique RÉ-EXÉCUTE son programme à chaque tick : il
    # sert donc toujours le code du disque, et la notion de « démarré avant une
    # modification » n'a aucun sens pour lui. Il est écarté du critère de
    # péremption par cette raison-là, pas par accident d'absence de PID.
    grep -q '<key>StartInterval</key>' "$1" 2>/dev/null
}

demarrage_epoch() {
    # `ps -o etimes` n'existe pas sur ce macOS ; on convertit `lstart`.
    l=$(ps -o lstart= -p "$1" 2>/dev/null | sed 's/[[:space:]]*$//')
    [ -n "$l" ] || return 1
    date -j -f "%a %b %d %T %Y" "$l" +%s 2>/dev/null
}

point_d_entree() {
    # Les fichiers PROPRES à un service : son lanceur, et le module que
    # celui-ci exécute. On n'attribue à un service que ce qui lui appartient —
    # sinon une retouche de `reaper_samples.py` déclarerait `gmail-mcp`
    # périmé, et un contrôle qui accuse tout le monde n'est plus lu.
    prog=$(grep -A2 '<key>ProgramArguments</key>' "$1" 2>/dev/null |
               grep -m1 '<string>' |
               sed -e 's/.*<string>//' -e 's|</string>.*||' \
                   -e "s|$ORIG_REPO|$REPO_ROOT|" -e "s|$ORIG_HOME|$HOME|")
    case "$prog" in "$REPO_ROOT"/*) [ -f "$prog" ] && echo "$prog" ;; esac
    mod=$(grep -oE 'python -m [A-Za-z0-9_.]+' "$prog" 2>/dev/null |
              tail -1 | awk '{print $3}')
    if [ -n "$mod" ]; then
        f="$REPO_ROOT/$(echo "$mod" | tr '.' '/').py"
        [ -f "$f" ] && echo "$f"
    fi
    return 0
}

sources_partagees() {
    # Le tronc commun : tout service qui l'importe peut être périmé sans que
    # son point d'entrée ait bougé. Impossible à attribuer sans résoudre le
    # graphe d'imports — on le signale donc en bloc, comme une réserve, et
    # jamais comme une accusation nominative.
    find "$REPO_ROOT/klody_mcp" "$REPO_ROOT/tools" "$REPO_ROOT/agent" \
         "$REPO_ROOT/api" -type f -name '*.py' 2>/dev/null || true
    for f in "$REPO_ROOT/config.py" "$REPO_ROOT/services.py"; do
        [ -f "$f" ] && echo "$f"
    done
    return 0
}

mtime_max() {
    # lit une liste de chemins sur stdin
    tr '\n' '\0' | xargs -0 stat -f '%m' 2>/dev/null | sort -rn | head -1
}

mtime_dependances() {
    # Le code des DÉPENDANCES, pas celui du dépôt — angle mort de tout ce qui
    # précède. Vécu le 2026-08-05 : pip a réécrit `site-packages/openai/` sous
    # l'API vivante (bump 2.41.1 → 2.53.0 de la PR #206), qui a rendu « cannot
    # import name 'path_template' from 'openai._utils' » sur CHAQUE requête ~23 h
    # plus tard. Aucun fichier du dépôt n'avait bougé : la section ci-dessus
    # était verte, et elle avait raison de l'être.
    #
    # On ne date QUE les `*.dist-info` : pip en réécrit un à chaque
    # (dés)installation, et ils sont insensibles aux `__pycache__` — dater le
    # dossier `site-packages` lui-même le rendrait faux dès la première
    # compilation d'un module de premier niveau après le démarrage.
    for sp in "$REPO_ROOT"/.venv/lib/python*/site-packages; do
        [ -d "$sp" ] || continue
        ls -d "$sp"/*.dist-info 2>/dev/null || true
    done | mtime_max
}

verifier_code_servi() {
    residents=""          # démons avec un processus vivant : évaluables
    nb_charges=0
    nb_residents=0
    nb_periodiques=0
    non_charges=""
    for s in "$SRC_DIR"/*.plist; do
        [ -e "$s" ] || continue
        lab=$(basename "$s" .plist)
        if ! service_charge "$lab"; then
            non_charges="$non_charges$lab
"
            continue
        fi
        nb_charges=$((nb_charges + 1))
        if est_periodique "$s"; then
            nb_periodiques=$((nb_periodiques + 1))
            continue
        fi
        p=$(pid_du_service "$lab")
        [ -n "$p" ] || continue
        residents="$residents$lab $p
"
        nb_residents=$((nb_residents + 1))
    done
    # Aucun agent chargé = runner de CI : la section entière se tait.
    [ "$nb_charges" -gt 0 ] || return 0

    echo
    if [ -n "$non_charges" ]; then
        echo "$non_charges" | while read -r lab; do
            [ -n "$lab" ] || continue
            echo "NON CHARGÉ  $lab — plist en place mais absent de launchd."
            echo "            launchctl bootstrap $DOMAIN ~/Library/LaunchAgents/$lab.plist"
        done
    fi
    if git -C "$REPO_ROOT" rev-parse --verify -q origin/main >/dev/null 2>&1; then
        branche=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
        ecarts=$(git -C "$REPO_ROOT" diff --name-only origin/main -- \
                     klody_mcp tools agent api config.py services.py scripts \
                     2>/dev/null | wc -l | tr -d ' ')
        if [ "${ecarts:-0}" -gt 0 ]; then
            echo "ATTENTION  les services exécutent l'arbre de travail (« $branche »), qui"
            echo "           diffère d'origin/main sur $ecarts fichier(s) servi(s) : un"
            echo "           redémarrage mettrait CE code en production, pas celui de main."
            echo "           git diff origin/main -- klody_mcp tools agent api scripts"
        else
            echo "arbre de travail (« $branche ») aligné sur origin/main pour le code servi."
        fi
    else
        echo "note : origin/main introuvable, alignement du code servi non vérifié."
    fi

    # Passage par un fichier : en `sh`, un `while` alimenté par un tube tourne
    # dans un sous-shell, et tout compteur incrémenté dedans est perdu à la
    # sortie de la boucle.
    liste=$(mktemp)
    plus_ancien=""
    echo "$residents" | while read -r lab p; do
        [ -n "$lab" ] || continue
        deb=$(demarrage_epoch "$p" || true)
        [ -n "$deb" ] || continue
        echo "$deb" >> "$liste.debuts"
        entree=$(point_d_entree "$SRC_DIR/$lab.plist" | mtime_max)
        if [ -n "$entree" ] && [ "$deb" -lt "$entree" ]; then
            echo "$lab $p" >> "$liste"
        fi
    done
    while read -r lab p; do
        echo "PÉRIMÉ  $lab (pid $p) — son lanceur ou son module ont changé depuis son"
        echo "        démarrage. Recharger : launchctl bootout $DOMAIN/$lab puis"
        echo "        bootstrap (jamais kickstart)."
    done < "$liste" 2>/dev/null || true
    perimes=$(wc -l < "$liste" 2>/dev/null | tr -d ' ' || echo 0)
    plus_ancien=$(sort -n "$liste.debuts" 2>/dev/null | head -1 || echo "")
    rm -f "$liste" "$liste.debuts"

    partage=$(sources_partagees | mtime_max)
    if [ -n "${plus_ancien:-}" ] && [ -n "${partage:-}" ] &&
           [ "$plus_ancien" -lt "$partage" ]; then
        echo "note : du code partagé (klody_mcp, tools, agent, api, config) a changé depuis"
        echo "       le démarrage du plus ancien démon résident — un service qui l'importe"
        echo "       peut servir du code périmé sans que son point d'entrée ait bougé."
    fi

    deps=$(mtime_dependances)
    if [ -n "${plus_ancien:-}" ] && [ -n "${deps:-}" ] &&
           [ "$plus_ancien" -lt "$deps" ]; then
        echo "DÉPENDANCES  site-packages a été réécrit depuis le démarrage du plus ancien"
        echo "             démon résident : un service peut tenir en mémoire un paquet à"
        echo "             moitié périmé (incident du 2026-08-05). Ce contrôle-ci ne dit"
        echo "             pas LESQUELS — celui qui les nomme, et qui les redémarre :"
        echo "             python scripts/diagnostic_peremption.py"
    fi
    echo "$nb_charges agent(s) chargé(s) : $nb_residents résident(s) dont" \
         "${perimes:-0} au point d'entrée périmé, $nb_periodiques périodique(s)" \
         "(ré-exécutés à chaque tick, jamais périmés)."
    return 0
}

drift=0
installed=0
skipped=0

for src in "$SRC_DIR"/*.plist; do
    [ -e "$src" ] || continue
    label=$(basename "$src" .plist)
    dest="$DEST_DIR/$label.plist"
    tmp=$(mktemp)
    render "$src" > "$tmp"

    if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
        skipped=$((skipped + 1))
        rm -f "$tmp"
        continue
    fi

    if [ "$CHECK_ONLY" -eq 1 ]; then
        if [ -f "$dest" ]; then
            echo "ÉCART   $label (installé ≠ repo)"
            diff -u "$dest" "$tmp" | sed 's/^/    /' || true
        else
            echo "ABSENT  $label (versionné, pas installé)"
        fi
        drift=$((drift + 1))
        rm -f "$tmp"
        continue
    fi

    plutil -lint "$tmp" >/dev/null || { echo "plist invalide : $label" >&2; rm -f "$tmp"; exit 1; }
    mkdir -p "$DEST_DIR"
    mv "$tmp" "$dest"
    chmod 644 "$dest"

    # bootout puis bootstrap : recharge la définition. Le bootout échoue si le
    # service n'était pas chargé, ce qui est le cas nominal d'une 1re install.
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$dest"
    echo "installé  $label"
    installed=$((installed + 1))
done

if [ "$CHECK_ONLY" -eq 1 ]; then
    if [ "$drift" -gt 0 ]; then
        echo "$drift agent(s) en écart, $skipped à jour." >&2
        verifier_code_servi
        exit 1
    fi
    echo "$skipped agent(s) à jour, aucun écart."
    verifier_code_servi
    exit 0
fi

echo "$installed installé(s), $skipped déjà à jour."
