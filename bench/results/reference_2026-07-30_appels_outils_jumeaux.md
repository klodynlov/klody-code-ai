# Appels d'outils par instance — lot apparié tracé du 2026-07-30

`bench.run --category discovery --repeat 5`, PYTHONUNBUFFERED=1 pour que les
en-têtes du parent s'intercalent correctement avec la sortie des sous-processus.

Extrait de la sortie brute ; seuls les deux jumeaux sont repris.

passe 1  hidden_invariant     ❌   44.5s  docs/ —— NON LU
           1. read_file 'cache.py'
           2. read_file 'test_cache.py'
           3. list_files '.'
           4. write_file 'cache.py'
           5. run_in_sandbox 'cd /private/tmp/kb-h5_azs2o && pyt  reason="Vérifier

passe 1  first_write_method   ✅   60.6s  docs/ LU
           1. read_file 'store.py'
           2. read_file 'test_store.py'
           3. read_file 'docs/DECISIONS.md'
           4. write_file 'store.py'
           5. run_in_sandbox 'python -m pytest test_store.py -v'

passe 2  hidden_invariant     ❌   47.1s  docs/ —— NON LU
           1. list_files '.'
           2. read_file 'cache.py'
           3. read_file 'test_cache.py'
           4. write_file 'cache.py'
           5. run_in_sandbox 'pytest test_cache.py -v'  reason="Vérifier que tous

passe 2  first_write_method   ❌   50.0s  docs/ —— NON LU
           1. list_files '.'
           2. read_file 'store.py'
           3. read_file 'test_store.py'
           4. write_file 'store.py'
           5. run_in_sandbox 'python -m pytest test_store.py -v'

passe 3  hidden_invariant     ❌   50.2s  docs/ —— NON LU
           1. list_files '.'
           2. read_file 'cache.py'
           3. read_file 'test_cache.py'
           4. write_file 'cache.py'
           5. run_in_sandbox 'cd /private/tmp/kb-x48zw9r0 && pyt  reason="Vérifier

passe 3  first_write_method   ✅   49.6s  docs/ LU
           1. read_file 'store.py'
           2. read_file 'test_store.py'
           3. read_file 'docs/DECISIONS.md'
           4. write_file 'store.py'
           5. run_in_sandbox 'pytest test_store.py -v'

passe 4  hidden_invariant     ❌   43.0s  docs/ —— NON LU
           1. list_files '.'
           2. read_file 'cache.py'
           3. read_file 'test_cache.py'
           4. write_file 'cache.py'
           5. run_in_sandbox 'pytest test_cache.py -v'  reason="Vérifier que tous

passe 4  first_write_method   ❌   40.2s  docs/ —— NON LU
           1. list_files '.'
           2. read_file 'store.py'
           3. read_file 'test_store.py'
           4. write_file 'store.py'
           5. run_in_sandbox 'python -m pytest test_store.py -v'

passe 5  hidden_invariant     ❌   39.3s  docs/ —— NON LU
           1. list_files '.'
           2. read_file 'cache.py'
           3. read_file 'test_cache.py'
           4. write_file 'cache.py'
           5. run_in_sandbox 'pytest test_cache.py -v'  reason="Vérifier que tous

passe 5  first_write_method   ✅   41.1s  docs/ LU
           1. list_files '.'
           2. read_file 'store.py'
           3. read_file 'test_store.py'
           4. read_file 'docs/DECISIONS.md'
           5. write_file 'store.py'
           6. run_in_sandbox 'pytest test_store.py -v'

=== SYNTHÈSE : lecture de docs/ × verdict ===

discovery/hidden_invariant  (n=5)
  docs/ lu    → ✅ 0   ❌ 0
  docs/ non lu→ ✅ 0   ❌ 5

discovery/first_write_method  (n=5)
  docs/ lu    → ✅ 3   ❌ 0
  docs/ non lu→ ✅ 0   ❌ 2
