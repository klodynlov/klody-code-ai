"""KlodyAbleton MCP — pont vers le serveur Ableton Live (mcp-server-ableton-live).

Le serveur MCP ne vit pas dans l'environnement du dépôt : il a son venv dédié
(`~/.klody/venvs/ableton-mcp`, dépendances propres à Ableton) et son programme
est `integrations/ableton/server.py`. Ce module est le point d'entrée NOMINATIF
exigé par la convention « un MCP = un start-*-mcp.sh + un LaunchAgent + un
module » (`scripts/install-launchagents.sh`, `point_d_entree()`), pour que le
contrôle de péremption sache quel fichier appartient au service. Il ne fait
qu'une chose : remplacer le processus courant par le python du venv dédié
(`os.execv`), sans rien importer d'Ableton.

Démarrage :
    python -m klody_mcp.ableton_server
    KLODY_ABLETON_VENV=/chemin python -m klody_mcp.ableton_server
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

PORT = 8094
URL_MCP = f"http://127.0.0.1:{PORT}/mcp"
RACINE = Path(__file__).resolve().parents[1]
PROGRAMME = RACINE / "integrations" / "ableton" / "server.py"


def python_dedie(env: Mapping[str, str] | None = None) -> Path:
    """Python du venv Ableton : `KLODY_ABLETON_VENV` sinon ~/.klody/venvs/ableton-mcp."""
    source: Mapping[str, str] = os.environ if env is None else env
    brut = source.get("KLODY_ABLETON_VENV")
    venv = Path(brut).expanduser() if brut else Path.home() / ".klody" / "venvs" / "ableton-mcp"
    return venv / "bin" / "python"


def verifier(python: Path) -> None:
    """`FileNotFoundError` si le venv dédié ou le programme manque."""
    if not python.is_file() or not os.access(python, os.X_OK):
        raise FileNotFoundError(f"venv Ableton absent ou non exécutable : {python}")
    if not PROGRAMME.is_file():
        raise FileNotFoundError(f"programme absent : {PROGRAMME}")


def main(argv: list[str] | None = None) -> int:
    """Remplace le processus par le serveur Ableton. Ne revient qu'en échec."""
    args = sys.argv[1:] if argv is None else argv
    python = python_dedie()
    try:
        verifier(python)
    except FileNotFoundError as e:
        print(f"ableton-mcp : {e}", file=sys.stderr)
        return 1
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  KlodyAbleton MCP Server (Ableton Live)")
    print(f"  Adresse   : {URL_MCP}")
    print(f"  Programme : {PROGRAMME}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
    os.chdir(RACINE)
    os.execv(str(python), [str(python), str(PROGRAMME), *args])
    return 1  # pragma: no cover — execv ne revient pas


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
