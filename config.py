import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- LLM Backend ---
# BACKEND=ollama (défaut) | mlx
# En mode mlx : MLX_BASE_URL + MLX_MODEL sont utilisés.
# En mode ollama : OLLAMA_BASE_URL + MODEL_NAME sont utilisés.
BACKEND: str = os.getenv("BACKEND", "ollama")

# Ollama
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_API_KEY: str  = os.getenv("OLLAMA_API_KEY", "ollama")
MODEL_NAME: str      = os.getenv("MODEL_NAME", "qwen3.5:9b")
MODEL_FALLBACK: str  = os.getenv("MODEL_FALLBACK", "qwen3.5:9b")

# MLX (Apple Silicon — mlx_lm.server)
MLX_BASE_URL: str = os.getenv("MLX_BASE_URL", "http://localhost:8080/v1")
MLX_API_KEY: str  = os.getenv("MLX_API_KEY", "mlx")
MLX_MODEL: str    = os.getenv("MLX_MODEL", "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2")
MLX_DRAFT_MODEL: str = os.getenv("MLX_DRAFT_MODEL", "")  # speculative decoding (optionnel)

# Résolution active selon BACKEND
LLM_BASE_URL: str = MLX_BASE_URL if BACKEND == "mlx" else OLLAMA_BASE_URL
LLM_API_KEY: str  = MLX_API_KEY  if BACKEND == "mlx" else OLLAMA_API_KEY
LLM_MODEL: str    = MLX_MODEL    if BACKEND == "mlx" else MODEL_NAME

# MLX — modèle CODE dédié. Les tâches de code (edit/refactor/bug_fix/feature/
# self_dev) sont routées dessus : un modèle coder émet de bien meilleurs gros
# blocs de code qu'un généraliste (cf. orchestrator._route_model). Serveur
# mlx_lm.server séparé sur son propre port. MLX_CODE_MODEL vide → routage
# désactivé, tout reste sur LLM_MODEL.
MLX_CODE_MODEL: str    = os.getenv("MLX_CODE_MODEL", "")
MLX_CODE_PORT: str     = os.getenv("MLX_CODE_PORT", "8081")
MLX_CODE_BASE_URL: str = os.getenv("MLX_CODE_BASE_URL", f"http://localhost:{MLX_CODE_PORT}/v1")
MLX_CODE_API_KEY: str  = os.getenv("MLX_CODE_API_KEY", MLX_API_KEY)

# Modèle code actif (backend mlx uniquement ; vide en ollama ou si non configuré).
CODE_MODEL: str    = MLX_CODE_MODEL if BACKEND == "mlx" else ""
CODE_BASE_URL: str = MLX_CODE_BASE_URL
CODE_API_KEY: str  = MLX_CODE_API_KEY

# --- Sandbox ---
PROJECT_ROOT: Path = Path(os.getenv("PROJECT_ROOT", ".")).resolve()

# --- Limites ---
MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", 1024 * 1024))  # 1 MB
MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", 25))
MAX_MESSAGES: int = int(os.getenv("MAX_MESSAGES", 50))
# Fenêtre de contexte du modèle (tokens) — sert à la jauge de contexte de l'UI.
CONTEXT_WINDOW: int = int(os.getenv("CONTEXT_WINDOW", 32768))
# Réserves soustraites de CONTEXT_WINDOW pour borner la fenêtre glissante des
# messages. Le prompt RÉEL envoyé au modèle = system + messages + SCHÉMAS D'OUTILS
# (passés à part, ~8k pour 38 outils internes + MCP) ; il faut EN PLUS laisser de
# quoi GÉNÉRER la réponse (max_tokens). Sans ces réserves, un long échange sature
# la fenêtre (jauge ~32k/32.8k) → plus de place pour répondre → génération vide/
# bloquée et WS qui lâche. budget_messages = CONTEXT_WINDOW − TOOLS − RESPONSE.
CONTEXT_TOOLS_RESERVE: int = int(os.getenv("CONTEXT_TOOLS_RESERVE", 8192))
CONTEXT_RESPONSE_RESERVE: int = int(os.getenv("CONTEXT_RESPONSE_RESERVE", 4096))
SUBPROCESS_TIMEOUT: int = int(os.getenv("SUBPROCESS_TIMEOUT", 30))

# --- Sandbox (Roadmap v2 #3) ---
# Auto-exec après chaque write_file sur un .py (pytest/python/py_compile selon contenu).
SANDBOX_AUTO_EXEC: bool = os.getenv("SANDBOX_AUTO_EXEC", "true").lower() in ("1", "true", "yes", "on")
SANDBOX_TIMEOUT: int = int(os.getenv("SANDBOX_TIMEOUT", 20))

# --- Router adaptatif (Roadmap v2 #4) ---
# Classifie le prompt avant la boucle ReAct → adapte max_iterations + stratégie.
ROUTER_ENABLED: bool = os.getenv("ROUTER_ENABLED", "true").lower() in ("1", "true", "yes", "on")

