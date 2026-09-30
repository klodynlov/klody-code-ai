# Contexte de requête dans le tour user — mesures (2026-09-27 → 28)

Changement mesuré : `Orchestrator._inject_system_prompt` ne met plus dans le
prompt système le retrieval proactif ni les skills how-to (sélectionnés par
requête). Ils sont ancrés sur le message user courant
(`ConversationMemory.ancrer_contexte_tour`, clé privée `_contexte_tour`, jamais
persistée). Les skills permanents (`utilisateur_*`, `conventions_*`) restent
dans le système.

## 1. Pourquoi le système doit être identique octet pour octet

mlx_lm 0.31.3, `LRUPromptCache.fetch_nearest_cache` : le cache du MoE
(`ArraysCache`) n'est pas rognable, donc seule une entrée dont les tokens sont
un PRÉFIXE EXACT du nouveau prompt est réutilisable (branche `shorter`). Quand
le dernier message est `user`, le serveur pose un point de reprise à la fin du
bloc système (`_tokenize`, segment `system`) ; le template Qwen3.6 y écrit les
schémas d'outils PUIS le texte du système. Rendu vérifié avec le tokenizer du
modèle :

```
<|im_start|>system
# Tools … (schémas)
SYSTEME<|im_end|>
<|im_start|>user
…
```

## 2. Stabilité du prompt système sur des messages réels consécutifs

`python scripts/mesure_stabilite_prompt.py [--routeur]` — construit le système
avec le VRAI `_inject_system_prompt`, écarte les sessions de tests et du banc
(classifieur de `scripts/etat_pollue.py`).

| corpus | paires | système identique AVANT | APRÈS |
|---|---|---|---|
| 2026-09-28, `~/.klody/data` assaini (251 sessions), `task_type` fixe | 386 | 22 % | **95 %** |
| idem, routeur réel rejoué, paires brain→brain | 245 | 17 % | **68 %** |
| 2026-09-27, dossier encore pollué (tests + banc), `task_type` fixe | 263 | 8 % | 86 % |
| idem, routeur réel rejoué | 61 | 0 % | 82 % |

Avec le routeur réel, le profil est identique sur 96 % des paires : l'écart
restant vient du prompt de tâche (`compose_system_prompt(task_type)`,
explain ↔ creative surtout : 130 messages `creative` sur 636 classés), laissé
dans le système délibérément. Borne basse : mlx_lm garde 10 entrées et évince
les points de reprise « system » en dernier.

## 3. Deux messages consécutifs d'une session, chemin de l'API, vrai gateway

`python scripts/mesure_cache_deux_messages.py` — un Orchestrator par message,
`stream_chat` = `api.streaming.make_stream_api`, deux questions qui
sélectionnent des skills how-to différentes. Prompt ≈ 92 k tokens (383 outils
avec les serveurs MCP du `.env`, ~70 k tokens de schémas, compte heuristique).

| run | message | système | cached | durée |
|---|---|---|---|---|
| main 83d8c8a (2026-09-28) | 1 | — | 0 | 219,0 s |
| | 2 | DIFFÉRENT | **0** | 134,3 s |
| branche (2026-09-28) | 1 | — | 0 | 213,3 s |
| | 2 | identique | **85 990 / 92 326 (93 %)** | **35,9 s** |
| main 7a31b71 (2026-09-27) | 2 | DIFFÉRENT | 0 | 154,9 s |
| branche (2026-09-27) | 2 | identique | 85 975 / 92 313 | 32,7 s |

Le juge est `cached`, rendu par mlx_lm. Les durées ne se comparent qu'à
l'intérieur d'un run : 213,3 s → 35,9 s entre les deux messages de la branche.

## 4. Banc complet — non-régression des verdicts

`python -m bench.run --repeat 3 --label ctx_user` puis `python -m bench.gate`,
branche rebasée sur main 83d8c8a. JSON :
`reference_2026-09-28_contexte_tour_banc.json`.

