# Klody v2 — Roadmap

> Document de référence pour l'évolution architecturale de Klody (mai 2026 → ).
> Toute décision majeure passe par mise à jour de ce fichier.

## Vision

Faire de Klody un agent de coding local **comparable à Claude Code** sur un M5 Max 128 Go.
Pas un clone : un système plus simple, plus rapide, qui exploite vraiment le hardware
Apple Silicon et l'inférence locale (MLX, modèles 2026).

## Principes directeurs

1. **Adaptatif > rigide** — un Router décide à la volée plutôt qu'un pipeline figé.
2. **Mesure avant optim** — aucune amélioration ne passe sans gain chiffré au bench.
3. **Sandbox > Reviewer-LLM** — un test qui passe vaut mieux qu'une opinion de modèle.
4. **1 Executor intelligent > 3 sous-agents rigides** — moins de handoff, moins de tokens perdus.
5. **Constrained generation** — les tool calls sont contraints par schéma, jamais
   parsés en JSON texte best-effort.

## Architecture cible

```
MLX Backend (OpenAI-compatible, port 11434 ou autre)
├─ Qwen3-Coder-30B-A3B-Instruct   (Executor, MoE actif 3B)
├─ Qwen3-4B                        (Router + Planner conditionnel + Verifier fallback)
├─ Qwen3-Embedding-8B              (retrieval)
└─ Qwen3-Reranker-4B               (lazy, chargé si best-of-N déclenché)

Adaptive Orchestrator
├─ Router       — classifie easy / medium / hard, choisit la stratégie
├─ Planner      — actif UNIQUEMENT si hard ou multi-fichier
├─ Executor     — 1 agent unique, system prompt hot-swap selon le routing
├─ Verifier     — sandbox (tests/exec) en priorité, model-fallback si rien d'exécutable
└─ Memory       — conventions du repo + erreurs récurrentes, exploitable

Tools
├─ FS / terminal / git                       (existants)
├─ Sandbox runner (venv jetable + pytest)    (nouveau, priorité)
├─ Retrieval (embeddings + tree-sitter/LSP)  (nouveau)
└─ Constrained generation (outlines/lm-format-enforcer) sur TOUS les tool calls
```

### Posture de l'Executor (hot-swap system prompts)

L'Executor reste **un seul agent** (même modèle, même process, même contexte mémoire),
mais reçoit un system prompt **court et ciblé** selon la classification du Router :

| Classification     | System prompt injecté          | Comportement                              |
|--------------------|--------------------------------|-------------------------------------------|
| `easy_edit`        | `prompts/easy_edit.md`         | edit localisé, pas de retrieval, pas de plan |
| `refactor`         | `prompts/refactor.md`          | retrieval + LSP d'abord, puis edits        |
| `bug_fix`          | `prompts/bug_fix.md`           | écrit un test qui reproduit, puis fixe     |
| `feature`          | `prompts/feature.md`           | plan d'abord, puis exécution itérative     |
| `explain`          | `prompts/explain.md`           | pas d'edit, juste lecture + réponse        |

Coût marginal nul (même contexte conservé), qualité ++.

## Roadmap

