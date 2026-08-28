# Klody — opérations (CI, runner self-hosted, bench)

Petit guide pour les manipulations CI/bench qui ne tournent pas en clic-bouton
GitHub Actions.

## 1. Figer la baseline bench

Le workflow `.github/workflows/bench-nightly.yml` (cron 03:00 UTC) lance le
bench sur le runner self-hosted Mac et compare `bench/results/latest.json` à
`bench/results/baseline.json`. Tant que `baseline.json` n'existe pas, le
workflow accepte le run sans gate.

Pour figer une baseline propre :

```bash
# 1. S'assurer que MLX + Ollama tournent
curl -sf http://127.0.0.1:8080/v1/models > /dev/null && echo "MLX ok"
curl -sf http://127.0.0.1:11434/api/tags > /dev/null && echo "Ollama ok"

# 2. Lancer le bench complet (~20 tâches easy/medium/hard)
cd ~/Projets/klody-code-ai && source .venv/bin/activate
python -m bench.run --label "baseline_$(date +%Y%m%d)"

# 3. Vérifier le résultat (taux de succès par catégorie)
cat bench/results/latest.json | jq '.success_by_category, .counts_by_category'

# 4. Si satisfaisant → figer comme baseline
cp bench/results/latest.json bench/results/baseline.json
git add bench/results/baseline.json
git commit -m "chore(bench): fige la baseline $(date +%Y-%m-%d)"
git push
```

À partir du prochain run nightly, une régression > 10pts sur le taux de succès
global fait échouer le job (et émet un `::error::` GitHub Actions).

Pour re-figer la baseline (changement de modèle, nouvelle stratégie validée) :
même procédure, écraser `baseline.json`.

## 2. Enregistrer le runner self-hosted GitHub

Le workflow bench tourne sur un runner Mac labellisé `[self-hosted, macOS, klody]`.

### Setup initial (une fois)

1. **GitHub** → Settings → Actions → Runners → **New self-hosted runner** (macOS).
2. GitHub donne un script `./config.sh --url ... --token ...`. Lancer dans un
   dossier dédié (ex: `~/.github-runner/`).
3. Quand `config.sh` demande les labels, taper : `self-hosted, macOS, klody`.
4. Lancer le service en démon :
   ```bash
   cd ~/.github-runner
   ./svc.sh install
   ./svc.sh start
   ```
5. Vérifier l'enregistrement côté GitHub : Settings → Actions → Runners doit
   afficher le Mac avec un point vert.

### Vérification

```bash
# Le service tourne ?
launchctl list | grep actions.runner

# Logs récents
tail -f ~/.github-runner/_diag/Runner_*.log
```

### Démarrage des services avant le run nightly

Le workflow bench sonde le **gateway Klody Core** sur `MLX_BASE_URL`
(`:8090` par défaut) et valide que les alias `brain` et `coder` résolvent — un port
qui répond ne suffit pas, c'est la résolution d'alias qui casse en pratique
(cf. [`../README-local-ai.md`](../README-local-ai.md)). Ollama est sondé en plus,
pour les embeddings bge-m3, mais son absence n'est qu'un avertissement.

Si la machine vient de démarrer, tout doit être levé **avant** le run planifié —
idéalement via les LaunchAgents, sinon à la main :

```bash
# Gateway Klody Core (:8090) — voir son propre dépôt
# Vérifier qu'il répond ET que les alias résolvent :
curl -sf http://127.0.0.1:8090/v1/models >/dev/null && echo "gateway ok"
curl -sf http://127.0.0.1:8090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"brain","messages":[{"role":"user","content":"ping"}],"max_tokens":1}' \
  >/dev/null && echo "alias brain ok"

# Embeddings bge-m3 : in-process (sentence-transformers, SEMANTIC_MEMORY_PROVIDER=st) — rien à lancer.
# Ollama n'est nécessaire QUE si SEMANTIC_MEMORY_PROVIDER=ollama ou BACKEND=ollama :
# ollama serve &
```

En mode autonome (sans gateway), c'est `./start-local-ai.sh both` qui lève les deux
serveurs — et le workflow doit alors pointer `MLX_BASE_URL` sur `:8080`.

> Si aucun runner self-hosted n'est enregistré, inutile de chercher plus loin : le job
> `verify-runner` annule le run après 15 min avec un message explicite. Un historique
> de runs « annulés » sans autre trace, c'est ça.

### Démarrage automatique de MLX (LaunchAgent)

Le runner self-hosted bench + l'app desktop dépendent tous deux de MLX joignable
sur `:8080`. Pour qu'il démarre à la connexion utilisateur sans intervention :

```bash
# 1. Créer ~/Library/LaunchAgents/com.klody.mlx.plist
cat > ~/Library/LaunchAgents/com.klody.mlx.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.klody.mlx</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/klodynlov/Projets/klody-code-ai/scripts/start-mlx.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/klodynlov/Projets/klody-code-ai</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key><false/>
        <key>Crashed</key><true/>
    </dict>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key>
    <string>/Users/klodynlov/Library/Logs/klody-mlx.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/klodynlov/Library/Logs/klody-mlx.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

# 2. Charger (et démarrer immédiatement)
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.klody.mlx.plist

# 3. Vérifier
launchctl print gui/$UID/com.klody.mlx | grep -E 'state|pid'
tail -f ~/Library/Logs/klody-mlx.log
curl -sf http://127.0.0.1:8080/v1/models | jq .
```

