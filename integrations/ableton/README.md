# Klody-AI et Ableton Live

Klody consomme un serveur MCP local, installé dans un environnement Python séparé.

| Composant | Configuration |
|---|---|
| Serveur amont | `mcp-server-ableton-live==1.8.0` |
| Environnement | `~/.klody/venvs/ableton-mcp`, dépendances figées dans `requirements.lock` |
| MCP HTTP | `http://127.0.0.1:8094/mcp` |
| Socket Live | `127.0.0.1:9878` |
| Surface de contrôle | `KlodyAbletonMCP`, entrée **None**, sortie **None** |
| Démarrage MCP | LaunchAgent `com.klody.ableton-mcp`, au login |
| Compétence Klody | `skills/ableton_live_pilotage.json` |

Le dossier de surface est dans `~/Music/Ableton/User Library/Remote Scripts/KlodyAbletonMCP`.
Son `bridge.py` est copié sans modification depuis le paquet épinglé ; seul notre
`__init__.py` fixe le port et l'adresse avant l'instanciation. Les empreintes sont
dans `installation.json`. Le pont existant `KlodyBridge` de Song2Chords garde son
code et le port 9877. Le diagnostic amont `doctor` attend le nom AbletonMCP et le
port standard : il ne valide pas cette installation avec coexistence de ponts.

Après installation, Live doit scanner les scripts au démarrage. Dans Réglages →
Tempo et MIDI → Surface de contrôle, choisir **KlodyAbletonMCP** sur une ligne
inutilisée, avec entrée et sortie **None**. Garder les autres surfaces en place.
Une fenêtre modale ou un blocage du moteur audio peut empêcher l'API Live de répondre.

## Surface offerte à Klody

`tools.json` sélectionne 53 outils typés : inspection du set, lecture/arrêt,
tempo, scènes, pistes MIDI/audio/retour, niveaux/panoramiques/mute/solo, clips et
notes, navigateur, instruments/effets, paramètres, arrangement et automation.
La liste explicite limite la taille du catalogue dans le contexte du modèle.
Elle peut être étendue aux autres outils de la même version du serveur.

Les indices commencent à zéro ; les temps sont en noires. Les faders Live ne
sont pas des dB. Lire les plages natives avant de régler un appareil. Certaines
opérations, notamment suppression de piste et placement dans l'arrangement,
modifient le contenu existant. Identifier la cible et relire l'état après action.
Les méthodes LibraryBrain Music fournissent le protocole musical ; une réponse
MCP réussie ne constitue pas une écoute ni une mesure d'amélioration sonore.

## Maintenance

Le script `scripts/start-ableton-mcp.sh` démarre le service local. Le LaunchAgent
installé correspond à `launchagents/com.klody.ableton-mcp.plist` du dépôt.
`KLODY_MCP_SERVERS` dans `.env` contient l'entrée `ableton` ; les dix entrées
préexistantes sont conservées. Une sauvegarde privée `.env.bak-ableton-*` précède
cette modification. Aucune dépendance du backend principal n'a été mise à jour.

Pour réinstaller les dépendances :

```sh
uv venv ~/.klody/venvs/ableton-mcp --python .venv/bin/python
uv pip install --python ~/.klody/venvs/ableton-mcp/bin/python -r integrations/ableton/requirements.lock
```

L'environnement reste hors du dépôt pour éviter que le moteur de recherche de
code de Klody indexe les dépendances du MCP en préparant une réponse.

Ne pas mettre à jour le paquet sans mettre à jour et vérifier le script chargé
par Live. La version du serveur est contrôlée avant son démarrage.

Pour retirer cette intégration, désactiver uniquement la ligne KlodyAbletonMCP
dans Live, retirer l'entrée `ableton` de la configuration MCP de Klody, puis
décharger `com.klody.ableton-mcp`. Les fichiers du pont Song2Chords sont indépendants.

Source amont : [Ableton Live MCP Server](https://github.com/wstierhout/ableton-live-mcp).
Licence MIT conservée avec le script installé. Les résultats de vérification
locale sont enregistrés dans le rapport d'intégration.
