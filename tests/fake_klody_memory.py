"""Double hermétique du paquet `klody_memory` (moteur RAG de Klody Core).

Pourquoi : `klody_memory` n'est pas distribuable — il vit hors du dépôt et n'est
installable que sur la machine de dev. En CI, `agent/semantic_memory.py` retombe
donc sur sa dégradation douce (`MEMORY_AVAILABLE = False`) et **toute sa logique
réelle reste non exécutée** : 28 % de couverture, alors que c'est le module qui
manipule le plus de SQL brut du dépôt (FTS5 à contenu externe, cascades FK,
nettoyage de tables virtuelles).

Ce double fournit la surface minimale que `semantic_memory` consomme, avec du
**vrai SQLite** derrière : le schéma, les triggers absents de FTS5, les cascades.
Ce qui est simulé, c'est uniquement ce qui exigerait un modèle ou un service —
les embeddings sont un sac de mots déterministe sur un petit vocabulaire.

Ce que ça teste réellement : l'INSERT/DELETE de `semantic_memory`, sa synchro
manuelle de `chunks_fts`, l'ordre de suppression imposé par les tables virtuelles,
et le formatage du rappel. Ce que ça ne teste pas : la pertinence sémantique du
vrai bge-m3 — c'est le rôle de `tests/test_semantic_memory.py`, qui tourne contre
le moteur réel quand il est présent.
"""
from __future__ import annotations

import math
import sqlite3
import sys
import types
from dataclasses import dataclass

# Vocabulaire du faux embedder : un mot présent → forte composante.
_VOCAB = ["python", "fastapi", "tauri", "react", "gateway", "mlx", "piano", "musique"]

# État partagé, rempli par configure() — le vrai paquet fait de même.
_settings = None
_connection_provider = None


def _fake_vec(text: str) -> list[float]:
    """Sac de mots normalisé. 0.1 de base : jamais de vecteur nul, norme définie."""
    t = (text or "").lower()
    v = [1.0 if w in t else 0.1 for w in _VOCAB]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


# ── klody_memory (racine) ─────────────────────────────────────────────────────


def ensure_memory_schema(conn: sqlite3.Connection) -> None:
    """Schéma « source → chunks » attendu par semantic_memory.

    `chunks_fts` est en **contenu externe** comme dans library-brain : pas de
    trigger, donc c'est bien `semantic_memory` qui doit synchroniser à la main —
    ce que ces tests vérifient.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS books (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            title     TEXT NOT NULL,
            author    TEXT,
            category  TEXT,
            format    TEXT,
            file_path TEXT
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            text        TEXT NOT NULL,
            page        INTEGER
        );
        CREATE TABLE IF NOT EXISTS book_embed_info (
            book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
            model   TEXT,
            dim     INTEGER
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='');
        """
    )
    conn.commit()


def configure(*, settings=None, connection=None) -> None:
    global _settings, _connection_provider
    _settings = settings
    _connection_provider = connection


# ── klody_memory.embedder ─────────────────────────────────────────────────────

_VEC_TABLE_CREATED = False


def _load_vec(conn: sqlite3.Connection) -> None:
    """Chargerait l'extension sqlite-vec. Ici : crée les tables que
    `_delete_sources` nettoie, pour que ce chemin soit réellement exercé."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vec_chunks     (chunk_id INTEGER PRIMARY KEY, embedding BLOB);
        CREATE TABLE IF NOT EXISTS vec_chunks_bit (chunk_id INTEGER PRIMARY KEY, embedding BLOB);
        """
    )


def embed_book(book_id: int) -> None:
    """Embedde les chunks du livre. Ouvre sa PROPRE connexion et reprend le verrou
    d'écriture, exactement comme le vrai moteur — c'est ce contrat qui oblige
    `semantic_memory.remember` à appeler hors de son bloc verrouillé.

    Le mémo `_VEC_TABLE_CREATED` est lu et écrit **sur le module injecté**, pas sur
    un global local : c'est cet attribut-là que `_reset_engine_memo()` remet à
    False, et le test qui vérifie le re-ciblage de base en dépend.
    """
    assert _connection_provider is not None, "configure() non appelé"
    embedder = sys.modules["klody_memory.embedder"]
    conn = _connection_provider.get_connection()
    try:
        with _connection_provider.db_write_lock():
            if not getattr(embedder, "_VEC_TABLE_CREATED", False):
                _load_vec(conn)
                embedder._VEC_TABLE_CREATED = True
            rows = conn.execute(
                "SELECT id, text FROM chunks WHERE book_id = ?", (book_id,)
            ).fetchall()
            for chunk_id, text in rows:
                blob = ",".join(f"{x:.6f}" for x in _fake_vec(text)).encode()
                conn.execute(
                    "INSERT OR REPLACE INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
                    (chunk_id, blob),
                )
            conn.execute(
                "INSERT OR REPLACE INTO book_embed_info (book_id, model, dim) VALUES (?, ?, ?)",
                (book_id, "fake-bge-m3", len(_VOCAB)),
            )
            conn.commit()
    finally:
        conn.close()


