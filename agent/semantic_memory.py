"""Mémoire sémantique de Klody — 3ᵉ consommateur du « memory bus » klody_memory
(moteur RAG réutilisable de Klody Core, extrait de library-brain ; vocalbrain
est le 2ᵉ consommateur).

Rôle : archive ILLIMITÉE et interrogeable sémantiquement, par-dessus la mémoire
long-terme plate (agent/long_term_memory.py) qui, elle, est capée à l'injection
(15 faits/catégorie) ET purgée sur disque (60 entrées "context") : un fait ancien
finit invisible puis perdu. Ici, rien n'est jamais purgé ; on retrouve à la
demande via l'outil `rappeler_memoire` (recherche hybride FTS5 + vectorielle).

Le moteur raisonne sur un modèle « source → chunks » (cf. klody_memory.schema) ;
Klody y mappe son domaine ainsi :
    source (table books)   ← un souvenir : un fait long-terme, une session passée
        title    = clé du fait (ou "session:<id>")
        category = `kind` ("user" | "project" | "preference" | "context" | "session")
        author   = sous-libellé optionnel (titre humain d'une session)
    chunk  (table chunks)  ← le texte indexé de ce souvenir

Embeddings : provider "st" par défaut (sentence-transformers in-process) — l'API
Klody est un service long-vécu (launchd com.klody.api), le modèle bge-m3 se charge
une fois par process ; AUCUNE dépendance au daemon Ollama (cf. décommission
embeddings de Klody Core). Basculer via SEMANTIC_MEMORY_PROVIDER=ollama au besoin.

Dégradation douce : si le paquet klody-memory (ou sqlite-vec / sentence-transformers)
manque, MEMORY_AVAILABLE passe à False, les fonctions lèvent un message clair et
le miroir depuis LongTermMemory est silencieusement ignoré — le cœur de Klody
n'est jamais impacté.
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# ── Dépendance optionnelle : le moteur réutilisable ───────────────────────────
try:
    import klody_memory
    import klody_memory.embedder as _km_embedder
    import klody_memory.retriever as _km_retriever
    from klody_memory.embedder import _load_vec, embed_book as _embed_book
    from klody_memory.retriever import HybridRetriever
    from klody_memory.sanitizer import sanitize as _sanitize

    MEMORY_AVAILABLE = True
    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # ImportError, ou sqlite-vec/ollama/tqdm absents
    MEMORY_AVAILABLE = False
    _IMPORT_ERROR = exc

    def _sanitize(text: str, strict: bool = False):  # repli identité, jamais
        return text, []                              # atteint : recall_for_llm
                                                     # sort avant si indisponible


# ── Réglages (satisfont structurellement klody_memory.SettingsProvider) ───────

@dataclass(frozen=True)
class MemorySettings:
    """Surface complète attendue par le moteur, taillée pour une mémoire de recall :
    pas de gate sémantique (le retriever renvoie toujours les plus proches), pas de
    traduction cross-langue ni de cross-encoder, pas de génération (Klody n'utilise
    QUE l'embedder + le retriever)."""
    # Embeddings
    embed_model: str = "bge-m3"
    embed_batch_size: int = 16
    embed_provider: str = "st"
    embed_st_model: str = "BAAI/bge-m3"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "mistral:latest"
    # Retrieval
    rag_top_k: int = 5
    rag_min_books: int = 0
    rag_min_relevance: float = 0.0
    rerank: bool = True
    rerank_model: str = ""          # rerank cosinus (pas de cross-encoder)
    rerank_pool: int = 0
    rag_cross_lingual: bool = False
    rag_query_lang: str = "en"
    # Génération — non utilisée ici, valeurs neutres
    llm_num_predict: int = 0
    llm_num_ctx: int = 0
    llm_temperature: float = 0.0
    llm_seed: int = 0
    llm_keep_alive: str = ""
    llm_provider: str = "ollama"
    llm_base_url: str = ""
    llm_api_key: str = ""
    code_model: str = ""
    repo_aware: bool = False
    repo_follow_imports: bool = False
    cross_domain: bool = False
    # Recherche web
    web_search_enabled: bool = False
    web_search_max_results: int = 3


# ── Provider de connexion (satisfait klody_memory.ConnectionProvider) ─────────

class _ConnectionProvider:
    """Connexion SQLite FRAÎCHE par appel (le moteur la ferme lui-même) + verrou
    d'écriture process. Pragmas alignés sur core.database de library-brain."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def db_write_lock(self) -> threading.Lock:
        return self._lock


# ── État process ──────────────────────────────────────────────────────────────

_provider: _ConnectionProvider | None = None
_configured_db: Path | None = None


@dataclass
class MemoryHit:
    """Un résultat de rappel sémantique."""
    book_id: int
    title: str
    author: str | None
    kind: str | None
    text: str
    score: float                      # score de fusion RRF
    relevance: float | None        # similarité cosinus brute [0,1] (None si FTS5 seul)


def _require_available() -> None:
    if not MEMORY_AVAILABLE:
        raise RuntimeError(
            "Mémoire sémantique indisponible : le paquet 'klody-memory' (et "
            "sqlite-vec / sentence-transformers) n'est pas installé :\n"
            "    .venv/bin/pip install -e ~/klody-core/memory sentence-transformers sqlite-vec ollama\n"
            f"(détail import : {_IMPORT_ERROR!r})"
        )


def _reset_engine_memo() -> None:
    """Réinitialise les mémos PROCESS du moteur. Indispensable quand on re-cible
    une AUTRE base : l'embedder mémorise si vec_chunks a été créée (sinon il ne
    (re)crée pas la table sur la nouvelle base → embeddings silencieusement non
    stockés)."""
    if hasattr(_km_embedder, "_VEC_TABLE_CREATED"):
        _km_embedder._VEC_TABLE_CREATED = False
    if hasattr(_km_retriever, "_bit_table_cache"):
        _km_retriever._bit_table_cache = None
    cache = getattr(_km_retriever, "_cached_query_embedding", None)
    if cache is not None and hasattr(cache, "cache_clear"):
        cache.cache_clear()


def configure_memory(db_path: Path | str | None = None) -> Path:
    """Branche klody_memory sur la base mémoire de Klody. Idempotent ; ré-appeler
    avec un autre `db_path` re-cible proprement. Retourne le chemin configuré."""
    _require_available()
    global _provider, _configured_db
    path = Path(db_path) if db_path else config.SEMANTIC_MEMORY_DB
    path.parent.mkdir(parents=True, exist_ok=True)

    settings = MemorySettings(
        embed_provider=(config.SEMANTIC_MEMORY_PROVIDER or "st").strip().lower(),
    )

    provider = _ConnectionProvider(path)
    conn = provider.get_connection()
    try:
        klody_memory.ensure_memory_schema(conn)
    finally:
        conn.close()

    _reset_engine_memo()
    klody_memory.configure(settings=settings, connection=provider)
    _provider = provider
    _configured_db = path
    return path


def is_ready() -> bool:
    """Le moteur est-il branché (deps présentes ET configure_memory appelé) ?"""
    return MEMORY_AVAILABLE and _provider is not None


def _ensure_configured(db_path: Path | str | None) -> None:
    """Auto-configure si besoin ; un db_path explicite l'emporte toujours."""
    if db_path is not None:
        p = Path(db_path)
        if _provider is None or _configured_db != p:
            configure_memory(db_path=p)
    elif _provider is None:
        configure_memory()


