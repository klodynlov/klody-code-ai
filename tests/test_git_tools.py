"""Tests pour tools/git_tools — introspection Git lecture seule (Roadmap v2 #10).

Happy paths sur un VRAI dépôt Git temporaire ; validations d'injection en amont de
subprocess (aucun git requis pour celles-ci).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from tools import git_tools
from tools.git_tools import format_git_result, git_control

_HAS_GIT = shutil.which("git") is not None


def _run(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """Dépôt Git réel à 1 commit, sous une racine autorisée."""
    if not _HAS_GIT:
        pytest.skip("git absent")
    _run("init", "-q", "-b", "main", ".", cwd=tmp_path)
    _run("config", "user.email", "t@t", cwd=tmp_path)
    _run("config", "user.name", "t", cwd=tmp_path)
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    _run("add", "-A", cwd=tmp_path)
    _run("commit", "-q", "-m", "init", cwd=tmp_path)
    monkeypatch.setattr(git_tools, "_GIT_ROOTS", [tmp_path.resolve()])
    return tmp_path


class TestLectureReelle:
    def test_status(self, repo):
        res = git_control("status", path=str(repo))
        assert res["ok"] is True
        assert "main" in res["output"]

    def test_log(self, repo):
        res = git_control("log", path=str(repo))
        assert res["ok"] is True
        assert "init" in res["output"]

    def test_branch(self, repo):
        res = git_control("branch", path=str(repo))
        assert res["ok"] is True and "main" in res["output"]

    def test_blame_avec_fichier(self, repo):
        res = git_control("blame", path=str(repo), file="hello.py")
        assert res["ok"] is True
        assert "hi" in res["output"]

    def test_show(self, repo):
        res = git_control("show", path=str(repo), ref="HEAD")
        assert res["ok"] is True and "hello.py" in res["output"]


class TestValidation:
    def test_action_inconnue_refusee(self, repo):
        for bad in ("frobnicate", "gc", "fsck"):
            assert git_control(bad, path=str(repo))["ok"] is False

    def test_mutations_dangereuses_absentes_de_lenum(self):
        # add/commit sont autorisés (gated) ; push/pull et les destructives, jamais.
        from tools.registry import TOOLS
        tool = next(t for t in TOOLS if t["function"]["name"] == "git_control")
        enum = set(tool["function"]["parameters"]["properties"]["action"]["enum"])
        assert enum.isdisjoint({"push", "pull", "checkout", "reset", "merge",
                                "rebase", "clean", "rm", "stash", "config"})

    def test_blame_sans_fichier_refuse(self, repo):
        assert git_control("blame", path=str(repo))["ok"] is False

    @pytest.mark.parametrize("ref", ["HEAD; rm -rf /", "$(id)", "--output=/x", "a b", "`x`"])
    def test_ref_malveillante_refusee(self, ref):
        res = git_control("log", ref=ref)
        assert res["ok"] is False and "Ref invalide" in res["error"]

    @pytest.mark.parametrize("file", ["../../etc/passwd", "a; rm", "/etc/passwd", "$(x)"])
    def test_fichier_malveillant_refuse(self, file):
        res = git_control("blame", file=file)
        assert res["ok"] is False and "invalide" in res["error"]

    def test_depot_hors_racines_refuse(self):
        res = git_control("status", path="/etc")
        assert res["ok"] is False
        assert "hors des racines" in res["error"]


class TestEnvironnement:
    def test_git_absent(self, repo, monkeypatch):
        monkeypatch.setattr(git_tools.shutil, "which", lambda _: None)
        res = git_control("status", path=str(repo))
        assert res["ok"] is False and "introuvable" in res["error"]

    def test_dossier_non_repo(self, tmp_path, monkeypatch):
        if not _HAS_GIT:
            pytest.skip("git absent")
        monkeypatch.setattr(git_tools, "_GIT_ROOTS", [tmp_path.resolve()])
        res = git_control("status", path=str(tmp_path))
        assert res["ok"] is False
        assert "dépôt Git" in res["error"]


class TestMutations:
    """add/commit : gated par GIT_WRITE_ENABLED, jamais destructif."""

    def test_add_desactive_par_defaut(self, repo, monkeypatch):
        monkeypatch.setattr(git_tools, "GIT_WRITE_ENABLED", False)
        res = git_control("add", path=str(repo), file=".")
        assert res["ok"] is False and "désactivée" in res["error"]

    def test_commit_desactive_par_defaut(self, repo, monkeypatch):
        monkeypatch.setattr(git_tools, "GIT_WRITE_ENABLED", False)
        res = git_control("commit", path=str(repo), message="x")
        assert res["ok"] is False and "désactivée" in res["error"]

    def test_add_puis_commit_si_active(self, repo, monkeypatch):
        monkeypatch.setattr(git_tools, "GIT_WRITE_ENABLED", True)
        (repo / "new.txt").write_text("data\n", encoding="utf-8")
        assert git_control("add", path=str(repo), file="new.txt")["ok"] is True
        assert git_control("commit", path=str(repo), message="ajoute new.txt")["ok"] is True
        log = git_control("log", path=str(repo))
        assert "ajoute new.txt" in log["output"]

    def test_commit_sans_message_refuse(self, repo, monkeypatch):
        monkeypatch.setattr(git_tools, "GIT_WRITE_ENABLED", True)
        assert git_control("commit", path=str(repo))["ok"] is False

    def test_add_sans_fichier_refuse(self, repo, monkeypatch):
        monkeypatch.setattr(git_tools, "GIT_WRITE_ENABLED", True)
        assert git_control("add", path=str(repo))["ok"] is False

    def test_message_avec_metacaracteres_est_sur(self, repo, monkeypatch):
        # Le message passe en argv : aucune exécution shell, le dépôt reste intact.
        monkeypatch.setattr(git_tools, "GIT_WRITE_ENABLED", True)
        (repo / "sentinel").write_text("keep\n", encoding="utf-8")
        git_control("add", path=str(repo), file=".")
        res = git_control("commit", path=str(repo), message="feat: x; rm -rf / $(id) `whoami`")
        assert res["ok"] is True
        assert (repo / "sentinel").exists()  # rien n'a été supprimé
        assert "rm -rf" in git_control("log", path=str(repo))["output"]


class TestFormat:
    def test_format_ok(self, repo):
        out = format_git_result(git_control("status", path=str(repo)))
        assert out.startswith("$ git status")

    def test_format_erreur(self):
        assert format_git_result({"ok": False, "error": "boom"}) == "boom"
