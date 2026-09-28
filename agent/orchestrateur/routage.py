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
    "_TYPES_ACTIONNABLES",
    "_skill_is_interactive",
]

# ------------------------------------------------------------------ #
# Constantes de routage                                               #
# ------------------------------------------------------------------ #

_CODE_TASK_TYPES = frozenset({
    "edit", "refactor", "bug_fix", "feature", "self_dev",
    "test_gen", "perf", "migrate",
})

# Types sur lesquels tournent l'auto-continue, l'anti-stall « plan annoncé /
# réponse vide » et le text-to-action. Recopiée À LA MAIN en trois endroits de
# l'orchestrateur jusqu'au 2026-09-28 — d'où l'oubli des types de #96, aucune
# copie n'ayant été mise à jour. Délibérément PLUS ÉTROITE que
# `agent.router.TYPES_QUI_ECRIVENT` : `edit` n'est pas prolongé (une boucle de
# lecture sans fin y est un stall), et le text-to-action écrit tout bloc Python
# dans `script.py` — l'étendre à `test_gen` y aurait écrit le test au mauvais
# endroit. Le garde « code affiché sans écriture » couvre le RESTE de
# TYPES_QUI_ECRIVENT (cf. tests/test_types_qui_ecrivent.py).
_TYPES_ACTIONNABLES = frozenset({"feature", "refactor", "self_dev", "bug_fix"})

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
