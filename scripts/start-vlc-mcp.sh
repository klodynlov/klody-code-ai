#!/usr/bin/env bash
# Démarre le serveur MCP VLC — expose le pilotage du lecteur VLC (lecture,
# playlist, volume, seek) pour qu'un client MCP (Klody, Claude Desktop, Cline…)
# puisse contrôler ce qui joue.
#
# Pont léger : appelle l'interface HTTP native de VLC (:8092 par défaut). VLC
# doit tourner AVEC cette interface activée — cf. scripts/setup-vlc-http.sh
# (l'outil demarrer_vlc lance VLC si besoin, mais ne peut pas activer l'iface).
#
# Usage:
#   ./scripts/start-vlc-mcp.sh                  # stdio (par défaut)
#   ./scripts/start-vlc-mcp.sh --http           # HTTP sur :8091
#   ./scripts/start-vlc-mcp.sh --port 9000      # port HTTP custom

set -euo pipefail

TRANSPORT="${VLC_MCP_TRANSPORT:-stdio}"
PORT="${VLC_MCP_PORT:-8091}"
HOST="${VLC_MCP_HOST:-127.0.0.1}"

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
echo "  VLC MCP Server (pilotage du lecteur)"
echo "  Transport : $TRANSPORT"
[[ "$TRANSPORT" == "http" ]] && echo "  Adresse   : http://$HOST:$PORT"
echo "  Backend   : http://${VLC_HTTP_HOST:-127.0.0.1}:${VLC_HTTP_PORT:-8092} (iface HTTP de VLC)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

VLC_MCP_TRANSPORT="$TRANSPORT" VLC_MCP_PORT="$PORT" VLC_MCP_HOST="$HOST" \
  exec python -m klody_mcp.vlc_server
