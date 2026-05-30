#!/usr/bin/env bash
# Démarre l'API Klody (FastAPI / uvicorn) sur 127.0.0.1:8000.
#
# C'est la "porte d'entrée" consommée par l'app desktop (klody-ui) via WebSocket.
# Normalement auto-spawnée par Tauri (src-tauri/src/lib.rs), mais ce spawn est
# fragile (échoue si l'app est fermée). Ce script permet de la rendre permanente
# via le LaunchAgent com.klody.api (démarrage au login + relance auto si crash).
#
# Le backend LLM (MLX vs Ollama) est choisi par BACKEND dans .env, lu par
# config.py à l'import via python-dotenv (load_dotenv).
#
# IMPORTANT : on ne `source .env` PAS ici. bash retirerait les guillemets des
# valeurs JSON (ex: KLODY_MCP_SERVERS={"gmail":"..."} → {gmail:...} invalide),
# et comme load_dotenv() n'override pas une variable déjà posée, le serveur
# hériterait du JSON cassé. python-dotenv parse .env correctement : on le laisse
# faire. Le wrapper se contente de fixer cwd + venv + exec.
#
# exec → le PID surveillé par launchd est bien le process uvicorn.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Télémétrie HF coupée (contrainte zéro-cloud), cohérent avec start-local-ai.sh.
export HF_HUB_DISABLE_TELEMETRY=1
export DISABLE_TELEMETRY=1

# Venv si présent, sinon python3 du PATH.
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  source "$ROOT/.venv/bin/activate"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Klody API (FastAPI)"
echo "  URL : http://127.0.0.1:8000"
echo "  (backend LLM + .env résolus par config.py via load_dotenv)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exec python api/server.py
