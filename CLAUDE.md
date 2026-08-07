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

Couverture **84,3 %** (gate 80), **2673 tests** (`pytest tests/ --collect-only`,
recompté le 2026-08-07 ; 2614 le 2026-08-05, 2313 le 2026-07-30 — personne ne le
rejouait alors, exactement le mode de défaillance décrit en bas de ce fichier).
⚠️ La couverture, elle, n'a PAS été recomptée à ces dates : elle porte toujours
la mesure du 2026-07-30. Un seul des deux chiffres de cette ligne est frais.
⚠️ `tests/test_vlc_server.py::TestResoudreMedia::test_traversal_refuse` échoue
sur `main` depuis au moins le 2026-08-07 (le message attendu, « racines
autorisées », a été remplacé par « Fichier non trouvé »). Non lié au reste, mais
un rouge permanent finit par se lire comme du bruit — donc par masquer un vrai.
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
tout sauf qu'aucune méthode d'écriture n'y est imitable, faisait **4/4**.
⚠️ **Ne pas raisonner sur ces deux taux — lire l'encadré ✅ plus bas** : ce qu'ils
mesurent indirectement est le taux d'OUVERTURE de `docs/`, seule variable qui
décide, et le témoin n'est pas à 100 % (8/10 mesuré).

Le premier nightly complet sur les 30 tâches (run 30532826914, 2026-07-30) le
confirme d'une source indépendante : **29/30**, seul `hidden_invariant` échoue
(« valeur non copiée : la liste est partagée »), `first_write_method` passe. Porte
verte, `Δ +0,0 %`.

