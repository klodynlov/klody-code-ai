# Lot 1.5 — A/B Best-of-N : le banc ne mesure rien

Date : 2026-09-03.

## Constat

Best-of-N ne FIRE sur aucune tâche du bench (hard + expert).

### Preuve par le code

`_should_run_best_of_n` (orchestrator.py:1686) retourne `False` quand
`_code_model_active=True`. `_route_model` active le coder pour tout
`task_type in _CODE_TASK_TYPES` quand `CODE_MODEL` est configuré.

```
_CODE_TASK_TYPES = {edit, refactor, bug_fix, feature, self_dev, test_gen, perf, migrate}
```

Les 10 tâches hard + expert du bench sont TOUTES des tâches de code :

| tâche | task_type probable |
|---|---|
| hard/fix_async_bug | bug_fix |
| hard/optimize_n_squared | perf |
| hard/migrate_sync_to_async | migrate |
| hard/api_endpoint_full | feature |
| hard/debug_test_suite | bug_fix |
| expert/unhashable_dedup | bug_fix |
| expert/deadlock_lock_order | bug_fix |
| expert/cross_module_rename | refactor |
| expert/spec_beyond_tests | feature |
| expert/stream_memory | perf |

→ Toutes routées vers le coder → BoN systématiquement sauté.

### Preuve par les traces

Résolution exhaustive (script inline) — sur les 22 combinaisons
`difficulty × task_type` qui déclenchent `use_best_of_n=True` :

| fire | skipped (coder) |
|---|---|
| hard/explain | hard/edit |
| hard/review | hard/refactor |
| hard/security | hard/bug_fix |
| hard/docs | hard/feature |
| hard/creative | hard/self_dev |
| hard/music | hard/test_gen |
| | hard/perf |
| | hard/migrate |
| | easy/self_dev |
| | medium/self_dev |

**6 FIRE / 10 SKIPPED** — mais les 6 qui fire (explain, review, security,
docs, creative, music) n'ont AUCUNE tâche dans le bench.

### Preuve par un run

`hard/fix_async_bug` avec `BEST_OF_N_COUNT=3` :
- Panel affiche « best-of-N » (décision du router)
- 0 trace de `🎲 Best-of-3` (jamais exécuté)
- success=True, iters=4, tools=4, latency=41.1 s
- Même tâche avec `BEST_OF_N_ENABLED=false` : ReadTimeout (bruit
  environnemental, coder en cours de chargement)

## Décision

**`BEST_OF_N_ENABLED` mis à `false` par défaut.**

Raisonnement (plan § lot 1.5) :
1. **Verdicts** : identiques entre les 3 configs (BoN ne fire sur aucune
   tâche mesurable)
2. **À verdicts égaux, la moins chère** : BoN=off
3. **BoN fire encore** sur hard/explain, hard/review, etc. — mais ces
   types ne sont pas mesurés par le bench, donc le coût est avéré (N+1
   appels) sans preuve de gain. Si un palier de bench les couvre un jour
   (lot 2.1 real_repo), réactiver et mesurer.

`BEST_OF_N_COUNT` reste à 3 pour quand il sera réactivé sélectivement.
