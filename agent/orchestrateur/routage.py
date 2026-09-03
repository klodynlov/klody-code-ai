"""Constantes et aides de routage — types de tâches, skills interactifs.

Extrait de `agent/orchestrator.py` (lot 4.1b). Les méthodes qui lisent des
symboles `config.*` monkeypatchés par les tests (`CODE_MODEL`, `THINKING_*`)
restent dans `orchestrator.py` pour que `monkeypatch.setattr("agent.orchestrator.X")`
continue de les atteindre.
"""
from __future__ import annotations

__all__ = [
    "_CODE_TASK_TYPES",
    "_INTERACTIVE_SKILL_MARKERS",
    "_skill_is_interactive",
]

# ------------------------------------------------------------------ #
# Constantes de routage                                               #
# ------------------------------------------------------------------ #

_CODE_TASK_TYPES = frozenset({
    "edit", "refactor", "bug_fix", "feature", "self_dev",
    "test_gen", "perf", "migrate",
})

_INTERACTIVE_SKILL_MARKERS = (
    "qcm", "à choix multiple", "choix multiple", "fiche de besoin", "questionnaire",
    "questions interactives", "étape par étape avec l'utilisateur",
    "pose-lui la question", "demande à l'utilisateur",
)


def _skill_is_interactive(skill: dict) -> bool:
    """Le skill est-il un guide INTERACTIF (QCM) plutôt qu'une fiche statique ?

    Vrai si le drapeau explicite `interactive: true` est présent, ou si ≥2
    marqueurs apparaissent dans le contenu (un how-to classique n'en contient
    pas plusieurs à la fois)."""
    if skill.get("interactive") is True:
        return True
    blob = (skill.get("content") or "").lower()
    return sum(marker in blob for marker in _INTERACTIVE_SKILL_MARKERS) >= 2
