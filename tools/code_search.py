"""Recherche sémantique dans le code via embeddings (Roadmap v2 #6).

Indexe chaque fichier source du projet et permet de trouver les fichiers
les plus pertinents pour une question en langage naturel.

Backend embeddings : bge-m3 in-process (memory bus, cf. tools/embeddings.py).
Index : en mémoire (dict), PERSISTÉ sur disque depuis le 2026-11-19 — un fichier
par (racine du projet, modèle d'embedding) sous `config.MEMORY_DIR/index_code/`.
Rebuild incrémental : on ne re-embed que les fichiers dont le mtime a changé.

Pourquoi persister (vécu le 2026-11-19, enquête latence Siri/iMessage) : la
première `search()` de CHAQUE processus ré-encodait ~550 fichiers — 160 à 171 s
mesurés (35 lots `embed_batch`, presque tout dans `tensor.cpu()`), contre 0,3 s
pour la deuxième recherche du même processus. Pire : le thread de retrieval
proactif continue après son échéance de 2 s et dispute le GPU au tour suivant.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import sys
import tempfile
from array import array
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Embeddings : in-process via le memory bus (cf. tools/embeddings.py). Avant le
# 2026-07-18 c'était un POST vers Ollama /api/embed ; même modèle bge-m3, mais
# plus de daemon à faire tourner. Le modèle vient des settings du memory bus, ce
# n'est plus une constante d'ici — et depuis que l'index est persisté
# (2026-11-19), son identifiant (`embeddings.model_id()`) fait partie de la clé
# du cache : changer de modèle relit un autre fichier, jamais des vecteurs
# d'un autre espace.

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

# Version du FORMAT du fichier persisté. À incrémenter dès que sa structure ou
# le texte embeddé (`_embed_input`) change : un ancien fichier est alors
# reconstruit, jamais relu de travers.
_CACHE_FORMAT = 1


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
# Embeddings (in-process, memory bus)                                          #
# ---------------------------------------------------------------------------- #


def _embed_batch(texts: list[str], timeout: float = 60.0) -> list[list[float]]:
    """Embedde un batch de textes. `timeout` conservé pour compat d'appel (le
    calcul est désormais local : plus rien à attendre sur le réseau)."""
    from tools import embeddings

    return embeddings.embed_batch(texts)


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


# Causes d'une recherche à vide. `search()` ne peut rendre qu'une liste : sans
# ce témoin, l'appelant ne distingue pas « moteur mort » de « index vide » de
# « rien trouvé » — et rend un verdict SÉMANTIQUE pour une panne d'INFRA.
# Rappel décisif : search() n'applique AUCUN seuil de pertinence (`scored[:k]`),
# donc un moteur sain rend TOUJOURS k fichiers, même sur une requête absurde
# (vérifié : « zzzz quantum banana teapot » → 3 hits à 0.45). Une liste vide est
# donc TOUJOURS une panne, JAMAIS une absence de code pertinent.
MISS_ENGINE = "le moteur d'embeddings est indisponible"
MISS_EMPTY_INDEX = "l'index est vide (aucun fichier source indexable sous la racine)"
MISS_QUERY_EMBED = "l'embedding de la requête a échoué"


class EmbeddingIndex:
    """Index par embedding des fichiers du projet."""

    def __init__(self, project_root: Path, cache_dir: Path | str | None = None):
        self.root: Path = Path(project_root).resolve()
        self._index: dict[str, FileEmbedding] = {}
        self._available: bool | None = None  # None = pas encore testé
        # Cause de la DERNIÈRE recherche à vide (None si la dernière a rendu des
        # hits). Lu par l'appelant pour nommer la panne au lieu de conclure.
        self.last_miss: str | None = None
        # Persistance. `cache_dir` None ⇒ `config.MEMORY_DIR/index_code`, résolu
        # PARESSEUSEMENT : la suite redirige `KLODY_DATA_DIR` (tests/garde_etat)
        # et un chemin figé à l'import contournerait cette redirection.
        self._cache_dir: Path | None = Path(cache_dir) if cache_dir else None
        # Modèle dont viennent les vecteurs de `_index` ("" = inconnu). Le
        # fichier n'est chargé qu'une fois par instance, au premier refresh().
        self._modele: str = ""
        self._charge: bool = False

    def is_available(self) -> bool:
        """Le moteur d'embeddings est-il utilisable ? (lazy, cache).

        Plus aucun ping réseau : on interroge le memory bus in-process, qui est
        disponible dès que `klody_memory` + sentence-transformers sont installés.
        """
        if self._available is not None:
            return self._available
        from tools import embeddings

        self._available = embeddings.is_available()
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

    # ── Persistance ────────────────────────────────────────────────────────── #

    def _dossier_cache(self) -> Path:
        if self._cache_dir is not None:
            return self._cache_dir
        import config  # tardif : cf. __init__

        return Path(config.MEMORY_DIR) / "index_code"

    def cache_path(self, modele: str) -> Path:
        """Fichier du cache pour (cette racine, ce modèle).

        Le nom HACHE les deux : une racine est un chemin arbitraire, un modèle
        un `provider:org/nom`. Le contenu les répète, et le chargement les
        vérifie — une collision de préfixe de hash ne peut pas servir les
        vecteurs d'un autre projet.
        """
        cle = hashlib.sha256(f"{self.root}\0{modele}".encode()).hexdigest()[:24]
        return self._dossier_cache() / f"{cle}.json"

    def _charger(self, modele: str) -> int:
        """Charge le cache persisté pour `modele`. Rend le nombre d'entrées.

        Tout écart — fichier illisible ou tronqué, autre format, autre racine,
        autre modèle, dimension incohérente, autre ordre d'octets — rend 0 et
        laisse l'index VIDE : la reconstruction complète qui suit est le
        comportement d'avant la persistance, donc jamais pire. Ne lève jamais.
        """
        chemin = self.cache_path(modele)
        try:
            brut = chemin.read_bytes()
        except FileNotFoundError:
            return 0
        except OSError as exc:
            logger.warning("[EmbeddingIndex] cache illisible %s (%s) — reconstruction", chemin, exc)
            return 0
        try:
            doc = json.loads(brut)
            if doc.get("format") != _CACHE_FORMAT:
                raise ValueError(f"format {doc.get('format')!r} ≠ {_CACHE_FORMAT}")
            if doc.get("root") != str(self.root):
                raise ValueError(f"racine {doc.get('root')!r} ≠ {self.root}")
            if doc.get("model") != modele:
                raise ValueError(f"modèle {doc.get('model')!r} ≠ {modele!r}")
            if doc.get("byteorder") != sys.byteorder:
                raise ValueError(f"ordre d'octets {doc.get('byteorder')!r}")
            dim = int(doc["dim"])
            if dim <= 0:
                raise ValueError(f"dimension {dim}")
            index: dict[str, FileEmbedding] = {}
            for rel, e in doc["files"].items():
                vec = array("f")
                vec.frombytes(base64.b64decode(e["vec"], validate=True))
                if len(vec) != dim:
                    raise ValueError(f"{rel} : dimension {len(vec)} ≠ {dim}")
                index[rel] = FileEmbedding(
                    rel_path=rel,
                    mtime=float(e["mtime"]),
                    vec=vec.tolist(),
                    preview=str(e.get("preview", "")),
                )
        except Exception as exc:  # corrompu, tronqué, schéma inattendu…
            logger.warning(
                "[EmbeddingIndex] cache rejeté %s (%s: %s) — reconstruction complète",
                chemin, type(exc).__name__, exc,
            )
            return 0
        self._index = index
        return len(index)

    def _sauver(self, modele: str) -> None:
        """Écrit l'index de façon ATOMIQUE (temporaire + `os.replace`).

        Un processus tué en pleine écriture laisse au pire un `.tmp` orphelin,
        jamais un cache à moitié écrit sous le vrai nom. Plusieurs processus
        (API, serveur MCP klody) partagent le fichier : le dernier qui écrit
        gagne, et chacun écrit un index complet et cohérent. Ne lève jamais —
        un cache non écrit coûte une reconstruction, pas une recherche.
        """
        dims = {len(fe.vec) for fe in self._index.values()}
        if len(dims) != 1:  # vide, ou dimensions mêlées : rien de sûr à écrire
            return
        doc = {
            "format": _CACHE_FORMAT,
            "root": str(self.root),
            "model": modele,
            "dim": dims.pop(),
            "byteorder": sys.byteorder,
            "files": {
                rel: {
                    "mtime": fe.mtime,
                    "preview": fe.preview,
                    "vec": base64.b64encode(array("f", fe.vec).tobytes()).decode("ascii"),
                }
                for rel, fe in self._index.items()
            },
        }
        chemin = self.cache_path(modele)
        tmp: str | None = None
        try:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=chemin.parent, prefix=chemin.stem, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, chemin)
            tmp = None
        except Exception as exc:  # disque plein, droits, valeur non sérialisable…
            logger.warning("[EmbeddingIndex] cache non écrit %s (%s: %s)", chemin, type(exc).__name__, exc)
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)

    def _synchroniser_modele(self) -> str:
        """Aligne l'index en mémoire sur le modèle courant ; rend son id.

        Premier appel : charge le cache persisté. Modèle changé en cours de
        processus : l'index est jeté (ses vecteurs sont d'un autre espace) et
        le cache du nouveau modèle est chargé s'il existe.
        """
        from tools import embeddings

        modele = embeddings.model_id()
        if self._charge and modele == self._modele:
            return modele
        if self._charge:
            logger.warning(
                "[EmbeddingIndex] modèle d'embedding changé (%r → %r) — index jeté",
                self._modele, modele,
            )
        self._index = {}
        self._modele = modele
        self._charge = True
        if modele:
            n = self._charger(modele)
            if n:
                logger.info("[EmbeddingIndex] %d fichier(s) rechargé(s) depuis le cache", n)
        return modele

    def _dimension(self) -> int | None:
        for fe in self._index.values():
            return len(fe.vec)
        return None

    # ── Indexation ─────────────────────────────────────────────────────────── #

    def refresh(self, batch_size: int = 16) -> int:
        """Re-indexe les fichiers ajoutés/modifiés. Retourne nb d'updates."""
        if not self.is_available():
            return 0
        modele = self._synchroniser_modele()

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
            # `!=` et non `>=` : le cache survit au processus, donc à un
            # `git checkout` ou une restauration qui RECULE un mtime. Seule
            # l'égalité prouve que le fichier n'a pas bougé.
            if cached and cached.mtime == mtime:
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

        if updated and modele:
            self._sauver(modele)
        return updated

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        """Top-k fichiers les plus pertinents pour la requête.

        Une liste vide signale TOUJOURS une panne, jamais « rien de pertinent » :
        `self.last_miss` en porte la cause (cf. MISS_*). Voir `format_miss()`.
        """
        self.last_miss = None
        if not self.is_available():
            self.last_miss = MISS_ENGINE
            return []
        self.refresh()
        if not self._index:
            self.last_miss = MISS_EMPTY_INDEX
            return []
        # Embed la query
        q_vecs = _embed_batch([query])
        if not q_vecs or not q_vecs[0]:
            self.last_miss = MISS_QUERY_EMBED
            return []
        q = q_vecs[0]
        dim = self._dimension()
        if dim is not None and len(q) != dim:
            # Même identifiant, autre espace (modèle remplacé sous le même nom,
            # cache d'une autre version) : `_cosine` rendrait 0.0 partout, soit
            # k fichiers au hasard présentés comme « pertinents ». On jette
            # l'index et on le reconstruit — une seule fois par recherche.
            logger.warning(
                "[EmbeddingIndex] dimension de la requête %d ≠ index %d — reconstruction complète",
                len(q), dim,
            )
            self._index = {}
            self.refresh()
            if not self._index:
                self.last_miss = MISS_EMPTY_INDEX
                return []
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


def format_miss(reason: str | None) -> str:
    """Message d'échec qui nomme la PANNE au lieu de rendre un verdict.

    L'ancien « Aucun fichier pertinent trouvé. » se lisait comme « ce code
    n'existe pas » et poussait l'agent à conclure à l'absence — alors que
    `search()` ne filtre pas par pertinence : zéro hit = moteur ou index en
    panne, jamais un jugement sur le code (cf. MISS_*). Même famille que le
    « pas de sources dans LibraryBrain » de #158.
    """
    cause = reason or "cause inconnue (moteur ou index d'embeddings)"
    return (
        f"Recherche sémantique EN PANNE — {cause}.\n"
        "⚠️ Ce n'est PAS un verdict sur le code : `find_relevant_files` ne filtre "
        "pas par pertinence, un moteur sain rend toujours des fichiers. Ne conclus "
        "donc PAS que le code cherché n'existe pas.\n"
        "Reprends avec `search_in_files` (grep, indépendant du moteur) ou "
        "`find_symbol` pour localiser une définition."
    )


def format_hits(hits: list[SearchHit]) -> str:
    if not hits:
        return format_miss(None)
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
