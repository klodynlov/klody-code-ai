# Sources, portée et adaptations

Ce complément utilise **25 passages** de Paolo Acampora, *3D Game Development with Blender 5 and Unity 6*, mars 2026, livre LibraryBrain **26489**, 306 pages PDF. Les exemples de l’auteur utilisent Blender 5.1 et Unity 6.3 ; le poste testé utilise Unity 6000.6.0f1. Les identifiants, pages indexées et SHA-256 sont dans [provenance.json](provenance.json). Le champ auteur absent du catalogue est complété ici depuis le frontispice déjà vérifié pour le skill Blender, sans modifier la base.

La recherche du titre `unity` contient des faux positifs (*community*, *immunity*). Une recherche FTS `Unity AND (prefab OR profiler OR GameObject OR URP OR "Build Settings")` a trouvé l’ouvrage principal et deux mentions dans un livre C++, insuffisantes pour fonder une méthode Unity supplémentaire. L’inventaire est conservé dans `LibraryBrainSkills/reports/unity`. La portée est le passage Blender → Unity et le prototype interactif ; ce n’est pas une revue complète du moteur.

Vérifications primaires consultées le 10 septembre 2026 :

- [Formats propriétaires et recommandation FBX, Unity 6.6](https://docs.unity3d.com/6000.6/Documentation/Manual/HOWTO-ImportObjectsFrom3DApps.html).
- [Introduction URP](https://docs.unity3d.com/6000.6/Documentation/Manual/urp/urp-introduction.html) et [shader Lit](https://docs.unity3d.com/6000.6/Documentation/Manual/urp/lit-shader.html).
- [Prefabs](https://docs.unity3d.com/6000.6/Documentation/Manual/Prefabs.html) et [réglages Rig](https://docs.unity3d.com/6000.6/Documentation/Manual/FBXImporter-Rig.html).
- [Profiler](https://docs.unity3d.com/6000.6/Documentation/Manual/Profiler.html) et [ligne de commande](https://docs.unity3d.com/6000.6/Documentation/Manual/CommandLineArguments.html).

Les critères de preuve, la conservation des `.meta`, la tranche de prototype, le contrat d’import et la réouverture du build sont notre synthèse d’ingénierie. U06 et U07 sont fondées sur le manuel et le protocole local, sans leur inventer une provenance livresque. Le vocabulaire « testé » désigne uniquement ce qui est enregistré dans les preuves de Signal ; aucune efficacité générale du skill n’a été évaluée.

Prototype original : `~/Projets/BlenderUnityPrototype`. Son dossier `Evidence` contient export Blender, import Unity, résultat du build et états des commandes du player. La source ne reprend pas les modèles du livre.
