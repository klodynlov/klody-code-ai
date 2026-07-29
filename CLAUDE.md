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

## État au 2026-07-29

Couverture **84,3 %** (gate 80), **2146 tests**. Huit PR (#162→#169) ont remis
l'instrumentation en service : gate de non-régression opérationnel, `bench.compare`
écrit, `--repeat` + provenance, sentinelle runner, et couverture de
`semantic_memory` (98 %), `embeddings` (100 %), `audio` (96 %), `orchestrator` (76 %).

### Ce qui reste à faire

1. **Enregistrer un runner self-hosted** — labels `self-hosted, macOS, klody`.
   Sans lui le nightly fait la queue puis est annulé ; le job `verify-runner` le
   dit désormais explicitement au lieu de laisser un historique vert-vide.
2. **Promouvoir une baseline** : `python -m bench.run --promote-baseline`, puis
   committer `bench/results/baseline.json` (dé-ignoré exprès). Sans elle le gate
   s'annonce neutralisé par un `::warning::` — il ne peut pas échouer.
3. **Trancher l'A/B cerveau** : Qwen3.6-35B-A3B (~50-60 tok/s) vs gpt-oss-120b
   (~61 GB, ~30 tok/s, réputé pour la fiabilité de ses tool calls). Hypothèse à
   falsifier : gpt-oss gagne sur `hard`, perd sur `easy`, parce qu'un tool call
   malformé coûte une itération entière là où 25 tok/s de moins ne coûtent que
   des secondes. **La colonne à regarder d'abord est `tool_calls_cassés`.**

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

## Le mode de défaillance dominant du dépôt

Quatre compteurs faux et trois garde-fous incapables d'échouer, découverts en une
session. Aucun par négligence : tous étaient justes le jour de leur écriture, et
personne ne les recomptait.

> Un garde-fou qui ne peut pas rougir est indiscernable d'un garde-fou vert.

Deux réflexes qui en découlent : tout chiffre affiché doit avoir une commande qui
le recalcule, et tout gate doit distinguer « je n'ai pas pu juger » de « j'ai
jugé, c'est bon ».
