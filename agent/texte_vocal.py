"""Markdown → texte lisible à voix haute (Siri, iMessage, montre).

Sorti de ``api/server.py`` le 2026-11-26 : ``canaux/imessage.py`` l'importait
depuis le serveur, et la route montre (``api/montre.py``, importée PAR le
serveur) passe par le canal — CodeQL a vu le cycle
``api.server → api.montre → canaux.imessage → api.server`` (alertes 336, 337),
et la résolution obligatoire des conversations bloquait la fusion de #344.
Module feuille : il n'importe rien du projet.
"""

from __future__ import annotations

import re

_MD_BOLD = re.compile(r"\*{1,3}(.+?)\*{1,3}", re.DOTALL)
_MD_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
_MD_INLINE_CODE = re.compile(r"`[^`]+`")
_MD_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BULLET = re.compile(r"^[-*]\s+", re.MULTILINE)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_MULTI_NL = re.compile(r"\n{3,}")


def sans_markdown(text: str) -> str:
    """Simplifie le markdown pour une lecture TTS naturelle."""
    text = _MD_CODE_BLOCK.sub("", text)
    text = _MD_INLINE_CODE.sub(lambda m: m.group(0)[1:-1], text)
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_BULLET.sub("- ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_MULTI_NL.sub("\n\n", text)
    return text.strip()
