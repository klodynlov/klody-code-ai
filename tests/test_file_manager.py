"""Tests de tools/file_manager.py — sandbox, lecture, écriture, listing."""

import pytest
from pathlib import Path

from tools.file_manager import FileManager, SandboxViolation


@pytest.fixture
def sandbox(tmp_path):
    """FileManager pointant sur un dossier temporaire isolé."""
    return FileManager(root=tmp_path)


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("Klody Code Ai\nLigne 2\n", encoding="utf-8")
    return f


# ------------------------------------------------------------------ #
# Sécurité sandbox                                                     #
# ------------------------------------------------------------------ #

class TestSandboxSecurity:
    def test_chemin_absolu_bloque(self, sandbox):
        with pytest.raises(SandboxViolation, match="absolu"):
            sandbox._validate_path("/etc/passwd")

    def test_path_traversal_bloque(self, sandbox):
        with pytest.raises(SandboxViolation):
            sandbox._validate_path("../../etc/passwd")

    def test_path_traversal_profond_bloque(self, sandbox):
        with pytest.raises(SandboxViolation):
            sandbox._validate_path("subdir/../../../etc/shadow")

    def test_chemin_vide_bloque(self, sandbox):
        with pytest.raises(SandboxViolation):
            sandbox._validate_path("")

    def test_chemin_dans_sandbox_ok(self, sandbox, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "file.txt").write_text("ok")
        result = sandbox.read_file("sub/file.txt")
        assert result == "ok"

    def test_extension_env_bloquee(self, sandbox, tmp_path):
        (tmp_path / ".env").write_text("SECRET=123")
        with pytest.raises(SandboxViolation, match="[Bb]loqu"):
            sandbox.read_file(".env")

    def test_extension_pem_bloquee(self, sandbox, tmp_path):
        (tmp_path / "cert.pem").write_text("CERT DATA")
        with pytest.raises(SandboxViolation, match="[Bb]loqu"):
            sandbox.read_file("cert.pem")

    def test_extension_key_bloquee(self, sandbox, tmp_path):
        (tmp_path / "id_rsa.key").write_text("KEY")
        with pytest.raises(SandboxViolation):
            sandbox.read_file("id_rsa.key")

    def test_fichier_env_bloque_a_lecriture(self, sandbox):
        with pytest.raises(SandboxViolation):
            sandbox.write_file(".env", "SECRET=hack")

    def test_ecriture_hors_sandbox_bloquee(self, sandbox):
        with pytest.raises(SandboxViolation):
            sandbox.write_file("../../evil.txt", "hack")


# ------------------------------------------------------------------ #
# Lecture                                                              #
# ------------------------------------------------------------------ #

class TestReadFile:
    def test_lecture_normale(self, sandbox, sample_file):
        result = sandbox.read_file("sample.txt")
        assert "Klody Code Ai" in result
        assert "Ligne 2" in result

    def test_fichier_inexistant_leve_erreur(self, sandbox):
        with pytest.raises(FileNotFoundError):
            sandbox.read_file("pas_la.txt")

    def test_lire_un_dossier_leve_erreur(self, sandbox, tmp_path):
        (tmp_path / "mondir").mkdir()
        with pytest.raises(IsADirectoryError):
            sandbox.read_file("mondir")

    def test_fichier_trop_grand_bloque(self, sandbox, tmp_path, monkeypatch):
        import tools.file_manager as fm
        monkeypatch.setattr(fm, "MAX_FILE_SIZE", 10)
        big = tmp_path / "gros.txt"
        big.write_text("x" * 100)
        sb = FileManager(root=tmp_path)
        with pytest.raises(ValueError, match="volumineux"):
            sb.read_file("gros.txt")

    def test_encodage_utf8(self, sandbox, tmp_path):
        (tmp_path / "unicode.txt").write_text("éàü 🤖", encoding="utf-8")
        result = sandbox.read_file("unicode.txt")
        assert "🤖" in result


# ------------------------------------------------------------------ #
# Écriture                                                             #
# ------------------------------------------------------------------ #

class TestWriteFile:
    def test_creation_nouveau_fichier(self, sandbox, tmp_path):
        result = sandbox.write_file("nouveau.txt", "Hello Klody")
        assert "créé" in result
        assert (tmp_path / "nouveau.txt").read_text() == "Hello Klody"

    def test_modification_fichier_existant(self, sandbox, tmp_path):
        (tmp_path / "exist.txt").write_text("avant")
        result = sandbox.write_file("exist.txt", "après")
        assert "modifié" in result
        assert (tmp_path / "exist.txt").read_text() == "après"

    def test_creation_sous_dossiers_automatique(self, sandbox, tmp_path):
        sandbox.write_file("a/b/c/file.txt", "nested")
        assert (tmp_path / "a" / "b" / "c" / "file.txt").read_text() == "nested"

    def test_contenu_vide_autorise(self, sandbox, tmp_path):
        sandbox.write_file("vide.txt", "")
        assert (tmp_path / "vide.txt").read_text() == ""


# ------------------------------------------------------------------ #
# Listing                                                              #
# ------------------------------------------------------------------ #

class TestListFiles:
    def test_liste_basique(self, sandbox, tmp_path):
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "b.py").write_text("b")
        result = sandbox.list_files(".")
        assert "a.py" in result
        assert "b.py" in result

    def test_liste_recursive(self, sandbox, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "deep.txt").write_text("deep")
        result = sandbox.list_files(".", recursive=True)
        assert "deep.txt" in result

    def test_repertoire_vide(self, sandbox, tmp_path):
        (tmp_path / "empty").mkdir()
        result = sandbox.list_files("empty")
        assert "vide" in result.lower()

    def test_repertoire_inexistant(self, sandbox):
        with pytest.raises(FileNotFoundError):
            sandbox.list_files("nope")

    def test_liste_hors_sandbox_bloquee(self, sandbox):
        with pytest.raises(SandboxViolation):
            sandbox.list_files("../../")


# ------------------------------------------------------------------ #
# Diff                                                                 #
# ------------------------------------------------------------------ #

class TestDiffFiles:
    def test_fichiers_identiques(self, sandbox, tmp_path):
        (tmp_path / "f1.txt").write_text("same")
        (tmp_path / "f2.txt").write_text("same")
        result = sandbox.diff_files("f1.txt", "f2.txt")
        assert "identiques" in result

    def test_diff_affiche_changements(self, sandbox, tmp_path):
        (tmp_path / "f1.txt").write_text("avant\n")
        (tmp_path / "f2.txt").write_text("après\n")
        result = sandbox.diff_files("f1.txt", "f2.txt")
        assert "-avant" in result
        assert "+après" in result
