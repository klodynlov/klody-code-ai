# 🤖 Klody Code Ai

Agent de coding IA autonome, 100% local, propulsé par Ollama + qwen2.5-coder:32b.

---

## Stack technique

| Composant | Technologie |
|-----------|------------|
| Runtime | Python 3.11+ |
| LLM | Ollama — `qwen2.5-coder:32b` |
| API Client | `openai` SDK (compatible Ollama) |
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

## Licence

Usage personnel, non commercial.
