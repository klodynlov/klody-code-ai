#!/usr/bin/env bash
# Démarre le serveur MCP VocalBrain — expose la génération de chant (voix clonée)
# pour qu'un client MCP (Klody, Claude Desktop, Cline…) puisse créer des chansons,
# suivre la génération et récupérer le mix + les stems.
#
# Pont léger : appelle le daemon local-suno (FastAPI, :8766) en HTTP. Le daemon
# doit tourner (service com.klody.localsuno-daemon).
#
# Usage:
#   ./scripts/start-vocalbrain-mcp.sh                  # stdio (par défaut)
#   ./scripts/start-vocalbrain-mcp.sh --http           # HTTP sur :8086
#   ./scripts/start-vocalbrain-mcp.sh --port 9000      # port HTTP custom

set -euo pipefail

TRANSPORT="${VOCALBRAIN_MCP_TRANSPORT:-stdio}"
PORT="${VOCALBRAIN_MCP_PORT:-8086}"
HOST="${VOCALBRAIN_MCP_HOST:-127.0.0.1}"

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
echo "  VocalBrain MCP Server (génération de chant)"
echo "  Transport : $TRANSPORT"
[[ "$TRANSPORT" == "http" ]] && echo "  Adresse   : http://$HOST:$PORT"
echo "  Backend   : ${LOCALSUNO_URL:-http://127.0.0.1:8766}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

VOCALBRAIN_MCP_TRANSPORT="$TRANSPORT" VOCALBRAIN_MCP_PORT="$PORT" VOCALBRAIN_MCP_HOST="$HOST" \
  exec python -m klody_mcp.vocalbrain_server
