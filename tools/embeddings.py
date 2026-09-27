"""Embeddings locaux partagés — in-process via `klody_memory` (memory bus).

Remplace les appels HTTP à Ollama `/api/embed` de `tools/code_search.py` et
`tools/skill_router.py` (migration 2026-07-18). Deux raisons :

1. **Un daemon en moins.** Ollama ne servait plus qu'à ça côté Klody ; les
   embeddings passent par le moteur déjà embarqué (`embed_provider="st"`,
   sentence-transformers en process). bge-m3 est chargé UNE fois par process —
   `com.klody.api` est un service long-vécu, le coût est amorti.
2. **Zéro re-embed.** Le provider "st" sert le même bge-m3 que le daemon
   (cos(ollama, st) = 1.0000, mesuré côté Klody Core). Les caches de vecteurs
   des deux appelants sont de toute façon EN MÉMOIRE, jamais persistés : aucune
   migration de données n'est nécessaire.

⚠️ **Un seul propriétaire de la configuration.** `klody_memory.configure()` pose
des singletons GLOBAUX (settings + connexion). Dans ce dépôt c'est
`agent.semantic_memory` qui les détient. Ce module **emprunte** cette
configuration au lieu d'appeler `configure()` une seconde fois — sinon la
connexion de la mémoire sémantique serait écrasée au premier embed.

Dégradation douce : si `klody_memory` (ou sentence-transformers) manque,
`is_available()` renvoie False et `embed_batch()` rend des vecteurs vides —
exactement le contrat de l'ancien client HTTP, donc les appelants retombent sur
leurs replis existants (grep littéral, `select_skills`).
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_available: bool | None = None  # None = pas encore testé (cache process)

# Panne CONSTATÉE (pas supposée) : le moteur a été configuré, mais un lot entier
# de textes non vides n'a rendu aucun vecteur. Vécu du 2026-09-15 au 09-27 :
# `scipy` du venv ne se chargeait plus sous macOS 27 (`__DATA/__thread_bss`),
# sentence-transformers échouait à CHAQUE appel — 1 522 erreurs dans agent.log,
# 1,5-1,8 s perdues par recherche, retrieval proactif muet — pendant que
# `is_available()` continuait de rendre True, faute d'avoir jamais essayé.
# On apprend de l'échec, et on ré-essaie de temps en temps : l'API vit des
# semaines, une panne transitoire ne doit pas la priver d'embeddings à vie.
REESSAI_APRES_PANNE_S = 600.0
_reessai_apres: float = 0.0


def _ensure_ready() -> bool:
    """Garantit que `klody_memory` est configuré, via son propriétaire."""
    from agent import semantic_memory as _sm  # import tardif : évite un cycle

    if not _sm.MEMORY_AVAILABLE:
        return False
    if not _sm.is_ready():
        _sm.configure_memory()
    return True


def is_available() -> bool:
    """Le moteur d'embeddings est-il utilisable ? (lazy, mis en cache).

    Ne fait AUCUN appel réseau, contrairement à l'ancien ping `/api/tags`.
    """
    global _available
    if _available is False and _reessai_apres and time.monotonic() >= _reessai_apres:
        _available = None  # panne constatée ancienne : on retente
    if _available is not None:
        return _available
    try:
        _available = _ensure_ready()
    except Exception as exc:
        logger.debug("embeddings indisponibles : %s", exc)
        _available = False
    return _available


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embedde N textes en un appel. Vecteur vide pour chaque texte échoué.

    Contrat identique à l'ancien `_embed_batch` HTTP : la liste renvoyée est
    TOUJOURS alignée sur `texts`, et l'échec ne lève jamais.
    """
    if not texts:
        return []
    if not is_available():
        return [[] for _ in texts]
    try:
        from klody_memory.embedder import get_embeddings_batch

        vecs = get_embeddings_batch(texts)
    except Exception as exc:
        _constater_panne(f"{type(exc).__name__}: {exc}")
        return [[] for _ in texts]
    # get_embeddings_batch rend None par texte échoué → [] pour l'appelant.
    out = [list(v) if v else [] for v in vecs]
    pleins = [i for i, t in enumerate(texts) if (t or "").strip()]
    if pleins and not any(out[i] for i in pleins):
        _constater_panne(f"aucun vecteur pour {len(pleins)} texte(s) non vide(s)")
    return out


def _constater_panne(cause: str) -> None:
    """Rend `is_available()` honnête après un échec réel, le dit UNE fois."""
    global _available, _reessai_apres
    _available = False
    _reessai_apres = time.monotonic() + REESSAI_APRES_PANNE_S
    logger.warning(
        "Embeddings EN PANNE (%s) — replis actifs (grep, select_skills), nouvel "
        "essai dans %d min. Cause fréquente : venv dérivé du lock "
        "(pip install -r requirements-macos.lock, puis redémarrer les services).",
        cause, int(REESSAI_APRES_PANNE_S // 60),
    )


def embed_one(text: str) -> list[float]:
    """Embedde un texte unique. [] si vide ou en cas d'échec."""
    if not (text or "").strip():
        return []
    out = embed_batch([text])
    return out[0] if out else []


def _reset_cache() -> None:
    """Ré-arme la détection de disponibilité (tests)."""
    global _available, _reessai_apres
    _available = None
    _reessai_apres = 0.0