| #  | Étape                                              | Effort | Critère de done                                          |
|----|----------------------------------------------------|--------|----------------------------------------------------------|
| ✅ 1 | **Bench baseline** (20 tâches reproductibles)    | done   | 5/5 easy ✅ (qwen2.5-coder:32b Ollama, 96.5s moy)         |
| ✅ 2 | **MLX backend** + Qwen3-Coder-30B-A3B            | done   | **×12 vs baseline** (96.5s → 8.0s moy easy) — cible ×1.5 explosée |
| ✅ 3 | **Sandbox loop** (venv jetable + auto-exec)      | done   | `medium/fix_failing_test` ✅ 14.7s ; +bonus fix multi-call JSON |
| ✅ 4 | **Router adaptatif** (Qwen3-Coder, self-route)   | done   | **F1 macro = 0.850** (cible > 0.800), 0.41s/classif       |
| ✅ 5 | **Hot-swap system prompts** (6 prompts focalisés)| done   | -75% noise dans le prompt système ; -1s sur bug_fix       |
| ✅ 6 | **Retrieval** (tree-sitter + bge-m3)             | done   | 1300 syms/15k refs en <1s ; `migrate_print_to_logger` ✅ 19.6s |
| ✅ 7 | **Best-of-N conditionnel** (LLM-as-judge)        | done   | infra prête, gated par router ; gain non mesurable sur hard synthétique |
| ✅ 8 | **Memory utile** (conventions + erreurs)         | done   | **5 conventions** sur Klody en 583ms (cible ≥3)          |
| ✅ 9 | **Optims** (MCP expose ✅, LoRA scaffolding ✅, spec decoding ⚠) | done | Klody MCP server 8 outils ; pipeline LoRA prêt ; spec decoding sans gain sur MoE |
| ✅ 10 | **Pilotage de l'environnement + Toolsmithing** | done | macOS (AppleScript/Spotlight/Raccourcis/Finder), maison (MQTT), automatisation fichiers, et **toolsmithing** (Klody fabrique scripts/CLI/API/serveurs MCP/workflows/pipelines/plugins/interfaces). Chaque artefact généré livré avec son test. |
| ✅ 11 | **Expansion des capacités** (task_types + langages + Ops + génération) | done | +6 task_types focalisés ; retrieval Rust/Go/Java/PHP ; outils `analyze_dependencies`, `run_sql` (SQLite sandboxé), introspection Docker/Kubernetes/Git + mutations gated, `generate_uml`, `scaffold_api` (REST/GraphQL) / `scaffold_sdk` / `scaffold_nosql` ; 7 skills de domaine. **69 outils au total.** |

**Total : 11/11 étapes livrées.**

Klody est passé d'un ReAct mono-modèle Ollama qwen2.5-coder:32b à un système
agentique adaptatif MLX multi-modèles avec routing, hot-swap prompts, sandbox
auto-feedback, retrieval code-aware, best-of-N gated, memory de conventions,
mémoire d'erreurs récurrentes, et exposé comme serveur MCP pour d'autres agents.

### Étape 11 — Expansion des capacités (4 leviers, additif)

Les « capacités » de Klody ne sont pas une liste en dur : ce sont 4 leviers
composables. L'étape 10 les actionne sans casser l'existant :

1. **`task_types` du routeur** (+ prompts focalisés) — 6 nouveaux workflows
   dédiés : `review` (revue de code), `test_gen` (tests unitaires + intégration),
   `security` (audit OWASP), `docs` (documentation), `perf` (perf + mémoire),
   `migrate` (versions + dépendances). Routage : `test_gen/perf/migrate` → modèle
   coder ; `review/security/docs` → généraliste (analyse/rédaction). Planner activé
   sur medium pour les types multi-étapes ; best-of-N inchangé (hard/self_dev).
2. **Langages tree-sitter** — retrieval code-aware étendu à Rust/Go/Java/PHP via un
   registre data-driven (`_LANG_SPEC`) + chargement de grammaire **optionnel et
   isolé** : un paquet absent laisse le langage dormant, sans jamais compromettre
   Python/JS/TS. Aucune dépendance dure ajoutée (pas de drift du lockfile).
