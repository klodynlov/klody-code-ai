"""Distillation d'un THÈME multi-livres depuis la DB Library Brain (FTS5).

Chaînon entre `learn_from_books` (RAG lite : quelques extraits via :8765) et le
pipeline brain-distiller premium (Claude Code, /distill) : Klody classe lui-même
les livres du corpus local par pertinence FTS5, moissonne les meilleurs extraits
(page/chapitre) et synthétise un digest couche A `skills/digest_<slug>.json`,
routé ensuite comme n'importe quel skill.

Règles héritées du pipeline premium (retours terrain 12/06) :
- DB ouverte en LECTURE SEULE (URI mode=ro) — jamais d'écriture ici.
- Index FTS parfois désynchronisé (« database disk image is malformed ») →
  repli automatique sur des requêtes LIKE (plus lent, jamais bloquant).
- `description` du digest = mots-clés techniques + vocabulaire d'USAGE.
- `content` : règles critiques dans les ~800 premiers caractères (cap du chemin
  coder), code data-driven, prose des auteurs jamais recopiée (cf. prompt).
- Slug `digest_<thème>` — jamais préfixé `distiller` (gate du routeur).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

from config import LIBRARY_DB_PATH, SKILLS_DIR

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "distill-theme.md"

# Petits mots non discriminants, retirés de la requête FTS (même esprit que
# tools.skills._STOP, réduit aux parasites fréquents d'un libellé de thème).
_STOP = {
    "les", "des", "une", "pour", "avec", "dans", "sur", "the", "and", "for",
    "with", "tout", "tous", "comment", "faire",
}


def _terms(theme: str) -> list[str]:
    """Termes de recherche : alphanumériques ≥3 chars, hors stopwords, max 6.

    Alphanumérique STRICT : c'est aussi la barrière d'injection FTS5 (pas de
    guillemets, parenthèses ou opérateurs transmissibles à MATCH).
    """
    words = re.findall(r"[a-zà-ÿ0-9]{3,}", theme.lower())
    return [w for w in words if w not in _STOP][:6]


def _connect() -> sqlite3.Connection:
    """Connexion LECTURE SEULE à la DB Library Brain."""
    if not LIBRARY_DB_PATH.exists():
        raise FileNotFoundError(
            f"DB Library Brain introuvable : {LIBRARY_DB_PATH} "
            "(configurer LIBRARY_DB_PATH)"
        )
    return sqlite3.connect(f"file:{LIBRARY_DB_PATH}?mode=ro", uri=True)


def _like_clause(terms: list[str]) -> tuple[str, list[str]]:
    """Clause WHERE en OR de LIKE — repli quand l'index FTS est malformed."""
    parts = " OR ".join(["c.text LIKE ?"] * len(terms))
    return f"({parts})", [f"%{t}%" for t in terms]


def rank_books(theme: str, k: int = 4) -> list[dict]:
    """Classe les livres du corpus par nombre de chunks matchant le thème.

    Voie rapide : FTS5 (`OR` des termes). Sur DatabaseError/OperationalError
    (index désynchronisé, table absente) → repli LIKE, plus lent mais sûr.
    """
    terms = _terms(theme)
    if not terms:
        return []
    con = _connect()
    try:
        try:
            # AND d'abord : tous les termes dans le MÊME chunk. Sans ça, un
            # terme fréquent isolé (« rest », « design ») fait gagner de gros
            # livres hors sujet au comptage brut. Repli OR si AND ne sort rien.
            rows: list = []
            for op in (" AND ", " OR "):
                rows = con.execute(
                    """
                    SELECT b.id, b.title, b.author, b.format, COUNT(*) AS hits
                    FROM chunks_fts f
                    JOIN chunks c ON c.id = f.rowid
                    JOIN books b ON b.id = c.book_id
                    WHERE chunks_fts MATCH ?
                    GROUP BY b.id ORDER BY hits DESC LIMIT ?
                    """,
                    (op.join(terms), k),
                ).fetchall()
                if rows:
                    break
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
            logger.warning("[distill_theme] FTS KO (%s) → repli LIKE", exc)
            where, params = _like_clause(terms[:4])
            rows = con.execute(
                f"""
                SELECT b.id, b.title, b.author, b.format, COUNT(*) AS hits
                FROM chunks c JOIN books b ON b.id = c.book_id
                WHERE {where}
                GROUP BY b.id ORDER BY hits DESC LIMIT ?
                """,
                (*params, k),
            ).fetchall()
    finally:
        con.close()
    return [
        {"id": r[0], "title": r[1], "author": r[2], "format": r[3], "hits": r[4]}
        for r in rows
    ]


def harvest(books: list[dict], theme: str, per_book: int = 5, clip: int = 900) -> list[dict]:
    """Moissonne les meilleurs extraits (texte, page, chapitre) de chaque livre."""
    terms = _terms(theme)
    if not terms or not books:
        return []
    out: list[dict] = []
    con = _connect()
    try:
        for b in books:
            try:
                rows = []
                for op in (" AND ", " OR "):  # même logique AND-first que rank_books
                    rows = con.execute(
                        """
                        SELECT c.text, c.page, c.chapter
                        FROM chunks_fts f JOIN chunks c ON c.id = f.rowid
                        WHERE chunks_fts MATCH ? AND c.book_id = ?
                        ORDER BY rank LIMIT ?
                        """,
                        (op.join(terms), b["id"], per_book),
                    ).fetchall()
                    if rows:
                        break
            except (sqlite3.DatabaseError, sqlite3.OperationalError):
                where, params = _like_clause(terms[:4])
                rows = con.execute(
                    f"""
                    SELECT c.text, c.page, c.chapter
                    FROM chunks c
                    WHERE {where} AND c.book_id = ?
                    LIMIT ?
                    """,
                    (*params, b["id"], per_book),
                ).fetchall()
            for text, page, chapter in rows:
                out.append({
                    "book_id": b["id"],
                    "text": (text or "")[:clip],
                    "page": page,
                    "chapter": chapter,
                })
    finally:
        con.close()
    return out


