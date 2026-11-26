"""`agent/texte_vocal.py` : Markdown → texte lu à voix haute, module feuille."""

from __future__ import annotations

import ast
from pathlib import Path

from agent.texte_vocal import sans_markdown

RACINE = Path(__file__).resolve().parent.parent


def test_markdown_retire():
    brut = "# Titre\n**Gras** et `code` et [lien](https://x.y)\n```py\nx = 1\n```\n\n\n\n- puce"
    assert sans_markdown(brut) == "Titre\nGras et code et lien\n\n- puce"


def test_le_serveur_et_le_canal_partagent_la_meme_fonction():
    import api.server as srv

    assert srv._strip_markdown is sans_markdown


def test_module_feuille_et_canal_sans_import_du_serveur():
    """Le cycle api.server → api.montre → canaux.imessage → api.server (CodeQL
    336/337, 2026-11-26) ne doit pas revenir, même par un import paresseux."""
    feuille = ast.parse((RACINE / "agent" / "texte_vocal.py").read_text())
    projet = {"agent", "api", "canaux", "tools", "config", "services"}
    for n in ast.walk(feuille):
        if isinstance(n, ast.ImportFrom) and n.module:
            assert n.module.split(".")[0] not in projet, n.module
        if isinstance(n, ast.Import):
            assert all(a.name.split(".")[0] not in projet for a in n.names)
    canal = ast.parse((RACINE / "canaux" / "imessage.py").read_text())
    modules = {n.module for n in ast.walk(canal) if isinstance(n, ast.ImportFrom)}
    assert not {m for m in modules if m and m.split(".")[0] == "api"}
