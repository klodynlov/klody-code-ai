#!/usr/bin/env bash
# Démarre le serveur MCP Klody — expose les outils code-aware
# (find_symbol, find_references, find_relevant_files, run_in_sandbox, …)
# pour qu'un client MCP externe (Continue.dev, Cline, Zed, autre agent)
# puisse les utiliser.
#
# Usage:
#   ./scripts/start-klody-mcp.sh                  # stdio (par défaut)
#   ./scripts/start-klody-mcp.sh --http           # HTTP sur :8087
#   ./scripts/start-klody-mcp.sh --root /path     # projet à exposer (défaut: cwd)

set -euo pipefail

ROOT="${KLODY_MCP_ROOT:-$(pwd)}"
TRANSPORT="${KLODY_MCP_TRANSPORT:-stdio}"
PORT="${KLODY_MCP_PORT:-8087}"  # 8083 entrait en collision avec MLX_CODE_PORT

while [[ $# -gt 0 ]]; do
  case $1 in
    --http)  TRANSPORT="http"; shift ;;
    --port)  PORT="$2"; shift 2 ;;
    --root)  ROOT="$2"; shift 2 ;;
    *) echo "Usage: $0 [--http] [--port <n>] [--root <path>]"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
  source "$SCRIPT_DIR/.venv/bin/activate"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Klody MCP Server"
echo "  Root      : $ROOT"
echo "  Transport : $TRANSPORT"
[[ "$TRANSPORT" == "http" ]] && echo "  Port      : $PORT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

KLODY_MCP_ROOT="$ROOT" KLODY_MCP_TRANSPORT="$TRANSPORT" KLODY_MCP_PORT="$PORT" \
  exec python -m klody_mcp.klody_server
