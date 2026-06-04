import contextlib
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

from config import (
    BEST_OF_N_COUNT,
    BEST_OF_N_ENABLED,
    BEST_OF_N_FORCE,
    CODE_API_KEY,
    CODE_BASE_URL,
    CODE_MODEL,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    MAX_ITERATIONS,
    PREVIEW_FEEDBACK_TIMEOUT_S,
    PROJECT_ROOT,
    ROUTER_ENABLED,
    SANDBOX_AUTO_EXEC,
    SANDBOX_TIMEOUT,
    match_allowed_root,
)
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.tree import Tree
from tools.file_manager import FileManager, SandboxViolation
from tools.github_reader import (
    browse_repo as gh_browse_repo,
    extract_best_practices as gh_extract_practices,
    index_github_repo as gh_index_repo,
    list_indexed_repos as gh_list_indexed,
    read_github_file as gh_read_file,
)
from tools.llm_import import import_llm_export, list_imports
from tools.mcp_client import (
    get_skills as mcp_get_skills,
    learn_from_books as mcp_learn,
    search_books as mcp_search_books,
)
from tools.preview import (
    list_previews as pv_list_previews,
    preview_code as pv_preview_code,
    preview_file as pv_preview_file,
    stop_preview_server as pv_stop_server,
)
from tools.project_creator import (
    clone_github_repo as pc_clone,
    create_project as pc_create,
    open_in_pycharm as pc_open_pycharm,
)
from tools.registry import get_tools
from tools.search import Search
from tools.skills import (
    delete_skill,
    format_skills_for_prompt,
    list_skills,
    load_skills,
    save_skill,
    select_skills,
)
from tools.terminal import CommandBlocked, Terminal

from agent import preview_errors
from agent.llm import LLMClient
from agent.long_term_memory import get_long_term_memory
from agent.memory import ConversationMemory
from agent.memory_extractor import extract_mid_session
from agent.profiler import get_profiler
from agent.prompts import compose_system_prompt

logger = logging.getLogger(__name__)
console = Console()


def _has_markdown_safe(text: str) -> bool:
    """Détection minimale de markdown (évite l'import circulaire avec llm._has_markdown)."""
    markers = ("```", "**", "##", "# ", "- ", "* ", "> ", "| ")
    return any(m in text for m in markers)


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


# ── Boucle de feedback preview : erreurs JS runtime → correction ──────────────
_MAX_PREVIEW_FIX = 2       # passes de correction auto max par requête
_PREVIEW_POLL_S = 0.2      # granularité du polling du tampon d'erreurs


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
    return bool(has_enumeration and intent_count >= 1)


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

# Types de tâches routés vers le modèle code dédié (cf. config.CODE_MODEL et
# Orchestrator._route_model). `explain` en est exclu : il reste sur le
# généraliste, meilleur en conversation/explication.
_CODE_TASK_TYPES = frozenset({"edit", "refactor", "bug_fix", "feature", "self_dev"})

