import logging
import os
from pathlib import Path

import httpx  # déjà tiré comme dépendance par openai
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

# MLX — modèle VISION (VL) dédié, exploité par l'outil `analyser_image`.
# Klody reste TEXTE de bout en bout : la vision est un outil À ARTEFACT (image →
# description renvoyée dans la boucle ReAct), PAS un changement du format des
# messages du cerveau. L'outil POSTe l'image (base64) au worker VL via le gateway
# Klody Core (mêmes :8090 que brain/coder ; le champ `model` route vers le worker
# mlx_vlm). VL_MODEL vide → outil enregistré mais désactivé (dégradation propre,
# message lisible — jamais d'exception). VL_MODEL = un alias gateway ("vision") ou
# l'id HF complet du modèle VL. Le client OpenAI de l'outil est DÉDIÉ : un appel
# d'outil ne détourne jamais le client de la boucle principale.
VL_MODEL: str    = os.getenv("VL_MODEL", "")
VL_BASE_URL: str = os.getenv("VL_BASE_URL", MLX_BASE_URL)
VL_API_KEY: str  = os.getenv("VL_API_KEY", MLX_API_KEY)
VL_MAX_TOKENS: int   = int(os.getenv("VL_MAX_TOKENS", "1024"))
VL_MAX_IMAGE_MB: float = float(os.getenv("VL_MAX_IMAGE_MB", "12"))

# --- Timeouts client LLM ---
# Le défaut du SDK OpenAI (timeout=600 s, max_retries=2) ferait attendre jusqu'à
# ~30 min si le serveur d'inférence local (MLX/Ollama) se fige. On coupe vite à la
# connexion et on ne retente PAS une génération en silence (un retry = re-générer
# tout le tour, coûteux et invisible). `read` reste large : c'est l'attente
# INTER-chunk pendant le stream (prefill d'un gros prompt + tokens), pas la durée
# totale de la génération — un long code ne déclenche donc pas de coupure.
LLM_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
LLM_MAX_RETRIES: int = 0

# --- Sandbox ---
PROJECT_ROOT: Path = Path(os.getenv("PROJECT_ROOT", ".")).resolve()

# --- Limites ---
MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", 1024 * 1024))  # 1 MB
MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", 25))
MAX_MESSAGES: int = int(os.getenv("MAX_MESSAGES", 50))
# Fenêtre de contexte (tokens) : sert À LA FOIS la jauge UI ET le budget de la
# fenêtre glissante des messages (cf. agent/memory._message_budget). 32k était un
# plafond ARTIFICIEL hérité d'Ollama : les modèles MLX servis ici (Qwen3.x-A3B)
# gèrent 256K natif, et une machine 64–128 Go a la RAM pour un large KV cache.
# 64k double le contexte utile sans prefill démesuré ; pousser à 131072 (128k) si
# la latence du 1er token reste acceptable. À régler aussi dans .env (runtime).
CONTEXT_WINDOW: int = int(os.getenv("CONTEXT_WINDOW", 65536))
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

# --- Outil SQL runtime (Roadmap v2 #10) ---
# Exécution SQL locale sandboxée (sqlite3). Le mode 'write' est DÉSACTIVÉ par défaut
# (sûr par défaut, comme GMAIL_READONLY) : l'outil ne peut que LIRE tant que ce flag
# n'est pas explicitement activé. Le confinement (racines autorisées, authorizer
# default-deny, verrou ATTACH, anti-DoS) s'applique dans les DEUX modes.
SQL_WRITE_ENABLED: bool = os.getenv("SQL_WRITE_ENABLED", "false").lower() in ("1", "true", "yes", "on")

# --- Outil Git runtime (git_control) ---
# Introspection Git toujours en lecture seule. Les mutations LOCALES (add, commit)
# sont DÉSACTIVÉES par défaut (sûr par défaut) ; `push`/`pull` et les opérations
# destructives (reset/checkout/clean/rebase) restent hors de l'outil quel que soit
# ce flag. Confinement (racines autorisées) + validation s'appliquent toujours.
GIT_WRITE_ENABLED: bool = os.getenv("GIT_WRITE_ENABLED", "false").lower() in ("1", "true", "yes", "on")

