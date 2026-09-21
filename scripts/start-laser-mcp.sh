#!/usr/bin/env bash
# Démarre le serveur MCP KlodyLaser (graveur Sculpfun S30 Pro Max + caméra CAM500).
#
# Le serveur vit dans son propre organe (~/Projets/KlodyLaser, venv dédié) : ce
# lanceur exécute le module pont klody_mcp/laser_server.py, qui remplace le
# processus par scripts/start.sh de l'organe. Convention respectée :
# « un MCP = un start-*-mcp.sh + un LaunchAgent + un module »
# (install-launchagents.sh, point_d_entree()).
# Transport HTTP streamable sur :8798 (/mcp), UI sur /. Sécurité : tout tir
# exige laser_arm(confirm="FIRE") côté serveur.
#
# Usage:
#   ./scripts/start-laser-mcp.sh            # HTTP :8798 (seul transport supporté)
#   KLODYLASER_DIR=/chemin ./scripts/start-laser-mcp.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
exec python -m klody_mcp.laser_server "$@"
