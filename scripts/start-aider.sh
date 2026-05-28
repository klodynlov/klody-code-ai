#!/usr/bin/env bash
# Lance Aider sur un projet — backend MLX local (OpenAI-compatible). 100% offline.
# Aucun abonnement, aucune clé cloud, aucune télémétrie sortante.
#
# Usage :
#   ./scripts/start-aider.sh [chemin]        # cerveau Qwen3.6-35B-A3B  (:8080)
#   ./scripts/start-aider.sh code [chemin]   # spécialiste Qwen3-Coder  (:8081)
#
# Démarrer les serveurs au préalable : ./start-local-ai.sh both
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# .env = source de vérité des ids modèles / ports.
set -a; [[ -f "$ROOT/.env" ]] && source "$ROOT/.env"; set +a

PROFILE="brain"
if [[ "${1:-}" == "code" ]]; then PROFILE="code"; shift; fi
PROJECT_PATH="${1:-$PWD}"
[[ $# -gt 0 ]] && shift   # consomme le chemin ; "$@" = args supplémentaires (ex: --message)

if [[ "$PROFILE" == "code" ]]; then
  MODEL_ID="${MLX_CODE_MODEL:-mlx-community/Qwen3-Coder-30B-A3B-Instruct-8bit}"
  PORT="${MLX_CODE_PORT:-8081}"
else
  MODEL_ID="${MLX_MODEL:-unsloth/Qwen3.6-35B-A3B-MLX-8bit}"
  PORT="${MLX_PORT:-8080}"
fi
BASE="http://127.0.0.1:${PORT}/v1"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║         Klody · Aider + MLX (local) — profil: $PROFILE"
echo "║         100% local — aucune donnée envoyée               ║"
echo "╚══════════════════════════════════════════════════════════╝"

# aider dans le venv si pas sur le PATH
if ! command -v aider &>/dev/null; then
  if [[ -f "$ROOT/.venv/bin/aider" ]]; then export PATH="$ROOT/.venv/bin:$PATH"
  else echo "❌ Aider introuvable. source .venv/bin/activate"; exit 1; fi
fi

echo -n "🔗 MLX ($BASE) ... "
if curl -sf "$BASE/models" -o /dev/null 2>/dev/null; then
  echo "✅ actif"
else
  echo "❌ injoignable"
  echo "   Démarrer le serveur : ./start-local-ai.sh ${PROFILE/brain/brain}"
  exit 1
fi

echo "📂 Projet  : $PROJECT_PATH"
echo "🤖 Modèle  : $MODEL_ID"
echo "🔒 Mode    : offline — télémétrie désactivée"
echo ""

cd "$PROJECT_PATH"

# LiteLLM route 'openai/<id>' vers OPENAI_API_BASE ; la clé doit être non-vide.
aider \
  --model "openai/${MODEL_ID}" \
  --openai-api-base "$BASE" \
  --openai-api-key "local-no-key" \
  --no-auto-commits \
  --no-analytics --analytics-disable \
  --no-check-update --no-show-release-notes \
  --pretty --stream \
  "$@"
