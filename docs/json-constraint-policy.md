# Politique de contrainte JSON (sorties structurées)

**Statut : normatif.** S'applique à toute sortie LLM dont on attend du JSON
structuré (tool-calls, classif du router, extraction, captions, etc.).

## Anti-pattern à NE PAS reproduire

Le node de veille `iChristGit/comfyui-llamacpp-ideogram` fiabilise sa sortie JSON
ainsi :

1. génère du texte libre ;
2. **strip** post-hoc des blocs `<think>...</think>` ;
3. **espère** que le reste soit du JSON valide.

Aucune validation de schéma, aucun retry. C'est **« strip + espoir »** : fragile
par construction. Dès que le modèle ajoute une phrase de courtoisie, une virgule
finale, un commentaire ou tronque sa sortie, le parse casse silencieusement et la
donnée structurée est perdue. **Interdit dans Klody.**

## Règle

La contrainte JSON se fait par **l'une** de ces deux voies, jamais autrement :

| Backend | Mécanisme imposé |
|---|---|
| **llama.cpp** | Grammaire **GBNF** (`grammar`/`json_schema`) — le décodage ne peut PAS produire de token hors-grammaire. La validité est garantie *à la génération*. |
| **mlx-lm** (backend Klody) | **Pydantic + retry sur parse échoué.** On valide la sortie contre un modèle Pydantic (ou un `json.loads` + schéma) ; en cas d'échec, on **relance** avec un message de correction, borné à N essais, puis fallback safe explicite. |

> mlx-lm n'a pas d'équivalent GBNF natif fiable à ce jour. La contrainte vit donc
> **côté client** : valider, et **réessayer** sur échec — pas espérer.

## Le strip `<think>` : nettoyage, jamais garantie

Retirer le bloc de raisonnement reste **utile**, mais **uniquement** comme étape
de nettoyage en amont du parse — **jamais** comme moyen d'obtenir du JSON valide.

- Klody capte déjà le CoT séparément (`delta.reasoning` → `reasoning_buf` dans
  [`agent/llm.py`](../agent/llm.py)) et **ne le réinjecte jamais** dans le content
  ni l'historique : le `<think>` n'a donc même pas à être « strippé » du JSON, il
  n'y est pas. C'est la bonne posture.
- Si un modèle émet malgré tout `<think>` *inline*, on peut le nettoyer — mais la
  **garantie** de validité reste Pydantic + retry, pas le nettoyage.

## Application dans Klody (état + cible)

- **Tool-calls** — `agent/llm.py` parse les `tool_calls` natifs OpenAI, avec des
  fallbacks tolérants (JSON, XML `<function=…>`, format compact). Les arguments
  sont re-sérialisés en JSON validé (`json.dumps`/`json.loads`) avant exécution.
  Tout argument injoignable au parse est rejeté, jamais « espéré ».
- **Router** — [`agent/router.py`](../agent/router.py) tente le parse, et sur échec
  retourne un **fallback safe explicite** (`medium`/`explain`). Trajectoire cible :
  remplacer le parse manuel par un modèle Pydantic + un retry borné avant le
  fallback, pour aligner pleinement la classif sur cette politique.
- **Toute nouvelle sortie structurée** doit suivre la règle Pydantic + retry dès
  l'écriture — pas après un bug de parse en prod.

## Checklist de revue

- [ ] La sortie est-elle validée contre un schéma (Pydantic / json_schema) ?
- [ ] Un échec de parse déclenche-t-il un **retry borné**, puis un fallback explicite ?
- [ ] Le `<think>`/CoT est-il traité comme nettoyage, **séparé** du content, jamais
      comme garantie de validité ?
- [ ] Aucun chemin ne fait « parse direct + on croise les doigts » ?
