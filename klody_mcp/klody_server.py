"""Klody MCP server (Roadmap v2 #9).

Expose les outils code-aware de Klody (tree-sitter, embeddings, sandbox)
comme un serveur MCP — n'importe quel client compatible (Continue.dev,
Cline, Zed, autre Claude/agent) peut se brancher et les utiliser.

Klody devient ainsi une PLATEFORME et pas seulement un CLI.

Démarrage :
    python -m mcp.klody_server                       # défaut: stdio
    KLODY_MCP_TRANSPORT=http python -m mcp.klody_server  # HTTP sur port 8083

Outils exposés :
- find_symbol(name)               — où est défini un symbole
- find_references(name)           — qui utilise ce symbole
- find_relevant_files(query, k)   — recherche sémantique de fichiers
- run_in_sandbox(command, timeout)— exécute du Python dans un venv jetable
- read_file(path)                 — lit un fichier du projet
- list_files(path, recursive)     — liste les fichiers
- search_in_files(pattern, ...)   — grep dans le projet
- detect_conventions()            — retourne les conventions auto-détectées

Le `project_root` est par défaut $KLODY_MCP_ROOT ou cwd.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Racine du projet à exposer (peut être overridée par env)
_ROOT = Path(os.getenv("KLODY_MCP_ROOT", os.getcwd())).resolve()

mcp = FastMCP("Klody")


@mcp.custom_route("/health", methods=["GET"])
async def _health(request: Request) -> JSONResponse:
    """Sonde de vitalité hors protocole MCP (GET /health → 200).

    FastMCP renvoie 404 sur les chemins qui ne sont pas du transport MCP ;
    un health check générique (curl /health) échouait donc. Cette route
    custom expose un 200 JSON stable pour le monitoring."""
    return JSONResponse({"status": "ok", "service": "klody-mcp", "root": str(_ROOT)})


def _get_code_index():
    """Lazy init de l'index tree-sitter (évite le coût au démarrage MCP)."""
    from tools.code_index import CodeIndex
    if not hasattr(_get_code_index, "_idx"):
        _get_code_index._idx = CodeIndex(_ROOT)
    return _get_code_index._idx


def _get_embed_index():
    from tools.code_search import EmbeddingIndex
    if not hasattr(_get_embed_index, "_idx"):
        _get_embed_index._idx = EmbeddingIndex(_ROOT)
    return _get_embed_index._idx


def _get_sandbox():
    from tools.sandbox import SandboxRunner
    if not hasattr(_get_sandbox, "_sb"):
        _get_sandbox._sb = SandboxRunner(_ROOT)
    return _get_sandbox._sb


def _get_file_manager():
    """FileManager Klody, racine pinnée sur _ROOT."""
    from tools.file_manager import FileManager
    if not hasattr(_get_file_manager, "_fm"):
        fm = FileManager()
        fm.root = _ROOT
        _get_file_manager._fm = fm
    return _get_file_manager._fm


def _get_conventions():
    from agent.conventions import ConventionDetector
    if not hasattr(_get_conventions, "_det"):
        _get_conventions._det = ConventionDetector(_ROOT)
    return _get_conventions._det


# ---------------------------------------------------------------------------- #
# Outils MCP                                                                    #
# ---------------------------------------------------------------------------- #


@mcp.tool()
def find_symbol(name: str) -> dict:
    """Où un symbole (fonction, classe, méthode) est défini dans le projet.

    Args:
        name: Nom exact du symbole (case-sensitive). Ex: 'Router', 'compute_area'.

    Returns:
        {"matches": [{"name", "kind", "file", "line", "parent"}, ...], "count": int}
    """
    syms = _get_code_index().find_symbol(name)
    return {
        "count": len(syms),
        "matches": [
            {"name": s.name, "kind": s.kind, "file": s.file, "line": s.line,
             "parent": s.parent}
            for s in syms
        ],
    }


