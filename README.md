# 🤖 Klody Code Ai

Agent de coding IA autonome, 100% local, propulsé par Ollama + qwen2.5-coder:32b.

---

## Stack technique

| Composant | Technologie |
|-----------|------------|
| Runtime | Python 3.11+ |
| LLM local | mlx-lm — `Qwen2.5-Coder-14B-4bit` (port 8080) |
| RAG / Livres | LibraryBrain — sqlite-vec + LlamaIndex (port 8765) |
| MCP Bridge | FastMCP — serveur MCP LibraryBrain (port 8082) |
| RAG Proxy | FastAPI — middleware Aider→mlx-lm (port 8081) |
| API Client | `openai` SDK (compatible Ollama / mlx-lm) |
| UI Terminal | `rich` — couleurs, panels, streaming |
| Config | `python-dotenv` — `.env` local |
| Tests | `pytest` — 77 tests, 100% pass |

---

## Installation

### 1. Prérequis système

```bash
# Homebrew (si absent)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Ollama
brew install ollama

# ripgrep (optionnel mais recommandé pour la recherche)
brew install ripgrep
```

### 2. Télécharger le modèle

```bash
# Démarrer le serveur Ollama
ollama serve

# Dans un autre terminal — télécharger le modèle (20 GB)
ollama pull qwen2.5-coder:32b

# Modèle plus léger si RAM limitée
ollama pull qwen2.5-coder:7b
```

### 3. Cloner et installer

```bash
git clone https://github.com/klodynlov/klody-code-ai.git
cd klody-code-ai

# Créer l'environnement virtuel
python3.11 -m venv .venv
source .venv/bin/activate

# Installer les dépendances
pip install -r requirements.txt
```

### 4. Configurer

```bash
cp .env.example .env
```

Éditer `.env` :

```env
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_API_KEY=ollama
MODEL_NAME=qwen2.5-coder:32b

# ⚠️  IMPORTANT : le dossier sur lequel l'agent peut travailler
PROJECT_ROOT=/Users/ton-nom/mon-projet
```

---

## Premier lancement

```bash
source .venv/bin/activate
python main.py
```

Vous verrez :

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
```

---

## Commandes spéciales

| Commande | Description |
|----------|-------------|
| `/help` | Afficher l'aide |
| `/clear` | Effacer l'historique de la session |
| `/memory` | Statistiques de mémoire (messages, fichier) |
| `/model` | Afficher le modèle actif |
| `/model qwen2.5-coder:7b` | Changer de modèle à la volée |
| `/exit` | Quitter |
| `Ctrl+C` | Quitter |

### Reprendre une session

```bash
# Reprendre la dernière session
python main.py --resume

# Reprendre une session précise (l'ID est affiché au lancement)
python main.py --session a1b2c3d4
```

---

## Outils disponibles

L'agent dispose de 5 outils qu'il invoque automatiquement :

| Outil | Description |
|-------|-------------|
| `read_file` | Lit un fichier (sandboxé, max 1 MB) |
| `write_file` | Écrit/crée un fichier |
| `list_files` | Liste un répertoire (optionnel: récursif) |
| `execute_command` | Exécute une commande shell **avec confirmation humaine** |
| `search_in_files` | Grep/ripgrep dans les fichiers du projet |

### Sécurité sandbox

- L'agent ne peut accéder qu'au dossier `PROJECT_ROOT` défini dans `.env`
- Chemins absolus, `../`, symlinks sortants → bloqués
- Extensions sensibles bloquées : `.env`, `.key`, `.pem`, `.p12`, `.cer`, `.crt`
- Commandes bloquées : `sudo`, `rm -rf /`, `mkfs`, `dd if=`, `curl | bash`, etc.
- Toute commande bash demande une confirmation `[Y/n]` — défaut = **N**

---

## Lancer les tests

```bash
source .venv/bin/activate
pytest tests/ -v
```

Résultat attendu :
```
77 passed in 1.12s
```

---

## Structure du projet

```
klody-code-ai/
├── .env                        # Config locale (jamais commitée)
├── .env.example                # Template public
├── .gitignore
├── requirements.txt
├── README.md
├── main.py                     # Point d'entrée CLI, REPL Rich
├── config.py                   # Chargement .env, constantes
│
├── agent/
│   ├── llm.py                  # Client Ollama, streaming token par token
│   ├── memory.py               # Historique JSON persistant (session)
│   └── orchestrator.py         # Boucle ReAct : Thought→Action→Observation
│
├── tools/
│   ├── registry.py             # Schémas JSON Schema des 5 outils
│   ├── file_manager.py         # read/write/list/diff sandboxé
│   ├── terminal.py             # Exécution bash + validation humaine
│   └── search.py               # grep/ripgrep
│
├── logs/
│   ├── agent.log               # Log complet (gitignored)
│   └── memory_*.json           # Sessions (gitignored)
│
└── tests/
    ├── test_file_manager.py    # 26 tests sandbox + I/O
    ├── test_terminal.py        # 23 tests sécurité + exécution
    └── test_memory.py          # 28 tests persistance + format API
