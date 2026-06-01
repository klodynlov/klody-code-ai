"""Détecteur de conventions du projet (Roadmap v2 #8).

Scanne le projet à l'init et identifie les conventions dominantes pour les
injecter dans le system prompt — Klody adapte son code au style du repo
sans qu'on ait à lui dire.

Approche : heuristiques pures + comptage statistique (pas de LLM).
Rapide (~100ms sur 100 fichiers), déterministe, debuggable.

Conventions détectées :
- Test framework (pytest vs unittest)
- Style (async vs sync, type hints, classes vs fonctions)
- Logging (logger vs print)
- Frameworks utilisés (FastAPI, Flask, Django, React, Vue…)
- Build / package manager (pip, poetry, npm, yarn, pnpm)
- CI (github actions, gitlab, circleci)
- Formatter (black, ruff, prettier)

Cache : <project>/.klody/conventions.json — invalidé via API ou TTL.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Dossiers à skipper
_SKIP_DIRS = frozenset({
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".git",
    "node_modules", "dist", "build", ".next", ".nuxt", ".cache",
    "htmlcov", ".mypy_cache", ".tox", "_preview", "preview", ".claude",
    ".klody", "imports", "logs",
})

# Cache TTL : 24h (suffisant — les conventions changent rarement)
_CACHE_TTL_S = 24 * 3600

_KLODY_DIR = ".klody"
_CACHE_FILE = "conventions.json"


@dataclass
class Convention:
    """Une convention détectée avec son évidence."""
    name: str           # ex: "test_framework"
    value: str          # ex: "pytest"
    evidence: str       # ex: "12 fichiers test_*.py + conftest.py présent"
    confidence: float   # 0.0 - 1.0


@dataclass
class ConventionReport:
    """Résultat complet d'une détection."""
    workdir: str
    detected_at: float = field(default_factory=time.time)
    conventions: list[Convention] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def is_fresh(self) -> bool:
        return (time.time() - self.detected_at) < _CACHE_TTL_S

    def to_dict(self) -> dict:
        return {
            "workdir": self.workdir,
            "detected_at": self.detected_at,
            "conventions": [asdict(c) for c in self.conventions],
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ConventionReport:
        return cls(
            workdir=d["workdir"],
            detected_at=d.get("detected_at", 0.0),
            conventions=[Convention(**c) for c in d.get("conventions", [])],
            stats=d.get("stats", {}),
        )

    def format_for_prompt(self) -> str:
        """Section concise à injecter dans le system prompt."""
        if not self.conventions:
            return ""
        lines = ["\n## Conventions du projet (auto-détectées)"]
        for c in self.conventions:
            lines.append(f"- **{c.name}** : {c.value} ({c.evidence})")
        return "\n".join(lines)


# ---------------------------------------------------------------------------- #
# Détecteurs individuels                                                       #
# ---------------------------------------------------------------------------- #


def _iter_source_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(p in _SKIP_DIRS for p in parts[:-1]):
            continue
        yield path


def _collect_stats(root: Path) -> dict:
    """Première passe : un seul walk, on collecte tous les compteurs en une fois."""
    stats = {
        "py_files": 0,
        "js_files": 0,
        "ts_files": 0,
        "test_files": 0,
        "conftest": False,
        "pytest_ini": False,
        "unittest_imports": 0,
        "pytest_imports": 0,
        "async_defs": 0,
        "sync_defs": 0,
        "typed_defs": 0,
        "untyped_defs": 0,
        "class_defs": 0,
        "func_defs": 0,
        "logger_calls": 0,
        "print_calls": 0,
        "frameworks": set(),
        "package_manager": None,
        "ci": [],
        "formatters": [],
    }

    # Files à la racine — signaux forts
    direct = {p.name for p in root.iterdir() if p.exists()}
    if ("pytest.ini" in direct or "pyproject.toml" in direct) and (root / "pytest.ini").exists():
        stats["pytest_ini"] = True
    if "conftest.py" in direct:
        stats["conftest"] = True
    if "pyproject.toml" in direct:
        try:
            text = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
            if "[tool.poetry]" in text or "[tool.poetry.dependencies]" in text:
                stats["package_manager"] = "poetry"
            elif "[project]" in text:
                stats["package_manager"] = "pip+pyproject"
            if "[tool.black]" in text:
                stats["formatters"].append("black")
            if "[tool.ruff]" in text:
                stats["formatters"].append("ruff")
            if "[tool.pytest" in text or "pytest" in text:
                stats["pytest_ini"] = True
        except OSError:
            pass
    elif "requirements.txt" in direct:
        stats["package_manager"] = "pip"
    if "package.json" in direct:
        if (root / "pnpm-lock.yaml").exists():
            stats["package_manager"] = "pnpm"
        elif (root / "yarn.lock").exists():
            stats["package_manager"] = "yarn"
        else:
            stats["package_manager"] = "npm"

    # CI / formatters depuis configs racine
    if (root / ".github" / "workflows").is_dir():
        stats["ci"].append("github-actions")
    if (root / ".gitlab-ci.yml").exists():
        stats["ci"].append("gitlab-ci")
    if (root / ".circleci" / "config.yml").exists():
        stats["ci"].append("circleci")
    if (root / ".prettierrc").exists() or (root / ".prettierrc.json").exists():
        stats["formatters"].append("prettier")
    if (root / ".eslintrc.js").exists() or (root / ".eslintrc.json").exists():
        stats["formatters"].append("eslint")

    # Walk Python/JS/TS pour stats détaillées
    framework_patterns = {
        "FastAPI": [r"\bfrom\s+fastapi\b", r"\bimport\s+fastapi\b"],
        "Flask": [r"\bfrom\s+flask\b", r"\bimport\s+flask\b"],
        "Django": [r"\bfrom\s+django\b", r"\bimport\s+django\b"],
        "Pydantic": [r"\bfrom\s+pydantic\b"],
        "React": [r"\bfrom\s+['\"]react['\"]"],
        "Vue": [r"\bfrom\s+['\"]vue['\"]"],
        "Next.js": [r"\bfrom\s+['\"]next/"],
        "Express": [r"\brequire\(['\"]express['\"]\)", r"\bfrom\s+['\"]express['\"]"],
        "httpx": [r"\bimport\s+httpx\b"],
        "requests": [r"\bimport\s+requests\b"],
        "axios": [r"\bfrom\s+['\"]axios['\"]"],
    }

    for path in _iter_source_files(root):
        suf = path.suffix
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, MemoryError):
            continue

        if suf == ".py":
            stats["py_files"] += 1
            if path.name.startswith("test_") or path.name.endswith("_test.py"):
                stats["test_files"] += 1
            stats["async_defs"] += len(re.findall(r"^\s*async\s+def\s+", src, re.M))
            stats["sync_defs"] += len(re.findall(r"^\s*def\s+", src, re.M))
            stats["typed_defs"] += len(re.findall(r"def\s+\w+\s*\([^)]*:\s*\w", src))
            stats["untyped_defs"] += len(re.findall(r"def\s+\w+\s*\([^)]*\)\s*:", src))
            stats["class_defs"] += len(re.findall(r"^\s*class\s+\w+", src, re.M))
            stats["func_defs"] += len(re.findall(r"^\s*def\s+", src, re.M))
            stats["logger_calls"] += len(re.findall(r"\blogger\.(debug|info|warning|error)", src))
            stats["print_calls"] += len(re.findall(r"\bprint\s*\(", src))
            if "import pytest" in src or "from pytest" in src:
                stats["pytest_imports"] += 1
            if "import unittest" in src or "from unittest" in src:
                stats["unittest_imports"] += 1
        elif suf in (".js", ".jsx"):
            stats["js_files"] += 1
        elif suf in (".ts", ".tsx"):
            stats["ts_files"] += 1

        # Frameworks (sur tous les fichiers texte)
        for name, pats in framework_patterns.items():
            if any(re.search(p, src) for p in pats):
                stats["frameworks"].add(name)

    stats["frameworks"] = sorted(stats["frameworks"])
    return stats


