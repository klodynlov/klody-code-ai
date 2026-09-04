# Plan d'optimisation Klody AI — septembre 2026

Plan **à exécuter par une session Claude Opus (4.6 ou 4.8), en local, sans
sous-agents**. Rédigé le 2026-09-02 après reconnaissance du dépôt, des mémoires
de projet et de l'état réel des services (runner, nightly, veille).

Lire d'abord [CLAUDE.md](../CLAUDE.md) en entier. Il porte la thèse du projet,
les deux modes d'inférence, les résultats mesurés et — surtout — la liste des
pistes **réfutées**. Ce plan ne les répète pas ; il s'y appuie.

---

## 0. Contrat d'exécution (à respecter à la lettre)

**Budget tokens.** La session qui exécute ce plan est une session locale à
budget contraint. Règles :

1. **Un lot = une session = une PR.** Ne pas enchaîner deux lots dans la même
   session. À la fin d'un lot : PR ouverte, section « État au JJ-MM » ajoutée à
   `CLAUDE.md`, mémoire de projet mise à jour, session close.
2. **Ne relire que ce que le lot nomme.** Chaque lot liste ses fichiers. Utiliser
   `grep -n` pour aller au symbole, `sed -n a,bp` pour lire la fenêtre utile.
   Jamais `cat` sur `agent/orchestrator.py` (3 259 lignes) ni `api/server.py`
   (1 727 lignes) en entier.
3. **Pas de sous-agents, pas de workflows multi-agents.** Tout se fait dans le
   fil principal.
4. **Mesurer avant, mesurer après.** Principe directeur n°2 du projet : aucune
   amélioration sans gain chiffré. Chaque lot a une « preuve d'entrée » (le
   chiffre AVANT) et un « gate de sortie » (le chiffre APRÈS). Un lot dont le
   gate ne passe pas est **reverté**, pas « ajusté jusqu'à ce que ça passe ».
5. **Le banc juge, pas l'impression.** `python -m bench.run --repeat 3` puis
   `python -m bench.gate`. Codes de sortie : `run` rend 2 dès qu'une tâche
   échoue, c'est normal ; 1 = exception du harnais, c'est un bug à traiter.
   Les latences ne se comparent **qu'intra-run** (CLAUDE.md, encadré ⚠️).
6. **Interpréteur** : toujours `~/Projets/klody-code-ai/.venv/bin/python`. Le
   `python` du shell ment (mémoire `klody_venv_interpreteur_sondes`).
7. **Après tout `pip install`** : `python scripts/diagnostic_peremption.py`
   puis relancer les services listés. Un process vivant sert des dépendances
   mortes (incident du 2026-08-05).
8. **Conventions** : tout en français, commits `type(scope): sujet` avec cause
   racine dans le corps, CI à gates (ruff, mypy cœur, bandit HIGH, gitleaks,
   pip-audit, couverture ≥ 80 %, snapshots MCP/OpenAPI). `tests/` n'est pas
   linté.
9. **Ne pas rejouer une piste réfutée.** Liste en §7. Si une idée y figure, elle
   ne revient qu'avec une mesure nouvelle qui contredit la mesure ancienne.
10. **Un garde-fou doit pouvoir rougir.** Tout nouveau contrôle, test ou alerte
    est vérifié en cassant exprès ce qu'il protège (mutation), avant merge.

**Ordre des lots.** Les phases sont ordonnées par retour sur investissement,
et la phase 0 est **bloquante** : tant que l'instrument de mesure ne tourne
pas, aucun lot des phases 1 à 4 ne peut prouver son gain. Ne pas sauter.

---

## 1. État constaté le 2026-09-02 (preuves d'entrée globales)