@mcp.tool()
def find_references(name: str, max_results: int = 50) -> dict:
    """Liste tous les endroits où un symbole est utilisé/appelé.

    Args:
        name: Nom exact du symbole (case-sensitive).
        max_results: Nombre max de références à retourner (défaut 50).

    Returns:
        {"references": [{"name", "file", "line", "context"}, ...], "count": int}
    """
    refs = _get_code_index().find_references(name, max_results=max_results)
    return {
        "count": len(refs),
        "references": [
            {"name": r.name, "file": r.file, "line": r.line, "context": r.context}
            for r in refs
        ],
    }


@mcp.tool()
def find_relevant_files(query: str, k: int = 5) -> dict:
    """Recherche sémantique : top-k fichiers les plus pertinents pour une question.

    Args:
        query: Question en langage naturel (français OK).
        k: Nombre de fichiers à retourner (1-10).

    Returns:
        {"hits": [{"file", "score", "preview"}, ...]}
    """
    idx = _get_embed_index()
    if not idx.is_available():
        return {"hits": [], "error": "Embedding backend unavailable (Ollama bge-m3 needed)"}
    hits = idx.search(query, k=max(1, min(10, k)))
    return {
        "hits": [
            {"file": h.rel_path, "score": round(h.score, 3), "preview": h.preview}
            for h in hits
        ],
    }


@mcp.tool()
def run_in_sandbox(command: str, timeout: int = 30) -> dict:
    """Exécute une commande Python dans un venv jetable.

    Args:
        command: Commande à exécuter ex: 'pytest test_x.py -q', 'python main.py'.
        timeout: Timeout en secondes (défaut 30).

    Returns:
        {"success", "exit_code", "stdout", "stderr", "duration_s"}
    """
    res = _get_sandbox().run(command, timeout=timeout)
    return {
        "success": res.success,
        "exit_code": res.exit_code,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "duration_s": res.duration_s,
        "timed_out": res.timed_out,
    }


@mcp.tool()
def read_file(path: str) -> dict:
    """Lit un fichier du projet (sandbox-validé, lecture seule).

    Args:
        path: Chemin relatif depuis la racine du projet.

    Returns:
        {"content": str} ou {"error": str}
    """
    try:
        content = _get_file_manager().read_file(path)
        return {"content": content}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def list_files(path: str = ".", recursive: bool = False) -> dict:
    """Liste les fichiers/dossiers du projet.

    Args:
        path: Sous-dossier à lister (défaut: racine).
        recursive: Si True, descend récursivement.

    Returns:
        {"listing": str}
    """
    try:
        listing = _get_file_manager().list_files(path, recursive)
        return {"listing": listing}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def search_in_files(pattern: str, path: str = ".", file_pattern: str = "",
                    case_sensitive: bool = True) -> dict:
    """Recherche un pattern dans les fichiers (ripgrep si dispo, sinon grep).

    Args:
        pattern: Pattern (texte ou regex).
        path: Répertoire où chercher.
        file_pattern: Filtre glob (ex: '*.py').
        case_sensitive: Sensibilité à la casse.

    Returns:
        {"results": str}
    """
    try:
        from tools.search import Search
        if not hasattr(search_in_files, "_s"):
            search_in_files._s = Search()
        result = search_in_files._s.search_in_files(pattern, path, file_pattern, case_sensitive)
        return {"results": result}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def detect_conventions() -> dict:
    """Conventions auto-détectées du projet (test framework, style, frameworks…).

    Returns:
        {"conventions": [{"name", "value", "evidence", "confidence"}, ...],
         "workdir": str}
    """
    report = _get_conventions().detect()
    return {
        "workdir": report.workdir,
        "conventions": [
            {"name": c.name, "value": c.value, "evidence": c.evidence,
             "confidence": c.confidence}
            for c in report.conventions
        ],
    }


# ---------------------------------------------------------------------------- #
# Entrée principale                                                            #
# ---------------------------------------------------------------------------- #


def main() -> None:
    transport = os.getenv("KLODY_MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("KLODY_MCP_PORT", "8087"))  # 8083 = collision avec MLX_CODE_PORT
    host = os.getenv("KLODY_MCP_HOST", "127.0.0.1")

    if transport == "http":
        # HTTP/SSE transport (FastMCP gère)
        logger.info("Klody MCP HTTP : http://%s:%d  | root=%s", host, port, _ROOT)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("Klody MCP stdio | root=%s", _ROOT)
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