def _delete_sources(conn: sqlite3.Connection, title: str, kind: str | None) -> int:
    """Supprime les sources (title[, kind]) et TOUTES leurs traces d'index.

    Ordre imposé : chunks_fts (contenu externe, pas de trigger → 'delete' manuel
    avec l'ANCIEN texte) et vec_chunks[_bit] (tables virtuelles, hors FK) d'abord,
    puis DELETE books — les FK ON DELETE CASCADE emportent chunks + book_embed_info.
    Appelant : tient le verrou d'écriture et commit."""
    if kind:
        rows = conn.execute(
            "SELECT id FROM books WHERE title = ? AND category = ?", (title, kind)
        ).fetchall()
    else:
        rows = conn.execute("SELECT id FROM books WHERE title = ?", (title,)).fetchall()
    if not rows:
        return 0

    vec_loaded = True
    try:
        _load_vec(conn)
    except Exception:
        vec_loaded = False   # pas d'extension → pas de table vec à nettoyer

    for (book_id,) in rows:
        chunks = conn.execute(
            "SELECT id, text FROM chunks WHERE book_id = ?", (book_id,)
        ).fetchall()
        for chunk_id, text in chunks:
            conn.execute(
                "INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)",
                (chunk_id, text),
            )
            if vec_loaded:
                for table in ("vec_chunks", "vec_chunks_bit"):
                    # OperationalError = table pas encore créée (aucun embedding stocké)
                    with contextlib.suppress(sqlite3.OperationalError):
                        conn.execute(f"DELETE FROM {table} WHERE chunk_id = ?", (chunk_id,))
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    return len(rows)


