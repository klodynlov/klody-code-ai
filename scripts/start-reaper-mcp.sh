#!/usr/bin/env bash
# Démarre le serveur MCP REAPER — pilote le DAW REAPER en langage naturel.
# Pont léger : parle au script ReaScript Python qui tourne DANS REAPER
# (reaper_bridge/klody_reaper_bridge.py) via un socket TCP localhost (:9000).
# REAPER doit être lancé ET le script pont chargé/actif (Actions > Load ReaScript).
# Voir reaper_bridge/README.md (config REAPER + test Gate 1).
#
# Usage:
#   ./scripts/start-reaper-mcp.sh                  # stdio (par défaut)
#   ./scripts/start-reaper-mcp.sh --http           # HTTP sur :8089
#   ./scripts/start-reaper-mcp.sh --port 9002      # port HTTP custom

set -euo pipefail

TRANSPORT="${REAPER_MCP_TRANSPORT:-stdio}"
PORT="${REAPER_MCP_PORT:-8089}"
HOST="${REAPER_MCP_HOST:-127.0.0.1}"

# Racines de la bibliothèque de samples (search_samples, workflow_place_sample).
# Posé ici et pas dans un plist : aucun agent launchd ne lance ce serveur, il
# démarre par ce script — c'est donc le seul point qui couvre tous les modes de
# démarrage. Une valeur déjà exportée gagne, le `:-` ne fait que fournir un
# défaut.
#
# ⚠️ `local-suno/samples` contient les 537 segments de la VOIX de l'utilisateur
# enregistrés pour l'entraînement RVC (local-suno/README.md), pas des samples
# musicaux. Les exposer ici est un choix ASSUMÉ : l'agent peut donc placer un
# de ces enregistrements dans un projet REAPER si une requête l'y amène
# (« vocal », « speaking », « voix »). Les retirer de cette liste suffit à
# revenir en arrière — ils resteront indexés et cherchables par
# `samplebrain-index search`, qui lui n'est pas filtré par cette variable.
KLODY_SAMPLES_DIR="${KLODY_SAMPLES_DIR:-$HOME/Desktop/SAMPLES:$HOME/local-suno/samples}"
export KLODY_SAMPLES_DIR

while [[ $# -gt 0 ]]; do
  case $1 in
    --http)  TRANSPORT="http"; shift ;;
    --port)  PORT="$2"; shift 2 ;;
    --host)  HOST="$2"; shift 2 ;;
    *) echo "Usage: $0 [--http] [--port <n>] [--host <addr>]"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
  source "$SCRIPT_DIR/.venv/bin/activate"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  REAPER MCP Server (pilotage du DAW)"
echo "  Transport : $TRANSPORT"
[[ "$TRANSPORT" == "http" ]] && echo "  Adresse   : http://$HOST:$PORT"
echo "  Pont      : ${REAPER_BRIDGE_HOST:-127.0.0.1}:${REAPER_BRIDGE_PORT:-9000} (script dans REAPER)"
echo "  Samples   : $KLODY_SAMPLES_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

REAPER_MCP_TRANSPORT="$TRANSPORT" REAPER_MCP_PORT="$PORT" REAPER_MCP_HOST="$HOST" \
  exec python -m klody_mcp.reaper_server
