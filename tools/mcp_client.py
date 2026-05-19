"""Client MCP pour les outils LibraryBrain — appels directs sans protocole MCP."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import httpx

from config import LIBRARYBRAIN_URL, SKILLS_DIR

logger = logging.getLogger(__name__)

_BASE_URL = LIBRARYBRAIN_URL
# Base sans le path (pour construire /api/ask/job)
_SERVER_BASE = _BASE_URL.rsplit("/api/", 1)[0]

# Polling : 2s entre chaque sonde, max 90 tentatives = 3 min
_POLL_INTERVAL = 2.0
_POLL_MAX = 90


def _parse_result(data: dict, limit: int) -> str:
    """Formate la réponse LibraryBrain {answer, sources, found} en texte LLM."""
    if not data.get("found", False):
        return "Aucun résultat trouvé dans la bibliothèque pour cette requête."

    answer = data.get("answer", "")
    sources = data.get("sources", [])

    lines: list[str] = []
    if answer:
        lines.append(answer)
    if sources:
        refs = " | ".join(
            f"{s.get('title','?')} — {s.get('author','?')}, p.{s.get('page','?')}"
            for s in sources[:limit]
        )
        lines.append(f"[Sources : {refs}]")

    return "\n\n".join(lines) if lines else "Réponse vide."


def search_books(query: str, limit: int = 3) -> str:
    """Recherche sémantique dans LibraryBrain via l'API job asynchrone.

    Soumet un job RAG puis sonde toutes les 2s jusqu'à réponse (max 3 min).
    Retourne un message d'erreur lisible si LibraryBrain est hors-ligne ou trop lent.
    """
    job_url = f"{_SERVER_BASE}/api/ask/job"
    payload = {"query": query, "response_format": "explication"}

    try:
        # 1. Soumettre le job
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(job_url, json=payload)
            resp.raise_for_status()
            job_id: str = resp.json()["job_id"]
        logger.info("search_books job soumis: %s", job_id)

        # 2. Polling jusqu'à completion
        status_url = f"{_SERVER_BASE}/api/ask/job/{job_id}"
        for attempt in range(_POLL_MAX):
            time.sleep(_POLL_INTERVAL)
            with httpx.Client(timeout=10.0) as client:
                status_resp = client.get(status_url)
                status_resp.raise_for_status()
                status_data = status_resp.json()

            status = status_data.get("status", "")
            if status == "done":
                result = status_data.get("result", {})
                logger.info("search_books done après %ds", int((attempt + 1) * _POLL_INTERVAL))
                return _parse_result(result, limit)
            if status == "error":
                err = status_data.get("error", "erreur inconnue")
                logger.error("search_books job error: %s", err)
                return f"LibraryBrain erreur RAG : {err}"

        return f"LibraryBrain timeout après {int(_POLL_MAX * _POLL_INTERVAL)}s — réessaie plus tard."

    except httpx.ConnectError:
        logger.warning("LibraryBrain inaccessible à %s", job_url)
        return "LibraryBrain inaccessible — serveur non démarré ou URL incorrecte."
    except httpx.HTTPStatusError as exc:
        logger.warning("LibraryBrain HTTP %s", exc.response.status_code)
        return f"LibraryBrain a retourné une erreur {exc.response.status_code}."
    except Exception as exc:
        logger.error("search_books erreur inattendue: %s", exc, exc_info=True)
        return f"Erreur lors de la recherche: {exc}"


def _is_domain_file(path: Path) -> bool:
    """Vérifie qu'un fichier est un domaine valide (liste de {title, content, tags})."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(data, list) and bool(data) and isinstance(data[0], dict) and "title" in data[0]
    except Exception:
        return False


def get_skills(domain: str) -> str:
    """Retourne les conventions du domaine sous forme de texte structuré pour le LLM.

    Charge depuis skills/{domain}.json (doit être un tableau de {title, content, tags}).
    Retourne la liste des domaines disponibles si le domaine est inconnu.
    """
    path = SKILLS_DIR / f"{domain}.json"
    if not path.exists() or not _is_domain_file(path):
        available = sorted(p.stem for p in SKILLS_DIR.glob("*.json") if _is_domain_file(p))
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