`KeepAlive` redémarre MLX uniquement sur crash (pas après un arrêt manuel via
`launchctl kickstart -k`). `ThrottleInterval` évite les restart-loops si le
modèle ne se charge pas (port pris, fichier corrompu).

Pour désactiver temporairement :

```bash
launchctl bootout gui/$UID/com.klody.mlx
```

Le bundle `klody-ui.app` v2 a un fallback : si `:8080` ne répond pas au
démarrage, Rust appelle `scripts/start-mlx.sh` directement
(cf. `src-tauri/src/lib.rs::spawn_mlx`). Le LaunchAgent reste la voie
canonique — l'auto-spawn Tauri sert uniquement quand le LaunchAgent est
désactivé ou cassé.

## 3. Coverage gate

- `.coveragerc` fixe `fail_under = 70`.
- `api/server.py` reste exclu : le chemin WS chat demande un mock LLM
  compatible streaming OpenAI. À réintégrer quand `_build_streaming_orchestrator`
  pourra être branché à un FakeLLMClient compatible chunks streaming.
- Modules réintégrés (étaient exclus) : `agent/orchestrator.py`,
  `agent/profiler.py`. Couverts respectivement à 60% et 94%.

Pour mesurer un module précis hors du gate :

```bash
source .venv/bin/activate
pytest tests/ --cov=agent.orchestrator --cov-report=term-missing --no-cov-on-fail
```

## 4. E2E UI (klody-ui repo)

Tests Playwright dans `~/Projets/klody-ui/tests/e2e/`. CI : `.github/workflows/e2e.yml`.
Aucune dépendance backend réelle — `WebSocket` et REST `/api/*` sont stubés.

Local :

```bash
cd ~/Projets/klody-ui
npm run test:e2e         # headless
npm run test:e2e:ui      # mode interactif Playwright UI
```

## 5. Mettre à jour les dépendances sans casser les services vivants

⚠️ **`pip install` ne suffit PAS : il faut redémarrer les services.** C'est
l'incident du 2026-08-05, et il coûte cher parce qu'il est silencieux et
différé.

Ce qui s'est passé : la PR #206 a bumpé `openai` 2.41.1 → 2.53.0 ; `pip install
-r requirements.lock` a réécrit `site-packages/openai/` **sous** `com.klody.api`
qui tournait depuis 28 h. `openai._utils` est resté figé en 2.41.1 dans
`sys.modules` (donc sans `path_template`), alors que `openai/resources/*` —
chargé PARESSEUSEMENT par `openai/_utils/_resources_proxy.py` — a été lu neuf
sur le disque. Au premier appel LLM suivant, l'app a rendu sur **chaque** requête

```
cannot import name 'path_template' from 'openai._utils'
```

**~23 h après la mise à jour**, sans corrélation visible avec elle, et avec un
venv parfaitement sain : l'import dans un interpréteur frais passait, ce qui a
envoyé le diagnostic chercher du côté des versions.

### La procédure

```bash
cd ~/Projets/klody-code-ai && source .venv/bin/activate
pip install -r requirements.lock
python scripts/diagnostic_peremption.py
```

Le diagnostic nomme les services qui tournent encore sur l'ancien
`site-packages` et donne la commande de relance de chacun. Pour les relancer
tous d'un coup :

```bash
python scripts/diagnostic_peremption.py --corriger
```

⚠️ `--corriger` fait un `launchctl kickstart -k`, et `-k` **tue** le process
avant de le relancer : sur `com.klody.api`, cela coupe la session de travail en
cours. C'est pour ça que rien ne le déclenche automatiquement.

Codes de sortie — trois, parce que « je n'ai pas pu juger » n'est pas « tout va
bien » :

| code | sens |
|---|---|
| 0 | jugé, aucun service périmé |
| 1 | jugé, au moins un service périmé |
| 2 | rien n'a pu être jugé (aucun agent chargé, `site-packages` introuvable) |

### Les autres surfaces où l'écart se voit

- **`/health`** passe en `degraded` + **503**, avec `checks.dependances =
  "perimees"`, le détail et la commande de remède. Le watchdog ne peut pas
  boucler dessus : `scripts/api-watchdog.sh` ignore délibérément le code HTTP et
  ne relance que sur *absence* de réponse.
- **`/api/status`** porte le même verdict dans son champ `dependances`.
- **`install-launchagents.sh --check`** affiche un bloc `DÉPENDANCES` quand
  `site-packages` a été réécrit depuis le démarrage du plus ancien démon. Il
  informe seulement — il ne nomme pas les services et n'influence pas son code
  de sortie ; le juge est `diagnostic_peremption.py`.

Le mécanisme est décrit en détail dans `agent/peremption.py`.
