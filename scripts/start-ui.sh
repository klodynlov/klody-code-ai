#!/usr/bin/env bash
# Lance le backend KlodyAI et ouvre l'app desktop Tauri
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="$HOME/Projets/klody-ui/src-tauri/target/release/bundle/macos/klody-ui.app"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║         KlodyAI  ·  Dashboard                           ║"
echo "║         100% local — aucune donnée envoyée              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Vérifier Ollama
if ! curl -sf http://localhost:11434/api/tags -o /dev/null 2>/dev/null; then
  echo "⚠️  Ollama n'est pas actif."
  echo "   Démarrer : ollama serve"
  echo ""
fi

# Vérifier que le port 8765 est libre
if lsof -i :8765 &>/dev/null; then
  echo "ℹ️  Backend déjà actif (port 8765)"
else
  echo "🚀 Démarrage du backend API (port 8765)…"
  cd "$SCRIPT_DIR"
  .venv/bin/python api/server.py &
  BACKEND_PID=$!
  sleep 1
  echo "   PID $BACKEND_PID"
fi

echo ""
echo "🖥  Ouverture de KlodyAI…"
open "$APP_PATH"
echo ""
echo "   Ctrl+C pour arrêter le backend"
echo "   (l'app Tauri se ferme indépendamment)"
echo ""

wait
