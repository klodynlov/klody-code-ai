import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- LLM ---
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_API_KEY: str = os.getenv("OLLAMA_API_KEY", "ollama")
MODEL_NAME: str = os.getenv("MODEL_NAME", "qwen2.5-coder:32b")
MODEL_FALLBACK: str = os.getenv("MODEL_FALLBACK", "qwen2.5-coder:7b")

# --- Sandbox ---
PROJECT_ROOT: Path = Path(os.getenv("PROJECT_ROOT", ".")).resolve()

# --- Limites ---
MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", 1024 * 1024))  # 1 MB
MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", 10))
MAX_MESSAGES: int = int(os.getenv("MAX_MESSAGES", 50))
SUBPROCESS_TIMEOUT: int = int(os.getenv("SUBPROCESS_TIMEOUT", 30))

# --- LibraryBrain / MCP ---
LIBRARYBRAIN_URL: str = os.getenv("LIBRARYBRAIN_URL", "http://127.0.0.1:8765/api/ask")
LIBRARYBRAIN_DIR: str = os.getenv("LIBRARYBRAIN_DIR", "")  # chemin vers le dépôt library-brain
MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8082/mcp")

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
