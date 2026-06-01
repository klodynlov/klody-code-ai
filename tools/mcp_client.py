"""Client MCP pour les outils LibraryBrain — appels directs sans protocole MCP."""
from __future__ import annotations

import json
import logging
import re
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
        with httpx.Client(timeout=10.0) as client:
            # 1. Soumettre le job
            resp = client.post(job_url, json=payload)
            resp.raise_for_status()
            job_id: str = resp.json()["job_id"]
            logger.info("search_books job soumis: %s", job_id)

            # 2. Polling jusqu'à completion (même client réutilisé)
            status_url = f"{_SERVER_BASE}/api/ask/job/{job_id}"
            for attempt in range(_POLL_MAX):
                time.sleep(_POLL_INTERVAL)
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


def _claude_code_skills_for(topic: str, limit: int = 4) -> list[dict]:
    """Retourne les compétences du domaine claude_code les plus pertinentes pour un sujet.

    Matching lexical simple : score chaque entrée par le nombre de mots du sujet (>2 lettres)
    présents dans son titre, son contenu ou ses tags. Retourne les `limit` meilleures.
    """
    path = SKILLS_DIR / "claude_code.json"
    if not _is_domain_file(path):
        return []
    try:
        skills = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    words = {w for w in re.findall(r"\w+", topic.lower()) if len(w) > 2}
    if not words:
        return []

    scored: list[tuple[int, dict]] = []
    for s in skills:
        haystack = f"{s.get('title', '')} {s.get('content', '')} {' '.join(s.get('tags', []))}".lower()
        score = sum(1 for w in words if w in haystack)
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:limit]]


def _format_claude_code_skills(skills: list[dict]) -> str:
    """Formate des compétences claude_code en bloc texte injectable dans une skill apprise."""
    lines = ["## Principes d'ingénierie applicables (domaine claude_code)"]
    for s in skills:
        lines.append(f"\n### {s['title']}\n{s['content']}")
    return "\n".join(lines)


def learn_from_books(topic: str, skill_name: str = "") -> str:
    """Apprend un sujet et le sauvegarde comme skill permanente.

    Combine deux sources puis save_skill en une seule action :
    1. Recherche sémantique dans LibraryBrain (livres)
    2. Principes d'ingénierie pertinents du domaine claude_code (local)
    3. Sauvegarde en tant que compétence Klody réutilisable

    Reste utile même si LibraryBrain est hors-ligne, tant que claude_code a des entrées pertinentes.

    Args:
        topic: Sujet à apprendre (ex: "design patterns Python", "optimisation SQL")
        skill_name: Nom de la skill (auto-généré si vide)
    """
    from tools.skills import save_skill

    book_result = search_books(topic, limit=5)
    book_failed = book_result.startswith(("Aucun", "LibraryBrain", "Erreur"))

    cc_skills = _claude_code_skills_for(topic)

    if book_failed and not cc_skills:
        return f"Impossible d'apprendre sur « {topic} » : {book_result}"

    parts: list[str] = []
    sources: list[str] = []
    if not book_failed:
        parts.append(book_result)
        sources.append("LibraryBrain")
    if cc_skills:
        parts.append(_format_claude_code_skills(cc_skills))
        sources.append("domaine claude_code")
    content = "\n\n".join(parts)

    name = skill_name or f"Connaissances : {topic[:50]}"
    description = f"Appris depuis {' + '.join(sources)} — {topic}"

    save_result = save_skill(name, description, content)
    logger.info("[learn_from_books] Skill créée : %s (sources: %s)", name, sources)

    extra = f"\n  + {len(cc_skills)} principe(s) claude_code intégré(s)" if cc_skills else ""
    return (
        f"✅ Nouvelle connaissance acquise !\n"
        f"  Sujet : {topic}\n"
        f"  Sources : {', '.join(sources)}{extra}\n"
        f"  {save_result}\n\n"
        f"Contenu appris :\n{content[:1500]}"
    )


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