```

---

## Erreurs fréquentes

### 1. `APIConnectionError: Connection refused`

Ollama n'est pas lancé.

```bash
ollama serve
# puis dans un autre terminal :
python main.py
```

### 2. `model "qwen2.5-coder:32b" not found`

Le modèle n'est pas téléchargé.

```bash
ollama pull qwen2.5-coder:32b
# version légère si mémoire insuffisante :
ollama pull qwen2.5-coder:7b
# puis dans .env : MODEL_NAME=qwen2.5-coder:7b
```

### 3. `SandboxViolation: Chemin hors sandbox`

`PROJECT_ROOT` dans `.env` n'est pas défini correctement.

```bash
# Vérifier la valeur
cat .env | grep PROJECT_ROOT

# Mettre un chemin absolu existant, ex :
PROJECT_ROOT=/Users/ton-nom/mon-projet
```

---

---

## Architecture Phase 4 — RAG Bridge (MCP + Proxy)

```
Aider (client)
    │
    ▼  OpenAI-compatible — port 8081
scripts/rag-proxy.py          ←── injecte contexte RAG (≤ 2000 tokens)
    │                                     │
    │  POST /ask                           │  skills/*.json
    ▼  port 8765                           ▼  filesystem
LibraryBrain (FastAPI)          skills/ (4 domaines JSON)
sqlite-vec + LlamaIndex
    │
    ▼  OpenAI-compatible — port 8080
mlx-lm (Qwen2.5-Coder-14B-4bit)

─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
MCP clients (Claude Desktop, etc.)
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
| 8080 | mlx-lm | Backend LLM — Qwen2.5-Coder |
| 8081 | rag-proxy | Middleware Aider (RAG injecté) |
| 8082 | FastMCP | Interface MCP pour clients externes |
| 8765 | LibraryBrain | Source de vérité — livres indexés |

### Lancer le RAG Proxy

```bash
source .venv/bin/activate

# Variables optionnelles (toutes ont des valeurs par défaut)
export LIBRARYBRAIN_URL=http://127.0.0.1:8765/ask
export MLX_URL=http://127.0.0.1:8080
export MAX_CONTEXT_TOKENS=2000

# Démarre MCP server (port 8082) + RAG proxy (port 8081)
./scripts/start-rag-proxy.sh
```

Configurer Aider pour utiliser le proxy :

```bash
aider --openai-api-base http://127.0.0.1:8081/v1 \
      --openai-api-key local \
      --model qwen2.5-coder
```

### Domaines de skills disponibles

| Fichier | Domaine | Contenu |
|---------|---------|---------|
| `skills/symfony.json` | symfony | Migrations Doctrine, DI, Messenger, PHPUnit |
| `skills/nextjs.json` | nextjs | App Router, Data fetching, TypeScript, Performance |
| `skills/python.json` | python | Type hints, Dataclasses, Async, Pytest |
| `skills/mlx.json` | mlx | Arrays, Quantization, mlx-lm serving, LoRA |

### Tests curl — validation MCP

```bash
# 1. Vérifier que le proxy est vivant
curl http://127.0.0.1:8081/health

# 2. Test search_books via MCP (nécessite LibraryBrain actif)
curl -s -X POST http://127.0.0.1:8082/mcp \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 1,
    "method": "tools/call",
    "params": {"name": "search_books", "arguments": {"query": "machine learning python", "limit": 2}}
  }' | python3 -m json.tool

# 3. Test get_skills via MCP
curl -s -X POST http://127.0.0.1:8082/mcp \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 2,
    "method": "tools/call",
    "params": {"name": "get_skills", "arguments": {"domain": "mlx"}}
  }' | python3 -m json.tool

# 4. Test get_conventions via MCP
curl -s -X POST http://127.0.0.1:8082/mcp \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 3,
    "method": "tools/call",
    "params": {"name": "get_conventions", "arguments": {"project": "/Users/klodynlov/Projets/klody-code-ai"}}
  }' | python3 -m json.tool

# 5. Test proxy end-to-end (nécessite mlx-lm actif)
curl -s -X POST http://127.0.0.1:8081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen2.5-coder",
    "messages": [{"role": "user", "content": "Comment faire une migration Doctrine dans Symfony ?"}],
    "stream": false
  }' | python3 -m json.tool
```

### Structure Phase 4

```
mcp/
├── __init__.py
└── server.py               # FastMCP — 3 outils exposés (port 8082)

skills/
├── symfony.json            # Conventions PHP/Symfony (4 entries)
├── nextjs.json             # Conventions Next.js/React (4 entries)
├── python.json             # Conventions Python (4 entries)
└── mlx.json                # Conventions MLX/Apple Silicon (4 entries)

scripts/
├── start-aider.sh          # (existant)
├── start-ui.sh             # (existant)
├── start-rag-proxy.sh      # Lance MCP server + RAG proxy
└── rag-proxy.py            # Middleware FastAPI (port 8081)
```

---

## Licence

Usage personnel, non commercial.