# --- Outil Docker runtime (docker_control) ---
# Introspection Docker toujours en lecture seule. `docker run` (mutation) est
# DÉSACTIVÉ par défaut ET doublement borné : il exige AUSSI une allowlist d'images
# non vide. Aucun flag utilisateur n'est accepté — l'outil impose un durcissement
# figé (--network none, --cap-drop ALL, no-new-privileges, limites ressources).
DOCKER_WRITE_ENABLED: bool = os.getenv("DOCKER_WRITE_ENABLED", "false").lower() in ("1", "true", "yes", "on")
# Images autorisées pour `docker run` (CSV). Vide = aucune (run refusé). Match par
# nom exact, par repo (sans tag) ou par préfixe. Ex: "python:3.12,alpine,ghcr.io/moi/".
DOCKER_ALLOWED_IMAGES: list[str] = [
    s.strip() for s in os.getenv("DOCKER_ALLOWED_IMAGES", "").split(",") if s.strip()
]

# --- Router adaptatif (Roadmap v2 #4) ---
# Classifie le prompt avant la boucle ReAct → adapte max_iterations + stratégie.
ROUTER_ENABLED: bool = os.getenv("ROUTER_ENABLED", "true").lower() in ("1", "true", "yes", "on")

# --- Mode raisonnement (thinking) ---
# Les modèles Qwen3 "thinking" (le brain) émettent une chaîne de raisonnement
# AVANT la réponse quand `chat_template_kwargs.enable_thinking=true`. Les serveurs
# mlx_lm sont lancés avec le thinking COUPÉ (--chat-template-args) ; Klody le
# RÉACTIVE par requête sur le brain pour les tâches de raisonnement (`explain` —
# le seul type qui reste sur le brain — ou difficulté `hard` ; cf. _should_think).
# Le CoT est diffusé à l'UI (panneau « Raisonnement… ») pour que l'attente ne soit
# pas un écran figé (A/B 08/06 : sans diffusion, TTFT aveugle jusqu'à 66 s).
THINKING_ENABLED: bool = os.getenv("THINKING_ENABLED", "true").lower() in ("1", "true", "yes", "on")
# Thinking sur le CODER : depuis la bascule Qwen3.6-35B-A3B-UD-4bit (03/07), le
# coder partage la base thinking du brain (lancé no-think par la gateway,
# réactivable PAR REQUÊTE exactement comme le brain). On ne raisonne que sur les
# tâches `hard` (cf. _should_think) : l'A/B coder du 03/07 (no-think 8/8 = 8/8)
# montre que le CoT ne vaut pas sa latence sur le code standard, mais une feature
# hard/créative sans CoT échoue (vécu « canard 3D » 03/07). Mettre à false si
# rollback vers un coder INSTRUCT sans mode thinking
# (KLODY_CORE_CODER_MODEL=mlx-community/Qwen3-Coder-Next-4bit).
THINKING_ON_CODER: bool = os.getenv("THINKING_ON_CODER", "true").lower() in ("1", "true", "yes", "on")
# Le raisonnement consomme beaucoup de tokens AVANT la réponse : on élargit le
# plafond de génération quand il est actif (sinon le CoT mange tout et la réponse
# n'a plus de place). Mesuré à l'A/B (08/06) : P95 du CoT ≈ 1800 tokens, donc 8192
# couvre largement réponse + CoT ; 16384 était du gâchis (latence/budget inutiles).
THINKING_MAX_TOKENS: int = int(os.getenv("THINKING_MAX_TOKENS", 8192))

