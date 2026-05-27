#!/usr/bin/env bash
# Ouvre l'app desktop KlodyAI v2.
# L'app auto-spawn MLX (port 8080) + API FastAPI (port 8000) via src-tauri/src/lib.rs.
# Ce script se contente de vérifier les services persistants (Ollama, LibraryBrain)
# et d'ouvrir le .app.
set -euo pipefail

APP_PATH="$HOME/Projets/klody-ui/src-tauri/target/release/bundle/macos/klody-ui.app"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║         KlodyAI v2  ·  Dashboard                         ║"
echo "║         100% local — aucune donnée envoyée               ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Ollama (port 11434) — service de fallback
if curl -sf http://127.0.0.1:11434/api/tags -o /dev/null 2>/dev/null; then
  echo "  ✓  Ollama actif (:11434)"
else
  echo "  ⚠  Ollama inactif. Démarrer : ollama serve"
fi

# LibraryBrain RAG (port 8765) — utilisé pour search_books
if curl -sf http://127.0.0.1:8765/api/ask -o /dev/null 2>/dev/null \
   || lsof -i :8765 &>/dev/null; then
  echo "  ✓  LibraryBrain actif (:8765)"
else
  echo "  ⚠  LibraryBrain inactif (search_books indisponible)"
fi

# MLX (port 8080) — auto-spawné par Tauri si absent, sinon LaunchAgent
if lsof -i :8080 &>/dev/null; then
  echo "  ✓  MLX actif (:8080)"
else
  echo "  ⏳  MLX inactif — l'app le démarrera (chargement modèle ~30-60s)"
fi

# Backend FastAPI (port 8000) — auto-spawné par Tauri
if lsof -i :8000 &>/dev/null; then
  echo "  ✓  API backend actif (:8000)"
else
  echo "  ⏳  API backend inactif — l'app le démarrera"
fi

if [[ ! -d "$APP_PATH" ]]; then
  echo ""
  echo "❌  Bundle introuvable: $APP_PATH"
  echo "    Lancer : cd ~/Projets/klody-ui && npm run tauri build"
  exit 1
fi

echo ""
echo "🖥  Ouverture de KlodyAI v2…"
open "$APP_PATH"
