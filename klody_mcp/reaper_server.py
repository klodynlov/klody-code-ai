"""REAPER MCP server — pilote le DAW REAPER en langage naturel.

Même patron que vocalbrain_server.py / web_server.py : pont MCP léger. Il
n'embarque AUCUNE dépendance REAPER. Il parle à un script ReaScript Python qui
tourne DANS REAPER (`reaper_bridge/klody_reaper_bridge.py`) via un socket TCP
localhost (JSON, une ligne par message). Tout reste sur 127.0.0.1.

Principe directeur : PAS de mapping 1:1 des ~1000 fonctions RPR_*. La surface
exposée au LLM est une poignée de verbes métier autodescriptifs.

PHASE 2 (Gate 1) : un SEUL outil actif, `get_track_count` (lecture pure). Les
~16 outils de la Phase 3 existent en squelette mais ne sont PAS enregistrés tant
que REAPER_ENABLE_SKELETON != "1" — le 4B voit donc exactement un outil, ce qui
isole la validation du format tool-call.

(Le pont répond aussi à une commande protocole `ping` — lecture seule, pour le
/health — que le serveur MCP n'expose PAS comme outil. Le dispatch du pont a
donc deux entrées read-only ; ce n'est pas du scope creep.)

Démarrage :
    python -m klody_mcp.reaper_server                              # stdio (défaut)
    REAPER_MCP_TRANSPORT=http python -m klody_mcp.reaper_server    # :8089

Prérequis : REAPER lancé + script pont chargé (Actions > Load ReaScript).
Voir reaper_bridge/README.md (config REAPER + procédure de test Gate 1).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import tempfile
import uuid

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

logger = logging.getLogger(__name__)

BRIDGE_HOST = os.getenv("REAPER_BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = int(os.getenv("REAPER_BRIDGE_PORT", "9000"))
BRIDGE_TIMEOUT = float(os.getenv("REAPER_BRIDGE_TIMEOUT", "5"))

mcp = FastMCP("REAPER")

_UNREACHABLE = (
    f"pont REAPER injoignable ({BRIDGE_HOST}:{BRIDGE_PORT}) — vérifie que (1) REAPER est lancé et "
    "(2) le script reaper_bridge/klody_reaper_bridge.py est chargé et actif "
    "(Actions > Load ReaScript puis lancer l'action)."
)
# Diagnostic DISTINCT : socket accepté mais pas de réponse complète. NE PAS dire
# « REAPER injoignable » (faux) — c'est plutôt une boucle defer arrêtée / REAPER figé.
_NO_REPLY = (
    f"pont REAPER joignable ({BRIDGE_HOST}:{BRIDGE_PORT}) mais pas de réponse complète avant "
    f"{BRIDGE_TIMEOUT}s — boucle defer du script pont arrêtée ou REAPER figé ? Relance l'action du pont."
)


# ---------------------------------------------------------------------------- #
# Client du pont (socket JSON, line-delimited)                                 #
# ---------------------------------------------------------------------------- #


def _bridge_call_sync(cmd: str, args: dict | None = None) -> dict:
    """Envoie une commande au pont REAPER et renvoie son `result`.

    Renvoie toujours un dict : le résultat en cas de succès, sinon
    {"error": "..."} — jamais d'exception qui remonte au LLM.
    """
    payload = (json.dumps({"cmd": cmd, "args": args or {}}) + "\n").encode("utf-8")
    buf = bytearray()
    try:
        with socket.create_connection((BRIDGE_HOST, BRIDGE_PORT), timeout=BRIDGE_TIMEOUT) as sock:
            sock.settimeout(BRIDGE_TIMEOUT)
            sock.sendall(payload)
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:  # pont a fermé avant un '\n' → ligne incomplète
                    break
                buf.extend(chunk)
    except (ConnectionRefusedError, FileNotFoundError):
        return {"error": _UNREACHABLE}  # rien n'écoute → REAPER/pont down
    except TimeoutError:
        return {"error": _NO_REPLY}  # connecté mais muet → pas un faux « down »
    except OSError as exc:
        return {"error": f"erreur socket pont REAPER ({BRIDGE_HOST}:{BRIDGE_PORT}): {exc}"}

    if b"\n" not in buf:  # connexion fermée sur réponse partielle/vide
        return {"error": _NO_REPLY if buf else "réponse vide du pont REAPER"}
    line = bytes(buf).split(b"\n", 1)[0]
    try:
        resp = json.loads(line)
    except (ValueError, TypeError) as exc:
        return {"error": f"réponse illisible du pont REAPER: {exc}"}

    if not resp.get("ok"):
        return {"error": resp.get("error", "erreur côté pont REAPER")}
    result = resp.get("result")
    return result if isinstance(result, dict) else {"result": result}


async def _bridge_call(cmd: str, args: dict | None = None) -> dict:
    """Variante non bloquante : le socket part dans un thread."""
    return await asyncio.to_thread(_bridge_call_sync, cmd, args)


# ---------------------------------------------------------------------------- #
# PHASE 2 — outil unique (Gate 1)                                              #
# ---------------------------------------------------------------------------- #


@mcp.tool()
async def get_track_count() -> dict:
    """Renvoie le nombre de pistes du projet REAPER actuellement ouvert.

    Lecture pure : ne modifie jamais le projet. Aucun argument.

    Returns:
        {"track_count": <int>} en cas de succès,
        {"error": "<message exploitable>"} si REAPER/le pont est indisponible.
    """
    return await _bridge_call("get_track_count")


# ---------------------------------------------------------------------------- #
# PHASE 3a — première écriture (outil LIVE, pas via le flag skeleton).          #
# Choix : exposer add_track comme 2e outil actif plutôt que d'activer les 16    #
# (le 4B verrait 15 outils non implémentés). Effet de bord SANS sauvegarde.     #
# ---------------------------------------------------------------------------- #


@mcp.tool()
async def add_track(name: str = "", index: int = -1) -> dict:
    """Ajoute une piste au projet REAPER ouvert.

    Effet de bord : modifie le projet en mémoire mais NE le sauvegarde PAS
    (pas de RPR_Main_SaveProject). Idempotent au sens « chaque appel ajoute une
    piste » — appeler 2× crée 2 pistes.

    Args:
        name: nom de la piste (optionnel ; vide = nom par défaut REAPER).
        index: position d'insertion 0-based (-1 ou hors borne = à la fin).

    Returns:
        {"inserted_index": <int>, "name": <str>, "track_count": <int>} après ajout,
        {"error": "<message>"} si REAPER/le pont est indisponible.
    """
    return await _bridge_call("add_track", {"name": name, "index": index})


# ---------------------------------------------------------------------------- #
# PHASE 3b — outils LIVE (lecture + écriture légère, sans save).               #
# Implémentés et testés ; signatures RPR vérifiées dans reaper_python.py.       #
# delete_track est DESTRUCTIF mais annulable (Cmd-Z REAPER).                    #
# ---------------------------------------------------------------------------- #


@mcp.tool()
async def list_tracks() -> dict:
    """Liste les pistes du projet : index, nom, volume_db, pan, mute, solo.

    Lecture pure : ne modifie jamais le projet. Le `guid` de chaque piste est un
    identifiant STABLE (contrairement à l'index, qui décale à chaque insert/delete) :
    le réutiliser pour cibler une piste dans les autres outils.

    Returns:
        {"count": <int>, "tracks": [{"index","guid","name","volume_db","pan","mute","solo"}, ...]}.
    """
    return await _bridge_call("list_tracks")


@mcp.tool()
async def get_play_position() -> dict:
    """État du transport : position de lecture + curseur d'édition + play/rec/pause.

    Lecture pure.

    Returns:
        {"play_position","edit_cursor","playing","paused","recording"} (sec + bools).
    """
    return await _bridge_call("get_play_position")


@mcp.tool()
async def rename_track(index: int = -1, name: str = "", guid: str = "") -> dict:
    """Renomme une piste, ciblée par `guid` (stable, prioritaire) ou `index` 0-based.
    Modifie le projet, sans le sauvegarder.

    Returns: {"index","guid","name"} ou {"error": ...}.
    """
    return await _bridge_call("rename_track", {"index": index, "name": name, "guid": guid})


@mcp.tool()
async def delete_track(index: int = -1, guid: str = "") -> dict:
    """Supprime une piste, ciblée par `guid` (stable, prioritaire) ou `index` 0-based.
    DESTRUCTIF mais annulable (`undo_last`, ou Cmd-Z REAPER). Sans sauvegarde.

    Returns:
        {"deleted_index": <int>, "guid": <str>, "track_count": <int>} ou {"error": ...}.
    """
    return await _bridge_call("delete_track", {"index": index, "guid": guid})


@mcp.tool()
async def set_track_volume(index: int = -1, db: float = 0.0, guid: str = "") -> dict:
    """Règle le volume en dB (0 = unité, négatif = plus bas) d'une piste ciblée par
    `guid` (stable, prioritaire) ou `index` 0-based. Sans sauvegarde."""
    return await _bridge_call("set_track_volume", {"index": index, "db": db, "guid": guid})


@mcp.tool()
async def set_track_pan(index: int = -1, pan: float = 0.0, guid: str = "") -> dict:
    """Règle le pan : -1.0 (gauche) .. 0.0 (centre) .. 1.0 (droite). Hors borne =
    clampé. Piste ciblée par `guid` (stable, prioritaire) ou `index`. Sans sauvegarde."""
    return await _bridge_call("set_track_pan", {"index": index, "pan": pan, "guid": guid})


@mcp.tool()
async def set_track_mute(index: int = -1, mute: bool = True, guid: str = "") -> dict:
    """Mute (True) ou démute (False) une piste ciblée par `guid` (stable, prioritaire)
    ou `index` 0-based. Sans sauvegarde."""
    return await _bridge_call("set_track_mute", {"index": index, "mute": mute, "guid": guid})


@mcp.tool()
async def set_track_solo(index: int = -1, solo: bool = True, guid: str = "") -> dict:
    """Solo (True) ou unsolo (False) une piste ciblée par `guid` (stable, prioritaire)
    ou `index` 0-based. Sans sauvegarde."""
    return await _bridge_call("set_track_solo", {"index": index, "solo": solo, "guid": guid})


@mcp.tool()
async def transport_play() -> dict:
    """Lance la lecture du projet. Returns {"playing": true}."""
    return await _bridge_call("transport_play")


@mcp.tool()
async def transport_stop() -> dict:
    """Arrête la lecture/enregistrement. Returns {"playing": false}."""
    return await _bridge_call("transport_stop")


# ---------------------------------------------------------------------------- #
# PHASE 3c — outils LOURDS, désormais LIVE. Effets de bord risqués :            #
# transport_record écrit de l'audio ; render_* écrivent un fichier sur disque   #
# (out_path REQUIS = intention explicite). Aucun ne sauvegarde le projet .rpp.  #
# ---------------------------------------------------------------------------- #


@mcp.tool()
async def transport_record() -> dict:
    """Démarre l'enregistrement sur les pistes armées.

    ⚠️ RISQUÉ : écrit de l'audio. À n'appeler que sur intention explicite de
    l'utilisateur. `transport_stop` arrête. Returns {"recording": true}.
    """
    return await _bridge_call("transport_record")


@mcp.tool()
async def insert_midi_note(
    track_index: int = -1,
    pitch: int = 60,
    start: float = 0.0,
    length: float = 0.5,
    velocity: int = 96,
    channel: int = 0,
    guid: str = "",
) -> dict:
    """Insère une note MIDI dans un nouvel item MIDI sur une piste ciblée.

    Chaque appel crée son propre item [start, start+length]. Sans sauvegarde.

    Args:
        track_index: piste cible (0-based) — ou utiliser `guid`.
        pitch: note MIDI 0-127 (60 = Do central).
        start: début en secondes (temps projet).
        length: durée en secondes.
        velocity: vélocité 1-127 (défaut 96).
        channel: canal MIDI 0-15 (défaut 0).
        guid: GUID de piste (identifiant stable, prioritaire sur track_index).
    """
    return await _bridge_call("insert_midi_note", {
        "track_index": track_index, "pitch": pitch, "start": start,
        "length": length, "velocity": velocity, "channel": channel, "guid": guid,
    })


@mcp.tool()
async def insert_midi_notes(
    track_index: int = -1, notes: list[dict] | None = None, guid: str = "",
) -> dict:
    """Insère une mélodie entière (plusieurs notes) dans UN SEUL item MIDI sur la piste.

    À préférer à `insert_midi_note` appelé en boucle : celui-ci crée un item par
    note (mélodie = N items qui se chevauchent), alors qu'`insert_midi_notes` pose
    toute la mélodie dans un seul item propre. L'item couvre [début de la 1re note,
    fin de la dernière]. Sans sauvegarde.

    `notes` est exactement la forme des `events` renvoyés par
    mcp__klodymusic__melodie_vers_midi : enchaîne donc directement les deux outils.

    Args:
        track_index: piste cible (0-based) — ou utiliser `guid`.
        notes: liste de notes, chacune {pitch: 0-127, start: sec, length: sec,
            velocity?: 1-127 (défaut 96), channel?: 0-15 (défaut 0)}.
        guid: GUID de piste (identifiant stable, prioritaire sur track_index).

    Returns:
        {"track_index", "guid", "note_count", "item_start", "item_end"} ou {"error": "..."}.
    """
    return await _bridge_call(
        "insert_midi_notes",
        {"track_index": track_index, "notes": notes or [], "guid": guid},
    )


@mcp.tool()
async def list_midi_notes(track_index: int = -1, item_index: int = 0, guid: str = "") -> dict:
    """Liste les notes MIDI d'un item (pitch, start, length, velocity, channel).

    Lecture pure.

    Args:
        track_index: piste (0-based) — ou utiliser `guid`.
        item_index: item MIDI de la piste (0-based, défaut 0).
        guid: GUID de piste (identifiant stable, prioritaire sur track_index).

    Returns:
        {"note_count": <int>, "notes": [{"index","pitch","start","length","velocity","channel","muted"}, ...]}.
    """
    return await _bridge_call(
        "list_midi_notes",
        {"track_index": track_index, "item_index": item_index, "guid": guid},
    )


@mcp.tool()
async def render_region(region_index: int, out_path: str) -> dict:
    """Rend une région du projet en fichier audio (écrit sur disque).

    `out_path` est OBLIGATOIRE (chemin complet du fichier). Utilise les derniers
    réglages de format de REAPER. Ne sauvegarde JAMAIS le projet .rpp.

    Args:
        region_index: index de la région (0-based, ordre du projet).
        out_path: chemin complet du fichier de sortie (ex. /tmp/region0.wav).

    Returns:
        {"region_index","out_path","start","end","rendered": <bool>} ou {"error"}.
    """
    return await _bridge_call("render_region", {"region_index": region_index, "out_path": out_path})


@mcp.tool()
async def render_project(out_path: str) -> dict:
    """Rend le projet entier (master) en fichier audio (écrit sur disque).

    `out_path` est OBLIGATOIRE. Derniers réglages de format REAPER. Ne sauvegarde
    JAMAIS le projet .rpp.

    Returns:
        {"out_path", "rendered": <bool>} ou {"error"}.
    """
    return await _bridge_call("render_project", {"out_path": out_path})


# ---------------------------------------------------------------------------- #
# P1 — sûreté : snapshot (observe-avant-modifie), réversibilité (undo/redo),    #
# modes d'exécution. Voir spec DAW agentique sections 4.3 / 4.4 / 5 / 6.        #
# ---------------------------------------------------------------------------- #


@mcp.tool()
async def get_project_snapshot(detail: str = "standard") -> dict:
    """Snapshot lecture pure de l'état du projet REAPER (observe avant de modifier).

    À appeler AVANT toute modification importante : donne pistes, tempo, signature,
    sample rate, curseur et transport — et fournit les GUID (identifiants stables à
    réutiliser pour cibler une piste, plutôt que l'index qui décale).

    Args:
        detail: "summary" (projet seul), "standard" (défaut : + liste pistes),
            "full" (+ compteurs fx/items par piste).

    Returns:
        {"project": {"tempo","time_signature","sample_rate","edit_cursor","playing",...},
         "tracks": [{"index","guid","name","volume_db","pan","mute","solo"}, ...]}.
    """
    return await _bridge_call("get_project_snapshot", {"detail": detail})


@mcp.tool()
async def undo_last() -> dict:
    """Annule la DERNIÈRE opération (le dernier bloc Undo REAPER).

    Chaque modification passée par ce serveur est encadrée dans un bloc Undo nommé
    « klody: … » : un seul `undo_last` annule toute l'opération d'un coup. Renvoie
    le libellé de ce qui a été annulé.

    Returns:
        {"undone": <bool>, "label": <str|null>} (+ "reason" si rien à annuler).
    """
    return await _bridge_call("undo")


@mcp.tool()
async def redo_last() -> dict:
    """Rétablit la dernière opération annulée (redo REAPER).

    Returns:
        {"redone": <bool>, "label": <str|null>} (+ "reason" si rien à rétablir).
    """
    return await _bridge_call("redo")


@mcp.tool()
async def set_mode(mode: str) -> dict:
    """Règle le mode d'exécution du pont (garde-fou, spec DAW agentique section 5).

    Args:
        mode: "read_only" (refuse toute modification du projet — inspection et
            recommandations seulement), "assisted" ou "autonomous" (autorisent les
            modifications ; la confirmation des opérations destructrices reste du
            ressort de l'agent).

    Returns:
        {"mode": <str>} ou {"error": ...} si le mode est invalide.
    """
    return await _bridge_call("set_mode", {"mode": mode})


@mcp.tool()
async def get_mode() -> dict:
    """Renvoie le mode d'exécution courant + la liste des modes (lecture pure).

    Returns:
        {"mode": <str>, "modes": ["read_only","assisted","autonomous"]}.
    """
    return await _bridge_call("get_mode")


# ---------------------------------------------------------------------------- #
# P2 — portée musicale : FX (add/param/bypass/remove), routing (sends/bus       #
# idempotents), markers/régions. Voir spec DAW agentique §7.3 / §7.6 / §7.8.    #
# ---------------------------------------------------------------------------- #


@mcp.tool()
async def add_fx(name: str, index: int = -1, guid: str = "") -> dict:
    """Ajoute (ou retrouve) un effet par nom sur une piste (ciblée par guid/index).

    Idempotent : si l'effet est déjà présent il est réutilisé (pas de doublon).
    REAPER résout le nom par sous-chaîne (ex. "ReaEQ", "ReaComp", "Pro-Q"). L'effet
    doit être INSTALLÉ — on ne suppose jamais un plugin présent : erreur claire sinon.

    Returns: {"track_index","guid","fx_index","fx_name","created"} ou {"error"}.
    """
    return await _bridge_call("add_fx", {"name": name, "index": index, "guid": guid})


@mcp.tool()
async def remove_fx(fx: str, index: int = -1, guid: str = "") -> dict:
    """Supprime un effet d'une piste. `fx` = index ("0","1"…) ou nom (sous-chaîne)."""
    return await _bridge_call("remove_fx", {"fx": fx, "index": index, "guid": guid})


@mcp.tool()
async def bypass_fx(fx: str, bypass: bool = True, index: int = -1, guid: str = "") -> dict:
    """Bypass (True) ou réactive (False) un effet. `fx` = index ou nom (sous-chaîne).
    Utile pour laisser un traitement en place mais inactif en attente d'écoute humaine."""
    return await _bridge_call("bypass_fx", {"fx": fx, "bypass": bypass, "index": index, "guid": guid})


@mcp.tool()
async def get_fx_params(index: int = -1, guid: str = "", fx: str = "") -> dict:
    """Lecture pure. Sans `fx` : liste les effets de la piste (index, nom, enabled).
    Avec `fx` (index ou nom) : liste ses paramètres (index, nom, valeur normalisée
    0..1, valeur réelle) — à lire AVANT set_fx_param pour connaître les bons noms.

    Returns: {"fx":[...]} ou {"fx_index","params":[{"param_index","name","normalized","value"}]}.
    """
    return await _bridge_call("get_fx_params", {"index": index, "guid": guid, "fx": fx})


@mcp.tool()
async def set_fx_param(
    fx: str, param: str, value: float, raw: bool = False, index: int = -1, guid: str = "",
) -> dict:
    """Règle un paramètre d'effet. `fx`/`param` = index ou nom (sous-chaîne).

    `value` est NORMALISÉE 0..1 par défaut (la surface exposée par REAPER ; ne jamais
    se fier à la position visuelle d'un bouton). raw=True pour une valeur en unités
    natives du plugin. Lire get_fx_params d'abord pour les noms/plages.

    Returns: {"fx_index","fx_name","param_index","param_name","normalized"} ou {"error"}.
    """
    return await _bridge_call(
        "set_fx_param",
        {"fx": fx, "param": param, "value": value, "raw": raw, "index": index, "guid": guid},
    )


@mcp.tool()
async def create_send(
    dest_index: int = -1, dest_guid: str = "", index: int = -1, guid: str = "",
    vol_db: float | None = None, pan: float | None = None,
) -> dict:
    """Crée un send d'une piste source vers une piste destination (ex. vers un bus
    reverb/delay). IDEMPOTENT : pas de doublon si le send existe déjà.

    Source ciblée par `guid`/`index`, destination par `dest_guid`/`dest_index`.
    `vol_db` et `pan` optionnels (réglages du send).

    Returns: {"src_index","dest_index","send_index","created"} ou {"error"}.
    """
    return await _bridge_call("create_send", {
        "index": index, "guid": guid, "dest_index": dest_index, "dest_guid": dest_guid,
        "vol_db": vol_db, "pan": pan,
    })


@mcp.tool()
async def create_bus(name: str) -> dict:
    """Crée (ou retrouve) une piste-bus par nom. IDEMPOTENT : si une piste porte déjà
    ce nom exact, elle est renvoyée sans en créer une seconde — pratique pour des bus
    'Reverb'/'Delay'/'Mix' stables. Router ensuite des pistes avec create_send.

    Returns: {"index","guid","name","created"} ou {"error"}.
    """
    return await _bridge_call("create_bus", {"name": name})


@mcp.tool()
async def add_marker(position: float, name: str = "", color: int = 0) -> dict:
    """Ajoute un marqueur à `position` (secondes). IDEMPOTENT par position (pas de
    doublon au même endroit). `color` = entier RGB (0 = couleur par défaut).

    Returns: {"position","name","marker_id","created"} ou {"error"}.
    """
    return await _bridge_call("add_marker", {"position": position, "name": name, "color": color})


@mcp.tool()
async def add_region(start: float, end: float, name: str = "", color: int = 0) -> dict:
    """Ajoute une région [start, end] (secondes) — ex. structurer un morceau (intro,
    couplet, refrain…). IDEMPOTENT par position. `color` = entier RGB (0 = défaut).
    Une région se rend ensuite avec render_region.

    Returns: {"start","end","name","region_id","created"} ou {"error"}.
    """
    return await _bridge_call("add_region", {"start": start, "end": end, "name": name, "color": color})


# ---------------------------------------------------------------------------- #
# P3 — oreilles : analyse audio (rend en temp puis mesure hors REAPER). Voir   #
# spec DAW agentique §9. Le calcul vit dans klody_mcp.audio_analysis (numpy/    #
# scipy ; LUFS/tempo/tonalité optionnels via pyloudnorm/librosa).              #
# ---------------------------------------------------------------------------- #


@mcp.tool()
async def analyze_audio_file(path: str) -> dict:
    """Analyse un fichier WAV déjà sur disque : niveau (peak/true-peak/RMS/crest),
    dynamique, spectre (centroïde, rolloff, énergie par bandes), stéréo, silence,
    clipping — + LUFS / tempo / tonalité si les libs sont présentes. Lecture pure.

    Toute lecture subjective (« manque de présence ») est une HYPOTHÈSE à formuler
    comme telle ; l'outil ne renvoie que des nombres.

    Returns: dict de métriques, ou {"error": ...} si le fichier est introuvable/illisible.
    """
    from klody_mcp import audio_analysis
    try:
        return await asyncio.to_thread(audio_analysis.analyze_file, path)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
async def compare_audio_versions(path_a: str, path_b: str) -> dict:
    """Compare deux WAV (avant/après) : analyse chacun + deltas (b − a) des
    métriques clés (LUFS, RMS, peak, crest, centroïde, largeur stéréo…).

    Returns: {"a": {...}, "b": {...}, "delta": {...}} ou {"error": ...}.
    """
    from klody_mcp import audio_analysis

    def _do() -> dict:
        return audio_analysis.compare(
            audio_analysis.analyze_file(path_a),
            audio_analysis.analyze_file(path_b),
        )

    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
async def analyze_track(index: int = -1, guid: str = "") -> dict:
    """Rend une piste EN ISOLATION (fichier temporaire) puis l'analyse. Restaure
    exactement l'état solo/mute du projet après coup. Piste ciblée par guid/index.

    Utile pour « analyse ma voix » : renvoie des métriques objectives ; toute
    conclusion (présence, compression…) est une HYPOTHÈSE à formuler comme telle.

    Returns: métriques (cf. analyze_audio_file) + track_index/guid, ou {"error": ...}.
    """
    out = os.path.join(tempfile.gettempdir(), f"klody_analyze_{uuid.uuid4().hex}.wav")
    r = await _bridge_call("render_track_isolated", {"index": index, "guid": guid, "out_path": out})
    # `files` calculé AVANT le try -> le finally nettoie le rendu sur TOUTES les
    # sorties (erreur, vide, succès, exception), jamais de fichier temp orphelin.
    files = (r.get("output_files") or []) if isinstance(r, dict) else []
    try:
        if isinstance(r, dict) and r.get("error"):
            return r
        if not files:
            return {"error": "rendu isolé vide (piste muette / sans contenu, ou render échoué)"}
        from klody_mcp import audio_analysis
        metrics = await asyncio.to_thread(audio_analysis.analyze_file, files[0])
        metrics["track_index"] = r.get("track_index")
        metrics["guid"] = r.get("guid")
        return metrics
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        for f in files:
            with contextlib.suppress(OSError):
                os.remove(f)


@mcp.tool()
async def analyze_master() -> dict:
    """Rend le master (mix complet, fichier temporaire) puis l'analyse. Pour garder
    une marge avant mastering : surveiller true_peak_dbfs et lufs_integrated.

    Returns: métriques (cf. analyze_audio_file), ou {"error": ...}.
    """
    out = os.path.join(tempfile.gettempdir(), f"klody_analyze_master_{uuid.uuid4().hex}.wav")
    r = await _bridge_call("render_project", {"out_path": out})
    files = (r.get("output_files") or []) if isinstance(r, dict) else []
    try:
        if isinstance(r, dict) and r.get("error"):
            return r
        if not files:
            return {"error": "rendu master vide (projet silencieux ou render échoué)"}
        from klody_mcp import audio_analysis
        return await asyncio.to_thread(audio_analysis.analyze_file, files[0])
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        for f in files:
            with contextlib.suppress(OSError):
                os.remove(f)


# ---------------------------------------------------------------------------- #
# Entrée                                                                        #
# ---------------------------------------------------------------------------- #


def main() -> None:
    transport = os.getenv("REAPER_MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("REAPER_MCP_PORT", "8089"))
    host = os.getenv("REAPER_MCP_HOST", "127.0.0.1")

    if transport == "http":
        logger.info("REAPER MCP HTTP : http://%s:%d", host, port)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("REAPER MCP stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
