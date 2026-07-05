"""Tests pour tools/docker_tools — introspection Docker lecture seule (Roadmap v2 #10).

Aucune dépendance à un vrai Docker : subprocess.run et shutil.which sont mockés.
Cœur des tests : anti-injection de commande (argv sans shell, cible validée) et
refus des mutations.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from tools import docker_tools
from tools.docker_tools import docker_control, format_docker_result


@pytest.fixture
def fake_docker(monkeypatch):
    """docker présent ; subprocess.run capturé (argv) et scriptable (retour)."""
    monkeypatch.setattr(docker_tools.shutil, "which", lambda _: "/usr/bin/docker")
    calls: list[list[str]] = []
    result = {"returncode": 0, "stdout": "OK", "stderr": ""}

    def _fake_run(argv, **kwargs):
        calls.append(argv)
        # Garde-fou du test : jamais de shell.
        assert kwargs.get("shell") is False
        return SimpleNamespace(
            returncode=result["returncode"],
            stdout=result["stdout"],
            stderr=result["stderr"],
        )

    monkeypatch.setattr(docker_tools.subprocess, "run", _fake_run)
    return SimpleNamespace(calls=calls, result=result)


class TestActions:
    def test_ps_construit_argv_fige(self, fake_docker):
        res = docker_control("ps")
        assert res["ok"] is True
        argv = fake_docker.calls[0]
        assert argv[0] == "docker" and argv[1] == "ps"
        assert "--all" in argv

    def test_action_inconnue_refusee(self, fake_docker):
        res = docker_control("run")  # mutation → non supportée
        assert res["ok"] is False
        assert "non supportée" in res["error"]
        assert fake_docker.calls == []  # jamais exécuté

    def test_mutations_absentes_de_lenum(self):
        from tools.registry import TOOLS
        tool = next(t for t in TOOLS if t["function"]["name"] == "docker_control")
        enum = set(tool["function"]["parameters"]["properties"]["action"]["enum"])
        assert enum.isdisjoint({"run", "build", "exec", "rm", "stop", "kill", "cp"})

    def test_logs_tail_clampe(self, fake_docker):
        docker_control("logs", "web", tail=99999)
        argv = fake_docker.calls[0]
        i = argv.index("--tail")
        assert argv[i + 1] == "500"  # _MAX_TAIL


class TestCibleValidation:
    def test_inspect_sans_cible_refuse(self, fake_docker):
        res = docker_control("inspect")
        assert res["ok"] is False and "requiert" in res["error"]
        assert fake_docker.calls == []

    @pytest.mark.parametrize("bad", [
        "web; rm -rf /",
        "$(whoami)",
        "`id`",
        "a && b",
        "a|b",
        "--privileged",
        "-v/etc",
        "name with space",
        "web\nrm",
    ])
    def test_cibles_malveillantes_refusees(self, fake_docker, bad):
        res = docker_control("inspect", bad)
        assert res["ok"] is False
        assert "invalide" in res["error"]
        assert fake_docker.calls == []  # jamais passé à subprocess

    @pytest.mark.parametrize("good", ["web", "my-app_1", "sha256:abcdef", "registry/img:1.2", "a1b2c3"])
    def test_cibles_valides_passent_comme_un_seul_argv(self, fake_docker, good):
        res = docker_control("inspect", good)
        assert res["ok"] is True
        argv = fake_docker.calls[0]
        # La cible est un ÉLÉMENT argv distinct (pas concaténé) et le dernier.
        assert argv[-1] == good
        assert argv[:2] == ["docker", "inspect"]


class TestEnvironnement:
    def test_docker_absent(self, monkeypatch):
        monkeypatch.setattr(docker_tools.shutil, "which", lambda _: None)
        res = docker_control("ps")
        assert res["ok"] is False
        assert "introuvable" in res["error"]

    def test_daemon_injoignable(self, fake_docker):
        fake_docker.result.update(returncode=1,
                                  stderr="Cannot connect to the Docker daemon at unix://…")
        res = docker_control("ps")
        assert res["ok"] is False
        assert "injoignable" in res["error"]

    def test_timeout(self, monkeypatch):
        monkeypatch.setattr(docker_tools.shutil, "which", lambda _: "/usr/bin/docker")

        def _boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=15)

        monkeypatch.setattr(docker_tools.subprocess, "run", _boom)
        res = docker_control("ps")
        assert res["ok"] is False and "expirée" in res["error"]


class TestSortie:
    def test_troncature(self, fake_docker):
        fake_docker.result.update(stdout="x" * 50_000)
        res = docker_control("ps")
        assert res["truncated"] is True
        assert "tronquée" in res["output"]

    def test_format_ok(self, fake_docker):
        out = format_docker_result(docker_control("inspect", "web"))
        assert out.startswith("$ docker inspect web")

    def test_format_erreur(self):
        assert format_docker_result({"ok": False, "error": "boom"}) == "boom"
