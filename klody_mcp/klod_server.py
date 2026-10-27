"""KLOD Live Brain MCP — pont vers l'organe GrooveDNA / KLOD Rig.

Le serveur MCP ne vit PAS ici : il appartient à son organe (~/klod-live-brain,
venv dédié — pyserial, mido, fastmcp) et expose le groove (Teensy 4.1, REAPER,
SampleBrain), la Ventuno Q (apps App Lab, démon Ableton Link, via `adb`) et Link
dans Ableton Live (Remote Script KlodyBridge, :9877). Ce module est le point
d'entrée NOMINATIF exigé par la convention « un MCP = un start-*-mcp.sh + un
LaunchAgent + un module » (scripts/install-launchagents.sh, `point_d_entree()`).
Il ne fait qu'une chose : se placer dans l'organe et remplacer le processus par
son interpréteur (`python -m klod_mcp.klod_server`, HTTP streamable sur /mcp),
sans rien importer de KLOD.

⚠️ `KLOD_MCP_PORT` (cet organe, :8104) n'est PAS `KLODY_MCP_PORT` (le serveur
MCP propre de Klody, :8087) : une lettre d'écart, deux services.
:8104 plutôt que :8099, libre aussi : le bloc 8080-8099 est celui des workers du
gateway klody-core (8082 lyricist, 8098 vision…).

Démarrage :
    python -m klody_mcp.klod_server            # HTTP :8104 (seul transport)
    KLOD_LIVE_BRAIN_DIR=/chemin python -m klody_mcp.klod_server

Sécurité : `ventuno_app_start` compile et FLASHE le STM32H5 de la Ventuno,
`play_groove` émet du MIDI, `live_link_set` change le tempo de la session Live —
politique « klod » de `agent/approval.py`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

PORT = int(os.environ.get("KLOD_MCP_PORT", "8104"))
URL_MCP = f"http://127.0.0.1:{PORT}/mcp"


def dossier_organe(env: Mapping[str, str] | None = None) -> Path:
    """Racine de KLOD Live Brain : `KLOD_LIVE_BRAIN_DIR` sinon ~/klod-live-brain."""
    source: Mapping[str, str] = os.environ if env is None else env
    brut = source.get("KLOD_LIVE_BRAIN_DIR")
    return Path(brut).expanduser() if brut else Path.home() / "klod-live-brain"


def interpreteur(racine: Path) -> Path:
    """Le python du venv de l'organe ; `FileNotFoundError` s'il manque ou n'est pas
    exécutable (organe non cloné, venv non créé)."""
    python = racine / ".venv" / "bin" / "python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise FileNotFoundError(f"KLOD Live Brain absent ou venv non créé : {python}")
    if not (racine / "klod_mcp" / "klod_server.py").is_file():
        raise FileNotFoundError(f"KLOD Live Brain sans klod_mcp/klod_server.py : {racine}")
    return python


def environnement(base: Mapping[str, str]) -> dict[str, str]:
    """Variables transmises à l'organe : transport HTTP sur 127.0.0.1:PORT."""
    env = dict(base)
    env["KLOD_MCP_TRANSPORT"] = "http"
    env["KLOD_MCP_HOST"] = "127.0.0.1"
    env["KLOD_MCP_PORT"] = str(PORT)
    return env


def main(argv: list[str] | None = None) -> int:
    """Remplace le processus par le serveur de l'organe. Ne revient qu'en échec."""
    del argv  # aucun argument : transport et port sont fixés par la convention
    racine = dossier_organe()
    try:
        python = interpreteur(racine)
    except FileNotFoundError as e:
        print(f"klod-mcp : {e}", file=sys.stderr)
        return 1
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  KLOD Live Brain MCP (GrooveDNA, Ventuno Q, Link)")
    print(f"  Adresse   : {URL_MCP}")
    print(f"  Organe    : {racine}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
    os.chdir(racine)  # `-m klod_mcp…` se résout sur le dossier courant
    os.execve(str(python), [str(python), "-m", "klod_mcp.klod_server"], environnement(os.environ))
    return 1  # pragma: no cover — execve ne revient pas


if __name__ == "__main__":
    sys.exit(main())
