# Klody — pile d'inférence locale (100% offline)

Environnement de coding agentique **entièrement local** sur Apple Silicon (M5 Max,
128 GB). Aucun service payant, aucune clé cloud, aucune donnée ne quitte la machine.
Des modèles open-weights exposés en **API OpenAI-compatible**, sur lesquels se
branchent Klody, Aider et les apps Next.js.

## Deux modes de fonctionnement

La pile se déploie de deux façons. **Elles ne se configurent pas pareil** — c'est la
source d'erreur n°1, lire la section « Pièges » avant de basculer de l'une à l'autre.

### A. Mode gateway (nominal)

Le gateway **Klody Core** (`:8090`, composant externe à ce dépôt) est le point
d'entrée unique. Il possède le resolver de modèles, expose des **alias stables**, et
route chaque requête vers le worker `mlx_lm` / `mlx_vlm` correspondant. Il journalise
aussi les requêtes LLM (cf. `agent/journal_client.py`).

```
                              ┌──────────────────────────────────────────┐
   Klody API (:8000) ────────▶│                                          │
   Klody MCP servers ────────▶│   Gateway Klody Core · :8090 · /v1       │
   Apps Next.js      ────────▶│   resolver d'alias + journalisation      │
                              └───────┬──────────┬───────────┬───────────┘
                                      │          │           │
                              alias « brain »  « coder »  « vision »
                                      │          │           │
                                 worker mlx_lm  mlx_lm    mlx_vlm
                                  (cerveau)     (code)   (Qwen2.5-VL)

   Ollama (:11434) ─ fallback LLM + embeddings bge-m3 (RAG)
   LibraryBrain    ─ RAG livres (service externe, lecture seule dans `status`)
```

`.env` de ce mode (cf. `.env.example`) :

```bash
BACKEND=mlx
MLX_BASE_URL=http://localhost:8090/v1
MLX_MODEL=brain            # ALIAS, jamais un id HF
MLX_CODE_MODEL=coder       # ALIAS
MLX_CODE_BASE_URL=http://localhost:8090/v1
MLX_CODE_PORT=8083
VL_MODEL=vision            # ALIAS ; vide → outil analyser_image désactivé proprement
VL_BASE_URL=http://localhost:8090/v1
```

> ⚠️ **Toujours référencer les modèles par alias, jamais par id HF brut.** Le gateway
> ne garde dans son resolver que le `model_id` **courant** de chaque worker : un id
> brut figé dans `.env` se périme à la première bascule de modèle et renvoie un 404
> « modèle inconnu ». Vécu le **2026-07-03** — bascule du coder, toutes les tâches de
> type `code` sont tombées sur l'ancien id.

### B. Mode autonome (`start-local-ai.sh`)

Sans gateway : ce dépôt démarre lui-même deux `mlx_lm.server` indépendants, chacun sur
son port, chacun chargé avec un **id HF explicite**.

```
   Klody / Aider ───▶ :8080  CERVEAU  (MLX_MODEL, id HF)
   Aider (profil code) ─▶ :8081  CODE  (MLX_CODE_MODEL, id HF)
```

```bash
./start-local-ai.sh brain     # cerveau sur :8080
./start-local-ai.sh code      # spécialiste code sur :8081
./start-local-ai.sh both      # les deux (~65 GB RAM)
./start-local-ai.sh status    # qui tourne + modèle servi + RAM + satellites
./start-local-ai.sh logs brain|code    # tail -f du log
./start-local-ai.sh stop [brain|code|all]   # arrêt propre par PID (défaut: all)
```

Lancement en arrière-plan avec logs (`logs/mlx-brain.log`, `logs/mlx-code.log`) et PID
files (`.run/`). Idempotent : ne double-démarre pas un port déjà actif. `status`
affiche aussi Ollama et LibraryBrain en lecture seule (jamais démarrés ni arrêtés ici).

### Pièges de bascule entre les deux modes

`start-local-ai.sh` et `scripts/start-mlx.sh` lisent `MLX_MODEL` / `MLX_CODE_MODEL`
comme des **ids HuggingFace** et les passent à `mlx_lm.server --model`. Or le mode
gateway y met des **alias**. Conséquence :

