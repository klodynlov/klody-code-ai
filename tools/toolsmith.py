"""Toolsmithing — Klody ne se contente pas d'utiliser des outils, il en fabrique.

`scaffold_tool(kind, name, …)` génère un artefact **réel et exécutable** dans une
racine autorisée, selon `kind` :

| kind             | produit                                                        |
|------------------|----------------------------------------------------------------|
| `python_script`  | script CLI autonome (argparse) + son test pytest               |
| `cli`            | CLI multi-commandes (sous-commandes argparse) + test            |
| `api`            | app FastAPI (health + endpoint exemple) + test TestClient       |
| `mcp_server`     | serveur MCP FastMCP (1 outil exemple) — comme `klody_mcp/`      |
| `workflow`       | orchestrateur d'étapes séquentielles (steps + runner) + test    |
| `pipeline`       | pipeline ETL (extract → transform → load) + test                |
| `klody_plugin`   | plugin outil Klody (schéma registry + handler) prêt à brancher  |
| `web_interface`  | interface web statique autonome (index.html + JS)               |

Sûreté :
- Destination **confinée aux racines autorisées** (`PROJECT_ROOT` + `ALLOWED_ROOTS`).
- Nom **assaini** en identifiant Python (`slug`) ; refus d'écraser un dossier existant.
- Chaque template Python généré est **valide syntaxiquement** (vérifié par les tests
  via `compile`). Aucun code n'est exécuté par le scaffolder.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from config import build_allowed_roots, match_allowed_root

logger = logging.getLogger(__name__)

_MAX_FILES = 30


class ToolsmithError(Exception):
    """Génération d'outil refusée (kind inconnu, hors sandbox, cible existante…)."""


def _slug(name: str) -> str:
    """Nom → identifiant Python sûr (snake_case, jamais vide)."""
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    if not s or not re.match(r"[a-z_]", s):
        s = f"tool_{s}" if s else "tool"
    return s


def _resolve_target(target_dir: str) -> Path:
    base = Path(target_dir).expanduser() if target_dir.strip() else Path.cwd()
    resolved = base.resolve() if base.is_absolute() else (Path.cwd() / base).resolve()
    roots = build_allowed_roots(Path.cwd())
    if match_allowed_root(resolved, roots) is None:
        raise ToolsmithError(f"Destination hors des racines autorisées : {target_dir} → {resolved}")
    return resolved


# --------------------------------------------------------------------------- #
# Templates — chacun renvoie {chemin_relatif: contenu}                          #
# --------------------------------------------------------------------------- #

