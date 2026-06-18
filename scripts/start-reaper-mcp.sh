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
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

REAPER_MCP_TRANSPORT="$TRANSPORT" REAPER_MCP_PORT="$PORT" REAPER_MCP_HOST="$HOST" \
  exec python -m klody_mcp.reaper_server
