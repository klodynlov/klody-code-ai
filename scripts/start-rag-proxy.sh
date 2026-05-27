#!/usr/bin/env bash
# Démarre le serveur MCP LibraryBrain (port 8082) puis le RAG Proxy (port 8081).
# Usage : ./scripts/start-rag-proxy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$ROOT_DIR/.venv"

if [[ ! -f "$VENV/bin/activate" ]]; then
  echo "ERROR: virtualenv introuvable — exécuter d'abord : python3.11 -m venv .venv && pip install -r requirements.txt" >&2
  exit 1
fi

source "$VENV/bin/activate"

MCP_PORT="${MCP_PORT:-8082}"
MCP_HOST="${MCP_HOST:-127.0.0.1}"
PROXY_PORT="${PROXY_PORT:-8081}"

echo "→ Démarrage du serveur MCP LibraryBrain sur ${MCP_HOST}:${MCP_PORT}..."
MCP_HOST="$MCP_HOST" MCP_PORT="$MCP_PORT" python "$ROOT_DIR/klody_mcp/server.py" &
MCP_PID=$!

# Attendre que le port MCP soit disponible (max 5 secondes)
READY=0
for i in $(seq 1 10); do
  if nc -z "$MCP_HOST" "$MCP_PORT" 2>/dev/null; then
    READY=1
    break
  fi
  sleep 0.5
done

if [[ $READY -eq 0 ]]; then
  echo "WARNING: MCP server pas encore prêt — le proxy démarre quand même" >&2
fi
echo "✓ MCP server PID=$MCP_PID"

# Nettoyage à l'arrêt
cleanup() {
  echo ""
  echo "→ Arrêt des services..."
  kill "$MCP_PID" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

echo "→ Démarrage du RAG Proxy sur 127.0.0.1:${PROXY_PORT}..."
echo "  Aider doit pointer sur : http://127.0.0.1:${PROXY_PORT}/v1"
echo "  Ctrl+C pour tout arrêter."
echo ""

PROXY_PORT="$PROXY_PORT" python "$ROOT_DIR/scripts/rag-proxy.py"
