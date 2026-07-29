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
> variance sur une tâche dépasse couramment l'écart qu'on cherche à mesurer. Chaque
> côté accepte donc N fichiers, agrégés par `task_id` (succès comptés sur N, métriques
> moyennées). Viser ≥3 runs par côté ; en dessous, le rapport le signale lui-même.
>
> ```bash
> for i in 1 2 3; do python -m bench.run --label qwen_$i; done
> ```
>
> Un `bench.run --repeat N` reste à faire, tout comme l'enregistrement de la config
> modèle dans le JSON — aujourd'hui c'est `--label` qui porte cette information, à la
> main.

La comparaison se fait sur l'**intersection des `task_id`** : les tâches absentes d'un
côté sont listées puis écartées des agrégats, jamais comptées comme des échecs.

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

Créer `bench/tasks/<category>/<task_id>.py` avec :

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
