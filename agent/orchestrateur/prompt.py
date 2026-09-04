"""Helpers de prompt — shield ASI06, prompt coder slim, détection markdown.

Extrait de `agent/orchestrator.py` (lot 4.1c). Les méthodes qui lisent des
attributs d'instance (`_inject_system_prompt`, `_relevant_files_section`)
restent dans `orchestrator.py` car elles touchent `self.llm`, `self.memory`,
`self.embed_index` et des symboles `config.*` monkeypatchés par les tests.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# ASI06 : bouclier anti-poisoning                                     #
# ------------------------------------------------------------------ #

try:
    from klody_memory.sanitizer import sanitize as _mem_sanitize
except Exception:
    _mem_sanitize = None


def _shield(section: str, label: str) -> str:
    """Sanitize strict d'une section de prompt auto-apprise. Ne strippe que les
    spans d'attaque (marqueur de rédaction), le contenu légitime passe intact."""
    if not section or _mem_sanitize is None:
        return section
    text, flags = _mem_sanitize(section, strict=True)
    if flags:
        logger.warning("[prompt-shield] injection suspecte strippée (section %s, "
                       "flags=%s)", label, flags)
    return text


# ------------------------------------------------------------------ #
# Détection minimale de markdown                                       #
# ------------------------------------------------------------------ #

def _has_markdown_safe(text: str) -> bool:
    """Détection minimale de markdown (évite l'import circulaire avec llm._has_markdown)."""
    markers = ("```", "**", "##", "# ", "- ", "* ", "> ", "| ")
    return any(m in text for m in markers)


# ------------------------------------------------------------------ #
# Prompt SLIM pour le modèle coder                                     #
# ------------------------------------------------------------------ #

_CODER_SLIM_PROMPT = (
    "Tu es un générateur de code expert. Réponds en français, très concis.\n\n"
    "Quand on te demande une page web, une visualisation ou une animation : "
    "génère le code COMPLET et AUTONOME dans UN SEUL bloc ```html (DOCTYPE + "
    "HTML + <style> + <script> inclus, directement ouvrable au navigateur). "
    "TOUT le JavaScript doit être écrit — jamais de coquille vide, jamais de "
    "placeholder « // à compléter ». Si tu utilises une lib externe (Three.js, "
    "Chart.js, d3…), ajoute son <script src=…CDN…>.\n\n"
    "Pour du code non-web : réponds avec le code complet dans un bloc "
    "```<langage>. Le code d'abord, explication minimale."
)