- Un `.env` en mode gateway (`MLX_MODEL=brain`) + `./start-local-ai.sh brain` →
  `mlx_lm.server --model brain` → échec de chargement, timeout, log rouge.
- Inversement, un `.env` en mode autonome (id HF) laissé en place quand on repasse
  derrière le gateway → 404 « modèle inconnu » à la première bascule.

En mode gateway, ne pas utiliser `start-local-ai.sh` : c'est le gateway qui gère le
cycle de vie des workers. Pour un démarrage manuel ponctuel, passer l'id explicitement :
`./scripts/start-mlx.sh --model <id-hf> --port 8080`.

## Modèles

| Rôle | Repo Hugging Face | Quant | Licence | Poids | Contexte | Notes |
|---|---|---|---|---|---|---|
| **Cerveau** | `unsloth/Qwen3.6-35B-A3B-MLX-8bit` | MLX 8-bit | Apache-2.0 | ~36 GB | 256K | MoE 35B / **~3B actifs**, vision+texte, **"thinking"** |
| **Code** | `mlx-community/Qwen3-Coder-30B-A3B-Instruct-8bit` | MLX 8-bit | Apache-2.0 | ~30 GB | 256K | MoE 30B / ~3B actifs, spécialisé code |
| **Vision** | `Qwen2.5-VL` (worker `mlx_vlm`) | — | Apache-2.0 | — | — | Servi par le gateway sous l'alias `vision` ; alimente `analyser_image` |
| *(alt. non installé)* | `lmstudio-community/Seed-OSS-36B-Instruct-MLX-8bit` | MLX 8-bit | Apache-2.0 | ~36 GB | **512K** | Dense 36B — cerveau alternatif si besoin de 512K. `hf download` pour l'installer. |

Benchmarks mesurés (M5 Max 128 GB, 8-bit, thinking off, à chaud) :

| Modèle | Débit décodage | RAM résidente |
|---|---|---|
| Qwen3.6-35B-A3B (cerveau) | ~49–62 tok/s | ~35 GB |
| Qwen3-Coder-30B-A3B (code) | ~66 tok/s | ~30 GB |

Les deux workers tournent simultanément (~65 GB) en laissant ~60 GB libres. Le garde
mémoire `mlx_server_guarded` reste loin de son plafond (~80 GB) même avec
`CONTEXT_WINDOW=131072` — validé le 2026-07-14, sans OOM ni watchdog.

### Deux réglages non négociables

**1. Couper le « thinking » de Qwen3.6.** Sans bridage il émet un long raisonnement et
n'atteint jamais la réponse. On le coupe **au niveau serveur** :

```bash
MLX_CHAT_TEMPLATE_ARGS='{"enable_thinking": false}'   # .env — garder les quotes
```

