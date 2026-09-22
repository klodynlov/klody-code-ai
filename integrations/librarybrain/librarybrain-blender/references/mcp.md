# Contrôler Blender depuis Klody

Configuration vérifiée le 10 septembre 2026 : Blender 5.1.2, pont Blender Lab v1.0.0 adapté localement, 17 outils. Le serveur de Klody est `http://127.0.0.1:8095/mcp`, relié à l’extension sur `127.0.0.1:9876`. L’accès Internet global de Blender reste désactivé. Blender doit être ouvert ; l’extension et le service Klody sont configurés pour démarrer automatiquement.

1. Découvrir les outils réellement disponibles ; commencer par `mcp__blender__get_objects_summary`, puis `mcp__blender__get_object_detail_summary` pour l’objet visé. Les noms ci-dessous ne prouvent pas qu’un serveur est actif dans une autre session.
2. Relever fichier, sélection, mode, version, transformations et dépendances avant de modifier. Les outils `get_blendfile_summary_path_info`, `get_blendfile_summary_missing_files` et `get_blendfile_summary_of_linked_libraries` complètent ce diagnostic.
3. Consulter `mcp__blender__get_python_api_docs`, `search_api_docs` ou `search_manual_docs` avec leurs schémas découverts pour une API incertaine. La documentation embarquée peut différer de la version exacte du binaire ; l’introspection `bpy` tranche.
4. Pour la tâche autorisée, appeler `mcp__blender__execute_blender_code` avec `code`. Le code a accès à `bpy` ; affecter un dictionnaire JSON à `result`. Il s’agit d’une exécution Python complète, pas d’un bac à sable de sécurité. Cibler les objets nommés et conserver le travail existant.
5. Relire l’état dans un autre appel et montrer un aperçu pertinent. `render_thumbnail_to_path` et `render_viewport_to_path` produisent des fichiers locaux. `get_screenshot_of_window_as_json` décrit l’interface ; ce n’est pas une image de contrôle visuel.

Exemple de premier appel Python sans mutation :

```python
import bpy
result = {"version": bpy.app.version_string, "file": bpy.data.filepath,
          "objects": [{"name": o.name, "type": o.type} for o in bpy.context.scene.objects]}
```

Vérifier `status`, `message`, `stdout` et le contenu de `result`, y compris les erreurs imbriquées des outils. Le délai socket local vaut 45 secondes et celui de Klody 60 secondes. Une expiration n’annule pas une opération déjà lancée : inspecter avant toute nouvelle tentative. Réduire un rendu de contrôle ; utiliser une tâche d’arrière-plan suivie pour un rendu long.

Installation documentée dans `klody-code-ai/integrations/blender/README.md`. Le pont utilise la source officielle [Blender Lab MCP](https://www.blender.org/lab/mcp-server/), commit `03004fd0216bfe5e0a3d9ac9b47d5efadc3d78c4`, et le SDK MCP 1.30.0. L’extension modifiée porte l’identifiant `bl_ext.user_default.klody_blender_mcp` et refuse les hôtes autres que `127.0.0.1`. Les adaptations sont conservées dans `local-loopback.patch` ; ne pas les attribuer au logiciel officiel.