# ── klody_memory.retriever ────────────────────────────────────────────────────

_bit_table_cache = None
_cached_query_embedding = None


@dataclass
class _Result:
    book_id: int
    book_title: str
    author: str | None
    category: str | None
    text: str
    score: float
    relevance: float | None


class HybridRetriever:
    """Recherche vectorielle sur les embeddings réellement stockés par embed_book.

    Le vrai moteur fusionne FTS5 et vectoriel en RRF ; ici on classe au cosinus,
    ce qui suffit à valider ce dont `semantic_memory` est responsable : le passage
    du `category_filter` et le mapping Result → MemoryHit.
    """

    def search(self, query: str, top_k: int = 5, category_filter: str | None = None):
        assert _connection_provider is not None, "configure() non appelé"
        conn = _connection_provider.get_connection()
        try:
            sql = (
                "SELECT b.id, b.title, b.author, b.category, c.text "
                "FROM chunks c JOIN books b ON b.id = c.book_id"
            )
            params: tuple = ()
            if category_filter:
                sql += " WHERE b.category = ?"
                params = (category_filter,)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        qv = _fake_vec(query)
        scored = []
        for book_id, title, author, category, text in rows:
            rel = _cosine(qv, _fake_vec(text))
            scored.append(
                _Result(
                    book_id=book_id,
                    book_title=title,
                    author=author,
                    category=category,
                    text=text,
                    score=rel,
                    relevance=rel,
                )
            )
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]


# ── klody_memory.sanitizer ────────────────────────────────────────────────────

# Motifs d'injection de prompt les plus grossiers — le vrai sanitizer en couvre
# davantage ; ce qui compte ici est que semantic_memory reçoive des flags non
# vides et journalise, ce que le test vérifie.
_SUSPECT = ("ignore les instructions", "ignore previous", "system:", "```")


def sanitize(text: str, strict: bool = False) -> tuple[str, list[str]]:
    flags = [m for m in _SUSPECT if m in (text or "").lower()]
    cleaned = (text or "").replace("\n", " ").replace("\r", " ") if flags else (text or "")
    return cleaned, flags


# ── Installation / retrait dans sys.modules ───────────────────────────────────


def install() -> dict[str, object]:
    """Injecte le double dans sys.modules. Retourne les modules évincés, pour
    restauration — un test qui laisserait le faux en place contaminerait tout run
    ultérieur dans le même process."""
    root = types.ModuleType("klody_memory")
    root.ensure_memory_schema = ensure_memory_schema
    root.configure = configure

    embedder = types.ModuleType("klody_memory.embedder")
    embedder._VEC_TABLE_CREATED = _VEC_TABLE_CREATED
    embedder._load_vec = _load_vec
    embedder.embed_book = embed_book
    embedder.get_embedding = _fake_vec

    retriever = types.ModuleType("klody_memory.retriever")
    retriever._bit_table_cache = _bit_table_cache
    retriever._cached_query_embedding = _cached_query_embedding
    retriever.HybridRetriever = HybridRetriever
    retriever.get_embedding = _fake_vec

    sanitizer = types.ModuleType("klody_memory.sanitizer")
    sanitizer.sanitize = sanitize

    root.embedder = embedder
    root.retriever = retriever
    root.sanitizer = sanitizer

    names = {
        "klody_memory": root,
        "klody_memory.embedder": embedder,
        "klody_memory.retriever": retriever,
        "klody_memory.sanitizer": sanitizer,
    }
    evicted = {n: sys.modules.get(n) for n in names}
    sys.modules.update(names)
    return evicted


def uninstall(evicted: dict[str, object]) -> None:
    for name, module in evicted.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def reset_state() -> None:
    """Remet l'état module à zéro entre deux tests (embed_book est mémoïsé)."""
    global _VEC_TABLE_CREATED, _settings, _connection_provider
    _VEC_TABLE_CREATED = False
    _settings = None
    _connection_provider = None
