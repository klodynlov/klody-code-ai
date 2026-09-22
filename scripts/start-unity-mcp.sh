#!/usr/bin/env bash
# Démarre le serveur MCP for Unity (:8097, transport HTTP).
#
# Le serveur tourne dans son venv dédié (~/.klody/venvs/unity-mcp), hors dépôt :
# ce lanceur exécute le module pont klody_mcp/unity_server.py, qui remplace le
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
exec python -m klody_mcp.unity_server "$@"
