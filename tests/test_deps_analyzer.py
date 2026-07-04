"""Tests pour tools/deps_analyzer — inventaire multi-écosystèmes (Roadmap v2 #10)."""
from __future__ import annotations

from pathlib import Path

from tools.deps_analyzer import (
    analyze_dependencies,
    format_dependency_report,
    _parse_go_mod,
    _parse_requirements,
)


class TestParsers:
    def test_requirements_ignore_commentaires_et_options(self):
        text = (
            "# commentaire\n"
            "fastapi>=0.100\n"
            "\n"
            "requests==2.31.0  # inline\n"
            "-r autre.txt\n"
            "--hash=sha256:abc\n"
        )
        deps = _parse_requirements(text)
        assert deps == ["fastapi>=0.100", "requests==2.31.0"]

    def test_go_mod_bloc_et_ligne_simple(self):
        text = (
            "module example.com/x\n"
            "go 1.22\n"
            "require github.com/gin-gonic/gin v1.9.1\n"
            "require (\n"
            "    github.com/stretchr/testify v1.8.4 // indirect\n"
            "    golang.org/x/sync v0.5.0\n"
            ")\n"
        )
        deps = _parse_go_mod(text)
        assert "github.com/gin-gonic/gin v1.9.1" in deps
        assert "github.com/stretchr/testify v1.8.4" in deps
        assert "golang.org/x/sync v0.5.0" in deps


class TestAnalyzeDir:
    def test_scan_multi_manifestes(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("fastapi>=0.1\npydantic>=2\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            '{"dependencies": {"react": "^19"}, "devDependencies": {"vite": "^5"}}',
            encoding="utf-8",
        )
        res = analyze_dependencies(tmp_path)
        assert res["total"] == 4
        assert set(res["ecosystems"]) == {"pip", "npm"}
        files = {m["file"] for m in res["manifests"]}
        assert files == {"requirements.txt", "package.json"}

    def test_scan_ne_descend_pas_recursivement(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("a\n", encoding="utf-8")
        sub = tmp_path / "node_modules" / "pkg"
        sub.mkdir(parents=True)
        (sub / "package.json").write_text('{"dependencies": {"x": "1"}}', encoding="utf-8")
        res = analyze_dependencies(tmp_path)
        # Seul le requirements.txt racine est vu, pas le package.json imbriqué.
        assert [m["file"] for m in res["manifests"]] == ["requirements.txt"]

    def test_pyproject_pep621(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\ndependencies = ["httpx>=0.27", "rich"]\n'
            '[project.optional-dependencies]\ndev = ["pytest"]\n',
            encoding="utf-8",
        )
        res = analyze_dependencies(tmp_path)
        deps = res["manifests"][0]["dependencies"]
        assert "httpx>=0.27" in deps
        assert "pytest" in deps

    def test_cargo_et_composer(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text(
            '[dependencies]\nserde = "1.0"\ntokio = { version = "1", features = ["full"] }\n',
            encoding="utf-8",
        )
        (tmp_path / "composer.json").write_text(
            '{"require": {"php": ">=8.1", "symfony/console": "^7"}}', encoding="utf-8"
        )
        res = analyze_dependencies(tmp_path)
        by_file = {m["file"]: m["dependencies"] for m in res["manifests"]}
        assert "serde 1.0" in by_file["Cargo.toml"]
        assert any(d.startswith("tokio") for d in by_file["Cargo.toml"])
        assert "symfony/console:^7" in by_file["composer.json"]


class TestAnalyzeFile:
    def test_fichier_manifeste_precis(self, tmp_path: Path):
        req = tmp_path / "requirements-dev.txt"
        req.write_text("black\nruff\nmypy\n", encoding="utf-8")
        res = analyze_dependencies(req)
        assert res["total"] == 3
        assert res["manifests"][0]["ecosystem"] == "pip"


class TestFormat:
    def test_rapport_vide(self, tmp_path: Path):
        res = analyze_dependencies(tmp_path)
        out = format_dependency_report(res)
        assert "Aucun manifeste" in out

    def test_rapport_lisible(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("fastapi>=0.1\n", encoding="utf-8")
        out = format_dependency_report(analyze_dependencies(tmp_path))
        assert "requirements.txt" in out
        assert "fastapi>=0.1" in out
