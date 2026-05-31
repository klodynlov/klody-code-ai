# Skills distillés (livre → JSON → artefact)

Pipeline générique de capitalisation de méthode. Deux phases **distinctes** :

1. **Distillation** (one-shot, raisonnement) — un livre lu via RAG (LibraryBrain)
   est reformulé en méthode actionnable, stockée ici en JSON. La prose de
   l'auteur n'est jamais recopiée (droit d'auteur) ; **la méthode**, elle, est
   réutilisable.
2. **Application** (récurrent, mécanique) — le JSON seul (sans le livre) sert à
   générer un artefact concret : template, checklist, plan, plan d'attaque...
   Aucun contenu réel n'est inventé : uniquement structure, consignes, et
   placeholders à remplir par l'humain.

## Arborescence

```
skills/distilled/
  README.md
  schema.json                  ← JSON Schema (draft-2020-12)
  <domaine>/                   ← ex: leadership, design, writing, pmo, sales…
    <slug-de-skill>.json       ← un skill = une méthode distillée d'un livre
```

Domaines libres (le nom du dossier suffit). Le slug d'un skill est le nom du
livre ou de la méthode, en kebab-case sans accent.

## Schéma (clés adaptables au contenu réel)

```json
{
  "skill":       "string  — nom court de la méthode (ex: \"Deep Work\")",
  "domain":      "string  — domaine du skill (ex: \"productivity\")",
  "description": "string  — 1 à 2 phrases : à quoi sert ce skill",
  "source": {
    "book":      "string  — titre du livre source",
    "author":    "string  — auteur",
    "year":      "integer — année (optionnel)"
  },
  "principles":  ["string — règle générale, courte, impérative"],
  "workflow": [
    {
      "step":       "string  — nom de l'étape",
      "purpose":    "string  — pourquoi cette étape existe",
      "guidelines": ["string — consigne actionnable pendant l'étape"],
      "pitfalls":   ["string — piège typique à éviter"]
    }
  ],
  "checklist":   ["string — point binaire (fait / pas fait) à vérifier"]
}
```

### Règles dures

- `principles`, `workflow`, `checklist` : reformulés, **jamais** des extraits
  textuels du livre.
- `workflow` est **ordonné** (l'ordre des étapes est porteur de sens — la
  Phase 2 le suit pas à pas).
- Une étape sans `guidelines` n'apporte rien : minimum 1 guideline par step.
- `principles` : ≤ 7 items, sinon le skill perd son tranchant.
- `checklist` : items binaires, vérifiables sans ambiguïté.

### Clés optionnelles

Selon le livre, on peut ajouter :
- `vocabulary`  : `[{"term": "...", "definition": "..."}]` — jargon clé
- `heuristics`  : `["..."]` — règles du pouce, raccourcis de décision
- `antipatterns`: `["..."]` — pratiques à proscrire
- `examples`    : `[{"context": "...", "outcome": "..."}]` — cas génériques,
  sans contenu de l'auteur

Ces clés sont **purement additives** : la Phase 2 les utilise si présentes,
les ignore sinon.

## Cycle de vie

```
                  Phase 1 (distill-book.md)
                  ──────────────────────────
   livre RAG ────▶ proxy :8081 (thinking ON)
                  ──────────────────────────
                          │
                          ▼
              skills/distilled/<domain>/<slug>.json
                          │
                          ▼
                  Phase 2 (apply-skill.md)
                  ──────────────────────────
   skill JSON ──▶ proxy :8081 (thinking OFF)
   + description                   │
   de l'artefact                   ▼
   voulu                       artefact final
```

Voir `prompts/distill-book.md` et `prompts/apply-skill.md`.

## Validation locale

```python
import json, jsonschema
schema = json.load(open("skills/distilled/schema.json"))
data   = json.load(open("skills/distilled/productivity/deep-work.json"))
jsonschema.validate(data, schema)
```

Le test `tests/test_distilled_skills.py` lance cette validation sur tous les
JSON présents dans `skills/distilled/<domain>/*.json`.

## Intégration MCP

Côté serveur MCP `LibraryBrain` (`klody_mcp/server.py`) :

- `list_distilled_skills(domain=None)` — liste les slugs présents (par
  domaine ou tous).
- `get_distilled_skill(slug, domain=None)` — retourne le JSON parsé.

L'outil legacy `get_skills(domain)` n'est pas modifié (il continue de servir
les `skills/<domain>.json` listes de conventions).