- **103/105**, porte **verte** : baseline 99,0 % → 98,1 %, Δ −1,0 pt (seuil 7,5).
- En baisse sous le seuil : `discovery/config_precedence` 3/3 → 2/3 — elle est
  aussi à 2/3 sur main le même jour (`reference_2026-09-28_discovery_main.json`),
  pas imputable ; `real_repo/null_byte_sanitize` 3/3 → 2/3 — voir §5.
- Échec de `null_byte_sanitize` (passe 3) : l'agent lit `docs/ARCHITECTURE.md`
  mais pas `docs/SECURITY.md`, qui porte la contrainte. Le retrieval avait
  nommé `docs/SECURITY.md` dans le tour (log 14:50:50). Même variable
  médiatrice que les jumeaux discovery (ouverture du document) ; le garde
  « décisions jamais ouvertes » est satisfait par la lecture d'ARCHITECTURE.md.
  Passes 1 et 2 : SECURITY.md lu, ✅.

### Cache observé pendant ce banc (lignes `[cache]` de `agent/llm.py`)

| | |
|---|---|
| appels LLM | 439 (coder 412, brain 27) |
| appels froids (< 50 % en cache) | **12** (6 coder, 6 brain) |
| part en cache des autres, médiane | 99,7 % |
| prompt médian | 82 389 tokens |
| durée médiane froid / chaud | 72,3 s / 2,1 s |

En mode coder le système se réduit au prompt slim, constant : son point de
reprise sert d'une tâche à l'autre. Avant, retrieval et skills y variaient par
tâche.

## 5. Contrôle apparié `real_repo` × 5 passes, main vs branche

Suite directe du §4 : le palier `real_repo` est celui où la contrainte vit dans
un document que le retrieval nomme — le plus exposé au déplacement du
retrieval vers le tour. `python -m bench.run --category real_repo --repeat 5`,
main 83d8c8a puis branche, dans la foulée. Le bras main portait la SEULE
instrumentation `[cache]` de `agent/llm.py` (patch local non commité, aucun
effet sur le prompt), pour lire le cache des deux côtés. JSON :
`reference_2026-09-28_contexte_tour_rr_main.json`,
`reference_2026-09-28_contexte_tour_rr_branche.json`.

| tâche | main | branche |
|---|---|---|
| `batch_atomic_delete` | 3/5 | 5/5 |
| `create_submodule_readme` | 5/5 | 5/5 |
| `fix_from_known_issue` | 5/5 | 5/5 |
| `null_byte_sanitize` | 5/5 (SECURITY.md lu 5/5) | 5/5 (SECURITY.md lu 5/5) |
| `test_with_fixture` | 5/5 | 5/5 |
| **total** | **23/25** | **25/25** |

L'échec de `null_byte_sanitize` du §4 ne se reproduit pas : 7/8 cumulé sur la
branche, 8/8 sur main (baseline comprise) — indiscernable. Les deux échecs de
main (`pytest KO: 2 failed`) sont du même ordre de bruit, dans l'autre sens.

| cache pendant le run | main | branche |
|---|---|---|
| appels LLM | 125 | 138 |
| appels froids (< 50 % en cache) | 12 | 8 |
| part des tokens de prompt servie par le cache | 89,9 % | **93,4 %** |

⚠️ Le banc rejoue des tâches IDENTIQUES d'une passe à l'autre : sur main aussi,
retrieval et skills y sont les mêmes d'une passe à la suivante, et le point de
reprise de la passe précédente resservait. Ce n'est donc pas l'instrument du
scénario visé (deux messages DIFFÉRENTS d'une même session) — c'est le §3. Les
durées cumulées (33,3 min contre 16,7 min) viennent de deux runs successifs et
ne sont pas revendiquées.

## Ce que ces mesures n'établissent pas

- Aucun gain de latence entre runs : les durées ne se comparent qu'intra-run.
- Le taux d'ouverture spontanée des documents n'est pas mesuré ici au-delà
  de `null_byte_sanitize` : le retrieval dans le tour ne le fait pas monter
  (négatif déjà établi, `reference_2026-07-30_piste_donnee_jamais_suivie.json`).
