"""Tests de tools/file_manager.py — sandbox, lecture, écriture, listing."""

from pathlib import Path

import pytest
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
    def test_chemin_absolu_hors_racines_bloque(self, sandbox):
        with pytest.raises(SandboxViolation, match="hors des racines"):
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

# ------------------------------------------------------------------ #
# Nouvelles règles de sécurité                                         #
# ------------------------------------------------------------------ #

class TestSecuriteAvancee:
    def test_claude_masque_dans_listing(self, tmp_path):
        """.claude/ ne doit pas apparaître dans list_files."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "secret.txt").write_text("interne")
        (tmp_path / "visible.py").write_text("ok")
        fm = FileManager(root=tmp_path)
        result = fm.list_files(".")
        assert ".claude" not in result
        assert "visible.py" in result

    def test_git_fichier_masque_dans_listing(self, tmp_path):
        """.git peut être un fichier (worktree) — doit aussi être masqué."""
        (tmp_path / ".git").write_text("gitdir: ../../.git/worktrees/x")
        (tmp_path / "code.py").write_text("ok")
        fm = FileManager(root=tmp_path)
        result = fm.list_files(".")
        assert ".git" not in result
        assert "code.py" in result

    def test_write_file_depasse_1mb_bloque(self, tmp_path):
        """write_file doit rejeter un contenu > MAX_FILE_SIZE."""
        import tools.file_manager as fm_mod
        from tools.file_manager import FileManager as FM
        original = fm_mod.MAX_FILE_SIZE
        try:
            # Fixer la limite à 10 octets pour le test
            fm_mod.MAX_FILE_SIZE = 10
            fm = FM(root=tmp_path)
            with pytest.raises(ValueError, match="volumineux"):
                fm.write_file("big.txt", "A" * 100)
        finally:
            fm_mod.MAX_FILE_SIZE = original

    def test_write_file_exactement_limite_ok(self, tmp_path, monkeypatch):
        """Un contenu de exactement MAX_FILE_SIZE octets passe."""
        import tools.file_manager as fm_mod
        monkeypatch.setattr(fm_mod, "MAX_FILE_SIZE", 10)
        fm = FileManager(root=tmp_path)
        fm.write_file("exact.txt", "A" * 10)   # 10 octets == limite → ok
        assert (tmp_path / "exact.txt").exists()

    def test_venv_masque_dans_listing(self, tmp_path):
        """.venv ne doit pas apparaître dans list_files."""
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "lib").mkdir()
        fm = FileManager(root=tmp_path)
        result = fm.list_files(".")
        assert ".venv" not in result

    def test_pycache_masque_dans_listing(self, tmp_path):
        """__pycache__ ne doit pas apparaître dans list_files."""
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"PYC")
        fm = FileManager(root=tmp_path)
        result = fm.list_files(".")
        assert "__pycache__" not in result


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


# ------------------------------------------------------------------ #
# Racines autorisées multiples (multi-projets)                        #
# ------------------------------------------------------------------ #

class TestRacinesAutorisees:
    def _two_roots(self, tmp_path):
        a = tmp_path / "proj_a"
        b = tmp_path / "proj_b"
        a.mkdir()
        b.mkdir()
        return a, b

    def test_lecture_absolue_dans_autre_racine_ok(self, tmp_path):
        a, b = self._two_roots(tmp_path)
        (b / "f.txt").write_text("contenu B", encoding="utf-8")
        fm = FileManager(root=a, allowed_roots=[a, b])
        assert fm.read_file(str(b / "f.txt")) == "contenu B"

    def test_ecriture_absolue_dans_autre_racine_ok(self, tmp_path):
        a, b = self._two_roots(tmp_path)
        fm = FileManager(root=a, allowed_roots=[a, b])
        fm.write_file(str(b / "sub" / "new.py"), "print('hi')")
        assert (b / "sub" / "new.py").read_text() == "print('hi')"

    def test_chemin_hors_toutes_racines_bloque(self, tmp_path):
        a, _ = self._two_roots(tmp_path)
        dehors = tmp_path / "dehors"
        dehors.mkdir()
        fm = FileManager(root=a, allowed_roots=[a])
        with pytest.raises(SandboxViolation, match="hors des racines"):
            fm._validate_path(str(dehors / "x.txt"))

    def test_root_primaire_toujours_autorise(self, tmp_path):
        a, _ = self._two_roots(tmp_path)
        autre = tmp_path / "autre"
        autre.mkdir()
        # allowed_roots ne contient PAS `a` : il doit quand même être ajouté.
        fm = FileManager(root=a, allowed_roots=[autre])
        fm.write_file("dans_a.txt", "ok")
        assert (a / "dans_a.txt").read_text() == "ok"

    def test_fichier_sensible_bloque_meme_dans_racine_autorisee(self, tmp_path):
        a, b = self._two_roots(tmp_path)
        fm = FileManager(root=a, allowed_roots=[a, b])
        with pytest.raises(SandboxViolation):
            fm.write_file(str(b / ".env"), "SECRET=1")
