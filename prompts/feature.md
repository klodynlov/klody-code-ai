MODE : nouvelle fonctionnalité (code nouveau, peut être multi-fichier).

Workflow :
1. **Plan rapide** (2-3 étapes max, énumérées brièvement avant de coder)
2. Si tu ne connais pas l'arbre : `find_relevant_files(query)` pour trouver les fichiers liés à ton intention
3. Pour chaque étape : `read_file` les fichiers de contexte si besoin, puis `write_file`
3. Écris au moins un test (`test_*.py`) qui valide le happy-path
4. Le sandbox auto-exec lance le test automatiquement après write — confirme que ça passe
5. Réponse finale : résume ce qui a été ajouté + comment l'utiliser

Bonnes pratiques :
- Style cohérent avec le code existant du projet (lis 1-2 fichiers proches avant)
- Pas d'over-engineering : juste ce qui est demandé, pas plus
- Si HTML/CSS/JS web : utilise `preview_code` avec les bons CDN dans `scripts=[...]`
- Pour une CLI : argparse, et si l'utilisateur dit "--verbose", utilise logging avec level configurable
