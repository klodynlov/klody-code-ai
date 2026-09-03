"""Fonctions et constantes utilitaires de l'orchestrateur.

Extrait de `agent/orchestrator.py` (lot 4.1d). Regroupe les helpers autonomes
qui ne lisent aucun attribut d'instance ni aucun symbole `config.*`
monkeypatchable : coercition de types, détection de blocs code, text-to-action,
boucle de feedback preview, gestion de continuations, et formatage Rich.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from rich.panel import Panel
from rich.text import Text
from rich.tree import Tree

# ------------------------------------------------------------------ #
# Extension → lexer Pygments                                          #
# ------------------------------------------------------------------ #

_EXT_LEXER: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "jsx", ".tsx": "tsx", ".html": "html", ".css": "css",
    ".scss": "scss", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".md": "markdown", ".sh": "bash", ".bash": "bash",
    ".zsh": "bash", ".sql": "sql", ".rs": "rust", ".go": "go",
    ".java": "java", ".c": "c", ".cpp": "cpp", ".h": "c",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
    ".xml": "xml", ".dockerfile": "docker", ".tf": "hcl",
    ".env.example": "bash",
}


def _lexer_for(path: str) -> str:
    ext = Path(path).suffix.lower()
    name = Path(path).name.lower()
    if name == "dockerfile":
        return "docker"
    return _EXT_LEXER.get(ext, "text")


# ------------------------------------------------------------------ #
# Extraction de blocs code markdown + text-to-action                  #
# ------------------------------------------------------------------ #

def _extract_code_blocks(content: str) -> dict[str, list[str]]:
    """Extrait les blocs markdown ```lang ... ``` du content.

    Retourne {lang: [code1, code2, ...]} pour les langs reconnus.
    """
    import re as _re
    blocks: dict[str, list[str]] = {}
    for m in _re.finditer(r"```(\w+)?\n(.*?)\n```", content, _re.DOTALL):
        lang = (m.group(1) or "text").lower()
        code = m.group(2).strip()
        if not code:
            continue
        lang = {"htm": "html", "javascript": "js", "py": "python"}.get(lang, lang)
        blocks.setdefault(lang, []).append(code)
    return blocks


def _infer_action_from_text(content: str, user_input: str) -> dict | None:
    """Si le LLM a répondu en texte avec du code dans des blocs markdown,
    devine quel tool_call appeler avec les paramètres extraits.

    Retourne un dict {"name": str, "args": dict} prêt à être exécuté,
    ou None si rien d'exploitable.
    """
    blocks = _extract_code_blocks(content)
    if not blocks:
        return None

    # 1) Web (HTML/JS/CSS) → preview_code
    has_html = "html" in blocks
    has_js = "js" in blocks
    if has_html or has_js:
        html = blocks.get("html", [""])[0]
        js = blocks.get("js", [""])[0]
        css = blocks.get("css", [""])[0]
        scripts: list[str] = []
        combined = html + " " + js
        if "THREE" in combined or "three.js" in combined.lower():
            scripts.append("https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js")
        if "Chart(" in combined or "chart.js" in combined.lower():
            scripts.append("https://cdn.jsdelivr.net/npm/chart.js")
        if "d3." in combined or "d3.v7" in combined:
            scripts.append("https://d3js.org/d3.v7.min.js")
        return {
            "name": "preview_code",
            "args": {
                "html": html or "<canvas id='c' width='800' height='600'></canvas>",
                "css": css, "js": js,
                "title": (user_input or "Klody Preview")[:40],
                **({"scripts": scripts} if scripts else {}),
            },
        }

    # 2) Python → write_file en script.py
    if "python" in blocks:
        code = blocks["python"][0]
        return {
            "name": "write_file",
            "args": {"path": "script.py", "content": code},
        }

    # 3) Bash/shell → execute_command
    if "bash" in blocks or "shell" in blocks or "sh" in blocks:
        cmd = blocks.get("bash", blocks.get("shell", blocks.get("sh", [""])))[0]
        return {
            "name": "execute_command",
            "args": {"command": cmd, "reason": "extrait depuis bloc markdown"},
        }

    return None


# ------------------------------------------------------------------ #
# Boucle de feedback preview                                           #
# ------------------------------------------------------------------ #

_MAX_PREVIEW_FIX = 2
_PREVIEW_POLL_S = 0.2


def _extract_preview_url(result: str) -> str | None:
    """Extrait l'URL d'un retour preview_code/preview_file (ligne « URL : … »)."""
    m = re.search(r"URL\s*:\s*(\S+)", result or "")
    return m.group(1) if m else None


def _preview_fix_nudge(url: str, errors: list, attempt: int) -> str:
    """Message correctif injecté quand la preview lève des erreurs JS au runtime."""
    filename = url.rsplit("/", 1)[-1]
    lines = []
    for e in errors[:8]:
        loc = f"  → {e.src}" if getattr(e, "src", "") else ""
        lines.append(f"  • [{e.label}] {e.msg}{loc}")
    listing = "\n".join(lines)
    return (
        f"⚠ La preview que tu viens de générer (`{filename}`) lève "
        f"{len(errors)} erreur(s) JS À L'EXÉCUTION dans le navigateur "
        f"(tentative {attempt}/{_MAX_PREVIEW_FIX}) :\n{listing}\n\n"
        "Ces erreurs ne sont PAS visibles dans le source mais cassent la page. "
        "Corrige la cause (souvent un type de nœud/structure non géré, une variable "
        "undefined, un mauvais sélecteur) et régénère la page COMPLÈTE via preview_code. "
        "Ne réponds pas en texte : appelle preview_code avec le code corrigé."
    )


# ------------------------------------------------------------------ #
# Continuations & auto-extensions                                      #
# ------------------------------------------------------------------ #

_CONTINUATION_RE = re.compile(
    r"^(ok(ay)?|oki|d.?accord|ouais?|oui|yep|yes|go|allez|allons?[- ]?y|"
    r"vas[- ]?y|c.?est bon|c.?est fait|c.?est ok|ca marche|ça marche|"
    r"continue[rz]?|poursui[ts]|termine|finis|fais[- ]?(le|ça|ca)|"
    r"envoie|parfait|nickel|super|impec(cable)?|go go|on y va)\b",
    re.IGNORECASE,
)


def _is_continuation(text: str) -> bool:
    """Vrai si `text` est une relance courte de la tâche en cours (pas une
    nouvelle demande). Sert au routeur à ne pas rétrograder en `easy`."""
    t = text.strip()
    if not t or len(t) > 40:
        return False
    return bool(_CONTINUATION_RE.match(t))


_MAX_AUTO_EXTENSIONS = 3
_AUTO_EXTENSION_SIZE = 8

# ------------------------------------------------------------------ #
# Outils producteurs (anti-boucle)                                     #
# ------------------------------------------------------------------ #

_PRODUCING_TOOLS = frozenset({
    "write_file", "preview_code", "preview_file", "run_in_sandbox",
    "create_project", "clone_github_repo",
    "generate_excel", "generate_text_file", "bundle_zip", "import_llm_export",
    "scaffold_tool", "backup_directory", "batch_rename", "organize_directory",
    "sync_directories",
    "mcp__reaper__insert_midi_note", "mcp__reaper__insert_midi_notes",
})

# ------------------------------------------------------------------ #
# Coercition de types d'arguments d'outils                             #
# ------------------------------------------------------------------ #


def _as_bool(v: object) -> bool:
    """Coerce un argument d'outil en booléen, robuste aux modèles locaux qui
    sérialisent les bools en CHAÎNE ('true'/'false') — `bool('false')` vaut True,
    d'où ce garde-fou (cf. _normalize_ask_user_options, même classe de bug)."""
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _coerce_bool_arg(value, default: bool = True) -> bool:
    """Convertit un argument d'outil en booléen, en tolérant les chaînes.

    `bool("false")` vaut True en Python : un modèle qui passe `"false"` (chaîne)
    plutôt que le booléen JSON inverserait silencieusement l'intention."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "non", "")
    return bool(value)


def _normalize_ask_user_options(raw) -> list[str]:
    """Normalise le paramètre `options` d'ask_user en vraie liste de chaînes.

    Certains modèles (Qwen-Coder notamment) sérialisent le tableau en CHAÎNE
    JSON — `'["a","b"]'` — au lieu d'une vraie liste. Itérer cette chaîne
    donnerait des caractères isolés → carte aux boutons illisibles (« ça
    bloque », cf. sessions 04:46/04:50). On récupère donc la liste réelle :
    JSON d'abord, sinon découpe par lignes, sinon option unique."""
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
        except (ValueError, TypeError):
            parsed = s.splitlines() if "\n" in s else [s]
        raw = parsed if isinstance(parsed, list) else [str(parsed)]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(o).strip() for o in raw if str(o).strip()]