# --- Best-of-N (Roadmap v2 #7) ---
# Génère N candidats + reranker LLM-as-judge sur la 1ère itération des tâches hard.
# Cost : (N+1) appels LLM au lieu de 1, déclenché UNIQUEMENT si router.use_best_of_n=True.
BEST_OF_N_ENABLED: bool = os.getenv("BEST_OF_N_ENABLED", "true").lower() in ("1", "true", "yes", "on")
BEST_OF_N_COUNT: int = int(os.getenv("BEST_OF_N_COUNT", 3))
# Override : force Best-of-N quelle que soit la décision du router. Utile pour
# l'évaluation A/B (mesurer le gain réel sur des tâches que le router n'aurait
# pas classifiées hard).
BEST_OF_N_FORCE: bool = os.getenv("BEST_OF_N_FORCE", "false").lower() in ("1", "true", "yes", "on")

# --- LibraryBrain / MCP ---
LIBRARYBRAIN_URL: str = os.getenv("LIBRARYBRAIN_URL", "http://127.0.0.1:8765/api/ask")
LIBRARYBRAIN_DIR: str = os.getenv("LIBRARYBRAIN_DIR", "")  # chemin vers le dépôt library-brain
MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8082/mcp")


# --- Client MCP : serveurs externes que Klody peut CONSOMMER ---
# Klody se connecte à ces serveurs MCP, découvre leurs outils et les expose au
# LLM (noms namespacés mcp__<serveur>__<outil>). Format env KLODY_MCP_SERVERS :
# un JSON {nom: cible} où cible est une URL HTTP ou un chemin de script.
#   KLODY_MCP_SERVERS='{"gmail":"http://127.0.0.1:8084/mcp"}'
# Vide par défaut (aucun serveur). Un serveur injoignable est ignoré au boot.
def _parse_mcp_servers(raw: str) -> dict:
    import json
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): v for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        logging.getLogger(__name__).warning(
            "KLODY_MCP_SERVERS: JSON invalide, ignoré : %r", raw[:120]
        )
        return {}


MCP_SERVERS: dict = _parse_mcp_servers(os.getenv("KLODY_MCP_SERVERS", ""))

# --- GitHub ---
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# --- Projets ---
PROJECTS_DIR: Path = Path(os.getenv("PROJECTS_DIR", str(Path.home() / "Projets"))).resolve()
PYCHARM_CMD: str = os.getenv("PYCHARM_CMD", "/usr/local/bin/pycharm")

# --- Sandbox multi-racines (lecture/écriture) ---
# Liste de dossiers (séparés par os.pathsep, ':' sur macOS/Linux) où Klody peut
# lire et écrire. Le projet courant (PROJECT_ROOT) est toujours autorisé même
# absent de la liste ; tout chemin hors de ces racines est refusé. Le blocage
# des fichiers sensibles (.env, clés, certificats) reste actif partout.
# Défaut : PROJECT_ROOT + PROJECTS_DIR. Exemple .env :
#   ALLOWED_ROOTS=/Users/moi/Projets:/Users/moi/work:/Users/moi/sites
def _parse_roots(raw: str) -> list[Path]:
    roots: list[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        resolved = Path(part).expanduser().resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


ALLOWED_ROOTS: list[Path] = _parse_roots(
    os.getenv("ALLOWED_ROOTS", os.pathsep.join([str(PROJECT_ROOT), str(PROJECTS_DIR)]))
)


def build_allowed_roots(primary: Path, extra: list[Path] | None = None) -> list[Path]:
    """Racines autorisées d'un outil : `primary` en tête (toujours autorisée),
    puis les racines `extra` (défaut ALLOWED_ROOTS), dédupliquées."""
    primary = primary.resolve()
    roots: list[Path] = [primary]
    for r in (ALLOWED_ROOTS if extra is None else extra):
        rr = Path(r).resolve()
        if rr not in roots:
            roots.append(rr)
    return roots


def match_allowed_root(resolved: Path, roots: list[Path]) -> Path | None:
    """Première racine de `roots` contenant `resolved`, sinon None."""
    for root in roots:
        try:
            resolved.relative_to(root)
            return root
        except ValueError:
            continue
    return None

# --- Preview ---
PREVIEW_DIR: Path = Path(os.getenv("PREVIEW_DIR", str(Path(__file__).parent / "_preview"))).resolve()
PREVIEW_PORT: int = int(os.getenv("PREVIEW_PORT", 8899))
# Boucle de feedback : délai (s) d'attente des erreurs JS runtime après une
# preview avant de relancer une passe de correction. 0 = désactivé (défaut, sûr
# pour les tests). À activer en live via .env (ex. 3.0). Cf. agent.preview_errors.
PREVIEW_FEEDBACK_TIMEOUT_S: float = float(os.getenv("PREVIEW_FEEDBACK_TIMEOUT_S", "0"))

# --- Chemins ---
_ROOT: Path = Path(__file__).parent
LOG_DIR: Path = _ROOT / "logs"
LOG_FILE: Path = LOG_DIR / "agent.log"
MEMORY_DIR: Path = LOG_DIR
SKILLS_DIR: Path = _ROOT / "skills"
# Artefacts générés téléchargeables (Excel, etc.), servis par l'API sur
# /api/files/<nom>. Dossier dédié et gitignoré : on n'y sert QUE des fichiers
# produits par les outils, jamais des fichiers du projet.
DOWNLOADS_DIR: Path = Path(os.getenv("DOWNLOADS_DIR", str(_ROOT / "_downloads"))).resolve()

LOG_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR.mkdir(exist_ok=True)

# --- Logging : fichier uniquement, ne pas polluer le terminal Rich ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
