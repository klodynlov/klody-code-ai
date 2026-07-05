"""Tests pour tools/toolsmith.py — Klody fabrique ses propres outils."""
from __future__ import annotations

import py_compile

import pytest
from tools.toolsmith import (
    ToolsmithError,
    _resolve_target,
    _slug,
    list_kinds,
    scaffold_tool,
)

ALL_KINDS = [
    "python_script", "cli", "api", "mcp_server",
    "workflow", "pipeline", "klody_plugin", "web_interface",
]


# ── _slug ────────────────────────────────────────────────────────────────────

class TestSlug:
    @pytest.mark.parametrize("raw,expected", [
        ("Mon Outil", "mon_outil"),
        ("data-pipe 2", "data_pipe_2"),
        ("  Éclair!! ", "clair"),          # accents/ponctuation supprimés
        ("", "tool"),
        ("123abc", "tool_123abc"),          # ne peut pas commencer par un chiffre
    ])
    def test_slugify(self, raw, expected):
        assert _slug(raw) == expected


# ── scaffold_tool : chaque kind produit du Python valide ─────────────────────

class TestScaffoldAllKinds:
    @pytest.mark.parametrize("kind", ALL_KINDS)
    def test_generates_and_compiles(self, kind, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = scaffold_tool(kind, f"demo {kind}", str(tmp_path), "outil de démo")
        assert "✅" in result
        dest = tmp_path / _slug(f"demo {kind}")
        assert dest.is_dir()
        py_files = list(dest.glob("*.py"))
        # Tous les artefacts sauf web_interface embarquent du Python.
        if kind != "web_interface":
            assert py_files, f"{kind} devrait générer du Python"
        for py in py_files:
            py_compile.compile(str(py), doraise=True)  # lève si syntaxe invalide

    def test_web_interface_has_html(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        scaffold_tool("web_interface", "dash", str(tmp_path), "tableau de bord")
        dest = tmp_path / "dash"
        assert (dest / "index.html").exists()
        assert (dest / "app.js").exists()

    def test_api_has_test_and_reqs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        scaffold_tool("api", "svc", str(tmp_path))
        dest = tmp_path / "svc"
        assert (dest / "main.py").exists()
        assert (dest / "test_main.py").exists()
        assert "fastapi" in (dest / "requirements.txt").read_text()


# ── garde-fous ───────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_kind(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = scaffold_tool("rocket", "x", str(tmp_path))
        assert "kind inconnu" in result

    def test_empty_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert "nom d'outil vide" in scaffold_tool("cli", "  ", str(tmp_path))

    def test_refuse_existing_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "taken").mkdir()
        result = scaffold_tool("cli", "taken", str(tmp_path))
        assert "existe déjà" in result

    def test_outside_sandbox(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # /etc n'est sous aucune racine autorisée
        result = scaffold_tool("cli", "evil", "/etc")
        assert "SÉCURITÉ" in result

    def test_resolve_target_rejects_outside(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ToolsmithError):
            _resolve_target("/usr/lib")


# ── list_kinds ───────────────────────────────────────────────────────────────

def test_list_kinds():
    out = list_kinds()
    for kind in ALL_KINDS:
        assert kind in out


# ── le test généré passe vraiment (méta-test workflow) ───────────────────────

def test_generated_workflow_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scaffold_tool("workflow", "flow", str(tmp_path))
    import importlib.util
    spec = importlib.util.spec_from_file_location("flow", tmp_path / "flow" / "flow.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.run({"input": "hi"})["output"] == "HI"