# --- Thinking budget PAR TYPE DE TÂCHE (inspiré du node comfyui-llamacpp-ideogram) ---
# Le node de référence module le raisonnement via `thinking_budget_tokens` PAR
# requête. mlx_lm n'a AUCUN équivalent natif (vérifié 0.31.3 : ni paramètre serveur,
# ni variable `thinking_budget` dans le template Qwen3.6 — seul `enable_thinking` est
# honoré), ET ne permet pas de borner le CoT côté client sans troncature dure du flux
# (écartée : risque sur le format tool-call). Le plafond max_tokens ne sait qu'ÉLARGIR
# (`max()`), jamais réduire → moduler par là serait un no-op (défaut 8192 ≥ tous les
# tiers). On calcule donc un budget par type de tâche et on le FORWARDE dans
# chat_template_kwargs.thinking_budget : FORWARD-COMPAT (no-op aujourd'hui, effectif si
# un futur template l'honore). Cf. docs/thinking-budget-policy.md et
# Orchestrator._thinking_budget. budget == 0 ⇒ thinking OFF (aucun CoT).
# Tiers (valeur forwardée ; aucun effet sur max_tokens aujourd'hui) :
THINKING_BUDGET_NONE: int = 0
THINKING_BUDGET_LOW: int = int(os.getenv("THINKING_BUDGET_LOW", 512))
THINKING_BUDGET_MED: int = int(os.getenv("THINKING_BUDGET_MED", 2048))
# Le tier HAUT vaut THINKING_MAX_TOKENS par défaut (P95 du CoT ≈ 1800 tok ; cf. note
# THINKING_MAX_TOKENS) — valeur de référence forwardée pour le raisonnement profond.
THINKING_BUDGET_HIGH: int = int(os.getenv("THINKING_BUDGET_HIGH", THINKING_MAX_TOKENS))
# Forward du budget dans chat_template_kwargs.thinking_budget. Le template Qwen3.6
# ignore la clé (l'appel live passe sans erreur — clé inconnue = variable Jinja
# inutilisée, pas de 400). Gardé true pour la forward-compat ; désactivable.
THINKING_BUDGET_FORWARD: bool = os.getenv(
    "THINKING_BUDGET_FORWARD", "true"
).lower() in ("1", "true", "yes", "on")

# Pénalité de répétition transmise au serveur MLX (extra_body, hors spec OpenAI —
# le gateway :8090 forwarde le body intégral au worker mlx_lm qui la supporte).
# Filet anti-boucle OPT-IN (défaut 1.0 = désactivé, param non envoyé, comportement
# historique strictement préservé) : à température basse, une longue liste quasi
# identique (53 atomes d'une molécule…) fait partir le modèle en répétition
# dégénérée qui mange tout le budget de tokens sans jamais terminer (vécu 12/06 :
# « molécule de THC en 3D », 2 requêtes parties en boucle infinie). Valeur
# recommandée : 1.05 — à peine perceptible sur du code normal, casse les cycles.
LLM_REPETITION_PENALTY: float = float(os.getenv("LLM_REPETITION_PENALTY", "1.0"))

# Filet DUR anti-boucle (cf. agent/stream_guard.py) : coupe le STREAM dès que la
# fin de la réponse est un motif répété >= LLM_LOOP_REPS fois (chaque motif
# >= LLM_LOOP_MIN_UNIT chars). Complète la pénalité SOUPLE ci-dessus — qui ne
# casse pas toujours le cycle — et s'applique au chemin WS de l'UI (stream_api).
# Actif par défaut (opt-out via LLM_LOOP_GUARD=0) : ne se déclenche que sur une
# boucle franche (4× un motif de 16+ chars), jamais sur du code/Markdown normal.
LLM_LOOP_GUARD: bool = os.getenv("LLM_LOOP_GUARD", "1") not in ("0", "false", "False")
LLM_LOOP_REPS: int = int(os.getenv("LLM_LOOP_REPS", "4"))
LLM_LOOP_MIN_UNIT: int = int(os.getenv("LLM_LOOP_MIN_UNIT", "16"))
LLM_LOOP_WINDOW: int = int(os.getenv("LLM_LOOP_WINDOW", "2000"))

# --- Auto-critique (Levier 3) ---
# Après la réponse finale d'une tâche de raisonnement (explain/hard, sur le brain),
# une passe de relecture critique cherche erreur/oubli/hypothèse fausse et réécrit
# la réponse si besoin (sinon la garde telle quelle via le sentinel INCHANGÉ).
# COÛTE un appel LLM supplémentaire → OFF par défaut : à activer après un A/B au
# bench (cf. bench/run.py) plutôt qu'imposer la latence à chaque tâche.
SELF_CRITIQUE_ENABLED: bool = os.getenv("SELF_CRITIQUE_ENABLED", "false").lower() in ("1", "true", "yes", "on")

