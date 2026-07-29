# Klody Bench

Banc de mesure reproductible pour piloter l'évolution de Klody (cf [`../ROADMAP.md`](../ROADMAP.md)).

## Principe

20 tâches catégorisées (5 easy / 10 medium / 5 hard), chacune avec :
- une `prompt` qu'on envoie à Klody
- une `setup()` qui crée un répertoire fixture isolé
- un `validate()` qui vérifie le résultat (souvent en exécutant des tests)

Le runner mesure pour chaque tâche : succès, latence, tokens/s, tool calls cassés, etc.

## Usage

```bash
# Lance le bench complet sur Klody (config courante)
python -m bench.run

# Filtre par catégorie
python -m bench.run --category easy
python -m bench.run --category medium

# Une seule tâche pour debug
python -m bench.run --task easy/rename_var

# N passes complètes (indispensable pour comparer deux configs — voir plus bas)
python -m bench.run --repeat 3 --label qwen
```

## Comparer deux configurations (A/B)

`bench/compare.py` met deux runs — ou deux **séries** de runs — face à face. C'est
l'outil pour trancher une question du type « ce cerveau est-il meilleur que l'autre
sur *mes* tâches ? », là où `bench.gate` ne rend qu'un verdict binaire vs baseline.

```bash
# Deux runs, cas simple
python -m bench.compare results/2026-07-29_qwen.json results/2026-07-29_oss.json

# Séries répétées, avec des noms lisibles
python -m bench.compare -a results/qwen_*.json -b results/oss_*.json \
    --label-a qwen3.6 --label-b gpt-oss-120b

# Sortie Markdown (collable dans une PR ou la ROADMAP)
python -m bench.compare a.json b.json --format md > compare.md
```

Le rapport donne les agrégats (succès, latence, débit, **tool calls cassés**,
itérations ReAct), la ventilation par catégorie — un modèle peut gagner sur `easy` et
perdre sur `hard` —, les **bascules** tâche par tâche (ce qui passe au vert, ce qui
passe au rouge), puis le détail complet.

> ⚠️ **Un run par côté ne conclut rien.** Un agent LLM est non-déterministe : sa
> variance sur une tâche dépasse couramment l'écart qu'on cherche à mesurer. Viser
> **≥3 passes par côté** ; en dessous, le rapport le signale lui-même.

Deux façons d'obtenir ces répétitions, cumulables et agrégées de la même manière
(succès comptés sur N, métriques moyennées) :

```bash
python -m bench.run --repeat 3 --label qwen        # 3 passes dans UN fichier
for i in 1 2 3; do python -m bench.run --label qwen_$i; done   # 3 fichiers
```

`--repeat` exécute **N passes complètes** sur la sélection, pas N exécutions
consécutives par tâche : répéter une tâche dos à dos la sert depuis un cache de prompt
chaud, ce qui fabriquerait une latence artificiellement basse sur les répétitions.

La comparaison se fait sur l'**intersection des `task_id`** : les tâches absentes d'un
côté sont listées puis écartées des agrégats, jamais comptées comme des échecs.

### Provenance : quel modèle a produit ces chiffres

Chaque run enregistre sa configuration dans `meta` — backend, base URL, modèle
configuré, **modèle réellement servi**, fenêtre de contexte, nombre de passes. Sans
`--label-a/-b`, `bench.compare` nomme donc chaque côté d'après le modèle qui l'a
produit, et affiche la provenance intégrale en tête de rapport.

Le modèle servi est résolu par une complétion d'un token : en mode gateway, `.env` ne
contient qu'un alias (`brain`), et le `/v1/models` de `mlx_lm.server` liste tout le
cache HF plutôt que le modèle chargé — la réponse OpenAI est la seule lecture fiable.
La sonde est best-effort : gateway injoignable, le run tourne quand même et
`model_served` reste `null`.

Les fichiers passent donc d'une liste plate à une enveloppe `{"meta": …,
"results": […]}`. Les deux formats restent lus, y compris par le gate : les baselines
déjà promues ne sont pas invalidées.

## Gate de non-régression

`bench/gate.py` compare `results/latest.json` à `results/baseline.json` et sort en
erreur si le taux de succès chute de plus de 10 points. C'est ce que lance le
workflow `bench-nightly`.

```bash
python -m bench.gate                    # baseline ↔ latest, seuil par défaut
python -m bench.gate --max-drop 0.05    # seuil plus strict
```

La comparaison se fait sur l'**intersection des `task_id`** : un run filtré
(`--category easy`) reste jugeable face à une baseline complète sans que la
différence de périmètre soit lue comme une régression.

**La baseline est versionnée** (exception explicite dans `.gitignore`). Sans elle,
le gate se neutralise avec un `::warning::` — il ne peut donc jamais échouer, ce qui
a été le cas tant que le fichier restait ignoré. Pour en promouvoir une :

```bash
python -m bench.run --promote-baseline   # écrit results/baseline.json
git add bench/results/baseline.json && git commit   # pas de -f : le fichier est dé-ignoré
```

À refaire délibérément après tout changement qui déplace légitimement le niveau de
référence (bascule de modèle, refonte du routeur) — jamais pour faire taire un run
rouge.

## Sortie

Chaque run produit :
- `results/<timestamp>.json` — données brutes machine-readable
- `results/<timestamp>.md` — table Markdown lisible
- `results/latest.json` — symlink/copie du dernier run

## Ajouter une tâche

Ajouter la classe dans `bench/tasks/<category>.py` (`easy.py`, `medium.py` ou
`hard.py`) :

```python
from bench.framework import Task, register

@register
class RenameVar(Task):
    id = "easy/rename_var"
    category = "easy"
    prompt = "Dans le fichier app.py, renomme la variable `usr` en `user` partout."

    def setup(self, workdir):
        (workdir / "app.py").write_text("usr = 'alice'\nprint(usr)\n")

    def validate(self, workdir):
        src = (workdir / "app.py").read_text()
        return ("usr" not in src) and ("user" in src), "renaming complete"
```

Pas besoin d'enregistrer manuellement : `bench/run.py` découvre via `bench/tasks/`.
