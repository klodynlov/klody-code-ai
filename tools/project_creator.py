"""Création de projets, clonage de dépôts GitHub, et ouverture dans PyCharm."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from config import GITHUB_TOKEN, PROJECTS_DIR, PYCHARM_CMD

logger = logging.getLogger(__name__)

_PYCHARM_PATHS = [
    PYCHARM_CMD,
    "/usr/local/bin/charm",
    "/usr/local/bin/pycharm",
]

_PYCHARM_APP = "/Applications/PyCharm.app"


def _find_pycharm() -> str | None:
    for cmd in _PYCHARM_PATHS:
        if shutil.which(cmd):
            return cmd
    if Path(_PYCHARM_APP).exists():
        return "open -a PyCharm"
    return None


def open_in_pycharm(project_path: str) -> str:
    """Ouvre un dossier dans PyCharm."""
    p = Path(project_path).resolve()
    if not p.exists():
        return f"Dossier introuvable : {p}"

    cmd = _find_pycharm()
    if not cmd:
        return (
            f"PyCharm introuvable. Installez le CLI : PyCharm → Tools → "
            f"Create Command-line Launcher. Dossier prêt : {p}"
        )

    try:
        if cmd.startswith("open -a"):
            subprocess.Popen(["open", "-a", "PyCharm", str(p)])
        else:
            subprocess.Popen([cmd, str(p)])
        return f"✅ PyCharm ouvert sur {p}"
    except Exception as exc:
        logger.error("[project_creator] Erreur PyCharm : %s", exc)
        return f"Erreur ouverture PyCharm : {exc}. Dossier prêt : {p}"


def clone_github_repo(repo_ref: str, target_dir: str = "") -> str:
    """Clone un dépôt GitHub dans PROJECTS_DIR et l'ouvre dans PyCharm."""
    ref = repo_ref.strip().rstrip("/")
    if ref.startswith("https://github.com/"):
        ref = ref.replace("https://github.com/", "")
    parts = ref.split("/")
    if len(parts) < 2:
        return f"Format attendu : owner/repo — reçu : '{repo_ref}'"
    owner, repo = parts[0], parts[1]

    if target_dir:
        dest = Path(target_dir).resolve()
    else:
        dest = PROJECTS_DIR / repo

    if dest.exists():
        return f"Le dossier {dest} existe déjà. Utilisez open_in_pycharm pour l'ouvrir."

    clone_url = f"https://github.com/{owner}/{repo}.git"
    if GITHUB_TOKEN:
        clone_url = f"https://{GITHUB_TOKEN}@github.com/{owner}/{repo}.git"

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "50", clone_url, str(dest)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return f"Erreur git clone : {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return "Timeout lors du clonage (>120s)."
    except FileNotFoundError:
        return "git non trouvé. Installez git."
    except Exception as exc:
        return f"Erreur clonage : {exc}"

    pycharm_result = open_in_pycharm(str(dest))

    return (
        f"✅ {owner}/{repo} cloné dans {dest}\n"
        f"{pycharm_result}"
    )


def create_project(
    name: str,
    template: str = "python",
    description: str = "",
    inspired_by: str = "",
) -> str:
    """Crée un nouveau projet dans PROJECTS_DIR et l'ouvre dans PyCharm.

    Args:
        name: Nom du projet (sera le nom du dossier)
        template: Type de template (python, fastapi, cli, empty)
        description: Description du projet
        inspired_by: owner/repo d'un dépôt GitHub dont on s'inspire
    """
    safe_name = name.strip().replace(" ", "-").lower()
    dest = PROJECTS_DIR / safe_name

    if dest.exists():
        return f"Le dossier {dest} existe déjà."

    dest.mkdir(parents=True, exist_ok=True)

    _TEMPLATES[template](dest, safe_name, description)

    subprocess.run(
        ["git", "init"], cwd=str(dest),
        capture_output=True, text=True,
    )

    info_lines = [
        f"✅ Projet '{safe_name}' créé dans {dest}",
        f"   Template : {template}",
    ]
    if description:
        info_lines.append(f"   Description : {description}")
    if inspired_by:
        info_lines.append(f"   Inspiré de : {inspired_by}")

    pycharm_result = open_in_pycharm(str(dest))
    info_lines.append(pycharm_result)

    return "\n".join(info_lines)


