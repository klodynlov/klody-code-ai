# Klody — pile d'inférence locale (100% offline)

Environnement de coding agentique **entièrement local** sur Apple Silicon (M5 Max,
128 GB). Aucun service payant, aucune clé cloud, aucune donnée ne quitte la machine.
Deux modèles open-weights (Apache-2.0) exposés en **API OpenAI-compatible** via
`mlx_lm.server`, sur lesquels se branchent Klody, Aider et les apps Next.js.

## Architecture

```
                         ┌─────────────────────────────────────────┐
   Klody API (:8000) ───▶│  :8080  CERVEAU  Qwen3.6-35B-A3B (8bit)  │  agentique, MoE
   Aider (défaut)    ───▶│         mlx_lm.server · OpenAI /v1       │  ~3B actifs → rapide
                         └─────────────────────────────────────────┘
                         ┌─────────────────────────────────────────┐
   Aider (profil code)─▶ │  :8081  CODE     Qwen3-Coder-30B-A3B(8b) │  spécialiste code
                         │         mlx_lm.server · OpenAI /v1       │
                         └─────────────────────────────────────────┘
   Ollama (:11434) ─ fallback + embeddings bge-m3 (RAG)
```

`.env` est la **source de vérité** : `MLX_MODEL` (cerveau) / `MLX_CODE_MODEL` (code).
Un seul changement y bascule l'API Klody, le LaunchAgent `:8080` et l'auto-spawn Tauri.

## Modèles installés

| Rôle | Repo Hugging Face | Quant | Licence | Poids | Contexte | Notes |
|---|---|---|---|---|---|---|
| **Cerveau** | `unsloth/Qwen3.6-35B-A3B-MLX-8bit` | MLX 8-bit | Apache-2.0 | ~36 GB | 256K | MoE 35B / **~3B actifs**, vision+texte, **"thinking"** |
| **Code** | `mlx-community/Qwen3-Coder-30B-A3B-Instruct-8bit` | MLX 8-bit | Apache-2.0 | ~30 GB | 256K | MoE 30B / ~3B actifs, spécialisé code |
| *(alt. non installé)* | `lmstudio-community/Seed-OSS-36B-Instruct-MLX-8bit` | MLX 8-bit | Apache-2.0 | ~36 GB | **512K** | Dense 36B — cerveau alternatif si besoin de contexte 512K. `hf download` pour l'installer. |

> **Important — Qwen3.6 est un modèle "thinking".** Sans bridage il émet un long
> raisonnement et n'atteint jamais la réponse. On le **coupe au niveau serveur** via
> `MLX_CHAT_TEMPLATE_ARGS='{"enable_thinking": false}'` (.env), appliqué par
> `start-mlx.sh`. C'est sans effet sur Qwen3-Coder (son gabarit l'ignore).

Benchmarks mesurés (M5 Max 128 GB, 8-bit, thinking off, à chaud) :

| Modèle | Débit décodage | RAM résidente |
|---|---|---|
| Qwen3.6-35B-A3B (cerveau) | ~49–62 tok/s | ~35 GB |
| Qwen3-Coder-30B-A3B (code) | ~66 tok/s | ~30 GB |

Les deux serveurs tournent simultanément (~65 GB) en laissant ~60 GB libres.

## Démarrage / arrêt

```bash
./start-local-ai.sh brain     # cerveau Qwen3.6 sur :8080
./start-local-ai.sh code      # spécialiste code sur :8081
./start-local-ai.sh both      # les deux
./start-local-ai.sh status    # qui tourne + modèle servi + RAM
./start-local-ai.sh logs brain|code   # tail -f du log
./start-local-ai.sh stop [brain|code|all]   # arrêt propre (défaut: all)
```

- Lancement en arrière-plan avec logs (`logs/mlx-brain.log`, `logs/mlx-code.log`)
  et PID files (`.run/`), arrêt propre par PID. Idempotent : ne double-démarre pas
  un port déjà actif.
- **Démarrage automatique du cerveau** : le LaunchAgent `com.klody.mlx` lance
  `scripts/start-mlx.sh` à la connexion (voir `docs/OPS.md`). `start-local-ai.sh brain`
  détecte si `:8080` est déjà servi et n'interfère pas.

## Endpoint & exemples curl

Base : **`http://127.0.0.1:8080/v1`** (cerveau) · **`http://127.0.0.1:8081/v1`** (code).
Surface OpenAI standard : `/v1/models`, `/v1/chat/completions`.

```bash
# Cerveau (Qwen3.6) — réponse directe (thinking coupé côté serveur)
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"unsloth/Qwen3.6-35B-A3B-MLX-8bit",
       "messages":[{"role":"user","content":"In one sentence, what is an API?"}],
       "max_tokens":120}'

# Spécialiste code (Qwen3-Coder)
curl -s http://127.0.0.1:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/Qwen3-Coder-30B-A3B-Instruct-8bit",
       "messages":[{"role":"user","content":"Write a Python is_prime(n). Code only."}],
       "max_tokens":300}'
```

> `mlx_lm.server` charge dynamiquement d'après le champ `model` : il doit **matcher
> l'id servi**, sinon le serveur tente de charger un autre repo.

## Bascule cerveau ↔ code

- **Klody** suit `.env`. Pour changer son cerveau : éditer `MLX_MODEL`, puis
  redémarrer le serveur `:8080` :
  ```bash
  launchctl kickstart -k gui/$UID/com.klody.mlx   # si LaunchAgent actif
  # ou : ./start-local-ai.sh stop brain && ./start-local-ai.sh brain
  ```
- **Aider** choisit le profil au lancement :
  ```bash
  ./scripts/start-aider.sh [chemin]        # cerveau Qwen3.6 (:8080)
  ./scripts/start-aider.sh code [chemin]   # spécialiste Qwen3-Coder (:8081)
  ```
  Aider est 100% offline : `--openai-api-base` local, clé fictive, analytics et
  check-update désactivés.

## Intégration Klody

`.env` : `BACKEND=mlx` + `MLX_MODEL` → `config.py` résout `LLM_MODEL` sur le cerveau.
Rien d'autre à faire : l'API Klody (`api/server.py`) et la CLI (`main.py`) tapent
`MLX_BASE_URL`.

## Garanties offline

- Aucune clé cloud (`.env` : `GITHUB_TOKEN` vide, pas de clé OpenAI/Anthropic).
- Télémétrie HF désactivée (`HF_HUB_DISABLE_TELEMETRY=1` dans les scripts).
- Télémétrie Aider désactivée (`--no-analytics --analytics-disable`).
- Modèles Apache-2.0 (vérifié sur les model cards HF).
