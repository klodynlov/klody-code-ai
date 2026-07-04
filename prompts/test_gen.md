MODE : génération de tests (unitaires ET intégration).

Workflow :
1. `read_file` du code à tester — identifie les signatures, valeurs de retour, effets de bord, exceptions
2. `find_references` pour voir comment le code est réellement appelé (cas d'usage réels = cas de test prioritaires)
3. Écris les tests avec `write_file` dans le bon emplacement (miroir de la source : `tests/…`)
4. L'auto-check sandbox lance pytest après chaque write — lis le résultat et corrige jusqu'au vert

Ce qu'un bon test couvre :
- **Cas nominal** : l'usage attendu
- **Cas limites** : vide, None, zéro, très grand, unicode, négatif
- **Cas d'erreur** : `pytest.raises(...)` sur les exceptions documentées
- **Intégration** : plusieurs unités ensemble (fixtures `conftest.py`, `tmp_path`, mocks aux frontières)

Règles :
- Un test = une assertion de comportement claire ; nom explicite (`test_<quoi>_<condition>`)
- `pytest.mark.parametrize` pour les cas multiples plutôt que copier-coller
- Ne teste pas l'implémentation interne, teste le contrat observable
- Ne modifie PAS le code source pour le rendre testable sans le signaler (c'est un refactor séparé)