# Prompt SLIM pour le modèle coder. Qwen3-Coder est un modèle de COMPLÉTION :
# sous le gros prompt agentique de Klody (~12k tok) il dégénère (sortie « ``` »).
# Avec ce prompt court, il sort le code complet en markdown ```html, que le
# text-to-action fallback (_infer_action_from_text) transforme en preview_code.
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
        # Hook optionnel (None en CLI) : permet aux outils bloquants comme
        # await_distillation d'observer une demande d'arrêt côté API.
        self._stop_check: Callable[[], bool] | None = None
        # Retrieval code-aware (Roadmap v2 #6) — tree-sitter + embeddings.
        self._code_index = None      # symboles + références (tree-sitter)
        self._embed_index = None     # recherche sémantique (bge-m3)
        # Best-of-N (Roadmap v2 #7) — N candidats + reranker LLM-as-judge.
        self._best_of_n = None
        self._best_of_n_enabled = BEST_OF_N_ENABLED
        self._best_of_n_count = BEST_OF_N_COUNT
        self._best_of_n_force = BEST_OF_N_FORCE
        self._current_user_prompt = ""
        # Hook optionnel (posé par l'API) : reçoit la liste des skills how-to
        # réellement injectés pour ce message → affichage UI. None en CLI.
        self._on_skills_selected = None
        self._injected_skill_slugs: list[str] = []
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

    def _route_model(self, task_type: str) -> None:
        """Bascule `self.llm` entre le généraliste et le modèle code selon la
        tâche classée par le router.

        - Tâche de code (cf. _CODE_TASK_TYPES) → modèle coder (CODE_MODEL) :
          il génère de bien meilleurs gros blocs de code.
        - Sinon (`explain`) → généraliste (LLM_MODEL).

        No-op si aucun modèle code n'est configuré (CODE_MODEL vide), ou si le
        client est sur un modèle qui n'est NI le généraliste NI le code — i.e.
        un choix manuel dans le sélecteur de l'UI, qu'on ne doit pas écraser.
        """
        self._code_model_active = False
        if not CODE_MODEL:
            return
        if self.llm.model not in (LLM_MODEL, CODE_MODEL):
            return
        if task_type in _CODE_TASK_TYPES:
            self.llm.switch_to(CODE_MODEL, CODE_BASE_URL, CODE_API_KEY)
            self._code_model_active = True  # → _inject_system_prompt utilise le prompt slim
        else:
            self.llm.switch_to(LLM_MODEL, LLM_BASE_URL, LLM_API_KEY)

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

    def _inject_system_prompt(self, task_type: str | None = None, query: str = "") -> None:
        """Injecte (ou met à jour) le system prompt en mémoire.

        Si task_type est fourni, utilise le prompt focalisé correspondant
        (Roadmap v2 #5). Sinon, utilise le fallback `default.md`.

        `query` (le prompt utilisateur courant) sert à n'injecter que les skills
        pertinents (cf. select_skills) plutôt que les ~6k tokens de tous les skills.
        """
        if getattr(self, "_code_model_active", False):
            # Modèle coder : prompt SLIM. Sous le gros prompt agentique il
            # dégénère ; en complétion il sort du code markdown ```html complet,
            # capté par le text-to-action fallback → preview_code. Pas de skills/
            # mémoire/conventions (inutiles et déstabilisants pour un coder).
            content = _CODER_SLIM_PROMPT
            skills: list[dict] = []
            self._injected_skill_slugs = []
        else:
            base_prompt = compose_system_prompt(task_type)
            skills = select_skills(load_skills(), query)
            self._injected_skill_slugs = [s.get("slug", "") for s in skills]
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

        # Notifie l'UI des skills « how-to » réellement injectés (hook optionnel).
        if self._on_skills_selected:
            howto = [s.get("name", s.get("slug", "")) for s in skills
                     if not str(s.get("slug", "")).startswith(("utilisateur_", "conventions_"))]
            with contextlib.suppress(Exception):
                self._on_skills_selected(howto)

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

    def _await_preview_errors(self, url: str, since: float) -> list:
        """Attend (borné) les erreurs JS runtime de la preview `url`.

        Poll le tampon alimenté par le beacon de l'overlay (navigateur → backend) :
        retourne la liste d'erreurs si la page plante, [] si elle se charge
        proprement (ping « ok ») ou si le délai expire. Inactif si timeout<=0
        (défaut hors live → aucun test ne bloque).
        """
        timeout = PREVIEW_FEEDBACK_TIMEOUT_S
        if timeout <= 0 or not url:
            return []
        filename = url.rsplit("/", 1)[-1]
        deadline = time.time() + timeout
        while time.time() < deadline:
            errs = [
                e
                for r in preview_errors.recent(since=since)
                if r.url.endswith(filename)
                for e in r.errors
            ]
            if errs:
                return errs
            if any(x.url.endswith(filename) for x in preview_errors.loaded(since=since)):
                return []  # chargée proprement → conclure tôt
            time.sleep(_PREVIEW_POLL_S)
        return []

    def _check_preview_feedback(self, url: str | None, since: float) -> None:
        """Relance une passe de correction si la preview plante au runtime.

        Plafonné à _MAX_PREVIEW_FIX passes/requête. Le nudge est injecté comme
        message user → l'itération suivante régénère via preview_code.
        """
        if not url:
            return
        attempts = getattr(self, "_preview_fix_attempts", 0)
        if attempts >= _MAX_PREVIEW_FIX:
            return
        errors = self._await_preview_errors(url, since)
        if not errors:
            return
        self._preview_fix_attempts = attempts + 1
        logger.info(
            "[preview-feedback] %d erreur(s) JS runtime → correction %d/%d",
            len(errors), self._preview_fix_attempts, _MAX_PREVIEW_FIX,
        )
        console.print(Panel(
            f"[bold]La preview lève {len(errors)} erreur(s) JS à l'exécution.[/bold]\n"
            f"Je renvoie les erreurs à Klody pour correction "
            f"({self._preview_fix_attempts}/{_MAX_PREVIEW_FIX}).",
            title="[yellow]🔁 Boucle de feedback preview[/yellow]",
            border_style="yellow", padding=(0, 1),
        ))
        self.memory.messages.append({
            "role": "user",
            "content": _preview_fix_nudge(url, errors, self._preview_fix_attempts),
            "timestamp": None,
        })
        # Rendre la boucle visible côté UI (klody-ui PreviewFeedbackChip).
        emit = getattr(self, "_emit", None)
        if emit is not None:
            emit({
                "type": "preview_feedback",
                "url": url,
                "count": len(errors),
                "attempt": self._preview_fix_attempts,
                "max": _MAX_PREVIEW_FIX,
                "errors": [
                    {"label": e.label, "msg": e.msg, "src": e.src} for e in errors[:8]
                ],
            })

    def _should_run_best_of_n(self, iteration: int) -> bool:
        """Faut-il lancer Best-of-N à cette itération ?

        Best-of-N (N candidats + rerank) n'a de sens qu'à la 1ère itération
        d'une tâche hard. On le DÉSACTIVE quand le coder dédié est routé (prompt
        slim) : ses N candidats sont tous actionnables et le reranker garde le
        plus rapide — générer N en séquence triple la latence pour récupérer ce
        qu'une passe unique aurait produit (~230 s → ~75 s sur le cas AVL).
        BEST_OF_N_FORCE (éval A/B) ne ressuscite pas Best-of-N sur le coder.
        """
        if iteration != 0 or not self._best_of_n_enabled:
            return False
        if getattr(self, "_code_model_active", False):
            return False
        if self._best_of_n_force:
            return True
        return self.last_routing is not None and self.last_routing.use_best_of_n

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

    # ------------------------------------------------------------------ #
    # Dispatch des outils (table nom → handler, vs ancienne chaîne de if) #
    # ------------------------------------------------------------------ #

    @property
    def _dispatch(self) -> dict:
        """Table {nom_outil: handler(args) -> str}, construite paresseusement.

        Les handlers capturent `self` mais ne déréférencent ses attributs (
        file_manager, terminal…) qu'à l'appel — l'instance peut donc être
        partiellement initialisée (tests de routage)."""
        table = self.__dict__.get("_dispatch_table")
        if table is None:
            table = self._build_dispatch()
            self.__dict__["_dispatch_table"] = table
        return table

    def _build_dispatch(self) -> dict:
        d = {
            # Fichiers
            "read_file": lambda a: self.file_manager.read_file(a["path"]),
            "write_file": lambda a: self.file_manager.write_file(a["path"], a["content"]),
            "list_files": lambda a: self.file_manager.list_files(
                a.get("path", "."), a.get("recursive", False)),
            # Terminal
            "execute_command": lambda a: self.terminal.execute_command(
                a["command"], a.get("reason", "")),
            "await_distillation": self._tool_await_distillation,
            # Recherche texte
            "search_in_files": lambda a: self.search.search_in_files(
                a["pattern"], a.get("path", "."), a.get("file_pattern", ""),
                a.get("case_sensitive", True)),
            # Sandbox + code-aware (logique multi-étapes → méthodes dédiées)
            "run_in_sandbox": self._tool_run_in_sandbox,
            "find_symbol": self._tool_find_symbol,
            "find_references": self._tool_find_references,
            "find_relevant_files": self._tool_find_relevant_files,
            # Skills
            "list_skills": lambda a: list_skills(),
            "delete_skill": lambda a: delete_skill(a["slug"]),
            "save_skill": lambda a: save_skill(a["name"], a["description"], a["content"]),
            # Imports LLM
            "import_llm_export": lambda a: import_llm_export(a["path"]),
            "list_imports": lambda a: list_imports(),
            # LibraryBrain (MCP interne)
            "search_books": lambda a: mcp_search_books(a["query"], a.get("limit", 3)),
            "get_skills": lambda a: mcp_get_skills(a["domain"]),
            "learn_from_books": lambda a: mcp_learn(a["topic"], a.get("skill_name", "")),
            # Mémoire long-terme
            "remember_fact": lambda a: self.lt_memory.remember(
                a["key"], a["content"], a.get("category", "context")),
            "forget_fact": lambda a: self.lt_memory.forget(a["key"]),
            # GitHub
            "browse_repo": lambda a: gh_browse_repo(
                a["repo"], a.get("path", ""), a.get("recursive", False)),
            "read_github_file": lambda a: gh_read_file(a["repo"], a["path"]),
            "list_indexed_repos": lambda a: gh_list_indexed(),
            "index_github_repo": lambda a: gh_index_repo(a["repo"]),
            "extract_best_practices": lambda a: gh_extract_practices(a["repo"]),
            # Projets
            "clone_github_repo": lambda a: pc_clone(a["repo"], a.get("target_dir", "")),
            "create_project": lambda a: pc_create(
                a["name"], a.get("template", "python"), a.get("description", ""),
                a.get("inspired_by", "")),
            "open_in_pycharm": lambda a: pc_open_pycharm(a["project_path"]),
            # Preview web
            "preview_code": lambda a: pv_preview_code(
                a["html"], a.get("css", ""), a.get("js", ""), a.get("title", "Preview"),
                a.get("scripts"), a.get("styles")),
            "preview_file": lambda a: pv_preview_file(a["path"]),
            "list_previews": lambda a: pv_list_previews(),
            "stop_preview_server": lambda a: pv_stop_server(),
            # Documents téléchargeables
            "generate_excel": self._tool_generate_excel,
        }
        # Audio : 6 outils, même handler paramétré par le nom (n=_name fige la
        # valeur de boucle dans le défaut → pas de closure tardive).
        for _name in (
            "analyze_audio", "edit_wav", "mix_stems",
            "generate_silence", "convert_format", "get_waveform_data",
        ):
            d[_name] = lambda a, n=_name: self._tool_audio(n, a)
        return d

    def _tool_run_in_sandbox(self, a: dict) -> str:
        sandbox = self.sandbox
        workdir = a.get("workdir", "") or ""
        if workdir.strip():
            p = Path(workdir).expanduser()
            wd = p.resolve() if p.is_absolute() else (self.file_manager.root / p).resolve()
            if match_allowed_root(wd, self.file_manager.allowed_roots) is None:
                return f"ERREUR SÉCURITÉ: workdir hors des racines autorisées: {workdir}"
            sandbox = self._sandbox_for(wd)
        res = sandbox.run(a["command"], timeout=int(a.get("timeout", 30)))
        return res.format_for_llm()

    def _tool_find_symbol(self, a: dict) -> str:
        from tools.code_index import format_symbols
        return format_symbols(self.code_index.find_symbol(a["name"]))

    def _tool_find_references(self, a: dict) -> str:
        from tools.code_index import format_references
        return format_references(self.code_index.find_references(a["name"]))

    def _tool_find_relevant_files(self, a: dict) -> str:
        from tools.code_search import format_hits
        hits = self.embed_index.search(a["query"], k=int(a.get("k", 5)))
        if not hits and not self.embed_index.is_available():
            return ("Recherche sémantique indisponible : Ollama ou "
                    "bge-m3 introuvable. Utilise find_symbol ou search_in_files.")
        return format_hits(hits)

    def _tool_audio(self, name: str, a: dict) -> str:
        from tools import audio as _audio
        fn = getattr(_audio, name)
        return json.dumps(fn(**a), ensure_ascii=False, indent=2)

    def _tool_generate_excel(self, a: dict) -> str:
        """Génère un .xlsx téléchargeable et surface un bouton de download côté UI.

        L'event `file_ready` n'est émis que dans le contexte API/WS (où `_emit`
        est injecté par _build_streaming_orchestrator) ; en CLI/tests il est
        simplement absent et l'URL reste dans le résultat JSON renvoyé au LLM.
        """
        from tools.excel import generate_excel
        result = generate_excel(a.get("filename", "export.xlsx"), a.get("sheets"))
        if result.get("status") == "ok":
            emit = getattr(self, "_emit", None)
            if emit is not None:
                emit({
                    "type": "file_ready",
                    "filename": result["filename"],
                    "download_url": result["download_url"],
                    "size": result.get("size", 0),
                    "kind": "xlsx",
                })
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _tool_await_distillation(self, a: dict) -> str:
        """Attend (côté serveur) la fin d'une distillation lancée en arrière-plan.

        Évite le polling dans la boucle ReAct : au lieu de N appels `status`
        (= N itérations = N appels LLM), on boucle ICI sur le wrapper jusqu'au
        verdict final, en UNE seule itération. Renvoie la ligne de statut telle
        quelle ('done <chemin>', 'refused <raison>', 'error <msg>') ou un
        'running' explicite en cas de timeout (pour rappeler l'outil).
        """
        import subprocess
        import time

        run_id = (a.get("run_id") or "").strip()
        if not run_id:
            return "ERREUR: run_id manquant (fourni par klody-distill.sh start)."
        try:
            timeout_s = max(1, int(a.get("timeout_s", 1800)))
        except (TypeError, ValueError):
            timeout_s = 1800

        script = Path(__file__).resolve().parents[1] / "scripts" / "klody-distill.sh"
        if not script.exists():
            return f"ERREUR: wrapper de distillation introuvable: {script}"

        stop_check = getattr(self, "_stop_check", None)
        poll_every = 5.0
        deadline = time.monotonic() + timeout_s
        last = "running"

        while time.monotonic() < deadline:
            if stop_check is not None and stop_check():
                return "Attente interrompue (arrêt demandé)."
            try:
                res = subprocess.run(
                    ["bash", str(script), "status", run_id],
                    capture_output=True, text=True, timeout=15,
                    cwd=str(script.parent.parent),
                )
                last = (res.stdout or "").strip() or (res.stderr or "").strip()
            except subprocess.TimeoutExpired:
                last = "running"  # status anormalement lent → on retente
            if last and not last.startswith("running"):
                return last
            # Sleep découpé pour réagir vite à un arrêt.
            slept = 0.0
            while slept < poll_every:
                if stop_check is not None and stop_check():
                    return "Attente interrompue (arrêt demandé)."
                time.sleep(0.5)
                slept += 0.5

        return (
            f"running — timeout après {timeout_s}s, distillation toujours en cours. "
            f"Rappelle await_distillation (run_id={run_id}) pour continuer d'attendre, "
            f"ou klody-distill.sh tail {run_id} 80 pour diagnostiquer."
        )

    def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        logger.info("Outil: %s | Args: %s", tool_name, tool_args)
        handler = self._dispatch.get(tool_name)
        try:
            if handler is not None:
                return handler(tool_args)
            # Outils MCP externes (mcp__<serveur>__<outil>)
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
        # Mémoriser le prompt courant tôt : sert au ranking des skills (injection
        # par pertinence, cf. _inject_system_prompt) et à Best-of-N.
        self._current_user_prompt = user_input
        self._preview_fix_attempts = 0  # boucle de feedback preview (reset/requête)
        task_type_for_prompt: str | None = None
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
                task_type_for_prompt = decision.task_type
                # Routage modèle : tâche de code → modèle coder, sinon généraliste.
                self._route_model(decision.task_type)
            except Exception as exc:
                logger.warning("Router failed, using defaults: %s", exc)

        # Injecte le system prompt avec les skills pertinents pour CE prompt
        # (+ task_type si le router a tranché). Toujours exécuté → le filtrage par
        # pertinence s'applique même quand le router est désactivé.
        self._inject_system_prompt(task_type=task_type_for_prompt, query=user_input)

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

            # Best-of-N (Roadmap v2 #7) : 1ère itération des tâches hard, où la
            # stratégie initiale est critique. Désactivé quand le coder dédié
            # est routé (cf. _should_run_best_of_n).
            use_bon = self._should_run_best_of_n(iteration)
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

                _preview_url: str | None = None
                _preview_since = 0.0
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

                    # Horodatage AVANT exécution → ne capter que les erreurs de CETTE preview.
                    if tool_name in ("preview_code", "preview_file"):
                        _preview_since = time.time()
                    result = self._execute_and_display(tool_name, tool_args)
                    self.memory.add_tool_result(tool_id, tool_name, result)
                    self.profiler.track_tool_usage(tool_name)
                    if tool_name in ("preview_code", "preview_file"):
                        _preview_url = _extract_preview_url(result)

                tools_called_in_pass = True
                # Boucle de feedback : si la preview plante au runtime, on relance
                # une passe de correction (no-op si pas de preview ou timeout désactivé).
                self._check_preview_feedback(_preview_url, _preview_since)
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
                        _t2a_result = ""
                        _t2a_since = time.time()
                        try:
                            _t2a_result = self._execute_and_display(inferred["name"], inferred["args"])
                            self.memory.add_message("assistant",
                                f"[Système] Tool fallback exécuté : {inferred['name']}\n{_t2a_result[:300]}")
                        except Exception as exc:
                            logger.warning("Text-to-action exec failed: %s", exc)
                        # Boucle de feedback : une preview qui plante au runtime → on
                        # relance une passe de correction au lieu de conclure.
                        if inferred["name"] in ("preview_code", "preview_file"):
                            _before = getattr(self, "_preview_fix_attempts", 0)
                            self._check_preview_feedback(_extract_preview_url(_t2a_result), _t2a_since)
                            if getattr(self, "_preview_fix_attempts", 0) > _before:
                                self._t2a_fired = False  # ré-autorise le fallback à la régénération
                                continue
                        # On break après — l'utilisateur a son résultat (preview, fichier…).
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
