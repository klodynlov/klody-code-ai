#!/usr/bin/env bash
# Wrapper pour que Klody puisse distiller un livre sans se prendre le
# SUBPROCESS_TIMEOUT=30s de execute_command : on lance la distillation en
# arrière-plan (mode `start`), puis Klody poll un statut court (mode `status`)
# tant que ce n'est pas fini.
#
# Usage :
#   ./scripts/klody-distill.sh start "<titre>" "<auteur>" "<annee|->" "<domaine>"
#       → écrit logs/distill/<run_id>.{log,pid,meta} en arrière-plan
#       → stdout : RUN_ID=<id>
#
#   ./scripts/klody-distill.sh batch "<skill>" "<domaine>" "<titre|auteur|annee>" [livres...]
#       → distille N livres en séquence puis fusionne en UN skill (background)
#       → stdout : RUN_ID=<id>   (suivre avec await_distillation / status)
#
#   ./scripts/klody-distill.sh status <run_id>
#       → stdout :
#           running    (encore en cours)
#           done <chemin/relatif/du.json>
#           refused <raison>            (livre narratif, code 2 du script)
#           error <message court>       (code 1, voir le log complet)
#
#   ./scripts/klody-distill.sh tail <run_id> [n]
#       → derniers n=80 lignes du log (debug humain, pas pour boucle ReAct)
#
# Pour year inconnue, passer `-` (le wrapper omet --year).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs/distill"
mkdir -p "$LOG_DIR"

cmd="${1:-}"
shift || true

case "$cmd" in

  start)
    if [[ $# -lt 4 ]]; then
      echo "usage: klody-distill.sh start <titre> <auteur> <annee|-> <domaine>" >&2
      exit 64
    fi
    title="$1"; author="$2"; year="$3"; domain="$4"

    # Slug stable pour le run_id (pas de collision si le titre revient).
    slug="$(echo "$title" | tr '[:upper:]' '[:lower:]' \
      | iconv -t ASCII//TRANSLIT 2>/dev/null \
      | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g' | cut -c1-40)"
    ts="$(date +%Y%m%d-%H%M%S)"
    run_id="${ts}-${slug}"
    log="$LOG_DIR/${run_id}.log"
    pidf="$LOG_DIR/${run_id}.pid"
    metaf="$LOG_DIR/${run_id}.meta"

    args=( --title "$title" --author "$author" --domain "$domain" )
    if [[ "$year" != "-" && -n "$year" ]]; then
      args+=( --year "$year" )
    fi

    cd "$ROOT"
    # `source .venv/bin/activate` ne tient pas pour un process en background
    # qui survit au shell parent → on appelle le python du venv directement.
    nohup "$ROOT/.venv/bin/python" scripts/distill_book.py "${args[@]}" \
      > "$log" 2>&1 &
    pid=$!
    echo "$pid" > "$pidf"
    {
      echo "run_id=$run_id"
      echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "title=$title"
      echo "author=$author"
      echo "year=$year"
      echo "domain=$domain"
      echo "pid=$pid"
      echo "log=$log"
    } > "$metaf"
    echo "RUN_ID=$run_id"
    ;;

  batch)
    # Distille PLUSIEURS livres puis fusionne en UN seul skill (séquentiel :
    # mlx-lm ne traite qu'une requête à la fois). Même schéma background que
    # `start` → status / await_distillation fonctionnent à l'identique.
    if [[ $# -lt 3 ]]; then
      echo "usage: klody-distill.sh batch <skill_name> <domaine> \"<titre|auteur|annee>\" [autres livres...]" >&2
      exit 64
    fi
    skill="$1"; domain="$2"; shift 2
    nbooks=$#

    slug="$(echo "$skill" | tr '[:upper:]' '[:lower:]' \
      | iconv -t ASCII//TRANSLIT 2>/dev/null \
      | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g' | cut -c1-40)"
    ts="$(date +%Y%m%d-%H%M%S)"
    run_id="${ts}-${slug}"
    log="$LOG_DIR/${run_id}.log"
    pidf="$LOG_DIR/${run_id}.pid"
    metaf="$LOG_DIR/${run_id}.meta"

    args=( --skill "$skill" --domain "$domain" )
    for book in "$@"; do
      args+=( --book "$book" )
    done

    cd "$ROOT"
    nohup "$ROOT/.venv/bin/python" scripts/distill_books_merge.py "${args[@]}" \
      > "$log" 2>&1 &
    pid=$!
    echo "$pid" > "$pidf"
    {
      echo "run_id=$run_id"
      echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "mode=batch"
      echo "skill=$skill"
      echo "domain=$domain"
      echo "books=$nbooks"
      echo "pid=$pid"
      echo "log=$log"
    } > "$metaf"
    echo "RUN_ID=$run_id"
    ;;

  status)
    run_id="${1:-}"
    if [[ -z "$run_id" ]]; then
      echo "usage: klody-distill.sh status <run_id>" >&2
      exit 64
    fi
    log="$LOG_DIR/${run_id}.log"
    pidf="$LOG_DIR/${run_id}.pid"
    if [[ ! -f "$pidf" ]]; then
      echo "error unknown run_id"
      exit 0
    fi
    pid="$(cat "$pidf")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "running"
      exit 0
    fi
    # Process terminé — lire la fin du log pour décider du verdict.
    # `set -e` + `grep -q` non-matchant = exit fatal → on désactive localement.
    set +e
    last="$(tail -n 40 "$log" 2>/dev/null)"
    written="$(printf '%s\n' "$last" | grep -oE 'skills/distilled/[^ ]+\.json' | tail -n 1)"
    if [[ -n "$written" ]]; then
      # Chemin ABSOLU : la racine sandbox de Klody (config.PROJECT_ROOT) n'est pas
      # forcément klody-code-ai — ici c'est ~/Projets (cf. .env), donc un chemin
      # relatif serait cherché sous ~/Projets, pas sous le dépôt Klody → "Répertoire
      # introuvable". $ROOT vient de BASH_SOURCE : toujours le dépôt Klody.
      echo "done $ROOT/$written"
      exit 0
    fi
    if printf '%s\n' "$last" | grep -q "le distillateur a refusé"; then
      reason="$(printf '%s\n' "$last" | grep "le distillateur a refusé" | tail -n 1 \
        | sed -E 's/.*refusé : //')"
      echo "refused ${reason:-no_reason}"
      exit 0
    fi
    if printf '%s\n' "$last" | grep -q "JSON non conforme"; then
      echo "error schema_invalid (voir logs/distill_book_last_invalid.json)"
      exit 0
    fi
    # Dernière erreur trouvée dans le log
    err="$(printf '%s\n' "$last" | grep -m1 "ERROR" | sed -E 's/.*ERROR *\| *//' \
      | cut -c1-180)"
    echo "error ${err:-process_failed_no_message}"
    ;;

  tail)
    run_id="${1:-}"
    n="${2:-80}"
    log="$LOG_DIR/${run_id}.log"
    [[ -f "$log" ]] || { echo "no log for $run_id" >&2; exit 64; }
    tail -n "$n" "$log"
    ;;

  *)
    cat >&2 <<EOF
usage:
  klody-distill.sh start <titre> <auteur> <annee|-> <domaine>
  klody-distill.sh status <run_id>
  klody-distill.sh tail <run_id> [n]
EOF
    exit 64
    ;;

esac