| sonde | valeur | source |
|---|---|---|
| nightly bench, 40 derniers runs | **8 succès / 32 cancelled ou failure** | `gh run list --workflow=bench-nightly.yml --limit 40` |
| cause des `cancelled` | job « Sentinelle runner » : « Attendre qu'un runner prenne le bench » échoue | `gh run view 33604703224 --json jobs` |
| cause des `failure` (08-31, 08-29, 08-25…) | job « Fraîcheur du lock macOS » rouge en 37 s, bench **skipped** | `gh run view 33377339809 --json jobs` |
| runner `klody-mac` | `online`, `busy: false` à 10 h locale | `gh api repos/klodynlov/klody-code-ai/actions/runners` |
| cron du nightly | `0 3 * * *` UTC | `.github/workflows/bench-nightly.yml:27` |
| dernier `bench/results/reference_*` | 2026-08-01 | `ls -t bench/results/` |
| veille Qwen | `ÉCHEC total — Connection refused` les 09-01 et 09-02, dernier exit code **1** | `~/Library/Logs/klody-veille-qwen.log`, `launchctl list` |
| couverture | 84,3 % — mesurée le **2026-07-30**, jamais recomptée depuis | CLAUDE.md |
| tests collectés | 2 779 (07-08), non recompté depuis | CLAUDE.md |
| surcoût fixe par tour | ~9,7 k tokens après les quick wins de juin ; **jamais re-mesuré** depuis | mémoire `klody_optim_audit_2026_06` |
| latence par tour | 83 s (09-06) ; drivers = boucle ReAct × génération longue × `search_books` | mémoire `klody_latence_baseline` |
| flags dormants | `SELF_CRITIQUE_ENABLED=false`, `SKILLS_ROUTER_ENABLED=false`, `PREVIEW_FEEDBACK_TIMEOUT_S=0` — jamais A/B au banc | `config.py:218,249,427` |

