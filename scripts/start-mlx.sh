#!/usr/bin/env bash
# Démarre le serveur MLX-LM (API OpenAI-compatible sur port 8080).
# Usage: ./scripts/start-mlx.sh [--model <hf-id>] [--port <port>]
#
# Par défaut: mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2 sur :8080
# Le modèle est téléchargé automatiquement depuis HuggingFace si absent.

set -euo pipefail

MODEL="${MLX_MODEL:-mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2}"
PORT="${MLX_PORT:-8080}"
DRAFT="${MLX_DRAFT_MODEL:-}"

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --model) MODEL="$2"; shift 2 ;;
    --port)  PORT="$2";  shift 2 ;;
    --draft) DRAFT="$2"; shift 2 ;;
    *) echo "Usage: $0 [--model <id>] [--port <n>] [--draft <id>]"; exit 1 ;;
  esac
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Klody MLX Server"
echo "  Model  : $MODEL"
echo "  Port   : $PORT"
[[ -n "$DRAFT" ]] && echo "  Draft  : $DRAFT (speculative decoding)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Activer le venv si présent
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
  source "$SCRIPT_DIR/.venv/bin/activate"
fi

# Construction de la commande
CMD=(python -m mlx_lm.server
  --model "$MODEL"
  --port "$PORT"
  --host "127.0.0.1"
)

# Speculative decoding si draft model fourni
if [[ -n "$DRAFT" ]]; then
  CMD+=(--draft-model "$DRAFT")
fi

echo ""
echo "→ Démarrage : ${CMD[*]}"
echo "→ API dispo sur : http://127.0.0.1:${PORT}/v1"
echo ""

exec "${CMD[@]}"
