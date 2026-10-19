#!/usr/bin/env bash
# Démarre le serveur MCP KLOD Live Brain (GrooveDNA, Ventuno Q, Link dans Live).
#
# Le serveur vit dans son propre organe (~/klod-live-brain, venv dédié) : ce
# lanceur exécute le module pont klody_mcp/klod_server.py, qui remplace le
# processus par l'interpréteur de l'organe. Convention respectée :
# « un MCP = un start-*-mcp.sh + un LaunchAgent + un module »
# (install-launchagents.sh, point_d_entree()).
# Transport HTTP streamable sur :8104 (/mcp). `adb` doit être dans le PATH
# (Homebrew, android-platform-tools) pour les outils ventuno_*.
#
# Usage:
#   ./scripts/start-klod-mcp.sh             # HTTP :8104 (seul transport supporté)
#   KLOD_LIVE_BRAIN_DIR=/chemin ./scripts/start-klod-mcp.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
exec python -m klody_mcp.klod_server "$@"
