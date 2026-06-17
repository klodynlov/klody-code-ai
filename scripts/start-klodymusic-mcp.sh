#!/usr/bin/env bash
# Démarre le serveur MCP KlodyMusic — analyse musicale de la voix (tessiture, et
# à venir théorie d'accords / idées de chanson). Process séparé du reste du stack
# (isolation crash/domaine). Les libs d'analyse (librosa…) sont importées en lazy.
#
# Usage:
#   ./scripts/start-klodymusic-mcp.sh                  # stdio (par défaut)
#   ./scripts/start-klodymusic-mcp.sh --http           # HTTP sur :8088
#   ./scripts/start-klodymusic-mcp.sh --port 9001      # port HTTP custom

set -euo pipefail

TRANSPORT="${KLODYMUSIC_MCP_TRANSPORT:-stdio}"
PORT="${KLODYMUSIC_MCP_PORT:-8088}"
HOST="${KLODYMUSIC_MCP_HOST:-127.0.0.1}"

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
echo "  KlodyMusic MCP Server (analyse musicale de la voix)"
echo "  Transport : $TRANSPORT"
[[ "$TRANSPORT" == "http" ]] && echo "  Adresse   : http://$HOST:$PORT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

KLODYMUSIC_MCP_TRANSPORT="$TRANSPORT" KLODYMUSIC_MCP_PORT="$PORT" KLODYMUSIC_MCP_HOST="$HOST" \
  exec python -m klody_mcp.klody_music_server
