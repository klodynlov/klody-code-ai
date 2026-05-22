"""Tests pour tools/github_reader.py."""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from tools.github_reader import (
    _parse_owner_repo,
    browse_repo,
    extract_best_practices,
    index_github_repo,
    list_indexed_repos,
    read_github_file,
)


# ── _parse_owner_repo ──────────────────────────────────────────────────────

class TestParseOwnerRepo:
    def test_simple(self):
        assert _parse_owner_repo("fastapi/fastapi") == ("fastapi", "fastapi")

    def test_url(self):
        assert _parse_owner_repo("https://github.com/owner/repo") == ("owner", "repo")

    def test_url_trailing_slash(self):
        assert _parse_owner_repo("https://github.com/owner/repo/") == ("owner", "repo")

    def test_invalid(self):
        with pytest.raises(ValueError, match="owner/repo"):
            _parse_owner_repo("justarepo")


# ── browse_repo ────────────────────────────────────────────────────────────

class TestBrowseRepo:
    @patch("tools.github_reader._gh_get")
    def test_list_root(self, mock_get):
        mock_get.return_value = [
            {"name": "src", "type": "dir"},
            {"name": "README.md", "type": "file", "size": 1024},
            {"name": "setup.py", "type": "file", "size": 512},
        ]
        result = browse_repo("owner/repo")
        assert "📁 src/" in result
        assert "📄 README.md" in result
        assert "📄 setup.py" in result

    @patch("tools.github_reader._gh_get")
    def test_recursive(self, mock_get):
        mock_get.return_value = {
            "tree": [
                {"path": "src", "type": "tree"},
                {"path": "src/main.py", "type": "blob", "size": 200},
                {"path": "README.md", "type": "blob", "size": 100},
            ]
        }
        result = browse_repo("owner/repo", recursive=True)
        assert "src/main.py" in result
        assert "README.md" in result

    @patch("tools.github_reader._gh_get")
    def test_not_found(self, mock_get):
        mock_get.return_value = None
        result = browse_repo("owner/repo")
        assert "Impossible" in result

    @patch("tools.github_reader._gh_get")
    def test_recursive_with_path(self, mock_get):
        mock_get.return_value = {
            "tree": [
                {"path": "src/main.py", "type": "blob", "size": 200},
                {"path": "src/utils.py", "type": "blob", "size": 150},
                {"path": "tests/test.py", "type": "blob", "size": 100},
            ]
        }
        result = browse_repo("owner/repo", path="src", recursive=True)
        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "tests/test.py" not in result


# ── read_github_file ───────────────────────────────────────────────────────

class TestReadGithubFile:
    @patch("tools.github_reader._gh_get")
    def test_base64_file(self, mock_get):
        import base64
        content = "print('hello')\n"
        mock_get.return_value = {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(content.encode()).decode(),
        }
        result = read_github_file("owner/repo", "main.py")
        assert "print('hello')" in result

    @patch("tools.github_reader._gh_get")
    def test_not_found(self, mock_get):
        mock_get.return_value = None
        result = read_github_file("owner/repo", "missing.py")
        assert "introuvable" in result

    @patch("tools.github_reader._gh_get")
    def test_directory_type(self, mock_get):
        mock_get.return_value = {"type": "dir"}
        result = read_github_file("owner/repo", "src")
        assert "n'est pas un fichier" in result


# ── list_indexed_repos ─────────────────────────────────────────────────────

class TestListIndexedRepos:
    @patch("tools.github_reader._LB_BASE", "http://localhost:8765")
    @patch("tools.github_reader.httpx.Client")
    def test_with_repos(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"file_path": "github://owner/repo1/README.md"},
            {"file_path": "github://owner/repo1/docs/guide.md"},
            {"file_path": "github://owner/repo2/README.md"},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        result = list_indexed_repos()
        assert "owner/repo1" in result
        assert "owner/repo2" in result
        assert "2 dépôt(s)" in result

    @patch("tools.github_reader._LB_BASE", "")
    def test_no_config(self):
        result = list_indexed_repos()
        assert "non configuré" in result


# ── index_github_repo ──────────────────────────────────────────────────────

class TestIndexGithubRepo:
    @patch("tools.github_reader._LB_BASE", "http://localhost:8765")
    @patch("tools.github_reader.httpx.Client")
    def test_success(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "added": 2, "updated": 0, "errors": 0,
            "files": ["README.md", "docs/guide.md"],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        result = index_github_repo("owner/repo")
        assert "indexé" in result
        assert "2 ajouté" in result

    @patch("tools.github_reader._LB_BASE", "")
    def test_no_config(self):
        result = index_github_repo("owner/repo")
        assert "non configuré" in result


# ── extract_best_practices ─────────────────────────────────────────────────

class TestExtractBestPractices:
    @patch("tools.github_reader._gh_get")
    @patch("tools.github_reader.read_github_file")
    def test_analysis(self, mock_read, mock_get):
        mock_read.side_effect = lambda repo, path: (
            '[project]\nname = "cool"\n' if path == "pyproject.toml"
            else "Fichier introuvable"
        )
        mock_get.side_effect = [
            {"language": "Python", "description": "A cool project", "stargazers_count": 100},
            {"tree": [{"path": "src"}, {"path": "src/main.py"}, {"path": "tests"}]},
        ]
        result = extract_best_practices("owner/cool")
        assert "owner/cool" in result
        assert "Python" in result
        assert "pyproject.toml" in result

    @patch("tools.github_reader._gh_get")
    @patch("tools.github_reader.read_github_file")
    def test_no_files(self, mock_read, mock_get):
        mock_read.return_value = "Fichier introuvable"
        mock_get.return_value = None
        result = extract_best_practices("owner/empty")
        assert "Aucun fichier" in result