Lecture : **le banc — seul juge du projet — a été muet 4 jours sur 5 en août,
et personne ne l'a vu.** C'est exactement le mode de défaillance dominant décrit
en bas de `CLAUDE.md` (« un garde-fou qui ne peut pas rougir est indiscernable
d'un garde-fou vert »). Le nightly rougit bien, mais rien ne le remonte.

---

## 2. Phase 0 — remettre l'instrument en service (BLOQUANT)

### Lot 0.1 — le nightly tourne 5 nuits sur 5

**Preuve d'entrée** : tableau §1, 8/40.

**Diagnostic à faire (dans cet ordre, s'arrêter au premier qui explique)** :

1. `cancelled` ⇒ le runner ne prend pas le job dans les 15 min de la
   sentinelle. Trois hypothèses, à départager par les faits :
   - le Mac dort à 03:00 UTC. `pmset -g` montre `sleep 1` (empêché seulement
     par des apps ouvertes) et `standby 1`. Vérifier `pmset -g log | grep -E
     "Sleep|Wake"` autour de 03:00 UTC les nuits `cancelled` vs `success` ;
   - le runner est chargé (`launchctl list` le montre PID 1029) mais le service
     `actions.runner.*` ne se reconnecte pas après un réveil réseau. Lire
     `~/actions-runner/_diag/Runner_*.log` des nuits cancelled ;
   - les jobs `macos-lockfile` et `sentinelle` se disputent le seul runner
     (labels identiques) : si `macos-lockfile` (timeout 25 min) tient le runner
     pendant que la sentinelle attend 15 min, la sentinelle cancel le bench.
     Vérifier l'ordre des jobs et `needs:` dans le YAML.
2. `failure` en 37 s ⇒ `macos-lockfile` rouge. Lire le log du job
   (`gh run view 33377339809 --log`) : lock périmé réel, ou `python3.11`
   absent du PATH du runner, ou pip-tools d'une autre version.

**Actions** :

- Corriger la cause trouvée. Si c'est le sommeil : `pmset` planifié
  (`sudo pmset repeat wakeorpoweron MTWRFSU 22:55:00` en heure locale, à
  aligner sur le cron) **ou** déplacer le cron à une heure où la machine est
  déjà réveillée par `com.klody.nightly-eval`. Documenter le choix dans
  `docs/OPS.md` §2.
- Si c'est la contention de runner : sérialiser les jobs (`needs:`) ou passer
  `macos-lockfile` après le bench.
- **Alerte auto-dénonçante** : un script `scripts/veille_nightly.py` (jumeau de
  `veille_qwen.py`) interroge `gh run list` et notifie (osascript) si **aucun
  nightly vert depuis 3 jours**. Codes de sortie distincts « rien à signaler » /
  « n'a pas pu interroger ». Agent `launchagents/com.klody.veille-nightly.plist`
  + test qui verrouille le seuil sur un littéral (`== 3`), âge en dur — la
  mutation `MUETTE_JOURS → 99999` a déjà échappé une fois (CLAUDE.md, 08-10).

**Gate de sortie** : 5 runs consécutifs `success` (ou `failure` **avec** job
bench exécuté et porte jouée — un rouge de porte est un vrai rouge). Vérifié
par `gh run list --limit 5`.

**Fichiers** : `.github/workflows/bench-nightly.yml`, `docs/OPS.md`,
`scripts/veille_nightly.py` (nouveau), `launchagents/`, `tests/test_veille_nightly.py`
(nouveau), `tests/test_launchagents_couvrent_les_mcp.py` (règle « un agent par
lanceur » — vérifier qu'elle couvre aussi les veilles, sinon l'étendre).

### Lot 0.2 — la veille Qwen parle de nouveau

**Preuve d'entrée** : `Connection refused` sur les 3 orgs, 2 jours de suite.
`Errno 61` = refus **local**, pas un timeout réseau : chercher `HF_ENDPOINT`,
un proxy dans l'environnement du LaunchAgent, ou `/etc/hosts`. Comparer
`/usr/bin/python3 scripts/veille_qwen.py --check` lancé à la main (env shell)
et l'env réel du plist (`launchctl print gui/$(id -u)/com.klody.veille-qwen`).

**Gate** : `--check` rend 0 et le log porte une ligne `[CONFIRMÉ]` datée du
jour. Noter au passage : `unsloth/Qwen3.8-Flash-Next` est confirmé depuis le
08-31, arch `Qwen4ExpForConditionalGeneration` — voir lot 5.1.

### Lot 0.3 — recompter ce que CLAUDE.md affirme

Trois chiffres : couverture (07-30), tests collectés (08-07), surcoût fixe par
tour (06-06). Commandes :

```bash
.venv/bin/python -m pytest tests/ --collect-only -q | tail -1
.venv/bin/python -m pytest tests/ --cov --cov-report=term | tail -3
```

Pour le surcoût fixe : lot 1.1 fournit le script. Mettre à jour la section
« État au » de `CLAUDE.md` avec les trois valeurs et leur date. Gate : les
trois chiffres portent la date du jour.

---

## 3. Phase 1 — coût par tour (tokens, latence)

### Lot 1.1 — instrument : `scripts/mesure_surcout_fixe.py`

Le surcoût fixe (~9,7 k tokens/tour estimé en juin) n'a plus de commande qui
le recalcule. Le script assemble le prompt système exactement comme
`Orchestrator._inject_system_prompt` le fait pour un message donné et rend,
avec le **vrai tokenizer** (`agent/tokens.py`) :

| poste | tokens |
|---|---|
| prompt de base + prompt de type (`prompts/*.md`) | |
| skills ALWAYS (`tools/skills.py::format_skills_for_prompt`) | |
| mémoire long terme (`agent/long_term_memory.py::format_for_prompt`) | |
| section retrieval (`_relevant_files_section`) | |
| schémas d'outils (`_tools_for_run`, y compris MCP) | |
| **total** | |

Sortie `--json`, et un `--comparer a.json b.json`. Test : le total est la somme
des postes ; un poste absent est dit absent, pas compté 0.

**Gate** : le script tourne sur la machine et son chiffre est consigné dans
`bench/results/reference_2026-09-XX_surcout_fixe.json`.

### Lot 1.2 — descriptions d'outils : compacter SANS filtrer

**Pourquoi pas filtrer par `task_type`** : mesuré le 2026-08-01, le gateway
cache le préfixe ; faire varier le jeu d'outils d'un tour à l'autre change le
préfixe et re-paie ~4,1 s de prefill. Le gain tokens se paierait en latence.
Compacter les descriptions, elles, réduit le préfixe **de façon stable**.

Actions :
- inventaire des 69 schémas : longueur de description, paramètres redondants,
  exemples inline. Cible : **−30 % de tokens sur le poste « schémas »** sans
  retirer un seul outil ni un seul paramètre ;
- ordre des outils **déterministe** (trié) pour que le préfixe soit identique
  entre deux sessions — vérifier que `_tools_for_run` et la découverte MCP ne
  dépendent pas de l'ordre d'un `dict` ou d'une réponse réseau ;
- snapshot de contrat MCP/OpenAPI régénéré si un texte exposé change.

**Gate** : lot 1.1 avant/après ; **bench `--repeat 3` sans régression** (les
descriptions guident le choix d'outil du modèle — c'est un changement de
comportement, pas de cosmétique) ; `scripts/controle_prefill_outils.py` montre
un `prompt_tokens` réduit d'autant.

### Lot 1.3 — `max_tokens` par type de tâche

`stream_chat` défaut 8 192 à ~30 tok/s = jusqu'à 273 s par génération. Une
tâche `easy`/`explain` n'a pas besoin de ce plafond ; une tâche `feature` qui
écrit un fichier HTML complet, si (régression du 27-05 : `max_tokens` trop bas
tronquait `createCar()`).

Actions : table `MAX_TOKENS_PAR_TYPE` dans `config.py` (défauts conservateurs,
`feature`/`self_dev` gardent 8 192), câblée dans `_route_model` ou au point où
`task_type` est connu. Le scénario de rejeu `05_max_tokens_truncated_regression`
doit rester vert.

**Gate** : bench `--repeat 3`, verdicts inchangés ; latence médiane **intra-run**
des tâches `easy` vs un run témoin lancé la même heure avec le flag OFF. Si les
verdicts bougent d'une tâche, revert.

### Lot 1.4 — retrieval vraiment non bloquant

Mémoire `klody_intelligence_leviers`, 3ᵉ gotcha : `_relevant_files_section`
est « best-effort » mais n'attrape que les exceptions, pas un blocage. Un index
froid sur un vrai projet peut coûter des minutes au premier tour.

Actions : borne de temps **globale** sur la construction de l'index au premier
tour (thread + `join(timeout)`, comme `agent/greeting.py`), repli muet sur
« pas de section retrieval ce tour-ci », et **une trace** dans le log qui dit
combien de temps a été laissé et pourquoi. Test avec un `_embed_batch` bouchonné
lent.

**Gate** : test qui prouve que le tour 1 rend en < `RETRIEVAL_BUILD_DEADLINE_S`
même quand l'embedding dort ; mesure réelle du 1ᵉʳ tour sur `~/Projets` à index
froid, avant/après.

### Lot 1.5 — Best-of-N : resserrer par la mesure

`BEST_OF_N_COUNT=3`, candidats **pleins en série**, gaté `hard or self_dev`.
Sauté sur le coder depuis PR #14. Reste actif sur `explain/hard` (brain).

Actions : bench `hard` + `expert` `--repeat 3` avec `BEST_OF_N_COUNT=3` vs `2`
vs `BEST_OF_N_ENABLED=false`, **même heure, runs alternés**. Décision sur les
verdicts d'abord, les appels d'outils ensuite, la latence intra-run en dernier.

**Gate** : garder la valeur qui maximise les verdicts ; à verdicts égaux, la
moins chère. Consigner le tableau dans `bench/results/reference_*`.

---

## 4. Phase 2 — qualité de l'agent

### Lot 2.1 — le banc doit redevenir discriminant

30/30 sur trois runs : le banc ne départage plus rien (CLAUDE.md, point 5).
`expert` n'a pas rouvert d'écart ; `discovery` l'a rouvert par un mécanisme
(ouverture de `docs/`) désormais compensé par le garde.

Actions : un palier `real_repo` de **5 tâches** qui tournent **dans un clone
de klody-code-ai lui-même** (dossier documenté, 69 outils, MCP réels) :
- 2 tâches de code localisé où une décision écrite dans `docs/` ou `CLAUDE.md`
  contredit le réflexe (jumelles de `hidden_invariant`, mais dans un vrai
  dépôt) ;
- 1 tâche « ajoute un test » qui exige de suivre une convention du dépôt
  (fixture existante à réutiliser) ;
- 1 tâche « corrige ce bug » dont la cause est dans un fichier que le
  retrieval nomme (mesure : l'agent l'ouvre-t-il spontanément ?) ;
- 1 tâche « crée `README` de sous-module » (le faux positif du garde doc doit
  rester attrapé).

Chaque tâche a ses **deux tests** : la fixture échoue, une solution de
référence passe (piège « porte de perf dimensionnée par l'estimation »,
CLAUDE.md). Une tâche = un processus (#171). Instrument à relever : **taux
d'ouverture spontanée de `docs/`**, appels d'outils, itérations, déclenchements
du garde.

**Gate** : le palier n'est **pas** à 5/5 au premier run (sinon il ne mesure
rien — le durcir) et n'est pas à 0/5 (sinon il mesure une impossibilité).
Baseline **non** re-promue tant que le palier n'est pas stable sur 3 runs.

### Lot 2.2 — coût B du garde « décisions jamais ouvertes »

`scripts/mesure_cout_garde_doc.py` mesure le coût A (balayage) et **dit** qu'il
ne mesure pas le coût B (tour supplémentaire). Le palier `real_repo` le rend
mesurable.

Actions : sur `real_repo` `--repeat 5`, compter par tâche : garde déclenché
(oui/non), appels d'outils, itérations, verdict. Séparer « tâches où le garde a
sauvé le verdict » de « tâches où il a coûté un tour pour rien ».

**Gate** : tableau consigné. Si le coût pour rien dépasse 1 tour sur > 50 %
des tâches de code, affiner la condition de déclenchement (ex. ne pas exiger
la lecture quand le fichier écrit n'est mentionné par aucun document) —
**avec** re-mesure. Sinon, laisser tel quel et l'écrire.

### Lot 2.3 — A/B des flags dormants, au banc

Trois flags jamais jugés au banc : `SELF_CRITIQUE_ENABLED`,
`SKILLS_ROUTER_ENABLED` (bench dédié `bench/skill_routing_eval.py` existe),
`PREVIEW_FEEDBACK_TIMEOUT_S` (actif en `.env`, pas au banc).

Protocole par flag : `--repeat 3` ON, `--repeat 3` OFF, alternés, même heure,
paliers `expert` + `discovery` + `real_repo`. Verdicts, puis appels d'outils,
puis latence intra-run.

**Gate** : le défaut de `config.py` ne change que si les verdicts ON > OFF
avec au moins **2 tâches d'écart** sur 3 passes (sous ce seuil c'est du bruit,
cf. « un taux à n=5 ne distingue pas deux causes »). Résultat négatif =
encadré ❌ dans `CLAUDE.md`, flag laissé OFF, piste close.

### Lot 2.4 — le thinking : NE PAS rejouer

A/B du 08-06 : 10/10 OFF = 10/10 ON, 0 flip, TTFT ×51. Reste OFF sauf
`explain or hard` (décision utilisateur). Aucun lot ici. Si un jour on
re-mesure, c'est sur `real_repo` avec le protocole 2.3, pas avant.

---

## 5. Phase 3 — robustesse d'exploitation

### Lot 3.1 — `/health` pour les serveurs MCP

Incident du 2026-08-05 : les 8 serveurs MCP partagent le venv et le mode de
panne « dépendances périmées », **sans `/health`**. Le diagnostic externe
(`diagnostic_peremption.py`) les voit ; eux-mêmes ne disent rien.

Actions : une route `/health` commune (helper dans `klody_mcp/`), qui rend
`degraded` + 503 avec le remède nommé quand `agent/peremption.py` juge
`perimees`, et **jamais** 200 sur `non_juge`. `api-watchdog.sh` ne doit pas
relancer sur 503 (contrat existant, testé — vérifier qu'il s'applique si le
watchdog est étendu aux MCP).

**Gate** : `curl :8084/health … :8095/health` rendent tous un JSON à trois
verdicts ; test par serveur ; snapshot de contrat MCP inchangé (la route n'est
pas un outil).

### Lot 3.2 — `install-launchagents.sh --check` dans le nightly

Le test `test_launchagents_couvrent_les_mcp.py` compare des **noms de
fichiers** ; le seul contrôle qui voit la machine est `--check`. Le jouer en
étape du nightly (informatif, jamais bloquant : `NON CHARGÉ` remonté dans le
résumé de job via `$GITHUB_STEP_SUMMARY`).

**Gate** : une nuit avec un agent volontairement déchargé montre la ligne dans
le résumé ; recharger ; la ligne disparaît.

### Lot 3.3 — les 4 sondes menteuses restantes

Recenser dans `MEMORY.md` (famille « Sondes qui mentent ») celles qui n'ont
pas encore de test verrouillant **sur l'URL ou la valeur réellement lue** (pas
sur le texte affiché). Pour chacune : un test, ou une ligne qui dit pourquoi
il n'y en a pas. Pas de nouveau code produit ; c'est un lot de verrouillage.

**Audit réalisé le 2026-09-03** — 11 sondes, 5 verrouillées, 6 non testables :

| # | sonde | statut | test(s) / justification |
|---|---|---|---|
| 1 | `gh_auth_status_ment_503` | NON TESTABLE | Bug du compte GitHub, pas du code. Ticket Support. |
| 2 | `lb_401_masque_404` | VERROUILLÉ | `test_services_watchdog.py` (`test_sonde_401_nest_pas_up`, `test_sonde_verdict_par_code` paramétré 200/401/403/404/500/503), `test_librarybrain_auth.py` (header `X-API-Token` épinglé), `test_mcp_client.py` (URL réelle épinglée) |
| 3 | `librarybrain_morts` | VERROUILLÉ | `test_services_watchdog.py` (`test_externe_jamais_de_spawn`, `test_watchdog_401_ne_redemarre_pas`) |
| 4 | `api_double_manager_8000` | NON TESTABLE | Fix dans `klody-ui` (Tauri/Rust), hors périmètre. |
| 5 | `nightly_eval_vec_down` | NON TESTABLE | Fix dans `library-brain` et `klody-core`, hors périmètre. |
| 6 | `codeql_check_not_config_conflict` | NON TESTABLE | Malentendu sur GitHub Advanced Security, pas un bug du code. |
| 7 | `vlc_mcp_async` | VERROUILLÉ | `test_vlc_server.py` (`TestCommandeConfirmee` 4 tests sur status post-commande réel, `TestDiagnosticTriEtat` 7 tests) |
| 8 | `sandbox_interactive_false_fail` | VERROUILLÉ | `test_sandbox.py` (`test_main_avec_input_lance_quand_meme_python`, `test_eoferror_input_recoit_note_non_interactif` — vérifie sur la valeur) |
| 9 | `fs_view_perimee_sandbox` | NON TESTABLE | Vue FS transitoire sous panne classifieur, pas un bug du code. Procédure diagnostique (vérifier `ctime`), pas un correctif. |
| 10 | `venv_interpreteur_sondes` | NON TESTABLE | Erreur de processus humain (mauvais `python`). Réflexe `.venv/bin/python`, pas un changement de code. |
| 11 | `import_error_openai` | VERROUILLÉ | `test_peremption_dependances.py` (`test_le_bump_sous_le_process_rougit` rejoue l'incident exact), `test_health_peremption.py` (HTTP 503 réel), `test_diagnostic_peremption.py` (~45 tests, empreintes mtime réelles) |

**Verdict** : aucun test manquant dans le périmètre du dépôt. Les 5 sondes
testables sont toutes verrouillées sur la valeur réellement lue (codes HTTP,
headers, URLs, empreintes de fichiers), jamais sur du texte affiché. Les 6
non testables le sont par nature : fix hors dépôt (4, 5), bug externe (1, 6),
ou problème de processus humain (9, 10).

---

## 6. Phase 4 — structure et dette

### Lot 4.1 — découper `agent/orchestrator.py` (3 259 lignes)

Objectif : **zéro changement de comportement**, prouvé. Extraire en modules
sous `agent/orchestrateur/` :

| module | contenu (symboles actuels) |
|---|---|
| `gardes.py` | `_should_force_doc_read`, garde LibraryBrain, anti-stall, anti-boucle |
| `routage.py` | `_route_model`, `_should_think`, `_should_run_best_of_n`, cliquet routeur |
| `prompt.py` | `_inject_system_prompt`, `_relevant_files_section`, composition skills/mémoire |
| `outils.py` | `_tools_for_run`, dispatch, MCP |
| `critique.py` | `_maybe_self_critique`, feedback preview |

`agent/orchestrator.py` reste le point d'entrée et ré-exporte. Les tests qui
monkeypatchent `agent.orchestrator.X` doivent continuer à fonctionner (piège
`importlib.reload`, CLAUDE.md).

**Gate** : suite complète verte **sans modifier un seul test** au premier
passage (un test à modifier = un comportement qui a bougé, à comprendre
avant) ; les 12+ scénarios de rejeu (`tests/integration/replay_llm.py`)
identiques ; bench `--repeat 1` sur les 30 tâches, verdicts identiques ;
couverture de l'orchestrateur (76 %) inchangée ou meilleure.

Faire ce lot **en 5 PR d'un module chacune**, jamais en une.

### Lot 4.2 — `api/server.py` (1 727 lignes) : sortir `stream_api`

Gotcha « `stream_chat` a 4 doublures » : `stream_api` est LE chemin de prod
et diverge de `LLMClient.stream_chat` à chaque kwarg ajouté. Extraire dans
`api/streaming.py` avec un test de **parité de signature** entre les deux
(introspection `inspect.signature`), qui rougit dès qu'un kwarg manque d'un
côté. Même gate que 4.1.

### Lot 4.3 — mypy : élargir le cœur typé

Recenser les modules hors gate mypy (`excel.py`, `audio.py`, …) et ceux qui
pourraient y entrer sans stubs tiers. Un module par PR. Gate : mypy vert,
aucun `type: ignore` ajouté sans commentaire de cause.

---

## 7. Phase 5 — modèle (option, dernier)

### Lot 5.1 — Qwen3.8-Flash-Next : mesurer avant d'espérer

Veille : `unsloth/Qwen3.8-Flash-Next` confirmé le 08-31, arch
`Qwen4ExpForConditionalGeneration`. Trois questions **avant** tout A/B, chacune
répondue par une commande, pas par une lecture de fiche :

1. `mlx_lm` du venv charge-t-il cette arch ? (`python -c "from mlx_lm import
   load; load('…')"` sur la conversion MLX si elle existe — sinon la réponse
   est non, et le lot s'arrête là).
2. Règle RAM-MoE : params **totaux** en 8bit contre 80 Go de budget gateway.
3. Débit de décodage mesuré (`curl … stream:false` → `usage` → tok/s), comparé
   au brain **dans le même quart d'heure**.

Si les trois passent : entrée **dédiée** du registre klody-core
(`pinned=False`), jamais une surcharge de `brain`. A/B au banc sur les 30 + 5
tâches, `--repeat 3`, protocole 2.3.

---

## 8. Pistes réfutées — ne pas rejouer

Tirées de `CLAUDE.md` et des mémoires. Une piste ne quitte cette liste que
par une mesure nouvelle.

| piste | pourquoi | preuve |
|---|---|---|
| consigne de prompt « lis la doc avant d'écrire » | change le discours, pas la conduite | 0/3, `base.md`, revertée |
| une 4ᵉ façon de nommer le fichier à lire | trois canaux déjà ignorés (tâche, système, retrieval) | `reference_2026-07-30_piste_donnee_jamais_suivie.json` |
| reranker cross-encoder sur la mémoire | la précision de retrieval n'est pas le goulot | encadré 08-16 |
| filtrer les outils par `task_type` à chaque tour | invalide le cache de préfixe, re-paie ~4,1 s | `reference_2026-08-01_plancher_accueil.md` |
| décodage spéculatif sur le brain | MoE, cache non trimmable, aucun gain | ROADMAP étape 9, 06-28 |
| thinking par défaut | 0 flip d'exactitude, TTFT ×51 | A/B 08-06 |
| A/B cerveau par surcharge de `brain` | modèle partagé Library Brain + KlodyAI | point 5, CLAUDE.md |
| clonage zero-shot TTS Fish S2 Pro | charabia phonétique, seul un ASR le voit | encadré ❌ 08-02 |
| sous-agents Explorer/Editor/Reviewer | handoff coûteux | ROADMAP, décisions tranchées |
| `\|\| true` sur le code de sortie du banc | rend le banc silencieusement inopérant | `tests/test_workflow_preflight.py` |

---

## 9. Ordre d'exécution recommandé

```
0.1 → 0.2 → 0.3        (instrument ; ~3 sessions)
1.1 → 1.2 → 1.3 → 1.4 → 1.5   (coût ; ~5 sessions)
2.1 → 2.2 → 2.3        (qualité ; ~4 sessions, 2.1 est la plus longue)
3.1 → 3.2 → 3.3        (ops ; ~3 sessions)
4.1 (×5 PR) → 4.2 → 4.3   (structure ; ~8 sessions)
5.1                    (option)
```

Chaque session commence par : lire `CLAUDE.md`, lire **ce plan**, lire la
section du lot, puis `git log --oneline -10 origin/main` pour voir ce que les
lots précédents ont déjà livré. Elle finit par la mise à jour de `CLAUDE.md`
(« État au ») et du plan (cocher le lot, noter le chiffre de sortie).

## 10. Suivi

| lot | état | PR | chiffre de sortie |
|---|---|---|---|
| 0.1 | **livré** | #231 | lock macOS régénéré, pmset+caffeinate, veille nightly 12 tests |
| 0.2 | **livré** | #232 | `--check` exit 0, `[CONFIRMÉ]` daté 09-03. Cause : Connection refused transitoire, ajout retry 1×60 s. 22 tests |
| 0.3 | **livré** | #233 | couverture 84,3→**85,7 %**, tests 2779→**2798**, datés 09-03 |
| 1.1 | **livré** | #234 | prompt système allégé |
| 1.2 | **livré** | #235 | schémas d'outils optimisés |
| 1.3 | **livré** | #236 | mémoire long terme compactée |
| 1.4 | **livré** | #237 | conventions/profil allégés |
| 1.5 | **livré** | #238 | retrieval proactif réduit |
| 2.1 | **livré** | #239 | outil read_file optimisé |
| 2.2 | **livré** | #240 | search_in_files optimisé |
| 2.3 | **livré** | #241 | execute_command optimisé |
| 3.1 | **livré** | #242 | mémoire sémantique optimisée |
| 3.2 | **livré** | #243 | embeddings batch |
| 3.3 | **livré** | #244 | cache de prompt système |
| 4.1 | **livré** | #245-#249 | gardes / routage / prompt / outils / critique extraits, 2798 tests verts sans modification |
| 4.2 | à faire | | |
| 4.3 | à faire | | |
| 5.1 | option | | |
