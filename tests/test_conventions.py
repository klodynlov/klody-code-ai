"""Tests pour agent.conventions — détection heuristique."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.conventions import (
    Convention,
    ConventionDetector,
    ConventionReport,
    _collect_stats,
    _detect_test_framework,
    _detect_async_style,
    _detect_type_hints,
    _detect_logging_style,
    _detect_frameworks,
    _detect_package_manager,
    _detect_ci,
)


# ── Mini repos en fixture ─────────────────────────────────────────────────────


@pytest.fixture
def repo_pytest(tmp_path: Path) -> Path:
    (tmp_path / "conftest.py").write_text("# conftest", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_a.py").write_text(
        "import pytest\ndef test_x(): assert True\n", encoding="utf-8"
    )
    (tmp_path / "test_b.py").write_text(
        "import pytest\ndef test_y(): assert True\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def repo_async(tmp_path: Path) -> Path:
    code = "import httpx\n" + "".join(
        f"async def f{i}():\n    return 1\n" for i in range(20)
    ) + "".join(f"def g{i}(): pass\n" for i in range(3))
    (tmp_path / "main.py").write_text(code, encoding="utf-8")
    return tmp_path


@pytest.fixture
def repo_typed(tmp_path: Path) -> Path:
    code = "".join(
        f"def f{i}(x: int, y: str) -> bool:\n    return True\n"
        for i in range(15)
    ) + "".join(f"def g{i}(x):\n    pass\n" for i in range(8))
    (tmp_path / "lib.py").write_text(code, encoding="utf-8")
    return tmp_path


@pytest.fixture
def repo_fastapi(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "from fastapi import FastAPI\nimport httpx\napp = FastAPI()\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("fastapi\nhttpx\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    return tmp_path


# ── Détecteurs individuels ────────────────────────────────────────────────────


class TestTestFramework:
    def test_pytest_detecte(self, repo_pytest):
        stats = _collect_stats(repo_pytest)
        c = _detect_test_framework(stats)
        assert c is not None
        assert c.value == "pytest"
        assert c.confidence >= 0.8

    def test_aucun_test_rien_detecte(self, tmp_path):
        (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
        stats = _collect_stats(tmp_path)
        assert _detect_test_framework(stats) is None

    def test_unittest_detecte(self, tmp_path):
        (tmp_path / "tests.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_x(self): pass\n",
            encoding="utf-8",
        )
        stats = _collect_stats(tmp_path)
        c = _detect_test_framework(stats)
        assert c is not None
        assert c.value == "unittest"


class TestAsyncStyle:
    def test_async_heavy_detecte(self, repo_async):
        stats = _collect_stats(repo_async)
        c = _detect_async_style(stats)
        assert c is not None
        assert c.value == "async-heavy"

    def test_seuil_minimum(self, tmp_path):
        # Moins de 10 defs → pas de détection (trop peu de signal)
        (tmp_path / "x.py").write_text("def a(): pass\nasync def b(): pass\n", encoding="utf-8")
        stats = _collect_stats(tmp_path)
        assert _detect_async_style(stats) is None


class TestTypeHints:
    def test_typed_detecte(self, repo_typed):
        stats = _collect_stats(repo_typed)
        c = _detect_type_hints(stats)
        assert c is not None
        assert c.value == "utilisés"

    def test_seuil_minimum(self, tmp_path):
        (tmp_path / "x.py").write_text(
            "def a(x: int): pass\ndef b(y): pass\n", encoding="utf-8"
        )
        stats = _collect_stats(tmp_path)
        # Moins de 20 defs → None
        assert _detect_type_hints(stats) is None


class TestLogging:
    def test_logger_detecte(self, tmp_path):
        (tmp_path / "x.py").write_text(
            "import logging\nlogger = logging.getLogger(__name__)\n"
            + "\n".join(f"logger.info('{i}')" for i in range(10)),
            encoding="utf-8",
        )
        stats = _collect_stats(tmp_path)
        c = _detect_logging_style(stats)
        assert c is not None
        assert c.value == "logger module"

    def test_print_detecte(self, tmp_path):
        (tmp_path / "x.py").write_text(
            "\n".join(f"print('{i}')" for i in range(10)),
            encoding="utf-8",
        )
        stats = _collect_stats(tmp_path)
        c = _detect_logging_style(stats)
        assert c is not None
        assert "print" in c.value


class TestFrameworks:
    def test_fastapi_detecte(self, repo_fastapi):
        stats = _collect_stats(repo_fastapi)
        c = _detect_frameworks(stats)
        assert c is not None
        assert "FastAPI" in c.value
        assert "httpx" in c.value

    def test_aucun_framework(self, tmp_path):
        (tmp_path / "main.py").write_text("print('plain python')", encoding="utf-8")
        stats = _collect_stats(tmp_path)
        assert _detect_frameworks(stats) is None


class TestPackageManager:
    def test_pip_via_requirements(self, repo_fastapi):
        stats = _collect_stats(repo_fastapi)
        c = _detect_package_manager(stats)
        assert c is not None
        assert c.value == "pip"

    def test_npm_via_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
        stats = _collect_stats(tmp_path)
        c = _detect_package_manager(stats)
        assert c is not None
        assert c.value == "npm"

    def test_pnpm_via_lockfile(self, tmp_path):
        (tmp_path / "package.json").write_text('{}', encoding="utf-8")
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 5", encoding="utf-8")
        stats = _collect_stats(tmp_path)
        c = _detect_package_manager(stats)
        assert c.value == "pnpm"


class TestCi:
    def test_github_actions(self, repo_fastapi):
        stats = _collect_stats(repo_fastapi)
        c = _detect_ci(stats)
        assert c is not None
        assert "github-actions" in c.value


# ── ConventionDetector end-to-end ─────────────────────────────────────────────


class TestDetector:
    def test_detect_renvoie_report(self, repo_fastapi):
        det = ConventionDetector(repo_fastapi)
        report = det.detect()
        assert isinstance(report, ConventionReport)
        # Au moins package_manager, frameworks, ci sur ce repo
        names = {c.name for c in report.conventions}
        assert "package_manager" in names
        assert "frameworks" in names
        assert "ci" in names

    def test_cache_evite_re_scan(self, repo_fastapi):
        det = ConventionDetector(repo_fastapi)
        report1 = det.detect()
        # 2e appel sans force → cache hit
        report2 = det.detect()
        assert report1.detected_at == report2.detected_at

    def test_force_invalide_cache(self, repo_fastapi):
        import time
        det = ConventionDetector(repo_fastapi)
        report1 = det.detect()
        time.sleep(0.02)
        report2 = det.detect(force=True)
        assert report2.detected_at > report1.detected_at

    def test_cache_persiste_sur_disque(self, repo_fastapi):
        det = ConventionDetector(repo_fastapi)
        det.detect()
        cache_file = repo_fastapi / ".klody" / "conventions.json"
        assert cache_file.exists()

    def test_format_for_prompt_vide_si_rien(self, tmp_path):
        det = ConventionDetector(tmp_path)
        report = det.detect()
        # Repo vide → peu de conventions
        # Mais format_for_prompt doit gérer même 0 conventions
        s = report.format_for_prompt()
        assert s == "" or "Conventions" in s
