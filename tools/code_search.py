"""Recherche sémantique dans le code via embeddings (Roadmap v2 #6).

Indexe chaque fichier source du projet et permet de trouver les fichiers
les plus pertinents pour une question en langage naturel.

Backend embeddings : Ollama bge-m3 (déjà disponible localement).
Index : en mémoire (numpy + dict). Pas de persistance pour l'instant.
Rebuild incrémental : on ne re-embed que les fichiers modifiés (cache mtime).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Endpoint Ollama embeddings (toujours sur le port Ollama, même si BACKEND=mlx
# pour le LLM principal — Ollama reste utilisé pour l'embedder léger).
_OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
_EMBED_MODEL = "bge-m3"

# Extensions à indexer
_INDEXABLE_EXT = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".md", ".yml", ".yaml", ".toml",
})

# Dossiers à skipper
_SKIP_DIRS = frozenset({
    ".venv", "venv", "__pycache__", ".git", "node_modules",
    "dist", "build", ".cache", "htmlcov", ".pytest_cache", ".mypy_cache",
    "_preview", "preview", "logs", "bench/results", ".claude", "imports",
})

# Taille max d'un chunk de fichier (en chars) avant troncation
_MAX_CHUNK_CHARS = 4000

# Garde-fou anti-emballement : nombre max de fichiers indexés. PROJECT_ROOT peut
# être un dossier large (ex. ~/Projets) ; sans plafond, un premier build
# embarquerait des milliers de fichiers. Au-delà, on s'arrête (avec un warning).
_MAX_INDEX_FILES = 1500


@dataclass
class FileEmbedding:
    rel_path: str
    mtime: float
    vec: list[float] = field(default_factory=list)
    preview: str = ""  # 200 premiers chars pour affichage


@dataclass
class SearchHit:
    rel_path: str
    score: float
    preview: str


# ---------------------------------------------------------------------------- #
# Client Ollama embeddings                                                     #
# ---------------------------------------------------------------------------- #


def _embed_batch(texts: list[str], timeout: float = 60.0) -> list[list[float]]:
    """Appelle Ollama /api/embed pour un batch de textes."""
    try:
        resp = httpx.post(
            _OLLAMA_EMBED_URL,
            json={"model": _EMBED_MODEL, "input": texts},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("embeddings", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Embedding call failed: %s", exc)
        return [[] for _ in texts]


# ---------------------------------------------------------------------------- #
# Cosine similarity sans numpy                                                 #
# ---------------------------------------------------------------------------- #


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------- #
# EmbeddingIndex                                                               #
# ---------------------------------------------------------------------------- #


class EmbeddingIndex:
    """Index par embedding des fichiers du projet."""

    def __init__(self, project_root: Path):
        self.root: Path = Path(project_root).resolve()
        self._index: dict[str, FileEmbedding] = {}
        self._available: bool | None = None  # None = pas encore testé

    def is_available(self) -> bool:
        """Vérifie qu'Ollama répond et que bge-m3 est dispo (lazy, cache)."""
        if self._available is not None:
            return self._available
        try:
            r = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
            self._available = any(_EMBED_MODEL in m for m in models)
        except (httpx.HTTPError, ValueError):
            self._available = False
        return self._available

    def _iter_source_files(self):
        # os.walk + élagage IN-PLACE de dirnames : on ne DESCEND jamais dans les
        # dossiers ignorés (node_modules, .venv, .git…). `rglob("*")` les
        # énumérait avant de les filtrer — sur une grosse racine (~/Projets, avec
        # des node_modules partout) c'était un coût de parcours énorme à chaque
        # refresh, donc à chaque recherche.
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if Path(fn).suffix in _INDEXABLE_EXT:
                    yield Path(dirpath) / fn

    def refresh(self, batch_size: int = 16) -> int:
        """Re-indexe les fichiers ajoutés/modifiés. Retourne nb d'updates."""
        if not self.is_available():
            return 0

        # Identifier les fichiers à (re)indexer
        to_update: list[tuple[str, float, str]] = []  # (rel, mtime, content)
        seen: set[str] = set()
        for path in self._iter_source_files():
            if len(seen) >= _MAX_INDEX_FILES:
                logger.warning(
                    "[EmbeddingIndex] plafond de %d fichiers atteint sous %s — "
                    "le reste est ignoré (réduis la racine ou _MAX_INDEX_FILES)",
                    _MAX_INDEX_FILES, self.root,
                )
                break
            try:
                mtime = path.stat().st_mtime
                rel = str(path.relative_to(self.root))
                seen.add(rel)
            except OSError:
                continue
            cached = self._index.get(rel)
            if cached and cached.mtime >= mtime:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Truncate très gros fichiers
            if len(content) > _MAX_CHUNK_CHARS:
                content = content[:_MAX_CHUNK_CHARS]
            # Préfixe avec le chemin pour aider le semantic match
            embed_input = f"# Fichier: {rel}\n\n{content}"
            to_update.append((rel, mtime, embed_input))

        # Embed par batch
        updated = 0
        for i in range(0, len(to_update), batch_size):
            batch = to_update[i:i + batch_size]
            texts = [t[2] for t in batch]
            vecs = _embed_batch(texts)
            for (rel, mtime, content), vec in zip(batch, vecs, strict=False):
                if not vec:
                    continue
                self._index[rel] = FileEmbedding(
                    rel_path=rel,
                    mtime=mtime,
                    vec=vec,
                    preview=content[:200].strip().replace("\n", " "),
                )
                updated += 1

        # Purger fichiers supprimés
        for rel in list(self._index.keys()):
            if rel not in seen:
                del self._index[rel]
                updated += 1

        return updated

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        """Top-k fichiers les plus pertinents pour la requête."""
        if not self.is_available():
            return []
        self.refresh()
        if not self._index:
            return []
        # Embed la query
        q_vecs = _embed_batch([query])
        if not q_vecs or not q_vecs[0]:
            return []
        q = q_vecs[0]
        # Cosine vs tous les fichiers
        scored = [
            SearchHit(
                rel_path=fe.rel_path,
                score=_cosine(q, fe.vec),
                preview=fe.preview,
            )
            for fe in self._index.values()
        ]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:k]

    def stats(self) -> dict:
        return {
            "available": self.is_available(),
            "files_indexed": len(self._index),
        }


def format_hits(hits: list[SearchHit]) -> str:
    if not hits:
        return "Aucun fichier pertinent trouvé."
    lines = [f"{len(hits)} fichier(s) pertinents :"]
    for h in hits:
        lines.append(f"  • [{h.score:.3f}] {h.rel_path}")
        lines.append(f"      {h.preview[:100]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------- #
# Singleton par racine                                                         #
# ---------------------------------------------------------------------------- #

_INDEX_CACHE: dict[str, EmbeddingIndex] = {}


def get_embedding_index(project_root: Path | str) -> EmbeddingIndex:
    """Index partagé (process-level) par racine de projet.

    L'Orchestrator est recréé à CHAQUE message (cf. api.server : `Orchestrator`
    dans la boucle WebSocket). Si l'index vivait dans l'instance, il serait
    reconstruit de zéro à chaque tour (≈ tous les fichiers ré-embeddés). Ce cache
    le fait SURVIVRE entre les tours : build une fois, puis refresh incrémental.
    Bénéficie aussi à l'outil find_relevant_files (même latence évitée)."""
    key = str(Path(project_root).resolve())
    idx = _INDEX_CACHE.get(key)
    if idx is None:
        idx = EmbeddingIndex(Path(key))
        _INDEX_CACHE[key] = idx
    return idx