Appliqué par `scripts/start-mlx.sh` via `--chat-template-args`. Sans effet sur
Qwen3-Coder (son gabarit l'ignore).

**2. Pas de speculative decoding sur le cerveau.** `MLX_DRAFT_MODEL` existe mais doit
rester **vide** pour Qwen3.6-35B-A3B : ce MoE produit un prompt cache `ArraysCache`
non-trimmable, et `mlx_lm` 0.31.3 lève
`ValueError: Speculative decoding requires a trimmable prompt cache` à **chaque**
requête (`generate.py:531`), quel que soit le draft. Tenté le 2026-06-28 → toutes les
complétions du cerveau tombaient en erreur. Le décodage spéculatif n'avait de toute
façon montré aucun gain sur MoE (cf. `ROADMAP.md`, étape 9).

## Lanceur supervisé (anti-wedge)

`scripts/start-mlx.sh` n'appelle **pas** `python -m mlx_lm.server` directement mais
`scripts/mlx_server_guarded.py`, qui enrobe `ResponseGenerator._generate` dans un
superviseur.

Le problème couvert (investigation 2026-06-28) : `mlx_lm` 0.31.3 exécute la génération
dans un seul thread, et sa boucle batchée n'a aucun `try/except`. Une exception y tue
le thread → plus aucun sentinel n'est déposé sur les `response_queue` → **toute**
requête suivante bloque indéfiniment, worker à 0 % CPU, wedge global jusqu'au restart.
Le superviseur log et redémarre la boucle avec un état frais ; `load()` court-circuite
quand le modèle est déjà chargé, donc le redémarrage ne recharge pas les ~36 GB.

Portée : uniquement le cas « thread mort → wedge permanent ». Un abort process (OOM
Metal) tue tout et c'est `launchd` (`KeepAlive`) qui relance. Si `mlx_lm` change de nom
de cible interne, le wrapper log un warning et démarre **non patché** plutôt que de
casser.

## Démarrage automatique (LaunchAgent)

Le LaunchAgent `com.klody.mlx` lance `scripts/start-mlx.sh` à la connexion
utilisateur — voir [`docs/OPS.md`](docs/OPS.md) pour le plist complet et les commandes
`launchctl`. `KeepAlive` ne relance que sur crash, pas après un arrêt manuel.

```bash
launchctl print gui/$UID/com.klody.mlx | grep -E 'state|pid'
launchctl kickstart -k gui/$UID/com.klody.mlx     # redémarrer après changement de modèle
tail -f ~/Library/Logs/klody-mlx.log
```

L'app desktop `klody-ui.app` a un fallback : si le port ne répond pas au démarrage,
Rust appelle `scripts/start-mlx.sh` directement (`src-tauri/src/lib.rs::spawn_mlx`). Le
LaunchAgent reste la voie canonique.

## Endpoint & exemples curl

Surface OpenAI standard : `/v1/models`, `/v1/chat/completions`.

```bash
# Mode gateway — on adresse un ALIAS
curl -s http://127.0.0.1:8090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"brain",
       "messages":[{"role":"user","content":"In one sentence, what is an API?"}],
       "max_tokens":120}'

# Mode autonome — le champ `model` doit matcher l'id HF réellement servi
curl -s http://127.0.0.1:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/Qwen3-Coder-30B-A3B-Instruct-8bit",
       "messages":[{"role":"user","content":"Write a Python is_prime(n). Code only."}],
       "max_tokens":300}'
```

> En mode autonome, `mlx_lm.server` charge dynamiquement d'après le champ `model` : il
> doit matcher l'id servi, sinon le serveur tente de charger un autre repo. Attention,
> son `/v1/models` liste **tout le cache HF**, pas le modèle chargé — utiliser
> `./start-local-ai.sh status` pour savoir ce qui tourne réellement.

## Bascule de modèle

- **Klody** suit `.env`. En mode gateway, changer l'alias ne suffit pas : c'est côté
  gateway qu'on rebranche l'alias sur un autre worker. En mode autonome, éditer
  `MLX_MODEL` puis redémarrer le serveur :
  ```bash
  launchctl kickstart -k gui/$UID/com.klody.mlx        # si LaunchAgent actif
  # ou : ./start-local-ai.sh stop brain && ./start-local-ai.sh brain
  ```
- **Aider** choisit son profil au lancement :
  ```bash
  ./scripts/start-aider.sh [chemin]        # cerveau
  ./scripts/start-aider.sh code [chemin]   # spécialiste code
  ```
  Aider est 100 % offline : base OpenAI locale, clé fictive, analytics et check-update
  désactivés.

## Intégration Klody

`config.py` résout le backend actif : `BACKEND=mlx` → `LLM_BASE_URL = MLX_BASE_URL` et
`LLM_MODEL = MLX_MODEL`. `MLX_CODE_MODEL` vide → routage code désactivé, tout reste sur
`LLM_MODEL`. `VL_MODEL` vide → outil `analyser_image` enregistré mais désactivé, avec un
message lisible (jamais d'exception). L'API (`api/server.py`), la CLI (`main.py`) et les
serveurs MCP tapent tous `MLX_BASE_URL`.

## Garanties offline

- Aucune clé cloud requise : `.env.example` n'en déclare aucune (ni OpenAI, ni
  Anthropic). `GITHUB_TOKEN` (`config.py:363`) est optionnel et vide par défaut — il ne
  sert qu'aux outils de lecture GitHub, jamais à l'inférence.
- Télémétrie HF désactivée (`HF_HUB_DISABLE_TELEMETRY=1`, `DISABLE_TELEMETRY=1` — posés
  par `start-local-ai.sh`).
- Télémétrie Aider désactivée (`--no-analytics --analytics-disable`).
- Modèles Apache-2.0 (vérifié sur les model cards HF).
- Serveurs bindés sur `127.0.0.1` uniquement.
