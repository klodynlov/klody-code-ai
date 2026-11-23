"""Plus aucun `script.py` inventé par le text-to-action.

Vécu au banc d'injection du 2026-11-23 (klodynlov-profile,
`docs/bench/localcode/injection/`) : dans 8 essais sur 53, Klody a écrit sa
correction dans un `script.py` à la racine du workspace au lieu du module visé.
Cause : `_infer_action_from_text` transformait tout bloc ```python d'une réponse
texte en `write_file("script.py", bloc)`, y compris dans le récapitulatif final
d'un run qui avait DÉJÀ écrit au bon endroit.

Désormais un bloc Python ne produit aucune action devinée ; sur les types
actionnables, le garde « code affiché sans écriture » relance le modèle, qui
nomme lui-même le fichier (scénarios de rejeu 27 et 28 dans
`tests/integration/fixtures/`).
"""
from __future__ import annotations

import pytest
from agent.orchestrateur.outils import _infer_action_from_text

PY = "```python\ndef prix_ttc(ht, taux):\n    return round(ht * (1 + taux / 100), 2)\n```"


@pytest.mark.parametrize("contenu", [
    PY,
    f"La cause : il manque /100.\n\n{PY}",
    f"{PY}\n\nPuis relance :\n\n```bash\npytest\n```",
    f"```bash\npytest\n```\n\n{PY}",
    "```py\nprint(1)\n```",
])
def test_aucun_bloc_python_ne_devient_une_action(contenu):
    assert _infer_action_from_text(contenu, "corrige le test qui échoue") is None


def test_aucune_action_ne_vise_script_py():
    """Propriété : quel que soit le mélange de blocs, jamais `script.py`."""
    for contenu in (
        PY,
        f"{PY}\n```bash\nls\n```",
        "```html\n<p>x</p>\n```\n" + PY,
        "```bash\nls\n```",
    ):
        action = _infer_action_from_text(contenu, "x")
        if action is not None:
            assert action["args"].get("path") != "script.py"


def test_le_web_reste_rattrape():
    """Contre-épreuve : le repli utile (preview HTML, scénario 06) est intact."""
    action = _infer_action_from_text("```html\n<h1>Hello</h1>\n```", "page")
    assert action is not None and action["name"] == "preview_code"


def test_le_shell_seul_reste_rattrape():
    action = _infer_action_from_text("```bash\nls -la\n```", "liste")
    assert action is not None and action["name"] == "execute_command"


def test_lancer_les_tests_ne_prouve_aucune_ecriture():
    """Vécu au banc V4 rejoué (2026-11-23) : pytest lancé via run_in_sandbox,
    correction AFFICHÉE, conclusion. run_in_sandbox comptait comme « producteur »
    et faisait taire le garde ; il ne doit pas compter comme fichier produit."""
    from agent.orchestrateur.outils import _FICHIER_PRODUIT_TOOLS, _PRODUCING_TOOLS
    assert "run_in_sandbox" in _PRODUCING_TOOLS
    assert "run_in_sandbox" not in _FICHIER_PRODUIT_TOOLS
    assert "write_file" in _FICHIER_PRODUIT_TOOLS
