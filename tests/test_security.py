"""Tests sécurité — non-régression sur le périmètre du sandbox.

Le modèle de sécurité Klody repose sur deux barrières :

1. **FileManager** : tout accès fichier passe par `_validate_path` qui
   refuse les chemins absolus, le path traversal et les symlinks sortants.
2. **SandboxRunner** : isolation des dépendances Python dans un venv jetable.
   Note : la barrière process est limitée (subprocess hérite des droits FS) ;
   la garantie est que le venv et le cwd restent dans `workdir`.

Ces tests bloquent les régressions sur les invariants ci-dessus. Ils
complètent test_file_manager.py et test_sandbox.py qui couvrent le bonheur.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.file_manager import FileManager, SandboxViolation
from tools.sandbox import SandboxRunner


@pytest.fixture
def fm(tmp_path: Path) -> FileManager:
    return FileManager(root=tmp_path)


# ─── FileManager : surface d'attaque chemins ──────────────────────────────────


class TestFileManagerPathTraversal:
    """Tente toutes les variantes courantes de path traversal."""

    @pytest.mark.parametrize("evil", [
        "../etc/passwd",
        "../../etc/passwd",
        "subdir/../../etc/passwd",
        "./../../etc/passwd",
        "foo/./../../bar",
    ])
    def test_path_traversal_variants(self, fm, evil):
        with pytest.raises(SandboxViolation):
            fm.read_file(evil)

    def test_windows_style_backslash_does_not_escape(self, fm):
        # Sur Unix, `.\..\foo` est un nom de fichier valide (avec backslashes).
        # Il ne sort PAS du sandbox — il n'existe simplement pas → FileNotFoundError.
        # L'invariant à vérifier : pas de lecture d'un fichier hors sandbox.
        with pytest.raises((SandboxViolation, FileNotFoundError)):
            fm.read_file(".\\..\\windows\\system32")

    def test_absolute_path_rejected(self, fm):
        with pytest.raises(SandboxViolation, match="hors des racines"):
            fm.read_file("/etc/passwd")

    def test_absolute_path_windows_rejected(self, fm):
        # Sur Unix, "C:\\..." est interprété comme un chemin relatif
        # contenant un backslash — ce n'est pas un chemin absolu Unix.
        # On vérifie au moins qu'il ne sort pas du sandbox.
        with pytest.raises((SandboxViolation, FileNotFoundError)):
            fm.read_file("C:\\Windows\\System32\\config")

    def test_empty_path_rejected(self, fm):
        with pytest.raises(SandboxViolation, match="vide"):
            fm.read_file("")
        with pytest.raises(SandboxViolation, match="vide"):
            fm.read_file("   ")

    def test_null_byte_in_path(self, fm):
        # Les null bytes peuvent tronquer le chemin côté OS — Python lève sur stat
        with pytest.raises((SandboxViolation, ValueError, OSError)):
            fm.read_file("safe.txt\x00../../etc/passwd")


class TestFileManagerSymlinkEscape:
    """Un symlink dans le sandbox qui pointerait dehors doit être bloqué."""

    def test_symlink_outside_sandbox_blocked(self, fm, tmp_path):
        # Crée un symlink à l'intérieur du sandbox pointant DEHORS
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("nuclear codes", encoding="utf-8")
        link = tmp_path / "innocent.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("Symlinks indisponibles sur ce FS")

        # Le symlink est résolu AVANT le check sandbox, donc on tombe sur
        # le check "hors sandbox" générique — ce qui est OK, le but est que
        # la lecture du fichier hors sandbox soit BLOQUÉE.
        with pytest.raises(SandboxViolation):
            fm.read_file("innocent.txt")

    def test_symlink_inside_sandbox_ok(self, fm, tmp_path):
        target = tmp_path / "real.txt"
        target.write_text("safe", encoding="utf-8")
        link = tmp_path / "alias.txt"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("Symlinks indisponibles sur ce FS")

        # Ne doit PAS lever — le symlink pointe dans la sandbox
        assert fm.read_file("alias.txt") == "safe"


class TestFileManagerBlockedFiles:
    """Les fichiers/extensions sensibles sont bloqués même dans la sandbox."""

    @pytest.mark.parametrize("filename", [
        ".env", ".env.local", ".env.production", ".env.staging",
    ])
    def test_dotenv_blocked(self, fm, tmp_path, filename):
        (tmp_path / filename).write_text("SECRET=x", encoding="utf-8")
        with pytest.raises(SandboxViolation, match="[Bb]loqu"):
            fm.read_file(filename)

    @pytest.mark.parametrize("ext", [".key", ".pem", ".p12", ".pfx", ".cer"])
    def test_credential_extensions_blocked(self, fm, tmp_path, ext):
        f = tmp_path / f"x{ext}"
        f.write_text("--BEGIN PRIVATE KEY--", encoding="utf-8")
        with pytest.raises(SandboxViolation, match="[Bb]loqu"):
            fm.read_file(f"x{ext}")


# ─── SandboxRunner : isolation cwd ─────────────────────────────────────────────


@pytest.mark.slow
class TestSandboxRunnerCwdIsolation:
    """Le subprocess sandbox tourne dans workdir ; il ne contamine pas le parent."""

    def test_subprocess_cwd_is_workdir(self, tmp_path):
        runner = SandboxRunner(workdir=tmp_path)
        result = runner.run(
            ["python", "-c", "import os; print(os.getcwd())"],
            timeout=20,
        )
        assert result.exit_code == 0
        # Sur macOS, /tmp peut être un symlink vers /private/tmp
        actual = Path(result.stdout.strip()).resolve()
        assert actual == tmp_path.resolve(), (
            f"Le subprocess sandbox tourne dans {actual}, "
            f"attendu {tmp_path.resolve()}"
        )

    def test_subprocess_chdir_does_not_affect_parent(self, tmp_path):
        runner = SandboxRunner(workdir=tmp_path)
        parent_before = os.getcwd()
        runner.run(
            ["python", "-c", "import os; os.chdir('/'); print('done')"],
            timeout=20,
        )
        parent_after = os.getcwd()
        assert parent_before == parent_after, (
            "Le cwd parent a été contaminé par le subprocess sandbox"
        )

    def test_empty_command_returns_error_not_crash(self, tmp_path):
        runner = SandboxRunner(workdir=tmp_path)
        result = runner.run([])
        assert result.exit_code == 1
        assert "vide" in result.stderr.lower()

    def test_timeout_kills_runaway_process(self, tmp_path):
        runner = SandboxRunner(workdir=tmp_path)
        result = runner.run(
            ["python", "-c", "import time; time.sleep(30)"],
            timeout=2,
        )
        assert result.timed_out, "Le timeout n'a pas tué le process runaway"
        assert result.exit_code == 124, "exit_code convention shell pour timeout"
