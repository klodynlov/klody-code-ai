import json
import logging
import re
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.tree import Tree


def _has_markdown_safe(text: str) -> bool:
    """Détection minimale de markdown (évite l'import circulaire avec llm._has_markdown)."""
    markers = ("```", "**", "##", "# ", "- ", "* ", "> ", "| ")
    return any(m in text for m in markers)

from agent.llm import LLMClient
from agent.memory import ConversationMemory
from agent.long_term_memory import get_long_term_memory
from agent.prompts import compose_system_prompt
from tools.file_manager import FileManager, SandboxViolation
from tools.registry import get_tools
from tools.search import Search
from tools.skills import save_skill, load_skills, list_skills, delete_skill, format_skills_for_prompt
from tools.llm_import import import_llm_export, list_imports
from tools.mcp_client import search_books as mcp_search_books, get_skills as mcp_get_skills, learn_from_books as mcp_learn
from tools.github_reader import (
    browse_repo as gh_browse_repo,
    read_github_file as gh_read_file,
    list_indexed_repos as gh_list_indexed,
    index_github_repo as gh_index_repo,
    extract_best_practices as gh_extract_practices,
)
from tools.project_creator import (
    clone_github_repo as pc_clone,
    create_project as pc_create,
    open_in_pycharm as pc_open_pycharm,
)
from tools.preview import (
    preview_code as pv_preview_code,
    preview_file as pv_preview_file,
    list_previews as pv_list_previews,
    stop_preview_server as pv_stop_server,
)
from tools.terminal import CommandBlocked, Terminal
from agent.profiler import get_profiler
from agent.memory_extractor import extract_mid_session
from config import (
    MAX_ITERATIONS, PROJECT_ROOT, SANDBOX_AUTO_EXEC, SANDBOX_TIMEOUT,
    ROUTER_ENABLED, BEST_OF_N_ENABLED, BEST_OF_N_COUNT, BEST_OF_N_FORCE,
    match_allowed_root,
)

logger = logging.getLogger(__name__)
console = Console()

# Extension → lexer Pygments
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


def _extract_code_blocks(content: str) -> dict[str, list[str]]:
    """Extrait les blocs markdown ```lang ... ``` du content.

    Retourne {lang: [code1, code2, ...]} pour les langs reconnus.
    """
    import re as _re
    blocks: dict[str, list[str]] = {}
    # ```lang\n...\n``` (lang optionnel, défaut text)
    for m in _re.finditer(r"```(\w+)?\n(.*?)\n```", content, _re.DOTALL):
        lang = (m.group(1) or "text").lower()
        code = m.group(2).strip()
        if not code:
            continue
        # Normalise quelques alias
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
        # Si HTML complet (avec <!DOCTYPE/<html), garde tel quel
        # Détecte les CDN nécessaires (Three.js, Chart.js, p5…)
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


def _looks_like_unfinished_plan(content: str | None) -> bool:
    """Détecte un message qui annonce un plan ou des intentions sans agir.

    Patterns courants observés sur Qwen3-Coder en mode hard/feature avec T basse :
    - "Voici mon plan : 1. … 2. … Commençons par X :"
    - "Je vais créer Y. Tout d'abord :"
    - "Je vais X. Je vais Y. Je vais d'abord Z." (≥2 intentions sans action)
    - Finir par ":" / "…" / "..." après une énumération
    """
    if not content:
        return False
    stripped = content.strip()
    if len(stripped) < 30:
        return False
    lower = stripped.lower()

    # 1) Finit par marqueur d'incomplétude
    ends_open = stripped[-1:] in (":", "…") or stripped.endswith("...")

    # 2) Phrases d'intention future (le LLM annonce ce qu'il VA faire)
    intent_patterns = (
        "je vais", "i will", "i'll ", "let's", "commençons par",
        "tout d'abord", "first,", "step 1", "étape 1",
        "je commence", "je vais d'abord", "je vais ensuite",
        "je vais maintenant", "voici mon plan", "let me start",
    )
    intent_count = sum(lower.count(p) for p in intent_patterns)

    # 3) Énumération markdown ou liste indentée
    has_enumeration = (
        "\n1." in content or "\n1)" in content or
        content.lstrip().startswith("1.") or
        content.count("\n    ") >= 2 or       # liste indentée 4 espaces
        content.count("\n- ") >= 2            # liste à tirets
    )

    # Triggers (en OR — il suffit qu'un seul soit vrai pour déclencher) :
    # - Finit ouvert avec ≥1 intention OU énumération
    # - OU ≥2 intentions futures distinctes (pattern "Je vais X. Je vais Y.")
    # - OU énumération + ≥1 intention
    if ends_open and (intent_count >= 1 or has_enumeration):
        return True
    if intent_count >= 2:
        return True
    if has_enumeration and intent_count >= 1:
        return True
    return False


# Messages courts qui poursuivent la tâche en cours plutôt que d'en lancer une
# nouvelle ("ok", "vas-y", "c'est bon ?", "continue"…). Le `.?` tolère les
# variantes d'apostrophe/accent (c'est / cest / c est).
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


# Auto-continue : quand le budget d'itérations est épuisé alors que l'agent
# travaille encore (des tools ont été appelés dans la passe), on prolonge le
# budget au lieu d'arrêter et de forcer l'utilisateur à relancer. Borné pour
# éviter l'emballement sur une mauvaise piste.
_MAX_AUTO_EXTENSIONS = 3
_AUTO_EXTENSION_SIZE = 8


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
    # Colorer les numéros de ligne
    lines = []
    for line in result.splitlines()[:50]:
        # format : fichier:ligne:contenu
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


