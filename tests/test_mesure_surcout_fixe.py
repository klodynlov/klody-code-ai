"""Le script de mesure du surcoût fixe doit rendre des chiffres COHÉRENTS.

Deux propriétés :
1. Le total est la SOMME des postes présents — pas un chiffre inventé à côté.
2. Un poste absent est dit absent (clé `absent: True`), JAMAIS compté 0.
   Compter 0 rendrait la mesure indiscernable d'un poste réellement vide.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.mesure_surcout_fixe import mesurer


@pytest.fixture
def resultat():
    """Un résultat de mesure sur le projet courant."""
    return mesurer()


def test_total_est_la_somme_des_postes(resultat):
    """Si ce test casse, un poste a été oublié dans la somme ou compté deux fois."""
    somme = sum(
        p["tokens"] for p in resultat["postes"].values() if "tokens" in p
    )
    assert resultat["total_tokens"] == somme


def test_poste_absent_dit_absent_pas_zero():
    """Un poste inaccessible doit porter `absent: True`, pas `tokens: 0`."""
    with patch("agent.long_term_memory.get_long_term_memory",
               side_effect=RuntimeError("moteur indisponible")):
        r = mesurer()
    lt = r["postes"]["memoire_lt"]
    assert lt.get("absent") is True, "le poste doit se dire absent"
    assert "tokens" not in lt, "un poste absent ne doit PAS avoir de clé tokens"


def test_tous_les_postes_sont_presents(resultat):
    """Les postes attendus par le plan doivent tous apparaître."""
    attendus = {
        "prompt_base_type", "skills", "memoire_lt", "retrieval",
        "profil", "conventions", "erreurs", "schemas_outils",
    }
    assert set(resultat["postes"]) == attendus


def test_schemas_outils_non_vide(resultat):
    """Le registre d'outils n'est jamais vide — sinon l'agent ne peut rien faire."""
    s = resultat["postes"]["schemas_outils"]
    assert s["tokens"] > 0
    assert s["nombre_registry"] > 0


def test_prompt_base_non_vide(resultat):
    """Le prompt de base (base.md + default.md) est toujours injecté."""
    assert resultat["postes"]["prompt_base_type"]["tokens"] > 0


def test_comparer_sortie(tmp_path):
    """--comparer ne plante pas sur deux JSON valides."""
    from scripts.mesure_surcout_fixe import comparer
    a = {"postes": {"x": {"tokens": 100}}, "total_tokens": 100}
    b = {"postes": {"x": {"tokens": 120}, "y": {"absent": True, "raison": "ko"}}, "total_tokens": 120}
    fa = tmp_path / "a.json"
    fb = tmp_path / "b.json"
    fa.write_text(json.dumps(a), encoding="utf-8")
    fb.write_text(json.dumps(b), encoding="utf-8")
    comparer(str(fa), str(fb))