def _render_corpus(books: list[dict], extracts: list[dict]) -> str:
    """Bloc texte « corpus » substitué dans le prompt de synthèse."""
    lines: list[str] = []
    for b in books:
        lines.append(f"\n## {b['title']} — {b['author'] or 'auteur inconnu'} ({b['hits']} hits)")
        for e in extracts:
            if e["book_id"] != b["id"]:
                continue
            loc = f"p.{e['page']}" if e["page"] else (e["chapter"] or "?")
            lines.append(f"[{loc}] {e['text']}")
    return "\n".join(lines)


def _parse_llm_json(text: str) -> dict:
    """Extrait l'objet JSON de la réponse LLM (bloc ```json ou accolades nues)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    data = json.loads(raw)
    missing = {"name", "description", "content"} - set(data)
    if missing:
        raise ValueError(f"champs manquants dans la synthèse : {sorted(missing)}")
    return data


def _digest_slug(theme: str, slug: str = "") -> str:
    """Slug final `digest_<...>` : ascii [a-z0-9_], ≤40 chars, jamais `distiller*`."""
    import unicodedata
    base = slug or theme
    ascii_base = unicodedata.normalize("NFKD", base.lower()).encode("ascii", "ignore").decode("ascii")
    body = re.sub(r"[^a-z0-9]+", "_", ascii_base).strip("_")
    body = re.sub(r"^(digest_|distiller_?)+", "", body)  # pas de double préfixe ni de gate
    return ("digest_" + body)[:40].rstrip("_") or "digest_theme"


def _write_digest(slug: str, name: str, description: str, content: str,
                  code_compatible: bool) -> Path:
    """Écrit le digest couche A — même format que tools.skills.save_skill,
    mais slug contrôlé (convention `digest_<thème>`)."""
    from datetime import datetime

    SKILLS_DIR.mkdir(exist_ok=True)
    data = {
        "name": name,
        "slug": slug,
        "description": description,
        "content": content,
        "updated": datetime.now().isoformat(),
    }
    if code_compatible:
        data["code_compatible"] = True
    path = SKILLS_DIR / f"{slug}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def distill_theme(theme: str, slug: str = "", code_compatible: bool = False,
                  llm=None) -> str:
    """Distille un thème multi-livres en digest couche A.

    Args:
        theme: thème à distiller (ex: "optimisation WebGL", "design d'API REST")
        slug: corps du slug (auto depuis le thème si vide) — préfixé `digest_`
        code_compatible: True si le digest doit aussi servir les tâches de code
            (injecté compact au coder ; règles critiques en tête de content)
        llm: client LLM (objet exposant stream_chat) — injecté par l'orchestrateur
    """
    if llm is None:
        return "Erreur : distill_theme nécessite le client LLM (câblage orchestrateur)."

    try:
        books = rank_books(theme)
    except FileNotFoundError as exc:
        return f"Erreur : {exc}"
    if not books:
        return (
            f"Aucun livre du corpus ne matche « {theme} ». "
            "Reformuler avec les mots techniques du domaine (FR ou EN)."
        )

    extracts = harvest(books, theme)
    if not extracts:
        return f"Livres trouvés mais aucun extrait exploitable pour « {theme} »."

    prompt = _PROMPT_PATH.read_text()
    prompt = prompt.replace("{{theme}}", theme)
    prompt = prompt.replace("{{corpus}}", _render_corpus(books, extracts))

    # 2 tentatives : un modèle local rate parfois une génération (réponse vide
    # ou hors format) — constaté au premier essai E2E, le retry suffit.
    digest = None
    last_err: Exception | None = None
    for _ in range(2):
        content_resp, _ = llm.stream_chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Thème : {theme}. Produis le JSON du digest."},
            ],
            tools=None,
            silent=True,
            temperature=0.2,
            max_tokens=6000,
        )
        try:
            digest = _parse_llm_json(content_resp or "")
            break
        except (ValueError, json.JSONDecodeError) as exc:
            last_err = exc
            logger.warning("[distill_theme] synthèse illisible, retry : %s", exc)
    if digest is None:
        return f"Synthèse illisible après 2 tentatives : {last_err}. Relancer distill_theme."

    final_slug = _digest_slug(theme, slug)
    path = _write_digest(
        final_slug, digest["name"], digest["description"],
        digest["content"], code_compatible,
    )
    logger.info("[distill_theme] Digest écrit : %s (%d livres, %d extraits)",
                final_slug, len(books), len(extracts))

    titles = "\n".join(f"  - {b['title']} ({b['hits']} hits)" for b in books)
    return (
        f"✅ Thème distillé : « {theme} »\n"
        f"Sources ({len(books)} livres, {len(extracts)} extraits) :\n{titles}\n"
        f"Digest : {path.name} (content {len(digest['content'])} caractères, "
        f"code_compatible={code_compatible})\n"
        f"Actif dès la prochaine requête (skills rechargés à chaque tour)."
    )
