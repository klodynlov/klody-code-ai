# B21 — Automatiser une opération observable

**Quand / entrées.** Script bpy, traitement répétitif ou diagnostic ; version de Blender, fichier source, objets visés et sortie attendue.

**Procédure.** Lire l’état avant d’agir : unités, moteur, objets, sélection, mode et dépendances. Préférer les accès aux données pour les changements qui ne nécessitent pas d’opérateur contextuel ; pour un opérateur, préparer explicitement son contexte et vérifier qu’il est disponible. Cibler les objets par identité connue plutôt que modifier toute la scène par défaut. Vérifier noms/types de sockets et propriétés dans la version réelle. Pour une opération répétable, définir si elle met à jour un objet existant ou crée une variante ; éviter les doublons silencieux.

Exécuter un script en arrière-plan avec un code d’erreur Python détectable. Lire le log et vérifier les objets ou fichiers produits, puis rouvrir le résultat dans un autre processus. Une animation demande de comparer l’état évalué à plusieurs frames ; un export demande son réimport et, pour conclure, sa lecture dans la destination.

Exemple de diagnostic sans sauvegarde ni exécution automatique des scripts embarqués :

```bash
"/Applications/Blender.app/Contents/MacOS/Blender" --background --factory-startup --disable-autoexec "/chemin/source.blend" --python-exit-code 1 --python "/chemin/librarybrain-blender/scripts/inspect_scene.py"
```

Adapter les chemins au poste. Le JSON est délimité par `BLENDER_INSPECTION_BEGIN` / `BLENDER_INSPECTION_END` dans stdout. Le script examine la scène active ; il ne calcule pas les meshes évalués, le caractère manifold, les performances de rendu ou les intersections. La source `.blend` n’est pas enregistrée. Les fichiers liés doivent être accessibles pour que l’inspection soit complète.

**Livrable / contrôle.** Opération reproductible ou diagnostic explicite ; échec non nul si le script échoue, résultat relu et limites rapportées.

**Limites.** Les scripts de démarrage et auto-exécution utilisés dans le livre Unity sont des choix de pipeline, pas des prérequis à activer globalement. Ne pas déduire une validation artistique d’un retour 0. Les fonctions précises de bpy demandent un contrôle par version, en particulier les actions et Geometry Nodes.

**Fondements.** Acampora 1951801, 1951804, 1951863, 1951865–1951866 pour les opérateurs/contextes et l’automatisation d’export. Préférences pour les données, contrôle du code de sortie, isolement et réouverture : notre protocole de fiabilisation, appuyé par le manuel officiel CLI 5.1 et testé localement. Le skill existant `blender_python_bpy` est conservé séparément ; ses snippets restent à vérifier selon la version.
