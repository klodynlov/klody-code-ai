"""Tests pour tools/sandbox.py — venv jetable + auto-détection commande."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools.sandbox import SandboxResult, SandboxRunner, auto_command_for


# ── auto_command_for ──────────────────────────────────────────────────────────


class TestAutoCommand:
    def test_pas_python_retourne_none(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text("{}", encoding="utf-8")
        assert auto_command_for(f) is None

    def test_fichier_test_lance_pytest(self, tmp_path):
        f = tmp_path / "test_foo.py"
        f.write_text("def test_a(): assert True\n", encoding="utf-8")
        cmd = auto_command_for(f)
        assert cmd is not None
        assert cmd[0] == "pytest"
        assert "test_foo.py" in cmd

    def test_fichier_contenant_def_test_lance_pytest(self, tmp_path):
        f = tmp_path / "validators.py"
        f.write_text("# module avec tests inline\ndef test_basic():\n    assert 1 == 1\n", encoding="utf-8")
        cmd = auto_command_for(f)
        assert cmd is not None
        assert cmd[0] == "pytest"

    def test_fichier_avec_main_lance_python(self, tmp_path):
        f = tmp_path / "script.py"
        f.write_text(
            'def main():\n    print("hi")\n\nif __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
        cmd = auto_command_for(f)
        assert cmd == ["python", "script.py"]

    def test_module_simple_lance_pycompile(self, tmp_path):
        f = tmp_path / "lib.py"
        f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        cmd = auto_command_for(f)
        assert cmd is not None
        assert cmd[:3] == ["python", "-m", "py_compile"]


# ── SandboxRunner ─────────────────────────────────────────────────────────────


@pytest.fixture
def runner(tmp_path):
    """SandboxRunner avec cache_root dans tmp pour ne pas polluer ~/.cache."""
    cache = tmp_path / "_klody_cache"
    return SandboxRunner(workdir=tmp_path, cache_root=cache)


class TestSandboxRunner:
    def test_venv_cree_paresseusement(self, runner):
        """Le venv ne doit pas être créé à l'instanciation."""
        assert not runner.venv_dir.exists()

    @pytest.mark.slow
    def test_ensure_venv_cree_python(self, runner):
        """ensure_venv() crée un Python utilisable."""
        ok = runner.ensure_venv()
        assert ok is True
        assert runner.python.exists()

    @pytest.mark.slow
    def test_run_pytest_fichier_passant(self, runner, tmp_path):
        """pytest sur un test qui passe → exit 0."""
        test_file = tmp_path / "test_ok.py"
        test_file.write_text(
            "def test_ok():\n    assert 1 + 1 == 2\n",
            encoding="utf-8",
        )
        result = runner.run(["pytest", "test_ok.py", "-q"], timeout=60)
        assert result.success is True
        assert result.exit_code == 0
        assert "passed" in result.stdout or "passed" in result.stderr

    @pytest.mark.slow
    def test_run_pytest_fichier_echouant(self, runner, tmp_path):
        """pytest sur un test qui échoue → exit != 0 + stderr/stdout."""
        test_file = tmp_path / "test_fail.py"
        test_file.write_text(
            "def test_ko():\n    assert 1 == 2\n",
            encoding="utf-8",
        )
        result = runner.run(["pytest", "test_fail.py", "-q"], timeout=60)
        assert result.success is False
        assert result.exit_code != 0

    @pytest.mark.slow
    def test_run_python_syntaxe_invalide(self, runner, tmp_path):
        """py_compile détecte une SyntaxError."""
        bad = tmp_path / "broken.py"
        bad.write_text("def f(:\n    pass\n", encoding="utf-8")
        result = runner.run(["python", "-m", "py_compile", "broken.py"], timeout=20)
        assert result.success is False
        assert "SyntaxError" in result.stderr or "SyntaxError" in result.stdout

    def test_run_commande_vide(self, runner):
        result = runner.run([], timeout=5)
        assert result.success is False

    def test_format_for_llm_lisible(self):
        r = SandboxResult(
            command="pytest x.py",
            exit_code=1,
            stdout="1 failed",
            stderr="AssertionError",
            duration_s=0.42,
        )
        s = r.format_for_llm()
        assert "FAIL" in s
        assert "pytest x.py" in s
        assert "AssertionError" in s

    def test_truncation_sortie_longue(self, runner, tmp_path):
        """Une sortie de 100 KB doit être tronquée à ~3000 chars."""
        # Génère un script qui affiche beaucoup
        script = tmp_path / "noisy.py"
        script.write_text(
            "for _ in range(20000): print('x' * 50)\n",
            encoding="utf-8",
        )
        # On utilise le Python système pour éviter de devoir créer le venv
        # (test rapide, on ne teste pas le venv ici, on teste la troncation).
        # ensure_venv() est quand même appelé — on skip si trop lent.
        if not runner.ensure_venv():
            pytest.skip("venv non disponible")
        result = runner.run(["python", "noisy.py"], timeout=10)
        # 3000 chars max + petit overhead du format
        assert len(result.stdout) <= 3000
