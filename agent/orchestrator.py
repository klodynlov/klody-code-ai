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
    RETRIEVAL_INJECT_ENABLED,
    RETRIEVAL_INJECT_K,
    RETRIEVAL_MIN_SCORE,
    ROUTER_ENABLED,
    SANDBOX_AUTO_EXEC,
    SANDBOX_TIMEOUT,
    SELF_CRITIQUE_ENABLED,
    SKILLS_ON_CODER_ENABLED,
    SKILLS_ON_CODER_MAX,
    SKILLS_ON_CODER_MAX_CHARS,
    SKILLS_ROUTER_ENABLED,
    SKILLS_ROUTER_JUDGE,
    THINKING_BUDGET_HIGH,
    THINKING_BUDGET_LOW,
    THINKING_BUDGET_MED,
    THINKING_BUDGET_NONE,
    THINKING_ENABLED,
    THINKING_ON_CODER,
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
from tools.library_distiller import distill_theme
from tools.llm_import import import_llm_export, list_imports
from tools.mcp_client import (
    catalog_lookup as mcp_catalog,
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
from tools.registry import ASK_USER_TOOL, get_tools
from tools.search import Search
from tools.skills import (
    _matching_terms,
    _skill_is_code_compatible,
    _skill_terms,
    delete_skill,
    format_skills_compact,
    format_skills_for_prompt,
    list_skills,
    load_skills,
    save_skill,
    select_skills,
)
from tools.terminal import CommandBlocked, Terminal
from tools.vision import analyser_image as vn_analyser_image
from tools.voice import speak as vc_speak

from agent import preview_errors, semantic_memory
from agent.llm import LLMClient
from agent.long_term_memory import get_long_term_memory
from agent.memory import ConversationMemory
from agent.memory_extractor import extract_mid_session
from agent.profiler import get_profiler
from agent.prompts import compose_system_prompt

logger = logging.getLogger(__name__)

# ASI06 : bouclier anti-poisoning des sections mémoire auto-apprises injectées au
# system prompt. Import souple — même dégradation douce que agent/semantic_memory.
try:
    from klody_memory.sanitizer import sanitize as _mem_sanitize
except Exception:  # paquet klody-memory absent : le cœur de Klody survit
    _mem_sanitize = None


def _shield(section: str, label: str) -> str:
    """Sanitize strict d'une section de prompt auto-apprise. Ne strippe que les
    spans d'attaque (marqueur de rédaction), le contenu légitime passe intact."""
    if not section or _mem_sanitize is None:
        return section
    text, flags = _mem_sanitize(section, strict=True)
    if flags:
        logger.warning("[prompt-shield] injection suspecte strippée (section %s, "
                       "flags=%s)", label, flags)
    return text
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

# Anti-boucle : le LLM peut rappeler le MÊME outil avec les MÊMES arguments en
# rafale quand le résultat ne le satisfait pas (typiquement un `run_in_sandbox`
# qui plante à l'identique — proxy mort, script cassé). Sans garde-fou il tourne
# jusqu'à épuiser max_iter (avec auto-continue, jusqu'à 30 passes) sans rien
# produire : c'est la « boucle » visible côté utilisateur. On compte les appels
# (nom + args) identiques sur le run ; au 3e on injecte un avertissement (change
# d'approche), au 4e on coupe et on force la synthèse finale.
_LOOP_REPEAT_WARN = 3
_LOOP_REPEAT_BREAK = 4

# Anti-scan : variante « lecture errante ». L'anti-boucle ci-dessus clé sur
# nom+args+résultat — elle ne voit PAS un modèle qui balaie 40 fichiers DIFFÉRENTS
# au hasard (40 read_file = 40 clés = compteur jamais > 1) en cherchant une info
# qu'il ne localise pas. Symptôme observé : storm de read_file à la racine, cap
# d'itérations atteint, réponse vide. On compte ici le MÊME OUTIL (quels que
# soient args/résultat), restreint aux outils d'exploration (non producteurs) :
# au seuil WARN on pousse à utiliser list_files/find_relevant_files ; au BREAK on
# coupe et on synthétise. Seuils plus hauts que l'anti-boucle : explorer un repo
# légitimement peut demander plusieurs lectures.
_SCAN_REPEAT_WARN = 8
_SCAN_REPEAT_BREAK = 14

# Anti-écho : un outil PRODUCTEUR réémis avec EXACTEMENT les mêmes arguments
# refabrique le même artefact — jamais un progrès — mais son résultat peut
# différer en surface (preview_code écrit preview-24, -25, -26… → URL neuve →
# hash(résultat) différent → l'anti-boucle nom+args+résultat ne monte jamais).
# Vécu 03/07 (« canard 3D ») : 11 preview_code identiques avec js vide (émission
# XML du tool call cassée), chaque appel « réussissait », 25 itérations brûlées
# puis dérive totale de l'agent. On compte la série CONSÉCUTIVE du même appel
# producteur (nom+args, résultat IGNORÉ) : la série casse dès qu'un AUTRE appel
# producteur passe (write_file puis re-preview du même fichier = workflow
# légitime) ; les lectures/sondages intercalés ne la cassent pas (ils ne
# changent pas l'appel réémis). Au WARN on signale l'écho au modèle (un argument
# est probablement vide/tronqué) ; au BREAK on coupe et on force la synthèse.
_ECHO_REPEAT_WARN = 3
_ECHO_REPEAT_BREAK = 5

# Outils « producteurs » : ils fabriquent/modifient un artefact (fichier, aperçu,
# projet) ou exécutent du code. Si la dernière passe en a appelé un, l'agent
# travaille vraiment — un échec en cours (ex: preview_file sur un HTML pas encore
# écrit) ne doit pas dead-locker sur un label `explain`/`edit` et forcer une
# relance. Les outils en lecture seule (read_file, search_*, find_*) en sont
# exclus : une boucle de lecture sans fin reste un stall, pas du travail.
_PRODUCING_TOOLS = frozenset({
    "write_file", "preview_code", "preview_file", "run_in_sandbox",
    "create_project", "clone_github_repo",
    # Générateurs d'artefacts téléchargeables (même piège : produire un .xlsx
    # puis « tu as fini ? » routé `explain` ne doit pas dead-locker).
    "generate_excel", "generate_text_file", "bundle_zip", "import_llm_export",
})

# Types de tâches routés vers le modèle code dédié (cf. config.CODE_MODEL et
# Orchestrator._route_model). Ceux qui PRODUISENT/MODIFIENT du code y vont ;
# ceux qui produisent surtout de la PROSE ou un RAPPORT (`explain`, `review`,
# `security`, `docs`) restent sur le généraliste, meilleur en analyse/rédaction.
_CODE_TASK_TYPES = frozenset({
    "edit", "refactor", "bug_fix", "feature", "self_dev",
    "test_gen", "perf", "migrate",
})

# Auto-critique (Levier 3) : on ne critique pas une réponse triviale (salutation,
# « oui », confirmation courte) — pas assez de matière, le coût ne vaut pas le gain.
_SELF_CRITIQUE_MIN_CHARS = 200
_SELF_CRITIQUE_PROMPT = (
    "Relis ta dernière réponse à l'utilisateur d'un œil critique. Cherche : erreur "
    "factuelle, oubli important, hypothèse non vérifiée, ou affirmation trop "
    "catégorique.\n"
    "- Si la réponse est DÉJÀ correcte et complète, réponds EXACTEMENT par le seul "
    "mot : INCHANGÉ\n"
    "- Sinon, réécris DIRECTEMENT la réponse finale corrigée pour l'utilisateur "
    "(sans méta-commentaire sur ta relecture)."
)

# Marqueurs d'un skill INTERACTIF (guide déroulé en posant des questions à
# l'utilisateur, façon QCM) par opposition à une fiche how-to statique. Un tel
# skill ne peut PAS fonctionner sous le modèle coder-slim (qui n'injecte aucun
# skill) ni avec l'anti-stall (qui force un tool alors que le skill doit poser
# ses questions en texte et attendre). Cf. session 419676b5 : skill QCM
# `concevoir_un_algorithme_pas_a_pas` routé #1 mais jamais déclenché.
_INTERACTIVE_SKILL_MARKERS = (
    "qcm", "à choix multiple", "choix multiple", "fiche de besoin", "questionnaire",
)


def _as_bool(v: object) -> bool:
    """Coerce un argument d'outil en booléen, robuste aux modèles locaux qui
    sérialisent les bools en CHAÎNE ('true'/'false') — `bool('false')` vaut True,
    d'où ce garde-fou (cf. _normalize_ask_user_options, même classe de bug)."""
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _skill_is_interactive(skill: dict) -> bool:
    """Le skill est-il un guide INTERACTIF (QCM) plutôt qu'une fiche statique ?

    Vrai si le drapeau explicite `interactive: true` est présent, ou si ≥2
    marqueurs apparaissent dans le contenu (un how-to classique n'en contient
    pas plusieurs à la fois)."""
    if skill.get("interactive") is True:
        return True
    blob = (skill.get("content") or "").lower()
    return sum(marker in blob for marker in _INTERACTIVE_SKILL_MARKERS) >= 2


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
        # Modèle ÉPINGLÉ par un choix manuel explicite dans le sélecteur de l'UI.
        # None = mode « Auto » (le routeur bascule brain↔coder selon la tâche, défaut).
        # Posé par l'API (_build_streaming_orchestrator) ; toujours None en CLI.
        self._pinned_model = None
        # Hook optionnel (None en CLI) : permet aux outils bloquants comme
        # await_distillation d'observer une demande d'arrêt côté API.
        self._stop_check: Callable[[], bool] | None = None
        # Hook optionnel (posé par l'API, None en CLI) : ouvre une question
        # interactive (carte cliquable côté UI), bloque le tour jusqu'à la
        # réponse et la renvoie. Utilisé par l'outil ask_user (skills QCM).
        self._ask_user: Callable[[str, list[str], bool], str] | None = None
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
        # Routeur de skills sémantique optionnel (lazy, opt-in, cache embeddings).
        self._skill_router = None
        # Anti-stall : 1 nudge max par run() pour débloquer un plan annoncé sans action.
        self._anti_stall_fired = False
        self._anti_stall_iter = -1
        # Text-to-action : 1 fallback max par run() pour extraire un tool_call
        # depuis un content texte+markdown si le LLM refuse d'appeler tool natifs.
        self._t2a_fired = False
        # Skill interactif (QCM) actif pour le run courant : neutralise la
        # bascule coder-slim, l'anti-stall et le text-to-action (cf. run()).
        self._interactive_skill_active = False

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

    def _route_model(self, task_type: str, *, force_generalist: bool = False) -> None:
        """Bascule `self.llm` entre le généraliste et le modèle code selon la
        tâche classée par le router.

        - Tâche de code (cf. _CODE_TASK_TYPES) → modèle coder (CODE_MODEL) :
          il génère de bien meilleurs gros blocs de code.
        - Sinon (`explain`) → généraliste (LLM_MODEL).

        `force_generalist` : reste sur le généraliste même pour une tâche de
        code. Sert aux skills interactifs (QCM), qui ont besoin du prompt
        complet (le coder-slim n'injecte aucun skill) et d'un mode dialogue.

        No-op si aucun modèle code n'est configuré (CODE_MODEL vide), ou si le
        client est sur un modèle qui n'est NI le généraliste NI le code — i.e.
        un choix manuel dans le sélecteur de l'UI, qu'on ne doit pas écraser.

        Pin manuel (`_pinned_model`) : si l'utilisateur a explicitement épinglé un
        modèle dans le sélecteur, le routeur ne bascule JAMAIS — son choix prime.
        On aligne seulement `_code_model_active` sur le modèle épinglé (coder →
        prompt slim + thinking seulement sur `hard`, cf. _should_think, exactement
        comme en routage auto) pour un comportement identique quel que soit le
        chemin d'accès au coder.
        """
        self._code_model_active = False
        if self._pinned_model:
            self._code_model_active = bool(CODE_MODEL) and self._pinned_model == CODE_MODEL
            return
        if not CODE_MODEL:
            return
        if self.llm.model not in (LLM_MODEL, CODE_MODEL):
            return
        if task_type in _CODE_TASK_TYPES and not force_generalist:
            self.llm.switch_to(CODE_MODEL, CODE_BASE_URL, CODE_API_KEY)
            self._code_model_active = True  # → _inject_system_prompt utilise le prompt slim
        else:
            self.llm.switch_to(LLM_MODEL, LLM_BASE_URL, LLM_API_KEY)

    def _should_think(self) -> bool:
        """Active le mode raisonnement (CoT) pour CE tour.

        Brain : tâches de RAISONNEMENT — `explain` (le SEUL task_type qui reste
        sur le brain, cf. _CODE_TASK_TYPES) ou difficulté `hard`. L'A/B (08/06) y
        a mesuré un gain de QUALITÉ (8/10) ; son seul coût était un TTFT aveugle,
        désormais corrigé en diffusant le CoT à l'UI (cf. stream_api) → le gate
        large redevient justifié.

        Coder : UNIQUEMENT sur `hard` (et si config.THINKING_ON_CODER). Depuis la
        bascule Qwen3.6-35B-A3B (03/07) le coder partage la base thinking du brain
        (lancé no-think par la gateway, réactivable par requête) ; l'A/B coder
        (no-think 8/8 = 8/8) montre que le CoT ne vaut pas sa latence sur le code
        standard, mais une feature hard/créative sans CoT échoue (vécu « canard
        3D » 03/07). Rollback coder instruct → THINKING_ON_CODER=false.

        JAMAIS en skill interactif (un QCM dialogue, il ne raisonne pas en
        silence). Cf. config.THINKING_ENABLED et llm.stream_chat."""
        if not THINKING_ENABLED:
            return False
        if getattr(self, "_interactive_skill_active", False):
            return False
        d = self.last_routing
        if d is None:
            return False
        if getattr(self, "_code_model_active", False):
            return THINKING_ON_CODER and d.difficulty == "hard"
        return d.task_type == "explain" or d.difficulty == "hard"

    def _thinking_budget(self) -> int:
        """Budget de raisonnement (CoT) PAR TYPE DE TÂCHE pour ce tour.

        Politique inspirée du `thinking_budget_tokens` par requête du node de veille.
        mlx_lm n'a AUCUN budget natif (vérifié 0.31.3) et ne permet pas de borner le
        CoT côté client sans troncature dure du flux (écartée). Ce budget est donc
        FORWARD-COMPAT : forwardé dans chat_template_kwargs.thinking_budget (no-op
        aujourd'hui, effectif si un futur template l'honore) — il ne modifie PAS
        max_tokens (cf. llm.stream_chat, docs/thinking-budget-policy.md). Tiers :
        - thinking OFF (skill interactif, edit/medium, coder non-hard…) → 0, aucun CoT.
        - `hard` → tier HAUT (raisonnement profond) — brain ET coder (cf.
          _should_think : le coder ne raisonne QUE sur hard depuis le 03/07).
        - `explain` medium → tier MOYEN ; `explain` easy → tier BAS.
        """
        if not self._should_think():
            return THINKING_BUDGET_NONE
        d = self.last_routing
        if d is not None and d.difficulty == "hard":
            budget = THINKING_BUDGET_HIGH
        elif d is not None and d.difficulty == "easy":
            budget = THINKING_BUDGET_LOW
        else:
            budget = THINKING_BUDGET_MED
        logger.info(
            "[thinking-budget] task_type=%s difficulty=%s → budget=%d tok (forward-compat)",
            getattr(d, "task_type", None), getattr(d, "difficulty", None), budget,
        )
        return budget

    def _maybe_self_critique(self, draft: str) -> None:
        """Passe d'auto-critique sur la réponse finale (Levier 3).

        Sur une tâche de raisonnement (explain/hard, servie par le brain), relit la
        réponse et la RÉÉCRIT si elle contient une erreur/oubli/hypothèse fausse ;
        sinon la garde telle quelle (sentinel « INCHANGÉ »). Coûte un appel LLM
        supplémentaire → OFF par défaut (config.SELF_CRITIQUE_ENABLED), à activer
        après A/B au bench. Best-effort, jamais bloquante.

        Mêmes gardes que le thinking : jamais sur le coder (instruct) ni en skill
        interactif (dialogue). Une seule passe par tour (_self_critique_done)."""
        if not SELF_CRITIQUE_ENABLED:
            return
        if getattr(self, "_code_model_active", False) or getattr(self, "_interactive_skill_active", False):
            return
        if getattr(self, "_self_critique_done", False):
            return
        d = self.last_routing
        if d is None or not (d.task_type == "explain" or d.difficulty == "hard"):
            return
        if len((draft or "").strip()) < _SELF_CRITIQUE_MIN_CHARS:
            return  # réponse trop courte pour qu'une relecture vaille le coût
        self._self_critique_done = True

        # Consigne posée sur une COPIE éphémère de la conversation : ni la consigne
        # ni un éventuel « INCHANGÉ » ne polluent l'historique persistant.
        critique_msgs = [
            *self.memory.get_messages_for_api(),
            {"role": "user", "content": _SELF_CRITIQUE_PROMPT},
        ]
        try:
            revised, _ = self.llm.stream_chat(
                critique_msgs, tools=None, silent=True,
                enable_thinking=self._should_think(),
            )
        except Exception as exc:  # une critique KO ne casse jamais le tour
            logger.debug("Auto-critique ignorée : %s", exc)
            return

        revised = (revised or "").strip()
        # Réponse déjà bonne (sentinel) ou vide → on garde le brouillon tel quel,
        # sans rien réafficher (l'utilisateur a déjà vu la réponse).
        if not revised or revised.upper().startswith(("INCHANGÉ", "INCHANGE")):
            logger.info("[auto-critique] réponse confirmée inchangée")
            return

        # La critique a produit une version affinée → on l'affiche et on remplace
        # le dernier message assistant en mémoire (le futur contexte voit la bonne).
        logger.info("[auto-critique] réponse affinée (%d chars)", len(revised))
        console.print(Panel(
            Markdown(revised),
            title="[cyan]🔍 Auto-critique — réponse affinée[/cyan]",
            border_style="cyan", padding=(0, 1),
        ))
        for msg in reversed(self.memory.messages):
            if msg.get("role") == "assistant":
                msg["content"] = revised
                break
        self.memory.save()

    def _detect_interactive_skill(self, query: str) -> bool:
        """Le skill le plus pertinent pour cette requête est-il un guide
        INTERACTIF (QCM) ? On regarde le 1er skill how-to routé (hors always-on
        `utilisateur_`/`conventions_`, toujours renvoyés en tête). Si oui, le
        run reste sur le généraliste avec prompt complet (sinon le coder-slim
        n'injecterait aucun skill) et l'anti-stall / text-to-action se taisent
        (le skill répond en posant des questions, pas en lançant un tool).

        Garde-fou anti-faux-positif : un skill interactif DÉTOURNE le tour (il
        pose des questions au lieu d'agir). On ne l'active donc que si la requête
        recoupe l'IDENTITÉ du skill (nom + slug), pas seulement des mots
        génériques de sa description. Sans ça, « liste les fichiers du projet »
        matcherait « concevoir un algorithme » via {liste, projet} (présents dans
        la longue liste de déclencheurs) et lancerait un QCM hors-sujet."""
        try:
            skills = select_skills(load_skills(), query)
        except Exception as exc:  # détection best-effort, jamais bloquante
            logger.debug("Détection skill interactif ignorée : %s", exc)
            return False
        terms = _skill_terms(query)
        for s in skills:
            if str(s.get("slug", "")).startswith(("utilisateur_", "conventions_")):
                continue
            if not _skill_is_interactive(s):
                return False
            # Recoupe-t-on l'identité (nom/slug), pas juste la description ?
            identity = {"name": s.get("name", ""), "slug": s.get("slug", ""), "description": ""}
            return bool(_matching_terms(terms, identity))
        return False

    @property
    def code_index(self):
        """Index tree-sitter symboles + références (lazy)."""
        if self._code_index is None:
            from tools.code_index import CodeIndex
            self._code_index = CodeIndex(self.file_manager.root)
        return self._code_index

    @property
    def embed_index(self):
        """Index embeddings bge-m3 pour recherche sémantique (lazy).

        Singleton PARTAGÉ par racine (get_embedding_index) : l'Orchestrator est
        recréé à chaque message, l'index doit survivre entre les tours pour ne pas
        se reconstruire à chaque fois (cf. tools.code_search.get_embedding_index)."""
        if self._embed_index is None:
            from tools.code_search import get_embedding_index
            self._embed_index = get_embedding_index(self.file_manager.root)
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

    def _get_skill_router(self):
        """Routeur de skills sémantique optionnel (lazy, opt-in).

        Singleton par orchestrateur : préserve le cache d'embeddings des
        descriptions de skills entre les tours ReAct (sinon on le reperdrait à
        chaque _inject_system_prompt → 1 embed/skill par tour). N'est instancié
        que si SKILLS_ROUTER_ENABLED ; sinon ce chemin n'est jamais pris.
        """
        if self._skill_router is None:
            from tools.skill_router import SkillRouter
            self._skill_router = SkillRouter(use_llm_judge=SKILLS_ROUTER_JUDGE)
        return self._skill_router

    def _relevant_files_section(self, query: str) -> str:
        """Recherche sémantique proactive : top-k fichiers du projet probablement
        pertinents pour `query`, formatés en PISTES pour le prompt.

        L'agent n'a plus à deviner/explorer à l'aveugle quels fichiers concernent
        la tâche. Best-effort et JAMAIS bloquant : silencieux si le retrieval est
        coupé (flag), si la requête est vide, si l'index embeddings est indisponible
        (Ollama/bge-m3 absent) ou en cas d'erreur. Les hits sous le seuil de
        similarité sont écartés (pas de bruit sur une requête conversationnelle).

        NB : le 1er appel d'une session construit l'index (coûteux), puis
        incrémental. Présenté en « pistes à vérifier » — pas une vérité absolue —
        pour ne pas ancrer le modèle sur un faux positif."""
        if not RETRIEVAL_INJECT_ENABLED or not query.strip():
            return ""
        try:
            if not self.embed_index.is_available():
                return ""
            hits = self.embed_index.search(query, k=RETRIEVAL_INJECT_K)
        except Exception as exc:  # best-effort : un retrieval KO ne casse pas le tour
            logger.debug("Retrieval proactif ignoré : %s", exc)
            return ""
        hits = [h for h in hits if h.score >= RETRIEVAL_MIN_SCORE]
        if not hits:
            return ""
        logger.info("[retrieval] %d piste(s) injectée(s) : %s",
                    len(hits), ", ".join(h.rel_path for h in hits))
        lines = [
            "\n\n## Fichiers du projet probablement pertinents (recherche sémantique)\n",
            "_Pistes — à confirmer en lisant les fichiers avant d'agir, pas une vérité absolue :_",
        ]
        for h in hits:
            lines.append(f"- `{h.rel_path}` (pertinence {h.score:.2f})")
        return "\n".join(lines)

    def _inject_system_prompt(self, task_type: str | None = None, query: str = "") -> None:
        """Injecte (ou met à jour) le system prompt en mémoire.

        Si task_type est fourni, utilise le prompt focalisé correspondant
        (Roadmap v2 #5). Sinon, utilise le fallback `default.md`.

        `query` (le prompt utilisateur courant) sert à n'injecter que les skills
        pertinents (cf. select_skills) plutôt que les ~6k tokens de tous les skills,
        ET les fichiers du projet sémantiquement proches (retrieval proactif).
        """
        # Retrieval proactif : pistes de fichiers pertinents pour CE prompt. Injecté
        # dans les DEUX modes — y compris coder-slim, car les tâches de code (donc
        # le besoin de savoir QUELS fichiers toucher) y sont justement routées.
        retrieval_section = self._relevant_files_section(query)

        if getattr(self, "_code_model_active", False):
            # Modèle coder : prompt SLIM. Sous le gros prompt agentique il
            # dégénère ; en complétion il sort du code markdown ```html complet,
            # capté par le text-to-action fallback → preview_code. Pas de skills/
            # mémoire/conventions (inutiles et déstabilisants pour un coder) — mais
            # les pistes de fichiers, factuelles et courtes, l'aident à viser juste.
            # Par défaut le coder ne reçoit AUCUN skill (prompt slim). Opt-in
            # (SKILLS_ON_CODER_ENABLED) : on laisse passer UNIQUEMENT les skills
            # marqués `code_compatible` ET jugés pertinents par select_skills
            # (double garde : tag = quoi est sûr, pertinence = quand c'est utile),
            # capés à SKILLS_ON_CODER_MAX et rendus COMPACTS (description + content
            # tronqué — le dump intégral réveillerait la dégénérescence du coder).
            skills: list[dict] = []
            if SKILLS_ON_CODER_ENABLED:
                # On EXCLUT les always-on (utilisateur_*/conventions_*) que
                # select_skills renvoie toujours en tête SANS test de pertinence :
                # sinon un always-on taggé code_compatible squatterait le coder sur
                # toute tâche (hors-sujet inclus) et mangerait le cap, évinçant un
                # how-to vraiment pertinent. Parité avec _detect_interactive_skill /
                # le hook UI. La « double garde » (tag ET pertinence) ne vaut que
                # pour les how-to relevance-filtrés.
                skills = [
                    s for s in select_skills(load_skills(), query)
                    if _skill_is_code_compatible(s)
                    and not str(s.get("slug", "")).startswith(("utilisateur_", "conventions_"))
                ][:SKILLS_ON_CODER_MAX]
            skills_section = (
                format_skills_compact(skills, SKILLS_ON_CODER_MAX_CHARS) if skills else ""
            )
            content = _CODER_SLIM_PROMPT + retrieval_section + skills_section
            self._injected_skill_slugs = [s.get("slug", "") for s in skills]
        else:
            base_prompt = compose_system_prompt(task_type)
            if SKILLS_ROUTER_ENABLED:
                # Routeur sémantique opt-in. select() ne lève jamais (repli
                # interne sur select_skills) ; le try/except couvre en plus un
                # éventuel échec d'import du module → zéro régression possible.
                try:
                    skills = self._get_skill_router().select(query, k=5)
                except Exception as exc:
                    logger.debug("skill_router KO → select_skills (IDF): %s", exc)
                    skills = select_skills(load_skills(), query)
            else:
                skills = select_skills(load_skills(), query)
            self._injected_skill_slugs = [s.get("slug", "") for s in skills]
            skills_section = format_skills_for_prompt(skills) if skills else ""
            lt_section = self.lt_memory.format_for_prompt()  # sanitize interne (ASI06)
            # ASI06 : profil/conventions/erreurs sont APPRIS automatiquement (requêtes,
            # sorties d'outils, contenu de fichiers) → mêmes canaux de poisoning que la
            # mémoire long terme. Bouclier au point d'injection dans le system prompt :
            # sanitize strict ne strippe que les spans d'attaque, le reste passe intact.
            profile_section = _shield(self.profiler.get_profile_for_prompt(), "profil")
            # Conventions auto-détectées + erreurs récurrentes (Roadmap v2 #8)
            conv_section = ""
            err_section = ""
            try:
                conv_section = _shield(
                    self.conventions.detect().format_for_prompt(), "conventions")
            except Exception as exc:
                logger.debug("Convention detection skipped: %s", exc)
            try:
                err_section = _shield(self.error_memory.format_for_prompt(), "erreurs")
            except Exception as exc:
                logger.debug("Error memory format skipped: %s", exc)
            content = (
                f"{base_prompt}\n\n"
                f"Dossier projet actif: {PROJECT_ROOT}"
                f"{retrieval_section}"
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
            # Question interactive (skills QCM) — exposé conditionnellement
            "ask_user": self._tool_ask_user,
            # Recherche texte
            "search_in_files": lambda a: self.search.search_in_files(
                a["pattern"], a.get("path", "."), a.get("file_pattern", ""),
                a.get("case_sensitive", True)),
            # Sandbox + code-aware (logique multi-étapes → méthodes dédiées)
            "run_in_sandbox": self._tool_run_in_sandbox,
            "find_symbol": self._tool_find_symbol,
            "find_references": self._tool_find_references,
            "find_relevant_files": self._tool_find_relevant_files,
            "code_graph": self._tool_code_graph,
            "analyze_dependencies": self._tool_analyze_dependencies,
            "run_sql": self._tool_run_sql,
            "docker_control": self._tool_docker_control,
            "kubectl_control": self._tool_kubectl_control,
            "git_control": self._tool_git_control,
            # Skills
            "list_skills": lambda a: list_skills(),
            "delete_skill": lambda a: delete_skill(a["slug"]),
            "save_skill": lambda a: save_skill(
                a["name"], a["description"], a["content"],
                code_compatible=_as_bool(a.get("code_compatible", False)),
            ),
            # Imports LLM
            "import_llm_export": lambda a: import_llm_export(a["path"]),
            "list_imports": lambda a: list_imports(),
            # LibraryBrain (MCP interne)
            "search_books": lambda a: mcp_search_books(a["query"], a.get("limit", 3)),
            "library_catalog": lambda a: mcp_catalog(a["query"], a.get("limit", 5)),
            "get_skills": lambda a: mcp_get_skills(a["domain"]),
            "learn_from_books": lambda a: mcp_learn(a["topic"], a.get("skill_name", "")),
            "distill_theme": lambda a: distill_theme(
                a["theme"], a.get("slug", ""),
                code_compatible=_as_bool(a.get("code_compatible", False)),
                llm=self.llm),
            # Mémoire long-terme
            "remember_fact": lambda a: self.lt_memory.remember(
                a["key"], a["content"], a.get("category", "context")),
            "forget_fact": lambda a: self.lt_memory.forget(a["key"]),
            # Mémoire sémantique (archive klody_memory — lecture seule)
            "rappeler_memoire": lambda a: semantic_memory.recall_for_llm(
                a["requete"],
                top_k=int(a.get("nombre", 5) or 5),
                kind=(a.get("type") or "").strip() or None),
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
            # Voix parlée (TTS VocalBrain + afplay)
            "speak": lambda a: vc_speak(a["text"], a.get("language", "fr")),
            # Vision (image → description via worker VL, gateway Klody Core)
            "analyser_image": lambda a: vn_analyser_image(
                a["image_path"], a.get("question", "")),
            # Documents téléchargeables
            "generate_excel": self._tool_generate_excel,
            "generate_text_file": self._tool_generate_text_file,
            "bundle_zip": self._tool_bundle_zip,
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

    def _tool_code_graph(self, a: dict) -> str:
        from tools import code_graph
        return code_graph.query(self.file_manager.root, a)

    def _tool_analyze_dependencies(self, a: dict) -> str:
        from tools.deps_analyzer import analyze_dependencies, format_dependency_report
        target = (a.get("path") or ".").strip() or "."
        p = Path(target).expanduser()
        base = p.resolve() if p.is_absolute() else (self.file_manager.root / p).resolve()
        if match_allowed_root(base, self.file_manager.allowed_roots) is None:
            return f"ERREUR SÉCURITÉ: chemin hors des racines autorisées: {target}"
        return format_dependency_report(analyze_dependencies(base))

    def _tool_run_sql(self, a: dict) -> str:
        from tools.sql_runner import format_sql_result, run_sql
        try:
            max_rows = int(a.get("max_rows", 100) or 100)
        except (TypeError, ValueError):
            max_rows = 100
        res = run_sql(
            a.get("query", ""),
            a.get("database", ""),
            mode=a.get("mode", "read"),
            params=a.get("params"),
            max_rows=max_rows,
        )
        return format_sql_result(res)

    def _tool_docker_control(self, a: dict) -> str:
        from tools.docker_tools import docker_control, format_docker_result
        try:
            tail = int(a.get("tail", 200) or 200)
        except (TypeError, ValueError):
            tail = 200
        res = docker_control(a.get("action", ""), a.get("target", ""), tail=tail)
        return format_docker_result(res)

    def _tool_kubectl_control(self, a: dict) -> str:
        from tools.k8s_tools import format_kubectl_result, kubectl_control
        try:
            tail = int(a.get("tail", 200) or 200)
        except (TypeError, ValueError):
            tail = 200
        res = kubectl_control(
            a.get("action", ""),
            resource=a.get("resource", ""),
            name=a.get("name", ""),
            namespace=a.get("namespace", ""),
            container=a.get("container", ""),
            tail=tail,
        )
        return format_kubectl_result(res)

    def _tool_git_control(self, a: dict) -> str:
        from tools.git_tools import format_git_result, git_control
        try:
            max_count = int(a.get("max_count", 20) or 20)
        except (TypeError, ValueError):
            max_count = 20
        res = git_control(
            a.get("action", ""),
            path=a.get("path", ""),
            ref=a.get("ref", ""),
            file=a.get("file", ""),
            max_count=max_count,
            message=a.get("message", ""),
        )
        return format_git_result(res)

    def _tool_audio(self, name: str, a: dict) -> str:
        from tools import audio as _audio
        fn = getattr(_audio, name)
        return json.dumps(fn(**a), ensure_ascii=False, indent=2)

    def _emit_file_ready(self, result: dict, kind: str) -> None:
        """Surface un bouton de téléchargement côté UI pour un artefact généré.

        L'event `file_ready` n'est émis que dans le contexte API/WS (où `_emit`
        est injecté par _build_streaming_orchestrator) ; en CLI/tests il est
        simplement absent et l'URL reste dans le résultat JSON renvoyé au LLM.
        """
        if result.get("status") != "ok":
            return
        emit = getattr(self, "_emit", None)
        if emit is not None:
            emit({
                "type": "file_ready",
                "filename": result["filename"],
                "download_url": result["download_url"],
                "size": result.get("size", 0),
                "kind": kind,
            })

    def _tool_generate_excel(self, a: dict) -> str:
        """Génère un classeur .xlsx téléchargeable."""
        from tools.excel import generate_excel
        result = generate_excel(a.get("filename", "export.xlsx"), a.get("sheets"))
        self._emit_file_ready(result, "xlsx")
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _tool_generate_text_file(self, a: dict) -> str:
        """Génère un fichier texte/code (.txt, .md, .py, .csv…) téléchargeable."""
        from tools.documents import generate_text_file
        result = generate_text_file(a.get("filename", "document.txt"), a.get("content", ""))
        kind = Path(result.get("filename", "")).suffix.lstrip(".").lower() or "txt"
        self._emit_file_ready(result, kind)
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _tool_bundle_zip(self, a: dict) -> str:
        """Regroupe plusieurs fichiers dans une archive .zip téléchargeable."""
        from tools.archive import bundle_zip
        result = bundle_zip(a.get("filename", "archive.zip"), a.get("files"))
        self._emit_file_ready(result, "zip")
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

    def _tool_ask_user(self, a: dict) -> str:
        """Pose UNE question à choix multiples à l'utilisateur et attend sa réponse.

        En mode API/UI, `self._ask_user` (posé par le serveur) ouvre une carte
        cliquable, met le tour en pause sur un Event et renvoie le choix. Sans
        canal (CLI/tests), on ne bloque PAS : on dégrade en demandant au modèle
        de poser la question en texte avec ses options et d'attendre (le QCM
        continue de fonctionner en mode dialogue, comme avant l'outil)."""
        question = (a.get("question") or "").strip()
        if not question:
            return "ERREUR: 'question' manquante pour ask_user."
        options = _normalize_ask_user_options(a.get("options"))
        allow_free_text = _coerce_bool_arg(a.get("allow_free_text", True))
        if not options and not allow_free_text:
            # Carte sans issue : ni choix cliquable, ni saisie libre.
            return ("ERREUR: ask_user requiert au moins une option non vide, "
                    "ou allow_free_text=true pour une réponse libre.")

        ask = getattr(self, "_ask_user", None)
        if ask is None:
            # Pas de canal interactif (CLI/tests) : repli texte.
            opts = "\n".join(f"  - {o}" for o in options) if options else ""
            return (
                "(Mode non-interactif : pas de fenêtre cliquable disponible.) "
                "Pose cette question à l'utilisateur EN TEXTE, avec ses options, "
                "puis attends sa réponse avant de continuer.\n"
                f"Question : {question}" + (f"\nOptions :\n{opts}" if opts else "")
            )
        answer = ask(question, options, allow_free_text)
        answer = (answer or "").strip()
        if not answer:
            return "L'utilisateur n'a pas répondu (aucun choix). Reformule ou propose de passer."
        return f"Réponse de l'utilisateur : {answer}"

    def _tools_for_run(self) -> list[dict]:
        """Outils proposés au modèle pour le tour courant.

        `ask_user` n'est exposé QUE pour un skill interactif (QCM) : ailleurs,
        l'agent reste autonome et ne doit pas pouvoir interrompre une tâche de
        code par des questions. Le handler existe en permanence dans le dispatch
        (inoffensif), mais l'outil reste invisible au modèle hors de ce mode."""
        if getattr(self, "_interactive_skill_active", False):
            return [*self.tools, ASK_USER_TOOL]
        return self.tools

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

        elif tool_name == "library_catalog":
            query = tool_args.get("query", "")
            miss = result.startswith(("Aucun", "Catalogue", "Erreur", "Requête"))
            console.print(Panel(
                f"[{'yellow' if miss else 'cyan'}]{result}[/]",
                title=f"[magenta]📖 catalogue: {query[:50]}[/magenta]",
                border_style="yellow" if miss else "magenta",
            ))

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

        elif tool_name in ("learn_from_books", "distill_theme"):
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

    def _trimmed_messages_for_synthesis(self) -> list[dict]:
        """Contexte rogné pour la synthèse finale de secours.

        En fin de boucle (cap d'itérations / coupe anti-boucle) le contexte est
        souvent saturé par des dumps d'outils (lectures de fichiers en rafale) —
        le LLM peut alors ne RIEN émettre (cf. gotcha budget de contexte). On
        tronque le contenu des messages `tool` volumineux (on garde l'id et le
        nom → l'appariement tool_call/tool_result reste valide) pour rendre de
        l'air au modèle sans casser l'historique.
        """
        trimmed: list[dict] = []
        for m in self.memory.get_messages_for_api():
            if m.get("role") == "tool":
                content = m.get("content") or ""
                if len(content) > 300:
                    m = {**m, "content": content[:300] + " …[tronqué pour synthèse]"}
            trimmed.append(m)
        return trimmed

    def _forced_final_synthesis(self, reason: str = "") -> str:
        """Force une réponse finale SANS outils et GARANTIT un message non vide.

        Appelé quand la boucle est coupée (cap d'itérations, anti-boucle,
        anti-scan). Le correctif historique (synthèse forcée) ne protégeait que
        si le LLM rendait du texte : un retour vide laissait l'utilisateur sur un
        écran blanc — récurrence du symptôme « il s'arrête, je dois relancer ».
        Ici on : (1) tente la synthèse, thinking OFF (le canal raisonnement peut
        absorber tout le budget de sortie) ; (2) si vide, réessaie sur un contexte
        rogné (cause probable = saturation) ; (3) si toujours vide, écrit un
        message de repli déterministe. L'utilisateur voit TOUJOURS quelque chose.
        """
        final_content, _ = self.llm.stream_chat(
            self.memory.get_messages_for_api(), tools=None, enable_thinking=False,
        )
        if final_content and final_content.strip():
            self.memory.add_message("assistant", final_content)
            return final_content

        logger.warning("Synthèse finale vide → retry sur contexte rogné")
        final_content, _ = self.llm.stream_chat(
            self._trimmed_messages_for_synthesis(), tools=None, enable_thinking=False,
        )
        if final_content and final_content.strip():
            self.memory.add_message("assistant", final_content)
            return final_content

        logger.warning("Synthèse finale toujours vide → message de repli déterministe")
        fallback = (
            "J'ai épuisé mon budget d'outils sur cette tâche sans aboutir à une "
            "réponse complète. "
            + (reason + " " if reason else "")
            + "Dis-moi sur quel fichier ou dossier précis me concentrer, ou "
            "reformule ta demande : je repartirai de là."
        )
        self.memory.add_message("assistant", fallback)
        console.print(f"\n[yellow]{fallback}[/yellow]")
        return fallback

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
        # Défaut : pas de skill interactif (réévalué dans le bloc routeur).
        self._interactive_skill_active = False

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
                # Skill interactif (QCM) : actif dès que le skill how-to routé #1
                # est interactif, QUEL QUE SOIT le task_type. Une demande de
                # conception (« aide-moi à concevoir l'algorithme de mon jeu »)
                # est classée `explain`, PAS `feature` — gater sur
                # _CODE_TASK_TYPES privait justement le QCM de l'outil `ask_user`
                # (questions déversées en texte au lieu de cartes cliquables,
                # cf. session 04:46). Les faux positifs sont déjà écartés en
                # amont par select_skills (filtre de pertinence IDF : un skill
                # non pertinent n'atteint jamais la tête de liste).
                self._interactive_skill_active = self._detect_interactive_skill(
                    user_input
                )
                # Routage modèle : tâche de code → modèle coder, sinon généraliste.
                # Un skill interactif force le généraliste (le coder-slim
                # n'injecte aucun skill, le QCM serait perdu).
                self._route_model(
                    decision.task_type,
                    force_generalist=self._interactive_skill_active,
                )
            except Exception as exc:
                logger.warning("Router failed, using defaults: %s", exc)

        # Injecte le system prompt avec les skills pertinents pour CE prompt
        # (+ task_type si le router a tranché). Toujours exécuté → le filtrage par
        # pertinence s'applique même quand le router est désactivé.
        self._inject_system_prompt(task_type=task_type_for_prompt, query=user_input)

        iteration = -1
        extensions = 0
        tools_called_in_pass = False
        produced_in_pass = False
        # Anti-boucle : compteur d'appels (nom+args) identiques sur tout le tour.
        call_repeat_counts: dict[str, int] = {}
        loop_warned: set[str] = set()
        # Anti-scan : compteur par NOM d'outil (indépendant des args/résultat) sur
        # tout le tour. Capte la lecture errante que l'anti-boucle ci-dessus rate
        # (40 fichiers différents = 40 clés distinctes).
        tool_name_counts: dict[str, int] = {}
        scan_warned: set[str] = set()
        # Anti-écho : série consécutive du même appel PRODUCTEUR (nom+args,
        # résultat ignoré — cf. commentaire de _ECHO_REPEAT_WARN).
        producer_echo_key: str | None = None
        producer_echo_count = 0
        echo_warned: set[str] = set()
        while True:
            iteration += 1
            if iteration >= max_iter:
                # Budget épuisé sans réponse finale. Auto-continue (A) : si la tâche
                # est actionnable (elle DOIT produire des changements), que l'agent
                # travaillait encore (tools appelés) et qu'il reste des extensions,
                # on prolonge au lieu d'arrêter et de forcer l'utilisateur à relancer.
                # Les tâches `explain`/`edit` ne sont pas prolongées (une boucle de
                # lecture sans fin est un stall) SAUF si la passe a appelé un outil
                # producteur (cf. produced_in_pass) : alors l'agent fabrique un
                # artefact et mérite le même filet qu'une tâche actionnable — typiquement
                # un suivi « tu as fini ? » routé `explain` qui tente une preview et
                # tombe sur une erreur récupérable.
                is_actionable = (
                    self.last_routing is not None
                    and self.last_routing.task_type
                    in ("feature", "refactor", "self_dev", "bug_fix")
                )
                if (is_actionable or produced_in_pass) and tools_called_in_pass and extensions < _MAX_AUTO_EXTENSIONS:
                    extensions += 1
                    max_iter += _AUTO_EXTENSION_SIZE
                    tools_called_in_pass = False
                    produced_in_pass = False
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
                # Budget épuisé sans auto-continue (tâche non-actionable type
                # `explain`, ou extensions épuisées) : NE PAS break-er en silence.
                # Sinon l'utilisateur ne voit plus rien après le dernier tool result
                # et DOIT relancer pour obtenir une réponse (symptôme « il s'arrête,
                # je suis obligé de le relancer »). On FORCE une réponse finale : un
                # appel LLM SANS outils, qui oblige le modèle à conclure avec ce qu'il
                # a déjà au lieu de retenter un tool. Même stream_chat → streamé à l'UI.
                console.print(
                    f"\n[yellow]  ⚠  Limite d'itérations atteinte ({max_iter}) — "
                    f"synthèse de la réponse finale.[/yellow]"
                )
                logger.warning("Limite d'itérations: %d → réponse finale forcée", max_iter)
                self.memory.messages.append({
                    "role": "user",
                    "content": (
                        "Tu as atteint la limite d'outils pour ce tour. N'appelle plus "
                        "aucun outil : rédige maintenant ta réponse finale à l'utilisateur "
                        "à partir de ce que tu as déjà recueilli. Si tu n'as pas trouvé ce "
                        "qui était demandé, dis-le clairement et donne la meilleure réponse "
                        "possible avec tes connaissances."
                    ),
                    "timestamp": None,
                })
                self._forced_final_synthesis(
                    reason="J'ai atteint la limite d'itérations pour ce tour."
                )
                break

            if iteration > 0:
                console.print(
                    f"\n[dim]  ⟳  Itération {iteration + 1}/{max_iter}[/dim]"
                )

            messages = self.memory.get_messages_for_api()

            # Best-of-N (Roadmap v2 #7) : 1ère itération des tâches hard, où la
            # stratégie initiale est critique. Désactivé quand le coder dédié
            # est routé (cf. _should_run_best_of_n) et pour un skill interactif
            # (un Q&A ne doit pas générer N candidats, et BoN n'expose pas ask_user).
            use_bon = self._should_run_best_of_n(iteration) and not getattr(
                self, "_interactive_skill_active", False
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
                budget = self._thinking_budget()
                content, tool_calls = self.llm.stream_chat(
                    messages, tools=self._tools_for_run(), tool_choice=tool_choice,
                    enable_thinking=self._should_think(),
                    thinking_budget=budget or None,
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
                _worst_n_local = 0
                _worst_name_local: str | None = None
                _scan_n_local = 0
                _scan_name_local: str | None = None
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

                    # Anti-boucle : on compte les appels (nom+args) qui rendent le
                    # MÊME résultat. Un sondage (statut_generation) voit son résultat
                    # évoluer (progression) → clé différente → aucun faux positif ;
                    # un échec à l'identique (proxy mort, script cassé) garde la même
                    # clé et fait grimper le compteur jusqu'au seuil de coupe.
                    try:
                        _args_key = json.dumps(tool_args, sort_keys=True, ensure_ascii=False)
                    except (TypeError, ValueError):
                        _args_key = repr(tool_args)
                    _loop_key = f"{tool_name}|{_args_key}|{hash(str(result))}"
                    call_repeat_counts[_loop_key] = call_repeat_counts.get(_loop_key, 0) + 1
                    if call_repeat_counts[_loop_key] > _worst_n_local:
                        _worst_n_local = call_repeat_counts[_loop_key]
                        _worst_name_local = tool_name

                    # Anti-écho : série consécutive du même appel producteur.
                    # Résultat volontairement ignoré : réémis à l'identique,
                    # preview_code « réussit » à chaque fois avec une URL neuve.
                    if tool_name in _PRODUCING_TOOLS:
                        _echo_key = f"{tool_name}|{_args_key}"
                        if _echo_key == producer_echo_key:
                            producer_echo_count += 1
                        else:
                            producer_echo_key = _echo_key
                            producer_echo_count = 1

                    # Anti-scan : compte par NOM seul, restreint aux outils
                    # d'exploration (un producteur appelé en rafale = travail réel,
                    # pas une errance). Clé indépendante des args → capte le balayage
                    # de fichiers distincts.
                    if tool_name not in _PRODUCING_TOOLS:
                        tool_name_counts[tool_name] = tool_name_counts.get(tool_name, 0) + 1
                        if tool_name_counts[tool_name] > _scan_n_local:
                            _scan_n_local = tool_name_counts[tool_name]
                            _scan_name_local = tool_name

                tools_called_in_pass = True
                if any(tc["function"]["name"] in _PRODUCING_TOOLS for tc in tool_calls):
                    produced_in_pass = True
                # Boucle de feedback : si la preview plante au runtime, on relance
                # une passe de correction (no-op si pas de preview ou timeout désactivé).
                self._check_preview_feedback(_preview_url, _preview_since)

                # Anti-scan : même OUTIL d'exploration répété en rafale (args
                # variés) = lecture errante. Au WARN on pousse vers list_files /
                # find_relevant_files ; au BREAK on coupe et on synthétise avant
                # d'atteindre le cap d'itérations (la storm de read_file observée).
                if _scan_name_local is not None and _scan_n_local >= _SCAN_REPEAT_BREAK:
                    logger.warning(
                        "[anti-scan] %s appelé %d× (args variés) → coupe + synthèse",
                        _scan_name_local, _scan_n_local)
                    console.print(
                        f"\n[yellow]  ⚠  Balayage détecté — `{_scan_name_local}` appelé "
                        f"{_scan_n_local}× sur des cibles variées. Arrêt et synthèse.[/yellow]"
                    )
                    self.memory.messages.append({
                        "role": "user",
                        "content": (
                            f"STOP. Tu as appelé `{_scan_name_local}` {_scan_n_local} fois "
                            "sur des cibles différentes sans converger : tu balaies au lieu "
                            "de cibler. N'appelle plus AUCUN outil. Explique à l'utilisateur "
                            "ce que tu cherchais, ce que tu as trouvé jusqu'ici, et demande "
                            "le chemin précis (fichier/dossier) si l'info manque. Rédige "
                            "maintenant ta réponse finale."
                        ),
                        "timestamp": None,
                    })
                    self._forced_final_synthesis()
                    break
                if (
                    _scan_name_local is not None
                    and _scan_n_local >= _SCAN_REPEAT_WARN
                    and _scan_name_local not in scan_warned
                ):
                    scan_warned.add(_scan_name_local)
                    logger.info("[anti-scan] %s appelé %d× → nudge ciblage injecté",
                                _scan_name_local, _scan_n_local)
                    self.memory.messages.append({
                        "role": "user",
                        "content": (
                            f"⚠ Tu as appelé `{_scan_name_local}` {_scan_n_local} fois sur "
                            "des cibles différentes : tu balaies des fichiers au hasard. "
                            "Arrête de lire à l'aveugle. Utilise `list_files` pour cadrer le "
                            "dossier, `find_relevant_files`/`search_in_files` pour localiser "
                            "par contenu, puis ne lis QUE les fichiers pertinents. Si tu ne "
                            "trouves pas, demande le chemin à l'utilisateur."
                        ),
                        "timestamp": None,
                    })

                # Anti-boucle : même appel + même résultat répété en rafale = le LLM
                # tourne sans avancer (souvent un échec identique). Au seuil WARN on
                # l'avertit une fois (change d'approche) ; au seuil BREAK on coupe et
                # on force la synthèse finale au lieu de laisser tourner jusqu'à
                # max_iter (la « boucle » visible côté utilisateur).
                if _worst_name_local is not None and _worst_n_local >= _LOOP_REPEAT_BREAK:
                    logger.warning(
                        "[anti-boucle] %s appelé %d× à l'identique (même résultat) "
                        "→ coupe + synthèse finale", _worst_name_local, _worst_n_local)
                    console.print(
                        f"\n[yellow]  ⚠  Boucle détectée — `{_worst_name_local}` répété "
                        f"{_worst_n_local}× sans changement. Arrêt et synthèse.[/yellow]"
                    )
                    self.memory.messages.append({
                        "role": "user",
                        "content": (
                            f"STOP. Tu as appelé `{_worst_name_local}` {_worst_n_local} fois "
                            "avec exactement les mêmes arguments et obtenu le même résultat à "
                            "chaque fois. N'appelle plus AUCUN outil. Explique à l'utilisateur "
                            "ce que tu as tenté, le résultat obtenu (notamment toute erreur "
                            "rencontrée), et propose une autre piste ou demande une précision. "
                            "Rédige maintenant ta réponse finale."
                        ),
                        "timestamp": None,
                    })
                    self._forced_final_synthesis()
                    break
                if (
                    _worst_name_local is not None
                    and _worst_n_local >= _LOOP_REPEAT_WARN
                    and _worst_name_local not in loop_warned
                ):
                    loop_warned.add(_worst_name_local)
                    logger.info("[anti-boucle] %s répété %d× → avertissement injecté",
                                _worst_name_local, _worst_n_local)
                    self.memory.messages.append({
                        "role": "user",
                        "content": (
                            f"⚠ Tu viens d'appeler `{_worst_name_local}` {_worst_n_local} fois "
                            "avec les mêmes arguments et tu obtiens le même résultat à chaque "
                            "fois. Arrête de répéter cet appel à l'identique : change "
                            "d'arguments, essaie une autre approche, ou si c'est un échec que "
                            "tu ne peux pas résoudre, explique-le à l'utilisateur au lieu de "
                            "retenter."
                        ),
                        "timestamp": None,
                    })

                # Anti-écho producteur : même appel réémis en série, résultat
                # ignoré — là où l'anti-boucle ci-dessus exige un résultat
                # identique et reste aveugle quand chaque réémission « réussit »
                # (cf. _ECHO_REPEAT_WARN, incident « canard 3D » du 03/07).
                if producer_echo_count >= _ECHO_REPEAT_BREAK:
                    _echo_tool = (producer_echo_key or "").split("|", 1)[0]
                    logger.warning(
                        "[anti-écho] %s réémis %d× à l'identique (résultats "
                        "distincts) → coupe + synthèse finale",
                        _echo_tool, producer_echo_count)
                    console.print(
                        f"\n[yellow]  ⚠  Écho détecté — `{_echo_tool}` réémis "
                        f"{producer_echo_count}× avec les mêmes arguments. "
                        "Arrêt et synthèse.[/yellow]"
                    )
                    self.memory.messages.append({
                        "role": "user",
                        "content": (
                            f"STOP. Tu as émis {producer_echo_count} fois de suite le même "
                            f"appel `{_echo_tool}` avec exactement les mêmes arguments : tu "
                            "refabriques le même artefact en boucle. N'appelle plus AUCUN "
                            "outil. Explique à l'utilisateur ce que tu essayais de produire "
                            "et ce qui bloque (par exemple un paramètre que tu n'arrives pas "
                            "à remplir), puis rédige maintenant ta réponse finale."
                        ),
                        "timestamp": None,
                    })
                    self._forced_final_synthesis()
                    break
                if (
                    producer_echo_key is not None
                    and producer_echo_count >= _ECHO_REPEAT_WARN
                    and producer_echo_key not in echo_warned
                ):
                    echo_warned.add(producer_echo_key)
                    _echo_tool = producer_echo_key.split("|", 1)[0]
                    logger.info("[anti-écho] %s réémis %d× → avertissement injecté",
                                _echo_tool, producer_echo_count)
                    self.memory.messages.append({
                        "role": "user",
                        "content": (
                            f"⚠ Tu viens d'émettre {producer_echo_count} fois de suite "
                            f"EXACTEMENT le même appel `{_echo_tool}` (mêmes arguments) : le "
                            "réémettre reproduira le même artefact. Relis les avertissements "
                            "du dernier résultat d'outil et vérifie tes arguments — l'un "
                            "d'eux est peut-être vide ou tronqué (par exemple le code oublié "
                            "dans `js`). Corrige l'appel ou change d'approche ; ne réémets "
                            "pas le même appel à l'identique."
                        ),
                        "timestamp": None,
                    })
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
                # Un skill interactif (QCM) répond LÉGITIMEMENT en texte (il pose
                # ses questions et attend) : ne pas confondre avec un plan annoncé
                # sans action. On garde toutefois le filet « réponse vide », qui
                # reste un vrai stall même en mode interactif.
                interactive = getattr(self, "_interactive_skill_active", False)
                stalled = (
                    actionable_mode
                    and not getattr(self, "_anti_stall_fired", False)
                    and iteration < max_iter - 1
                    and (
                        (_looks_like_unfinished_plan(content) and not interactive)
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
                    # Skill interactif : sa réponse texte EST le livrable (questions
                    # du QCM) — ne pas la détourner en exécution de code.
                    and not getattr(self, "_interactive_skill_active", False)
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
                # Auto-critique de la réponse finale (Levier 3) : no-op si désactivé,
                # tâche non-raisonnement, coder, skill interactif ou réponse triviale.
                self._maybe_self_critique(content)
                break

        # Reset les flags pour le prochain run (chaque appel utilisateur est neuf)
        self._anti_stall_fired = False
        self._anti_stall_iter = -1
        self._t2a_fired = False
        self._self_critique_done = False
        self._interactive_skill_active = False

    def _mid_session_extract(self) -> None:
        """Extraction mid-session en arrière-plan."""
        try:
            facts = extract_mid_session(self.memory.messages, self.lt_memory)
            if facts:
                logger.info("[Orchestrator] Mid-session: %d fait(s) extraits", len(facts))
        except Exception as e:
            logger.debug("[Orchestrator] Mid-session extract error: %s", e)
