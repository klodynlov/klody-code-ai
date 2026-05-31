"""Schéma et chargement des skills distillés (pipeline livre → JSON → artefact).

On vérifie :
- le schéma JSON Schema lui-même est valide (draft-2020-12)
- tous les fichiers `skills/distilled/<domain>/*.json` valident contre le schéma
- les outils MCP `list_distilled_skills` / `get_distilled_skill` retournent
  les bonnes formes (lecture seule, pas de réseau)
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
DISTILLED = ROOT / "skills" / "distilled"
SCHEMA_PATH = DISTILLED / "schema.json"


# ── Schéma ─────────────────────────────────────────────────────────────────────

def test_schema_file_exists() -> None:
    assert SCHEMA_PATH.is_file(), "skills/distilled/schema.json absent"


def test_schema_is_valid_draft_2020_12() -> None:
    """Le schéma doit lui-même être un JSON Schema bien formé."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Lèvera SchemaError si le schéma n'est pas valide.
    jsonschema.Draft202012Validator.check_schema(schema)


# ── Skills réels ───────────────────────────────────────────────────────────────

def _discover_skill_files() -> list[Path]:
    if not DISTILLED.exists():
        return []
    out: list[Path] = []
    for domain_dir in DISTILLED.iterdir():
        if not domain_dir.is_dir():
            continue
        for path in sorted(domain_dir.glob("*.json")):
            out.append(path)
    return out


@pytest.mark.parametrize("skill_path", _discover_skill_files(), ids=lambda p: p.stem)
def test_distilled_skill_validates(skill_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    data = json.loads(skill_path.read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)


def test_at_least_one_example_skill_present() -> None:
    """Il doit rester au moins un fichier d'exemple pour montrer la forme."""
    files = _discover_skill_files()
    assert files, "aucun JSON dans skills/distilled/<domain>/ — manque l'exemple"


# ── Outils MCP ─────────────────────────────────────────────────────────────────

def test_iter_distilled_skips_underscore_and_schema() -> None:
    from klody_mcp.server import _iter_distilled

    paths = list(_iter_distilled())
    names = {p.name for p in paths}
    assert "schema.json" not in names
    assert all(not p.name.startswith("_") for p in paths)


def test_list_distilled_skills_returns_metadata() -> None:
    from klody_mcp.server import list_distilled_skills

    entries = list_distilled_skills()
    assert isinstance(entries, list)
    if entries:
        e = entries[0]
        for key in ("slug", "domain", "skill", "description"):
            assert key in e, f"clé manquante : {key}"


def test_list_distilled_filters_by_domain() -> None:
    from klody_mcp.server import list_distilled_skills

    all_entries = list_distilled_skills()
    if not all_entries:
        pytest.skip("aucun skill distillé — rien à filtrer")
    domain = all_entries[0]["domain"]
    filtered = list_distilled_skills(domain=domain)
    assert filtered
    assert {e["domain"] for e in filtered} == {domain}


def test_get_distilled_skill_unknown_returns_error_with_suggestions() -> None:
    from klody_mcp.server import get_distilled_skill

    result = get_distilled_skill(slug="ce-skill-n-existe-pas")
    assert "error" in result
    assert "available" in result
    assert isinstance(result["available"], list)


def test_get_distilled_skill_roundtrip() -> None:
    from klody_mcp.server import get_distilled_skill, list_distilled_skills

    entries = list_distilled_skills()
    if not entries:
        pytest.skip("aucun skill distillé")
    first = entries[0]
    data = get_distilled_skill(slug=first["slug"], domain=first["domain"])
    assert "error" not in data
    assert data["skill"] == first["skill"]
    assert "workflow" in data
