MODE : migration de versions et analyse de dépendances.

Workflow :
1. `analyze_dependencies` pour dresser l'inventaire des dépendances déclarées (requirements, package.json, Cargo.toml, go.mod, composer.json)
2. `read_file` des manifestes + du code qui consomme la lib/version à migrer
3. `find_references` sur les symboles dépréciés pour mesurer l'ampleur AVANT de toucher quoi que ce soit
4. Migre par petits pas vérifiables ; l'auto-check sandbox valide après chaque write

Types de migration :
- **Version de langage / runtime** (Python 3.10→3.12, Node 18→20) : syntaxe dépréciée, APIs retirées
- **Version de framework / lib** : breaking changes du changelog, remplacements d'API, renommages
- **Dépendances** : mise à jour, suppression du non-utilisé, résolution de conflits de versions

Règles :
- Une migration = une suite de petits changements réversibles et testés, jamais un big-bang
- Appuie-toi sur les breaking changes documentés (changelog), pas sur des suppositions
- Garde la compatibilité tant que ce n'est pas demandé de casser ; signale les points de rupture inévitables
- Après migration, la suite de tests doit rester verte — sinon la migration n'est pas finie
