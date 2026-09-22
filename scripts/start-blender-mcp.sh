#!/usr/bin/env bash
# Démarre le serveur MCP Blender Lab (blender_mcp v1.0.0, :8095).
#
# Le serveur tourne dans son venv dédié (~/.klody/venvs/blender-mcp), hors dépôt :
# ce lanceur exécute le module pont klody_mcp/blender_server.py, qui remplace le
# processus par ce venv. Convention respectée :
# « un MCP = un start-*-mcp.sh + un LaunchAgent + un module »
# (install-launchagents.sh, point_d_entree()).

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
exec python -m klody_mcp.blender_server "$@"