def remember(
    text: str,
    *,
    title: str,
    kind: str = "context",
    author: str | None = None,
    replace: bool = False,
    db_path: Path | str | None = None,
) -> int:
    """Mémorise un texte et l'embedde. Retourne l'id de la source (book_id).

    `kind` est filtrable au rappel. `replace=True` supprime d'abord les sources
    de même (title, kind) — c'est la sémantique « mise à jour par clé » du miroir
    LongTermMemory (sinon chaque update dupliquerait le souvenir)."""
    _require_available()
    _ensure_configured(db_path)
    assert _provider is not None
    text = (text or "").strip()
    title = (title or "").strip()
    if not text:
        raise ValueError("remember(): texte vide")
    if not title:
        raise ValueError("remember(): title requis")

    conn = _provider.get_connection()
    try:
        with _provider.db_write_lock():
            if replace:
                _delete_sources(conn, title, kind)
            cur = conn.execute(
                "INSERT INTO books (title, author, category, format, file_path) "
                "VALUES (?, ?, ?, 'memory', ?)",
                (title, author or kind, kind, f"klody://{kind}/{uuid.uuid4().hex}"),
            )
            book_id = int(cur.lastrowid or 0)
            cur2 = conn.execute(
                "INSERT INTO chunks (book_id, chunk_index, text, page) VALUES (?, 0, ?, NULL)",
                (book_id, text),
            )
            chunk_id = int(cur2.lastrowid or 0)
            # FTS5 contenu externe : synchro manuelle (pas de trigger sur chunks),
            # exactement comme library-brain à l'indexation.
            conn.execute(
                "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
                (chunk_id, text),
            )
            conn.commit()
    finally:
        conn.close()

    # Embeddings : embed_book ouvre sa PROPRE connexion (même base) et reprend le
    # verrou d'écriture en interne → on l'appelle hors de notre bloc verrouillé.
    _embed_book(book_id)
    return book_id


def forget(
    title: str,
    *,
    kind: str | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Oublie les souvenirs de ce `title` (option : restreint à un `kind`).
    Retourne le nombre de sources supprimées."""
    _require_available()
    _ensure_configured(db_path)
    assert _provider is not None
    conn = _provider.get_connection()
    try:
        with _provider.db_write_lock():
            removed = _delete_sources(conn, title.strip(), kind)
            conn.commit()
    finally:
        conn.close()
    return removed


def recall(
    query: str,
    *,
    top_k: int = 5,
    kind: str | None = None,
    db_path: Path | str | None = None,
) -> list[MemoryHit]:
    """Rappel sémantique hybride (FTS5 + vectoriel, fusion RRF). `kind` filtre par
    type de souvenir (réutilise le `category_filter` du moteur)."""
    _require_available()
    _ensure_configured(db_path)
    results = HybridRetriever().search(query, top_k=top_k, category_filter=kind)
    return [
        MemoryHit(
            book_id=r.book_id,
            title=r.book_title,
            author=r.author,
            kind=r.category,
            text=r.text,
            score=r.score,
            relevance=r.relevance,
        )
        for r in results
    ]


def recall_for_llm(query: str, top_k: int = 5, kind: str | None = None) -> str:
    """Rappel formaté pour la boucle ReAct (outil `rappeler_memoire`).
    Ne lève jamais : toute indisponibilité devient un message lisible par le LLM."""
    if not config.SEMANTIC_MEMORY_ENABLED:
        return "Mémoire sémantique désactivée (SEMANTIC_MEMORY_ENABLED=0)."
    if not MEMORY_AVAILABLE:
        return ("Mémoire sémantique indisponible (paquet klody-memory absent). "
                "La mémoire long-terme du prompt reste la seule source.")
    try:
        hits = recall(query, top_k=max(1, min(int(top_k or 5), 20)), kind=kind or None)
    except Exception as e:
        logger.warning("[semantic_memory] recall en échec : %s", e)
        return f"Rappel impossible ({e.__class__.__name__}: {e})."
    if not hits:
        suffix = f" (type={kind})" if kind else ""
        return f"Aucun souvenir pertinent pour « {query} »{suffix}."

    lines = [f"Souvenirs les plus proches de « {query} » :"]
    for i, h in enumerate(hits, 1):
        label = h.title if (not h.author or h.author == h.kind) else f"{h.title} — {h.author}"
        rel = f", similarité {h.relevance:.2f}" if h.relevance is not None else ""
        # ASI06 : l'archive n'est JAMAIS purgée → un souvenir empoisonné écrit
        # avant la barrière d'écriture (ou par un autre écrivain du bus) vivrait
        # pour toujours. Barrière au rendu : le LLM ne reçoit jamais de brut.
        label, _ = _sanitize(label, strict=True)
        text, flags = _sanitize(h.text, strict=True)
        if flags:
            # %r : un titre de souvenir porteur de \n/\r forgerait de fausses
            # lignes de log (log injection, CodeQL) — repr les échappe.
            logger.warning("[semantic_memory] injection suspecte strippée au rappel "
                           "(titre=%r, flags=%s)", h.title, flags)
        lines.append(f"{i}. [{h.kind}] {label}{rel}\n   {text}")
    return "\n".join(lines)


def reset_memory(db_path: Path | str | None = None) -> None:
    """Vide la mémoire (supprime le fichier de base + WAL/SHM) et réinitialise les
    mémos process. Surtout utile pour les tests."""
    global _provider, _configured_db
    path = Path(db_path) if db_path else (_configured_db or config.SEMANTIC_MEMORY_DB)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()
    if MEMORY_AVAILABLE:
        _reset_engine_memo()
    _provider = None
    _configured_db = None