def _detect_test_framework(stats: dict) -> Convention | None:
    if stats["pytest_imports"] > stats["unittest_imports"] and (stats["pytest_imports"] > 0 or stats["pytest_ini"]):
        return Convention(
            name="test_framework",
            value="pytest",
            evidence=f"{stats['pytest_imports']} imports pytest, conftest={stats['conftest']}, ini={stats['pytest_ini']}",
            confidence=0.95 if (stats["pytest_ini"] or stats["conftest"]) else 0.8,
        )
    if stats["unittest_imports"] > 0 and stats["pytest_imports"] == 0:
        return Convention(
            name="test_framework",
            value="unittest",
            evidence=f"{stats['unittest_imports']} imports unittest",
            confidence=0.85,
        )
    return None


def _detect_async_style(stats: dict) -> Convention | None:
    a, s = stats["async_defs"], stats["sync_defs"]
    if a + s < 10:
        return None
    ratio = a / (a + s)
    if ratio > 0.3:
        return Convention(
            name="async_style",
            value="async-heavy",
            evidence=f"{a} async def / {s} sync def ({ratio:.0%} async)",
            confidence=min(0.9, 0.5 + ratio),
        )
    if ratio < 0.05:
        return Convention(
            name="async_style",
            value="sync",
            evidence=f"{s} sync def vs seulement {a} async — préfère du code synchrone",
            confidence=0.85,
        )
    return None


