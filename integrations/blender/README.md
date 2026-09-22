# Blender MCP pour Klody

Installation locale vérifiée le 10 septembre 2026 : Blender 5.1.2 ; Blender Lab MCP v1.0.0 ; SDK MCP 1.30.0 ; 17 outils conservés dans `tools.json`.

```
Klody API → http://127.0.0.1:8095/mcp → 127.0.0.1:9876 → Blender ouvert
```

La source est le dépôt officiel https://projects.blender.org/lab/blender_mcp, tag v1.0.0, commit `03004fd0216bfe5e0a3d9ac9b47d5efadc3d78c4`. Le nom du package est `blender-mcp`, mais l’installation utilise cette source officielle et non un package homonyme choisi au hasard sur PyPI. Les empreintes sont enregistrées dans `installation.json`. Le code amont est GPL-3.0-or-later ; les notices figurent dans chaque fichier de l’extension.

L’extension installée se nomme **Klody MCP (Blender Lab)**, module `bl_ext.user_default.klody_blender_mcp`, dans le dépôt local User Default de Blender 5.1. Le patch `local-loopback.patch` permet les communications locales même lorsque l’accès Internet de Blender est désactivé, mais refuse un hôte autre que `127.0.0.1`. Cet accès Internet global est resté désactivé. Les réglages de l’extension sont sauvegardés avec son démarrage automatique.

Le serveur HTTP conserve la protection contre DNS rebinding du SDK. Il utilise un environnement dédié `~/.klody/venvs/blender-mcp`, hors du dépôt scanné par Klody. `requirements.lock` fixe les dépendances ; MCP 2.x est incompatible avec l’API FastMCP employée par cette version amont. Le script `scripts/start-blender-mcp.sh` est lancé par `~/Library/LaunchAgents/com.klody.blender-mcp.plist`, chargé dans la session utilisateur. Log : `~/Library/Logs/klody-blender-mcp.log`.

La clé `blender` a été ajoutée au JSON `KLODY_MCP_SERVERS` du `.env` existant. Les 11 autres entrées sont conservées ; une sauvegarde locale protégée de ce fichier a été faite avant modification. L’API Klody inactive a été relancée pour lire la configuration.

## Vérification et utilisation

Le client réel `tools.mcp_bridge.MCPManager` de Klody découvre les 17 outils. Il a lu la scène, déplacé temporairement le cube de 0,25 m, puis restauré position, sélection, frame et interface. Le déplacement et le retour ont été contrôlés visuellement. Les preuves sont dans `LibraryBrainSkills/reports/blender/mcp-*.json`.

Commencer par `mcp__blender__get_objects_summary`. Utiliser `mcp__blender__execute_blender_code` pour la tâche demandée en affectant un dictionnaire à `result`. Ce tool dispose de l’environnement Python de Blender ; ce n’est pas un mécanisme de confinement du code. Le skill `librarybrain_blender` décrit inspection, modification ciblée et relecture. Le script de diagnostic `inspect_scene.py` reste disponible pour les fichiers hors interface.

Les appels locaux expirent après 45 s ; Klody après 60 s. Une expiration ne signifie pas que le travail exécuté a été annulé. Inspecter avant de relancer une mutation. Les outils CLI amont et les captures renvoyant de volumineux blocs d’image sont écartés de cette surface ; les outils de rendu vers un chemin local sont disponibles.

Si Blender est fermé, la découverte HTTP peut réussir mais les outils de scène échouent explicitement. Ouvrir Blender et vérifier l’extension dans les préférences. Ne pas changer globalement ses permissions Internet ni réinitialiser la scène pour réparer la connexion.

Pour reproduire l’installation Python : créer l’environnement dédié avec Python 3.11, puis installer ce `requirements.lock` avec uv. Pour l’extension, installer `klody-blender-mcp-1.0.0.zip` depuis le disque. Les chemins du plist et des scripts doivent être adaptés sur un autre poste. L’archive amont non modifiée est aussi conservée séparément.

## Import des bibliothèques d’assets — 13 septembre 2026

Deux outils locaux complètent les 17 outils amont (19 au total) :
`list_asset_library_assets` puis `import_asset_collection`. Ils résolvent le chemin
via les préférences Blender, listent les collections marquées comme assets et
importent uniquement la collection choisie. Le code utilise l’API de données Blender,
sans changer d’éditeur, ouvrir un autre fichier ni sauvegarder la scène actuelle.
`scene_name` cible une scène existante ; les répétitions réutilisent l’import par défaut.

Implémentation : `asset_library_tools.py` (MCP) et `asset_library_runtime.py` (bpy).
Validation : unittest `test_asset_tools.py` dans le venv Blender MCP ;
`Blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python integrations/blender/verify_asset_runtime.py`.
Ce dernier test crée ses propres assets temporaires : il ne touche pas à Blender ouvert.

Les mots Z-Anatomy et Human Generator chargent désormais le serveur Blender même
sans le mot « Blender ». Une consigne ciblée rappelle à brain/coder que les assets
s’importent par script. Après mise à jour, relancer le service blender-mcp et
l’API Klody inactive pour vider son cache de découverte MCP.
