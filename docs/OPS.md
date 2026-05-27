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

Le workflow bench attend MLX et Ollama joignables. Si la machine vient de
démarrer, lancer manuellement (ou via LaunchAgents) :

```bash
# MLX
cd ~/Projets/klody-code-ai
source .venv/bin/activate
python -m mlx_lm.server --model mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit-dwq-v2 --port 8080 --host 127.0.0.1 &

# Ollama
ollama serve &
```

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
