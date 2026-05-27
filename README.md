# Klody Code AI

Agent de coding local autonome — 100% offline, propulsé par MLX-LM + Qwen3-Coder-30B-A3B
sur Apple Silicon. Pensé pour rivaliser avec un agent cloud sur une machine perso.

> **Status** : v2.1 stable · 490 tests · 9 étapes roadmap livrées · MLX backend × 12 vs Ollama

---

## Architecture

```
                ┌─── UI Tauri (klody-ui) ─────────────┐
                │  React 19 · Tailwind 4 · WebSocket  │
                └──────────────┬──────────────────────┘
                               │ ws://localhost:8000/api/ws
                               ▼
                ┌─── FastAPI + WebSocket ─────────────┐
                │  api/server.py — port 8000          │
                └──────────────┬──────────────────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
    ┌── Adaptive Orchestrator ──┐   ┌── MCP Server (klody_mcp) ──┐
    │  Router (Qwen3-4B logic)  │   │  8 tools exposés en MCP    │
    │   → easy / medium / hard  │   │  Continue.dev / Cline /    │
    │   → 6 task_types          │   │  Zed peuvent consommer     │
    │  Hot-swap system prompts  │   │  port 8083 (stdio | http)  │
    │  Best-of-N conditionnel   │   └────────────────────────────┘
    │  Anti-stall + nudge       │
    │  Text-to-action fallback  │
    └──────────────┬────────────┘
                   │
            ┌──────┴───────┐
            ▼              ▼
    ┌── Tools (~30) ──┐  ┌── LLM Backend ──┐
    │ read/write_file │  │ MLX  → port 8080│
    │ execute_command │  │   Qwen3-Coder-  │
    │ find_symbol     │  │   30B-A3B-4bit  │
    │ find_references │  │ ou Ollama       │
    │ find_relevant_  │  │   → port 11434  │
    │   files (RAG)   │  └─────────────────┘
    │ run_in_sandbox  │
    │ preview_code    │
    │ search_books    │  ┌── LibraryBrain ──┐
    │ browse_repo     │  │ RAG livres locale│
    │ ...             │  │ port 8765 / 8082 │
    └─────────────────┘  └──────────────────┘
```

## Caractéristiques clés (v2)

| # | Feature | Détail |
|---|---|---|
| 1 | **Bench reproductible** | 20 tâches catégorisées (easy/medium/hard) + métriques (latence, tokens, tool_calls cassés) |
| 2 | **MLX backend** | Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2 sur Apple Silicon, **×12** vs Ollama qwen2.5-coder:32b |
| 3 | **Sandbox loop** | venv jetable + auto-exec après `write_file` sur `.py` (pytest si test, py_compile sinon) |
| 4 | **Router adaptatif** | classifie chaque prompt → 3 difficultés × 6 task_types → max_iter + planner + best_of_n |
| 5 | **Hot-swap prompts** | 6 prompts focalisés (`easy_edit`, `refactor`, `bug_fix`, `feature`, `explain`, `self_dev`) |
| 6 | **Retrieval code-aware** | tree-sitter symboles/refs + embeddings bge-m3 (`find_symbol`, `find_relevant_files`) |
| 7 | **Best-of-N conditionnel** | 3 candidats T=0.5/0.8/1.0 + action override (préfère un candidat avec tool_calls) |
| 8 | **Memory utile** | Conventions auto-détectées + erreurs récurrentes (`.klody/` cache) |
| 9 | **MCP server expose** | Klody = plateforme pour d'autres agents (Continue.dev, Cline, Zed) |

## Stack technique

