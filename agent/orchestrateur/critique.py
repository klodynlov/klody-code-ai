"""Constantes d'auto-critique — seuils et prompt de relecture.

Extrait de `agent/orchestrator.py` (lot 4.1e). La méthode `_maybe_self_critique`
reste dans `orchestrator.py` car elle lit `SELF_CRITIQUE_ENABLED` monkeypatchée
par les tests via `agent.orchestrator.SELF_CRITIQUE_ENABLED`.
"""
from __future__ import annotations

_SELF_CRITIQUE_MIN_CHARS = 200

_SELF_CRITIQUE_PROMPT = (
    "Relis ta dernière réponse à l'utilisateur d'un œil critique. Cherche : erreur "
    "factuelle, oubli important, hypothèse non vérifiée, ou affirmation trop "
    "catégorique.\n"
    "- Si la réponse est DÉJÀ correcte et complète, réponds EXACTEMENT par le seul "
    "mot : INCHANGÉ\n"
    "- Sinon, réécris DIRECTEMENT la réponse finale corrigée pour l'utilisateur "
    "(sans méta-commentaire sur ta relecture)."
)
