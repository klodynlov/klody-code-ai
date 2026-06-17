# Politique de thinking-budget par requête (mlx-lm)

**Statut : implémenté en FORWARD-COMPAT.** Inspiré du `thinking_budget_tokens`
par requête du node de veille `comfyui-llamacpp-ideogram`, adapté au backend réel
**mlx-lm** (≠ llama.cpp).

## Constat (vérifié)

mlx-lm 0.31.3 **n'expose aucun budget de raisonnement natif** :
- pas de paramètre serveur dédié ;
- le template Qwen3.6 honore `enable_thinking` mais **pas** `thinking_budget`.

Et on **ne peut pas** borner le CoT côté client sans **troncature dure du flux**
(intercepter le stream, injecter `</think>` au dépassement) — option **écartée**
car elle touche le hot-path de streaming et risque de casser le format tool-call.

Le seul levier client restant est `max_tokens`. Or, quand le thinking est actif,
le code fait `max_tokens = max(max_tokens, THINKING_MAX_TOKENS)` : un `max()` ne
sait qu'**élargir** le plafond, jamais le réduire. Comme le défaut (8192) est ≥ à
tous les tiers de budget, **moduler `max_tokens` serait un no-op**. (C'est
exactement le bug qu'une revue adversariale a trouvé sur une 1re version qui
prétendait moduler par là.)

## Décision

Le budget par type de tâche est calculé puis **forwardé** dans
`chat_template_kwargs.thinking_budget` — **FORWARD-COMPAT** :
- **no-op aujourd'hui** (le template Qwen3.6 ignore la clé ; vérifié : l'appel live
  passe sans erreur — une clé inconnue est une variable Jinja inutilisée, pas un 400) ;
- **effectif automatiquement** si un futur template/serveur honore la clé.

Il **ne touche pas** `max_tokens` (pour ne rien prétendre moduler qui ne le serait pas).

### Tiers (`config.THINKING_BUDGET_*`)

| Tier | Tokens | Tâche (via `_thinking_budget`) |
|---|---|---|
| NONE | 0 | thinking OFF : coder, skill interactif, code (edit/refactor/bug_fix/feature), tout non-brain |
| LOW | 512 | `explain` easy |
| MED | 2048 | `explain` medium |
| HIGH | 8192 | difficulté `hard` |

> **NB archi** : les tâches de CODE partent sur le coder *instruct* (sans thinking,
> vérifié : 0 marqueur `enable_thinking` dans son template) → budget 0 par
> construction. Le raisonnement de Klody fire sur le **brain** (`explain`/`hard`).
> L'exemple « budget élevé sur l'édition » du node de veille ne s'applique donc pas
> tel quel : c'est `explain`/`hard` qui portent le budget, pas l'édition de code.

## Pour rendre le budget RÉELLEMENT effectif (chantier futur)

Deux voies, à arbitrer si le besoin de réduire le CoT devient mesurable :
1. **Template** qui honore `thinking_budget` (côté modèle) — le forward est déjà en
   place, rien à changer côté Klody.
2. **Troncature dure du flux** côté client (cap CoT + `</think>` forcé) — précise
   mais invasive (hot-path streaming), à n'engager qu'avec une couverture de tests
   solide sur le format tool-call.

Tant qu'aucune des deux n'est en place, **ne pas affirmer que le budget réduit le
CoT** : il exprime une *politique* (intention par tâche), forwardée, sans effet
comportemental sur le plafond de génération.

## Mesure (déféré)

Un A/B « budget MED vs HIGH » sur les tâches `explain` ne mesurerait **rien**
aujourd'hui (aucun des deux ne change `max_tokens`). Il n'a de sens qu'une fois le
budget rendu effectif (voie 1 ou 2). À ce moment-là : mesurer latence, **tokens de
CoT réels** (à capter via `_delta_reasoning`, aujourd'hui jetés par `stream_chat`),
et qualité (LLM-as-judge en process neutre). Attention au mode d'échec
*recursive-CoT* (cf. mémoire `distilled-mastering-claude-ai` : plus de raisonnement
peut amplifier l'erreur) — une régression de qualité à HIGH serait un résultat
valide, pas un bug.
