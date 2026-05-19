# 🤖 Klody Code AI

Agent de coding IA autonome, 100 % local, propulsé par Ollama + qwen2.5-coder:32b.

---

## Stack technique

| Composant | Technologie |
|-----------|------------|
| Runtime | Python 3.11+ |
| LLM local | Ollama — `qwen2.5-coder:32b` (port 11434) |
| RAG / Livres | LibraryBrain — sqlite-vec + FTS5 hybride (port 8765) |
| MCP Bridge | FastMCP — serveur MCP LibraryBrain (port 8082) |
| RAG Proxy | FastAPI — middleware Aider→mlx-lm (port 8081) |
| API Client | `openai` SDK (compatible Ollama) |
| UI Terminal | `rich` — couleurs, panels, streaming Markdown |
| Config | `python-dotenv` — `.env` local |
| Tests | `pytest` — 100 % pass |

---

## Installation

### 1. Prérequis système

```bash
# Ollama
brew install ollama

# ripgrep (optionnel — recherche plus rapide)
brew install ripgrep

# sqlite-vec (requis pour la recherche vectorielle LibraryBrain)
pip install sqlite-vec
```

### 2. Télécharger les modèles

```bash
ollama serve

# Modèle principal (20 GB)
ollama pull qwen2.5-coder:32b

# Modèle plus léger si RAM limitée
ollama pull qwen2.5-coder:7b
```

### 3. Cloner et installer

```bash
git clone https://github.com/klodynlov/klody-code-ai.git
cd klody-code-ai

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Configurer

```bash
cp .env.example .env
```

Éditer `.env` :

```env
# LLM
OLLAMA_BASE_URL=http://localhost:11434/v1
MODEL_NAME=qwen2.5-coder:32b

# Sandbox — dossier sur lequel l'agent peut travailler
PROJECT_ROOT=/Users/ton-nom/mon-projet

# LibraryBrain (optionnel — si installé)
LIBRARYBRAIN_URL=http://127.0.0.1:8765/api/ask
```

---

## Lancement

```bash
# Démarrer Ollama (si pas encore lancé)
ollama serve

# Lancer Klody
source .venv/bin/activate
python main.py
```

### Démarrer LibraryBrain (optionnel)

LibraryBrain doit être lancé **depuis son propre répertoire** :

```bash
cd /chemin/vers/library-brain
python3 -m uvicorn search.api:app --host 127.0.0.1 --port 8765
```

---

## Interface

```
╔══════════════════════════════════════════╗
║  🤖  Klody Code Ai                       ║
║  Powered by Ollama · 100% local · privé  ║
╚══════════════════════════════════════════╝

  Modèle    qwen2.5-coder:32b
  Projet    /Users/ton-nom/mon-projet
  Session   a1b2c3d4
  Messages  0

Tapez /help pour l'aide · /exit pour quitter

Vous >
```

### Exemples de requêtes

```
Vous > Liste les fichiers Python dans ce projet
Vous > Lis le fichier src/main.py et explique ce qu'il fait
Vous > Ajoute des docstrings aux fonctions de utils.py
Vous > Lance les tests et dis-moi ce qui échoue
Vous > Quels design patterns utilise-t-on pour ce problème ?
```

---

## Commandes spéciales

| Commande | Description |
|----------|-------------|
| `/help` | Afficher l'aide |
| `/clear` | Effacer l'historique de la session |
| `/memory` | Statistiques de mémoire |
| `/model` | Afficher le modèle actif |
| `/model qwen2.5-coder:7b` | Changer de modèle à la volée |
| `/exit` | Quitter |
| `Ctrl+C` | Quitter |

```bash
# Reprendre la dernière session
python main.py --resume

