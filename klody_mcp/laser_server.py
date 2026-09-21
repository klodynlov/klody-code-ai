"""KlodyLaser MCP — pont vers l'organe graveur (Sculpfun S30 Pro Max + CAM500).

Le serveur MCP du laser ne vit PAS ici : il appartient à son organe
(~/Projets/KlodyLaser, venv dédié — pyserial, OpenCV) et tourne en HTTP
streamable sur :8798 (/mcp, UI sur /, /health). Ce module est le point
d'entrée NOMINATIF exigé par la convention « un MCP = un start-*-mcp.sh +
un LaunchAgent + un module » (scripts/install-launchagents.sh,
`point_d_entree()`), pour que le contrôle de péremption sache quel fichier
appartient au service. Il ne fait qu'une chose : remplacer le processus
courant par le lanceur de l'organe (`os.execv`), sans rien importer de
KlodyLaser.

Démarrage :
    python -m klody_mcp.laser_server            # HTTP :8798 (seul transport)
    KLODYLASER_DIR=/chemin python -m klody_mcp.laser_server

Sécurité : tout tir exige `laser_arm(confirm="FIRE")` côté serveur — rien ici
ne peut armer la machine.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

PORT = 8798
URL_MCP = f"http://127.0.0.1:{PORT}/mcp"


def dossier_organe(env: Mapping[str, str] | None = None) -> Path:
    """Racine de KlodyLaser : `KLODYLASER_DIR` sinon ~/Projets/KlodyLaser."""
    source: Mapping[str, str] = os.environ if env is None else env
    brut = source.get("KLODYLASER_DIR")
    return Path(brut).expanduser() if brut else Path.home() / "Projets" / "KlodyLaser"


def lanceur(racine: Path) -> Path:
    """Le `scripts/start.sh` de l'organe ; `FileNotFoundError` s'il manque ou
    n'est pas exécutable (organe non cloné, ou disque externe non monté)."""
    script = racine / "scripts" / "start.sh"
    if not script.is_file() or not os.access(script, os.X_OK):
        raise FileNotFoundError(f"KlodyLaser absent ou lanceur non exécutable : {script}")
    return script


def main(argv: list[str] | None = None) -> int:
    """Remplace le processus par le lanceur de l'organe. Ne revient qu'en échec."""
    args = sys.argv[1:] if argv is None else argv
    racine = dossier_organe()
    try:
        script = lanceur(racine)
    except FileNotFoundError as e:
        print(f"laser-mcp : {e}", file=sys.stderr)
        return 1
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  KlodyLaser MCP Server (Sculpfun S30 Pro Max)")
    print(f"  Adresse   : {URL_MCP}")
    print(f"  Organe    : {racine}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
    os.chdir(racine)
    os.execv(str(script), [str(script), *args])
    return 1  # pragma: no cover — execv ne revient pas


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
