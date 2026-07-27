"""Tests pour tools/code_index — tree-sitter symbols + references."""
from __future__ import annotations

from pathlib import Path

import pytest
from tools.code_index import (
    MISS_EMPTY_INDEX,
    MISS_ENGINE,
    CodeIndex,
    Reference,
    Symbol,
    format_miss,
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


class TestQualificationDesVides:
    """Audit 27/07 : un résultat vide ne doit jamais valoir verdict tant que
    l'état de l'index est inconnu. tree-sitter absent → tout est vide, ce qui
    était indiscernable de « ce symbole n'existe pas »."""

    def test_moteur_absent_pose_la_cause(self, python_repo, monkeypatch):
        import tools.code_index as ci

        monkeypatch.setattr(ci, "_AVAILABLE", False)
        idx = CodeIndex(python_repo)
        assert idx.find_symbol("greet") == []
        assert idx.last_miss == MISS_ENGINE

    def test_index_vide_pose_la_cause(self, tmp_path):
        # Racine sans aucun fichier d'une extension couverte.
        (tmp_path / "notes.txt").write_text("rien à indexer", encoding="utf-8")
        idx = CodeIndex(tmp_path)
        assert idx.find_symbol("greet") == []
        assert idx.last_miss == MISS_EMPTY_INDEX

    def test_index_sain_vraie_absence(self, python_repo):
        idx = CodeIndex(python_repo)
        assert idx.find_symbol("symbole_qui_nexiste_pas") == []
        assert idx.last_miss is None  # index sain → on a le droit de conclure

    def test_trouvaille_remet_le_temoin_a_zero(self, python_repo):
        idx = CodeIndex(python_repo)
        idx.last_miss = MISS_ENGINE  # résidu d'une recherche précédente
        assert idx.find_symbol("greet")
        assert idx.last_miss is None

    def test_references_qualifient_aussi(self, python_repo, monkeypatch):
        import tools.code_index as ci

        monkeypatch.setattr(ci, "_AVAILABLE", False)
        idx = CodeIndex(python_repo)
        assert idx.find_references("greet") == []
        assert idx.last_miss == MISS_ENGINE

    def test_references_index_sain_vraie_absence(self, python_repo):
        idx = CodeIndex(python_repo)
        assert idx.find_references("jamais_appele_nulle_part") == []
        assert idx.last_miss is None

    def test_warn_une_seule_fois_quand_moteur_absent(self, python_repo, monkeypatch, caplog):
        import tools.code_index as ci

        monkeypatch.setattr(ci, "_AVAILABLE", False)
        monkeypatch.setattr(ci, "_warned_unavailable", False)
        idx = CodeIndex(python_repo)
        with caplog.at_level("WARNING", logger="tools.code_index"):
            idx.refresh()
            idx.refresh()
        # L'ancien code était MUET : rien ne distinguait panne et projet vide.
        assert sum("tree-sitter indisponible" in r.message for r in caplog.records) == 1


class TestFormatMiss:
    def test_panne_ne_rend_aucun_verdict(self):
        msg = format_miss(MISS_ENGINE, name="greet", indexed=0, kind="symbole")
        assert MISS_ENGINE in msg
        assert "IMPOSSIBLE" in msg
        assert "Ne conclus donc pas" in msg
        assert "search_in_files" in msg

    def test_absence_reelle_est_cadree_par_sa_portee(self):
        msg = format_miss(None, name="greet", indexed=42, kind="symbole")
        assert "42 fichier(s) indexés" in msg
        assert "Portée de cette recherche" in msg
        assert ".py" in msg          # extensions couvertes annoncées
        assert "search_in_files" in msg

    def test_accord_du_type_recherche(self):
        assert "référence" in format_miss(None, name="x", indexed=1, kind="référence")


class TestFormatters:
    def test_format_symbols_vide_ne_conclut_pas(self):
        s = format_symbols([])
        assert "Ne conclus pas à l'absence" in s
        assert s != "Aucun symbole trouvé."

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

    def test_format_references_vide_ne_conclut_pas(self):
        s = format_references([])
        assert "Ne conclus pas à l'absence" in s
        assert s != "Aucune référence trouvée."

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
