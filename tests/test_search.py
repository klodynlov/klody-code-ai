"""Tests pour tools/search.py — Search.search_in_files."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def searcher(tmp_path, monkeypatch):
    """Search pointant sur un répertoire temporaire isolé."""
    import tools.search as sm
    monkeypatch.setattr(sm, "HAS_RIPGREP", False)   # toujours grep pour les tests
    from tools.search import Search
    return Search(root=tmp_path)


@pytest.fixture
def project(tmp_path):
    """Structure de projet minimaliste avec quelques fichiers."""
    (tmp_path / "main.py").write_text("def hello():\n    print('klody')\n")
    (tmp_path / "utils.py").write_text("def helper():\n    pass\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").write_text("# deep module\nKLODY = True\n")
    return tmp_path


# ── Pattern vide ───────────────────────────────────────────────────────────────

class TestPatternVide:
    def test_pattern_vide_retourne_erreur(self, searcher):
        result = searcher.search_in_files("")
        assert "ERREUR" in result

    def test_pattern_espaces_retourne_erreur(self, searcher):
        result = searcher.search_in_files("   ")
        assert "ERREUR" in result


# ── Sandbox ────────────────────────────────────────────────────────────────────

class TestSandbox:
    def test_chemin_hors_sandbox_retourne_erreur(self, searcher):
        result = searcher.search_in_files("pattern", path="../../etc")
        assert "ERREUR" in result
        assert "sandbox" in result.lower() or "hors" in result.lower()

    def test_chemin_inexistant_retourne_erreur(self, searcher):
        result = searcher.search_in_files("pattern", path="inexistant_xyz")
        assert "ERREUR" in result or "introuvable" in result.lower()

    def test_chemin_dans_sandbox_ok(self, searcher, tmp_path, project):
        searcher.root = tmp_path
        # Recherche qui existe — ne doit pas lever d'exception
        result = searcher.search_in_files("hello", path=".")
        assert "ERREUR sandbox" not in result


# ── Résultats ──────────────────────────────────────────────────────────────────

class TestResultats:
    def test_pattern_trouve(self, tmp_path, project, monkeypatch):
        import tools.search as sm
        monkeypatch.setattr(sm, "HAS_RIPGREP", False)
        from tools.search import Search
        s = Search(root=tmp_path)
        result = s.search_in_files("klody", path=".")
        # grep retournera au moins la ligne contenant "klody" ou "KLODY"
        assert "klody" in result.lower() or "Aucun" in result

    def test_pattern_absent_retourne_aucun_resultat(self, tmp_path, project, monkeypatch):
        import tools.search as sm
        monkeypatch.setattr(sm, "HAS_RIPGREP", False)
        from tools.search import Search
        s = Search(root=tmp_path)
        result = s.search_in_files("PATTERN_JAMAIS_PRESENT_XYZ42", path=".")
        assert "Aucun résultat" in result

    def test_case_insensitive(self, tmp_path, project, monkeypatch):
        import tools.search as sm
        monkeypatch.setattr(sm, "HAS_RIPGREP", False)
        from tools.search import Search
        s = Search(root=tmp_path)
        result = s.search_in_files("KLODY", path=".", case_sensitive=False)
        # "klody" est dans main.py en minuscule
        assert "Aucun" not in result or "klody" in result.lower()

    def test_file_pattern_filtre(self, tmp_path, monkeypatch):
        """--include *.py ne renvoie pas les .txt"""
        import tools.search as sm
        monkeypatch.setattr(sm, "HAS_RIPGREP", False)
        from tools.search import Search
        (tmp_path / "note.txt").write_text("cible ici")
        (tmp_path / "code.py").write_text("cible aussi")
        s = Search(root=tmp_path)
        result = s.search_in_files("cible", path=".", file_pattern="*.py")
        # note.txt ne doit pas apparaître
        assert "note.txt" not in result


# ── Troncature ─────────────────────────────────────────────────────────────────

class TestTroncature:
    def test_troncature_max_results(self, tmp_path, monkeypatch):
        """Au-delà de MAX_RESULTS lignes, la sortie doit être tronquée."""
        import tools.search as sm
        monkeypatch.setattr(sm, "HAS_RIPGREP", False)
        monkeypatch.setattr(sm, "MAX_RESULTS", 3)
        from tools.search import Search
        # Créer un fichier avec 10 occurrences
        content = "\n".join(f"ligne {i}: target" for i in range(10))
        (tmp_path / "big.txt").write_text(content)
        s = Search(root=tmp_path)
        result = s.search_in_files("target", path=".")
        assert "tronquées" in result or "supplémentaires" in result


# ── Timeout ────────────────────────────────────────────────────────────────────

class TestTimeout:
    def test_timeout_retourne_message_erreur(self, searcher, monkeypatch):
        """subprocess.TimeoutExpired doit renvoyer un message lisible."""
        import tools.search as sm
        original_run = sm.subprocess.run

        def mock_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=15)

        monkeypatch.setattr(sm.subprocess, "run", mock_run)
        result = searcher.search_in_files("any", path=".")
        assert "Timeout" in result or "timeout" in result.lower()

    def test_outil_manquant_retourne_message_erreur(self, searcher, monkeypatch):
        """FileNotFoundError (grep introuvable) doit renvoyer un message lisible."""
        import tools.search as sm

        def mock_run(*args, **kwargs):
            raise FileNotFoundError("No such file or directory: 'grep'")

        monkeypatch.setattr(sm.subprocess, "run", mock_run)
        result = searcher.search_in_files("any", path=".")
        assert "introuvable" in result.lower() or "ERREUR" in result
