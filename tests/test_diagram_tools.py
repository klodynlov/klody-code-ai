"""Tests pour tools/diagram_tools — génération UML mermaid depuis le code (#10)."""
from __future__ import annotations

from pathlib import Path

import pytest
from tools import diagram_tools
from tools.diagram_tools import format_diagram_result, generate_class_diagram

_AVAILABLE = None


def _ts_available() -> bool:
    global _AVAILABLE
    if _AVAILABLE is None:
        from tools.code_index import CodeIndex
        _AVAILABLE = CodeIndex(Path(".")).is_available()
    return _AVAILABLE


@pytest.fixture
def code_repo(tmp_path: Path, monkeypatch) -> Path:
    if not _ts_available():
        pytest.skip("tree-sitter indisponible")
    (tmp_path / "models.py").write_text(
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n"
        "    def multiply(self, a, b):\n"
        "        return a * b\n"
        "\n"
        "class Logger:\n"
        "    def info(self, msg):\n"
        "        pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(diagram_tools, "_DIAGRAM_ROOTS", [tmp_path.resolve()])
    return tmp_path


class TestGeneration:
    def test_diagramme_de_classes(self, code_repo):
        res = generate_class_diagram(str(code_repo))
        assert res["ok"] is True
        m = res["mermaid"]
        assert m.startswith("classDiagram")
        assert "class Calculator" in m
        assert "+add()" in m and "+multiply()" in m
        assert "class Logger" in m
        assert res["class_count"] == 2

    def test_format_bloc_mermaid(self, code_repo):
        out = format_diagram_result(generate_class_diagram(str(code_repo)))
        assert "```mermaid" in out
        assert "classDiagram" in out

    def test_max_classes_tronque(self, code_repo):
        res = generate_class_diagram(str(code_repo), max_classes=1)
        assert res["class_count"] == 1
        assert res["truncated"] is True

    def test_aucune_classe(self, tmp_path, monkeypatch):
        if not _ts_available():
            pytest.skip("tree-sitter indisponible")
        (tmp_path / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        monkeypatch.setattr(diagram_tools, "_DIAGRAM_ROOTS", [tmp_path.resolve()])
        res = generate_class_diagram(str(tmp_path))
        assert res["ok"] is False
        assert "Aucune classe" in res["error"]


class TestConfinement:
    def test_hors_racines_refuse(self, code_repo):
        res = generate_class_diagram("/etc")
        assert res["ok"] is False
        assert "hors des racines" in res["error"]


class TestFormat:
    def test_format_erreur(self):
        assert format_diagram_result({"ok": False, "error": "boom"}) == "boom"
