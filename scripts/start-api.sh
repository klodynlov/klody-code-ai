#!/usr/bin/env bash
# Démarre l'API Klody (FastAPI / uvicorn) sur 127.0.0.1:8000.
#
# C'est la "porte d'entrée" consommée par l'app desktop (klody-ui) via WebSocket.
# Normalement auto-spawnée par Tauri (src-tauri/src/lib.rs), mais ce spawn est
# fragile (échoue si l'app est fermée). Ce script permet de la rendre permanente
# via le LaunchAgent com.klody.api (démarrage au login + relance auto si crash).
#
# Le backend LLM (MLX vs Ollama) est choisi par BACKEND dans .env, lu par
# config.py à l'import. Le serveur lit .env lui-même (load_dotenv) ; on le source
# aussi ici par cohérence avec scripts/start-mlx.sh.
#
# exec → le PID surveillé par launchd est bien le process uvicorn.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# .env = source de vérité (BACKEND, MLX_*, KLODY_MCP_SERVERS, secrets…).
set -a
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"
set +a

# Télémétrie HF coupée (contrainte zéro-cloud), cohérent avec start-local-ai.sh.
export HF_HUB_DISABLE_TELEMETRY=1
export DISABLE_TELEMETRY=1

# Venv si présent, sinon python3 du PATH.
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  source "$ROOT/.venv/bin/activate"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Klody API (FastAPI)"
echo "  Backend LLM : ${BACKEND:-ollama}"
echo "  URL         : http://127.0.0.1:8000"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exec python api/server.py
