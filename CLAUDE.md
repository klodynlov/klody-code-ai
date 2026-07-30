# Klody Code AI — mémoire de projet

Agent de code 100 % local sur Apple Silicon (M5 Max 128 Go). Le pari du projet :
un modèle local devient fiable par l'**orchestration** (routeur adaptatif, boucle
ReAct qui va au bout, gardes anti-stall), pas par la taille du modèle. Toute
proposition qui sacrifie le « 100 % local » sacrifie la thèse du projet.

Docs de fond : [ROADMAP.md](ROADMAP.md) · [README-local-ai.md](README-local-ai.md) ·
[bench/README.md](bench/README.md) · [docs/OPS.md](docs/OPS.md)

## Conventions

- **Tout est en français** : code, commentaires, docstrings, tests, messages de
  commit, PR. Les commentaires expliquent *pourquoi*, en citant l'incident quand
  il y en a eu un (« vécu le 2026-07-03 : … »). C'est la mémoire du projet.
- **Commits** : `type(scope): sujet`, corps qui explique la cause racine. Squash
  merge, le numéro de PR est ajouté au titre.
- **CI à gates** : ruff, mypy (cœur typé), bandit HIGH, gitleaks, pip-audit
  `--strict`, **couverture ≥ 80 %**, snapshots de contrat MCP/OpenAPI.
  `tests/` n'est PAS linté par la CI.
- Branch protection sur `main`, commits signés.

## Le piège n°1 : deux modes d'inférence, deux conventions

| | Mode gateway (nominal) | Mode autonome |
|---|---|---|
| Point d'entrée | Klody Core `:8090` (hors dépôt) | `start-local-ai.sh` → `:8080`/`:8081` |
| `MLX_MODEL` | un **ALIAS** (`brain`, `coder`) | un **id HuggingFace** |

Les deux sont incompatibles et échouent de façon asymétrique :
`start-local-ai.sh` passe `MLX_MODEL` à `mlx_lm.server --model`, donc `brain` le
fait échouer ; le gateway, lui, rejette un id HF figé dès la première bascule de
modèle (404 « modèle inconnu » — incident du 2026-07-03, toutes les tâches `code`
tombées). Détail complet dans `README-local-ai.md`.

## Réglages non négociables

- `MLX_CHAT_TEMPLATE_ARGS='{"enable_thinking": false}'` — sans ça Qwen3.6
  raisonne sans jamais répondre.
- `MLX_DRAFT_MODEL` doit rester **vide** sur le cerveau. Le MoE produit un prompt
  cache `ArraysCache` non-trimmable ; `mlx_lm` lève sur *chaque* requête. Tenté le
  2026-06-28, échec total. Le décodage spéculatif n'avait de toute façon montré
  aucun gain sur MoE (ROADMAP étape 9).

## Le banc de mesure

Le principe directeur n°2 dit « aucune amélioration ne passe sans gain chiffré au
bench ». Il est de nouveau applicable depuis les étapes 12-13.

```bash
python -m bench.run --category easy --repeat 3 --label qwen   # dégrossir
python -m bench.run --repeat 3 --label qwen                   # les 20 tâches
python -m bench.gate                                          # non-régression vs baseline
python -m bench.compare -a bench/results/*qwen*.json -b bench/results/*oss*.json
```

`--repeat N` fait **N passes complètes**, pas N exécutions d'affilée par tâche —
répéter dos à dos sert depuis un cache de prompt chaud et fausse la latence.
Chaque run enregistre sa **provenance** (modèle réellement servi, résolu derrière
l'alias par une complétion d'un token).

Cinq paliers, 30 tâches : `easy`, `medium`, `hard` (les 20 de la baseline),
`expert` (le réflexe est faux), `discovery` (la contrainte n'est pas dans
l'énoncé). Le gate n'intersecte que les `task_id` communs et annonce « N hors
baseline, non jugée(s) » — les 10 tâches des deux nouveaux paliers ne sont donc
pas jugées tant qu'une baseline ne les inclut pas.

### ⚠️ `bench.run` MESURE, `bench.gate` JUGE — deux codes de sortie, un seul verdict

`bench.run` rend **2 dès qu'une tâche échoue** (`bench/run.py:414`, verrouillé par
`tests/test_bench_expert_tasks.py`). C'est le bon contrat en local. Ce n'est **pas**
un verdict de CI : le juge est la porte, seule à connaître la baseline.