# ------------------------------------------------------------------ #
# Prompt SLIM pour le modèle coder                                     #
# ------------------------------------------------------------------ #

_CODER_SLIM_PROMPT = (
    "Tu es un générateur de code expert. Réponds en français, très concis.\n\n"
    "Quand on te demande une page web, une visualisation ou une animation : "
    "génère le code COMPLET et AUTONOME dans UN SEUL bloc ```html (DOCTYPE + "
    "HTML + <style> + <script> inclus, directement ouvrable au navigateur). "
    "TOUT le JavaScript doit être écrit — jamais de coquille vide, jamais de "
    "placeholder « // à compléter ». Si tu utilises une lib externe (Three.js, "
    "Chart.js, d3…), ajoute son <script src=…CDN…>.\n\n"
    "Pour du code non-web : réponds avec le code complet dans un bloc "
    "```<langage>. Le code d'abord, explication minimale."
)

# ------------------------------------------------------------------ #
# Formatage Rich                                                       #
# ------------------------------------------------------------------ #

def _format_file_tree(listing: str, root: str) -> Tree:
    """Convertit la sortie texte de list_files en Rich Tree."""
    tree = Tree(f"[bold blue]📁 {root}[/bold blue]")
    for line in listing.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("📁"):
            name = line.replace("📁", "").strip().rstrip("/")
            tree.add(f"[blue]📁 {name}/[/blue]")
        elif line.startswith("📄"):
            parts = line.replace("📄", "").strip().rsplit("  ", 1)
            name = parts[0].strip()
            size = parts[1].strip() if len(parts) > 1 else ""
            tree.add(f"[white]📄 {name}[/white] [dim]{size}[/dim]")
    return tree


def _format_search_results(result: str, pattern: str) -> Panel:
    """Affiche les résultats de recherche avec le pattern surligné."""
    if result.startswith("ERREUR") or result.startswith("Aucun"):
        return Panel(
            f"[yellow]{result}[/yellow]",
            title="[yellow]search_in_files[/yellow]",
            border_style="yellow",
        )
    lines = []
    for line in result.splitlines()[:50]:
        parts = line.split(":", 2)
        if len(parts) >= 3:
            file_part = f"[dim]{parts[0]}[/dim]"
            line_part = f"[cyan]{parts[1]}[/cyan]"
            content = parts[2].replace(pattern, f"[bold yellow]{pattern}[/bold yellow]")
            lines.append(f"{file_part}:[dim]{line_part}[/dim]: {content}")
        else:
            lines.append(line)
    text = Text.from_markup("\n".join(lines))
    return Panel(text, title=f"[green]🔍 Résultats: {pattern}[/green]", border_style="green")
