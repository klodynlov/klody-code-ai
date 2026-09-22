# Piloter Unity depuis Klody

Installation vérifiée le 10 septembre 2026 : Unity 6000.6.0f1, MCP for Unity
10.2.0, commit `30d22075093d1d35dfb0091c1c7550e9ad948577`.
Il s’agit du connecteur libre CoplayDev, distinct du service Unity AI.

- Projet équipé : `~/Projets/BlenderUnityPrototype`.
- Serveur HTTP local : `http://127.0.0.1:8097/mcp`.
- Service macOS : `com.klody.unity-mcp`.
- Script : `~/Projets/klody-code-ai/scripts/start-unity-mcp.sh`.
- Package Editor : `file:~/.klody/vendor/unity-mcp-10.2.0/MCPForUnity`.
- Python dédié : `~/.klody/venvs/unity-mcp`.

## Inspection et ciblage

1. Demander les outils avec `find_tools("unity")`. Seuls les outils courants
   sont proposés au départ. Charger les autres avec un sujet précis, par exemple
   `Unity animation`, `Unity prefab`, `Unity script`, `Unity tests`, `Unity build`,
   un nom exact, ou `Unity tous les outils` si nécessaire.
2. Lire la scène active (`manage_scene`, `action=get_active`) et sa hiérarchie
   (`action=get_hierarchy`). Paginer : les enfants peuvent être tronqués.
3. Vérifier le nom/path du projet et de la scène, les modifications non
   sauvegardées, le mode Play et la console avant d’agir. Le connecteur MCP
   n’est utilisable que lorsque l’éditeur équipé est ouvert et connecté.
4. S’il existe plusieurs éditeurs, lister les instances avec un GET local sur
   `http://127.0.0.1:8097/api/instances`, puis passer `unity_instance="Name@hash"`
   **à chaque appel**. Le client Klody ouvre une nouvelle session MCP par appel :
   `set_active_instance` seul ne conserve donc pas la sélection entre appels.
   Ne jamais choisir le premier projet arbitrairement.

## Changer, relire, vérifier

Utiliser les opérations structurées : `manage_gameobject`, `manage_components`,
`manage_material`, `manage_animation`, `manage_prefabs`, `manage_scene`.
Pour C#, préférer les outils de scripts et `unity_reflect`/`unity_docs` pour
vérifier les API de la version installée. `execute_code` exécute du C# Editor
avec le compilateur disponible ; ce n’est pas un bac à sable de sécurité.

Après une modification, relire les transforms/composants puis vérifier le rendu
et la console. Pour le comportement, tester Play puis Stop, en conservant les
valeurs et l’état manuel précédents. Garder le test limité au résultat demandé.

L’Undo Unity peut regrouper plusieurs opérations et laisser `isDirty=true` même
après retour à la géométrie d’origine. Relire après chaque annulation ; ne pas
supposer une annulation par appel. Ne pas sauvegarder ou jeter des modifications
utilisateur pour nettoyer un essai. Un rechargement ciblé n’est acceptable que
si les changements à abandonner sont exclusivement ceux de cet essai et que le
fichier original a été conservé.

## Périmètre créatif

Logo 3D : créer le modèle et le rendu dans Blender, utiliser Unity pour une
présentation interactive. Personnage : prévoir topologie, UV, rig, clips et
vérifications Humanoid/Generic ; un personnage réaliste final exige davantage
qu’une génération de primitives. Jeu : construire une interaction jouable puis
élargir par étapes. Animation audiovisuelle : fixer durée, images par seconde,
caméra, événements et piste audio, puis vérifier la synchronisation à l’export.

Les outils `generate_model`, `generate_image` et `generate_audio` du connecteur
requièrent des fournisseurs et clés distincts. Aucun fournisseur de génération
externe n’a été configuré pour cette installation. Leur présence dans le catalogue
ne prouve pas un moteur text-to-3D ou audio local disponible. Utiliser les modèles
Blender, assets et outils audio effectivement accessibles à Klody.

## Sources et preuves

[Code et version du connecteur](https://github.com/CoplayDev/unity-mcp/releases/tag/v10.2.0),
[installation du serveur](https://github.com/CoplayDev/unity-mcp/blob/v10.2.0/Server/README.md).
Le ciblage par appel est implémenté dans `Server/src/transport/unity_instance_middleware.py`.

Preuves locales : `~/Projets/BlenderUnityPrototype/Evidence/unity-mcp`.
Essai vérifié : scène Signal, création d’un cube temporaire, déplacement vers
`(2, 3, 4)`, relecture, annulation, puis retour à la scène d’origine sans changer
le fichier `.unity`. Ce test valide la liaison et ces opérations ; il ne valide
pas tous les 47 outils ni toutes les fonctionnalités créatives.
