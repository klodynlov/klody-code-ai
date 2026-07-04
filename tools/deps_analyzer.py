"""Analyse de dépendances multi-écosystèmes (Roadmap v2 #10).

Inventorie les dépendances DÉCLARÉES d'un projet à partir de ses manifestes,
sans réseau ni installation — lecture seule, stdlib uniquement :

- pip       : requirements*.txt, pyproject.toml (PEP 621 + Poetry)
- npm       : package.json (dependencies + devDependencies)
- cargo     : Cargo.toml ([dependencies] + [dev-dependencies])
- go        : go.mod (blocs `require`)
- composer  : composer.json (require + require-dev)

But : donner à Klody une vue d'ensemble fiable avant une migration, un audit ou
une revue — « quelles libs, quelles versions, combien ». Le confinement des
chemins (racines autorisées) est assuré par l'appelant (orchestrator).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Fichiers manifestes reconnus à la racine scannée (hors requirements*.txt, gérés à part).
_MANIFEST_FILES = {
    "pyproject.toml": "pip",
    "package.json": "npm",
    "Cargo.toml": "cargo",
    "go.mod": "go",
    "composer.json": "composer",
}

_MAX_FILE_BYTES = 1_000_000  # garde-fou lecture (un manifeste géant est anormal)
_MAX_DEPS_LISTED = 60         # borne l'affichage par manifeste


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# --- Parsers par écosystème ------------------------------------------------- #


def _parse_requirements(text: str) -> list[str]:
    """requirements.txt : une dépendance par ligne, hors commentaires et options."""
    deps: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):  # -r, -c, -e, --hash… ignorés
            continue
        deps.append(line)
    return deps


def _parse_pyproject(text: str) -> list[str]:
    """pyproject.toml : PEP 621 [project] + Poetry [tool.poetry]."""
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(text)
    except Exception:
        return []
    deps: list[str] = []
    project = data.get("project", {})
    if isinstance(project.get("dependencies"), list):
        deps.extend(str(d) for d in project["dependencies"])
    opt = project.get("optional-dependencies", {})
    if isinstance(opt, dict):
        for group in opt.values():
            if isinstance(group, list):
                deps.extend(str(d) for d in group)
    poetry = data.get("tool", {}).get("poetry", {})
    poetry_deps = poetry.get("dependencies", {})
    if isinstance(poetry_deps, dict):
        for name, spec in poetry_deps.items():
            if name.lower() == "python":
                continue
            deps.append(f"{name} {spec}" if isinstance(spec, str) else name)
    return deps


def _parse_package_json(text: str) -> list[str]:
    """package.json : dependencies + devDependencies → 'name@version'."""
    try:
        data = json.loads(text)
    except Exception:
        return []
    deps: list[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(key, {})
        if isinstance(block, dict):
            deps.extend(f"{n}@{v}" for n, v in block.items())
    return deps


def _parse_cargo(text: str) -> list[str]:
    """Cargo.toml : [dependencies] + [dev-dependencies]."""
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(text)
    except Exception:
        return []
    deps: list[str] = []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        block = data.get(section, {})
        if isinstance(block, dict):
            for name, spec in block.items():
                if isinstance(spec, str):
                    deps.append(f"{name} {spec}")
                elif isinstance(spec, dict) and "version" in spec:
                    deps.append(f"{name} {spec['version']}")
                else:
                    deps.append(name)
    return deps


_GO_REQUIRE_LINE = re.compile(r"^\s*([^\s]+)\s+(v[^\s]+)")


def _parse_go_mod(text: str) -> list[str]:
    """go.mod : lignes `require` simples et blocs `require ( ... )`."""
    deps: list[str] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block:
            if line == ")":
                in_block = False
                continue
            m = _GO_REQUIRE_LINE.match(line)
            if m:
                deps.append(f"{m.group(1)} {m.group(2)}")
        elif line.startswith("require "):
            m = _GO_REQUIRE_LINE.match(line[len("require "):])
            if m:
                deps.append(f"{m.group(1)} {m.group(2)}")
    return deps


def _parse_composer(text: str) -> list[str]:
    """composer.json : require + require-dev → 'name:constraint'."""
    try:
        data = json.loads(text)
    except Exception:
        return []
    deps: list[str] = []
    for key in ("require", "require-dev"):
        block = data.get(key, {})
        if isinstance(block, dict):
            deps.extend(f"{n}:{v}" for n, v in block.items())
    return deps


_PARSERS = {
    "requirements": _parse_requirements,
    "pyproject.toml": _parse_pyproject,
    "package.json": _parse_package_json,
    "Cargo.toml": _parse_cargo,
    "go.mod": _parse_go_mod,
    "composer.json": _parse_composer,
}


# --- Orchestration ---------------------------------------------------------- #


def _analyze_one(path: Path, ecosystem: str, parser_key: str) -> dict | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        deps = _PARSERS[parser_key](text)
    except Exception as exc:  # pragma: no cover - parser défensif
        logger.debug("parse deps échoué %s: %s", path.name, exc)
        return None
    return {"file": path.name, "ecosystem": ecosystem, "count": len(deps), "dependencies": deps}


def analyze_dependencies(target: Path) -> dict:
    """Analyse les dépendances déclarées.

    `target` peut être un RÉPERTOIRE (scan des manifestes à sa racine) ou un
    FICHIER manifeste précis. Retourne un dict structuré { manifests, total,
    ecosystems, root }. Ne descend pas récursivement (les manifestes vivent à la
    racine du projet ; on évite ainsi node_modules/vendor)."""
    target = Path(target)
    manifests: list[dict] = []

    if target.is_file():
        candidates = [target]
        scan_dir = target.parent
    else:
        scan_dir = target
        candidates = []
        for name in _MANIFEST_FILES:
            p = target / name
            if p.is_file():
                candidates.append(p)
        candidates.extend(sorted(target.glob("requirements*.txt")))

    for path in candidates:
        if path.name.startswith("requirements") and path.suffix == ".txt":
            res = _analyze_one(path, "pip", "requirements")
        elif path.name in _MANIFEST_FILES:
            res = _analyze_one(path, _MANIFEST_FILES[path.name], path.name)
        else:
            continue
        if res is not None:
            manifests.append(res)

    ecosystems = sorted({m["ecosystem"] for m in manifests})
    return {
        "root": str(scan_dir),
        "manifests": manifests,
        "total": sum(m["count"] for m in manifests),
        "ecosystems": ecosystems,
    }


def format_dependency_report(result: dict) -> str:
    """Rend l'inventaire lisible pour le LLM."""
    manifests = result.get("manifests", [])
    if not manifests:
        return (
            f"Aucun manifeste de dépendances trouvé sous {result.get('root', '?')}. "
            "Manifestes reconnus : requirements*.txt, pyproject.toml, package.json, "
            "Cargo.toml, go.mod, composer.json."
        )
    lines = [
        f"{result['total']} dépendance(s) déclarée(s) dans {len(manifests)} manifeste(s) "
        f"({', '.join(result['ecosystems'])}) :",
    ]
    for m in manifests:
        lines.append(f"\n**{m['file']}** ({m['ecosystem']}) — {m['count']} dépendance(s)")
        for dep in m["dependencies"][:_MAX_DEPS_LISTED]:
            lines.append(f"  • {dep}")
        if m["count"] > _MAX_DEPS_LISTED:
            lines.append(f"  … +{m['count'] - _MAX_DEPS_LISTED} autres (affichage tronqué)")
    return "\n".join(lines)
