# Unity MCP pour Klody

Connecteur libre [CoplayDev MCP for Unity 10.2.0](https://github.com/CoplayDev/unity-mcp/releases/tag/v10.2.0),
commit `30d22075093d1d35dfb0091c1c7550e9ad948577`, testé avec Unity 6000.6.0f1.

## Installation locale

- Source épinglée : `~/.klody/vendor/unity-mcp-10.2.0`.
- Serveur Python séparé : `~/.klody/venvs/unity-mcp`.
- Lanceur : `scripts/start-unity-mcp.sh`.
- Service : `launchagents/com.klody.unity-mcp.plist`, copié dans
  `~/Library/LaunchAgents`, chargé comme `com.klody.unity-mcp`.
- URL : `http://127.0.0.1:8097/mcp`, écoute uniquement sur loopback.
- Entrée `.env` : ajouter `"unity":"http://127.0.0.1:8097/mcp"` au JSON
  `KLODY_MCP_SERVERS`, en conservant les autres serveurs, puis relancer l'API.
- Versions Python résolues : `installed-packages.txt`.

Projet équipé : `~/Projets/BlenderUnityPrototype`. Son manifest
contient `com.coplaydev.unity-mcp` avec la source locale
`file:~/.klody/vendor/unity-mcp-10.2.0/MCPForUnity`.
Les versions des packages préexistants n'ont pas changé. Les seules additions
sont le connecteur, `com.unity.modules.unitywebrequest` et Newtonsoft JSON.

Pour un autre projet, installer le même package via Package Manager (« Add
package from disk », sélectionner son `package.json`) puis vérifier l'instance.
Le package est installé par projet. Sur une autre machine, adapter les chemins
locaux du manifest et de l'agent launchd. Le code C# reste issu du tag épinglé.

Réglages EditorPrefs du connecteur : transport HTTP, scope local, URL ci-dessus,
AutoStartOnLoad activé, TelemetryDisabled activé. Le serveur démarre avec la
session macOS ; le bridge du projet se connecte à l'ouverture de l'éditeur.
Les générateurs externes de modèles/images/audio n'ont aucun fournisseur ni clé
configurés par cette installation.

## Utilisation et contexte

`find_tools("unity")` charge huit outils courants. `find_tools("Unity animation")`,
`Unity script`, `Unity prefab`, `Unity build`, le nom exact d'un outil, ou
`Unity tous les outils` permettent d'élargir la sélection. Les 47 outils du
serveur restent accessibles. Une inspection simple propose 17 outils au total
(8 Unity, 7 outils communs Klody, 2 outils de chargement).

Les appels Klody utilisent des sessions MCP distinctes. Pour plusieurs éditeurs,
lire `GET http://127.0.0.1:8097/api/instances`, puis répéter `unity_instance=Name@hash`
à chaque appel. Le paramètre est pris en charge par le middleware du serveur et
ajouté aux définitions proposées par Klody. `set_active_instance` seul ne garantit
pas le ciblage du prochain appel Klody.

Le skill `librarybrain_unity` contient le protocole d'inspection, de modification
et de vérification ; sa source est dans `LibraryBrainSkills/skills/librarybrain-unity`.
La sélection privilégie un terme exact dans le titre pour éviter de perdre le
skill Unity lorsque d'autres compétences mentionnent Unity dans leur description.

## Vérifications réalisées

Découverte MCP, lecture du projet et de la scène Signal, création d'un cube
temporaire, déplacement à `(2, 3, 4)`, relecture, Undo, restauration de la scène
propre. Le SHA-256 du fichier Signal est conservé et la console ne contenait
aucune erreur après l'essai. L'Undo a regroupé création et déplacement ; une
seconde annulation n'avait plus d'opération. Le rechargement a retiré le marqueur
`isDirty` laissé par le test, sans sauvegarder de changement au fichier original.

Une vraie conversation WebSocket Klody a appelé `mcp__unity__manage_scene` et
renvoyé « Signal, 6 objets racine ». Les preuves et le script de reproduction
figurent dans `~/Projets/BlenderUnityPrototype/Evidence/unity-mcp`.
Ce test ne constitue pas une validation exhaustive des 47 outils.

Tests concernés : sélection de contexte, client MCP, routage des modèles,
sélection/import des skills, santé MCP, couverture des launchagents et rejeux
de l'orchestrateur. Ruff, shell syntax et validation plist passent.

## Retirer la connexion

Retirer uniquement l'entrée `unity` de `KLODY_MCP_SERVERS`, puis relancer l'API.
Arrêter le service avec `launchctl bootout gui/$(id -u)/com.klody.unity-mcp`.
Retirer le package du projet via Package Manager si souhaité. Les manifests
antérieurs sont conservés dans `Evidence/unity-mcp` ; éviter de restaurer tout un
ancien manifest si d'autres packages ont été ajoutés entre-temps.
