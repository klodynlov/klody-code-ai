#!/usr/bin/env bash
# Démarre le serveur MCP Web — expose des outils web en LECTURE SEULE
# (fetch_url, web_search) pour qu'un client MCP (Klody, Claude Desktop, Cline,
# Zed, autre agent) puisse lire des pages et chercher sur le net.
#
# Aucune authentification requise. Garde-fous : GET only, http/https only,
# IP privées/loopback refusées (anti-SSRF), taille plafonnée.
#
# Usage:
#   ./scripts/start-web-mcp.sh                  # stdio (par défaut)
#   ./scripts/start-web-mcp.sh --http           # HTTP sur :8085
#   ./scripts/start-web-mcp.sh --port 9000      # port HTTP custom

set -euo pipefail

TRANSPORT="${WEB_MCP_TRANSPORT:-stdio}"
PORT="${WEB_MCP_PORT:-8085}"
HOST="${WEB_MCP_HOST:-127.0.0.1}"

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
echo "  Web MCP Server (lecture seule)"
echo "  Transport : $TRANSPORT"
[[ "$TRANSPORT" == "http" ]] && echo "  Adresse   : http://$HOST:$PORT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

WEB_MCP_TRANSPORT="$TRANSPORT" WEB_MCP_PORT="$PORT" WEB_MCP_HOST="$HOST" \
  exec python -m klody_mcp.web_server