def _t_python_script(slug: str, desc: str) -> dict[str, str]:
    return {
        f"{slug}.py": (
            f'"""{desc or slug} — script autonome."""\n'
            "from __future__ import annotations\n\n"
            "import argparse\n\n\n"
            "def run(name: str) -> str:\n"
            '    """Cœur logique — testable sans I/O."""\n'
            '    return f"Bonjour, {name} !"\n\n\n'
            "def main() -> None:\n"
            f'    parser = argparse.ArgumentParser(description="{desc or slug}")\n'
            '    parser.add_argument("name", nargs="?", default="monde")\n'
            "    args = parser.parse_args()\n"
            "    print(run(args.name))\n\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
        f"test_{slug}.py": (
            f"from {slug} import run\n\n\n"
            "def test_run():\n"
            '    assert run("Klody") == "Bonjour, Klody !"\n'
        ),
        "README.md": f"# {slug}\n\n{desc}\n\n```bash\npython {slug}.py <nom>\n```\n",
    }


def _t_cli(slug: str, desc: str) -> dict[str, str]:
    return {
        f"{slug}.py": (
            f'"""{desc or slug} — CLI multi-commandes."""\n'
            "from __future__ import annotations\n\n"
            "import argparse\n\n\n"
            "def cmd_hello(args: argparse.Namespace) -> str:\n"
            '    return f"Bonjour, {args.name} !"\n\n\n'
            "def cmd_add(args: argparse.Namespace) -> str:\n"
            '    return f"{args.a + args.b}"\n\n\n'
            "def build_parser() -> argparse.ArgumentParser:\n"
            f'    parser = argparse.ArgumentParser(prog="{slug}", description="{desc or slug}")\n'
            '    sub = parser.add_subparsers(dest="command", required=True)\n\n'
            '    p_hello = sub.add_parser("hello", help="Salue quelqu\'un")\n'
            '    p_hello.add_argument("name", nargs="?", default="monde")\n'
            "    p_hello.set_defaults(func=cmd_hello)\n\n"
            '    p_add = sub.add_parser("add", help="Additionne deux entiers")\n'
            '    p_add.add_argument("a", type=int)\n'
            '    p_add.add_argument("b", type=int)\n'
            "    p_add.set_defaults(func=cmd_add)\n"
            "    return parser\n\n\n"
            "def main() -> None:\n"
            "    args = build_parser().parse_args()\n"
            "    print(args.func(args))\n\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
        f"test_{slug}.py": (
            f"from {slug} import build_parser\n\n\n"
            "def test_hello():\n"
            '    args = build_parser().parse_args(["hello", "Klody"])\n'
            '    assert args.func(args) == "Bonjour, Klody !"\n\n\n'
            "def test_add():\n"
            '    args = build_parser().parse_args(["add", "2", "3"])\n'
            '    assert args.func(args) == "5"\n'
        ),
        "README.md": f"# {slug}\n\n{desc}\n\n```bash\npython {slug}.py hello Klody\npython {slug}.py add 2 3\n```\n",
    }


def _t_api(slug: str, desc: str) -> dict[str, str]:
    return {
        "main.py": (
            f'"""{desc or slug} — API FastAPI."""\n'
            "from __future__ import annotations\n\n"
            "from fastapi import FastAPI\n"
            "from pydantic import BaseModel\n\n"
            f'app = FastAPI(title="{slug}", description="{desc or slug}")\n\n\n'
            "class EchoIn(BaseModel):\n"
            "    message: str\n\n\n"
            '@app.get("/health")\n'
            "def health() -> dict:\n"
            '    return {"status": "ok"}\n\n\n'
            '@app.post("/echo")\n'
            "def echo(payload: EchoIn) -> dict:\n"
            '    return {"echo": payload.message}\n'
        ),
        "test_main.py": (
            "from fastapi.testclient import TestClient\n"
            "from main import app\n\n"
            "client = TestClient(app)\n\n\n"
            "def test_health():\n"
            '    assert client.get("/health").json() == {"status": "ok"}\n\n\n'
            "def test_echo():\n"
            '    r = client.post("/echo", json={"message": "hi"})\n'
            '    assert r.json() == {"echo": "hi"}\n'
        ),
        "requirements.txt": "fastapi\nuvicorn[standard]\npydantic\n",
        "README.md": f"# {slug}\n\n{desc}\n\n```bash\nuvicorn main:app --reload\n```\n",
    }


def _t_mcp_server(slug: str, desc: str) -> dict[str, str]:
    return {
        f"{slug}_server.py": (
            f'"""{desc or slug} — serveur MCP (FastMCP)."""\n'
            "from __future__ import annotations\n\n"
            "from fastmcp import FastMCP\n\n"
            f'mcp = FastMCP("{slug}")\n\n\n'
            "@mcp.tool()\n"
            "def echo(message: str) -> str:\n"
            '    """Renvoie le message reçu (outil exemple)."""\n'
            "    return message\n\n\n"
            'if __name__ == "__main__":\n'
            "    mcp.run()\n"
        ),
        f"test_{slug}_server.py": (
            f"from {slug}_server import echo\n\n\n"
            "def test_echo():\n"
            '    assert echo("ping") == "ping"\n'
        ),
        "requirements.txt": "fastmcp\n",
        "README.md": (
            f"# {slug} (serveur MCP)\n\n{desc}\n\n"
            "Branche-le dans Klody via `.env` :\n\n"
            "```env\n"
            f'KLODY_MCP_SERVERS={{"{slug}":"http://127.0.0.1:8090/mcp"}}\n'
            "```\n"
        ),
    }


def _t_workflow(slug: str, desc: str) -> dict[str, str]:
    return {
        f"{slug}.py": (
            f'"""{desc or slug} — workflow d\'étapes séquentielles."""\n'
            "from __future__ import annotations\n\n"
            "from collections.abc import Callable\n\n"
            "Step = Callable[[dict], dict]\n\n\n"
            "def step_validate(ctx: dict) -> dict:\n"
            '    if "input" not in ctx:\n'
            '        raise ValueError("contexte sans \'input\'")\n'
            "    return ctx\n\n\n"
            "def step_transform(ctx: dict) -> dict:\n"
            '    ctx["output"] = str(ctx["input"]).upper()\n'
            "    return ctx\n\n\n"
            "STEPS: list[Step] = [step_validate, step_transform]\n\n\n"
            "def run(context: dict) -> dict:\n"
            '    """Exécute les étapes dans l\'ordre, en propageant le contexte."""\n'
            "    ctx = dict(context)\n"
            "    for step in STEPS:\n"
            "        ctx = step(ctx)\n"
            "    return ctx\n\n\n"
            'if __name__ == "__main__":\n'
            '    print(run({"input": "hello"}))\n'
        ),
        f"test_{slug}.py": (
            f"from {slug} import run\n\n\n"
            "def test_run():\n"
            '    assert run({"input": "hi"})["output"] == "HI"\n'
        ),
        "README.md": f"# {slug} (workflow)\n\n{desc}\n\nChaîne d'étapes : validate → transform.\n",
    }


def _t_pipeline(slug: str, desc: str) -> dict[str, str]:
    return {
        f"{slug}.py": (
            f'"""{desc or slug} — pipeline ETL (extract → transform → load)."""\n'
            "from __future__ import annotations\n\n\n"
            "def extract(source: list[dict]) -> list[dict]:\n"
            '    """Étape 1 — récupère les enregistrements bruts."""\n'
            "    return list(source)\n\n\n"
            "def transform(rows: list[dict]) -> list[dict]:\n"
            '    """Étape 2 — nettoie/normalise."""\n'
            '    return [{**r, "value": int(r.get("value", 0)) * 2} for r in rows]\n\n\n'
            "def load(rows: list[dict]) -> int:\n"
            '    """Étape 3 — persiste (ici : renvoie le nombre chargé)."""\n'
            "    return len(rows)\n\n\n"
            "def run(source: list[dict]) -> int:\n"
            "    return load(transform(extract(source)))\n\n\n"
            'if __name__ == "__main__":\n'
            '    print(run([{"value": 1}, {"value": 2}]))\n'
        ),
        f"test_{slug}.py": (
            f"from {slug} import run, transform\n\n\n"
            "def test_transform():\n"
            '    assert transform([{"value": 3}])[0]["value"] == 6\n\n\n'
            "def test_run():\n"
            '    assert run([{"value": 1}, {"value": 2}]) == 2\n'
        ),
        "README.md": f"# {slug} (pipeline)\n\n{desc}\n\nExtract → Transform → Load.\n",
    }


def _t_klody_plugin(slug: str, desc: str) -> dict[str, str]:
    return {
        f"{slug}_plugin.py": (
            f'"""{desc or slug} — plugin outil pour Klody.\n\n'
            "Copie TOOL_SCHEMA dans la liste d'outils exposée au LLM et branche\n"
            "`handler` dans le dispatch de l'orchestrateur (agent/orchestrator.py).\n"
            '"""\n'
            "from __future__ import annotations\n\n"
            "TOOL_SCHEMA: dict = {\n"
            '    "type": "function",\n'
            '    "function": {\n'
            f'        "name": "{slug}",\n'
            f'        "description": "{desc or slug}",\n'
            '        "parameters": {\n'
            '            "type": "object",\n'
            '            "properties": {\n'
            '                "text": {"type": "string", "description": "Texte d\'entrée"},\n'
            "            },\n"
            '            "required": ["text"],\n'
            "        },\n"
            "    },\n"
            "}\n\n\n"
            "def handler(args: dict) -> str:\n"
            '    """Reçoit les arguments validés, renvoie une chaîne pour le LLM."""\n'
            '    return f"{slug} a traité : {args.get(\'text\', \'\')}"\n'.replace("{slug}", slug)
        ),
        f"test_{slug}_plugin.py": (
            f"from {slug}_plugin import TOOL_SCHEMA, handler\n\n\n"
            "def test_schema():\n"
            f'    assert TOOL_SCHEMA["function"]["name"] == "{slug}"\n\n\n'
            "def test_handler():\n"
            '    assert "traité" in handler({"text": "ok"})\n'
        ),
        "README.md": (
            f"# {slug} (plugin Klody)\n\n{desc}\n\n"
            "1. Importe `TOOL_SCHEMA` et ajoute-le à `tools/registry.py`.\n"
            "2. Branche `handler` dans `Orchestrator._build_dispatch`.\n"
        ),
    }


def _t_web_interface(slug: str, desc: str) -> dict[str, str]:
    return {
        "index.html": (
            "<!doctype html>\n"
            '<html lang="fr">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{slug}</title>\n"
            "<style>\n"
            "  body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 40rem; }\n"
            "  button { padding: .5rem 1rem; font-size: 1rem; cursor: pointer; }\n"
            "  #out { margin-top: 1rem; font-weight: 600; }\n"
            "</style>\n</head>\n<body>\n"
            f"<h1>{slug}</h1>\n<p>{desc}</p>\n"
            '<input id="name" placeholder="ton nom" value="Klody">\n'
            '<button id="go">Dire bonjour</button>\n'
            '<div id="out"></div>\n'
            '<script src="app.js"></script>\n'
            "</body>\n</html>\n"
        ),
        "app.js": (
            '"use strict";\n'
            'document.getElementById("go").addEventListener("click", () => {\n'
            '  const name = document.getElementById("name").value || "monde";\n'
            '  document.getElementById("out").textContent = `Bonjour, ${name} !`;\n'
            "});\n"
        ),
        "README.md": f"# {slug} (interface web)\n\n{desc}\n\nOuvre `index.html` dans un navigateur.\n",
    }


_TEMPLATES = {
    "python_script": _t_python_script,
    "cli": _t_cli,
    "api": _t_api,
    "mcp_server": _t_mcp_server,
    "workflow": _t_workflow,
    "pipeline": _t_pipeline,
    "klody_plugin": _t_klody_plugin,
    "web_interface": _t_web_interface,
}


def list_kinds() -> str:
    """Liste les types d'outils que Klody sait fabriquer."""
    descs = {
        "python_script": "script CLI autonome + test",
        "cli": "CLI multi-commandes (sous-commandes) + test",
        "api": "API FastAPI (health + echo) + test",
        "mcp_server": "serveur MCP FastMCP + test",
        "workflow": "orchestrateur d'étapes séquentielles + test",
        "pipeline": "pipeline ETL (extract/transform/load) + test",
        "klody_plugin": "plugin outil Klody (schéma + handler)",
        "web_interface": "interface web statique (HTML + JS)",
    }
    return "Types fabricables :\n" + "\n".join(f"  🔧 {k} — {v}" for k, v in descs.items())


def scaffold_tool(
    kind: str,
    name: str,
    target_dir: str = "",
    description: str = "",
) -> str:
    """Fabrique un nouvel outil et écrit ses fichiers sur le disque.

    Args:
        kind: type d'artefact (voir `list_kinds`).
        name: nom de l'outil (→ dossier + identifiant Python assaini).
        target_dir: dossier parent où créer le dossier de l'outil (racine autorisée ;
            défaut : répertoire courant).
        description: courte description injectée dans les fichiers générés.
    """
    if kind not in _TEMPLATES:
        return (
            f"ERREUR : kind inconnu '{kind}'. "
            + list_kinds()
        )
    if not name or not name.strip():
        return "ERREUR : nom d'outil vide."

    slug = _slug(name)
    try:
        parent = _resolve_target(target_dir)
    except ToolsmithError as e:
        return f"ERREUR SÉCURITÉ : {e}"

    dest = parent / slug
    if dest.exists():
        return f"ERREUR : le dossier existe déjà : {dest}. Choisis un autre nom."

    files = _TEMPLATES[kind](slug, description)
    if len(files) > _MAX_FILES:
        return f"ERREUR : template '{kind}' génère trop de fichiers ({len(files)})."

    dest.mkdir(parents=True)
    written: list[str] = []
    for rel, content in files.items():
        fp = dest / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        written.append(rel)

    logger.info("scaffold_tool : %s '%s' → %s (%d fichiers)", kind, slug, dest, len(written))
    lines = [
        f"✅ Outil '{slug}' fabriqué ({kind}) dans {dest} :",
        *(f"  📄 {r}" for r in sorted(written)),
    ]
    if any(r.startswith("test_") or r == "test_main.py" for r in written):
        lines.append(f"→ Teste-le : `cd {dest} && pytest -q`")
    return "\n".join(lines)
