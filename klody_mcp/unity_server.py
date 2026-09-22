"""KlodyUnity MCP — pont vers MCP for Unity (paquet amont mcp-for-unity).

Le serveur MCP ne vit pas dans l'environnement du dépôt : il est installé dans
son venv dédié (`~/.klody/venvs/unity-mcp`) et s'exécute via le binaire
`mcp-for-unity`. Ce module est le point d'entrée NOMINATIF exigé par la
convention « un MCP = un start-*-mcp.sh + un LaunchAgent + un module »
(`scripts/install-launchagents.sh`, `point_d_entree()`), pour que le contrôle
de péremption sache quel fichier appartient au service. Il ne fait qu'une
chose : remplacer le processus courant par le binaire amont (`os.execv`), sans
rien importer d'Unity.

La télémétrie amont est coupée par variables d'environnement avant l'exec.

Démarrage :
    python -m klody_mcp.unity_server
    KLODY_UNITY_VENV=/chemin python -m klody_mcp.unity_server
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

PORT = 8097
HOTE = "127.0.0.1"
URL_MCP = f"http://{HOTE}:{PORT}/mcp"
RACINE = Path(__file__).resolve().parents[1]


def binaire(env: Mapping[str, str] | None = None) -> Path:
    """Binaire `mcp-for-unity` : `KLODY_UNITY_VENV` sinon ~/.klody/venvs/unity-mcp."""
    source: Mapping[str, str] = os.environ if env is None else env
    brut = source.get("KLODY_UNITY_VENV")
    venv = Path(brut).expanduser() if brut else Path.home() / ".klody" / "venvs" / "unity-mcp"
    return venv / "bin" / "mcp-for-unity"


def verifier(programme: Path) -> None:
    """`FileNotFoundError` si le binaire amont manque ou n'est pas exécutable."""
    if not programme.is_file() or not os.access(programme, os.X_OK):
        raise FileNotFoundError(f"MCP for Unity absent ou non exécutable : {programme}")


def main(argv: list[str] | None = None) -> int:
    """Remplace le processus par le serveur Unity. Ne revient qu'en échec."""
    args = sys.argv[1:] if argv is None else argv
    programme = binaire()
    try:
        verifier(programme)
    except FileNotFoundError as e:
        print(f"unity-mcp : {e}", file=sys.stderr)
        return 1
    os.environ["DISABLE_TELEMETRY"] = "true"
    os.environ["UNITY_MCP_TELEMETRY_ENABLED"] = "false"
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  KlodyUnity MCP Server (MCP for Unity)")
    print(f"  Adresse   : {URL_MCP}")
    print(f"  Programme : {programme}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
    os.chdir(RACINE)
    os.execv(str(programme), [
        str(programme),
        "--transport", "http",
        "--http-url", f"http://{HOTE}:{PORT}",
        "--http-host", HOTE,
        "--http-port", str(PORT),
        *args,
    ])
    return 1  # pragma: no cover — execv ne revient pas


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