def _detect_type_hints(stats: dict) -> Convention | None:
    typed, untyped = stats["typed_defs"], stats["untyped_defs"]
    total = typed + untyped
    if total < 20:
        return None
    ratio = typed / total
    if ratio > 0.5:
        return Convention(
            name="type_hints",
            value="utilisés",
            evidence=f"{typed}/{total} signatures typées ({ratio:.0%}) — annote tes nouvelles fonctions",
            confidence=min(0.95, 0.4 + ratio),
        )
    if ratio < 0.15:
        return Convention(
            name="type_hints",
            value="rarement utilisés",
            evidence=f"seulement {typed}/{total} signatures typées ({ratio:.0%}) — ne sur-annote pas",
            confidence=0.7,
        )
    return None


def _detect_logging_style(stats: dict) -> Convention | None:
    logger_n, print_n = stats["logger_calls"], stats["print_calls"]
    total = logger_n + print_n
    if total < 5:
        return None
    if logger_n > 3 * max(1, print_n):
        return Convention(
            name="logging",
            value="logger module",
            evidence=f"{logger_n} logger.* vs {print_n} print — utilise logging, pas print",
            confidence=0.9,
        )
    if print_n > 3 * max(1, logger_n):
        return Convention(
            name="logging",
            value="print (style script)",
            evidence=f"{print_n} print vs {logger_n} logger — code style script, print OK",
            confidence=0.75,
        )
    return None


def _detect_frameworks(stats: dict) -> Convention | None:
    fw = stats["frameworks"]
    if not fw:
        return None
    return Convention(
        name="frameworks",
        value=", ".join(fw),
        evidence=f"{len(fw)} framework(s) détectés via imports",
        confidence=0.95,
    )


def _detect_package_manager(stats: dict) -> Convention | None:
    pm = stats["package_manager"]
    if not pm:
        return None
    return Convention(
        name="package_manager",
        value=pm,
        evidence="détecté via lockfile/config racine",
        confidence=0.95,
    )


def _detect_ci(stats: dict) -> Convention | None:
    ci = stats["ci"]
    if not ci:
        return None
    return Convention(
        name="ci",
        value=", ".join(ci),
        evidence="workflows présents",
        confidence=1.0,
    )


def _detect_formatters(stats: dict) -> Convention | None:
    fmts = stats["formatters"]
    if not fmts:
        return None
    return Convention(
        name="formatters",
        value=", ".join(sorted(set(fmts))),
        evidence="config détectée — respecte ces formatters",
        confidence=0.9,
    )


_DETECTORS = (
    _detect_test_framework,
    _detect_async_style,
    _detect_type_hints,
    _detect_logging_style,
    _detect_frameworks,
    _detect_package_manager,
    _detect_ci,
    _detect_formatters,
)


# ---------------------------------------------------------------------------- #
# ConventionDetector (API publique)                                            #
# ---------------------------------------------------------------------------- #


class ConventionDetector:
    """Scanne le workdir et détecte les conventions dominantes."""

    def __init__(self, workdir: Path):
        self.workdir: Path = Path(workdir).resolve()
        self._cache_path: Path = self.workdir / _KLODY_DIR / _CACHE_FILE

    def detect(self, force: bool = False) -> ConventionReport:
        """Détecte les conventions. Utilise le cache si frais et !force."""
        if not force:
            cached = self._load_cache()
            if cached and cached.is_fresh():
                return cached

        stats = _collect_stats(self.workdir)
        conventions: list[Convention] = []
        for det in _DETECTORS:
            try:
                c = det(stats)
                if c is not None:
                    conventions.append(c)
            except Exception as exc:
                logger.debug("Detector %s failed: %s", det.__name__, exc)

        report = ConventionReport(
            workdir=str(self.workdir),
            conventions=conventions,
            stats={k: v for k, v in stats.items() if not isinstance(v, set)},
        )
        self._save_cache(report)
        return report

    def _load_cache(self) -> ConventionReport | None:
        if not self._cache_path.exists():
            return None
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            return ConventionReport.from_dict(data)
        except (OSError, ValueError):
            return None

    def _save_cache(self, report: ConventionReport) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("Could not save conventions cache: %s", exc)
