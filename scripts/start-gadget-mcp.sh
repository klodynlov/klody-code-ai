#!/usr/bin/env bash
# Démarre le serveur MCP KORG Gadget — pilotage INDIRECT de l'app Gadget.
#
# Gadget n'est pas scriptable ; le serveur (1) lit les projets .gdproj2
# (tonalité, tempo, pistes, instruments — lecture seule) et (2) pilote les
# instruments Gadget installés en VST via le pont REAPER (:9000, le même que
# reaper_server). Voir klody_mcp/gadget_server.py (docstring module).
#
# Usage:
#   ./scripts/start-gadget-mcp.sh                  # stdio (par défaut)
#   ./scripts/start-gadget-mcp.sh --http           # HTTP sur :8093
#   ./scripts/start-gadget-mcp.sh --port 9500      # port HTTP custom

set -euo pipefail

TRANSPORT="${GADGET_MCP_TRANSPORT:-stdio}"
PORT="${GADGET_MCP_PORT:-8093}"
HOST="${GADGET_MCP_HOST:-127.0.0.1}"

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
echo "  KORG Gadget MCP Server (pilotage indirect)"
echo "  Transport : $TRANSPORT"
[[ "$TRANSPORT" == "http" ]] && echo "  Adresse   : http://$HOST:$PORT"
echo "  Backend   : pont REAPER ${REAPER_BRIDGE_HOST:-127.0.0.1}:${REAPER_BRIDGE_PORT:-9000} (instruments VST KORG)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

GADGET_MCP_TRANSPORT="$TRANSPORT" GADGET_MCP_PORT="$PORT" GADGET_MCP_HOST="$HOST" \
  exec python -m klody_mcp.gadget_server