# Reprendre une session précise
python main.py --session a1b2c3d4
```

---

## Outils disponibles

L'agent dispose de **10 outils** invoqués automatiquement selon le besoin :

| Outil | Description |
|-------|-------------|
| `read_file` | Lit un fichier (sandboxé, max 1 MB) |
| `write_file` | Écrit/crée un fichier (max 1 MB) |
| `list_files` | Liste un répertoire (récursif optionnel) |
| `execute_command` | Exécute une commande shell **avec confirmation humaine** |
| `search_in_files` | Grep/ripgrep dans les fichiers du projet |
| `save_skill` | Mémorise un pattern ou convention pour les prochaines sessions |
| `import_llm_export` | Analyse un export JSON ChatGPT/Claude pour apprendre les pratiques |
| `list_imports` | Liste les exports disponibles dans `imports/` |
| `search_books` | Recherche sémantique dans LibraryBrain (RAG hybride FTS5+vec) |
| `get_skills` | Récupère les conventions d'un domaine (symfony, nextjs, python, mlx) |

---

## Sécurité sandbox

### Fichiers

- L'agent ne peut accéder qu'au dossier `PROJECT_ROOT` défini dans `.env`
- Chemins absolus, `../`, symlinks sortants → bloqués
- `list_files` masque automatiquement : `.git`, `.claude`, `.env`, `.venv`, `__pycache__`, `node_modules`
- Extensions bloquées en lecture/écriture : `.env`, `.key`, `.pem`, `.p12`, `.cer`, `.crt`, `.ppk`
- Écriture limitée à **1 MB** par fichier

### Commandes

Toute commande demande une **confirmation `[Y/n]`** — défaut = **N**.

Commandes bloquées sans confirmation possible :

```
sudo              rm -rf /          mkfs             dd if=/dev
bash -c '…'       sh -c '…'         python3 -c '…'   ruby -e '…'
node -e '…'       perl -e '…'       php -r '…'
cat /etc/passwd   cat /etc/shadow   ~/.ssh/id_rsa    ~/.aws/
env               printenv          nc -…            netcat -…
curl … && bash    wget … && sh      |bash            |sh
```

- Sortie des commandes tronquée à **50 000 caractères**

### API

- CORS restreint aux origines locales (`localhost`, `127.0.0.1`, `tauri://localhost`)
- Aucune clé API exposée dans les réponses

---

## Skills & mémoire

Klody peut **mémoriser des patterns** entre les sessions :

```
Vous > save_skill(name="Commit convention", description="Format commit", content="feat(scope): message")
```

Les skills sont rechargés automatiquement à chaque démarrage et injectés dans le system prompt.

### Skills domaines (pour search_books / get_skills)

| Fichier | Domaine | Contenu |
|---------|---------|---------|
| `skills/symfony.json` | symfony | Migrations Doctrine, DI, Messenger, PHPUnit |
| `skills/nextjs.json` | nextjs | App Router, Data fetching, TypeScript, Performance |
| `skills/python.json` | python | Type hints, Dataclasses, Async, Pytest |
| `skills/mlx.json` | mlx | Arrays, Quantization, mlx-lm serving, LoRA |

---

## LibraryBrain — RAG local

Quand LibraryBrain est actif, l'agent peut interroger ta bibliothèque de livres :

```
Vous > Quels livres parles de clean code ?
Vous > Explique-moi les design patterns selon tes livres
```

La recherche est **hybride** : FTS5 (plein texte) + sqlite-vec (vecteurs sémantiques).

### Prérequis LibraryBrain

```bash
pip install sqlite-vec   # moteur vectoriel
```

---

## Architecture Phase 4 — RAG Bridge

```
Klody (main.py)
    │  search_books / get_skills — natifs
    ▼
tools/mcp_client.py  ──── POST /api/ask/job ───►  LibraryBrain :8765
                          GET  /api/ask/job/{id}   (sqlite-vec + FTS5)

─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
Aider / clients externes
    │
    ▼  OpenAI-compatible — port 8081
scripts/rag-proxy.py        injecte contexte RAG (≤ 2000 tokens)
    │
    ▼  port 8765
LibraryBrain (FastAPI)
    │
    ▼  port 8080
mlx-lm (optionnel)

─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
Claude Desktop / MCP clients
    │
    ▼  MCP streamable-http — port 8082
mcp/server.py (FastMCP)
    ├── search_books(query, limit)
    ├── get_skills(domain)
    └── get_conventions(project)
```

### Ports

| Port | Service | Rôle |
|------|---------|------|
| 11434 | Ollama | Backend LLM principal |
| 8765 | LibraryBrain | Source RAG — livres indexés |
| 8081 | rag-proxy | Middleware Aider (RAG injecté) |
| 8082 | FastMCP | Interface MCP clients externes |
| 8000 | Klody API | WebSocket dashboard Tauri |
| 8080 | mlx-lm | Backend LLM alternatif (optionnel) |