def _template_python(dest: Path, name: str, description: str) -> None:
    src = dest / "src" / name.replace("-", "_")
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(f'"""Module {name}."""\n\n__version__ = "0.1.0"\n')
    (src / "main.py").write_text(
        f'"""{description or name} — point d\'entrée."""\n\n\ndef main():\n    print("Hello from {name}!")\n\n\nif __name__ == "__main__":\n    main()\n'
    )

    tests = dest / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / f"test_{name.replace('-', '_')}.py").write_text(
        f"from src.{name.replace('-', '_')}.main import main\n\n\ndef test_main(capsys):\n    main()\n    assert \"{name}\" in capsys.readouterr().out.lower()\n"
    )

    (dest / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\ndescription = "{description}"\nrequires-python = ">=3.11"\n\n'
        f'[tool.pytest.ini_options]\ntestpaths = ["tests"]\n\n'
        f'[tool.ruff]\nline-length = 100\n'
    )
    (dest / "README.md").write_text(f"# {name}\n\n{description}\n")
    (dest / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.venv/\n*.egg-info/\ndist/\nbuild/\n.env\n"
    )


def _template_fastapi(dest: Path, name: str, description: str) -> None:
    app_dir = dest / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("")
    (app_dir / "main.py").write_text(
        'from fastapi import FastAPI\n\napp = FastAPI(title="{name}")\n\n\n@app.get("/health")\ndef health():\n    return {{"status": "ok"}}\n'.format(name=name)
    )
    (app_dir / "config.py").write_text(
        'import os\nfrom dotenv import load_dotenv\n\nload_dotenv()\n\nDEBUG = os.getenv("DEBUG", "false").lower() == "true"\n'
    )

    tests = dest / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_health.py").write_text(
        "from fastapi.testclient import TestClient\nfrom app.main import app\n\nclient = TestClient(app)\n\n\ndef test_health():\n    r = client.get(\"/health\")\n    assert r.status_code == 200\n"
    )

    (dest / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\ndescription = "{description}"\nrequires-python = ">=3.11"\n'
        f'dependencies = ["fastapi", "uvicorn[standard]", "python-dotenv"]\n\n'
        f'[project.optional-dependencies]\ndev = ["pytest", "httpx", "ruff"]\n\n'
        f'[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    )
    (dest / "README.md").write_text(f"# {name}\n\n{description}\n\n## Lancer\n\n```bash\nuvicorn app.main:app --reload\n```\n")
    (dest / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.venv/\n*.egg-info/\ndist/\nbuild/\n.env\n"
    )
    (dest / ".env.example").write_text("DEBUG=false\n")


def _template_cli(dest: Path, name: str, description: str) -> None:
    module = name.replace("-", "_")
    src = dest / module
    src.mkdir()
    (src / "__init__.py").write_text(f'__version__ = "0.1.0"\n')
    (src / "cli.py").write_text(
        f'"""CLI {name}."""\nimport argparse\n\n\ndef main():\n    parser = argparse.ArgumentParser(description="{description or name}")\n    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")\n    args = parser.parse_args()\n    print("Hello from {name}!")\n\n\nif __name__ == "__main__":\n    main()\n'
    )

    tests = dest / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")

    (dest / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\ndescription = "{description}"\nrequires-python = ">=3.11"\n\n'
        f'[project.scripts]\n{name} = "{module}.cli:main"\n\n'
        f'[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    )
    (dest / "README.md").write_text(f"# {name}\n\n{description}\n")
    (dest / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.venv/\n*.egg-info/\ndist/\nbuild/\n.env\n"
    )


def _template_empty(dest: Path, name: str, description: str) -> None:
    (dest / "README.md").write_text(f"# {name}\n\n{description}\n")
    (dest / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.venv/\n*.egg-info/\ndist/\nbuild/\n.env\n"
    )


_TEMPLATES = {
    "python": _template_python,
    "fastapi": _template_fastapi,
    "cli": _template_cli,
    "empty": _template_empty,
}


def list_templates() -> str:
    """Liste les templates de projet disponibles."""
    lines = ["Templates disponibles :\n"]
    descs = {
        "python": "Projet Python standard (src/, tests/, pyproject.toml)",
        "fastapi": "API FastAPI (app/, tests/, uvicorn, dotenv)",
        "cli": "Outil en ligne de commande (argparse, scripts entry point)",
        "empty": "Projet vide (README + .gitignore)",
    }
    for name, desc in descs.items():
        lines.append(f"  📦 {name} — {desc}")
    return "\n".join(lines)
