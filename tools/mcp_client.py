"""Client MCP pour les outils LibraryBrain — appels directs sans protocole MCP."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

LIBRARYBRAIN_URL = os.getenv("LIBRARYBRAIN_URL", "http://127.0.0.1:8765/ask")
SKILLS_DIR = Path(__file__).parent.parent / "skills"


def search_books(query: str, limit: int = 3) -> str:
    """Recherche sémantique dans LibraryBrain (RAG local).

    Retourne les passages les plus pertinents formatés pour le LLM,
    ou un message d'erreur lisible si LibraryBrain est hors-ligne.
    """
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(LIBRARYBRAIN_URL, json={"query": query, "limit": limit})
            resp.raise_for_status()
            data = resp.json()

        raw = data if isinstance(data, list) else data.get("results", data.get("chunks", []))
        if not raw:
            return "Aucun résultat trouvé dans la bibliothèque pour cette requête."

        lines: list[str] = []
        for c in raw[:limit]:
            source = c.get("source", c.get("metadata", {}).get("source", "source inconnue"))
            content = c.get("content", c.get("text", ""))
            score = float(c.get("score", c.get("similarity", 0.0)))
            lines.append(f"[{source} | score: {score:.2f}]\n{content}")

        return "\n\n---\n\n".join(lines)

    except httpx.ConnectError:
        logger.warning("LibraryBrain inaccessible à %s", LIBRARYBRAIN_URL)
        return "LibraryBrain inaccessible — serveur non démarré ou URL incorrecte."
    except httpx.HTTPStatusError as exc:
        logger.warning("LibraryBrain HTTP %s", exc.response.status_code)
        return f"LibraryBrain a retourné une erreur {exc.response.status_code}."
    except Exception as exc:
        logger.error("search_books erreur inattendue: %s", exc, exc_info=True)
        return f"Erreur lors de la recherche: {exc}"


def get_skills(domain: str) -> str:
    """Retourne les conventions du domaine sous forme de texte structuré pour le LLM.

    Charge depuis skills/{domain}.json.
    Retourne la liste des domaines disponibles si le domaine est inconnu.
    """
    path = SKILLS_DIR / f"{domain}.json"
    if not path.exists():
        available = sorted(p.stem for p in SKILLS_DIR.glob("*.json"))
        avail_str = ", ".join(available) if available else "aucun"
        return f"Domaine '{domain}' inconnu. Domaines disponibles : {avail_str}."

    try:
        skills = json.loads(path.read_text(encoding="utf-8"))
        sections: list[str] = [f"## Conventions {domain}\n"]
        for s in skills:
            tags = ", ".join(s.get("tags", []))
            sections.append(f"### {s['title']}\n{s['content']}\n*Tags: {tags}*")
        return "\n\n".join(sections)

    except Exception as exc:
        logger.error("get_skills '%s' erreur: %s", domain, exc, exc_info=True)
        return f"Erreur lors du chargement des skills '{domain}': {exc}"
