import os
import logging
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

# --- Sandbox ---
PROJECT_ROOT: Path = Path(os.getenv("PROJECT_ROOT", ".")).resolve()

# --- Limites ---
MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", 1024 * 1024))  # 1 MB
MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", 10))
MAX_MESSAGES: int = int(os.getenv("MAX_MESSAGES", 50))
SUBPROCESS_TIMEOUT: int = int(os.getenv("SUBPROCESS_TIMEOUT", 30))

# --- Sandbox (Roadmap v2 #3) ---
# Auto-exec après chaque write_file sur un .py (pytest/python/py_compile selon contenu).
SANDBOX_AUTO_EXEC: bool = os.getenv("SANDBOX_AUTO_EXEC", "true").lower() in ("1", "true", "yes", "on")
SANDBOX_TIMEOUT: int = int(os.getenv("SANDBOX_TIMEOUT", 20))

# --- LibraryBrain / MCP ---
LIBRARYBRAIN_URL: str = os.getenv("LIBRARYBRAIN_URL", "http://127.0.0.1:8765/api/ask")
LIBRARYBRAIN_DIR: str = os.getenv("LIBRARYBRAIN_DIR", "")  # chemin vers le dépôt library-brain
MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8082/mcp")

# --- GitHub ---
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# --- Projets ---
PROJECTS_DIR: Path = Path(os.getenv("PROJECTS_DIR", str(Path.home() / "Projets"))).resolve()
PYCHARM_CMD: str = os.getenv("PYCHARM_CMD", "/usr/local/bin/pycharm")

# --- Preview ---
PREVIEW_DIR: Path = Path(os.getenv("PREVIEW_DIR", str(Path(__file__).parent / "_preview"))).resolve()
PREVIEW_PORT: int = int(os.getenv("PREVIEW_PORT", 8899))

# --- Chemins ---
_ROOT: Path = Path(__file__).parent
LOG_DIR: Path = _ROOT / "logs"
LOG_FILE: Path = LOG_DIR / "agent.log"
MEMORY_DIR: Path = LOG_DIR
SKILLS_DIR: Path = _ROOT / "skills"

LOG_DIR.mkdir(exist_ok=True)

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
