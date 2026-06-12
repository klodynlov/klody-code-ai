"""Tests pour tools/sandbox.py — venv jetable + auto-détection commande."""
from __future__ import annotations

import logging
import os
import subprocess
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

    def test_commande_str_guillemets_non_fermes_pas_de_crash(self, runner):
        """Un guillemet non fermé (souvent un script collé à la place d'une
        commande) ne doit PAS lever ValueError — message actionnable à la place."""
        result = runner.run("echo 'unterminated", timeout=5)
        assert result.success is False
        assert result.exit_code == 1
        assert "non analysable" in result.stderr
        # Pas besoin de venv : la garde court avant ensure_venv().
        assert not runner.venv_dir.exists()

    @pytest.mark.slow
    def test_timeout_dumpe_la_pile_python(self, runner, tmp_path):
        """Au timeout, un script Python bloqué doit renvoyer la pile faulthandler
        (où ça coince) et non un timeout opaque — cf. deadlock session 419676b5."""
        hang = tmp_path / "hang.py"
        # Lock non-réentrant ré-acquis → deadlock permanent.
        hang.write_text(
            "import threading\nlock = threading.Lock()\nlock.acquire()\nlock.acquire()\n",
            encoding="utf-8",
        )
        if not runner.ensure_venv():
            pytest.skip("venv non disponible")
        result = runner.run(["python", "hang.py"], timeout=4)
        assert result.timed_out is True
        assert result.exit_code == 124
        assert "most recent call first" in result.stderr  # dump faulthandler
        assert "hang.py" in result.stderr

    @pytest.mark.slow
    def test_stdin_devnull_input_ne_pend_pas(self, runner, tmp_path):
        """input() sans TTY doit échouer vite (EOFError) au lieu de pendre
        jusqu'au timeout — stdin est branché sur /dev/null."""
        asks = tmp_path / "asks.py"
        asks.write_text("x = input('? ')\nprint(x)\n", encoding="utf-8")
        if not runner.ensure_venv():
            pytest.skip("venv non disponible")
        result = runner.run(["python", "asks.py"], timeout=10)
        assert result.timed_out is False
        assert "EOFError" in result.stderr


# ── Packages par défaut + requirements.txt (staleness mtime) ─────────────────


@pytest.fixture
def fake_venv_runner(tmp_path, monkeypatch):
    """Runner dont le venv « existe » déjà — subprocess.run remplacé par un
    enregistreur d'appels. Aucun venv réel : tests rapides du cycle de vie."""
    cache = tmp_path / "_klody_cache"
    runner = SandboxRunner(workdir=tmp_path, cache_root=cache)
    (runner.venv_dir / "bin").mkdir(parents=True)
    runner.python.touch()

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.sandbox.subprocess.run", fake_run)
    return runner, calls


def _pip_calls(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if c and c[0].endswith("/pip")]


class TestDefaultPackages:
    def test_defauts_legers_calcul_et_http(self):
        """Socle volontairement minimal : tests + calcul + HTTP. Les libs
        lourdes (pandas, torch, opencv…) restent à la demande/requirements."""
        assert set(SandboxRunner._DEFAULT_PACKAGES) == {"pytest", "numpy", "requests"}


class TestRequirementsStaleness:
    """requirements.txt écrit ou modifié APRÈS le 1er run → pris en compte
    au run suivant (avant : ignoré jusqu'au restart backend, _ready cachait)."""

    def test_sans_requirements_aucun_pip(self, fake_venv_runner):
        runner, calls = fake_venv_runner
        assert runner.ensure_venv() is True
        assert _pip_calls(calls) == []

    def test_requirements_present_installe_une_fois(self, fake_venv_runner):
        runner, calls = fake_venv_runner
        req = runner.workdir / "requirements.txt"
        req.write_text("httpx\n", encoding="utf-8")
        assert runner.ensure_venv() is True
        assert runner.ensure_venv() is True  # 2e appel sans changement
        pips = _pip_calls(calls)
        assert len(pips) == 1
        assert "-r" in pips[0] and str(req) in pips[0]

    def test_requirements_ecrit_apres_premier_run_installe(self, fake_venv_runner):
        """LE bug d'origine : req écrit après le 1er ensure_venv → installé."""
        runner, calls = fake_venv_runner
        assert runner.ensure_venv() is True
        assert _pip_calls(calls) == []

        req = runner.workdir / "requirements.txt"
        req.write_text("pandas\n", encoding="utf-8")
        assert runner.ensure_venv() is True
        assert len(_pip_calls(calls)) == 1

    def test_requirements_modifie_reinstalle(self, fake_venv_runner):
        runner, calls = fake_venv_runner
        req = runner.workdir / "requirements.txt"
        req.write_text("pandas\n", encoding="utf-8")
        runner.ensure_venv()
        # mtime forcé dans le futur (l'écriture peut tomber dans la même seconde)
        st = req.stat()
        os.utime(req, (st.st_atime, st.st_mtime + 10))
        runner.ensure_venv()
        assert len(_pip_calls(calls)) == 2

    def test_requirements_supprime_pas_de_pip(self, fake_venv_runner):
        runner, calls = fake_venv_runner
        req = runner.workdir / "requirements.txt"
        req.write_text("pandas\n", encoding="utf-8")
        runner.ensure_venv()
        req.unlink()
        runner.ensure_venv()
        assert len(_pip_calls(calls)) == 1  # pas de réinstall fantôme

    def test_pip_echec_logge_mais_sandbox_utilisable(self, tmp_path, monkeypatch, caplog):
        """Un requirements cassé ne tue pas le sandbox — mais n'est plus muet."""
        cache = tmp_path / "_klody_cache"
        runner = SandboxRunner(workdir=tmp_path, cache_root=cache)
        (runner.venv_dir / "bin").mkdir(parents=True)
        runner.python.touch()
        (tmp_path / "requirements.txt").write_text("paquet-inexistant-xyz\n", encoding="utf-8")

        def fail_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No matching distribution")

        monkeypatch.setattr("tools.sandbox.subprocess.run", fail_run)
        with caplog.at_level(logging.WARNING, logger="tools.sandbox"):
            assert runner.ensure_venv() is True
        assert any("requirements.txt" in r.message for r in caplog.records)


class TestEnvHeadless:
    def test_mplbackend_agg_injecte(self, tmp_path, monkeypatch):
        """matplotlib sans écran : MPLBACKEND=Agg doit être posé, sinon
        plt.show() ouvre le backend macosx et pend jusqu'au timeout."""
        cache = tmp_path / "_klody_cache"
        runner = SandboxRunner(workdir=tmp_path, cache_root=cache)
        monkeypatch.setattr(SandboxRunner, "ensure_venv", lambda self: True)

        captured: dict = {}

        class FakeProc:
            returncode = 0

            def communicate(self, timeout=None):
                return ("", "")

        def fake_popen(cmd, env=None, **kwargs):
            captured["env"] = env
            return FakeProc()

        monkeypatch.setattr("tools.sandbox.subprocess.Popen", fake_popen)
        result = runner.run(["python", "plot.py"], timeout=5)
        assert result.exit_code == 0
        assert captured["env"]["MPLBACKEND"] == "Agg"
        assert captured["env"]["PYTHONFAULTHANDLER"] == "1"
