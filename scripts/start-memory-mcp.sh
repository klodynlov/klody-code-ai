#!/usr/bin/env bash
# Démarre le serveur MCP mémoire — expose la mémoire sémantique de Klody (bge-m3
# en process, SQLite + sqlite-vec + FTS5) à un client MCP (Klody, Codex, ChatGPT
# Web, Claude Desktop, Cline…), pour PARTAGER le même contexte entre outils.
#
# 100 % local : aucun modèle de plus, aucune dépendance de plus. Le serveur
# délègue à agent/semantic_memory.py (seule source de vérité). Si le paquet
# klody-memory n'est pas installé, les outils rendent un diagnostic lisible au
# lieu de planter — mais ils ne mémorisent alors rien : installer le moteur
# (pip install -e ~/klody-core/memory sentence-transformers sqlite-vec).
#
# Usage:
#   ./scripts/start-memory-mcp.sh                 # stdio (par défaut)
#   ./scripts/start-memory-mcp.sh --http          # HTTP sur :8095
#   ./scripts/start-memory-mcp.sh --port 9000     # port HTTP custom

set -euo pipefail

TRANSPORT="${MEMORY_MCP_TRANSPORT:-stdio}"
PORT="${MEMORY_MCP_PORT:-8095}"
HOST="${MEMORY_MCP_HOST:-127.0.0.1}"

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
echo "  Memory MCP Server (mémoire sémantique partagée)"
echo "  Transport : $TRANSPORT"
[[ "$TRANSPORT" == "http" ]] && echo "  Adresse   : http://$HOST:$PORT"
echo "  Base      : ${SEMANTIC_MEMORY_DB:-<défaut config>}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

MEMORY_MCP_TRANSPORT="$TRANSPORT" MEMORY_MCP_PORT="$PORT" MEMORY_MCP_HOST="$HOST" \
  exec python -m klody_mcp.memory_server
