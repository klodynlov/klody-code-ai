from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from fastmcp import FastMCP
from loguru import logger

LIBRARYBRAIN_URL = os.getenv("LIBRARYBRAIN_URL", "http://127.0.0.1:8765/api/ask")
SKILLS_DIR = Path(__file__).parent.parent / "skills"
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8082"))

mcp = FastMCP("LibraryBrain")


@dataclass
class Chunk:
    content: str
    source: str
    score: float


@dataclass
class Skill:
    id: str
    domain: str
    title: str
    content: str
    tags: list[str]


@mcp.tool()
async def search_books(query: str, limit: int = 3) -> list[dict]:
    """Recherche sémantique dans LibraryBrain (sqlite-vec + LlamaIndex).

    Retourne les chunks les plus pertinents avec leur source (titre, auteur, page).
    Retourne une liste vide si LibraryBrain est inaccessible.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                LIBRARYBRAIN_URL,
                json={"query": query, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()

        # Format LibraryBrain : {answer, sources:[{title,author,page,score}], found}
        if not data.get("found", False):
            logger.info("search_books('{}') → not found", query)
            return []

        answer = data.get("answer", "")
        sources = data.get("sources", [])
        chunks = [
            Chunk(
                content=answer,
                source=" | ".join(
                    f"{s.get('title','?')} — {s.get('author','?')}, p.{s.get('page','?')}"
                    for s in sources[:limit]
                ),
                score=float(sources[0].get("score", 0.0)) if sources else 0.0,
            )
        ]
        logger.info("search_books('{}') → 1 chunk, {} sources", query, len(sources))
        return [asdict(c) for c in chunks]

    except httpx.ConnectError:
        logger.warning("LibraryBrain unreachable at {}", LIBRARYBRAIN_URL)
        return []
    except httpx.HTTPStatusError as exc:
        logger.warning("LibraryBrain returned {}: {}", exc.response.status_code, LIBRARYBRAIN_URL)
        return []
    except Exception as exc:
        logger.error("search_books unexpected error: {}", exc)
        return []


def _is_domain_file(path: Path) -> bool:
    """Vérifie qu'un fichier est un domaine valide (liste de {title, content, tags})."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(data, list) and bool(data) and isinstance(data[0], dict) and "title" in data[0]
    except Exception:
        return False


@mcp.tool()
def get_skills(domain: str) -> list[dict]:
    """Retourne les conventions et patterns du domaine demandé.

    Domaines disponibles : symfony, nextjs, python, mlx.
    Retourne une liste vide si le domaine est inconnu ou invalide.
    """
    path = SKILLS_DIR / f"{domain}.json"
    if not path.exists() or not _is_domain_file(path):
        logger.warning("Skills domain not found or invalid: {}", domain)
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        skills = [Skill(**s) for s in raw]
        logger.info("get_skills('{}') → {} entries", domain, len(skills))
        return [asdict(s) for s in skills]
    except Exception as exc:
        logger.error("Failed to load skills '{}': {}", domain, exc)
        return []


@mcp.tool()
def get_conventions(project: str) -> str:
    """Retourne le CLAUDE.md / AGENTS.md du projet cible sous forme de texte brut.

    Cherche dans l'ordre : CLAUDE.md, AGENTS.md, .claude/CLAUDE.md.
    Retourne une chaîne vide si aucun fichier n'est trouvé.
    """
    root = Path(project).expanduser().resolve()
    candidates = [
        root / "CLAUDE.md",
        root / "AGENTS.md",
        root / ".claude" / "CLAUDE.md",
    ]
    for path in candidates:
        if path.is_file():
            logger.info("get_conventions('{}') → {}", project, path.name)
            return path.read_text(encoding="utf-8")
    logger.warning("No conventions file found for project: {}", project)
    return ""


if __name__ == "__main__":
    logger.info("LibraryBrain MCP server → {}:{}", MCP_HOST, MCP_PORT)
    mcp.run(transport="streamable-http", host=MCP_HOST, port=MCP_PORT)