| Composant | Tech |
|-----------|------|
| Runtime | Python 3.11+ |
| LLM principal | **MLX-LM** — `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2` (port 8080) |
| LLM fallback | **Ollama** — `qwen2.5-coder:32b` ou `qwen3.5:9b` (port 11434) |
| Embeddings | **Ollama** — `bge-m3` (toujours via Ollama, léger) |
| RAG livres | **LibraryBrain** — sqlite-vec + FTS5 (port 8765) |
| MCP server | **FastMCP** — 8 outils Klody exposés (port 8083) |
| API client | `openai` SDK (compat MLX et Ollama) |
| UI terminal | `rich` |
| UI graphique | `klody-ui` (Tauri 2 + React 19 + Tailwind 4) — voir [klody-ui repo](https://github.com/klodynlov/klody-ui) |
| Tests | `pytest` — **490 tests** |

## Installation

### 1. Prérequis système

```bash
# MLX-LM (Apple Silicon recommandé)
pip install mlx-lm

# Ollama (pour bge-m3 embeddings + fallback LLM)
brew install ollama

# Optionnel : ripgrep (recherche plus rapide), sqlite-vec (LibraryBrain)
brew install ripgrep
pip install sqlite-vec
```

### 2. Télécharger les modèles

```bash
# MLX — modèle principal (~16 Go, 30B paramètres, 3B actifs MoE)
huggingface-cli download mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2

# Ollama — embeddings (toujours requis pour retrieval)
ollama serve
ollama pull bge-m3

# Optionnel : fallback Ollama si tu n'as pas Apple Silicon
ollama pull qwen2.5-coder:32b
```

### 3. Cloner et installer

```bash
git clone https://github.com/klodynlov/klody-code-ai.git
cd klody-code-ai
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Configurer `.env`

```env
# Backend LLM : "mlx" (recommandé Apple Silicon) ou "ollama"
BACKEND=mlx

# MLX
MLX_BASE_URL=http://localhost:8080/v1
MLX_MODEL=mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2

# Ollama (fallback ou si BACKEND=ollama)
OLLAMA_BASE_URL=http://localhost:11434/v1
MODEL_NAME=qwen2.5-coder:32b
MODEL_FALLBACK=mistral:latest

# Sandbox de fichiers — dossier que l'agent peut éditer
PROJECT_ROOT=/Users/ton-nom/mon-projet

# Adaptive Orchestrator (tout activé par défaut)
ROUTER_ENABLED=true
BEST_OF_N_ENABLED=true
BEST_OF_N_COUNT=3
SANDBOX_AUTO_EXEC=true
SANDBOX_TIMEOUT=20

# LibraryBrain (optionnel)
LIBRARYBRAIN_URL=http://127.0.0.1:8765/api/ask
LIBRARYBRAIN_DIR=/chemin/vers/library-brain
```

## Lancement

```bash
# Terminal 1 — MLX server (charge le modèle ~30s, ~16 Go RAM)
./scripts/start-mlx.sh

# Terminal 2 — Ollama (pour embeddings)
ollama serve

# Terminal 3 — Klody CLI Rich
source .venv/bin/activate
python main.py

# (Optionnel) Terminal 4 — API WebSocket pour l'UI Tauri
python api/server.py

# (Optionnel) Terminal 5 — MCP server pour exposer Klody à d'autres clients
./scripts/start-klody-mcp.sh           # stdio
./scripts/start-klody-mcp.sh --http    # HTTP port 8083
```

## Commandes CLI

| Commande | Description |
|----------|-------------|
| `/help` | Affiche l'aide |
| `/sessions` | Liste / charge une session passée |
| `/memory` | Mémoire courte + souvenirs long terme |
| `/model` | Affiche le modèle actif |
| `/model <id>` | Change de modèle à chaud |
| `/skills` | Liste les compétences mémorisées |
| `/exit` | Quitte |
| `Cmd+K / Ctrl+K` | Nouvelle session |

```bash
python main.py --resume                  # reprend la dernière session
python main.py --session <id-court>      # reprend une session précise
```

## Outils disponibles (~30)

| Catégorie | Outils |
|---|---|
| **Fichiers** | `read_file`, `write_file`, `list_files`, `search_in_files` |
| **Code-aware** (#6) | `find_symbol`, `find_references`, `find_relevant_files` |
| **Exécution** | `execute_command`, `run_in_sandbox` |
| **Web preview** | `preview_code` (HTML/CSS/JS auto-CDN + overlay erreurs), `preview_file`, `list_previews` |
| **GitHub** | `browse_repo`, `read_github_file`, `index_github_repo`, `clone_github_repo`, `extract_best_practices`, `create_project` |
| **RAG / Skills** | `search_books`, `learn_from_books`, `get_skills`, `save_skill`, `list_skills`, `delete_skill` |
| **Mémoire long terme** | `remember_fact`, `forget_fact` |
| **Imports LLM** | `import_llm_export`, `list_imports` (analyse exports ChatGPT/Claude) |

## Sécurité

- **Sandbox fichiers** : tout accès limité à `PROJECT_ROOT`, `../`/symlinks bloqués, `.env`/`.key`/`.pem` interdits, max 1 MB par écriture
- **Sandbox exécution** : `execute_command` requiert confirmation `[Y/n]` en TTY (auto-confirm en API + check sécurité pré-confirmation pour bloquer `sudo`, `rm -rf /`, `mkfs`, exfil SSH/AWS, etc.)
- **Sandbox isolé** : `run_in_sandbox` lance dans un venv jetable dédié (`~/.cache/klody/sandbox-venvs/<hash>/`)
- **CORS** restreint aux origines locales

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -q      # 490 passing
python -m pytest tests/ -v      # détaillé
```

## Bench

```bash
# Lance les tâches du bench sur Klody actuel (config .env)
BACKEND=mlx python -m bench.run --category easy
BACKEND=mlx python -m bench.run --task hard/debug_test_suite

# Évaluer la précision du Router (F1 macro)
BACKEND=mlx python -m bench.router_eval --label mlx_qwen3coder
```

Voir [`bench/README.md`](bench/README.md) pour ajouter des tâches.

## Architecture détaillée

```
klody-code-ai/
├── agent/
│   ├── llm.py                  # Client LLM unifié (MLX/Ollama) + 6 parsers tool_call
│   ├── orchestrator.py         # Boucle ReAct + Router + BoN + anti-stall + auto-preview
│   ├── router.py               # Classifier easy/medium/hard × 6 task_types (F1=0.85)
│   ├── prompts.py              # Composer + cache LRU des prompts focalisés
│   ├── best_of_n.py            # N candidats + action override + LLM-as-judge fallback
│   ├── conventions.py          # Détecteur heuristique (test framework, async, types, …)
│   ├── error_memory.py         # Mémoire des erreurs récurrentes sandbox
│   ├── memory.py / long_term_memory.py
│   └── memory_extractor.py
│
├── tools/
│   ├── file_manager.py         # Sandbox fichiers
│   ├── terminal.py             # Shell + auto-confirm non-TTY + blocklist
│   ├── sandbox.py              # venv jetable + py_compile/pytest auto
│   ├── code_index.py           # tree-sitter (Python/JS/TS) — symboles + refs
│   ├── code_search.py          # Embeddings bge-m3 — recherche sémantique
│   ├── preview.py              # HTML/CSS/JS + auto-CDN + overlay erreurs JS
│   ├── github_reader.py / project_creator.py
│   ├── mcp_client.py           # Client LibraryBrain
│   └── llm_import.py / skills.py / search.py
│
├── prompts/                    # 6 prompts focalisés + base + default (~300-600 tok chacun)
├── klody_mcp/                  # Serveur MCP Klody (FastMCP, 8 outils exposés)
├── api/server.py               # FastAPI + WebSocket pour UI Tauri
├── bench/                      # Bench reproductible 20 tâches
├── scripts/
│   ├── start-mlx.sh            # Lance mlx_lm.server
│   ├── start-klody-mcp.sh      # Lance le MCP server
│   ├── start-rag-proxy.sh      # Lance LibraryBrain + proxy RAG pour Aider
│   └── lora/                   # Scaffolding LoRA fine-tuning
├── tests/                      # 490 tests pytest
└── ROADMAP.md                  # 9 étapes v2 + critères de done
```

## Erreurs fréquentes

**`APIConnectionError` sur MLX** → lance `./scripts/start-mlx.sh`

**`Connection refused` Ollama** → `ollama serve` (requis pour bge-m3 embeddings)

**`model not found` MLX** → `huggingface-cli download mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2`

**`SandboxViolation`** → `PROJECT_ROOT` dans `.env` doit être un chemin absolu existant

**`Confirm.ask EOFError`** → tu es dans un environnement non-TTY (script, CI). Mets `BACKEND=mlx` et utilise l'API (le terminal auto-confirme en non-TTY depuis le fix terminal v2.1)

**UI Tauri stuck** → recharge avec `Cmd+Shift+R`, vérifie que `python api/server.py` tourne sur port 8000

## Licence

Usage personnel, non commercial.
