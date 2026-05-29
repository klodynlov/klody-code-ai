#!/usr/bin/env bash
# Démarre le serveur MCP Gmail — expose les outils Gmail (search_emails,
# read_email, send_email, create_draft, list_labels, modify_labels, …)
# via IMAP/SMTP + mot de passe d'application, pour qu'un client MCP
# (Klody, Claude Desktop, Cline, Zed, autre agent) puisse les utiliser.
#
# Auth : renseigner GMAIL_ADDRESS + GMAIL_APP_PASSWORD dans .env
# (mot de passe d'application Google : https://myaccount.google.com/apppasswords).
#
# Usage:
#   ./scripts/start-gmail-mcp.sh                  # stdio (par défaut)
#   ./scripts/start-gmail-mcp.sh --http           # HTTP sur :8084
#   ./scripts/start-gmail-mcp.sh --port 9000      # port HTTP custom

set -euo pipefail

TRANSPORT="${GMAIL_MCP_TRANSPORT:-stdio}"
PORT="${GMAIL_MCP_PORT:-8084}"
HOST="${GMAIL_MCP_HOST:-127.0.0.1}"

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
echo "  Gmail MCP Server"
echo "  Transport : $TRANSPORT"
[[ "$TRANSPORT" == "http" ]] && echo "  Adresse   : http://$HOST:$PORT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

GMAIL_MCP_TRANSPORT="$TRANSPORT" GMAIL_MCP_PORT="$PORT" GMAIL_MCP_HOST="$HOST" \
  exec python -m klody_mcp.gmail_server
