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

Six paliers, 35 tâches : `easy`, `medium`, `hard` (les 20 de la baseline
historique), `expert` (le réflexe est faux), `discovery` (la contrainte n'est pas
dans l'énoncé), `real_repo` (dans un clone du dépôt, #239). Le gate n'intersecte
que les `task_id` communs et annonce « N tâche(s) hors baseline, non jugée(s) » —
des tâches absentes de la baseline ne sont pas jugées tant qu'une baseline ne les
inclut pas. Baseline à 35 tâches × 3 passes depuis le 2026-09-28 (section dédiée
plus bas), seuil 0.075.

⚠️ **Jusqu'au 2026-09-27, le gate ne jugeait que la DERNIÈRE passe d'un run
`--repeat N`** (`{task_id: résultat}` écrase), et comptait les N−1 autres comme
« hors baseline ». Vécu : `--category discovery --repeat 3` = 14/15,
`config_precedence` ❌ en passe 1 ⇒ « courant=100.0 % … 10 hors baseline ✓ ». Le
même écrasement lisait `reference_2026-07-30_garde_arret_apres.json` (24/25) à
100 %. Il juge désormais toutes les passes, des deux côtés (moyenne des taux par
tâche, en arithmétique exacte), et nomme en `::notice::` toute tâche en baisse
sous le seuil. Sensibilité à N passes : tableau dans `bench/gate.py`, verrouillé
par `tests/test_gate_sensibilite.py::TABLEAU_PASSES`. À une passe (le nightly),
verdict inchangé — vérifié exhaustivement sur 5 à 30 tâches.

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

Couverture **85,1 %** (run CI 33828140106 du 2026-09-04, gate 80), **3467 tests**
(`pytest tests/ --collect-only`, recompté le 2026-09-28 sur `main` à jour, `511cab1` ;
2904 le 2026-09-04, 2829 le 2026-09-03, 2779 le 2026-08-07, 2614 le 2026-08-05, 2313 le 2026-07-30 — personne
ne le rejouait alors, exactement le mode de défaillance décrit en bas de ce fichier).
⚠️ Le 2026-09-03, la PR #250 (docs publics, CONSULTING/CASE-STUDY) a RECOPIÉ le
2779 de ce paragraphe alors que `main` en collectait déjà 2829 ; le 2026-09-04
(22 PR du plan d'optimisation mergées) `main` en collecte 2904 : un chiffre lu
ici est périmé dès qu'on le recopie. Le recompter, jamais le recopier.
⚠️ **Un rouge lu sur une branche N'EST PAS un rouge de `main`.** Le 2026-08-07,
`tests/test_vlc_server.py::TestResoudreMedia::test_traversal_refuse` échouait
dans un worktree, et je l'ai signalé « échoue sur `main` » après l'avoir rejoué…
sur ce même worktree, dont la branche était en retard de 8 commits. `main` avait
déjà le correctif (#209, `7801cb2`) : 12/12 au vert. Le vrai contrôle est
`git log HEAD..origin/main` AVANT d'attribuer un échec à la base — sinon on
ouvre un chantier pour un bug déjà réparé, ce qui a bien failli arriver.
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
>
> ⚠️ **Trou fermé le 2026-09-27 : le garde se taisait à la DERNIÈRE itération.**
> Il exigeait `iteration < max_iter - 1` « pour que la relance ne meure pas sur
> le cap » — et laissait donc passer toute conclusion posée au dernier tour.
> Vécu sur `hidden_invariant` (`easy · feature · max_iter=6`) : 5 appels
> d'exploration et d'écriture, conclusion à 6/6, `docs/` jamais ouvert,
> invariant violé. La marge ne protégeait rien : une relance consomme **5
> itérations** (mesuré, 3/3 déclenchements), que seule l'auto-continue
> fournissait — en injectant « … puis conclus dès que c'est fait » juste après
> le nudge. Le garde tire désormais à toute itération et se garantit son budget
> (`_DOC_GUARD_MARGE = 5`). Scénario de rejeu 22.
> **Même trou, même jour, dans le garde LibraryBrain — et sur l'incident même qui
> l'a fait écrire** : 5 `library_catalog` séquentiels en `easy · explain`
> (max_iter=6) placent « pas de sources » à 6/6, le garde se taisait. Pire qu'ici :
> `explain` n'a pas d'auto-continue, une relance sans budget garanti finit en
> synthèse forcée SANS outils. Fermé pareil (`_LIBRARY_GUARD_MARGE = 4` : sur 123
> tours réels, ≤ 4 itérations du premier `search_books` à la conclusion dans
> 98 % des cas ; 0 déclenchement réel à mesurer). Scénario de rejeu 23.
> Banc du jour, même config (QCM/brain), `discovery --repeat 3` : **13/15** avec
> le correctif contre 12/15 sans — aucune régression, mais le cas « dernière
> itération » ne s'est PAS reproduit dans ce run (garde tiré 2× à l'index 4) :
> seul le scénario 22 prouve la fermeture du trou, le banc n'en voit que
> l'innocuité. `reference_2026-09-27_garde_derniere_iteration.json`.

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

## État au 2026-08-05 — un service long peut servir des dépendances mortes

> ### ⚠️ INCIDENT — `pip install` sous un process vivant, symptôme 23 h plus tard
>
> La PR #206 (`f962c1e`, mergée le 2026-08-04 à 05:40) bumpe `openai`
> 2.41.1 → 2.53.0. `com.klody.api` tourne depuis le 2026-08-03 01:36 et n'est
> jamais redémarré : pip réécrit `site-packages/openai/` **sous** le process.
>
> `openai._utils` reste figé en 2.41.1 dans `sys.modules` — donc sans
> `path_template` — tandis que `openai/resources/*`, chargé **paresseusement**
> par `openai/_utils/_resources_proxy.py`, est lu neuf sur le disque. Au premier
> appel LLM suivant, l'app rend sur CHAQUE requête :
>
>     cannot import name 'path_template' from 'openai._utils'
>
> **~23 h après le merge**, sans corrélation visible avec la mise à jour, et
> avec un venv parfaitement SAIN : l'import dans un interpréteur frais passe.
> C'est ce dernier point qui a coûté le plus cher — il envoie le diagnostic
> chercher du côté des versions et du venv, où il n'y a rien.
>
> Remède : `launchctl kickstart -k gui/$(id -u)/com.klody.api`. Rien — ni sonde,
> ni log, ni statut — ne le disait.
>
> **Ce n'est pas propre à l'API** : tous les `com.klody.*` (gateway, 8 serveurs
> MCP) tournent sur le même venv et ont le même mode de panne. Les serveurs MCP
> n'ont même pas de `/health`.
>
> **Le garde, `agent/peremption.py`** — l'empreinte de `site-packages`
> (mtime max des `*.dist-info` **+** leur nombre) prise au démarrage, comparée
> au disque à la demande. Trois surfaces :
>
> | surface | ce qu'elle voit | ce qu'elle fait |
> |---|---|---|
> | `/health`, `/api/status` | ce process-ci | `degraded` + **503**, remède nommé |
> | `scripts/diagnostic_peremption.py` | **tous** les `com.klody.*` | nomme les services, `--corriger` les relance |
> | `install-launchagents.sh --check` | le plus ancien démon | bloc `DÉPENDANCES`, informatif |
>
> ⚠️ **Le 503 ne peut pas faire boucler le watchdog** : `api-watchdog.sh` ignore
> délibérément le code HTTP et ne relance que sur ABSENCE de réponse. Vérifié
> par un test — si ce contrat changeait, la péremption relancerait l'API toutes
> les deux minutes.
>
> ⚠️ **On date les `*.dist-info`, JAMAIS le dossier `site-packages`.** La
> première compilation d'un module de premier niveau y crée `__pycache__/` et
> rajeunit le dossier sans qu'aucune dépendance n'ait bougé : le garde
> rougirait sur un venv stable, quelques secondes après le démarrage. Le
> NOMBRE de paquets est le second axe, seul témoin d'une désinstallation sèche
> — qui n'écrit rien et ne rajeunit aucune mtime.
>
> ⚠️ **Trois verdicts, pas deux** : `a_jour`, `perimees`, `non_juge`. Un
> `site-packages` introuvable ne doit pas se lire « tout va bien ».
>
> **Vérifié sur la machine, pas seulement sur des fixtures** : le diagnostic
> reproduit l'incident à l'identique (`com.klody.api` démarrée le 2026-08-03,
> pip passé le 2026-08-04 05:27:36), et rejoué avec une empreinte datée de
> l'instant, il rougit sur les 9 démons résidents et reste vert sur
> `api-watchdog` (périodique, ré-exécuté à chaque tick). Coût du scan : **0,46
> ms** pour 278 `*.dist-info` — d'où l'appel synchrone dans `/health`, sans
> cache.
>
> Procédure de mise à jour des dépendances : `docs/OPS.md` §5.

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
- **La durée se déduit des paroles** quand elle n'est pas donnée — pas par
  `mots ÷ 2`, mais par la plus courte durée où **aucun segment** ne dépasse la
  cible (`duree_sans_saturation`). Le défaut fixe à 30 s était la cause n°1 ; il
  n'y a plus de défaut fixe.
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

> ### ✅ MESURÉ IN VIVO — le débit GLOBAL masquait le seul chiffre qui décide
>
> Deux générations réelles des mêmes paroles (358 mots, 9 sections), transcrites
> à Whisper large-v3-turbo, appariement flou ligne à ligne (seuil 0,60) :
>
> | | 180 s | 228 s |
> |---|---|---|
> | débit **global** | 1,99 mots/s ✅ | 1,57 |
> | débit du **pire segment** | **2,52** ❌ | 2,00 |
> | **couverture du texte** | **65 %** (31/48 vers) | **94 %** (45/48) |
>
> **La cause, lue dans la répartition** : `split_arrangement_text` équilibre le
> **NOMBRE de sections**, pas le nombre de mots, et `plan_segment_durations` rend
> des durées **égales**. 9 sections en 2 segments ⇒ 5 + 4, soit **232 mots contre
> 126** dans deux segments de 92 s. Le premier saturait à 2,52 mots/s.
>
> **Signature causale** : les gains sont exactement dans les sections du segment
> saturé — 13/30 → **22/30** — pendant que celles du segment sain ne bougent pas
> (18/18 → 17/18, le −1 étant du bruit d'appariement). Ce n'est pas « c'est mieux
> plus long », c'est « c'est mieux là où ça saturait ».
>
> ⚠️ **Le contrôle initial ne voyait rien** : il ne regardait que le débit global,
> conforme dans les deux cas. `debit_par_segment` + `duree_sans_saturation`
> corrigent ça, et `TestPasDeDerive::test_la_repartition_des_mots_est_celle_du_daemon`
> confronte la répartition prédite au vrai `split_arrangement_text`.
>
> ⚠️ **Ce que ça n'établit PAS** : Whisper sur un mix complet (voix + instru)
> dérive phonétiquement (« ton nom » → « ton mot »), donc les pourcentages
> absolus portent du bruit. Ce qui tient, c'est la **comparaison** — mêmes
> paroles, même modèle, même seuil, et un écart concentré sur les sections
> prédites. Un pourcentage isolé de cette paire ne veut rien dire.

⚠️ **Le débit de 2 mots/s n'est pas une estimation maison** : c'est la cible que
le daemon écrit lui-même, et il avertit déjà sous 1 mot/s — mais dans un `print`
de sous-processus worker que **ni l'utilisateur ni Klody ne voient jamais**. Le
contrôle remonte ce que le daemon savait déjà, au seul endroit où ça peut servir.
- ⚠️ **Ces deux nombres n'étaient relus par AUCUN test** jusqu'au 2026-09-27 —
  angle mort resté ouvert quand #275 a réaligné le plafond. La vraie source de la
  cible est `pipeline/lyrics_generator.py::_WORDS_PER_SEC` (le message de `main.py`
  ne fait que la répéter). `TestPasDeDerive` la relit désormais, et confronte le
  seuil par COMPORTEMENT : il rejoue le vrai `_warn_if_lyrics_too_short` sur une
  grille LITTÉRALE de débits. Mutations rejouées des deux côtés (Klody et miroir du
  daemon) : 6/6 rouges.
- ⚠️ **La mesure « 65 % → 94 % » est celle du mode DÉCOUPÉ** (v1.5, deux segments
  de ~92 s) : en une passe, le pire segment EST le débit global, le même texte y
  partirait en un seul appel à 1,99 mot/s. **En une passe, la cible n'est
  corroborée que jusqu'à 1,8 mot/s** — banc local-suno du 2026-09-06, compté par
  `mots_chantes` : 1,66 ⇒ WER 5,7 %, 1,81 ⇒ 14,0 % (mêmes textes découpés : 68,7
  et 149,6 %). Le rapport du banc annonce 1,84 et 2,02 : son tokeniseur scinde les
  apostrophes. Au-delà de 1,8, l'alerte « débit serré » est une prudence, pas un
  constat.

⚠️ **Un TTS/chant cassé CHANTE quand même** — piège déjà écrit plus bas pour la
voix parlée, revécu ici : à 180 s le morceau sonnait complet, structure entière,
aucune répétition audible. Il manquait **35 % du texte**. Seul l'ASR l'a vu.

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

> ### ✅ 2026-09-27 — le daemon chante en UNE passe, et le garde anti-dérive l'a vu
>
> `TestPasDeDerive` a rougi (`600.0 == 120.0`) : local-suno `3fddc2c`
> (2026-09-09) a passé `ACESTEP_MAX_SEGMENT_SEC` à **600 s en v1.5** (défaut),
> 120 s en v1. Motif côté daemon : WER ~68 % en segments contre 2,7 % / 19,1 % en
> une passe. `split_arrangement_text` et `plan_segment_durations` n'ont pas
> bougé ; seul le plafond a changé. Vérifié in vivo dans `daemon.log` : les
> 3 rendus > 120 s depuis le 09-09 (165, 165, 180 s) sont partis en un seul appel
> ACE-Step, et la dernière ligne « Long-format » précède le changement.
>
> Resté à 120, `song_structure` calculait contre un découpage disparu : durée
> déduite gonflée (témoin à 9 sections : **218 s au lieu de 169**, chant étiré),
> avertissements sur un segment fantôme, refus « RE-CHANTERONT » de textes que le
> daemon rend intégraux. Aucune génération de Klody n'en a pâti — 0 session à
> marqueurs numérotés dans `library.db` depuis le 09-09 — mais c'était faux.
>
> - **Le mécanisme n° 2 (sections < segments) ne mord plus qu'en mode DÉCOUPÉ**
>   (v1, ou `ACESTEP_MAX_SEGMENT_SEC=120` côté daemon). En nominal, le plafond
>   (600) égale la durée maximale du contrat : jamais plus d'un segment. Le code
>   reste, il reste testé — sous la fixture explicite `chanson_decoupee`.
> - `plafond_segment()` réplique la **règle** (défaut selon `ACE_STEP_VERSION`,
>   surcharge prioritaire), confrontée au vrai `config.py` sur la même matrice que
>   local-suno, `.env` neutralisé. Un second test, `.env` compris, dit si le daemon
>   de CETTE machine est surchargé : deux tests, deux diagnostics.
> - ⚠️ Les sondes tournent avec les deux variables PURGÉES : le daemon tourne sous
>   launchd, qui ne les pose pas. Les hériter du shell rendait la suite
>   dépendante de l'environnement du développeur — attrapé en l'exportant exprès.
> - La sonde passe par le **vrai** `generate_song_long` (moteur remplacé par un
>   enregistreur) au lieu de recopier sa boucle `chunks.append(chunks[-1])`, et
>   `test_le_verdict_de_repetition_est_celui_du_daemon` exige que Klody refuse
>   « RE-CHANTERONT » **SSI** le daemon re-chante, dans les deux modes. Mutation
>   de cette boucle dans un miroir de local-suno : seul ce test rougit — la copie
>   recopiée l'aurait laissé passer.
## État au 2026-08-10 — la veille Qwen3.8, et une sonde de plus qui ment

Qwen3.8 annoncé le 2026-08-03. Deux checkpoints, **un seul intégrable ici** :

- **`Qwen3.8-Max`** : MoE **2,4 T params TOTAUX / 95 B actifs**, contexte 1 M,
  multimodal. **Hors de cette machine par construction** — règle RAM-MoE (on
  compte les params totaux, pas actifs) : ~1 200 Go en 4bit contre 80 Go de
  budget gateway, **facteur 15**. Aucune quantification ne le ferme ; ce n'est
  pas « attendre une conversion MLX ».
- **`Qwen3.8-27B`** dense : ~29 Go en 8bit, tient. **Seul chemin.** Non publié
  au 2026-08-10 — l'org officielle `Qwen/` en est encore à `Qwen/Qwen3.6-27B`
  (2026-04-21).

⚠️ **Les 11 dépôts « Qwen3.8 » de HuggingFace sont des faux.** Vérifié, pas
supposé : `Ma7ee7/Qwen3.8_4B_Distilled` a été créé **4 jours AVANT l'annonce**,
`config.json` = arch Qwen3-4B ; `huginnfork/Qwen3.8-27B-FP8` et `neroued/*` =
aucun `config.json`, 0 téléchargement. **Un nom de dépôt est une sonde qui ment
— le juge est `config.json`, jamais le nom.**

**La veille, `scripts/veille_qwen.py` + `launchagents/com.klody.veille-qwen.plist`**
(PR #214, mergée). Tick 24 h, interroge `Qwen`, `mlx-community`, `unsloth`,
confirme par `config.json`, notifie (osascript). État :
`~/Library/Caches/klody/veille-qwen.json` ; journal :
`~/Library/Logs/klody-veille-qwen.log`. Contrôle :
`/usr/bin/python3 scripts/veille_qwen.py --check`.

Trois propriétés la rendent **capable de rougir** — l'enjeu d'une veille dont
l'état nominal EST le silence, cas extrême du mode de défaillance dominant du
dépôt (bas de ce fichier) :

- « rien trouvé » et « rien regardé » rendent des **codes de sortie distincts**
  (0 / 1) ; l'échec total ne peut pas passer pour un run vert ;
- au-delà de **3 jours** sans une seule interrogation réussie, le silence se
  **DÉNONCE** par notification au lieu de se lire « rien de neuf » ;
- un `config.json` absent **nomme sa vraie cause** (GGUF — pas de config par
  nature ; placeholder ; dépôt incomplet), jamais « dépôt vide » indifférencié.

> ### ⚠️ MUTATION ÉCHAPPÉE — un test qui dérive avec la valeur qu'il surveille
>
> Trois mutations injectées à la mise au point ; deux attrapées d'emblée. La
> troisième, `MUETTE_JOURS = 3 → 99999` (donc alerte de mutisme DÉSARMÉE),
> **laissait la suite verte** : le test calculait l'âge du silence depuis la
> constante `MUETTE_JOURS` elle-même, il suivait donc le seuil au lieu de le
> juger. Corrigé — seuil **verrouillé sur un littéral** (`== 3`), âge de test en
> dur (30 j), mutation rejouée ⇒ rouge. Jumeau exact du reste du dépôt :
> **un test qui se recalcule à partir du réglage qu'il protège ne peut pas
> rougir.**

⚠️ **Ce que la veille ne peut PAS voir** : que launchd a cessé de la lancer —
elle ne parle que quand elle tourne. Ce trou reste couvert par
`scripts/install-launchagents.sh --check` (ligne `NON CHARGÉ`). Ce qu'elle
couvre, elle, c'est l'autre moitié : « elle tourne mais l'interrogation
échoue ».

**Le jour où le 27B dense sort** — chemin déjà écrit (A/B cerveau, point 5 plus
haut) : **entrée DÉDIÉE** du registre `~/klody-core/gateway/config.py`
(`pinned=False`), **jamais** une surcharge de `brain` (`pinned=True`, partagé
Library Brain + KlodyAI). Deux objections à **mesurer avant** l'A/B, pas après :
*dense ≠ MoE sur la vitesse* — le brain lit 3 B actifs/token, un 27B dense en
lit 27, prédiction ~8× plus lent au décodage contre une baseline déjà à
83 s/tour ; et *multimodal ⇒ mlx-vlm*, **absent du venv** (`mlx_lm` seul), alors
que le worker brain démarre via `mlx_lm.server`.

> ### ⚠️ SONDE QUI MENT (encore) — une PR mergée lue « en conflit »
>
> Après le merge de #214, un moniteur CI a signalé « PR #214 has merge
> conflicts ». Faux : `gh pr view` rendait `state: MERGED`, le commit sur
> `origin/main`. Cause : **sur une PR fermée, GitHub cesse de calculer la
> mergeabilité et rend `mergeable: UNKNOWN`** ; un moniteur qui lit `UNKNOWN`
> comme « conflit » crie sur CHAQUE PR mergée. **Le champ qui tranche est
> `state`, pas `mergeable`.** Même famille que les sondes menteuses de la
> mémoire projet — vérifier l'état réel avant d'agir sur une alerte.
>
> ⚠️ **Le vrai conflit, lui, avait existé** — mais AVANT : la branche re-portait
> 5 commits déjà squash-mergés (#210/#211/#212), d'où un conflit `CLAUDE.md`
> avec elle-même. `git rebase --onto origin/main` l'a ramenée à 1 commit /
> 3 fichiers. **Une branche coupée d'une branche locale squash-mergée re-porte
> son contenu et entre en conflit** ; le diff de contenu (`git diff A origin/main`)
> le dit — vide ⇒ redondant, nettoyable sans perte par rebase ou `reset --hard`.

## État au 2026-08-16 — la mémoire de Klody parle enfin MCP

Analyse d'un post r/LocalLLaMA (mémoire pour LLM via MCP, deux étages
embedding + reranker). Le post décrit à ~85 % ce que Klody faisait déjà :
embeddings locaux `bge-m3` en process, retrieval **hybride** vecteur + FTS5
fusionné RRF (`agent/semantic_memory.py`), extraction de faits
(`agent/memory_extractor.py`). **Deux écarts réels** confrontés au dépôt :

1. **Reranker cross-encoder (Qwen3-Reranker-4B), le titre du post — NON fait,
   délibérément.** Le « rerank » de la mémoire est cosinus, pas cross-encoder
   (`semantic_memory.py:69,82-84`), et c'est un choix : le moteur (`klody_memory`)
   est hors dépôt, et surtout la preuve mesurée du dépôt dit que **la précision
   de retrieval n'est pas le goulot** (encadré ✅ « l'ouverture de docs/ décide
   de tout » : l'agent reçoit le bon fichier nommé à 0,59 et ne l'ouvre pas). Un
   reranker améliorerait un étage qui n'est pas le problème — et la règle d'or
   interdit toute amélioration non chiffrée au bench. ⚠️ **Ne pas rejouer** cette
   piste sans mesurer d'abord que la précision est devenue le goulot.

2. **Mémoire exposée en MCP (le « GBrain » du post) — FAIT.** Toute la
   machinerie existait mais n'était offerte qu'en process (outil ReAct
   `rappeler_memoire`). Aucun client externe (Codex, ChatGPT Web, Claude Desktop)
   ne partageait cette mémoire. `klody_mcp/memory_server.py` (:8095) l'expose :
   `memoriser` / `rappeler` / `oublier` / `etat_memoire`, en **déléguant** à
   `agent.semantic_memory` (seule source de vérité — une seconde copie
   divergerait en silence). 100 % local, aucun modèle ni dépendance de plus.
   - Frontière MCP **ne lève JAMAIS** : indisponibilité ou crash moteur → dict
     lisible (même philosophie que `recall_for_llm`). Barrière ASI06 conservée :
     `rappeler` rend le texte déjà sanitisé par `recall_for_llm`, jamais de brut.
   - `etat_memoire` a **trois verdicts** (disponible / désactivée / moteur absent)
     et **nomme la cause** de l'indispo — jamais « rien » indifférencié.
   - Lanceur `scripts/start-memory-mcp.sh` + agent
     `launchagents/com.klody.memory-mcp.plist` (la règle « un agent par
     `start-*-mcp.sh` » est testée). Tests : `tests/test_memory_server.py`
     (délégation, refus nommés, contrat « ne lève jamais » ; le chemin dégradé
     est testé en réel — `klody-memory` est absent du conteneur de dev).

## État au 2026-09-03 — le nightly tournait 4 nuits sur 5 dans le vide

Plan d'optimisation : `docs/PLAN_OPTIMISATION_2026-09.md`, lot 0.1 (instrument,
BLOQUANT). Le nightly bench — seul juge du projet — a été muet **4 jours sur
5 en août** (8/40 runs verts). Personne ne l'a vu. Deux causes, deux correctifs.

**Cause 1 : `cancelled` (11/20)** — le Mac dort à 03:00 UTC (cron du nightly).
`sleep 1`, pas de `pmset repeat wakeorpoweron`. Les 4 succès coïncidaient avec
un Mac déjà réveillé pour d'autres raisons (DarkWake, activité utilisateur).
Correctif : `pmset repeat wakeorpoweron MTWRFSU 03:50:00` (réveil matériel) +
`scripts/bench-wake.sh` (`caffeinate -u -t 5400`, LaunchAgent à 03:52 local).
Couvre les deux changements d'heure : le cron tombe à 04:00 (hiver) ou 05:00
(été), les deux après 03:50.

**Cause 2 : `failure` (5/20)** — le job `macos-lockfile` rouge. Diff : le header
du lock macOS ne correspondait pas à ce que `pip-compile 7.5.3` produit (flag
`--no-index` ajouté automatiquement). **Zéro changement de paquet**, uniquement
des commentaires. Lock régénéré.

**Veille auto-dénonçante** : `scripts/veille_nightly.py` (jumeau de
`veille_qwen.py`), piloté par `com.klody.veille-nightly` (24 h). Notifie si
aucun nightly vert depuis **3 jours**. Codes de sortie distincts « rien à
signaler » (0) / « pas pu interroger » (1) — la mutation `MUETTE_JOURS → 99999`
est verrouillée par `test_seuil_muette_est_3` sur un littéral (la même mutation
a déjà échappé une fois sur la veille Qwen).

Test de couverture LaunchAgent étendu aux scripts de veille (`veille_*.py →
com.klody.veille-*.plist`). 14 tests.

⚠️ **Gate de sortie** : 5 nightly consécutifs `success`. Le `pmset repeat`
requiert `sudo` et n'a pas pu être posé dans cette session — l'utilisateur doit
le faire une fois. Sans lui, la cause 1 reste ouverte.

## État au 2026-09-20 — un 503 brut dans le chat, et ce qu'il cachait

Capture KlodyAI : « faut-il une autorisation pour vendre des bouteilles de
liqueur au marché ? » → bulle rouge `Error code: 503 - {'error': "RAM
insuffisante pour brain (~44 Go) : libre 80/80 Go virtuel, RAM réelle 46 Go
(plancher 12), rien d'évinçable de plus"}`, en-tête `[fallback: LLM error]`, et
un skill « Séquencer un visage 3D » injecté. Le 503 était LÉGITIME et
transitoire (RAM réelle 46 Go < 44 + plancher 12 ; une heure plus tard
`memory_pressure` rendait 70 % libres). Tout le reste était à nous.

> ### ⚠️ `stream_chat` n'est PAS le chemin de l'API — c'est `api/streaming.py`
>
> `_build_streaming_orchestrator` REMPLACE `orch.llm.stream_chat` par la closure
> `make_stream_api(...)`. Un correctif posé dans `agent/llm.py` (réessai, message)
> ne touche donc que la CLI. Et c'est `stream_api` qui envoyait `str(e)` à l'UI
> AVANT de relancer — or **le relais WebSocket s'arrête au PREMIER `error`**
> (`api/server.py`, `if et in ("done", "error"): break`) : le message de
> `run_agent` n'arrivait jamais. Désormais `stream_api` ne rend plus d'`error` ;
> `run_agent` est l'unique émetteur, via `agent/erreurs_llm.py` (cause + remède,
> même texte en CLI), et JOURNALISE — avant, ce 503 n'existait que dans l'UI.
>
> **Corollaire trouvé par le test : la file d'events est PAR CONNEXION.** Le
> `done` que `run_agent` posait après l'`error` restait en file et était consommé
> en tête du message SUIVANT, qui se terminait avant d'avoir commencé — deux
> messages avalés après une panne. Purge avant chaque run
> (`WS : N event(s) périmé(s) purgé(s)`), plus de `done` après `error`, et
> `_drain_until` des tests rougit sur un `error` inattendu au lieu de BLOQUER
> (deux runs de suite de 120 s ont été perdus à ça).

- **Réessai borné sur 503** (`LLM_503_ESSAIS=2`, `LLM_503_ATTENTE_S=5`, backoff
  ×2), décision UNIQUE dans `agent/erreurs_llm.py::attente_reessai_503`, branchée
  sur les DEUX chemins. Statut visible : filigrane `reasoning` dans l'UI, ligne
  jaune en CLI. Justifié comme le préflight du nightly : un 503 est rendu avant
  toute génération, le rejouer ne coûte rien.
- **Skills : `bout` ⊂ `bouteilles`.** `_term_matches` acceptait toute sous-chaîne
  de 4 caractères dans les deux sens ; « pipeline bout-en-bout » pêchait
  `bouteilles`, `auto` pêchait `autorisation` sur trois skills. Règle remplacée
  par un **radical** (`_meme_radical`) : le court est PRÉFIXE du long, ≥ 4 car.,
  écart ≤ 3 (`arbre`↔`arbres`, `next`↔`nextjs`). Rejoué sur les 37 skills réels :
  la question de liqueur n'injecte plus rien, la vraie demande « visage 3D »
  route toujours. Reste connu : un terme exact à df = 1 suffit (« auto » dans le
  nom d'un skill pêche « le prix d'une auto ») — homonymie, pas sous-chaîne.
- ⚠️ **Deux branches mortes dans `stream_chat`, trouvées par les tests, pas par
  la lecture.** (1) `except APIConnectionError` était placée AVANT
  `except APITimeoutError`, qui en HÉRITE : la bascule sur timeout n'a jamais
  tourné, un modèle lent était annoncé « injoignable ». (2) La bascule
  `MODEL_FALLBACK` (`mistral:latest`, un nom OLLAMA) s'appliquait aussi en
  `BACKEND=mlx`, où le gateway la rejette en 404 — et comme elle MUTE
  `self.model`, elle empoisonnait toute la session. `_fallback_model_utilisable`
  la réserve au mode ollama. Les relances rappelaient
  `stream_chat(messages, tools, token_callback)` : `tool_choice="required"`,
  `max_tokens`, `silent` perdus — `_rejouer` transmet tout.
- Le routeur nomme la cause : `[fallback: LLM error: HTTP 503]`.

## État au 2026-09-27 — le nightly muet 9 jours, et la veille qui ne POUVAIT pas le dire

Audit : **aucun nightly vert du 18 au 26 septembre** (dernier vert 09-17). Le
correctif de septembre (lot 0.1, ci-dessus) visait les bonnes causes avec de
mauvaises hypothèses. Quatre défauts empilés, chacun suffisant pour le silence :

| # | défaut | effet | correctif |
|---|---|---|---|
| 1 | venv du contrôle de lock dans **`/tmp`**, réutilisé d'un run à l'autre ; `com.apple.tmp_cleaner` purge chaque nuit ce qui n'a été ni lu ni modifié depuis 3 j | code de pip-tools purgé, dist-info vide ⇒ pip dit « déjà installé » ⇒ `pip-compile: No such file` — **5 rouges**, bench SAUTÉ. Venv créé le 09-16 : 3 verts, puis rouge | `venv --clear` sous `$RUNNER_TEMP` |
| 2 | le cron GitHub `0 3 * * *` tire entre **03:37 et 14:55 UTC** (90 runs planifiés, `gh run list --json createdAt`) ; chaque jour de septembre entre 07:26 et 08:36 UTC | le réveil `pmset` 03:50 + `caffeinate` 90 min tombaient TOUJOURS à côté ; run vers 10 h sur portable endormi ⇒ **4 annulés** | plus de `schedule:` ; déclencheur local `bench_dispatch.py` (5 créneaux, rattrapés au réveil, dédoublonnés) + `caffeinate` dans le job |
| 3 | `veille_nightly.py` importait `datetime.UTC` (3.11+) et son agent le lance sous **`/usr/bin/python3` = 3.9** | ImportError AU CHARGEMENT : la veille ne pouvait ni interroger ni notifier. Suite verte (testée sous 3.11). C'est **ruff UP017** (cible py311) qui l'avait réclamé | `timezone.utc` + `per-file-target-version = py39` + `tests/test_scripts_python_systeme.py` |
| 4 | `gh` absent du PATH de launchd (`/usr/bin:/bin:/usr/sbin:/sbin`) | même corrigé, la veille aurait rendu « pas pu interroger » à chaque tick | `trouver_gh()` (PATH puis Homebrew) |

Et au-dessus : **`com.klody.veille-nightly` et `com.klody.bench-wake` n'ont jamais
été chargés** (`install-launchagents.sh --check` : `ABSENT`). Le test « chaque
veille a son agent » compare des NOMS DE FICHIERS — vert sur un plist jamais
installé, exactement la limite déjà écrite plus bas pour les MCP.

- ⚠️ **Un linter qui cible une version réécrit du code qui tourne sous une
  autre.** Le fichier ne ment pas, la config du linter si. D'où la cible par
  fichier : dire au linter la vérité plutôt que `# noqa`.
- ⚠️ **Un commentaire XML ne tolère pas `--`.** launchd et `plutil` l'acceptent,
  `plistlib` refuse : `com.klody.veille-qwen.plist` était illisible pour tout
  test qui le parse. Verrouillé (`test_chaque_plist_est_du_xml_strict`).
- ⚠️ **Mesurer l'heure réelle d'un déclencheur avant de caler un réveil dessus.**
  Le lot 0.1 a réglé `pmset` sur l'heure ÉCRITE dans le cron ; `gh run list
  --json createdAt` donnait l'heure réelle en une commande.
- Gate de sortie inchangée : 5 nightlies verts consécutifs, APRÈS chargement de
  `com.klody.bench-dispatch` et `com.klody.veille-nightly`.

## État au 2026-09-27 — `~/.klody/data` était aux trois quarts des tests et du banc

~6 000 sessions `memory_*.json` là où la mémoire projet en comptait 1 624. Relevé
par contenu (`python scripts/etat_pollue.py`, qui le recompte et n'écrit rien) :
**4 456 de la suite, 1 236 du banc, ~310 gardées** (dont une cinquantaine de
tests probables, gardés par prudence : sans réponse, ou plus d'une minute). Effets : historique
KlodyAI noyé, `klody --continue` capable de rouvrir un test, et `user_profile.json`
— injecté dans le prompt — gonflé par les requêtes des tests et du banc.

- **Tests** : `test_websocket_chat.py`, les rejeux, `test_preview_feedback_loop.py`
  n'isolaient pas `config.MEMORY_DIR` (`test_api_routes.py`, à côté, le faisait).
  Sonde d'audit sur une passe complète : **234 écritures** dans le vrai dossier.
  Remède de SUITE, pas de fichier (`tests/garde_etat.py`) : `KLODY_DATA_DIR`
  redirigé AVANT `import config` — trois modules recopient le chemin à l'import,
  un monkeypatch ne les atteint pas — puis un hook d'audit qui REFUSE et CONSIGNE
  toute écriture sous le vrai dossier. Consigner est indispensable :
  `ConversationMemory.save` avale l'`OSError`, le refus seul laissait le test vert.
  Trois mutations vérifiées rouges (hook, dénonciation, redirection).
- **Banc** : une tâche = un **état** neuf (`--child-data-dir`, dans le dossier
  jetable de la tâche) ; le fils refuse de tourner sans. ⚠️ **Conséquence de
  mesure** : le banc injectait le profil et la mémoire long terme de
  l'utilisateur dans le prompt complet. La baseline a été mesurée avec ; le
  premier run qui suit, sans.
- ⚠️ **Rien n'a été supprimé.** `scripts/etat_pollue.py --quarantaine DIR` DÉPLACE
  (manifeste, réversible), refuse tant que `:8000` écoute. Règle « test » = premier
  message ET réponses scriptés : une vraie session ouverte sur « explique en
  raisonnant » (chaîne des tests) avec une vraie réponse du modèle y a été trouvée.
  `user_profile.json` reste pollué — compteurs agrégés, inséparables.
- ⚠️ Tout worktree dont la branche n'a pas ce correctif continue d'écrire dans le
  vrai dossier à chaque `pytest` (vu en direct pendant l'enquête).

## État au 2026-09-27 — l'extraction de faits était morte depuis 2,5 mois

`agent/memory_extractor.py::_client_llm` visait `OLLAMA_BASE_URL` avec
`MODEL_FALLBACK` (`mistral:latest`, un nom Ollama) **quel que soit `BACKEND`**.
En mlx, Ollama n'est même pas installé (`lsof -iTCP:11434` vide). Relevé dans
`logs/agent.log` : dernière extraction réussie le **2026-07-18 02:20**, puis
**164** « Erreur LLM » (127 fin de session, 37 mi-session ; timeouts en juin,
`Connection error.` depuis juillet), toutes en WARNING, sans URL ni modèle.
L'extraction de fin de message WebSocket (`mem-extractor`) et la mi-session
n'apprenaient plus rien, et rien ne le disait.

- **Cible** : `config.LLM_*` + alias `LLM_MODEL` (`brain`) en mlx ;
  `MODEL_FALLBACK` reste réservé au mode ollama — même piège que
  `_fallback_model_utilisable` (2026-09-20). `MEMORY_EXTRACTOR_MODEL` surcharge
  (une entrée dédiée du registre un jour, jamais une surcharge de `brain`).
- **Coût mesuré sur `brain`** (conversation maximale, 2 775 tokens de prompt) :
  **3,0 s** (2,1 s en rejeu exact, ce qui n'arrive pas en usage). Le cache de
  préfixe du chat (69 schémas) **survit** à une extraction (0,18 s avant et
  après, `--prompt-cache-bytes 8G`) ; un tour lancé pendant une extraction paie
  **+0,56 s**, pas 3 s (`--decode-concurrency 8`).
- ⚠️ **Coût INDIRECT** : un fait nouveau change `lt_section` du prompt système,
  donc le tour suivant rate le cache depuis le token 0 (préfixe EXACT, #270).
  **Mesuré et traité par #291** (section suivante) : système identique entre
  deux messages 95 % → 83 % avec extraction, ramené à 95 % en figeant la
  section pour la session.
- **Journal d'usage** : `X-Klody-App: klody-ai` + **`X-Klody-Source: system`** —
  sans lui, chaque extraction (une par message) serait classée `user` et
  nourrirait le miner d'habitudes. Session posée par requête (`extra_headers`),
  le client étant partagé. Vérifié en base : `klody-ai | system | <session>`.
- **La panne se voit** : la cause nomme la cible (`expliquer_erreur_llm`), le 3ᵉ
  échec consécutif passe en **ERROR** « HORS SERVICE », `etat_extraction()` rend
  trois verdicts (`non_tentee` / `operationnelle` / `en_echec`), lus par
  `/api/status` (`extraction_memoire`, informatif, jamais un 503), `/status` et
  la fin de session CLI. 12 mutations rejouées, 12 rouges.
- ⚠️ **Réparer la cible a rendu RÉELLE une fuite de la suite.** Le thread
  `mem-extractor` des tests WebSocket tapait :11434 (refusé, invisible) ; il
  tapait désormais `brain` — **2 appels par passe** de
  `tests/integration/test_websocket_chat.py`, lus dans `journal.db`. Garde de
  **session** dans `tests/conftest.py` (`_extraction_memoire_hors_reseau`).
  Contre-épreuve : SANS garde ⇒ +3 appels réels. La portée session est une
  PRÉCAUTION (le thread démon pourrait partir après le teardown) : la course
  n'a pas été observée — garde en portée test, 0 appel sur 5 passes, 0 sur 8
  côté #287. ⚠️ Première version de ce paragraphe : « une garde par test serait
  déjà restaurée », affirmé sans l'avoir mesuré — c'est #287 qui l'a relevé.
- ⚠️ **Mesurer contre le gateway sans `X-Klody-Source: system` pollue le miner** :
  ma propre mesure (app `mesure-extracteur`) a posé 17 événements `user` dans
  `journal.db` — une app inconnue n'est PAS dans `_SYSTEM_APPS`. Tout script de
  mesure doit poser l'en-tête.

## État au 2026-09-27 — le palier `discovery` tournait en mode QCM, sur brain

`Orchestrator._detect_interactive_skill` n'active le mode « skill interactif »
(QCM) que si la requête recoupe l'IDENTITÉ (nom + slug) du how-to de tête. Le
seul skill interactif s'appelle « Concevoir un algorithme **pas** à pas », et
`_STOP` ne contenait pas « pas » : toute négation française passait la garde.
Conséquence : tâche `feature` forcée sur le **généraliste** (brain) au lieu du
coder, anti-stall et text-to-action coupés, `ask_user` exposé. Présent depuis
#27. Correctif : « pas » dans les mots vides, apocope `algo` → `algorithme`
(`tools/skills.py`), verrouillé par `tests/test_qcm_negation_pas.py` sur les
VRAIS énoncés du banc.

> ### ⚠️ Toutes les mesures `discovery` de ce fichier ont été faites en mode QCM
>
> Rejoué par `git archive` à chaque commit (détection réelle, corpus réel) :
>
> | depuis | tâches en QCM |
> |---|---|
> | #175 (07-30) | `hidden_invariant`, `data_contract` |
> | #177 (07-30) | + `first_write_method` |
> | #259 (09-20) | + `config_precedence`, `error_contract` (le radical a changé les classements) |
> | + | `real_repo/fix_from_known_issue` |
>
> **Ce qui tient** : les deux jumeaux étaient TOUS DEUX en QCM le 2026-07-30,
> l'appariement de l'encadré « l'ouverture de `docs/` décide de tout » reste
> valide. **Ce qui était faux** : « routent tous deux en `easy · feature` »
> n'impliquait PAS le coder — ils tournaient sur brain, anti-stall coupé. Le
> garde « décisions jamais ouvertes » (0/5 → 5/5) a été mesuré dans ce mode.

**Le mécanisme réel n'est pas celui qu'on croyait en ouvrant le chantier.**
« pas » n'a PAS df = 1 : df = 10 sur nom+desc+slug, et ne pèse que 2,05 sur ~30
de score. Le skill arrive en tête des how-to par la consigne commune du palier —
`list_files` → « file » (la file FIFO de sa description), « liste », « avant »,
« écrire », « sera ». « pas » ne sert qu'à franchir la garde d'identité.

- ⚠️ **Le seuil « df faible » pour l'identité est RÉFUTÉ, ne pas y revenir.**
  df(« pas ») = 10 quand « structure » = 20, « méthodes » = 31, « digest » = 45 :
  tout seuil de df rejetterait des mots d'identité légitimes avant « pas ». La
  df sur le contenu complet sépare (92 % contre 68 % au suivant, « livre »), mais
  sur un point unique, et elle grimpe avec chaque digest de livre. La
  distinction utile est grammaticale : un mot vide.
- ⚠️ **La locution « pas à pas » comme terme : écartée.** Elle sauvait « conçois
  mon algo pas à pas », mais décrit une MANIÈRE : « corrige ce bug pas à pas »
  basculait en QCM. C'est l'apocope `algo` qui nomme le sujet.
- **Rayon de souffle en prod** (1 787 messages utilisateur distincts de
  `~/.klody/data`) : 319 activaient le QCM, 3 après — toutes des demandes de
  conception. Sur les 319, 311 étaient… des runs du banc, et 1 vrai faux positif
  humain (« Lis le code source de LibraryBrain… N'utilise PAS … »).
  `bench.skill_routing_eval` (IDF) inchangé, hit@1 13/18, hit@3 14/18.
- ⚠️ `select_skills` N'écarte PAS ce skill des tâches `discovery` : il reste en
  tête des how-to injectés au brain. Le commentaire du routeur qui affirmait le
  contraire (« un skill non pertinent n'atteint jamais la tête de liste ») était
  faux — corrigé. C'est la garde d'identité qui tranche.

> ### ✅ MESURÉ — le coder ne régresse pas sur `discovery` (A/B du jour même)
>
> `bench/results/reference_2026-09-27_discovery_qcm_contre_coder.json`. Avant =
> `origin/main` (QCM, brain) ; après = correctif (coder). 3 passes par tâche.
>
> | tâche | avant : succès · `docs/` spont. · garde | après : succès · `docs/` spont. · garde |
> |---|---|---|
> | `hidden_invariant` | 3/3 · 2/3 · 1/3 | 3/3 · 1/3 · 2/3 |
> | `first_write_method` | 3/3 · 3/3 · 0/3 | 3/3 · 3/3 · 0/3 |
> | `config_precedence` | **0/3** · 3/3 · 0/3 | **2/3** · 3/3 · 0/3 |
> | `error_contract` | 3/3 · 3/3 · 0/3 | 3/3 · 3/3 · 0/3 |
> | `data_contract` | 3/3 · 0/3 · 0/3 | 3/3 · 0/3 · 0/3 |
> | **total** | **12/15** | **14/15** |
> | `real_repo/fix_from_known_issue` | 3/3 | 3/3 |
>
> **Aucune régression, amélioration NON établie** : Fisher p = 0,60 sur le
> total, 0,40 sur `config_precedence` (0,14 avec le premier essai avant, 0/4).
> `tool_calls_cassés` = 0 partout.
>
> Les deux bras échouent `config_precedence` en LISANT le README (3/3 des deux
> côtés) — « a cherché sans comprendre », pas « n'a pas cherché » :
> - brain : `argparse` `type=int` → `SystemExit` au lieu de `ValueError` sur une
>   valeur CLI illisible, **reproductible** (4/4 avec le premier essai) ;
> - coder, passe 1 : « option mal formée → ignorée » au lieu de `ValueError`.
>
> ⚠️ `hidden_invariant` sur brain ouvre désormais `docs/` spontanément 2/3 (la
> référence du 07-30 disait 0/8 puis 0/5). Non expliqué ici — le retrieval, muet
> 12 jours jusqu'à #271, et #269 ont changé depuis. À re-mesurer avant d'en
> tirer quoi que ce soit.
>
> ⚠️ Le premier essai du bras avant (dans l'ordre, un seul `--repeat 3`) avait
> 8 instances sur 15 perdues par l'infra (encadré suivant) : il a été REJOUÉ
> tâche par tâche plutôt que comparé tel quel. Parmi ses 7 instances propres,
> un `hidden_invariant` ❌ montre un trou du garde : écriture sans ouvrir
> `docs/`, conclusion à l'itération 6/6 — or le garde exige
> `iteration < max_iter - 1`. Avec `max_iter = 6` (`easy · feature`), un agent
> qui explore 4 appels avant d'écrire sort de la fenêtre du garde. **Fermé par
> #292**, avec le même trou du garde LibraryBrain.

> ### ✅ MESURÉ — `main` après #288 + #292 : 14/15, porte verte de justesse
>
> `bench/results/reference_2026-09-28_discovery_main.json` — `main` à `fb91d89`,
> `discovery --repeat 3`, 2026-09-28 11:27 → 12:01.
>
> | tâche | succès | `docs/` spont. | garde doc | itér. méd. |
> |---|---|---|---|---|
> | `hidden_invariant` | 3/3 | **3/3** | 0/3 | 5 |
> | `first_write_method` | 3/3 | 3/3 | 0/3 | 5 |
> | `error_contract` | 3/3 | 3/3 | 0/3 | 4 |
> | `data_contract` | 3/3 | 0/3 | 0/3 | 5 |
> | `config_precedence` | **2/3** | 3/3 | 0/3 | 4 |
>
> - Les 15 exécutions sur le **coder** (15 bascules journalisées) : le correctif
>   du QCM tient en conditions réelles. 0 panne d'infra, 0 appel cassé.
> - `config_precedence` est l'unique échec, **même mode que la veille sur le
>   coder** : README lu, puis « option mal formée → ignorée » au lieu de
>   `ValueError`. 4/6 sur le coder en deux jours, contre 1/7 sur brain.
> - Porte : `Δ −6,7 %` contre la baseline à 35 tâches (#296, seuil **0.075**)
>   — verte avec **0,8 point de marge**. Une seconde perte sur `discovery`
>   seule la ferait rougir.
> - 0 déclenchement des gardes doc et LibraryBrain, 0 auto-continue : le
>   correctif #292 n'est pas exercé ici, il ne gêne pas non plus.
>
> ⚠️ **Conditions NON identiques à la baseline** : lancé depuis un worktree, le
> banc remonte au `.env` et chaque exécution reçoit **314 outils MCP** dans son
> prompt (journalisé 15/15) — la baseline #296 a été mesurée hors du dépôt,
> sans eux. Et depuis #279 le banc n'injecte plus le profil ni la mémoire long
> terme de l'utilisateur. L'ouverture spontanée de `docs/` sur
> `hidden_invariant` (3/3, contre 1/3 la veille sur le coder) ne s'attribue donc
> à rien : n=3, et deux variables de prompt ont bougé.

- ⚠️ **`bench.gate` ne jugeait que la DERNIÈRE passe d'un run `--repeat N`**,
  et annonçait les passes précédentes comme « N hors baseline, non jugée(s) ».
  Vécu sur ce run : 14/15 lu « 100 %, Δ +0,0 % » — l'échec de la passe 1 lui
  était invisible. Corrigé le jour même (#280) ; rejouée sur le même JSON, la
  porte rend `Δ −6,7 %` (93,3 %, sous le seuil de 9 points) et nomme
  `config_precedence (1/1 → 2/3)`.
- Les deux bras ont tourné AVANT #279 : le banc injectait encore le profil et la
  mémoire long terme de l'utilisateur dans le prompt. À égalité entre les bras,
  mais les chiffres ne sont pas ceux d'un banc isolé.
- ⚠️ **brain (52 Go) + coder (40 Go) = 92 > 80** : ils ne sont plus
  co-résidents. Sur le coder, chaque tâche paie brain (routeur LLM) PUIS coder —
  deux chargements. C'est désormais le coût de TOUTE tâche de code routée.
- ⚠️ **Le banc partage brain avec le reste de la machine**, et ce jour-là tout
  tapait dessus : `klody-core-tests` (suite de tests d'une autre session contre
  le gateway VIVANT, qui l'a redémarré à 16:09:55), KlodyAI, Library Brain.
  Résultat : `RemoteProtocolError`, `APIConnectionError`, puis des timeouts 600 s
  en cascade — un enfant tué laisse sa génération tourner sur brain, la suivante
  fait la queue. Le juge des requêtes en vol est `~/klody-core/state/journal.db`
  (colonne `app`), pas `/health` : son `inflight` est resté à 2 pendant des
  minutes, banc arrêté, alors que le seul client restant envoyait un appel de
  ~3 s par minute.

## État au 2026-09-27 — la mémoire longue terme du système est figée pour la session

L'extraction de faits réparée écrit dans `LongTermMemory` après CHAQUE message
WebSocket, et `format_for_prompt()` vit dans le prompt SYSTÈME — or le cache de
préfixe de mlx_lm ne réutilise qu'un préfixe exact (#270). Mesuré, pas supposé
(`scripts/mesure_stabilite_memoire.py`, rejeu des 45 sessions réelles les plus
récentes avec la VRAIE extraction sur `brain` ; relevé complet :
`bench/results/reference_2026-09-27_stabilite_memoire.md`) :

| système (profil + mémoire) identique entre deux messages | 93 paires |
|---|---|
| sans extraction (production depuis le 2026-07-18) | 95 % |
| **extraction à chaque message** | **83 %** |
| **extraction + section figée** | **95 %** |

11/53 extractions écrivent (21 %), et **chacune** change la section : une ligne
de plus dans une petite catégorie (`user` 1, `preference` 2, `project` 6
entrées), ou la fenêtre des 15 `context` les plus récents qui glisse. Un raté
coûte 11,2 s contre 0,55 s à 69 outils (#270), ~120 s au régime de l'API de prod
(297 outils MCP, prompt ~92 k tokens).

- **Remède** : `agent/long_term_memory.py::section_de_session` rend la section
  UNE fois par `ConversationMemory` (qui survit aux messages WS — l'Orchestrator,
  non). Un fait extrait devient visible à la session suivante : il vient de la
  conversation en cours, déjà sous les yeux du modèle.
- Trois invalidations, parce que cette justification tombe : `remember_fact` et
  `forget_fact` (demande EXPLICITE — un fait oublié ne doit pas rester affirmé ;
  3 appels pour 1 359 réponses dans les sessions réelles), et
  `ConversationMemory.clear()` (`/clear` efface l'historique d'où venaient les
  faits).
- ⚠️ **Aujourd'hui l'effet est masqué** : skills et retrieval, placés AVANT
  `lt_section`, changent déjà le système sur ~91 % des paires. Le gain apparaît
  quand ils sortent du prompt système (branche `claude/contexte-tour-utilisateur`).
- ⚠️ **Le banc ne peut pas le voir** : une tâche = un état neuf (mémoire vide) et
  un seul message. Il a tourné quand même : **34/35**, porte verte (`Δ −3,3 %`).
  Seul échec, `discovery/config_precedence` (puis 0/3 rejouée) — alors que le
  prompt système du fils du banc est **identique octet pour octet** à `main`
  (sonde : même sha, 65 395 car.). Témoin `main` le même jour : 2/3, même
  signature (`argparse type=int` ⇒ `SystemExit` au lieu de `ValueError`), déjà
  relevée par #288. Même entrée, verdicts différents : de la variance, sur une
  tâche instable partout ce jour-là (3/10 toutes branches confondues).
- ⚠️ Les vrais tours utilisateur d'une session se reconnaissent à leur
  `timestamp` : les relances de l'orchestrateur (« Ton budget d'itérations… »,
  « STOP — ne conclus pas… ») sont AUSSI en rôle `user`, posées à `timestamp:
  None` — 89 sur 659 messages `user` de `~/.klody/data`. Compter les rôles
  compterait des tours qui n'existent pas.

## État au 2026-09-28 — baseline à 35 tâches, et le seuil qui l'accompagne

Les 5 tâches `real_repo` tournaient chaque nuit hors baseline (« 5 tâche(s) hors
baseline, non jugée(s) »). Promues, avec un run FRAIS `--repeat 3` : **104/105**,
seul `easy/add_simple_test` instable (✅ ❌ ✅ — l'agent AFFICHE le code du test au
lieu d'appeler `write_file`, puis conclut). Promu tel quel : relancer jusqu'au
35/35 aurait été choisir la mesure qui arrange.

- **Cause du flake trouvée, et c'était l'orchestrateur** : `test_gen` n'était
  couvert par AUCUN des trois filets (anti-stall, auto-continue, text-to-action),
  qui ne tournaient que sur 4 types recopiés à la main en trois endroits — les
  types de #96 (2026-07-05) n'y avaient jamais été reportés. Sur le banc, 13
  tâches sur 35 tombaient dans ce trou. Fermé par le garde « code affiché sans
  écriture » (`test_gen`, `docs`, `migrate`, `edit`) ; tout TaskType doit être
  classé (`tests/test_types_qui_ecrivent.py`). ⚠️ Étendre le text-to-action
  n'aurait PAS suffi : il écrit tout bloc Python dans `script.py` — le test serait
  parti au mauvais endroit, et dans un vrai projet un fichier parasite.
- ⚠️ **Deux bancs lancés en parallèle (deux sessions) se contaminent** : chacun
  prend les 503 de l'autre, et les deux runs sont à jeter. `pgrep -fl bench.run`
  avant de lancer.

- ⚠️ **Promouvoir sans recalculer le seuil DESSERRAIT la porte** : 3 tâches
  cassées sur 35 = −8,57 %, VERT sous 0.09 ; il en fallait 4, contre 3 sur 30.
  Seuil passé à **0.075** (3 aux deux tailles). 0.08 marchait aussi mais tombe
  pile sur des écarts exacts (2/25, 14/175) où seul le `<` strict tranche.
  Sensibilité vérifiée sur le fichier promu lui-même : 3 tâches cassées rougissent,
  `add_simple_test` comprise ou non — le tiers de tâche figé ne rachète rien.
  Tableaux : `bench/gate.py`, verrouillés par `tests/test_gate_sensibilite.py`.
- ⚠️ **Un run lancé depuis un worktree n'est PAS un run de nightly.** `config`
  fait `load_dotenv()` sans chemin : depuis `.claude/worktrees/…` il remonte au
  `.env` du dépôt principal (15 serveurs MCP, ~300 outils dans le prompt). Le
  checkout du runner n'en a aucun au-dessus de lui. Promotion faite depuis un
  checkout HORS de `~/Projets/klody-code-ai`, venv du runner, variables du
  workflow — `config.MCP_SERVERS == {}` et `CONTEXT_WINDOW` 65536 vérifiés avant.
- ⚠️ **Un premier run a été JETÉ : 3 échecs sur 4 tâches, tous des 503** « RAM
  insuffisante pour coder ». `brain` (52 Go) + `coder` (40 Go) ne tiennent plus
  ensemble quand la machine sert autre chose : le gateway évince l'un pour
  l'autre (~3 s, poids en cache — supportable), mais refuse net si l'autre a une
  requête en vol. Un autre client martelait `brain` (137 requêtes en 20 min) :
  chaque appel `coder` du banc prenait un 503, compté comme un échec du modèle.
  Le journal qui le dit : `~/klody-core/logs/gateway.error.log` (« pression
  mémoire … déchargement de coder », « ensure(coder) refusé »). Critère de rejet
  appliqué au run retenu : **zéro** « Backend indisponible » dans son log.
- `bench.run` a laissé un fils `--child-task` ORPHELIN après un `pkill` du
  parent : il a rechargé `brain` tout seul. Tuer aussi les `--child-task`.

## État au 2026-09-28 — le contexte d'une requête suit le tour, plus le système

Le cache de préfixe de mlx_lm 0.31.3 (ArraysCache du MoE, **non rognable**) ne
réutilise qu'une entrée dont les tokens sont un préfixe EXACT du nouveau prompt
(`LRUPromptCache.fetch_nearest_cache` : sans `trim`, seule la branche `shorter`
sert). Quand le dernier message est `user`, le serveur pose un point de
reprise à la fin du bloc système — et le template Qwen y écrit **les schémas
d'outils PUIS le système**. Un octet variable dans le système ⇒ tout recalculer
depuis le token 0. Or retrieval et skills how-to changent à chaque requête.

`_inject_system_prompt` ne met plus dans le système que ce qui tient une session
(prompt de base + prompt de tâche, dossier, **skills permanents**
`utilisateur_*`/`conventions_*`, mémoire long terme, profil, conventions,
erreurs). Retrieval + skills how-to vont sur le message user COURANT, dans une
clé privée `_contexte_tour` (`ConversationMemory.ancrer_contexte_tour`) : jamais
dans `content`, jamais sur disque, rendue par `get_messages_for_api` seulement —
donc identique à chaque itération ReAct, et absente des tours suivants.

| mesure (2026-09-28, `~/.klody/data` assaini, 251 sessions) | avant | après |
|---|---|---|
| système identique, 386 paires, `task_type` fixe | 22 % | **95 %** |
| idem, routeur RÉEL rejoué, 245 paires brain→brain | 17 % | **68 %** |
| 1ᵉʳ appel du 2ᵉ message (92 k tokens, 383 outils), sur main 83d8c8a | cached=0, 134,3 s | **cached=85 990**, 35,9 s |

`scripts/mesure_stabilite_prompt.py [--routeur]` et
`scripts/mesure_cache_deux_messages.py` les recalculent. Le premier écarte
désormais les sessions de tests et du banc (classifieur d'`etat_pollue.py`) :
la veille, sur le dossier encore pollué, il rendait 8 → 86 % et 0 → 82 %.

Relevé complet : `bench/results/reference_2026-09-28_contexte_tour.md`.

- ⚠️ **L'ancre est posée par run(), pas sur le dernier `user`.** Les relances
  (auto-continue, anti-stall, synthèse forcée) s'ajoutent en `user` au fil du
  tour : un contexte qui les suivrait déplacerait le préfixe à chaque itération.
  Et la mémoire survit à l'orchestrateur (`_sessions` côté API, un seul
  orchestrateur en CLI) : `ancrer_contexte_tour` RETIRE l'ancre précédente,
  sinon chaque ancien tour repartirait avec son contexte.
- ⚠️ **Les skills permanents restent dans le système.** `select_skills` les
  renvoie en tête sans test de pertinence ; ~3,4 k tokens (heuristique) de
  profil. Dans le tour, ils seraient re-prefillés à chaque message.
- ⚠️ **Avec le vrai routeur, le prompt de tâche est le premier casseur restant**
  (68 % contre 95 % à type fixe, profil identique à 96 %) : `explain.md` ↔
  `creative.md`/`music.md`… — 130 messages `creative` sur 636 classés.
  `compose_system_prompt(task_type)` reste dans le système, délibérément : le
  sortir change le comportement bien plus que les skills, à mesurer seul. Borne
  basse : mlx_lm garde jusqu'à 10 entrées et évince les points de reprise
  « system » en dernier — un retour à `explain` peut retrouver le sien.
- ⚠️ **383 outils ≈ 70 k tokens de schémas** avec les serveurs MCP du `.env` :
  un prompt de 92 k tokens, **~135-250 s de prefill à froid** sur brain. Le
  banc les voit aussi (le worktree trouve le `.env` principal en remontant).
- Le banc passe maintenant par `[cache]` lui aussi : `agent/llm.py` demande
  `usage` en streaming et journalise comme l'API (`agent/cache_prefixe.py`).
  En mode coder le système n'est plus que le prompt slim, constant : son point
  de reprise sert d'une tâche à l'autre — 12 appels froids sur 439 dans le banc
  complet, médiane 99,7 % en cache pour les autres.
- **Banc** (`--repeat 3`, rebasé sur 83d8c8a) : **103/105, porte verte** (99,0 →
  98,1 %, Δ −1,0 pt, seuil 7,5). `config_precedence` 2/3 est aussi à 2/3 sur
  main le même jour ; `null_byte_sanitize` 2/3 (SECURITY.md non ouvert, alors
  que le retrieval le nommait DANS le tour) ne se reproduit pas en apparié
  `real_repo` × 5 : 5/5 des deux côtés, SECURITY.md lu 5/5, total 25/25
  contre 23/25 sur main.
- ⚠️ **Le banc n'est PAS l'instrument du gain inter-messages** : il rejoue des
  tâches identiques d'une passe à l'autre, donc main y réutilise aussi ses
  points de reprise (89,9 % des tokens en cache contre 93,4 %). Le scénario
  visé — deux messages différents d'une même session — se mesure avec
  `scripts/mesure_cache_deux_messages.py`.

## Pièges qui coûtent du temps

- ⚠️ **Un `load_dotenv()` placé APRÈS un import arrive trop tard pour tout
  module qui lit ses réglages à l'import.** Trouvé le 2026-09-27 :
  `vocalbrain_server` et `klody_music_server` importaient `song_structure`
  (`KLODY_SONG_*`, `ACESTEP_*`, `ACE_STEP_VERSION`) avant leur `load_dotenv()`,
  et six serveurs faisaient de même avec `_pathguard` (`KLODY_MCP_AUDIO_ROOTS`).
  Toute valeur du `.env` était ignorée en silence — une racine audio RESTREINTE
  dans le `.env` laissait le garde sur ses défauts, plus larges. Seul l'export
  (plist, `start-*-mcp.sh`) comptait. Latent (aucune de ces variables n'était
  dans le `.env`), invisible en test : `conftest` importe `config`, qui charge le
  `.env` avant tout. Le `.env` est désormais chargé par `klody_mcp/__init__.py`,
  qui précède tout module du paquet ; `tests/test_klody_mcp_dotenv.py` importe une
  COPIE du paquet dans un processus neuf, `.env` posé à côté, et rougit si
  l'ordre se réinverse (vérifié : 8 rouges sans le chargement, 1 avec
  `override=True`).
- ⚠️ **Un `pip install` ne prend effet qu'au redémarrage des services — et
  l'oubli ne se voit que des heures plus tard.** Incident du 2026-08-05
  ci-dessus. Réflexe : après toute mise à jour de `requirements.lock`,
  `python scripts/diagnostic_peremption.py`.

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
  ⚠️ Revu le 2026-09-27 : « même prompt » tient, mais c'était le prompt du mode
  QCM, sur brain — les deux jumeaux basculaient en skill interactif par le
  « pas » de leur énoncé (section « le palier `discovery` tournait en mode QCM »).

## Le mode de défaillance dominant du dépôt

Quatre compteurs faux et trois garde-fous incapables d'échouer, découverts en une
session. Aucun par négligence : tous étaient justes le jour de leur écriture, et
personne ne les recomptait.

> Un garde-fou qui ne peut pas rougir est indiscernable d'un garde-fou vert.

Deux réflexes qui en découlent : tout chiffre affiché doit avoir une commande qui
le recalcule, et tout gate doit distinguer « je n'ai pas pu juger » de « j'ai
jugé, c'est bon ».