### Lancer le RAG Proxy (pour Aider)

```bash
source .venv/bin/activate
./scripts/start-rag-proxy.sh

# Configurer Aider
aider --openai-api-base http://127.0.0.1:8081/v1 \
      --openai-api-key local \
      --model qwen2.5-coder
```

---

## Tests

```bash
source .venv/bin/activate
pytest tests/ -v
```

Couverture :

| Fichier | Tests |
|---------|-------|
| `test_file_manager.py` | Sandbox, lecture, écriture, limite taille, masquage `.claude/` |
| `test_terminal.py` | Blocklist sécurité, faux positifs, sortie tronquée |
| `test_memory.py` | Persistance, troncature, messages API |
| `test_skills.py` | save/load, format prompt, domaines vs user skills |
| `test_mcp_client.py` | `_is_domain_file`, `get_skills`, `_parse_result`, erreurs réseau |
| `test_search.py` | Recherche pattern, sandbox path, résultats tronqués |
| `test_llm_import.py` | Parsers ChatGPT/Claude/generic, détection techs, fichiers invalides |

---

## Structure du projet

```
klody-code-ai/
├── .env.example               # Template de configuration
├── .gitignore
├── requirements.txt
├── README.md
├── main.py                    # Point d'entrée CLI — REPL Rich
├── config.py                  # Variables d'environnement, constantes
│
├── agent/
│   ├── llm.py                 # Client Ollama, streaming, fallback tool calls
│   ├── memory.py              # Historique JSON persistant par session
│   └── orchestrator.py        # Boucle ReAct : Thought → Action → Observation
│
├── api/
│   └── server.py              # API WebSocket pour dashboard Tauri (port 8000)
│
├── mcp/
│   └── server.py              # Serveur FastMCP — 3 outils (port 8082)
│
├── tools/
│   ├── registry.py            # Schémas JSON Schema des 10 outils
│   ├── file_manager.py        # read/write/list sandboxé + limites
│   ├── terminal.py            # Exécution bash — confirmation + blocklist
│   ├── search.py              # grep/ripgrep sandboxé
│   ├── skills.py              # Mémoire persistante inter-sessions
│   ├── mcp_client.py          # Client LibraryBrain — job polling async
│   └── llm_import.py          # Parser exports ChatGPT/Claude/Gemini
│
├── skills/
│   ├── symfony.json           # Conventions PHP/Symfony
│   ├── nextjs.json            # Conventions Next.js/React
│   ├── python.json            # Conventions Python
│   ├── mlx.json               # Conventions MLX/Apple Silicon
│   └── utilisateur_*.json     # Skills appris depuis l'export Claude
│
├── scripts/
│   ├── rag-proxy.py           # Middleware FastAPI — RAG pour Aider
│   ├── start-rag-proxy.sh     # Lance MCP + RAG proxy
│   └── import-claude-export.py # Import export Claude.ai → skills
│
├── imports/                   # Dépôt des exports JSON à analyser
├── logs/                      # Logs + sessions (gitignored)
│
└── tests/
    ├── test_file_manager.py
    ├── test_terminal.py
    ├── test_memory.py
    ├── test_skills.py
    ├── test_mcp_client.py
    ├── test_search.py
    └── test_llm_import.py
```

---

## Erreurs fréquentes

### `APIConnectionError: Connection refused`

Ollama n'est pas lancé.

```bash
ollama serve
```

### `model "qwen2.5-coder:32b" not found`

```bash
ollama pull qwen2.5-coder:32b
# ou version légère :
ollama pull qwen2.5-coder:7b
# puis dans .env : MODEL_NAME=qwen2.5-coder:7b
```

### `SandboxViolation: Chemin hors sandbox`

`PROJECT_ROOT` dans `.env` doit être un chemin absolu existant.

```bash
echo "PROJECT_ROOT=$(pwd)" >> .env
```

### `LibraryBrain inaccessible`

LibraryBrain n'est pas démarré ou n'est pas lancé depuis son répertoire.

```bash
cd /chemin/vers/library-brain
python3 -m uvicorn search.api:app --host 127.0.0.1 --port 8765
```

### `no such module: vec0`

Le module `sqlite-vec` n'est pas installé.

```bash
pip install sqlite-vec
```

---

## Licence

Usage personnel, non commercial.
