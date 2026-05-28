#!/usr/bin/env bash
# Démarre le serveur MLX-LM (API OpenAI-compatible sur port 8080).
# Usage: ./scripts/start-mlx.sh [--model <hf-id>] [--port <port>] [--chat-template-args <json>]
#
# Défaut (cerveau) : valeur de MLX_MODEL dans .env (Qwen3.6-35B-A3B 8bit).
# Le modèle est téléchargé automatiquement depuis HuggingFace si absent.
#
# MLX_CHAT_TEMPLATE_ARGS (.env) : JSON passé au gabarit de chat. Sert à couper le
# raisonnement des modèles "thinking" — ex: '{"enable_thinking": false}' pour
# Qwen3.6 (sinon il sur-raisonne). Inoffensif pour les modèles non-thinking.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# .env = source de vérité des modèles/ports. Le LaunchAgent (com.klody.mlx) et
# l'auto-spawn Tauri appellent ce script sans passer MLX_MODEL ; on le lit ici
# pour qu'un seul changement dans .env bascule tout (Klody API + serveur servi).
set -a
[[ -f "$SCRIPT_DIR/.env" ]] && source "$SCRIPT_DIR/.env"
set +a

MODEL="${MLX_MODEL:-unsloth/Qwen3.6-35B-A3B-MLX-8bit}"
PORT="${MLX_PORT:-8080}"
DRAFT="${MLX_DRAFT_MODEL:-}"
CHAT_ARGS="${MLX_CHAT_TEMPLATE_ARGS:-}"

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --model) MODEL="$2"; shift 2 ;;
    --port)  PORT="$2";  shift 2 ;;
    --draft) DRAFT="$2"; shift 2 ;;
    --chat-template-args) CHAT_ARGS="$2"; shift 2 ;;
    *) echo "Usage: $0 [--model <id>] [--port <n>] [--draft <id>] [--chat-template-args <json>]"; exit 1 ;;
  esac
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Klody MLX Server"
echo "  Model  : $MODEL"
echo "  Port   : $PORT"
[[ -n "$DRAFT" ]] && echo "  Draft  : $DRAFT (speculative decoding)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Activer le venv si présent
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

# Args de gabarit de chat (ex: couper le thinking de Qwen3.6)
if [[ -n "$CHAT_ARGS" ]]; then
  CMD+=(--chat-template-args "$CHAT_ARGS")
  echo "  Chat args : $CHAT_ARGS"
fi

echo ""
echo "→ Démarrage : ${CMD[*]}"
echo "→ API dispo sur : http://127.0.0.1:${PORT}/v1"
echo ""

exec "${CMD[@]}"