Ces deux critères se sont contredits le jour où la baseline est passée à 30 tâches
**en y gelant `discovery/hidden_invariant` comme échec attendu** : depuis,
`bench.run` ne peut plus rendre 0, donc le nightly ne pouvait plus être vert, même
sur un run parfait — et le rouge faisait SAUTER l'étape de non-régression, c'est-à-
dire exactement l'inverse du but. Constaté le 2026-07-30 sur le run 30532826914 :
**29/30**, porte rejouée à la main sur le JSON produit ⇒ `Δ +0,0 %`, aucune
régression, job rouge quand même.

L'étape accepte donc 0 et 2, et **rien d'autre** : 1 = exception du harnais.
⚠️ Un `|| true` confondrait les deux et rendrait le banc silencieusement inopérant
— c'est la panne la plus coûteuse de ce dépôt (~13 % pendant des mois, le banc se
mesurant lui-même sans que rien ne rougisse). Verrouillé par
`tests/test_workflow_preflight.py::TestCodeDeSortieDuBanc`.

### ⚠️ Les latences ne sont PAS comparables d'un run à l'autre

Mesuré le 2026-07-30 : **la même tâche `discovery/hidden_invariant` a rendu
34 s dans un run et 119 s dans un autre**, sans qu'aucune variable de la tâche
ni du modèle ne change. Un run complet montrait 2-3× la latence de référence
sur les cinq paliers à la fois, y compris sur des tâches où rien n'avait bougé.

Conséquence : **tout jugement de vitesse doit être INTRA-RUN.** Comparer la
latence d'une tâche mesurée aujourd'hui à celle d'un run d'hier ne dit rien.

Ça a produit une conclusion fausse qu'il a fallu retirer d'une PR : la latence
de `first_write_method` (70-82 s) contre celle de `hidden_invariant` (34-38 s)
avait été lue comme « l'agent travaille deux fois plus quand il explore ». Les
deux tâches mesurées **dans un même run** coûtent 119,5 s et 119,8 s — soit
rigoureusement la même chose. L'écart était de la dérive d'environnement, pas
du comportement.

Ce qui reste comparable entre runs : les **verdicts** (succès/échec), les
**itérations**, les **appels d'outils**, et les **traces d'exploration**. Ce
sont eux qui portent les conclusions de `bench/results/reference_*.json` ; la
latence n'en porte aucune.

⚠️ `bench.compare` affiche des deltas de latence entre deux runs. Ils sont
donc à traiter comme du bruit sauf écart énorme et répété — la colonne utile
est le taux de succès, pas la vitesse.

## État au 2026-07-30

