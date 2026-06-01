"""Smoke tests pour klody_mcp.klody_server — vérifie l'enregistrement des tools.

Tests via API in-process FastMCP, sans démarrer un vrai serveur HTTP.
Pour le test end-to-end HTTP, voir le bench live (qui démarre vraiment le serveur).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from klody_mcp.klody_server import mcp


async def test_serveur_expose_8_tools_attendus():
    """Le serveur doit exposer les 8 outils principaux."""
    tools = await mcp._list_tools()
    names = {t.name for t in tools}
    expected = {
        "find_symbol", "find_references", "find_relevant_files",
        "run_in_sandbox", "read_file", "list_files",
        "search_in_files", "detect_conventions",
    }
    assert expected.issubset(names), f"Outils manquants : {expected - names}"


async def test_tools_ont_descriptions():
    """Chaque outil doit avoir une description non vide."""
    tools = await mcp._list_tools()
    for t in tools:
        assert t.description, f"Outil {t.name} sans description"
        assert len(t.description) > 20, f"Outil {t.name} : description trop courte"


async def test_find_symbol_sur_repo_klody(monkeypatch):
    """find_symbol sur le repo Klody trouve la classe Router."""
    # Force le root sur le repo Klody
    repo_root = Path(__file__).resolve().parent.parent
    import klody_mcp.klody_server as ks
    monkeypatch.setattr(ks, "_ROOT", repo_root)
    # Reset le singleton du code_index pour qu'il recharge avec le nouveau ROOT
    if hasattr(ks._get_code_index, "_idx"):
        delattr(ks._get_code_index, "_idx")

    # Appel direct de la fonction Python (le décorateur @mcp.tool ne wrap pas)
    result = ks.find_symbol(name="Router")
    assert result["count"] >= 1
    assert any(m["file"].endswith("router.py") for m in result["matches"])