# --- Best-of-N (Roadmap v2 #7) ---
# Génère N candidats + reranker LLM-as-judge sur la 1ère itération des tâches hard.
# Cost : (N+1) appels LLM au lieu de 1, déclenché UNIQUEMENT si router.use_best_of_n=True.
BEST_OF_N_ENABLED: bool = os.getenv("BEST_OF_N_ENABLED", "true").lower() in ("1", "true", "yes", "on")
BEST_OF_N_COUNT: int = int(os.getenv("BEST_OF_N_COUNT", 3))
# Override : force Best-of-N quelle que soit la décision du router. Utile pour
# l'évaluation A/B (mesurer le gain réel sur des tâches que le router n'aurait
# pas classifiées hard).
BEST_OF_N_FORCE: bool = os.getenv("BEST_OF_N_FORCE", "false").lower() in ("1", "true", "yes", "on")

# --- Retrieval proactif (Levier 1c) ---
# Avant la boucle ReAct, on injecte dans le prompt les fichiers du projet
# sémantiquement proches de la requête (embeddings bge-m3, cf. tools/code_search),
# en PISTES à vérifier. Évite à l'agent d'explorer à l'aveugle / de deviner les
# fichiers. Best-effort : silencieux si l'index est indisponible (Ollama/bge-m3
# absent) ou en cas d'erreur. Le 1er tour d'une session paie la construction de
# l'index (puis incrémental). Mettre RETRIEVAL_INJECT_ENABLED=0 pour couper.
RETRIEVAL_INJECT_ENABLED: bool = os.getenv("RETRIEVAL_INJECT_ENABLED", "true").lower() in ("1", "true", "yes", "on")
RETRIEVAL_INJECT_K: int = int(os.getenv("RETRIEVAL_INJECT_K", 5))
# Seuil de similarité cosinus sous lequel un hit est jugé hors-sujet (filtre le
# bruit : sur une requête de pure conversation, aucun fichier n'est injecté).
RETRIEVAL_MIN_SCORE: float = float(os.getenv("RETRIEVAL_MIN_SCORE", 0.35))

# --- Routeur de skills sémantique (OPTIONNEL, cf. tools/skill_router.py) ---
# OFF par défaut : Klody reste offline-first (select_skills, IDF déterministe,
# zéro dépendance réseau). À ON, l'injection des skills couche A passe par
# SkillRouter (embeddings Ollama + juge LLM, avec repli automatique sur
# select_skills si un endpoint est indisponible). N'active rien au démarrage
# tant que le flag vaut 0.
SKILLS_ROUTER_ENABLED: bool = os.getenv("SKILLS_ROUTER_ENABLED", "false").lower() in ("1", "true", "yes", "on")
# Sous-flag : utiliser le juge LLM en plus des embeddings (sinon rang cosinus seul).
SKILLS_ROUTER_JUDGE: bool = os.getenv("SKILLS_ROUTER_JUDGE", "true").lower() in ("1", "true", "yes", "on")

# --- Skills sur tâches de code (OPT-IN) ---
# Par défaut, le modèle coder (Qwen3-Coder, complétion — dégénère sous un gros
# prompt) ne reçoit AUCUN skill. À ON, on autorise l'injection d'un sous-ensemble
# MINUSCULE : uniquement les skills explicitement marqués `code_compatible: true`
# ET jugés pertinents par select_skills (double garde), capés à SKILLS_ON_CODER_MAX
# et rendus COMPACTS (description + content tronqué — jamais le dump intégral qui
# réveille la dégénérescence). OFF → comportement actuel strictement préservé.
SKILLS_ON_CODER_ENABLED: bool = os.getenv("SKILLS_ON_CODER_ENABLED", "false").lower() in ("1", "true", "yes", "on")
# Nombre max de skills injectés au coder (garder très bas : 1, exceptionnellement 2).
SKILLS_ON_CODER_MAX: int = int(os.getenv("SKILLS_ON_CODER_MAX", 1))
# Plafond de caractères du `content` d'un skill injecté au coder (rendu compact).
SKILLS_ON_CODER_MAX_CHARS: int = int(os.getenv("SKILLS_ON_CODER_MAX_CHARS", 800))

