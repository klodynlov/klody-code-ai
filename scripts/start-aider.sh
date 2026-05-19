#!/usr/bin/env bash
# Lance Aider sur klody-code-ai — backend Ollama local (qwen2.5-coder:32b)
# Aucun abonnement. Aucune clé API. 100% offline.
set -euo pipefail

PROJECT_PATH="${1:-$PWD}"
BACKEND_URL="http://localhost:11434"
MODEL="ollama/qwen2.5-coder:32b"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║         KlodyAI  ·  Aider + Ollama (local)              ║"
echo "║         100% local — aucune donnée envoyée              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Vérifications
if ! command -v aider &>/dev/null; then
  SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
  if [[ -f "$SCRIPT_DIR/.venv/bin/aider" ]]; then
    export PATH="$SCRIPT_DIR/.venv/bin:$PATH"
  else
    echo "❌ Aider non trouvé. Activer le venv : source .venv/bin/activate"
    exit 1
  fi
fi

echo -n "🔗 Ollama ($BACKEND_URL) ... "
if curl -sf "$BACKEND_URL/api/tags" -o /dev/null 2>/dev/null; then
  echo "✅ actif"
else
  echo "❌ non accessible"
  echo "   Démarrer Ollama : ollama serve"
  exit 1
fi

echo ""
echo "📂 Projet  : $PROJECT_PATH"
echo "🤖 Modèle  : qwen2.5-coder:32b (local, ~19 GB)"
echo "🌐 Backend : Ollama localhost:11434"
echo "🔒 Mode    : offline — aucun appel externe"
echo ""
echo "   /add <fichier>   ajouter au contexte"
echo "   /drop <fichier>  retirer du contexte"
echo "   /diff            voir les changements"
echo "   /undo            annuler le dernier commit"
echo "   /exit            quitter"
echo ""

cd "$PROJECT_PATH"
export OPENAI_API_KEY="local-no-key"

aider \
  --model "$MODEL" \
  --no-auto-commits \
  --pretty \
  --stream
