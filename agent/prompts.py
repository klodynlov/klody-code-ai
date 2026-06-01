"""Chargement et composition des system prompts (Roadmap v2 #5).

Architecture :
    SYSTEM = BASE + TASK_SPECIFIC + DYNAMIC_CONTEXT
- BASE          : identité, invariants — toujours inclus (prompts/base.md)
- TASK_SPECIFIC : workflow ciblé selon le task_type du router
- DYNAMIC       : skills, mémoire long-terme, profil utilisateur (injecté par l'orchestrator)

Si router désactivé ou task_type inconnu → fallback sur prompts/default.md
(qui contient l'ensemble du comportement historique).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPTS_DIR: Path = Path(__file__).resolve().parent.parent / "prompts"

# Mapping task_type (du Router) → fichier prompt
_TASK_PROMPT_FILES: dict[str, str] = {
    "edit": "easy_edit.md",
    "refactor": "refactor.md",
    "bug_fix": "bug_fix.md",
    "feature": "feature.md",
    "explain": "explain.md",
    "self_dev": "self_dev.md",
}

_BASE_FILE = "base.md"
_DEFAULT_FILE = "default.md"


@lru_cache(maxsize=16)
def load_prompt_file(filename: str) -> str:
    """Charge un fichier prompt depuis prompts/. Cache LRU pour ne pas relire à chaque appel."""
    path = PROMPTS_DIR / filename
    if not path.exists():
        logger.warning("Prompt introuvable: %s", path)
        return ""
    return path.read_text(encoding="utf-8").strip()


def compose_system_prompt(task_type: str | None = None) -> str:
    """Compose le system prompt selon le task_type.

    Args:
        task_type : 'edit' | 'refactor' | 'bug_fix' | 'feature' | 'explain' | None
                    Si None ou inconnu → fallback default.

    Returns:
        Le system prompt composé (BASE + TASK_SPECIFIC ou BASE + DEFAULT).
    """
    base = load_prompt_file(_BASE_FILE)
    if task_type and task_type in _TASK_PROMPT_FILES:
        specific = load_prompt_file(_TASK_PROMPT_FILES[task_type])
    else:
        specific = load_prompt_file(_DEFAULT_FILE)

    if not specific:
        return base
    return f"{base}\n\n{specific}"


def available_task_types() -> list[str]:
    """Liste des task_types reconnus (debug/intro)."""
    return list(_TASK_PROMPT_FILES.keys())
