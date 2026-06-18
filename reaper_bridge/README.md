# REAPER MCP — piloter REAPER en langage naturel

Serveur MCP local qui permet à Klody (scaffold ReAct, Qwen3-Coder-Next 4B servi
par mlx-lm) de piloter le DAW **REAPER** par tool-calling. 100 % local, zéro
réseau sortant, zéro télémétrie.

## Architecture (pourquoi un pont)

L'API REAPER (~1000 fonctions `RPR_*`) n'est accessible que **depuis l'intérieur
du processus REAPER** (ReaScript). Le serveur MCP est un processus externe → il
faut un pont :

```
  Klody (client MCP)
        │ mcp__reaper__get_track_count
        ▼
  klody_mcp/reaper_server.py   ← serveur FastMCP (process externe, .venv Klody)
        │ socket TCP 127.0.0.1:9000  (JSON, 1 ligne/message)
        ▼
  reaper_bridge/klody_reaper_bridge.py   ← ReaScript Python, tourne DANS REAPER
        │ appels RPR_*
        ▼
  REAPER 7.74
```

**Choix : pont-maison, pas `reapy`.** `python-reapy` (PyPI) est figé à 0.10.0
(2020-12-29), mappe l'API 1:1 (ce qu'on veut éviter) et n'était pas installable
ici. Le pont-maison n'utilise que la **stdlib** côté REAPER (`socket`, `select`,
`json`) → aucune dépendance pip dans REAPER, robuste quelle que soit la version
de libpython chargée.

**Principe directeur : pas de mapping 1:1.** Le serveur expose une poignée de
verbes métier autodescriptifs, pas les 1000 primitives.

---

## (a) Configuration manuelle de REAPER / ReaScript (une fois)

ReaScript Python n'est **pas activé par défaut** (vérifié : `reaper.ini` ne
contient aucune ligne python).

