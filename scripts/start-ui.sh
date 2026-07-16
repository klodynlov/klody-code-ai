#!/usr/bin/env bash
# Ouvre l'app desktop KlodyAI v2.
# L'app auto-spawn MLX (port 8080) + API FastAPI (port 8000) via src-tauri/src/lib.rs.
# Ce script se contente de vérifier les services persistants (Ollama, LibraryBrain)
# et d'ouvrir le .app.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

APP_PATH="$HOME/Projets/klody-ui/src-tauri/target/release/bundle/macos/klody-ui.app"

# LIBRARYBRAIN_URL / LIBRARYBRAIN_TOKEN viennent du .env, comme côté Python : la
# sonde d'ici codait l'URL en dur et ignorait .env, donc elle interrogeait :8765
# même quand Klody, lui, parlait à un autre hôte — un vert sur un service que
# Klody n'utilise pas.
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi
# shellcheck source=lib/librarybrain-probe.sh
source "$SCRIPT_DIR/lib/librarybrain-probe.sh"

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

# LibraryBrain RAG (port 8765) — utilisé pour search_books.
# Sonde /api/stats en 200 strict (cf. scripts/lib/librarybrain-probe.sh) : la
# version précédente ne pouvait STRUCTURELLEMENT pas rapporter de panne. Elle
# faisait un GET sur /api/ask, qui est POST-only : le 405 faisait sortir
# `curl -sf` en 22 à TOUS les coups, donc elle repliait toujours sur
# `lsof -i :8765`, qui ne prouve que le port lié. Un 401, un 404, n'importe quelle
# panne derrière un port ouvert s'affichait « ✓ LibraryBrain actif ».
LB_BASE="$(lb_base_url)"
case "$(lb_probe "$LB_BASE")" in
  "$LB_PROBE_UP")
    echo "  ✓  LibraryBrain actif ($LB_BASE)"
    ;;
  "$LB_PROBE_UNAUTHORIZED")
    echo "  ⚠  LibraryBrain refuse Klody — 401 sur /api/ (search_books indisponible)"
    echo "     $(lb_unauthorized_detail)"
    ;;
  *)
    echo "  ⚠  LibraryBrain injoignable sur $LB_BASE (search_books indisponible)"
    ;;
esac

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
