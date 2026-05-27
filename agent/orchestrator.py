import json
import logging
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

from agent.llm import LLMClient, SYSTEM_PROMPT
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
        self.lt_memory = get_long_term_memory()
        self.profiler = get_profiler()
        # Sandbox jetable (créé paresseusement à la première utilisation).
        self._sandbox = None
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

        if not any(m["role"] == "system" for m in memory.messages):
            self._inject_system_prompt()

    @property
    def sandbox(self):
        """SandboxRunner attaché au PROJECT_ROOT courant (lazy init)."""
        if self._sandbox is None:
            from tools.sandbox import SandboxRunner
            self._sandbox = SandboxRunner(self.file_manager.root)
        return self._sandbox

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

        return result

    def _auto_sandbox_check(self, rel_path: str) -> str:
        """Si rel_path est un .py exécutable, lance la commande la plus
        pertinente dans le sandbox et retourne le rapport formaté."""
        if not rel_path:
            return ""
        from tools.sandbox import auto_command_for

        full_path = (self.file_manager.root / rel_path).resolve()
        # Le file_manager a déjà validé la sandbox — on re-vérifie quand même
        try:
            full_path.relative_to(self.file_manager.root.resolve())
        except ValueError:
            return ""

        cmd = auto_command_for(full_path)
        if cmd is None:
            return ""

        # Lancer dans le sandbox du workdir (cwd = workdir, chemin relatif)
        rel_cmd = [c if c != full_path.name else rel_path for c in cmd]
        result = self.sandbox.run(rel_cmd, timeout=self._sandbox_timeout)
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
                res = self.sandbox.run(
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
        max_iter = MAX_ITERATIONS
        if self._router_enabled:
            try:
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

        for iteration in range(max_iter):
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
            if use_bon:
                content, tool_calls = self._run_best_of_n(messages)
            else:
                content, tool_calls = self.llm.stream_chat(messages, tools=self.tools)

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

                continue

            else:
                if content:
                    self.memory.add_message("assistant", content)
                break

        else:
            console.print(
                f"\n[yellow]  ⚠  Limite d'itérations atteinte ({max_iter}).[/yellow]"
            )
            logger.warning("Limite d'itérations: %d", max_iter)

    def _mid_session_extract(self) -> None:
        """Extraction mid-session en arrière-plan."""
        try:
            facts = extract_mid_session(self.memory.messages, self.lt_memory)
            if facts:
                logger.info("[Orchestrator] Mid-session: %d fait(s) extraits", len(facts))
        except Exception as e:
            logger.debug("[Orchestrator] Mid-session extract error: %s", e)
