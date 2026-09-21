#!/usr/bin/env bash
# Démarre le serveur MCP KlodyLaser (graveur Sculpfun S30 Pro Max + caméra CAM500).
#
# Le serveur vit dans son propre organe (~/Projets/KlodyLaser, venv dédié) : ce
# script n'est qu'un pont vers scripts/start.sh, pour respecter la convention
# « un MCP = un start-*-mcp.sh + un LaunchAgent » (test_launchagents_couvrent_les_mcp).
# Transport HTTP streamable sur :8798 (/mcp), UI sur /. Sécurité : tout tir
# exige laser_arm(confirm="FIRE") côté serveur.
#
# Usage:
#   ./scripts/start-laser-mcp.sh            # HTTP :8798 (seul transport supporté)

set -euo pipefail
LASER_DIR="${KLODYLASER_DIR:-$HOME/Projets/KlodyLaser}"
[[ -x "$LASER_DIR/scripts/start.sh" ]] || { echo "KlodyLaser absent : $LASER_DIR" >&2; exit 1; }
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  KlodyLaser MCP Server (Sculpfun S30 Pro Max)"
echo "  Adresse   : http://127.0.0.1:8798/mcp"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cd "$LASER_DIR"
exec ./scripts/start.sh "$@"
