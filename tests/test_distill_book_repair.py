"""Régressions sur `scripts.distill_book._repair`.

Cas central : un livre sans année connue. Le modèle émet `"source": {..., "year":
null}` (le prompt le décrit comme « null si inconnu »), mais le schéma veut un
entier *ou* l'absence de la clé. Avant le correctif, ce JSON pourtant complet
était rejeté et dumpé dans `logs/distill_book_last_invalid.json` — donc TOUT
livre sans millésime échouait. `_repair` doit normaliser ce `null` en retirant
la clé, sans toucher aux années valides ni au reste du contenu.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from scripts.distill_book import _repair

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "skills" / "distilled" / "schema.json").read_text(encoding="utf-8"))


def _base_skill() -> dict:
    """Skill minimal et valide (sans la clé `source`)."""
    return {
        "skill": "Analytical Method Validation",
        "domain": "analytical-chemistry",
        "description": "Prove an analytical procedure meets predefined performance criteria.",
        "principles": ["Define acceptance criteria before experimentation begins."],
        "workflow": [
            {
                "step": "Define scope",
                "purpose": "Establish what the method must measure.",
                "guidelines": ["Identify analytes, matrix, and regulatory framework."],
            }
        ],
        "checklist": ["Acceptance criteria defined before experimentation"],
    }


def test_repair_drops_null_year_and_result_validates() -> None:
    """`source.year == null` doit être retiré, et le JSON valider ensuite."""
    data = _base_skill()
    data["source"] = {
        "book": "Method Validation in Pharmaceutical Analysis - A Guide to Best Practice",
        "author": "Joachim Ermer",
        "year": None,
    }

    repaired = _repair(data, SCHEMA)

    assert "year" not in repaired["source"], "year=null aurait dû être retiré"
    assert repaired["source"]["book"]   # book/author préservés
    assert repaired["source"]["author"]
    # Doit désormais valider sans lever.
    jsonschema.validate(repaired, SCHEMA)


def test_repair_keeps_valid_integer_year() -> None:
    """Une année entière valide ne doit pas être touchée."""
    data = _base_skill()
    data["source"] = {"book": "X", "author": "Y", "year": 2005}

    repaired = _repair(data, SCHEMA)

    assert repaired["source"]["year"] == 2005
    jsonschema.validate(repaired, SCHEMA)


def test_repair_without_source_is_noop_on_year() -> None:
    """Pas de clé `source` du tout : `_repair` ne doit pas planter."""
    data = _base_skill()
    repaired = _repair(data, SCHEMA)
    assert "source" not in repaired
    jsonschema.validate(repaired, SCHEMA)