1. **Activer Python.** REAPER > **Settings/Preferences > Plug-ins > ReaScript**.
   - Cocher **Enable Python for use with ReaScript**.
   - Cocher **Force ReaScript to use specific Python .dylib** et renseigner :
     - Custom path : `/opt/homebrew/Frameworks/Python.framework/Versions/3.11/lib`
     - dylib name : `libpython3.11.dylib`
   - (Ce dylib est présent sur cette machine et s'aligne avec le `.venv` Klody
     en 3.11.15. Le pont n'importe que la stdlib, donc 3.14 marcherait aussi.)
   - Cliquer **OK**. Le statut doit afficher Python détecté (version + chemin).

2. **Charger le pont.** REAPER > **Actions > Show action list… > ReaScript:
   Load…** et choisir :
   `~/Projets/klody-code-ai/reaper_bridge/klody_reaper_bridge.py`

3. **Lancer le pont.** Dans la liste d'actions, double-cliquer
   `klody_reaper_bridge.py`. La console REAPER affiche :
   `[klody_reaper_bridge] pont actif sur 127.0.0.1:9000 (protocole v1)`.
   Le pont vit en tâche de fond (`defer`) sans figer REAPER.

   - **Démarrage auto (optionnel)** : copier le fichier dans
     `~/Library/Application Support/REAPER/Scripts/` et l'ajouter à
     `__startup.lua` (ou via SWS Startup actions) pour qu'il se lance à
     l'ouverture de REAPER.

---

## (b) Lancer le serveur MCP

Process séparé, dans le `.venv` Klody :

```bash
cd ~/Projets/klody-code-ai
./scripts/start-reaper-mcp.sh            # stdio (défaut)
./scripts/start-reaper-mcp.sh --http     # HTTP sur :8089  (pour Klody)
```

Variables d'environnement (toutes optionnelles) :

| Var | Défaut | Rôle |
|---|---|---|
| `REAPER_MCP_TRANSPORT` | `stdio` | `stdio` ou `http` |
| `REAPER_MCP_PORT` | `8089` | port HTTP du serveur MCP |
| `REAPER_BRIDGE_HOST` | `127.0.0.1` | hôte du pont dans REAPER |
| `REAPER_BRIDGE_PORT` | `9000` | port socket du pont (doit matcher le script) |
| `REAPER_BRIDGE_TIMEOUT` | `5` | timeout socket (s) |
| `REAPER_ENABLE_SKELETON` | `0` | `1` = enregistre les ~16 outils Phase 3 |

---

## (c) Procédure de test — Gate 1 (manuelle)

But : valider la chaîne complète avec **un seul** outil de lecture pure, avant
d'élargir. Critères binaires.

### G1.a — la chaîne renvoie le vrai nombre de pistes

1. Ouvrir REAPER sur un **projet vide** (0 piste). Activer ReaScript + lancer le
   pont (section a).
2. Sonder le pont sans LLM :
   ```bash
   cd ~/Projets/klody-code-ai
   .venv/bin/python -c "from klody_mcp.reaper_server import _bridge_call_sync as c; print(c('ping')); print(c('get_track_count'))"
   ```
   Attendu : `{'pong': True, 'protocol': 1, 'reaper': '7.74...'}` puis
   `{'track_count': 0}`.
3. Dans REAPER, ajouter **3 pistes** (Cmd-T ×3). Relancer la commande
   `get_track_count` → attendu `{'track_count': 3}`.

   ✅ **G1.a vert** si le compte suit le projet réel (0 → 3).
   ❌ Si `{'error': 'pont REAPER injoignable…'}` : REAPER pas lancé, ou pont pas
   chargé/lancé, ou port ≠ 9000.

### G1.b — Klody (4B) émet le tool-call au bon format

1. Déclarer le serveur dans Klody (section d) et redémarrer le backend.
2. Démarrer le serveur MCP en HTTP : `./scripts/start-reaper-mcp.sh --http`.
3. Demander à Klody, en langage naturel : « combien de pistes dans mon projet
   REAPER ? »

   ✅ **G1.b vert** si Klody émet un tool-call `mcp__reaper__get_track_count`
   avec des arguments JSON valides (ici aucun argument requis).
   ❌ Si le format est cassé (mauvais nom, JSON invalide, arguments inventés) :
   **c'est un problème de format tool-call du 4B, PAS de l'architecture** — la
   chaîne G1.a prouve que le pont marche. Voir agent.log côté Klody.

### G1.c — la boucle ReAct se termine proprement

✅ **G1.c vert** si la séquence est : 1 appel → 1 résultat → réponse finale en
langage naturel, **sans réémission en boucle** du même appel.
❌ Si le 4B réémet `get_track_count` en boucle ou n'intègre jamais le résultat :
problème de format/parsing tool-call du 4B (cf. mémoires Klody sur l'arrêt à
max_iter et l'injection de skills), pas de l'architecture.

---

## (d) Déclarer l'outil dans la config MCP de Klody

Klody est client MCP. Une seule ligne dans `.env`
(`KLODY_MCP_SERVERS`) — rien dans le cœur :

```env
KLODY_MCP_SERVERS={"gmail":"http://127.0.0.1:8084/mcp", ... ,"reaper":"http://127.0.0.1:8089/mcp"}
```

(Déjà ajouté par ce travail — voir `.env`.) Les outils sont exposés au LLM sous
`mcp__reaper__<outil>`.

> **GOTCHA** (recette MCP Klody) : `KLODY_MCP_SERVERS` est lu **à l'import** +
> cache de découverte process-level → après modif, **redémarrer le backend**
> (`launchctl kickstart -k gui/$(id -u)/com.klody.api`). Un serveur down au boot
> est ignoré : démarrer le serveur MCP REAPER **avant** le backend.

Vérif sans réseau :
```bash
.venv/bin/python -c "import config; from tools.mcp_bridge import MCPManager; print(MCPManager(config.MCP_SERVERS).discover())"
```

---

## Sécurité & idempotence

- **Lecture seule pour l'instant.** Phase 2 = `get_track_count` uniquement.
  Aucune commande ne modifie ou ne sauvegarde le projet.
- **Jamais de sauvegarde implicite** : aucun `RPR_Main_SaveProject`. Les outils à
  effet de bord (Phase 3) restent en TODO explicite et devront exiger une
  intention claire.
- **Redémarrage sans état corrompu** : le pont rebinde avec `SO_REUSEADDR` ; le
  serveur MCP est sans état. Les lectures sont idempotentes.
- **Erreurs exploitables** : pont down → `{"error": "pont REAPER injoignable…"}`
  avec le diagnostic, jamais un crash silencieux ni un faux succès.

## Outils exposés

**12 outils LIVE** (implémentés + testés) :

| Outil | Type | Effet |
|---|---|---|
| `get_track_count` | lecture | nombre de pistes |
| `list_tracks` | lecture | index, nom, volume_db, pan, mute, solo |
| `get_play_position` | lecture | position lecture + curseur + play/rec/pause |
| `add_track` | écriture | insère une piste (nom + index optionnels) |
| `rename_track` | écriture | renomme la piste `index` |
| `delete_track` | écriture | supprime la piste `index` (annulable Cmd-Z) |
| `set_track_volume` | écriture | volume en dB |
| `set_track_pan` | écriture | pan [-1..1] |
| `set_track_mute` | écriture | mute/unmute |
| `set_track_solo` | écriture | solo/unsolo |
| `transport_play` | écriture | lance la lecture |
| `transport_stop` | écriture | arrête lecture/enregistrement |

Aucune n'appelle `RPR_Main_SaveProject` : le projet est modifié en mémoire mais
**jamais sauvegardé** automatiquement.

**Squelette restant** (gaté `REAPER_ENABLE_SKELETON=1`, effets lourds/risqués) :
`transport_record`, `insert_midi_note`, `list_midi_notes`, `render_region`,
`render_project`. Ajouter un outil = handler dans `_DISPATCH` côté pont +
`@mcp.tool()` côté serveur (calquer un outil live), puis recharger le pont +
redémarrer le serveur MCP + le backend.

## Démarrage automatique du pont (optionnel)

`~/Library/Application Support/REAPER/Scripts/__startup.lua` (installé par ce
travail) lance le pont à l'ouverture de REAPER via
`AddRemoveReaScript` + `Main_OnCommand` (portable, sans SWS). Pour désactiver :
supprimer ce fichier. Sinon, charger/relancer le pont manuellement (Actions).

## État de validation

| Test (auto, sans REAPER — `RPR_*` stubbé) | Statut |
|---|---|
| Compile (bridge + serveur) | ✅ |
| 1 seul outil par défaut, 17 avec `REAPER_ENABLE_SKELETON=1` | ✅ |
| Protocole bout-en-bout : ping, `track_count` 0 et 3, cmd inconnue, JSON invalide | ✅ |
| Garde anti-balloon : ligne > 64 KiB sans `\n` → enveloppe d'erreur + fermeture | ✅ |
| Double `bind` (port occupé) → `False` propre, pas de crash | ✅ |
| Diagnostic distinct : refusé → « injoignable » ; **connecté mais muet → « pas de réponse »** (pas de faux « down ») | ✅ |
| **G1.a/b/c en vrai (REAPER lancé + 4B)** | ⏳ **manuel** — section (c) |

> Une revue adversariale (4 lentes : ReaScript, protocole socket, idiome MCP,
> sécurité) a confirmé que l'idiome `RPR_defer("_serve()")` (chaîne, pas
> callable) est le bon pour le ReaScript Python, et a motivé les durcissements
> ci-dessus (purge des fd morts, garde de buffer, désambiguïsation d'erreur,
> publication de `_serve` dans `__main__`, `RPR_atexit`).
