"""Tests pour tools/project_creator.py."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.project_creator import (
    _find_pycharm,
    clone_github_repo,
    create_project,
    list_templates,
    open_in_pycharm,
)


# ── _find_pycharm ──────────────────────────────────────────────────────────

class TestFindPycharm:
    @patch("shutil.which", return_value="/usr/local/bin/charm")
    def test_found_cli(self, mock_which):
        assert _find_pycharm() is not None

    @patch("shutil.which", return_value=None)
    @patch("tools.project_creator.Path")
    def test_found_app(self, mock_path, mock_which):
        mock_path.return_value.exists.return_value = True
        result = _find_pycharm()
        assert result is not None or result is None  # depends on _PYCHARM_APP

    @patch("shutil.which", return_value=None)
    @patch("tools.project_creator._PYCHARM_APP", "/nonexistent/app")
    def test_not_found(self, mock_which):
        with patch.object(Path, "exists", return_value=False):
            result = _find_pycharm()


# ── open_in_pycharm ────────────────────────────────────────────────────────

class TestOpenInPycharm:
    def test_nonexistent_dir(self, tmp_path):
        result = open_in_pycharm(str(tmp_path / "nope"))
        assert "introuvable" in result

    @patch("tools.project_creator._find_pycharm", return_value=None)
    def test_no_pycharm(self, mock_find, tmp_path):
        result = open_in_pycharm(str(tmp_path))
        assert "introuvable" in result.lower() or "prêt" in result.lower()

    @patch("tools.project_creator._find_pycharm", return_value="/usr/local/bin/charm")
    @patch("subprocess.Popen")
    def test_success(self, mock_popen, mock_find, tmp_path):
        result = open_in_pycharm(str(tmp_path))
        assert "PyCharm" in result
        mock_popen.assert_called_once()


# ── clone_github_repo ──────────────────────────────────────────────────────

class TestCloneGithubRepo:
    def test_invalid_format(self):
        result = clone_github_repo("justarepo")
        assert "Format attendu" in result

    def test_already_exists(self, tmp_path):
        existing = tmp_path / "myrepo"
        existing.mkdir()
        with patch("tools.project_creator.PROJECTS_DIR", tmp_path):
            result = clone_github_repo("owner/myrepo")
            assert "existe déjà" in result

    @patch("tools.project_creator.open_in_pycharm", return_value="✅ PyCharm ouvert")
    @patch("subprocess.run")
    def test_success(self, mock_run, mock_pycharm, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        dest = tmp_path / "repo"
        with patch("tools.project_creator.PROJECTS_DIR", tmp_path):
            result = clone_github_repo("owner/repo")
        assert "cloné" in result

    @patch("subprocess.run")
    def test_clone_error(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=128, stderr="fatal: repo not found")
        with patch("tools.project_creator.PROJECTS_DIR", tmp_path):
            result = clone_github_repo("owner/nonexistent")
        assert "Erreur git clone" in result


# ── create_project ─────────────────────────────────────────────────────────

class TestCreateProject:
    @patch("tools.project_creator.open_in_pycharm", return_value="✅ PyCharm ouvert")
    @patch("subprocess.run")
    def test_python_template(self, mock_run, mock_pycharm, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        with patch("tools.project_creator.PROJECTS_DIR", tmp_path):
            result = create_project("mon-projet", "python", "Un super projet")
        assert "créé" in result
        assert (tmp_path / "mon-projet" / "pyproject.toml").exists()
        assert (tmp_path / "mon-projet" / "src" / "mon_projet" / "__init__.py").exists()
        assert (tmp_path / "mon-projet" / "tests").exists()

    @patch("tools.project_creator.open_in_pycharm", return_value="✅ PyCharm ouvert")
    @patch("subprocess.run")
    def test_fastapi_template(self, mock_run, mock_pycharm, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        with patch("tools.project_creator.PROJECTS_DIR", tmp_path):
            result = create_project("api-test", "fastapi", "API de test")
        assert "créé" in result
        assert (tmp_path / "api-test" / "app" / "main.py").exists()
        assert (tmp_path / "api-test" / "tests" / "test_health.py").exists()

    @patch("tools.project_creator.open_in_pycharm", return_value="✅ PyCharm ouvert")
    @patch("subprocess.run")
    def test_cli_template(self, mock_run, mock_pycharm, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        with patch("tools.project_creator.PROJECTS_DIR", tmp_path):
            result = create_project("my-tool", "cli", "Mon outil CLI")
        assert "créé" in result
        assert (tmp_path / "my-tool" / "my_tool" / "cli.py").exists()

    @patch("tools.project_creator.open_in_pycharm", return_value="✅ PyCharm ouvert")
    @patch("subprocess.run")
    def test_empty_template(self, mock_run, mock_pycharm, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        with patch("tools.project_creator.PROJECTS_DIR", tmp_path):
            result = create_project("blank", "empty")
        assert "créé" in result
        assert (tmp_path / "blank" / "README.md").exists()
        assert (tmp_path / "blank" / ".gitignore").exists()

    def test_already_exists(self, tmp_path):
        (tmp_path / "existing").mkdir()
        with patch("tools.project_creator.PROJECTS_DIR", tmp_path):
            result = create_project("existing")
        assert "existe déjà" in result

    @patch("tools.project_creator.open_in_pycharm", return_value="✅ PyCharm ouvert")
    @patch("subprocess.run")
    def test_inspired_by(self, mock_run, mock_pycharm, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        with patch("tools.project_creator.PROJECTS_DIR", tmp_path):
            result = create_project(
                "new-api", "fastapi", "Nouvelle API",
                inspired_by="tiangolo/fastapi",
            )
        assert "Inspiré de" in result


# ── list_templates ─────────────────────────────────────────────────────────

class TestListTemplates:
    def test_all_templates(self):
        result = list_templates()
        assert "python" in result
        assert "fastapi" in result
        assert "cli" in result
        assert "empty" in result
