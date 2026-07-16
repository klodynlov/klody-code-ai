"""Client MCP pour les outils LibraryBrain — appels directs sans protocole MCP."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path

import httpx
from config import (
    LIBRARY_DB_PATH,
    LIBRARYBRAIN_URL,
    SKILLS_DIR,
    librarybrain_auth_hint,
    librarybrain_headers,
)

logger = logging.getLogger(__name__)

_BASE_URL = LIBRARYBRAIN_URL
# Base sans le path (conservée pour les autres routes éventuelles)
_SERVER_BASE = _BASE_URL.rsplit("/api/", 1)[0]

# /api/ask est SYNCHRONE et lent (RAG mesuré à 37-42s le 16/07). L'ancienne archi
# job+polling tapait POST /api/ask/job, route supprimée côté LibraryBrain (f606a1a) :
# elle renvoyait 404. Le timeout doit couvrir la génération complète, pas un ACK.
_ASK_TIMEOUT = 120.0


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
    """Recherche sémantique dans LibraryBrain via POST /api/ask (synchrone).

    Retourne un message d'erreur lisible si LibraryBrain est hors-ligne ou trop lent.
    """
    ask_url = _BASE_URL
    payload = {"query": query, "response_format": "explication"}

    try:
        with httpx.Client(timeout=_ASK_TIMEOUT, headers=librarybrain_headers()) as client:
            started = time.monotonic()
            resp = client.post(ask_url, json=payload)
            resp.raise_for_status()
            logger.info("search_books done après %.1fs", time.monotonic() - started)
            return _parse_result(resp.json(), limit)

    except httpx.ConnectError:
        logger.warning("LibraryBrain inaccessible à %s", ask_url)
        return "LibraryBrain inaccessible — serveur non démarré ou URL incorrecte."
    except httpx.TimeoutException:
        logger.warning("LibraryBrain timeout après %ss", _ASK_TIMEOUT)
        return f"LibraryBrain timeout après {int(_ASK_TIMEOUT)}s — réessaie plus tard."
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        logger.warning("LibraryBrain HTTP %s sur %s", code, ask_url)
        if code == 401:
            return f"LibraryBrain a refusé l'accès (401) : {librarybrain_auth_hint()}"
        if code == 404:
            return f"LibraryBrain : route {ask_url} introuvable (404) — l'API a bougé."
        return f"LibraryBrain a retourné une erreur {code}."
    except Exception as exc:
        logger.error("search_books erreur inattendue: %s", exc, exc_info=True)
        return f"Erreur lors de la recherche: {exc}"


# ── Catalogue (métadonnée, NON gaté) ────────────────────────────────────────
# search_books interroge le RAG génératif (/api/ask) gaté par similarité
# cosinus ≥ 0.55 : une question par titre échoue le gate et renvoie « aucun
# résultat », même quand le livre EST au catalogue. catalog_lookup lit la table
# `books` en direct (FTS5 sur titre+auteur) — répond à « est-ce indexé / as-tu
# le livre X / quels livres sur Y » sans passer par le gate ni le serveur :8765.

def _catalog_connect() -> sqlite3.Connection:
    """Connexion LECTURE SEULE à la DB Library Brain (catalogue)."""
    return sqlite3.connect(f"file:{LIBRARY_DB_PATH}?mode=ro", uri=True)


# Mots vides FR/EN — exclus du MATCH sinon le repli OR matche « livre », « the »…
# et renvoie des faux positifs (prétend indexé un livre absent).
_CATALOG_STOPWORDS = frozenset({
    "le", "la", "les", "un", "une", "des", "de", "du", "au", "aux", "et", "ou",
    "en", "ce", "ces", "que", "qui", "sur", "par", "dans", "avec", "sans",
    "pour", "est", "as", "tu", "livre", "livres", "bouquin", "ouvrage",
    "the", "of", "to", "and", "or", "in", "on", "for", "with", "by", "book",
    "books", "do", "you", "have", "is", "it",
})


def _catalog_terms(query: str) -> list[str]:
    """Mots significatifs (≥ 2 lettres, hors mots vides) pour un MATCH FTS5, max 6."""
    return [
        t for t in re.findall(r"\w+", query.lower())
        if len(t) >= 2 and t not in _CATALOG_STOPWORDS
    ][:6]


def catalog_lookup(query: str, limit: int = 5) -> str:
    """Cherche un livre AU CATALOGUE par titre/auteur (métadonnée, instantané).

    FTS5 sur `books_fts(title, author)` — AND des termes d'abord, repli OR, puis
    repli LIKE si l'index FTS est malformé. Retourne titre, auteur, année, pages,
    format et date d'indexation. Ne passe PAS par le gate sémantique du RAG.
    """
    if not LIBRARY_DB_PATH.exists():
        return f"Catalogue LibraryBrain introuvable ({LIBRARY_DB_PATH})."

    terms = _catalog_terms(query)
    if not terms:
        return "Requête vide — précise un titre ou un auteur."

    # Littéral figé : la seule valeur interpolée dans le SQL ci-dessous. Toute donnée
    # issue de la requête utilisateur passe par un placeholder `?` (d'où les nosec B608).
    cols = "b.id, b.title, b.author, b.year, b.page_count, b.format, b.indexed_at"
    try:
        con = _catalog_connect()
    except sqlite3.Error as exc:
        logger.warning("catalog_lookup connexion KO: %s", exc)
        return "Catalogue LibraryBrain inaccessible."

    try:
        rows: list = []
        approx = False  # True si match par OR (tous les termes pas réunis) → approchant
        try:
            quoted = [f'"{t}"' for t in terms]
            for joiner in (" AND ", " OR "):
                rows = con.execute(
                    f"""
                    SELECT {cols}
                    FROM books_fts f JOIN books b ON b.id = f.rowid
                    WHERE books_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,  # nosec B608 — {cols} est un littéral ; le MATCH est paramétré
                    (joiner.join(quoted), limit),
                ).fetchall()
                if rows:
                    approx = joiner == " OR " and len(terms) > 1
                    break
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            logger.warning("catalog_lookup FTS KO (%s) → repli LIKE", exc)
            where = " OR ".join(["b.title LIKE ?", "b.author LIKE ?"])
            like = f"%{' '.join(terms)}%"
            rows = con.execute(
                # {cols} et {where} sont des littéraux ; les LIKE sont paramétrés
                f"SELECT {cols} FROM books b WHERE {where} LIMIT ?",  # nosec B608
                (like, like, limit),
            ).fetchall()
        total = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    except sqlite3.Error as exc:
        logger.error("catalog_lookup erreur DB: %s", exc, exc_info=True)
        return f"Erreur catalogue : {exc}"
    finally:
        con.close()

    if not rows:
        return (
            f"Aucun livre au catalogue pour « {query} » "
            f"(catalogue = {total} livres). Le livre n'est pas indexé."
        )

    header = (
        f"Aucune correspondance exacte pour « {query} ». "
        f"Livres approchants (ne contiennent pas tous les mots) :"
        if approx else
        f"{len(rows)} livre(s) au catalogue pour « {query} » :"
    )
    lines = [header]
    for r in rows:
        _id, title, author, year, pages, fmt, indexed = r
        meta = ", ".join(
            p for p in (
                str(year) if year else "",
                f"{pages} p." if pages else "",
                (fmt or "").upper(),
            ) if p
        )
        date = (indexed or "")[:10]
        lines.append(
            f"• {(title or '?').strip()} — {author or 'auteur inconnu'}"
            f"{f' ({meta})' if meta else ''}"
            f"{f' · indexé le {date}' if date else ''}"
        )
    return "\n".join(lines)


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
