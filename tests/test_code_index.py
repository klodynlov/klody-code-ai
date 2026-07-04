"""Tests pour tools/code_index — tree-sitter symbols + references."""
from __future__ import annotations

from pathlib import Path

import pytest
from tools.code_index import (
    CodeIndex,
    Reference,
    Symbol,
    format_references,
    format_symbols,
)


@pytest.fixture
def python_repo(tmp_path: Path) -> Path:
    """Mini repo Python avec 2 fichiers + 1 sous-dossier à skipper."""
    (tmp_path / "module_a.py").write_text(
        "def greet(name):\n"
        "    return f'hi {name}'\n"
        "\n"
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n"
        "    def multiply(self, a, b):\n"
        "        return a * b\n",
        encoding="utf-8",
    )
    (tmp_path / "module_b.py").write_text(
        "from module_a import greet, Calculator\n"
        "\n"
        "def main():\n"
        "    print(greet('world'))\n"
        "    c = Calculator()\n"
        "    result = c.add(2, 3)\n"
        "    print(result)\n",
        encoding="utf-8",
    )
    # Doit être skippé
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "fake.py").write_text("def should_be_skipped(): pass\n", encoding="utf-8")
    return tmp_path


class TestIndexation:
    def test_index_refresh_indexe_les_fichiers(self, python_repo):
        idx = CodeIndex(python_repo)
        n = idx.refresh()
        assert n == 2  # module_a + module_b, pas venv/fake.py

    def test_skip_venv(self, python_repo):
        idx = CodeIndex(python_repo)
        idx.refresh()
        syms = idx.find_symbol("should_be_skipped")
        assert syms == []  # le fichier dans .venv est ignoré

    def test_re_refresh_idempotent_si_pas_de_changement(self, python_repo):
        idx = CodeIndex(python_repo)
        idx.refresh()
        n2 = idx.refresh()
        assert n2 == 0

    def test_re_refresh_detecte_modif(self, python_repo, tmp_path):
        import time
        idx = CodeIndex(python_repo)
        idx.refresh()
        time.sleep(0.05)  # garantir mtime différent
        (python_repo / "module_a.py").write_text(
            "def new_func(): pass\n", encoding="utf-8"
        )
        n = idx.refresh()
        assert n == 1


class TestFindSymbol:
    def test_trouve_fonction(self, python_repo):
        idx = CodeIndex(python_repo)
        syms = idx.find_symbol("greet")
        assert len(syms) == 1
        assert syms[0].kind == "function"
        assert syms[0].file == "module_a.py"
        assert syms[0].line == 1

    def test_trouve_classe(self, python_repo):
        idx = CodeIndex(python_repo)
        syms = idx.find_symbol("Calculator")
        assert len(syms) == 1
        assert syms[0].kind == "class"

    def test_trouve_methode_avec_parent(self, python_repo):
        idx = CodeIndex(python_repo)
        syms = idx.find_symbol("add")
        assert len(syms) == 1
        assert syms[0].kind == "method"
        assert syms[0].parent == "Calculator"

    def test_inexistant_renvoie_vide(self, python_repo):
        idx = CodeIndex(python_repo)
        assert idx.find_symbol("xyz_nonexistent") == []


class TestFindReferences:
    def test_trouve_references_de_greet(self, python_repo):
        idx = CodeIndex(python_repo)
        refs = idx.find_references("greet")
        # module_b importe et appelle greet
        assert any(r.file == "module_b.py" for r in refs)
        assert any("greet" in r.context for r in refs)

    def test_trouve_references_de_Calculator(self, python_repo):
        idx = CodeIndex(python_repo)
        refs = idx.find_references("Calculator")
        # Au moins le `c = Calculator()` dans module_b
        files = {r.file for r in refs}
        assert "module_b.py" in files

    def test_max_results_respecte(self, python_repo):
        # Crée un fichier avec 100 appels à `greet`
        spammy = python_repo / "spammy.py"
        spammy.write_text(
            "from module_a import greet\n" + "\n".join(f"greet({i})" for i in range(100)),
            encoding="utf-8",
        )
        idx = CodeIndex(python_repo)
        refs = idx.find_references("greet", max_results=10)
        assert len(refs) == 10


class TestFormatters:
    def test_format_symbols_vide(self):
        assert format_symbols([]) == "Aucun symbole trouvé."

    def test_format_symbols_lisible(self):
        syms = [
            Symbol(name="foo", kind="function", file="a.py", line=1),
            Symbol(name="bar", kind="method", file="b.py", line=10, parent="Cls"),
        ]
        s = format_symbols(syms)
        assert "function" in s
        assert "method" in s
        assert "Cls" in s
        assert "a.py:1" in s

    def test_format_references_vide(self):
        assert format_references([]) == "Aucune référence trouvée."

    def test_format_references_tronquage(self):
        refs = [Reference(name="x", file=f"f{i}.py", line=1, context="x()") for i in range(50)]
        s = format_references(refs)
        # On affiche max 25 + ligne "autres"
        assert s.count("•") <= 26
        assert "autres" in s


class TestStats:
    def test_stats_compteurs(self, python_repo):
        idx = CodeIndex(python_repo)
        s = idx.stats()
        # 2 fichiers, plusieurs symboles
        assert s["files"] == 2
        assert s["symbols"] >= 4  # greet, Calculator, add, multiply
        assert s["references"] > 0


class TestLangagesEtendus:
    """Registre des langages étendus (Roadmap v2 #10) — sans dépendance grammaire."""

    def test_extensions_etendues_mappees(self):
        from tools.code_index import _EXT_TO_LANG
        assert _EXT_TO_LANG[".rs"] == "rust"
        assert _EXT_TO_LANG[".go"] == "go"
        assert _EXT_TO_LANG[".java"] == "java"
        assert _EXT_TO_LANG[".php"] == "php"

    def test_registres_coherents(self):
        # Chaque langage optionnel a un loader, une spec et au moins une extension.
        from tools.code_index import _EXT_TO_LANG, _LANG_SPEC, _OPTIONAL_LOADERS
        assert set(_OPTIONAL_LOADERS) == set(_LANG_SPEC)
        mapped_langs = set(_EXT_TO_LANG.values())
        for lang in _OPTIONAL_LOADERS:
            assert lang in mapped_langs, lang

    def test_spec_a_des_types_de_noeuds(self):
        from tools.code_index import _LANG_SPEC
        for lang, spec in _LANG_SPEC.items():
            union = spec.class_nodes | spec.method_nodes | spec.func_nodes
            assert union, f"{lang}: aucun node de définition"
            assert spec.call_nodes, f"{lang}: aucun node d'appel"

    def test_grammaire_absente_ignore_le_fichier_sans_crash(self, python_repo):
        # Un .rs sans grammaire installée ne doit ni crasher ni polluer l'index ;
        # les fichiers Python restent indexés normalement.
        idx = CodeIndex(python_repo)
        if not idx.is_available():
            pytest.skip("tree-sitter base indisponible")
        (python_repo / "lib.rs").write_text(
            "fn helper() -> i32 { 42 }\n", encoding="utf-8"
        )
        idx.refresh()  # ne doit pas lever
        # greet (Python) toujours trouvé quelle que soit la dispo de la grammaire Rust.
        assert idx.find_symbol("greet")
        from tools.code_index import _PARSERS
        if "rust" not in _PARSERS:
            # Grammaire Rust non installée → helper non indexé (langage dormant).
            assert idx.find_symbol("helper") == []
