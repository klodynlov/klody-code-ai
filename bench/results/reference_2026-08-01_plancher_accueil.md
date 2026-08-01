# Plancher de latence d'un accueil de session — 2026-08-01

Configuration des deux runs, identique : `BACKEND=mlx`, gateway `http://localhost:8090/v1`,
alias `brain` → **`unsloth/Qwen3.6-35B-A3B-MLX-8bit`** (résolu par la réponse),
69 outils dans `tools.registry.TOOLS`, M5 Max 128 Go.

Recalcul :

```bash
python scripts/mesure_plancher_accueil.py --passes 3
python scripts/mesure_plancher_accueil.py --balayage --passes 3
```

⚠️ **Lecture intra-run uniquement.** Ces latences ne se comparent pas à celles
d'un autre jour — mesuré le 2026-07-30, la même tâche du banc a rendu 34 s puis
119 s sans qu'aucune variable ne change. Ce qui est comparable ici, c'est un
bras contre un autre **dans le même run**.

---

## Run 1 — trois bras : combien coûte une phrase d'accueil ?

| bras | 1ᵉʳ appel | à chaud |
|---|---|---|
| `plancher` (`max_tokens=1`, sans outils) | **6,52 s** | 0,13 s |
| `accueil` (micro-prompt, ~60 tokens) | 0,46 s | **0,37 s** |
| `avec_outils` (idem + 69 schémas) | 4,06 s | 0,37 s |

**Conclusion : ce n'est pas le coût qui interdit un accueil synchrone, c'est la
VARIANCE.** 0,37 s serait invisible ; 6,52 s serait un gel du prompt au
lancement. Le même appel donne l'un ou l'autre selon que le gateway est
réveillé, et rien ne permet de le savoir à l'avance.

Les 6,52 s sont le réveil du gateway, payées par le bras qui passait en tête —
`accueil` a rendu 0,46 s juste après, modèle déjà chaud. C'est un **plancher**
du cas froid : un vrai chargement des 44 Go de `brain` coûterait davantage
(voisin documenté : `coder`, 30 Go, chargé en 8,3 s).

→ A décidé la forme de `agent/greeting.py` : thread démon + attente bornée +
repli local muet.

---

## Run 2 — balayage : les schémas d'outils coûtent-ils leur prefill ?

Le run 1 ne pouvait pas répondre : `avec_outils` a rendu 4,06 s une seule fois
puis 0,37 s, et trois bras qui se suivent confondent le réveil du gateway avec
le coût des schémas. Le balayage envoie le **même prompt** avec un nombre
d'outils différent — deux tailles différentes = deux préfixes différents = cache
de prompt froid garanti, sans parier sur la façon dont le gateway sérialise les
outils.

| palier | tokens¹ | médiane | min | max | Δ vs palier 0 | µs/token |
|---|---|---|---|---|---|---|
| 0 | 0 | 0,47 s | 0,41 | 0,57 | — | — |
| 17 | 2 883 | 1,23 s | 1,19 | 1,34 | 0,76 s | 264 |
| 34 | 5 573 | 2,04 s | 1,93 | 2,24 | 1,57 s | 282 |
| 69 | 12 291 | **4,58 s** | 4,18 | 4,73 | 4,11 s | 334 |

¹ compte **heuristique** (`agent.tokens.count_tokens` a signalé
`tokenizer_is_exact() = False` sur les deux machines) : la colonne `tokens` est
indicative. Les latences, elles, sont mesurées. Le **nombre d'outils** est exact,
et la monotonie s'y lit directement.

### Ce que ça établit

**Le prefill des schémas se paie, et il se paie cher.** La colonne monte de
façon monotone sur les trois passes : doubler le nombre d'outils double
grossièrement la latence. À 69 outils, **~4,1 s de prefill** avant que le modèle
ne produise quoi que ce soit.

Ça **explique rétroactivement les deux chiffres du run 1** : 4,06 s = le prefill
non caché des 69 schémas ; 0,37 s = le même appel une fois le préfixe en cache.
Les deux mesures se rejoignent, elles ne se contredisaient pas.

Le débit de prefill **décroît d'environ 20 % à mesure que le contexte grandit**
(3 790 → 3 550 → 2 990 tokens/s), ce qui est cohérent avec un coût d'attention
super-linéaire. Ordre de grandeur seulement : le compte de tokens est
heuristique.

### Ce que ça ne dit pas

- Les Δ sont calculés contre le palier 0, **qui est caché** (il n'a rien à faire
  varier, il rejoue le même préfixe à chaque passe). Il minore donc le coût de
  génération non caché, et les Δ **surestiment légèrement** le prefill.
- Rien ici ne mesure une **bascule de modèle** `brain` ↔ `coder`. Le routeur en
  fait à chaque tâche, et un modèle différent implique un cache de préfixe
  différent — donc, en principe, ce prefill re-payé. **Hypothèse non mesurée**,
  candidate au prochain balayage.

### Conséquences

1. **L'accueil généré ne doit pas passer par `orchestrator.run()`** — argument
   désormais chiffré : ~150 tokens contre ~12,3 k, soit ~0,4 s contre ~4,6 s à
   cache froid. `agent/greeting.py` n'envoie aucun schéma.
2. **Le premier tour de chaque session paie ce prefill**, accueil ou pas. Ce
   n'est plus une question d'accueil : c'est une taxe de démarrage de l'agent
   lui-même, et tout ce qui invalide le préfixe (outil ajouté, schéma modifié,
   serveur MCP qui apparaît, bascule de modèle) la fait re-payer.
