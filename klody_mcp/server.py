from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from config import LIBRARYBRAIN_URL, SKILLS_DIR
from fastmcp import FastMCP
from loguru import logger

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8082"))

# Pipeline "livre → skill JSON → artefact" (cf. skills/distilled/README.md).
# Sous-dossier dédié, ne perturbe ni les domain files (legacy) ni les user
# skills (tools/skills.py).
DISTILLED_DIR: Path = SKILLS_DIR / "distilled"

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


def _iter_distilled(domain: str | None = None):
    """Itère sur les fichiers distillés (skills/distilled/<domain>/*.json).

    Si `domain` est fourni, restreint à ce sous-dossier. Sinon parcourt tous
    les domaines. Ignore les fichiers commençant par `_` (placeholders) et
    `schema.json` à la racine.
    """
    if not DISTILLED_DIR.exists():
        return
    if domain is not None:
        roots = [DISTILLED_DIR / domain]
    else:
        roots = [p for p in DISTILLED_DIR.iterdir() if p.is_dir()]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.json")):
            if path.name.startswith("_"):
                continue
            yield path


@mcp.tool()
def list_distilled_skills(domain: str | None = None) -> list[dict]:
    """Liste les skills distillés (méthodes extraites de livres, schéma fixe).

    Args:
        domain: filtre optionnel (ex: "productivity"). None → tous les domaines.

    Returns:
        Liste de `{"slug", "domain", "skill", "description", "source"}` —
        métadonnées seulement, pas le contenu complet. Utiliser
        `get_distilled_skill` pour récupérer le JSON entier.
    """
    out: list[dict] = []
    for path in _iter_distilled(domain):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("distilled skill unreadable: {} ({})", path, exc)
            continue
        if not isinstance(data, dict) or "workflow" not in data:
            continue
        out.append(
            {
                "slug":        path.stem,
                "domain":      data.get("domain", path.parent.name),
                "skill":       data.get("skill", path.stem),
                "description": data.get("description", ""),
                "source":      data.get("source", {}),
            }
        )
    logger.info("list_distilled_skills({}) → {} entries", domain, len(out))
    return out


@mcp.tool()
def get_distilled_skill(slug: str, domain: str | None = None) -> dict:
    """Retourne un skill distillé complet (JSON parsé), prêt pour `apply-skill.md`.

    Args:
        slug: nom de fichier sans extension (ex: "deep-work").
        domain: sous-dossier optionnel — si fourni, lecture directe ;
                sinon recherche dans tous les domaines.

    Returns:
        Le contenu du JSON, ou `{"error": "...", "available": [...]}` si
        introuvable (liste de slugs voisins pour aider).
    """
    candidates: list[Path] = []
    if domain is not None:
        candidates.append(DISTILLED_DIR / domain / f"{slug}.json")
    else:
        candidates.extend(_iter_distilled(None))
        candidates = [p for p in candidates if p.stem == slug]

    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                logger.info("get_distilled_skill({}) → ok", slug)
                return data
            except Exception as exc:
                return {"error": f"invalid JSON: {exc}", "path": str(path)}

    available = sorted({p.stem for p in _iter_distilled(domain)})
    logger.warning("distilled skill not found: {} (domain={})", slug, domain)
    return {
        "error": f"slug '{slug}' not found"
                 + (f" in domain '{domain}'" if domain else ""),
        "available": available,
    }


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