> ### ✅ MÉCANISME ÉTABLI PAR LES TRACES — l'ouverture de `docs/` décide de tout
>
> Lot apparié TRACÉ du 2026-07-30 (`--repeat 5` + traces isolées, n=18 sur les
> deux jumeaux ; `bench/results/reference_2026-07-30_appels_outils_jumeaux.md`
> porte les appels d'outils instance par instance) :
>
> | | ✅ | ❌ | succès |
> |---|---|---|---|
> | **`docs/` lu** | **8** | 0 | **100 %** |
> | **`docs/` non lu** | 0 | **10** | **0 %** |
>
> **Séparation parfaite, Fisher bilatéral p = 2,3 × 10⁻⁵.** L'ouverture du
> document prédit le verdict sans une seule exception sur 18 instances.
>
> ⚠️ **La copie profonde ne rate JAMAIS quand le document est lu.** Elle n'est pas
> la difficulté ; elle n'est que le symptôme mesuré par la sonde. Chercher
> pourquoi l'agent « rate la copie profonde » est une impasse — il ne la rate pas,
> il ne sait pas qu'elle est exigée.
>
> Ce qui varie entre les jumeaux est donc le TAUX D'OUVERTURE de `docs/` :
>
> | | ouverture de `docs/` |
> |---|---|
> | `hidden_invariant` | **0/8** |
> | `first_write_method` | **8/10** (p = 1,05 × 10⁻³) |
>
> **Le mécanisme, lu dans les appels d'outils** — les totaux sont identiques
> (5 appels partout), les compositions sont opposées :
>
> | appel | `hidden_invariant` | `first_write_method` |
> |---|---|---|
> | 4ᵉ | **`write_file`** | **`read_file docs/DECISIONS.md`** |
> | 5ᵉ | `pytest` | `write_file` |
>
> `hidden_invariant` ÉCRIT avant d'avoir rien cherché, puis lance les tests
> visibles — **qui passent**, puisqu'ils ne testent pas la copie. Il reçoit donc
> une CONFIRMATION VERTE de sa réponse fausse et s'arrête. Le contexte local lui
> fournit une histoire complète et cohérente : une méthode `set` à imiter, des
> tests au vert. Le témoin, privé de modèle à imiter, n'a pas cette histoire sous
> la main et va chercher.
>
> ⚠️ **Les 2 échecs du témoin dans le lot du matin étaient EXACTEMENT les 2 passes
> où il n'a pas ouvert son document.** Il n'existe pas de second mode d'échec.
>
> **Instrument à préférer** : le TAUX D'OUVERTURE de `docs/`, pas le taux de
> succès. C'est la variable médiatrice, elle se mesure directement dans les
> traces, elle prédit parfaitement — et elle sépare « n'a pas cherché » de « a
> cherché sans comprendre », ce que le taux de succès confond.
>
> ⚠️ **Trois révisions de cette conclusion dans la même journée** (0/8 vu comme
> déterministe → « l'explication tombe » en #186 → mécanisme établi ici). Les deux
> premières venaient de TAUX à petit n sans traces ; celle-ci vient du mécanisme
> observé. Quand un taux et un mécanisme se contredisent, aller chercher le
> mécanisme — un taux à n=5 ne distingue pas deux causes, il les moyenne.

> ### ✅ LE CRITÈRE D'ARRÊT EST ATTAQUÉ — garde « décisions jamais ouvertes »
>
> Suite directe de l'encadré précédent. `agent/orchestrator.py` refuse désormais
> une conclusion posée après une écriture dans le dépôt quand l'agent n'a ouvert
> AUCUN document du projet, et lui injecte la lecture. Jumeau exact du garde
> LibraryBrain — même sophisme, autre preuve invoquée :
>
> | | LibraryBrain | ce garde |
> |---|---|---|
> | preuve invoquée | catalogue (titres seuls) | tests **préexistants** au vert |
> | ce qu'elle ne porte pas | le contenu des livres | ce que l'agent vient d'écrire |
> | action forcée | `search_books` | `read_file` sur les documents trouvés |
>
> Lot apparié `--repeat 5`, même protocole que la mesure du matin :
>
> | tâche | avant | après |
> |---|---|---|
> | **`hidden_invariant`** | **0/5** | **5/5** (Fisher p = 7,9 × 10⁻³) |
> | **`first_write_method`** | **2/5** | **5/5** |
> | les deux jumeaux | 2/10 | **10/10** (p = 7,1 × 10⁻⁴) |
> | `config_precedence` | 5/5 | 4/5 |
> | `error_contract`, `data_contract` | 5/5 | 5/5 |
> | **total `discovery`** | **17/25** | **24/25** |
>
> ⚠️ **Ce que ce garde ne fait PAS, et c'est le point central : le taux
> d'ouverture SPONTANÉE n'a pas bougé.**
>
> | | ouverture spontanée | garde déclenché | succès |
> |---|---|---|---|
> | `hidden_invariant` | **0/5** (référence : 0/8) | 5/5 | 5/5 |
> | `first_write_method` | 3/5 (référence : 8/10) | 2/5 | 5/5 |
>
> Le modèle ne s'est pas mis à chercher. Son critère d'arrêt est intact — c'est
> le HARNAIS qui refuse la conclusion et le renvoie lire. La feuille de route
> demandait « faire monter le taux d'ouverture » : ce n'est pas ce qui a été
> fait. Ce qui a été fait, c'est **rendre le non-ouverture sans conséquence**, en
> l'attrapant au moment où il devient une réponse livrée.
>
> Le fait qui rend la manœuvre légitime : **une lecture FORCÉE vaut une lecture
> spontanée** — 5/5 dans les deux cas. La variable médiatrice reste parfaitement
> prédictive, on la contrôle désormais au lieu de l'espérer.
>
> **Coût mesuré**, médianes sur `hidden_invariant` : appels d'outils 5 → **9**,
> itérations 4 → **10**, latence 45,0 → **72,1 s**. Il lit, corrige, relance les
> tests — c'est le travail attendu, pas de la surcharge.
>
> ⚠️ **Faux positif RÉEL attrapé avant le banc, par deux scénarios de rejeu**
> (04, 12) : la tâche était « crée un `README.md` », et le garde exigeait la
> lecture du fichier écrit à l'instant. Les documents produits par l'agent
> pendant le run sont exclus de l'inventaire. Sans la suite de rejeu, ce garde
> partait en production en brûlant un tour sur toute création de document.
>
> **Rayon de souffle borné par construction, et vérifié** : le garde ne se
> déclenche que si le dossier de travail contient un document que l'agent n'a pas
> écrit lui-même. Seul `bench/tasks/discovery.py` en pose — les 25 autres tâches
> du banc ne peuvent pas le déclencher. Mesuré dans le lot : 0 déclenchement sur
> `config_precedence`, `error_contract` et `data_contract`.
>
> ⚠️ L'unique échec restant (`config_precedence`, passe 2) est survenu **sans
> déclenchement du garde**, donc sur un chemin de code inchangé. Non imputable au
> garde — mais n=1, et cette tâche était à 5/5 le matin : à re-mesurer plutôt
> qu'à écarter.
>
> ⚠️ **Dans un dépôt réellement documenté — klody-code-ai lui-même — le garde
> coûte un appel d'outil supplémentaire par tâche de code** qui n'ouvre rien.
> Borné à un par run, mais réel, et ce banc ne le mesure pas : ses dossiers de
> travail sont minuscules.

> ### ❌ RÉSULTAT NÉGATIF — la piste était donnée à chaque fois, et jamais suivie
>
> Question posée en voulant faire monter le taux d'ouverture SPONTANÉE : l'agent
> ignore-t-il **où** chercher, ou refuse-t-il de chercher ?
>
> **Il sait où.** `_relevant_files_section` (recherche sémantique proactive,
> `tools.code_search`) injecte les fichiers pertinents dans le prompt système, dès
> le tour 1, sous un intitulé qui dit en toutes lettres « à confirmer **en lisant
> les fichiers avant d'agir** ». Relevé dans `logs/agent.log` sur la fenêtre du lot
> apparié (2026-07-30 16:47 → 17:13) :
>
> | | |
> |---|---|
> | injections de `docs/INCIDENTS.md` | **5** — une par passe |
> | score de pertinence | 0,59 (seuil `RETRIEVAL_MIN_SCORE` = 0,35) |
> | lectures spontanées | **0** |
>
> **Trois canaux indépendants donnent l'adresse, aucun ne produit une lecture :**
>
> | canal | contenu | résultat |
> |---|---|---|
> | énoncé de la tâche (`_AVERTISSEMENT`) | « explore avant d'écrire » | 100 % des passes, ignoré |
> | consigne de prompt système | instruction dédiée dans `base.md` | **0/3**, revertée |
> | recherche sémantique | **nomme `docs/INCIDENTS.md`** | **5/5 injections, 0/5 lectures** |
>
> ⚠️ **Le levier « mieux le lui dire » est donc épuisé, et démontré tel** — pas
> supposé. Ce qui manque n'est pas l'adresse du document : c'est une raison de
> regarder AVANT de se sentir fini. Un agent qui tient déjà une histoire complète
> et cohérente (une méthode à imiter, des tests au vert) n'ouvre pas un fichier
> qu'on lui a pourtant nommé.
>
> **Corollaire, et c'est ce qui rend ce négatif utile** : agir sur le critère
> d'arrêt n'était pas un pis-aller faute de mieux — c'était le seul levier
> restant. Le garde ne compense pas un défaut d'information, il répond au vrai
> défaut.
>
> ⚠️ Ne pas rejouer : une 4ᵉ manière de nommer le fichier (outil dédié, note dans
> le résultat de `write_file`, ré-injection à chaque tour). Ces trois-là couvrent
> déjà le prompt de tâche, le prompt système et le retrieval. Preuve figée dans
> `bench/results/reference_2026-07-30_piste_donnee_jamais_suivie.json`.

Mesures de référence dans `bench/results/reference_*.{json,md}` (convention : ce
préfixe est dé-ignoré, cf. `.gitignore`).

## État au 2026-08-01 — accueil de session, et le coût réel du prefill

PR #191. Le fil : la CLI *affirmait* des choses qu'elle pouvait dériver ou
sonder, et l'écran d'accueil en était la vitrine.

- La bannière annonçait « Powered by **Ollama** » en dur quel que soit
  `BACKEND`, et affichait `MODEL_NAME` — le modèle du mode *ollama* — même en
  `BACKEND=mlx`, où l'agent parle à `MLX_MODEL`. Elle se contredisait à l'écran :
  la toolbar affiche déjà `orchestrator.llm.model`, deux lignes plus bas. Tout
  est dérivé désormais (`_backend_label()`, `LLM_MODEL`).
- `/status` sondait Ollama **inconditionnellement** et affichait « ✗ hors ligne /
  `ollama serve` » en mode mlx, où Ollama n'est ni le backend LLM ni le
  fournisseur d'embeddings. Il sonde la cible réelle, et n'affiche Ollama que
  s'il sert vraiment. Sinon la ligne dit « **non sondé** » plutôt que d'inventer
  un verdict — vérifier `st` imposerait de charger bge-m3 en processus.
  - ⚠️ Sonde de **disponibilité**, jamais de résolution d'alias : aucune
    complétion. Une complétion peut charger 44 Go ou rendre 503 — c'est le
    préflight du nightly qui fabriquait sa propre panne. Un test le verrouille
    **sur les URL réellement appelées**, pas sur le texte affiché.

> ### ✅ MESURÉ — le prefill des schémas d'outils coûte ~4,1 s, et c'est la
> ### VARIANCE qui décide, pas le coût
>
> Relevé complet : `bench/results/reference_2026-08-01_plancher_accueil.md`
> (`scripts/mesure_plancher_accueil.py`, gateway `:8090`, `brain` →
> Qwen3.6-35B-A3B-8bit).
>
> | bras | 1ᵉʳ appel | à chaud |
> |---|---|---|
> | `plancher` (1 token, sans outils) | **6,52 s** | 0,13 s |
> | `accueil` (~60 tokens, sans outils) | 0,46 s | **0,37 s** |
> | `avec_outils` (+ 69 schémas) | 4,06 s | 0,37 s |
>
> Balayage à cache froid garanti (deux tailles d'outils = deux préfixes) :
>
> | outils | tokens¹ | médiane | Δ vs palier 0 |
> |---|---|---|---|
> | 0 | 0 | 0,47 s | — |
> | 17 | 2 883 | 1,23 s | 0,76 s |
> | 34 | 5 573 | 2,04 s | 1,57 s |
> | 69 | 12 291 | **4,58 s** | **4,11 s** |
>
> ¹ compte **heuristique** (`tokenizer_is_exact() = False` sur les deux
> machines). La monotonie se lit sur le nombre d'outils, qui est exact.
>
> **Ça réconcilie deux chiffres qui semblaient se contredire** : 4,06 s = le
> prefill NON CACHÉ des 69 schémas, 0,37 s = le même appel une fois le préfixe
> en cache. Il ne manquait pas une mesure, il manquait une variable.
>
> ⚠️ **La conclusion qui dépasse l'accueil** : le **premier tour de chaque
> session** paie ces ~4,1 s, accueil ou pas. C'est une taxe de démarrage de
> l'agent lui-même, et tout ce qui invalide le préfixe (outil ajouté, schéma
> modifié, serveur MCP qui apparaît) la fait re-payer. La bascule de modèle
> figurait dans cette liste par principe — **mesurée le jour même : elle n'en
> fait PAS partie** (encadré suivant).

> ### ✅ MESURÉ — la bascule `brain` ↔ `coder` ne re-paie PAS le prefill
>
> Run 3 du même relevé (`--bascule`, 69 outils dans CHAQUE appel, amorçage
> exclu ; JSON : `reference_2026-08-01_bascule_brain_coder.json`) :
>
> | phase | médiane (n=3) |
> |---|---|
> | alternance · `brain` | 0,23 s |
> | alternance · `coder` | 0,22 s |
> | témoin · `brain` (sans bascule) | 0,24 s |
> | **écart alternance − témoin** | **−0,01 s** |
>
> Là où « prefill re-payé » exigeait ~+3,7 s. **Chaque modèle garde son cache
> de préfixe** : la bascule du routeur est gratuite en régime chaud, seul le
> chargement initial de `coder` (30 Go, 8,3 s) se paie, une fois par session.
> Alias distincts vérifiés par la réponse (8bit / UD-4bit) — le garde « même
> modèle des deux côtés » n'a pas eu à rougir.
>
> ⚠️ **Un écart nul admettait DEUX lectures**, et le run seul ne les séparait
> pas : cache touché — ou champ `tools` JETÉ par le gateway, auquel cas rien
> n'avait été mesuré. 0,23 s pour 12,5 k tokens de schémas est le même chiffre
> dans les deux cas. `scripts/controle_prefill_outils.py` tranche dans un même
> run : `prompt_tokens=13 802` avec outils contre 38 sans — le juge est le
> compte rendu par le backend, pas la latence — +3,7 s à froid (cohérent avec
> le balayage, protocole indépendant), effondrement 3,90 → 0,18 s au rejeu.
> Les 0,23 s sont des cache-hits réels.
>
> ⚠️ **Ce que ça n'établit PAS** : mesuré sur préfixe court et FIXE. En session
> réelle, l'historique de conversation change le préfixe entre deux passages
> sur le même modèle, et le cache rate alors pour une raison étrangère à la
> bascule. Le run dit « la bascule en soi n'invalide rien », pas « le cache
> survit à tout ».

**`agent/greeting.py`** — accueil de session généré, en tâche de fond. Ce n'est
pas le coût qui interdisait le synchrone (0,37 s est invisible), c'est la
**variance** : 0,13 s ou 6,52 s pour le même appel, sans moyen de savoir lequel
à l'avance. Thread démon lancé **avant** `Orchestrator(...)` (découverte MCP,
réseau) et la sonde LibraryBrain — ce temps est déjà payé — puis attente bornée
(`GREETING_DEADLINE_S`, 1,5 s) et repli muet sur un accueil composé localement.
L'appel ne porte **aucun schéma d'outil** : ~150 tokens contre ~12,3 k.

- Le micro-prompt est **identique** à celui de `scripts/mesure_plancher_accueil.py` :
  le faire diverger rendrait le 0,37 s caduc sans que rien ne rougisse.
- ⚠️ Effet de bord assumé : lancer la CLI **réveille `brain`**, modèle partagé
  avec Library Brain et KlodyAI (`pinned`). `GREETING_ENABLED=false` coupe
  l'appel, le socle local reste.
- **Non fait, délibérément** : le chemin websocket. Il demanderait un type de
  message que klody-ui (hors dépôt) ne sait pas afficher, plus une garde par
  session — un client qui bat déclencherait un appel `brain` par battement.

### Ce qui reste à faire

1. ~~Enregistrer un runner self-hosted~~ — **fait**, `klody-mac` en ligne
   (labels `self-hosted, macOS, ARM64, klody`). Le nightly tourne de bout en
   bout depuis le 2026-07-30 (run 30534579266, **30/30**, porte verte).
2. ~~Promouvoir une baseline~~ — **fait** (#172 pour 20/20, promue à 30 tâches,
   puis **re-promue à 30/30 le 2026-07-30 au soir**, après le garde du point 4.
   Elle ne fige plus aucun échec attendu.) Trois runs complets à 30/30 la
   précèdent — local, nightly sur le runner, puis un run FRAIS pour la
   promotion elle-même : promouvoir le run qui a servi à plaider la
   non-régression aurait été circulaire.
   - ⚠️ **Figer un échec attendu dans une baseline ne rend pas la porte neutre,
     il la DESSERRE.** Tant qu'elle était à 96,7 %, il fallait **4** tâches
     cassées pour rougir ; à 100 % il en faut **3**. L'arithmétique en
     pourcentage donne du mou dès que la référence n'est plus parfaite.
   - ⚠️ **Le tableau de sensibilité de `bench/gate.py` était FAUX d'une unité**
     sur toute sa colonne `29/30` (2/3/4 annoncés, 3/4/5 réels), du matin au
     soir du 2026-07-30. La justification écrite du passage 0.10 → 0.09 en
     découlait : elle prétendait ramener la porte à 3 tâches, il en fallait 4 —
     **la porte est restée plus lâche qu'annoncé toute la journée**. Le tableau
     avait été calculé de tête, dans le commit même qui changeait le seuil.
     Corrigé par mesure, et `tests/test_gate_sensibilite.py` le verrouille
     désormais case par case. **Un commentaire qui chiffre une sensibilité EST
     un réglage** — et un réglage que rien ne vérifie finit par mentir.
3. ~~Mesurer le taux, puis le mécanisme~~ — **fait** le 2026-07-30 : l'ouverture
   de `docs/` décide de tout, séparation parfaite sur n=18 (encadré ✅).
   - ⚠️ La consigne de prompt a DÉJÀ échoué (0/3, `base.md`, tracé identique,
     revertée). Ne pas la rejouer telle quelle.
   - Se juge sur le TAUX D'OUVERTURE, pas sur le taux de succès : il est
     parfaitement prédictif, et il sépare « n'a pas cherché » de « a cherché
     sans comprendre ». `--repeat 5` minimum, traces capturées
     (`PYTHONUNBUFFERED=1`, sinon les en-têtes du parent se désynchronisent de
     la sortie des sous-processus et l'attribution est fausse).
4. ~~Attaquer le critère d'arrêt~~ — **fait** le 2026-07-30 : garde
   « décisions jamais ouvertes » dans `agent/orchestrator.py`, `discovery` à
   **24/25** (encadré ✅). Ce qui reste ouvert :
   - **Le taux d'ouverture SPONTANÉE est toujours 0/5** sur `hidden_invariant`.
     Le garde compense, il ne corrige pas. ⚠️ **Et ce n'est PAS un problème
     d'information** — encadré ❌ ci-dessous. Le levier « mieux le lui dire » est
     épuisé, sur trois canaux indépendants.
   - **Mesurer le coût sur un vrai dépôt** : un appel d'outil de plus par tâche
     de code qui n'ouvre aucun document. Le banc ne peut pas le voir, ses
     dossiers de travail sont vides de documentation hors `discovery`.
   - ~~Re-promouvoir la baseline~~ — **fait** le soir même (cf. point 2). Elle
     est à **30/30**, `hidden_invariant` comprise.
5. ~~Trancher l'A/B cerveau~~ — **clos par décision** le 2026-07-29 : on garde
   Qwen3.6-35B-A3B. Trois raisons, dans l'ordre où elles ont compté : à 20/20
   partout et `tool_calls_cassés` à 0, le banc n'avait **aucune marge** pour
   départager deux modèles (c'est ce qui a motivé les paliers `expert` et
   `discovery`) ; gpt-oss-120b n'est pas sur la machine ; et `brain` est le
   modèle **partagé** avec Library Brain et KlodyAI, donc basculer l'alias
   changeait ces applications aussi. Depuis, `KLODY_CORE_BRAIN_MODEL` existe
   côté klody-core — un futur A/B passe par une entrée dédiée du registre,
   pas par une surcharge de `brain`.
6. ~~Mesurer la bascule `brain` → `coder`~~ — **fait** le 2026-08-01 :
   **elle ne re-paie pas le prefill**, écart −0,01 s là où « re-payé » exigeait
   ~+3,7 s (encadré ✅ « la bascule ne re-paie PAS »). La conclusion n'a été
   posée qu'après le contrôle de validité (`controle_prefill_outils.py`) — un
   écart nul aurait aussi bien pu dire « le gateway jette `tools` », et le
   `prompt_tokens` du backend a tranché (13 802 vs 38).
   - Reste ouvert : le coût du garde « décisions jamais ouvertes » sur un
     vrai dépôt documenté (cf. point 4), que le banc ne peut pas voir.

## État au 2026-08-02 — le connecteur sample cherche par le SON

`klody_mcp/reaper_samples.py` ne cherchait que dans les NOMS de fichiers. Il
interroge désormais l'index CLAP de **SampleBrain** (dépôt séparé
`~/Projets/SampleBrain`, index par défaut `~/.samplebrain`) quand celui-ci
répond, et se rabat sur les tokens sinon. Le champ `via` de chaque résultat
nomme le moteur qui a répondu ; `semantic_status()` dit pourquoi l'autre s'est
tu. Mesuré sur la bibliothèque réelle (654 fichiers, 569 contenus) : premier
appel ~6,7 s (chargement des poids), appels suivants **~0,02 s**.

- ⚠️ **Indexer et exposer sont deux décisions distinctes.** `SAMPLEBRAIN_ROOTS`
  (côté agent d'indexation) dit ce qui entre dans l'index ; `KLODY_SAMPLES_DIR`
  dit ce que le connecteur a le droit d'en ressortir, et les résultats
  sémantiques sont filtrés sur ces racines-là. Le défaut est posé dans
  `scripts/start-reaper-mcp.sh` (`$HOME`-relatif, une valeur déjà exportée
  gagne) et non dans le plist : le script est le point de passage commun à tous
  les modes de démarrage. Verrouillé par `tests/test_start_reaper_mcp_env.py`.
  Les racines couvrent `~/Desktop/SAMPLES` **et** `~/local-suno/samples` ; ce
  second dossier contient les 537 segments de la VOIX de l'utilisateur
  enregistrés pour l'entraînement RVC, pas des samples musicaux — les exposer
  est un choix assumé, l'agent peut donc en placer un dans un projet si une
  requête l'y amène.
- **Dépendance strictement optionnelle et NON déclarée.** Elle tire `lancedb`
  (+ `torch`/`transformers`, déjà là pour les embeddings). L'imposer au dépôt
  ferait payer ce poids à tout le monde pour un connecteur REAPER. Activation :
  `pip install -e ~/Projets/SampleBrain` puis `samplebrain-index index`.
  `KLODY_SAMPLEBRAIN=0` coupe le moteur sans rien désinstaller.
- ⚠️ **L'import est isolé, jamais groupé** — le piège « `numpy` dans le `try` de
  `librosa` » plus bas, appliqué par avance.
- ⚠️ **`score` n'a pas la même échelle selon `via`** : entier de tokens en
  `filesystem`, cosinus [0,1] en `samplebrain`. Il classe à l'intérieur d'un
  résultat, il ne se compare pas entre deux `via`. Un même appel ne mélange
  jamais les deux moteurs, précisément pour que personne ne les additionne.
- La conversion distance → similarité (`1 - d/2`) a été **vérifiée sur l'index
  réel** (concordance à 1e-6 avec le cosinus recalculé), pas déduite de la doc.

## État au 2026-08-02 — Klody a UN timbre, et il est figé

Jusqu'ici la voix parlée sortait de Qwen3-TTS 0.6B **Base**, conditionné par
aucun locuteur : le timbre était **tiré au sort à chaque phrase**. Klody n'avait
pas une voix, il en avait une par réplique — un défaut qui ne se lit nulle part,
il s'entend.

`config.VOICE_PRESET` nomme désormais un **preset de voix clonée**, transmis à
chaque synthèse (`vocalbrain generate --preset klody`, ajouté côté VocalBrain).
Le preset `klody` convertit la prise au modèle RVC **`klody_e250`** — l'epoch
retenu au test d'écoute du 2026-07-28. Conséquence utile : la voix **parlée** de
Klody est maintenant la même que sa voix **chantée** dans local-suno.

Le nom part de l'appelant, pas du profil du personnage : ce profil est un JSON
que Klody ne lit jamais, et un profil vidé ferait retomber la synthèse sur le
timbre aléatoire **sans que rien ne le signale**. Preset absent ⇒ la CLI refuse
la synthèse. Coût mesuré : **~4,7 s de RVC pour 2,6 s de parole**, dans le même
subprocess (`speak` n'importe toujours ni torch ni mlx_audio) — un `speak`
complet passe de ~3 s à **~8 s**.

> ### ❌ RÉSULTAT NÉGATIF — le clonage zero-shot TTS PARLE, et dit n'importe quoi
>
> Premier chemin essayé : clonage zero-shot à partir d'un extrait de référence
> de 12,2 s. Le câblage était à faire des deux côtés — `mlx_backend` passait
> `ref_audio` en **chaîne de caractères** là où les modèles mlx-audio attendent
> un `mx.array` (`_prepare_reference_prompt` fait `audio.ndim`), et comme leurs
> signatures finissent par `**kwargs`, le filtre laissait tout passer : **ce
> chemin n'avait jamais pu fonctionner**. Corrigé (`_load_reference_audio`).
>
> Une fois câblé, Fish S2 Pro 8bit — **seul modèle de clonage en cache** — rend
> du charabia phonétique. Transcription Whisper large-v3-turbo :
>
> | attendu | entendu |
> |---|---|
> | « La voix clonée de Klody est maintenant active. » | « La voix crueuse est le clou de la peau de l'orpective. » |
> | « Hello, this is a test of the cloned voice. » | « Hello. Hello. » |
> | « Bonjour, ceci est un test de la voix clonée. » | « Bonjour. » |
>
> Anglais comme français, **avec ou sans référence** : ce n'est pas le clonage
> qui casse, c'est le modèle. La référence, elle, est parfaite — transcrite mot
> pour mot, avant comme après RVC. Donc : rien à corriger côté preset, tout à
> jeter côté modèle.
>
> ⚠️ **Ne pas rejouer sans mesurer autrement** : le seul autre modèle de clonage
> installable est à télécharger (Qwen3-TTS CustomVoice, ~2-3 Go), et rien ne dit
> qu'il fait mieux dans cette version de mlx-audio (0.4.3). Le RVC, lui, est
> entraîné, écouté et retenu.

## État au 2026-08-07 — la chanson était tronquée par ce que Klody ENVOYAIT

Klody avait diagnostiqué « les générateurs IA sautent des sections ou répètent le
refrain », et proposait de générer par segments puis d'assembler dans REAPER. Le
diagnostic était juste, le remède non : **le daemon fait déjà le long-format**
(`generate_song_long`, segments chevauchants recollés en cross-fade). Assembler à
la main aurait re-fabriqué le bug à l'identique.

La cause n'est pas dans le moteur, elle est dans la **requête**. Trois mécanismes
déterministes, tous vérifiés sur le code réel de local-suno et sur les
**78 chansons chantées de `library.db`** :

| # | mécanisme | mesure |
|---|---|---|
| 1 | **Trop de mots pour la durée.** Le daemon vise ~2 mots/s (`main.py::_warn_if_lyrics_too_short`, qui l'écrit) ; `generer_chanson` avait `duree_sec=30` **en dur** quelles que soient les paroles | demandes réelles à **15,3** · 6,0 · 5,7 · 5,6 · 5,3 mots/s — jusqu'à **7× la cible**. Le moteur ne peut que couper |
| 2 | **Moins de sections que de segments.** Au-delà de 120 s, `generate_song_long` fait `chunks.append(chunks[-1])` : les segments de fin **re-chantent le texte précédent** | **17/78** chansons n'avaient qu'**une** section (texte sans ligne vide ni en-tête). À 240 s = 3 segments, les 3 chantent la même chose — reproduit |
| 3 | **Rôles de section perdus.** Un bloc séparé par une simple ligne vide devient `section_2`, `section_3`… et `section_marker` rend `[verse]` pour ces noms inconnus | **10/78** avaient au moins une clé `section_N` : leur refrain n'était plus balisé comme un refrain |

> ### ⚠️ Trouvé en route : deux `[Refrain]` et le premier DISPARAÎT
>
> `_build_lyrics_from_custom` range les sections dans un **dict indexé par le nom
> d'en-tête**. Un texte qui reprend `[Refrain]` en fin de morceau écrase donc le
> premier : mesuré sur le vrai parseur, un texte à 5 blocs ressort à **4
> sections**, refrain final avalé. Ce n'est pas un risque théorique — c'est la
> forme la plus naturelle d'écrire une chanson.
>
> D'où la **numérotation** des marqueurs émis (`[chorus 1]`, `[chorus 2]`) :
> `_base_section_name` retire le suffixe côté daemon avant le mapping, la balise
> qui atteint le moteur reste canonique, et aucune section ne s'écrase.
> ⚠️ Canoniser SANS numéroter aurait donc *aggravé* le bug.

**Le correctif** — `klody_mcp/song_structure.py`, branché sur les deux chemins
(`vocalbrain_server.generer_chanson`, `klody_music_server.composer_demo`) :

- **Les paroles partent balisées.** `[Couplet 1]`, `Refrain :`, `Pont` → `[verse 1]`,
  `[chorus 1]`, `[bridge 1]`. Un bloc sans en-tête reste `[verse]` (même repli que
  le daemon) mais est **compté et signalé** : deviner qu'un bloc est un refrain
  serait transformer les paroles sans le dire.
- **La durée se déduit des paroles** quand elle n'est pas donnée (mots ÷ 2). Le
  défaut fixe à 30 s était la cause n°1 ; il n'y a plus de défaut fixe.
- **Refus AVANT le POST** quand le rendu serait tronqué ou répété — pas après.
  Un refus qui arrive une fois la génération en file ne sert à rien : elle dure
  des minutes. `forcer=True` reste l'échappatoire, et la note dit « FORCÉ malgré ».

> ### ✅ Ce que le correctif ne fait PAS, et c'est délibéré
>
> Un texte d'un seul bloc reste **incorrigible** : à 240 s il donnera 3 segments
> identiques quoi qu'on fasse. Le module ne fabrique pas les sections manquantes,
> il **refuse** et nomme le remède (« découpe en au moins 3 sections »).
> Inventer une structure que l'utilisateur n'a pas écrite serait exactement le
> travers déjà refusé pour la traduction des requêtes CLAP.
>
> Vérifié en rejouant le circuit réel : canonicalisation seule sur un texte sans
> structure ⇒ toujours 3 segments identiques. Le garde, lui, refuse.

⚠️ **Le débit de 2 mots/s n'est pas une estimation maison** : c'est la cible que
le daemon écrit lui-même, et il avertit déjà sous 1 mot/s — mais dans un `print`
de sous-processus worker que **ni l'utilisateur ni Klody ne voient jamais**. Le
contrôle remonte ce que le daemon savait déjà, au seul endroit où ça peut servir.

⚠️ **`_idee_to_body` bornait la durée à 120 s** en citant « bornes daemon (ge=10
le=120) » alors que le contrat était passé à **600**. Toute démo au-delà de 2 min
était donc silencieusement coupée de moitié. Corrigé — et les constantes
recopiées de local-suno sont désormais **relues dans le vrai dépôt** par
`tests/test_song_structure.py::TestPasDeDerive`.

⚠️ **Ce garde anti-dérive s'est d'abord sauté en silence**, écrit en import
direct : les deux dépôts ont un module `config` (et un `main`), et celui de
klody-code-ai est déjà dans `sys.modules` quand la suite tourne — `pytest -rs`
disait « local-suno présent mais non importable ». Il tourne maintenant dans un
**sous-processus** avec l'interpréteur et le cwd de local-suno, et sa capacité à
rougir a été vérifiée en cassant une constante exprès. Un test sauté est
indiscernable d'un test vert : c'est le mode de défaillance du dépôt, reproduit
ici en écrivant le garde-fou censé le prévenir.

⚠️ **Non fait, et pas par oubli** : aucune consigne ajoutée au prompt système. Le
levier « mieux le lui dire » est déjà mesuré épuisé sur trois canaux (encadré ❌
plus haut). La docstring de l'outil porte la règle — c'est ce que le modèle lit
au moment de choisir ses arguments — et le **refus de l'outil** est le garde-fou.

## Pièges qui coûtent du temps

- ⚠️ **Un lanceur de service sans son agent launchd = un service qui ne démarre
  JAMAIS, sans la moindre erreur.** `launchagents/README.md` nomme ce mode de
  panne comme celui que le dossier ferme — et `reaper` y est quand même passé
  au travers pendant des mois : `.env` le déclarait consommé sur `:8089`,
  `scripts/start-reaper-mcp.sh` existait, aucun agent ne le lançait, et
  `config.py` ignore silencieusement un serveur MCP injoignable au boot. Klody
  perdait donc **50 outils REAPER** à chaque démarrage sans que rien ne le
  signale. Corrigé le 2026-08-02 (`launchagents/com.klody.reaper-mcp.plist`),
  et la règle est désormais **testée** :
  `tests/test_launchagents_couvrent_les_mcp.py` exige un agent versionné pour
  chaque `scripts/start-*-mcp.sh`. `gadget` (`:8093`) était la même panne, à un
  jour d'écart : corrigé le 2026-08-03
  (`launchagents/com.klody.gadget-mcp.plist`), **`SANS_AGENT` est désormais
  vide** et un test verrouille le fait que cette liste ne se remplit pas en
  douce. ⚠️ Le test compare des noms de fichiers, pas des services vivants : il
  aurait dit vert sur un plist présent et jamais installé. Le contrôle qui voit
  la machine est `scripts/install-launchagents.sh --check`.
- ⚠️ **CLAP est ANGLOPHONE — et ce dépôt est en français.** Une requête
  française répond, mais moins bien, et pas seulement au score. Mesuré :

  | requête | score | meilleur résultat |
  |---|---|---|
  | « grosse caisse qui claque » | 0,458 | un FX |
  | « punchy kick drum » | **0,509** | `kickdrum2.wav` |
  | « nappe sombre cinématique » | 0,393 | `05_Chant_80bpm.wav` |
  | « dark cinematic pad » | **0,447** | `Deep Synth.wav` |

  L'anglais gagne sur les trois paires testées. Le connecteur ne traduit rien :
  traduire silencieusement la requête d'un utilisateur serait une
  transformation invisible de son intention. C'est à l'appelant de le savoir,
  d'où le rappel dans la docstring de l'outil MCP.

- ⚠️ **Un TTS cassé PARLE — l'oreille du script ne suffit pas, il faut
  transcrire.** Le 2026-08-02, j'ai jugé une prise Fish « de la vraie parole »
  sur son enveloppe (RMS variable, 44 % de silences, durée plausible) et je l'ai
  jouée comme preuve que le clonage marchait. Whisper a rendu « La voix crueuse
  est le clou de la peau de l'orpective. » Aucune statistique de signal ne
  sépare la parole du charabia **phonétiquement voisin** : seul un ASR est un
  verdict. `mlx_whisper` est en cache, la vérification coûte quelques secondes.
- **Une conversion qui laisse l'original à côté du converti se fait jouer à
  l'envers.** RVC écrivait `seg_take01_abc_rvc_klody_e250.wav` à côté de
  `seg_take01_abc.wav` ; `speak` retrouve sa prise par
  `glob(f"*/{seg}_take*.wav")` puis `sorted()[0]` — et `.` (46) trie avant `_`
  (95), donc il aurait joué la voix NON convertie en croyant tenir la voix
  clonée. La conversion écrase désormais sur place : une seule prise sur le
  disque, donc un seul timbre possible.
- **zsh n'active pas les commentaires en interactif.** Coller un bloc avec des
  lignes `#` les exécute. `setopt interactive_comments` dans `~/.zshrc`.
  ⚠️ Vécu de nouveau le 2026-08-01, dans un bloc que j'avais moi-même écrit en
  connaissant le piège : `vocalbrain --help  # y a-t-il une sous-commande ?` →
  `zsh: no matches found: ?`, la commande n'a jamais tourné. **Ne jamais mettre
  de commentaire dans un bloc destiné au copier-coller.**
- **La voix Klody sort avec PLUSIEURS TIMBRES si le clonage est absent.**
  Le modèle est un Qwen3-TTS 0.6B **Base**, sans conditionnement de locuteur.
  `tools/voice._segment_sentences` découpe une phrase par ligne — découpage
  OBLIGATOIRE, sans lui le Base n'émet jamais l'EOS (103 caractères → WAV de
  163,8 s) — et Qwen3-TTS scinde en interne sur ces `\n`. Chaque morceau prend
  alors son propre timbre : un accueil de cinq phrases sort avec cinq voix.
  - Le juge est la ligne **`clonage :`** du `vocalbrain generate --dry-run`,
    pas la configuration locale. `python scripts/diagnostic_voix.py` la lit.
    ⚠️ Elle rend la SOURCE du timbre (`klody_e250 (preset « klody »)`) ou le mot
    `non` — **jamais « oui »**. Un analyseur qui chercherait « oui » serait vert
    contre un faux complaisant et rouge à jamais sur la machine.
  - **Réglé le 2026-08-02** : `VOICE_PRESET=klody` fige le timbre (section « Klody
    a UN timbre » plus haut). Le mécanisme n'est plus `--voice` mais `--preset`.
  - ⚠️ **`--voice <valeur-inconnue>` était accepté SANS ERREUR puis ignoré** — il
    ne figeait donc rien, et une `VOICE_PRESET` mal orthographiée ne produisait ni
    message ni changement. `--preset` refuse net. Le mode muet est mort, mais le
    refus sort **sans ligne `clonage :`** : sans lecture explicite, cette absence
    se lirait « CLI d'une autre version », donc comme un non-problème — la panne
    muette rentrerait par la fenêtre. Verrouillé par
    `tests/test_diagnostic_voix.py::test_preset_inconnu_est_DENONCE`.
- **`.gitignore` disait « préfixer `reference_` », mais ne dé-ignorait que
  `.json`.** Un `reference_*.md` était donc silencieusement écarté par
  `git add` — `reference_2026-07-30_appels_outils_jumeaux.md` n'est dans le
  dépôt que parce qu'il a été **forcé**. Corrigé le 2026-08-01 : la règle vaut
  pour les deux formats. Une convention écrite qu'un outil n'applique pas est
  une convention qui ne tient que par accident.
- **Un test qui scanne le SOURCE confond la prose et la sortie.** Un filet
  anti-récidive cherchant « Ollama » dans `main.py` a servi (il a trouvé une
  occurrence oubliée dans `HELP_TEXT`), puis s'est mis à rougir sur les
  *commentaires qui expliquaient le correctif* — donc à pousser vers la
  suppression des explications pour le faire taire. Remplacé par la propriété
  comportementale : « l'écran ne nomme pas un service inutilisé », vérifiée sur
  le rendu et sur les URL appelées.
- **Un chronomètre de latence doit dire QUEL appel est froid, pas quelle
  passe.** L'instrument étiquetait « (à froid) » toute la 1ʳᵉ passe ; seul le
  premier APPEL l'est. Ça faisait lire un coût de bras (4,06 s pour
  `avec_outils`) là où il y avait un réveil de gateway attribué au bras qui
  passait en tête.
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