class Orchestrator:
    def __init__(self, memory: ConversationMemory):
        self.memory = memory
        self.llm = LLMClient()
        self.file_manager = FileManager()
        self.terminal = Terminal()
        self.search = Search()
        self.tools = get_tools()
        # Client MCP : découvre les outils des serveurs MCP externes configurés
        # (KLODY_MCP_SERVERS) et les ajoute aux outils exposés au LLM, sous des
        # noms namespacés mcp__<serveur>__<outil>. Résilient : un serveur
        # injoignable est simplement ignoré, ça ne casse jamais le démarrage.
        self.mcp = None
        try:
            from config import MCP_SERVERS
            if MCP_SERVERS:
                from tools.mcp_bridge import MCPManager
                self.mcp = MCPManager(MCP_SERVERS)
                mcp_tools = self.mcp.discover()
                if mcp_tools:
                    self.tools = [*self.tools, *mcp_tools]
                    logger.info("[MCP] %d outil(s) externe(s) ajouté(s)", len(mcp_tools))
        except Exception as exc:
            logger.warning("[MCP] initialisation client échouée : %s", exc)
            self.mcp = None
        self.lt_memory = get_long_term_memory()
        self.profiler = get_profiler()
        # Sandbox jetable — un venv par racine autorisée, créé paresseusement
        # et mis en cache (clé = racine résolue). Permet de tester/exécuter du
        # code dans n'importe quelle racine de ALLOWED_ROOTS, pas seulement
        # PROJECT_ROOT.
        self._sandbox_cache: dict = {}
        self._sandbox_auto_exec = SANDBOX_AUTO_EXEC
        self._sandbox_timeout = SANDBOX_TIMEOUT
        # Router adaptatif — classifie chaque prompt avant la boucle ReAct.
        self._router_enabled = ROUTER_ENABLED
        self._router = None  # lazy
        self.last_routing = None  # dernière décision, pour debug et étapes futures
        # Retrieval code-aware (Roadmap v2 #6) — tree-sitter + embeddings.
        self._code_index = None      # symboles + références (tree-sitter)
        self._embed_index = None     # recherche sémantique (bge-m3)
        # Best-of-N (Roadmap v2 #7) — N candidats + reranker LLM-as-judge.
        self._best_of_n = None
        self._best_of_n_enabled = BEST_OF_N_ENABLED
        self._best_of_n_count = BEST_OF_N_COUNT
        self._best_of_n_force = BEST_OF_N_FORCE
        self._current_user_prompt = ""
        # Memory utile (Roadmap v2 #8) — détection conventions projet + erreurs récurrentes.
        self._conventions = None
        self._error_memory = None
        # Anti-stall : 1 nudge max par run() pour débloquer un plan annoncé sans action.
        self._anti_stall_fired = False
        self._anti_stall_iter = -1
        # Text-to-action : 1 fallback max par run() pour extraire un tool_call
        # depuis un content texte+markdown si le LLM refuse d'appeler tool natifs.
        self._t2a_fired = False

        if not any(m["role"] == "system" for m in memory.messages):
            self._inject_system_prompt()

    def _sandbox_for(self, root):
        """SandboxRunner (venv jetable) pour une racine donnée, mis en cache.

        Permet d'exécuter/tester du code dans n'importe quelle racine autorisée.
        SandboxRunner cache déjà son venv par hash du chemin → coût minimal.
        """
        root = Path(root).resolve()
        if root not in self._sandbox_cache:
            from tools.sandbox import SandboxRunner
            self._sandbox_cache[root] = SandboxRunner(root)
        return self._sandbox_cache[root]

    @property
    def sandbox(self):
        """SandboxRunner du PROJECT_ROOT courant (défaut). Voir _sandbox_for
        pour le multi-racines."""
        return self._sandbox_for(self.file_manager.root)

    @property
    def router(self):
        """Router adaptatif (lazy init, partage le même LLM backend)."""
        if self._router is None:
            from agent.router import Router
            self._router = Router()
        return self._router

    @property
    def code_index(self):
        """Index tree-sitter symboles + références (lazy)."""
        if self._code_index is None:
            from tools.code_index import CodeIndex
            self._code_index = CodeIndex(self.file_manager.root)
        return self._code_index

    @property
    def embed_index(self):
        """Index embeddings bge-m3 pour recherche sémantique (lazy)."""
        if self._embed_index is None:
            from tools.code_search import EmbeddingIndex
            self._embed_index = EmbeddingIndex(self.file_manager.root)
        return self._embed_index

    @property
    def best_of_n(self):
        """BestOfN engine (lazy)."""
        if self._best_of_n is None:
            from agent.best_of_n import BestOfN
            self._best_of_n = BestOfN(self.llm, n=self._best_of_n_count)
        return self._best_of_n

    @property
    def conventions(self):
        """Détecteur de conventions projet (lazy)."""
        if self._conventions is None:
            from agent.conventions import ConventionDetector
            self._conventions = ConventionDetector(self.file_manager.root)
        return self._conventions

    @property
    def error_memory(self):
        """Mémoire des erreurs récurrentes sandbox (lazy)."""
        if self._error_memory is None:
            from agent.error_memory import ErrorMemory
            self._error_memory = ErrorMemory(workdir=self.file_manager.root)
        return self._error_memory

    def _inject_system_prompt(self, task_type: str | None = None) -> None:
        """Injecte (ou met à jour) le system prompt en mémoire.

        Si task_type est fourni, utilise le prompt focalisé correspondant
        (Roadmap v2 #5). Sinon, utilise le fallback `default.md`.
        """
        base_prompt = compose_system_prompt(task_type)
        skills = load_skills()
        skills_section = format_skills_for_prompt(skills) if skills else ""
        lt_section = self.lt_memory.format_for_prompt()
        profile_section = self.profiler.get_profile_for_prompt()
        # Conventions auto-détectées + erreurs récurrentes (Roadmap v2 #8)
        conv_section = ""
        err_section = ""
        try:
            conv_section = self.conventions.detect().format_for_prompt()
        except Exception as exc:
            logger.debug("Convention detection skipped: %s", exc)
        try:
            err_section = self.error_memory.format_for_prompt()
        except Exception as exc:
            logger.debug("Error memory format skipped: %s", exc)
        content = (
            f"{base_prompt}\n\n"
            f"Dossier projet actif: {PROJECT_ROOT}"
            f"{skills_section}"
            f"{lt_section}"
            f"{profile_section}"
            f"{conv_section}"
            f"{err_section}"
        )

        # Si un system message existe déjà → on le remplace (hot-swap).
        # Sinon → on l'insère en tête.
        message = {"role": "system", "content": content, "timestamp": None}
        if self.memory.messages and self.memory.messages[0].get("role") == "system":
            self.memory.messages[0] = message
        else:
            self.memory.messages.insert(0, message)

    # ------------------------------------------------------------------ #
    # Routing + affichage intelligent des outils                          #
    # ------------------------------------------------------------------ #

    def _execute_and_display(self, tool_name: str, tool_args: dict) -> str:
        """Exécute un outil et affiche le résultat avec un rendu adapté.

        Bonus : après un write_file réussi sur un .py, lance un auto-check
        sandbox (pytest si tests, python si main, py_compile sinon) et
        injecte le résultat à la suite — l'agent voit immédiatement si
        son code parse / passe les tests.
        """
        result = self._execute_tool(tool_name, tool_args)
        self._display_tool_result(tool_name, tool_args, result)

        # Auto-check sandbox après write_file (Roadmap v2 #3)
        if (
            tool_name == "write_file"
            and not result.startswith("ERREUR")
            and getattr(self, "_sandbox_auto_exec", True)
        ):
            extra = self._auto_sandbox_check(tool_args.get("path", ""))
            if extra:
                result = f"{result}\n\n{extra}"
            # Auto-preview : si c'est un .html, ouvre dans le navigateur via preview_file
            preview_extra = self._auto_preview_check(tool_args.get("path", ""))
            if preview_extra:
                result = f"{result}\n\n{preview_extra}"

        return result

    def _auto_preview_check(self, rel_path: str) -> str:
        """Si rel_path est un .html écrit dans le projet, lance preview_file
        automatiquement pour que l'utilisateur voie le résultat dans le navigateur.

        Émis comme un tool_call visible dans le chat (l'orchestrator passe par
        execute_with_events qui dispatche l'event UI).
        """
        if not rel_path or not rel_path.lower().endswith((".html", ".htm")):
            return ""
        try:
            # Réutilise le pipeline d'exécution standard pour que l'event UI
            # `tool_call` + `tool_result` soit émis comme un tool normal.
            res = self._execute_tool("preview_file", {"path": rel_path})
            self._display_tool_result("preview_file", {"path": rel_path}, res)
            return f"[auto-preview] preview_file lancé sur {rel_path}\n{res[:200]}"
        except Exception as exc:
            logger.debug("Auto-preview failed: %s", exc)
            return ""

    def _resolve_sandbox_target(self, path: str):
        """Pour un fichier écrit (chemin relatif ou absolu), retourne le tuple
        (sandbox, rel_cmd, root) permettant de lancer l'auto-check dans la
        BONNE racine autorisée, ou None si aucun check n'est pertinent.

        Multi-racines : le fichier peut être sous n'importe quelle racine de
        ALLOWED_ROOTS — on exécute alors dans le venv de CETTE racine, avec un
        chemin relatif à elle. Plus de skip silencieux hors PROJECT_ROOT.
        Partagé entre le CLI (_auto_sandbox_check) et l'API WebSocket.
        """
        if not path:
            return None
        from tools.sandbox import auto_command_for

        p = Path(path).expanduser()
        full_path = p.resolve() if p.is_absolute() else (self.file_manager.root / p).resolve()
        root = match_allowed_root(full_path, self.file_manager.allowed_roots)
        if root is None:
            return None  # hors racines (ne devrait pas arriver après write_file)

        cmd = auto_command_for(full_path)
        if cmd is None:
            return None

        rel = str(full_path.relative_to(root))
        rel_cmd = [c if c != full_path.name else rel for c in cmd]
        return self._sandbox_for(root), rel_cmd, root

    def _auto_sandbox_check(self, rel_path: str) -> str:
        """Si rel_path est un .py exécutable, lance la commande la plus
        pertinente dans le sandbox de sa racine et retourne le rapport formaté."""
        target = self._resolve_sandbox_target(rel_path)
        if target is None:
            return ""
        sandbox, rel_cmd, _root = target

        result = sandbox.run(rel_cmd, timeout=self._sandbox_timeout)
        # Mémorise les erreurs récurrentes (Roadmap v2 #8)
        if not result.success and result.stderr:
            try:
                self.error_memory.record(result.stderr, command=" ".join(rel_cmd))
            except Exception as exc:
                logger.debug("Error memory record skipped: %s", exc)
        report = result.format_for_llm()
        self._display_sandbox_result(report, result.success)
        return f"[sandbox auto-check]\n{report}"

    def _run_best_of_n(self, messages: list[dict]) -> tuple[str, list[dict] | None]:
        """Génère N candidats, sélectionne le meilleur, affiche le résultat retenu.

        Retourne (content, tool_calls) du candidat gagnant — drop-in remplacement
        pour stream_chat() côté boucle ReAct.
        """
        n = self._best_of_n_count
        console.print(Panel(
            f"[bold magenta]🎲 Best-of-{n}[/bold magenta] — génération de {n} candidats…",
            border_style="magenta",
            padding=(0, 1),
        ))
        winner, all_cands, reasoning = self.best_of_n.best(
            messages,
            tools=self.tools,
            user_prompt=self._current_user_prompt,
        )

        # Récap des candidats + choix
        recap_lines = []
        for c in all_cands:
            marker = "[bold green]→[/bold green]" if c.idx == winner.idx else "  "
            tools_str = ""
            if c.tool_calls:
                tools_str = " · tools: " + ", ".join(
                    tc["function"]["name"] for tc in c.tool_calls
                )
            text_preview = c.content.strip()[:50].replace("\n", " ") or "(no text)"
            recap_lines.append(
                f"{marker} [{c.idx + 1}] T={c.temperature:.1f} ({c.latency_s}s){tools_str}  «{text_preview}»"
            )
        recap_lines.append("")
        recap_lines.append(f"[dim]Reranker: {reasoning}[/dim]")
        console.print(Panel(
            "\n".join(recap_lines),
            title=f"[magenta]🏆 Candidat retenu : [{winner.idx + 1}][/magenta]",
            border_style="magenta",
            padding=(0, 1),
        ))

        # Re-display du candidat retenu comme s'il venait de stream_chat
        if winner.content:
            if _has_markdown_safe(winner.content):
                console.print(Markdown(winner.content))
            else:
                console.print(winner.content, markup=False, highlight=False)

        return winner.content, winner.tool_calls

    def _display_routing(self, decision, max_iter: int) -> None:
        """Affiche la décision du router en panneau discret."""
        color = {"easy": "green", "medium": "cyan", "hard": "magenta"}.get(
            decision.difficulty, "white"
        )
        flags = []
        if decision.use_planner:
            flags.append("planner")
        if decision.use_best_of_n:
            flags.append("best-of-N")
        flags_str = f"  · {' · '.join(flags)}" if flags else ""
        body = (
            f"[bold {color}]{decision.difficulty}[/bold {color}] "
            f"· [bold]{decision.task_type}[/bold] "
            f"· max_iter={max_iter}{flags_str}\n"
            f"[dim]{decision.reasoning}[/dim]"
        )
        console.print(Panel(
            body,
            title="[dim]🎯 Router[/dim]",
            border_style="dim",
            padding=(0, 1),
        ))

    def _display_sandbox_result(self, report: str, success: bool) -> None:
        """Affiche le résultat sandbox dans un panneau coloré."""
        style = "green" if success else "yellow"
        title = "🧪 Sandbox auto-check" if success else "🧪 Sandbox auto-check (échec)"
        # Tronquer pour l'affichage console (le LLM voit la version complète)
        body = report
        if len(body) > 600:
            body = body[:300] + "\n[...]\n" + body[-280:]
        console.print(Panel(body, title=f"[{style}]{title}[/{style}]", border_style=style))

    def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        logger.info("Outil: %s | Args: %s", tool_name, tool_args)
        try:
            if tool_name == "read_file":
                return self.file_manager.read_file(tool_args["path"])
            if tool_name == "write_file":
                return self.file_manager.write_file(tool_args["path"], tool_args["content"])
            if tool_name == "list_files":
                return self.file_manager.list_files(
                    tool_args.get("path", "."), tool_args.get("recursive", False)
                )
            if tool_name == "execute_command":
                return self.terminal.execute_command(
                    tool_args["command"], tool_args.get("reason", "")
                )
            if tool_name == "search_in_files":
                return self.search.search_in_files(
                    tool_args["pattern"],
                    tool_args.get("path", "."),
                    tool_args.get("file_pattern", ""),
                    tool_args.get("case_sensitive", True),
                )
            if tool_name == "run_in_sandbox":
                sandbox = self.sandbox
                workdir = tool_args.get("workdir", "") or ""
                if workdir.strip():
                    p = Path(workdir).expanduser()
                    wd = p.resolve() if p.is_absolute() else (self.file_manager.root / p).resolve()
                    if match_allowed_root(wd, self.file_manager.allowed_roots) is None:
                        return f"ERREUR SÉCURITÉ: workdir hors des racines autorisées: {workdir}"
                    sandbox = self._sandbox_for(wd)
                res = sandbox.run(
                    tool_args["command"],
                    timeout=int(tool_args.get("timeout", 30)),
                )
                return res.format_for_llm()
            if tool_name == "find_symbol":
                from tools.code_index import format_symbols
                syms = self.code_index.find_symbol(tool_args["name"])
                return format_symbols(syms)
            if tool_name == "find_references":
                from tools.code_index import format_references
                refs = self.code_index.find_references(tool_args["name"])
                return format_references(refs)
            if tool_name == "find_relevant_files":
                from tools.code_search import format_hits
                hits = self.embed_index.search(
                    tool_args["query"],
                    k=int(tool_args.get("k", 5)),
                )
                if not hits and not self.embed_index.is_available():
                    return ("Recherche sémantique indisponible : Ollama ou "
                            "bge-m3 introuvable. Utilise find_symbol ou search_in_files.")
                return format_hits(hits)
            if tool_name == "list_skills":
                return list_skills()
            if tool_name == "delete_skill":
                return delete_skill(tool_args["slug"])
            if tool_name == "save_skill":
                return save_skill(
                    tool_args["name"],
                    tool_args["description"],
                    tool_args["content"],
                )
            if tool_name == "import_llm_export":
                return import_llm_export(tool_args["path"])
            if tool_name == "list_imports":
                return list_imports()
            if tool_name == "search_books":
                return mcp_search_books(
                    tool_args["query"], tool_args.get("limit", 3)
                )
            if tool_name == "get_skills":
                return mcp_get_skills(tool_args["domain"])
            if tool_name == "learn_from_books":
                return mcp_learn(
                    tool_args["topic"],
                    tool_args.get("skill_name", ""),
                )
            if tool_name == "remember_fact":
                return self.lt_memory.remember(
                    tool_args["key"],
                    tool_args["content"],
                    tool_args.get("category", "context"),
                )
            if tool_name == "forget_fact":
                return self.lt_memory.forget(tool_args["key"])
            if tool_name == "browse_repo":
                return gh_browse_repo(
                    tool_args["repo"],
                    tool_args.get("path", ""),
                    tool_args.get("recursive", False),
                )
            if tool_name == "read_github_file":
                return gh_read_file(tool_args["repo"], tool_args["path"])
            if tool_name == "list_indexed_repos":
                return gh_list_indexed()
            if tool_name == "index_github_repo":
                return gh_index_repo(tool_args["repo"])
            if tool_name == "extract_best_practices":
                return gh_extract_practices(tool_args["repo"])
            if tool_name == "clone_github_repo":
                return pc_clone(
                    tool_args["repo"], tool_args.get("target_dir", "")
                )
            if tool_name == "create_project":
                return pc_create(
                    tool_args["name"],
                    tool_args.get("template", "python"),
                    tool_args.get("description", ""),
                    tool_args.get("inspired_by", ""),
                )
            if tool_name == "open_in_pycharm":
                return pc_open_pycharm(tool_args["project_path"])
            if tool_name == "preview_code":
                return pv_preview_code(
                    tool_args["html"],
                    tool_args.get("css", ""),
                    tool_args.get("js", ""),
                    tool_args.get("title", "Preview"),
                    tool_args.get("scripts"),
                    tool_args.get("styles"),
                )
            if tool_name == "preview_file":
                return pv_preview_file(tool_args["path"])
            if tool_name == "list_previews":
                return pv_list_previews()
            if tool_name == "stop_preview_server":
                return pv_stop_server()
            # ─ Audio (tools/audio.py) ─────────────────────────────────────
            if tool_name in (
                "analyze_audio", "edit_wav", "mix_stems",
                "generate_silence", "convert_format", "get_waveform_data",
            ):
                from tools import audio as _audio
                fn = getattr(_audio, tool_name)
                return json.dumps(fn(**tool_args), ensure_ascii=False, indent=2)
            # ─ Outils MCP externes (mcp__<serveur>__<outil>) ──────────────
            if getattr(self, "mcp", None) is not None and self.mcp.owns(tool_name):
                return self.mcp.call(tool_name, tool_args)
            return f"ERREUR: Outil inconnu '{tool_name}'"

        except SandboxViolation as e:
            return f"ERREUR SÉCURITÉ: {e}"
        except CommandBlocked as e:
            return f"ERREUR SÉCURITÉ: {e}"
        except FileNotFoundError as e:
            return f"ERREUR: Fichier introuvable — {e}"
        except Exception as e:
            logger.error("Erreur dans %s: %s", tool_name, e, exc_info=True)
            return f"ERREUR: {e}"

    def _display_tool_result(self, tool_name: str, tool_args: dict, result: str) -> None:
        """Rendu adapté selon le type d'outil."""

        if tool_name == "read_file":
            path = tool_args.get("path", "")
            lexer = _lexer_for(path)
            console.print(Panel(
                Syntax(result, lexer, theme="monokai", line_numbers=True, word_wrap=True),
                title=f"[cyan]📄 {path}[/cyan]",
                border_style="cyan",
                padding=(0, 1),
            ))

        elif tool_name == "write_file":
            path = tool_args.get("path", "")
            status = "créé" if "créé" in result else "modifié"
            icon = "✨" if status == "créé" else "✏️"
            console.print(
                f"\n  [green]{icon}  {status.capitalize()} :[/green] [bold]{path}[/bold]"
            )
            if "\n\n" in result:
                diff_part = result.split("\n\n", 1)[1]
                console.print(Panel(
                    Syntax(diff_part, "diff", theme="monokai", word_wrap=True),
                    title="[dim]Diff[/dim]",
                    border_style="dim",
                    padding=(0, 1),
                ))
            else:
                console.print()

        elif tool_name == "list_files":
            path = tool_args.get("path", ".")
            if result.startswith("ERREUR") or "vide" in result.lower():
                console.print(Panel(f"[yellow]{result}[/yellow]", border_style="yellow"))
            else:
                tree = _format_file_tree(result, str(PROJECT_ROOT / path))
                console.print(Panel(tree, title="[blue]Arborescence[/blue]", border_style="blue", padding=(0, 2)))

        elif tool_name == "execute_command":
            if result.startswith("ERREUR") or result.startswith("Commande refusée"):
                console.print(Panel(
                    f"[yellow]{result}[/yellow]",
                    title="[yellow]⚡ Résultat[/yellow]",
                    border_style="yellow",
                    padding=(0, 1),
                ))
            elif result != "(aucune sortie)":
                display = result[:5000] + ("…" if len(result) > 5000 else "")
                console.print(Panel(
                    Syntax(display, "bash", theme="monokai", word_wrap=True),
                    title="[green]⚡ Résultat[/green]",
                    border_style="green",
                    padding=(0, 1),
                ))

        elif tool_name == "search_in_files":
            pattern = tool_args.get("pattern", "")
            console.print(_format_search_results(result, pattern))

        elif tool_name == "search_books":
            query = tool_args.get("query", "")
            if result.startswith("LibraryBrain") or result.startswith("Aucun") or result.startswith("Erreur"):
                console.print(Panel(
                    f"[yellow]{result}[/yellow]",
                    title=f"[yellow]📚 search_books: {query[:50]}[/yellow]",
                    border_style="yellow",
                ))
            else:
                chunks = result.split("\n\n---\n\n")
                console.print(Panel(
                    "\n[dim]─────[/dim]\n".join(
                        f"[cyan]{c.splitlines()[0]}[/cyan]\n"
                        + "\n".join(c.splitlines()[1:])
                        for c in chunks
                    ),
                    title=f"[magenta]📚 LibraryBrain — {len(chunks)} passage(s) pour: {query[:40]}[/magenta]",
                    border_style="magenta",
                    padding=(0, 1),
                ))

        elif tool_name == "get_skills":
            domain = tool_args.get("domain", "")
            console.print(Panel(
                result[:800] + ("…" if len(result) > 800 else ""),
                title=f"[blue]🎓 Conventions {domain}[/blue]",
                border_style="blue",
                padding=(0, 1),
            ))

        elif tool_name == "browse_repo":
            repo = tool_args.get("repo", "")
            tree = Tree(f"[bold blue]📦 {repo}[/bold blue]")
            for line in result.splitlines()[1:]:
                line = line.strip()
                if line.startswith("📁"):
                    tree.add(f"[blue]{line}[/blue]")
                elif line.startswith("📄"):
                    tree.add(f"[white]{line}[/white]")
            console.print(Panel(tree, title="[blue]GitHub — Arborescence[/blue]", border_style="blue", padding=(0, 2)))

        elif tool_name == "read_github_file":
            path = tool_args.get("path", "")
            lexer = _lexer_for(path)
            console.print(Panel(
                Syntax(result[:5000], lexer, theme="monokai", line_numbers=True, word_wrap=True),
                title=f"[cyan]📄 GitHub: {tool_args.get('repo', '')}/{path}[/cyan]",
                border_style="cyan",
                padding=(0, 1),
            ))

        elif tool_name == "learn_from_books":
            console.print(Panel(
                result,
                title="[bold green]🧠 Apprentissage[/bold green]",
                border_style="green",
                padding=(0, 1),
            ))

        elif tool_name in ("list_indexed_repos", "index_github_repo"):
            icon = "📚" if "list" in tool_name else "📥"
            console.print(Panel(
                result,
                title=f"[magenta]{icon} {tool_name}[/magenta]",
                border_style="magenta",
                padding=(0, 1),
            ))

        elif tool_name == "extract_best_practices":
            repo = tool_args.get("repo", "")
            preview = result[:2000] + "…" if len(result) > 2000 else result
            console.print(Panel(
                preview,
                title=f"[yellow]🔍 Bonnes pratiques — {repo}[/yellow]",
                border_style="yellow",
                padding=(0, 1),
            ))

        elif tool_name in ("clone_github_repo", "create_project", "open_in_pycharm"):
            console.print(Panel(
                result,
                title=f"[green]🚀 {tool_name}[/green]",
                border_style="green",
                padding=(0, 1),
            ))

        elif tool_name in ("preview_code", "preview_file"):
            console.print(Panel(
                result,
                title="[bold magenta]👁  Aperçu[/bold magenta]",
                border_style="magenta",
                padding=(0, 1),
            ))

        elif tool_name == "list_previews":
            console.print(Panel(
                result,
                title="[blue]📋 Aperçus disponibles[/blue]",
                border_style="blue",
                padding=(0, 1),
            ))

        elif tool_name == "stop_preview_server":
            console.print(f"\n  [yellow]⏹  {result}[/yellow]\n")

        else:
            # Fallback générique
            preview = result[:300] + "…" if len(result) > 300 else result
            console.print(Panel(
                f"[green]{preview}[/green]",
                title=f"[green]✓ {tool_name}[/green]",
                border_style="green",
                padding=(0, 1),
            ))

    # ------------------------------------------------------------------ #
    # Boucle ReAct                                                         #
    # ------------------------------------------------------------------ #

    def run(self, user_input: str) -> None:
        """
        Boucle ReAct : Thought (LLM) → Action (outil) → Observation → repeat.
        Max MAX_ITERATIONS cycles.
        """
        # Profilage + suggestions proactives
        self.profiler.track_request(user_input)
        skills = load_skills()
        suggestions = self.profiler.get_suggestions(user_input, skills)
        if suggestions:
            hint_text = "\n".join(suggestions)
            console.print(Panel(
                hint_text,
                title="[dim bold]💡 Suggestions[/dim bold]",
                border_style="dim cyan",
                padding=(0, 1),
            ))

        self.memory.add_message("user", user_input)

        # Extraction mid-session (non-bloquante, toutes les N requêtes)
        import threading
        threading.Thread(
            target=self._mid_session_extract,
            daemon=True,
            name="mid-extract",
        ).start()

        # Router adaptatif (Roadmap v2 #4) — classifie le prompt avant la boucle.
        # Le max_iterations est adapté selon la difficulté détectée.
        # Le system prompt est hot-swappé selon task_type (Roadmap v2 #5).
        # Sticky : si le prompt est une suite courte ("on y va", "ok", "continue"…)
        # ET qu'on a déjà une décision plus complexe, on la réutilise pour ne pas
        # rétrograder la stratégie au milieu d'une tâche.
        max_iter = MAX_ITERATIONS
        if self._router_enabled:
            try:
                # Cliquet anti-rétrogradation : une relance courte ("ok", "vas-y",
                # "c'est bon ?", "continue") poursuit la tâche en cours — on réutilise
                # la dernière décision au lieu de re-classer le message en `easy`
                # (ce qui rabotait le budget à 3 itérations et forçait à relancer).
                is_continuation = (
                    self.last_routing is not None
                    and _is_continuation(user_input)
                )
                if is_continuation:
                    decision = self.last_routing
                    logger.info("Router : continuation détectée → réutilise %s/%s",
                                decision.difficulty, decision.task_type)
                else:
                    decision = self.router.classify(user_input)
                    self.last_routing = decision
                max_iter = min(decision.max_iterations, MAX_ITERATIONS)
                self._display_routing(decision, max_iter)
                # Hot-swap du system prompt selon le task_type détecté
                self._inject_system_prompt(task_type=decision.task_type)
            except Exception as exc:
                logger.warning("Router failed, using defaults: %s", exc)

        # Mémoriser le prompt utilisateur courant (utilisé par Best-of-N)
        self._current_user_prompt = user_input

        iteration = -1
        extensions = 0
        tools_called_in_pass = False
        while True:
            iteration += 1
            if iteration >= max_iter:
                # Budget épuisé sans réponse finale. Auto-continue (A) : si la tâche
                # est actionnable (elle DOIT produire des changements), que l'agent
                # travaillait encore (tools appelés) et qu'il reste des extensions,
                # on prolonge au lieu d'arrêter et de forcer l'utilisateur à relancer.
                # Les tâches `explain`/`edit` ne sont pas prolongées (une boucle de
                # lecture sans fin est un stall, pas du travail).
                is_actionable = (
                    self.last_routing is not None
                    and self.last_routing.task_type
                    in ("feature", "refactor", "self_dev", "bug_fix")
                )
                if is_actionable and tools_called_in_pass and extensions < _MAX_AUTO_EXTENSIONS:
                    extensions += 1
                    max_iter += _AUTO_EXTENSION_SIZE
                    tools_called_in_pass = False
                    logger.info("[auto-continue] extension %d/%d → max_iter=%d",
                                extensions, _MAX_AUTO_EXTENSIONS, max_iter)
                    console.print(
                        f"\n[dim cyan]  ⟳  Tâche non terminée — budget prolongé "
                        f"(+{_AUTO_EXTENSION_SIZE}, {extensions}/{_MAX_AUTO_EXTENSIONS})[/dim cyan]"
                    )
                    self.memory.messages.append({
                        "role": "user",
                        "content": (
                            "Ton budget d'itérations vient d'être prolongé car la tâche "
                            "n'est pas terminée. Continue directement l'étape en cours, "
                            "puis conclus dès que c'est fait."
                        ),
                        "timestamp": None,
                    })
                    continue
                console.print(
                    f"\n[yellow]  ⚠  Limite d'itérations atteinte ({max_iter}).[/yellow]"
                )
                logger.warning("Limite d'itérations: %d", max_iter)
                break

            if iteration > 0:
                console.print(
                    f"\n[dim]  ⟳  Itération {iteration + 1}/{max_iter}[/dim]"
                )

            messages = self.memory.get_messages_for_api()

            # Best-of-N (Roadmap v2 #7) : activé uniquement à la 1ère itération
            # des tâches hard, où la stratégie initiale est critique.
            # Override BEST_OF_N_FORCE pour l'éval A/B.
            use_bon = iteration == 0 and self._best_of_n_enabled and (
                self._best_of_n_force
                or (self.last_routing is not None and self.last_routing.use_best_of_n)
            )
            # Anti-stall escalation : si on vient d'injecter un nudge à l'itération
            # précédente et que le LLM a encore produit du texte sans action, on
            # FORCE tool_choice="required" pour le prochain tour — pas le choix.
            force_action = (
                getattr(self, "_anti_stall_fired", False)
                and getattr(self, "_anti_stall_iter", -1) == iteration - 1
            )
            tool_choice = "required" if force_action else "auto"
            if force_action:
                logger.info("[anti-stall] iter %d : tool_choice=required (escalation)", iteration)

            if use_bon:
                content, tool_calls = self._run_best_of_n(messages)
            else:
                content, tool_calls = self.llm.stream_chat(
                    messages, tools=self.tools, tool_choice=tool_choice,
                )

            if tool_calls:
                self.memory.add_tool_call_message([
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in tool_calls
                ])

                for tc in tool_calls:
                    tool_name = tc["function"]["name"]
                    tool_id = tc["id"]

                    try:
                        tool_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        tool_args = {}

                    # Afficher l'en-tête de l'action (masquer les gros blocs de contenu)
                    _HIDE_ARGS = {"content", "html", "css", "js"}
                    args_preview = "  ".join(
                        f"[dim]{k}=[/dim][bold]{repr(v)[:35]}[/bold]"
                        for k, v in tool_args.items()
                        if k not in _HIDE_ARGS
                    )
                    console.print(
                        f"\n[bold cyan]❯[/bold cyan] [bold]{tool_name}[/bold]"
                        + (f"  {args_preview}" if args_preview else "")
                    )

                    result = self._execute_and_display(tool_name, tool_args)
                    self.memory.add_tool_result(tool_id, tool_name, result)
                    self.profiler.track_tool_usage(tool_name)

                tools_called_in_pass = True
                continue

            else:
                if content:
                    self.memory.add_message("assistant", content)
                # Anti-stall : déclenche dans 2 cas, tous deux des stalls réels
                # sur les tâches actionnables (feature/refactor/self_dev) :
                #   A. content textuel "Je vais X... Voici mon plan : ..." sans tool
                #   B. content vide ET pas de tool_call (BoN avec 3 candidats vides,
                #      LLM bloqué par prompt trop strict, etc.) — pire que A
                content_stripped = (content or "").strip()
                is_empty_response = not content_stripped
                actionable_mode = (
                    self.last_routing is not None
                    and self.last_routing.task_type in ("feature", "refactor", "self_dev", "bug_fix")
                )
                stalled = (
                    actionable_mode
                    and not getattr(self, "_anti_stall_fired", False)
                    and iteration < max_iter - 1
                    and (
                        _looks_like_unfinished_plan(content)
                        or is_empty_response
                    )
                )
                if stalled:
                    self._anti_stall_fired = True
                    self._anti_stall_iter = iteration
                    cause = "réponse vide" if is_empty_response else "plan annoncé sans action"
                    logger.info("[anti-stall] %s → nudge injecté (iter=%d)", cause, iteration)
                    if is_empty_response:
                        nudge = (
                            "Ta dernière réponse était vide. Pour cette tâche, lance "
                            "directement un tool concret — par exemple :\n"
                            "  • `preview_code(html=..., js=...)` pour une démo web/canvas/3D\n"
                            "  • `write_file(path, content)` pour créer un fichier\n"
                            "  • `find_relevant_files(query)` pour explorer le projet\n"
                            "Réponds maintenant avec un tool_call qui adresse la demande "
                            "initiale de l'utilisateur."
                        )
                    else:
                        nudge = (
                            "Tu as annoncé un plan ou commencé à expliquer ton approche. "
                            "Maintenant EXÉCUTE l'étape 1 immédiatement en appelant le "
                            "tool nécessaire (write_file, preview_code, run_in_sandbox, "
                            "find_relevant_files…). Ne réponds plus en texte pur, lance un tool."
                        )
                    self.memory.messages.append({
                        "role": "user", "content": nudge, "timestamp": None,
                    })
                    console.print("[dim yellow]  ⤵  anti-stall : nudge → exécute maintenant[/dim yellow]")
                    continue

                # Text-to-action fallback : le LLM n'a pas appelé de tool mais a
                # peut-être écrit du code dans des blocs markdown. On extrait et
                # on appelle le tool nous-mêmes. C'est notre filet ultime pour
                # contourner l'incapacité MLX-LM à enforcer tool_choice=required.
                if (
                    content
                    and self.last_routing is not None
                    and self.last_routing.task_type in ("feature", "refactor", "self_dev", "bug_fix")
                    and not getattr(self, "_t2a_fired", False)
                ):
                    inferred = _infer_action_from_text(content, self._current_user_prompt)
                    if inferred:
                        self._t2a_fired = True
                        logger.info("[text-to-action] LLM stallé → extraction code → %s", inferred["name"])
                        console.print(Panel(
                            f"[bold]Klody a répondu en texte au lieu d'appeler un outil.[/bold]\n"
                            f"J'extrais le code de sa réponse et j'invoque [cyan]{inferred['name']}[/cyan] moi-même.",
                            title="[yellow]🔧 Text-to-action fallback[/yellow]",
                            border_style="yellow", padding=(0, 1),
                        ))
                        try:
                            result = self._execute_and_display(inferred["name"], inferred["args"])
                            self.memory.add_message("assistant",
                                f"[Système] Tool fallback exécuté : {inferred['name']}\n{result[:300]}")
                        except Exception as exc:
                            logger.warning("Text-to-action exec failed: %s", exc)
                        # On break après — l'utilisateur a son résultat (preview ouverte, fichier écrit, etc.)
                break

        # Reset les flags pour le prochain run (chaque appel utilisateur est neuf)
        self._anti_stall_fired = False
        self._anti_stall_iter = -1
        self._t2a_fired = False

    def _mid_session_extract(self) -> None:
        """Extraction mid-session en arrière-plan."""
        try:
            facts = extract_mid_session(self.memory.messages, self.lt_memory)
            if facts:
                logger.info("[Orchestrator] Mid-session: %d fait(s) extraits", len(facts))
        except Exception as e:
            logger.debug("[Orchestrator] Mid-session extract error: %s", e)
