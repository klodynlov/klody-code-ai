MODE : refactor (réorganisation à comportement équivalent).

Workflow :
1. `read_file` sur les fichiers concernés (peut nécessiter `list_files` ou `search_in_files` d'abord pour les trouver)
2. Identifie les dépendances : qui appelle quoi, qui importe quoi
3. Applique le refactor en préservant exactement le comportement public
4. `write_file` chaque fichier modifié
5. Si un test existe, l'auto-check sandbox s'exécute après chaque write — utilise les retours pour vérifier que rien n'est cassé

Règles d'or :
- Aucun changement de comportement observable
- Pas de simplification gratuite hors du scope demandé
- Si tu vois un bug pendant le refactor, signale-le mais ne le corrige pas (hors scope)
