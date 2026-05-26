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

# Compare deux runs
python -m bench.compare results/2026-05-26_baseline.json results/2026-05-27_mlx.json
```

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