3. **Nouveaux outils** — `analyze_dependencies` : inventaire multi-écosystèmes
   (pip/npm/cargo/go/composer) en lecture seule, confiné aux racines autorisées.
   Puis `run_sql` (premier outil **runtime**) : exécution SQL sur une base SQLite
   locale, sandboxée. Conçue via un **threat-model adversarial** (workflow multi-agents
   sur les vecteurs ATTACH / VACUUM INTO / load_extension / injection d'URI / DoS) :
   authorizer sqlite3 *default-deny*, verrou `SQLITE_LIMIT_ATTACHED=0`, URI
   percent-encodée, échéance wall-clock, une seule instruction, **écriture désactivée
   par défaut** (`SQL_WRITE_ENABLED`). 23 tests dont un par vecteur d'évasion.
   Puis `docker_control` : introspection Docker **lecture seule** (ps/images/inspect/
   logs/stats/version/df), aucune mutation du démon ; `subprocess` en argv sans shell,
   sous-commandes hardcodées, cible validée → pas d'injection de commande. 25 tests.
   Puis `kubectl_control` : introspection Kubernetes **lecture seule** (get/describe/
   logs/top/version/cluster-info/api-resources), même patron (argv sans shell, verbes
   hardcodés, resource/name/namespace/container validés, `--request-timeout`). 26 tests.
   Puis `git_control` : introspection Git **lecture seule** (status/log/diff/show/blame/
   branch/tag/remote/shortlog), dépôt confiné aux racines autorisées, ref & fichier
   validés (pas de `..`, pas de flag injectable) — préférable à `execute_command`
   (sans confirmation TTY) pour comprendre l'état d'un repo. 23 tests. Puis premières
   **mutations sécurisées** : `git add`/`commit` **locaux**, gated par `GIT_WRITE_ENABLED`
   (défaut false), même posture sûr-par-défaut que le SQL ; message de commit passé en
   argv (test prouvant qu'un `; rm -rf /` ne s'exécute pas). 29 tests.
   Puis `docker run` **ultra-contraint** (gated par `DOCKER_WRITE_ENABLED` ET une
   allowlist d'images non vide) : aucun flag utilisateur, durcissement figé
   (`--network none`, `--cap-drop ALL`, `no-new-privileges`, limites ressources, pas
   de montage), image validée+allowlistée, `command` en argv isolée dans le conteneur.
   36 tests dont la preuve qu'aucun flag dangereux (`--privileged`/`-v`/`host`) n'est
   émis. Les mutations restantes (`docker build`/`exec`, `kubectl apply`/`delete`/`scale`,
   `git push`/`reset`) — évasion hôte/cluster ou sortantes — restent hors scope.
4. **Skills de domaine** — connaissance reformulée servie par `get_skills` :
   `graphql`, `docker`, `kubernetes`, `cicd`, `sdk`, `uml`, `sql` (drop-in, loader générique).
5. **Diagrammes UML** — `generate_uml` : diagramme de classes **Mermaid** dérivé de la
   structure réelle du code (via l'index tree-sitter), sortie texte confinée aux racines.
6. **Scaffolding d'API** — `scaffold_api` : génère un squelette CRUD idiomatique
   depuis une ressource + champs typés, en **REST (FastAPI/Pydantic v2)** ou
   **GraphQL (schéma Strawberry : type/input/Query/Mutation)** ; code déterministe et
   garanti compilable (test `compile()` sur les deux cibles), entrées validées.
7. **Génération de SDK** — `scaffold_sdk` : client Python typé (dataclass + `httpx.Client`,
   list/get/create/update/delete) consommant l'API générée ; validation partagée avec
   `scaffold_api`, code garanti compilable.
8. **Génération NoSQL** — `scaffold_nosql` : repository MongoDB typé (dataclass + `pymongo`,
   CRUD + `ObjectId`) depuis ressource + champs ; validation partagée, code garanti compilable.

Principe : privilégier l'additif et la dégradation gracieuse. Les langages
étendus et les grammaires optionnelles n'imposent rien à l'installation de base.
### Étape 10 — Pilotage de l'environnement & Toolsmithing

Avec Apple Silicon et les bons connecteurs, Klody pilote l'environnement — et,
surtout, **fabrique ses propres outils** plutôt que de seulement les utiliser.

- **Mac** (`tools/mac_control.py`) — `run_applescript` (toute app scriptable),
  `spotlight_search` (`mdfind`), `run_shortcut` + `list_shortcuts` (passerelle
  Raccourcis → HomeKit / Automator / automatisations), `reveal_in_finder`. Terminal
  déjà couvert par `execute_command`. Garde plateforme hors macOS + blocklist
  AppleScript (destruction / shell / contrôle UI refusés).
- **Maison** (`tools/home_automation.py`) — `mqtt_publish` / `mqtt_subscribe` :
  dénominateur commun d'ESP32, Raspberry Pi, Home Assistant et ponts HomeKit.
  paho-mqtt optionnel, écoute bornée (timeout dur + max messages).
- **Automatisation** (`tools/automation.py`) — `batch_rename`, `organize_directory`
  (par type ou date), `backup_directory` (archive horodatée), `sync_directories`
  (miroir incrémental). Sandboxés, `dry_run` par défaut, fichiers sensibles exclus.
- **Toolsmithing** (`tools/toolsmith.py`) — `scaffold_tool(kind, name, …)` génère un
  artefact réel et testé : `python_script`, `cli`, `api` (FastAPI), `mcp_server`
  (FastMCP), `workflow`, `pipeline` (ETL), `klody_plugin`, `web_interface`. Chaque
  Python généré est valide (vérifié par `compile` dans les tests) ; les artefacts à
  test sont livrés avec leur `pytest`.

## Métriques du bench (étape 1)

Chaque tâche du bench mesure :

- **success** (bool) — la sortie passe le validateur
- **latency_s** (float) — wall-clock total
- **tokens_generated** (int) — total tokens produits par le modèle
- **tokens_per_sec** (float)
- **tool_calls_total** (int)
- **tool_calls_broken** (int) — JSON cassé, schéma non respecté
- **iterations** (int) — nombre de tours ReAct
- **cost_usd_equiv** (float) — équivalent prix API si appelé sur un cloud (référence)

Output : `bench/results/<timestamp>.json` + `bench/results/<timestamp>.md`.

## Tâches du bench

**Easy (5)** — édit localisé, < 30s attendus
1. `easy/rename_var` — renomme une variable dans un fichier Python
2. `easy/add_docstring` — ajoute une docstring à une fonction
3. `easy/fix_typo` — corrige une typo dans un commentaire
4. `easy/add_import` — ajoute un import manquant
5. `easy/add_simple_test` — ajoute un test pytest simple pour une fonction donnée

**Medium (10)** — multi-fichier ou refactor léger, < 2min attendus
6. `medium/extract_function` — extrait du code en fonction réutilisable
7. `medium/convert_loop_to_comprehension` — refactor stylistique
8. `medium/add_type_hints` — ajoute typing à un module
9. `medium/fix_failing_test` — un test existant échoue, corriger le code (pas le test)
10. `medium/add_cli_arg` — ajoute une option `--verbose` à un script argparse
11. `medium/json_to_dataclass` — convertit dict en dataclass
12. `medium/split_module` — sépare un gros fichier en 2 modules
13. `medium/add_logging` — ajoute logging structuré à un script
14. `medium/migrate_print_to_logger` — remplace tous les print par logger
15. `medium/add_error_handling` — ajoute try/except contextualisés

**Hard (5)** — multi-étapes, dépendances, débogage
16. `hard/fix_async_bug` — bug subtil dans code async (race condition)
17. `hard/optimize_n_squared` — détecte algo O(n²) et propose O(n log n)
18. `hard/migrate_sync_to_async` — convertit un module sync en async
19. `hard/api_endpoint_full` — ajoute un endpoint FastAPI complet (route + model + test)
20. `hard/debug_test_suite` — 3 tests échouent pour 3 raisons différentes

## Décisions tranchées

- ❌ **Pas de sous-agents fixes** (Explorer/Editor/Reviewer séparés) — handoff coûteux
- ❌ **Pas de Planner systématique** — actif uniquement sur tâches hard/multi-fichier
- ❌ **Pas de Reviewer-LLM séparé** — le sandbox (tests) sert de verifier en priorité
- ✅ **Constrained generation obligatoire** sur tous les tool calls
- ✅ **Verifier hybride** — sandbox prioritaire, model-fallback (Qwen3-4B) sur tâches sans test
- ✅ **MLX en backend principal**, Ollama conservé en fallback

## Hors-scope explicite (pour l'instant)

- Klody mobile (Tauri iOS/Android) — pas avant que v2 soit stable
- Voice in/out (Whisper + Piper) — nice-to-have, après v2
- Ambient agent (git hooks pré-review) — phase 9+
- LoRA fine-tune sur sessions — phase 9, après stabilisation