Couverture **84,3 %** (gate 80), **2313 tests** (`pytest tests/ --collect-only`).
Huit PR (#162→#169) ont remis l'instrumentation en service : gate de
non-régression opérationnel, `bench.compare` écrit, `--repeat` + provenance,
sentinelle runner, et couverture de `semantic_memory` (98 %), `embeddings`
(100 %), `audio` (96 %), `orchestrator` (76 %).

Sept PR (#171→#177) ont rendu le banc capable de mesurer autre chose que
lui-même. Le point de départ : il rendait **~13 %** depuis des mois, et c'était
un artefact — `FileManager.allowed_roots` est figé dans `__init__`, or toutes
les tâches tournaient dans un processus partagé, si bien que les tâches 2..N
travaillaient sur un workdir déjà supprimé. Une tâche = un processus (#171)
⇒ **20/20**, puis 60/60 sur 3 passes (#173).

Le banc saturé ne départageait plus rien, d'où deux paliers : `expert` (#174,
**5/5** — n'a PAS rouvert d'écart, l'empilement de difficulté ne suffit pas) et
`discovery` (#175, **3/4**). Le seul échec, `discovery/hidden_invariant`, est
reproductible : **0/8**. Son témoin `first_write_method` (#177), identique en
tout sauf qu'aucune méthode d'écriture n'y est imitable, fait **4/4**.

Le premier nightly complet sur les 30 tâches (run 30532826914, 2026-07-30) le
confirme d'une source indépendante : **29/30**, seul `hidden_invariant` échoue
(« valeur non copiée : la liste est partagée »), `first_write_method` passe. Porte
verte, `Δ +0,0 %`.

> **Le mode d'échec établi : un contexte local suffisant supprime le besoin de
> chercher.** L'agent lit `cache.py`, y trouve une ligne qui ressemble à la
> réponse, et n'ouvre jamais `docs/` — sept fois sur sept, alors que
> `📁 docs/` figure dans la sortie de `list_files` qu'il vient de recevoir.
> C'est le cas le plus fréquent en vrai, puisqu'on demande presque toujours
> d'étendre du code existant.

Mesures de référence dans `bench/results/reference_*.json` (convention : ce
préfixe est dé-ignoré, cf. `.gitignore`).

### Ce qui reste à faire

1. **Enregistrer un runner self-hosted** — labels `self-hosted, macOS, klody`.
   Sans lui le nightly fait la queue puis est annulé ; le job `verify-runner` le
   dit désormais explicitement au lieu de laisser un historique vert-vide.
2. ~~Promouvoir une baseline~~ — **fait** (#172), 20/20 committée. Reste à
   décider si les 10 tâches `expert` + `discovery` y entrent : elles sont
   mesurées (5/5 et 4/5 stables sur 3 passes) donc gelables, mais une baseline
   à 30 fige aussi l'échec connu de `hidden_invariant`.
3. ~~Trancher l'A/B cerveau~~ — **clos par décision** le 2026-07-29 : on garde
   Qwen3.6-35B-A3B. Trois raisons, dans l'ordre où elles ont compté : à 20/20
   partout et `tool_calls_cassés` à 0, le banc n'avait **aucune marge** pour
   départager deux modèles (c'est ce qui a motivé les paliers `expert` et
   `discovery`) ; gpt-oss-120b n'est pas sur la machine ; et `brain` est le
   modèle **partagé** avec Library Brain et KlodyAI, donc basculer l'alias
   changeait ces applications aussi. Depuis, `KLODY_CORE_BRAIN_MODEL` existe
   côté klody-core — un futur A/B passe par une entrée dédiée du registre,
   pas par une surcharge de `brain`.

## Pièges qui coûtent du temps

- **zsh n'active pas les commentaires en interactif.** Coller un bloc avec des
  lignes `#` les exécute. `setopt interactive_comments` dans `~/.zshrc`.
- **Vérifier le venv actif** avant `python -m bench.run` : le module se résout sur
  le cwd, et une autre venv du Mac (`local-suno`) traîne dans le PATH.
- **Un `importlib.reload` recrée les classes du module** : tout `pytest.raises`
  qui a importé l'exception par son nom cesse de la reconnaître. Poser les
  attributs sur le module vaut mieux que le recharger.
- **Une dépendance optionnelle dans un `try` groupé emporte ses voisines** :
  `numpy` est importé dans le même `try` que `librosa`, donc absent lui aussi si
  `librosa` manque.
- **Une couverture basse décrit d'abord l'environnement de test**, pas la qualité
  du code. Trois modules à 18-28 % l'étaient parce qu'un paquet manquait en CI.
  Recette dans `tests/fake_klody_memory.py` et `tests/fake_audio_libs.py` :
  doubler *uniquement* le paquet absent, garder réels SQLite/numpy/les fichiers.
- **Les flags de garde sont remis à zéro en fin de run** (`_catalog_missed`,
  `_content_searched`, anti-stall) : les tester après `orch.run()` ne prouve rien,
  il faut chercher la trace laissée en mémoire.
- **Une porte de perf se DIMENSIONNE par la mesure, jamais par l'estimation.**
  Une tâche `expert` bornait un dédoublonnage quadratique à N=4000 en supposant
  ~5 s : mesuré, **0,087 s** — `in` sur une liste compare les dicts côté C. La
  porte ne se serait jamais fermée, et la tâche aurait rendu un ✅ crédible et
  faux. Corollaire : une tâche de banc a besoin de DEUX tests — la fixture doit
  échouer, et une solution de référence doit passer. C'est la seconde moitié qui
  a trouvé ce défaut et deux autres (`spec_beyond_tests` rejetait sa propre
  solution correcte ; `copy.copy` passait une tâche qui exige `deepcopy`).
- **Ollama n'est PAS requis.** `SEMANTIC_MEMORY_PROVIDER` vaut `st` par défaut —
  sentence-transformers en processus, même modèle bge-m3, `cos(ollama, st) =
  1.0000` mesuré. Vérifié le 2026-07-30 : Ollama n'est même pas installé sur la
  machine, et `embeddings.is_available()` rend `True` (1024 dimensions, norme
  1.0). L'en-tête de `bench-nightly.yml` le liste encore comme prérequis de
  setup ; c'est faux depuis le passage à `st`.
- **Un AVERTISSEMENT qui décrit un problème inexistant coûte autant qu'une
  erreur fausse.** Le nightly criait « Ollama injoignable — embeddings dégradés »
  alors que rien n'était dégradé. Le 2026-07-30, ce faux avertissement en tête du
  log a orienté tout le diagnostic d'un 1/5 vers Ollama ; la cause était
  `MLX_CODE_BASE_URL`. ⚠️ Et j'ai ÉDITÉ cette ligne une heure avant de la
  corriger, pour la rendre plus précise, sans vérifier que sa prémisse était
  vraie. Deux fois le même angle mort dans la même journée : **croire un message
  au lieu de vérifier ce qu'il affirme.**
- **Un message d'erreur qui nomme la mauvaise dépendance coûte une enquête
  entière.** `agent/llm.py` affichait « ✗ Impossible de joindre Ollama » sur
  TOUTE `APIConnectionError` — hérité du mode ollama, alors qu'en `BACKEND=mlx`
  l'appel va au gateway sur `:8090` et qu'Ollama ne sert que les embeddings
  bge-m3 (best-effort, `tools/embeddings.py`). Le 2026-07-30 le nightly rend
  1/5 avec ce message en tête ; Ollama était effectivement éteint, ce qui rendait
  la fausse piste **crédible**. La vraie cause était la saturation mémoire du
  gateway. Le message nomme désormais le backend réellement visé.
- **Le banc en CI concurrence le gateway pour la mémoire unifiée.** Constaté le
  2026-07-30 : `budget=80 resident=74 libre=6`, une requête encore en vol, et
  des `APIConnectionError` en cascade — une tâche cassée APRÈS 21 s (connexion
  perdue en cours d'appel), trois autres en moins de 3,5 s (refus immédiat).
  Le runner travaille dans son propre checkout avec son propre venv, donc un
  second jeu de processus torch face à brain (44 Go) + coder (30 Go).
  ⚠️ **Ne pas transformer ça en garde-fou bloquant** : vingt minutes plus tard
  le même gateway affichait `libre=36` — il évince ses workers À LA DEMANDE, donc
  une jauge lue à l'instant t ne prédit rien. C'est le raisonnement déjà écrit
  dans `preflight.py` côté local-suno. Le workflow en garde une **trace** dans
  ses logs, jamais un refus ; le vrai remède serait de sérialiser, pas de mesurer.
  - ⚠️ **`libre` du gateway est un budget VIRTUEL, pas de la RAM libre.**
    `libre=6` veut dire `80 − 74 résidents`, donc **les deux modèles chargés** —
    l'état NOMINAL du banc, pas un état critique. Comparer ce chiffre à un
    `available` système (74,5 Go le 2026-07-30) compare deux axes différents ;
    je l'ai fait, et ça inverse la lecture du run.
- ⚠️ **Le préflight du nightly FABRIQUAIT la condition qui le faisait échouer.**
  Le 2026-07-30, run 30531761423 rouge sur « L'alias 'coder' ne résout pas —
  vérifier le resolver du gateway ». Le resolver allait parfaitement bien. La
  réponse réelle était un **503 « RAM insuffisante pour coder (~30 Go) : libre
  36/80 Go virtuel, RAM réelle 41 Go (plancher 12) »** — raté d'**1 Go**.
  - **Deux pannes derrière un seul `curl -sf`.** Un 404 « modèle inconnu » dit
    que l'alias ne résout pas — c'est la seule raison d'être du contrôle
    (incident 2026-07-03). Un 503 RAM prouve **l'inverse** : le gateway a nommé
    l'alias et connaît son empreinte. `-f` rend le même code d'erreur pour les
    deux, et `-s … > /dev/null` jette le message qui les sépare.
  - **Pourquoi le préflight se sabote** : il ping `brain` (44 Go) puis `coder`
    neuf secondes plus tard. Or `vm_stat` SOUS-ESTIME la RAM disponible juste
    après un gros chargement — les pages fraîchement touchées comptent `active`
    et n'ont pas encore vieilli vers `inactive`, la file que somme le garde-fou
    anti-OOM (`klody-core/gateway/sysmem.py`, `free + inactive + speculative`).
    Mesuré : **41 GiB à t+9 s** (refus), **62 GiB à t+2 min**, `coder` chargé en
    8,3 s. Rien ne s'était libéré entre-temps.
  - **La co-résidence est une VRAIE contrainte, pas un artefact** : `brain` est
    `pinned=True` (`gateway/config.py:132`, modèle partagé Library Brain +
    KlodyAI), et `pinned` bloque l'éviction AUTOMATIQUE — le chemin exact d'une
    requête `coder`. Le banc a donc besoin des deux résidents (44 + 30 sur 80).
    D'où un **réessai** (6 × 60 s), pas un contournement.
  - Le contrôle est verrouillé par `tests/test_workflow_preflight.py` : il
    EXÉCUTE le bloc `run:` extrait du YAML avec un `curl` bouchonné, sous
    `/bin/bash` 3.2 (la version du runner). 404 échoue toujours **sans réessai**.
  - ⚠️ Piège rencontré en écrivant ce bouchon : `reponse=$(curl …)` tourne dans
    un **sous-shell**, donc un compteur d'appels en variable est remis à zéro et
    le bouchon rend toujours la première réponse — tous les scénarios d'échec
    passaient au vert. Compteur sur fichier.
- **Une consigne ajoutée au prompt peut changer le DISCOURS sans changer la
  CONDUITE.** Mesuré : `base.md` enrichi de « ouvre la documentation avant
  d'écrire » → l'agent répond « je vais d'abord explorer le projet » aux trois
  passes, puis lit les deux mêmes fichiers et écrit. 0/3, tracé identique,
  reverté.
- ⚠️ **La piste « `feature.md` contredit `base.md` » est RÉFUTÉE — ne pas y
  repartir.** `compose_system_prompt` place bien le prompt de type APRÈS
  `base.md`, et `feature.md` ouvre bien sur « Agis d'abord. Lance directement un
  tool_call. » Mais `hidden_invariant` (❌ 0/9) et son témoin
  `first_write_method` (✅ 4/4) routent **tous deux** en `easy · feature ·
  max_iter=6` : même prompt assemblé, même budget d'itérations. La contradiction
  s'applique aux deux et ne peut donc pas les départager. Vérifié le 2026-07-30
  en une commande — la donnée dormait dans les logs de #177 depuis le début.
  Corollaire utile : le résultat du témoin en sort RENFORCÉ, puisqu'une variable
  candidate de plus est éliminée entre les jumeaux.

## Le mode de défaillance dominant du dépôt

Quatre compteurs faux et trois garde-fous incapables d'échouer, découverts en une
session. Aucun par négligence : tous étaient justes le jour de leur écriture, et
personne ne les recomptait.

> Un garde-fou qui ne peut pas rougir est indiscernable d'un garde-fou vert.

Deux réflexes qui en découlent : tout chiffre affiché doit avoir une commande qui
le recalcule, et tout gate doit distinguer « je n'ai pas pu juger » de « j'ai
jugé, c'est bon ».
