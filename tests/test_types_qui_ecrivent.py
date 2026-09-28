"""Types de tâche qui DOIVENT écrire, et le garde « code affiché sans écriture ».

Vécu le 2026-09-28 (run de promotion de la baseline, passe 2) :
`easy/add_simple_test`, routée `test_gen`, affiche le test dans un bloc ```python
au lieu d'appeler `write_file` ; le tour s'arrête là — 1 itération, 0 appel
d'outil. L'anti-stall, l'auto-continue et le text-to-action ne tournaient que sur
quatre types, recopiés à la main en trois endroits de l'orchestrateur ; les types
ajoutés par #96 (2026-07-05) n'y avaient jamais été reportés.

Ces tests rendent l'oubli impossible à refaire en silence : un nouveau TaskType
doit être classé, et `test_gen` doit rester couvert.
"""
from __future__ import annotations

import typing

import pytest
from agent.orchestrateur.gardes import _affiche_du_code
from agent.orchestrateur.routage import _TYPES_ACTIONNABLES
from agent.router import TYPES_QUI_ECRIVENT, TYPES_REPONSE_TEXTE, TaskType

TOUS = frozenset(typing.get_args(TaskType))


class TestClassementDesTypes:
    def test_chaque_type_est_classe(self):
        # Un type absent des deux ensembles est un type qu'aucun garde ne juge.
        oublies = TOUS - TYPES_QUI_ECRIVENT - TYPES_REPONSE_TEXTE
        assert not oublies, f"TaskType non classé(s) : {sorted(oublies)}"

    def test_aucun_type_n_est_classe_deux_fois(self):
        assert not TYPES_QUI_ECRIVENT & TYPES_REPONSE_TEXTE

    def test_aucun_type_inconnu(self):
        assert (TYPES_QUI_ECRIVENT | TYPES_REPONSE_TEXTE) <= TOUS

    def test_les_types_actionnables_ecrivent(self):
        assert _TYPES_ACTIONNABLES <= TYPES_QUI_ECRIVENT

    def test_test_gen_est_couvert_par_le_garde_code_affiche(self):
        # Le cas vécu : ni actionnable (pas de text-to-action, qui écrirait
        # `script.py`), donc c'est le garde « code affiché » qui le rattrape.
        couverts = TYPES_QUI_ECRIVENT - _TYPES_ACTIONNABLES
        assert "test_gen" in couverts
        assert couverts == {"test_gen", "docs", "migrate", "edit"}


class TestAfficheDuCode:
    @pytest.mark.parametrize("content", [
        "```python\ndef test_add():\n    assert add(1, 2) == 3\n```",
        "Voici le test :\n\n```\nassert add(0, 5) == 5\n```\n",
        "```py\nx = 1\n```\nC'est tout.",
    ])
    def test_bloc_de_code_non_vide(self, content):
        assert _affiche_du_code(content) is True

    @pytest.mark.parametrize("content", [
        None,
        "",
        "J'ai écrit test_math.py : 2 tests, tous verts.",
        "Utilise `pytest -q` pour les lancer.",   # code EN LIGNE
        "```python\n\n```",                        # bloc vide
        "Je remplace par ceci ?\n```python\nx = 2\n```\nTu confirmes ?",
    ])
    def test_pas_de_code_affiche(self, content):
        assert _affiche_du_code(content) is False
