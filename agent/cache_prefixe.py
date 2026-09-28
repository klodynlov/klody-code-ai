"""Taux de cache du préfixe, lu dans l'`usage` que rend mlx_lm.

Le cache de préfixe de mlx_lm (ArraysCache du MoE, non rognable) ne réutilise
qu'un préfixe EXACT : un prompt système qui change d'un octet recalcule tous les
schémas d'outils depuis le token 0 (mesuré le 2026-09-27 : 11,2 s contre 0,55 s
en cache). Rien d'autre ne le dit — d'où une ligne `[cache]` par appel LLM, sur
les DEUX chemins : l'API (api/streaming.py) et la CLI, donc aussi le banc
(agent/llm.py). `grep -F '[cache]' logs/agent.log` donne le taux réel.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def tokens_en_cache(usage: Any) -> int | None:
    """`usage.prompt_tokens_details.cached_tokens` (rendu par mlx_lm), ou None."""
    details = getattr(usage, "prompt_tokens_details", None)
    valeur = getattr(details, "cached_tokens", None)
    return valeur if isinstance(valeur, int) else None


def journaliser_cache(usage: Any, modele: str, duree_s: float) -> None:
    """Une ligne par appel LLM : prompt, part servie par le cache, durée."""
    if usage is None:
        return
    prompt = getattr(usage, "prompt_tokens", None)
    cache = tokens_en_cache(usage)
    if not isinstance(prompt, int) or prompt <= 0:
        return
    part = "?" if cache is None else f"{cache / prompt:.0%}"
    logger.info(
        "[cache] %s prompt=%d cached=%s (%s) durée=%.2fs",
        modele, prompt, "?" if cache is None else cache, part, duree_s,
    )
