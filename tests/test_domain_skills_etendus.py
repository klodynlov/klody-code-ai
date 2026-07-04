"""Validité des skills de domaine étendus (Roadmap v2 #10).

Vérifie que les nouveaux fichiers de domaine (graphql/docker/kubernetes/cicd/
sdk/uml) sont des fichiers de domaine valides et servis par get_skills, et que
l'enum de l'outil get_skills les référence.
"""
from __future__ import annotations

import json

import pytest
from config import SKILLS_DIR
from tools.mcp_client import _is_domain_file, get_skills

_NOUVEAUX_DOMAINES = ["graphql", "docker", "kubernetes", "cicd", "sdk", "uml", "sql"]


@pytest.mark.parametrize("domain", _NOUVEAUX_DOMAINES)
def test_fichier_domaine_valide(domain):
    path = SKILLS_DIR / f"{domain}.json"
    assert path.exists(), f"skills/{domain}.json manquant"
    assert _is_domain_file(path), f"skills/{domain}.json n'est pas un fichier de domaine valide"


@pytest.mark.parametrize("domain", _NOUVEAUX_DOMAINES)
def test_schema_entrees(domain):
    entries = json.loads((SKILLS_DIR / f"{domain}.json").read_text(encoding="utf-8"))
    assert isinstance(entries, list) and entries
    for e in entries:
        for key in ("id", "domain", "title", "content", "tags"):
            assert key in e, f"{domain}: clé '{key}' manquante"
        assert e["domain"] == domain, f"{domain}: champ domain incohérent ({e['domain']})"
        assert isinstance(e["tags"], list)
        assert len(e["content"]) > 80, f"{domain}/{e['id']}: contenu trop court"


@pytest.mark.parametrize("domain", _NOUVEAUX_DOMAINES)
def test_get_skills_rend_le_domaine(domain):
    out = get_skills(domain)
    assert "inconnu" not in out.lower()
    assert f"Conventions {domain}" in out


def test_enum_get_skills_couvre_les_nouveaux_domaines():
    from tools.registry import MCP_TOOLS
    tool = next(t for t in MCP_TOOLS if t["function"]["name"] == "get_skills")
    enum = set(tool["function"]["parameters"]["properties"]["domain"]["enum"])
    for d in _NOUVEAUX_DOMAINES:
        assert d in enum, f"domaine {d} absent de l'enum get_skills"