# --- LibraryBrain / MCP ---
LIBRARYBRAIN_URL: str = os.getenv("LIBRARYBRAIN_URL", "http://127.0.0.1:8765/api/ask")
LIBRARYBRAIN_DIR: str = os.getenv("LIBRARYBRAIN_DIR", "")  # chemin vers le dépôt library-brain
# DB SQLite de Library Brain (index FTS5 des livres) — lue DIRECTEMENT (lecture
# seule) par tools/library_distiller.py, sans passer par le serveur :8765.
LIBRARY_DB_PATH: Path = Path(os.getenv("LIBRARY_DB_PATH", str(Path.home() / "library_brain.db")))
MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8082/mcp")

# Serveur MCP PROPRE de Klody (klody_mcp.klody_server) — expose les outils de
# Klody à des clients externes (Claude Desktop, Continue.dev…). Port HTTP par
# défaut 8087 : 8083 entrait en collision avec MLX_CODE_PORT (modèle code MLX),
# ce qui empêchait ce serveur de démarrer. Sert aussi à la sonde /api/status.
KLODY_MCP_PORT: str = os.getenv("KLODY_MCP_PORT", "8087")
KLODY_MCP_URL: str = os.getenv("KLODY_MCP_URL", f"http://127.0.0.1:{KLODY_MCP_PORT}/mcp")


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
# Images uploadées par le front (vision B-lite) : POST /api/upload y écrit, puis le
# message chat joint le chemin retourné dans `image_paths`. Sous PROJECT_ROOT (le
# repo est un enfant de ~/Projets) → l'outil analyser_image (sandbox + whitelist
# ext) l'accepte. Dédié et gitignoré ; nom de fichier = uuid serveur (jamais le nom
# client). Le cerveau reste TEXTE : on ne fait que produire un fichier que l'outil
# Path A sait lire — aucun changement du format des messages.
UPLOADS_DIR: Path = Path(os.getenv("UPLOADS_DIR", str(_ROOT / "_uploads"))).resolve()

# --- Mémoire sémantique (klody_memory — « memory bus » Klody Core) ---
# Archive ILLIMITÉE + rappel sémantique bge-m3 par-dessus la mémoire long-terme
# plate : chaque fait remember_fact / extrait auto est MIROITÉ dans une base
# sqlite-vec dédiée (cf. agent/semantic_memory.py), interrogeable via l'outil
# rappeler_memoire — y compris les faits "context" purgés du JSON ou au-delà du
# cap d'injection du prompt. Best-effort : paquet klody-memory absent → tout le
# reste fonctionne sans la couche sémantique.
SEMANTIC_MEMORY_ENABLED: bool = os.getenv("SEMANTIC_MEMORY_ENABLED", "true").lower() in ("1", "true", "yes", "on")
SEMANTIC_MEMORY_DB: Path = Path(os.getenv("SEMANTIC_MEMORY_DB", str(MEMORY_DIR / "semantic_memory.db")))
# "st" = sentence-transformers in-process (bge-m3, chargé 1×/process) — le bon
# choix pour l'API launchd long-vécue, et zéro dépendance au daemon Ollama.
# "ollama" reste possible (daemon requis).
SEMANTIC_MEMORY_PROVIDER: str = os.getenv("SEMANTIC_MEMORY_PROVIDER", "st")

# --- Voix parlée de Klody (outil speak → CLI VocalBrain + afplay) ---
# Pont léger : la synthèse vit dans le venv local-suno (mlx-audio), Klody ne
# l'importe jamais — il appelle la CLI en subprocess. Le projet/personnage
# VocalBrain « klody-voice »/« Klody » (Qwen3-TTS 0.6B) ont été créés une fois ;
# surchargeables ici si on veut une autre voix.
VOICE_CLI: str = os.getenv("VOICE_CLI", str(Path.home() / "local-suno" / ".venv" / "bin" / "vocalbrain"))
VOICE_PROJECT_ID: str = os.getenv("VOICE_PROJECT_ID", "58a252a5-1c07-4bd1-bf36-ad59bcdbd413")
VOICE_CHARACTER: str = os.getenv("VOICE_CHARACTER", "Klody")
VOICE_AUDIO_DIR: Path = Path(os.getenv("VOICE_AUDIO_DIR", str(Path.home() / ".vocalbrain" / "audio")))
VOICE_PLAY_CMD: str = os.getenv("VOICE_PLAY_CMD", "afplay")

LOG_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

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
