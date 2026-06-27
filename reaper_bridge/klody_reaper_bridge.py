# -*- coding: utf-8 -*-
"""klody_reaper_bridge.py -- pont localhost EXECUTE A L'INTERIEUR de REAPER.

ASCII PUR VOLONTAIRE : REAPER charge le ReaScript Python via un decodeur ASCII.
Tout caractere non-ASCII (accents, tirets longs, box-drawing) leve
UnicodeDecodeError au chargement. Ne PAS reintroduire d'accents ici. La doc
accentuee est dans reaper_bridge/README.md.

Ce script est un ReaScript Python : il tourne dans le processus REAPER et a donc
acces a l'API complete RPR_*. Il ouvre un socket TCP sur 127.0.0.1 et recoit des
commandes JSON (une par ligne) emises par le serveur MCP externe
(klody_mcp/reaper_server.py), les execute via l'API REAPER, et renvoie le
resultat en JSON.

Pourquoi un pont-maison et pas reapy : reapy (python-reapy) est fige depuis
2020-12-29 (0.10.0) et mappe l'API 1:1 -- ecarte. Ce pont n'importe QUE la stdlib
(socket, select, json) : aucune dependance pip dans REAPER, donc robuste quelle
que soit la version de libpython que REAPER charge.

INSTALLATION (manuelle, une fois) -- voir reaper_bridge/README.md :
  1. REAPER > Preferences > Plug-ins > ReaScript : Enable Python + .dylib
     directory /opt/homebrew/Frameworks/Python.framework/Versions/3.11/lib
     et force-dylib libpython3.11.dylib.
  2. Actions > Show action list > ReaScript: Load... > choisir ce fichier.
  3. Lancer l'action. Elle reste en tache de fond (defer) sans figer REAPER.
     Pour redemarrer : terminer le script via la liste d'actions (RPR_atexit
     libere le socket) puis relancer. Relancer pendant qu'il tourne donne un
     message clair "port occupe", jamais un crash.

PROTOCOLE (line-delimited JSON sur TCP) :
  requete  : {"cmd": "get_track_count", "args": {}}\\n
  reponse  : {"ok": true,  "result": {"track_count": 3}}\\n
             {"ok": false, "error": "commande inconnue: 'foo'"}\\n

Securite : modes d'execution (section 5 de la spec DAW agentique). read_only
refuse toute mutation du projet ; assisted/autonomous l'autorisent. Chaque
mutation est encadree dans un bloc Undo REAPER (un Cmd-Z = une operation agent).
AUCUN appel ne sauvegarde le projet (jamais de RPR_Main_SaveProject).
"""

import contextlib
import glob
import json
import math
import os
import re
import select
import socket
import traceback

# -- Detection du contexte REAPER --------------------------------------------
# Dans REAPER, TOUTES les fonctions RPR_* sont injectees EN BLOC dans le
# namespace du script avant execution du corps ; sonder une seule
# (RPR_CountTracks) suffit a deduire la presence des autres (RPR_GetAppVersion,
# RPR_ShowConsoleMsg, RPR_defer, RPR_atexit). defer/atexit ne sont PAS dans
# reaper_python.py : ce sont des fonctions hote injectees au runtime. Hors
# REAPER (test de syntaxe) toutes manquent.
try:
    RPR_CountTracks  # noqa: B018  (sonde de presence du bloc RPR_*)
    _IN_REAPER = True
except NameError:
    _IN_REAPER = False

HOST = "127.0.0.1"
PORT = 9000  # doit matcher REAPER_BRIDGE_PORT cote serveur MCP
PROTOCOL_VERSION = 1
_BACKLOG = 8
_RECV = 4096
_MAX_BUF = 64 * 1024  # garde-fou anti-balloon : ligne sans \n > 64 KiB = on coupe

# Etat serveur (globals : le defer-loop de REAPER re-execute par nom) ---------
_server_sock = None
_clients = {}  # fileno -> [conn, bytearray(buffer)]
_running = False
# Mode d'execution (spec DAW agentique section 5). Defaut 'autonomous' = comportement
# historique (tout autorise) -> aucune regression. 'read_only' refuse toute mutation
# du projet. 'assisted' se comporte comme autonomous cote pont (la confirmation des
# operations destructrices vit cote agent/MCP, phase ulterieure).
_MODE = "autonomous"
_MODES = ("read_only", "assisted", "autonomous")


def _log(msg):
    """Ecrit dans la console REAPER (ou stdout hors REAPER)."""
    line = "[klody_reaper_bridge] %s\n" % msg
    if _IN_REAPER:
        RPR_ShowConsoleMsg(line)  # noqa: F821  (injecte par REAPER)
    else:
        print(line, end="")


# -- Dispatch des commandes ---------------------------------------------------
# Chaque handler recoit un dict `args` et renvoie un dict serialisable, ou leve
# une exception (capturee -> {"ok": false, "error": ...}).


def _cmd_ping(args):
    """Sonde de vivacite -- sert au G1 et au /health du serveur MCP."""
    ver = RPR_GetAppVersion() if _IN_REAPER else "offline"  # noqa: F821
    return {"pong": True, "protocol": PROTOCOL_VERSION, "reaper": ver, "mode": _MODE}


def _cmd_get_track_count(args):
    """Nombre de pistes du projet actif. Lecture pure (RPR_CountTracks).

    L'argument `proj` (index de projet, 0 = projet courant) est optionnel.
    """
    proj = int(args.get("proj", 0))
    return {"track_count": int(RPR_CountTracks(proj))}  # noqa: F821


def _cmd_add_track(args):
    """Insere une piste (effet de bord). NE sauvegarde PAS le projet.

    args: name (str, optionnel), index (int, -1 = a la fin). Hors borne -> fin.
    """
    name = (args.get("name") or "")
    try:
        idx = int(args.get("index", -1))
    except (TypeError, ValueError):
        idx = -1
    count = int(RPR_CountTracks(0))  # noqa: F821
    if idx < 0 or idx > count:
        idx = count  # append
    RPR_InsertTrackAtIndex(idx, True)  # noqa: F821  (wantDefaults=True)
    RPR_TrackList_AdjustWindows(False)  # noqa: F821  (refresh TCP/MCP)
    tr = RPR_GetTrack(0, idx)  # noqa: F821  (toujours : sert au GUID du retour)
    if name:
        RPR_GetSetMediaTrackInfo_String(tr, "P_NAME", name, True)  # noqa: F821
    RPR_UpdateArrange()  # noqa: F821
    return {"inserted_index": idx, "guid": _track_guid(tr), "name": name,
            "track_count": int(RPR_CountTracks(0))}  # noqa: F821


# -- Helpers Phase 3 ----------------------------------------------------------


def _refresh():
    """Rafraichit TCP/MCP + arrange apres une ecriture."""
    RPR_TrackList_AdjustWindows(False)  # noqa: F821
    RPR_UpdateArrange()  # noqa: F821


def _as_bool(v):
    """Truthiness JSON-robuste (le pont est joignable en TCP brut, pas que via MCP).
    bool('false')/bool('0') == True en Python -> on normalise les formes string."""
    if isinstance(v, str):
        return v.strip().lower() not in ("", "false", "0", "no", "off")
    return bool(v)


def _track_at(index):
    """Renvoie (MediaTrack, index_resolu) pour `index` 0-based, ou IndexError.

    NB : renvoie l'INDEX i (pas le nombre de pistes) -- les appelants
    (_resolve_track / _resolve_dest_track) s'en servent comme index resolu dans
    leur reponse. (Une revue adversariale a releve qu'on renvoyait `count` : les
    ops ciblees par index echoaient alors le nombre de pistes au lieu de l'index ;
    la piste mutee `tr` etait correcte, seul l'index renvoye etait faux.)
    """
    count = int(RPR_CountTracks(0))  # noqa: F821
    i = int(index)
    if i < 0 or i >= count:
        msg = "aucune piste dans le projet" if count == 0 else "index %d hors borne (0..%d)" % (i, count - 1)
        raise IndexError(msg)
    return RPR_GetTrack(0, i), i  # noqa: F821


def _track_name(tr):
    """Nom d'une piste. RPR_GetTrackName renvoie (retval, ptr_str, NAME, size) :
    le nom est l'index 2 ; l'index 1 est la chaine pointeur '(MediaTrack*)0x..'
    (non vide) -- NE PAS scanner 'premiere chaine non vide', ca renverrait le ptr.
    """
    res = RPR_GetTrackName(tr, "", 1024)  # noqa: F821
    if isinstance(res, tuple) and len(res) >= 3:
        return res[2] or ""
    return str(res or "")


def _track_guid(tr):
    """GUID stable d'une piste. RPR_GetSetMediaTrackInfo_String(tr,'GUID','',False)
    renvoie (retval, track, 'GUID', guid_str, setNewValue) -> GUID = index 3 (forme
    '{XXXXXXXX-...}'). Identifiant durable (spec DAW agentique 4.3) la ou l'index
    decale a chaque insert/delete de piste."""
    res = RPR_GetSetMediaTrackInfo_String(tr, "GUID", "", False)  # noqa: F821
    if isinstance(res, tuple) and len(res) >= 4:
        return res[3] or ""
    return ""


def _track_by_guid(guid):
    """Retrouve (piste, index) par GUID, ou None si absent du projet courant."""
    g = (guid or "").strip()
    if not g:
        return None
    n = int(RPR_CountTracks(0))  # noqa: F821
    for i in range(n):
        tr = RPR_GetTrack(0, i)  # noqa: F821
        if _track_guid(tr) == g:
            return tr, i
    return None


def _resolve_track(args):
    """Resout la piste cible par GUID (prioritaire) sinon par index 0-based.

    La spec impose le GUID comme identifiant durable ; on accepte les deux pour ne
    rien casser (les appels index-seul marchent tels quels), mais si `guid` est
    fourni il gagne. `index` ou `track_index` selon la commande. Renvoie
    (MediaTrack, index_resolu) comme `_track_at`.
    """
    guid = (args.get("guid") or "").strip()
    if guid:
        hit = _track_by_guid(guid)
        if hit is None:
            raise IndexError("aucune piste avec le GUID %s dans le projet" % guid)
        return hit
    idx = args.get("index")
    if idx is None:
        idx = args.get("track_index")
    return _track_at(idx)


def _db_to_ratio(db):
    return 0.0 if db <= -150.0 else 10.0 ** (db / 20.0)


def _ratio_to_db(r):
    return -150.0 if r <= 0 else 20.0 * math.log10(r)


def _cmd_list_tracks(args):
    """Liste les pistes (lecture pure) : index, nom, volume_db, pan, mute, solo."""
    n = int(RPR_CountTracks(0))  # noqa: F821
    out = []
    for i in range(n):
        tr = RPR_GetTrack(0, i)  # noqa: F821
        out.append({
            "index": i,
            "guid": _track_guid(tr),
            "name": _track_name(tr),
            "volume_db": round(_ratio_to_db(RPR_GetMediaTrackInfo_Value(tr, "D_VOL")), 2),  # noqa: F821
            "pan": round(RPR_GetMediaTrackInfo_Value(tr, "D_PAN"), 3),  # noqa: F821
            "mute": bool(RPR_GetMediaTrackInfo_Value(tr, "B_MUTE")),  # noqa: F821
            "solo": bool(RPR_GetMediaTrackInfo_Value(tr, "I_SOLO")),  # noqa: F821
        })
    return {"count": n, "tracks": out}


def _cmd_get_play_position(args):
    """Etat transport (lecture pure) : position, curseur, play/rec/pause."""
    st = int(RPR_GetPlayState())  # noqa: F821  (bitmask 1=play 2=pause 4=rec)
    return {
        "play_position": round(RPR_GetPlayPosition(), 4),  # noqa: F821
        "edit_cursor": round(RPR_GetCursorPosition(), 4),  # noqa: F821
        "playing": bool(st & 1),
        "paused": bool(st & 2),
        "recording": bool(st & 4),
    }


def _cmd_rename_track(args):
    """Renomme la piste `index`. Effet de bord, sans save."""
    tr, idx = _resolve_track(args)
    name = args.get("name") or ""
    RPR_GetSetMediaTrackInfo_String(tr, "P_NAME", name, True)  # noqa: F821
    _refresh()
    return {"index": idx, "guid": _track_guid(tr), "name": name}


def _cmd_delete_track(args):
    """Supprime la piste `index`. DESTRUCTIF (annulable via Cmd-Z REAPER), sans save."""
    tr, idx = _resolve_track(args)
    guid = _track_guid(tr)  # capture avant suppression (la piste disparait apres)
    RPR_DeleteTrack(tr)  # noqa: F821
    _refresh()
    return {"deleted_index": idx, "guid": guid, "track_count": int(RPR_CountTracks(0))}  # noqa: F821


def _cmd_set_track_volume(args):
    """Regle le volume (dB) de la piste `index`. Effet de bord, sans save."""
    tr, idx = _resolve_track(args)
    db = float(args.get("db", 0.0))
    RPR_SetMediaTrackInfo_Value(tr, "D_VOL", _db_to_ratio(db))  # noqa: F821
    _refresh()
    return {"index": idx, "guid": _track_guid(tr), "volume_db": db}


def _cmd_set_track_pan(args):
    """Regle le pan [-1..1] de la piste `index`. Effet de bord, sans save."""
    tr, idx = _resolve_track(args)
    pan = max(-1.0, min(1.0, float(args.get("pan", 0.0))))
    RPR_SetMediaTrackInfo_Value(tr, "D_PAN", pan)  # noqa: F821
    _refresh()
    return {"index": idx, "guid": _track_guid(tr), "pan": pan}


def _cmd_set_track_mute(args):
    """Mute/unmute la piste `index`. Effet de bord, sans save."""
    tr, idx = _resolve_track(args)
    mute = _as_bool(args.get("mute", True))
    RPR_SetMediaTrackInfo_Value(tr, "B_MUTE", 1.0 if mute else 0.0)  # noqa: F821
    _refresh()
    return {"index": idx, "guid": _track_guid(tr), "mute": mute}


def _cmd_set_track_solo(args):
    """Solo/unsolo la piste `index`. Effet de bord, sans save."""
    tr, idx = _resolve_track(args)
    solo = _as_bool(args.get("solo", True))
    # I_SOLO : 0=off, 1=solo, 2=SIP. On expose un booleen (solo oui/non) -> 1.0.
    RPR_SetMediaTrackInfo_Value(tr, "I_SOLO", 1.0 if solo else 0.0)  # noqa: F821
    _refresh()
    return {"index": idx, "guid": _track_guid(tr), "solo": solo}


def _cmd_transport_play(args):
    """Lance la lecture. Effet de bord (etat transport)."""
    RPR_OnPlayButton()  # noqa: F821
    return {"playing": True}


def _cmd_transport_stop(args):
    """Arrete la lecture/enregistrement. Effet de bord."""
    RPR_OnStopButton()  # noqa: F821
    return {"playing": False}


# -- Helpers Phase 3c (MIDI / render) ----------------------------------------


def _take_is_null(t):
    return (not t) or (isinstance(t, str) and "0x0000000000000000" in t)


def _project_regions():
    """Liste ordonnee des regions du projet : [(pos, end), ...]."""
    total = int(RPR_CountProjectMarkers(0, 0, 0)[0])  # noqa: F821  (markers+regions)
    out = []
    for i in range(total):
        res = RPR_EnumProjectMarkers(i, 0, 0.0, 0.0, "", 0)  # noqa: F821
        if res[2]:  # isrgn
            out.append((res[3], res[4]))
    return out


def _check_out_path(args):
    """Valide out_path (requis + dossier parent existant). Renvoie le chemin."""
    out = (args.get("out_path") or "").strip()
    if not out:
        raise ValueError("out_path requis (chemin complet du fichier de sortie)")
    parent = os.path.dirname(out) or "."
    if not os.path.isdir(parent):
        raise ValueError("dossier de sortie inexistant: %s" % parent)
    return out


def _render_to(out_path, bounds_flag):
    """Configure bornes + sortie, rend (sans dialogue), renvoie les fichiers ecrits.

    REAPER traite RENDER_FILE comme un DOSSIER et RENDER_PATTERN comme le nom (sans
    extension, ajoutee selon le format actif). On scinde donc out_path -> dir+stem.
    """
    d = os.path.dirname(out_path) or "."
    stem = os.path.splitext(os.path.basename(out_path))[0]
    # Pre-supprime toute sortie existante <stem>.* AVANT de rendre. Sinon l'action
    # 41824 detecte le fichier present et ouvre un modal "overwrite?" : ce modal
    # FIGE le thread principal de REAPER -> la boucle defer du pont ne tourne plus
    # -> toute commande socket/MCP part en timeout (faux "REAPER fige", et le
    # watchdog "ReaScript task control" finit par tuer la boucle). Supprimer la
    # cible rend le render direct et sans dialogue, comme prevu.
    for _old in glob.glob(os.path.join(d, glob.escape(stem) + ".*")):
        # best-effort : si la suppression echoue, REAPER incrementera le nom
        with contextlib.suppress(OSError):
            os.remove(_old)
    RPR_GetSetProjectInfo_String(0, "RENDER_FILE", d, True)  # noqa: F821  (dossier)
    RPR_GetSetProjectInfo_String(0, "RENDER_PATTERN", stem, True)  # noqa: F821  (nom)
    RPR_GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", float(bounds_flag), True)  # noqa: F821
    # 41824 = "Render using most recent settings" -> ecrit direct sur disque, SANS
    # dialogue (42230 ouvre le modal "Render to File" -> figerait REAPER).
    RPR_Main_OnCommand(41824, 0)  # noqa: F821
    # Fichier reel = dir/stem.<ext-du-format> ; on le retrouve par glob.
    matches = sorted(glob.glob(os.path.join(d, glob.escape(stem) + ".*")))
    return matches


# -- Handlers Phase 3c --------------------------------------------------------


def _cmd_transport_record(args):
    """Demarre l'enregistrement. RISQUE : ecrit de l'audio sur les pistes armees."""
    RPR_CSurf_OnRecord()  # noqa: F821
    return {"recording": True}


def _cmd_insert_midi_note(args):
    """Insere une note MIDI dans un NOUVEL item MIDI sur la piste. Sans save.

    args: track_index, pitch(0-127), start(sec), length(sec), velocity(1-127),
    channel(0-15). Chaque appel cree son propre item [start, start+length].
    """
    tr, ti = _resolve_track(args)
    pitch = max(0, min(127, int(args.get("pitch", 60))))
    start = float(args.get("start", 0.0))
    length = max(0.001, float(args.get("length", 0.5)))
    vel = max(1, min(127, int(args.get("velocity", 96))))
    chan = max(0, min(15, int(args.get("channel", 0))))
    end = start + length
    item = RPR_CreateNewMIDIItemInProj(tr, start, end, 0)[0]  # noqa: F821  (0=temps, pas QN)
    take = RPR_GetActiveTake(item)  # noqa: F821
    if _take_is_null(take):
        raise RuntimeError("impossible de creer l'item MIDI")
    sppq = RPR_MIDI_GetPPQPosFromProjTime(take, start)  # noqa: F821
    eppq = RPR_MIDI_GetPPQPosFromProjTime(take, end)  # noqa: F821
    RPR_MIDI_InsertNote(take, False, False, sppq, eppq, chan, pitch, vel, False)  # noqa: F821
    RPR_MIDI_Sort(take)  # noqa: F821
    _refresh()
    return {"track_index": ti, "guid": _track_guid(tr), "pitch": pitch, "start": start,
            "length": length, "velocity": vel, "channel": chan}


def _cmd_insert_midi_notes(args):
    """Insere PLUSIEURS notes MIDI dans UN SEUL item sur la piste. Sans save.

    args: track_index, notes = [{pitch, start, length, velocity?, channel?}, ...]
    -- forme exacte des `events` de klody_music_server.melodie_vers_midi. L'item
    couvre [min(start), max(start+length)] : une melodie = UN item (pas N items
    comme insert_midi_note appele en boucle). noSort=True a chaque insert puis un
    seul MIDI_Sort a la fin (insertion par lot correcte + moins de tri).
    """
    tr, ti = _resolve_track(args)
    notes = args.get("notes")
    if not isinstance(notes, list) or not notes:
        raise ValueError("notes doit etre une liste non vide de {pitch,start,length,...}")
    norm = []
    item_start = None
    item_end = None
    for n in notes:
        if not isinstance(n, dict):
            raise ValueError("chaque note doit etre un objet {pitch,start,length,...}")
        pitch = max(0, min(127, int(n.get("pitch", 60))))
        start = float(n.get("start", 0.0))
        length = max(0.001, float(n.get("length", 0.5)))
        vel = max(1, min(127, int(n.get("velocity", 96))))
        chan = max(0, min(15, int(n.get("channel", 0))))
        end = start + length
        norm.append((start, end, chan, pitch, vel))
        item_start = start if item_start is None else min(item_start, start)
        item_end = end if item_end is None else max(item_end, end)
    item = RPR_CreateNewMIDIItemInProj(tr, item_start, item_end, 0)[0]  # noqa: F821  (0=temps)
    take = RPR_GetActiveTake(item)  # noqa: F821
    if _take_is_null(take):
        raise RuntimeError("impossible de creer l'item MIDI")
    for (start, end, chan, pitch, vel) in norm:
        sppq = RPR_MIDI_GetPPQPosFromProjTime(take, start)  # noqa: F821
        eppq = RPR_MIDI_GetPPQPosFromProjTime(take, end)  # noqa: F821
        # noSort=True : on differe le tri jusqu'au MIDI_Sort final (insertion par lot).
        RPR_MIDI_InsertNote(take, False, False, sppq, eppq, chan, pitch, vel, True)  # noqa: F821
    RPR_MIDI_Sort(take)  # noqa: F821
    _refresh()
    return {"track_index": ti, "guid": _track_guid(tr), "note_count": len(norm),
            "item_start": round(item_start, 4), "item_end": round(item_end, 4)}


def _cmd_list_midi_notes(args):
    """Liste les notes MIDI d'un item (lecture pure).

    args: track_index, item_index (defaut 0). Renvoie pitch/start/length/vel/chan.
    """
    tr, ti = _resolve_track(args)
    item_index = int(args.get("item_index", 0))
    n_items = int(RPR_CountTrackMediaItems(tr))  # noqa: F821
    if n_items == 0:
        return {"track_index": ti, "item_index": item_index, "note_count": 0, "notes": []}
    if item_index < 0 or item_index >= n_items:
        raise IndexError("item_index %d hors borne (0..%d)" % (item_index, n_items - 1))
    item = RPR_GetTrackMediaItem(tr, item_index)  # noqa: F821
    take = RPR_GetActiveTake(item)  # noqa: F821
    if _take_is_null(take):
        return {"track_index": ti, "item_index": item_index, "note_count": 0, "notes": []}
    notecnt = int(RPR_MIDI_CountEvts(take, 0, 0, 0)[2])  # noqa: F821
    notes = []
    for i in range(notecnt):
        nd = RPR_MIDI_GetNote(take, i, 0, 0, 0, 0, 0, 0, 0)  # noqa: F821
        # (r, take, idx, selected, muted, startppq, endppq, chan, pitch, vel)
        st = RPR_MIDI_GetProjTimeFromPPQPos(take, nd[5])  # noqa: F821
        en = RPR_MIDI_GetProjTimeFromPPQPos(take, nd[6])  # noqa: F821
        notes.append({"index": i, "pitch": nd[8], "start": round(st, 4),
                      "length": round(en - st, 4), "velocity": nd[9],
                      "channel": nd[7], "muted": bool(nd[4])})
    return {"track_index": ti, "item_index": item_index, "note_count": notecnt, "notes": notes}


def _cmd_render_region(args):
    """Rend une region en fichier audio (ecrit sur disque). out_path REQUIS.

    Utilise les derniers reglages de format REAPER ; ne sauvegarde PAS le .rpp.
    """
    out = _check_out_path(args)
    ri = int(args.get("region_index", 0))
    regions = _project_regions()
    if ri < 0 or ri >= len(regions):
        raise IndexError("region_index %d hors borne (%d region(s) dans le projet)" % (ri, len(regions)))
    pos, end = regions[ri]
    RPR_GetSet_LoopTimeRange(True, False, pos, end, False)  # noqa: F821  (selection = region)
    files = _render_to(out, 2)  # 2 = time selection ; rend (41824, sans dialogue)
    return {"region_index": ri, "start": round(pos, 4), "end": round(end, 4),
            "rendered": bool(files), "output_files": files}


def _cmd_render_project(args):
    """Rend le projet entier en fichier audio (ecrit sur disque). out_path REQUIS.

    Derniers reglages de format REAPER ; ne sauvegarde PAS le .rpp.
    """
    out = _check_out_path(args)
    files = _render_to(out, 1)  # 1 = projet entier ; rend (41824, sans dialogue)
    return {"rendered": bool(files), "output_files": files}


def _cmd_render_track_isolated(args):
    """Rend UNE piste en isolation vers out_path (pour analyse audio, P3 spec 9).

    Sauvegarde l'etat solo/mute de TOUTES les pistes, solo la cible (le master ne
    contient alors qu'elle), demute la cible (le mute prime sur le solo), rend, puis
    RESTAURE EXACTEMENT l'etat initial (jamais laisser un solo/mute different apres
    l'analyse). Ecrit un fichier ; l'etat NET du projet est inchange (pas de mutation
    -> ni bloc Undo ni blocage read_only ; c'est un primitive d'analyse comme render).
    """
    tr, idx = _resolve_track(args)
    out = _check_out_path(args)
    n = int(RPR_CountTracks(0))  # noqa: F821
    tracks = [RPR_GetTrack(0, i) for i in range(n)]  # noqa: F821
    state = [(RPR_GetMediaTrackInfo_Value(t, "I_SOLO"),  # noqa: F821
              RPR_GetMediaTrackInfo_Value(t, "B_MUTE")) for t in tracks]  # noqa: F821
    try:
        for j, t in enumerate(tracks):
            RPR_SetMediaTrackInfo_Value(t, "I_SOLO", 1.0 if j == idx else 0.0)  # noqa: F821
            if j == idx:
                RPR_SetMediaTrackInfo_Value(t, "B_MUTE", 0.0)  # noqa: F821  (mute > solo)
        files = _render_to(out, 1)  # projet entier ; solo => seule la cible sonne
    finally:
        for t, (s, m) in zip(tracks, state):
            RPR_SetMediaTrackInfo_Value(t, "I_SOLO", s)  # noqa: F821
            RPR_SetMediaTrackInfo_Value(t, "B_MUTE", m)  # noqa: F821
        _refresh()
    return {"track_index": idx, "guid": _track_guid(tr), "out_path": out,
            "rendered": bool(files), "output_files": files}


# -- Handlers P1 (snapshot / undo / modes) -----------------------------------
# Surete avant puissance (spec DAW agentique sections 4.3/4.4/5/6) : lecture
# d'etat riche, reversibilite par bloc Undo, garde de mode. Aucun n'ajoute de
# surface musicale neuve ; tous reutilisent des RPR_* deja verifiees dans le stub.


def _cmd_get_project_snapshot(args):
    """Snapshot lecture pure du projet (spec DAW agentique section 6).

    args.detail : 'summary' (projet seul) | 'standard' (defaut : + pistes) |
    'full' (+ compteurs fx/items par piste). Ne modifie jamais le projet.
    """
    detail = str(args.get("detail", "standard")).lower()
    st = int(RPR_GetPlayState())  # noqa: F821  (bitmask 1=play 2=pause 4=rec)
    proj = {
        "tempo": round(RPR_Master_GetTempo(), 4),  # noqa: F821
        "edit_cursor": round(RPR_GetCursorPosition(), 4),  # noqa: F821
        "play_position": round(RPR_GetPlayPosition(), 4),  # noqa: F821
        "playing": bool(st & 1),
        "paused": bool(st & 2),
        "recording": bool(st & 4),
        "track_count": int(RPR_CountTracks(0)),  # noqa: F821
    }
    # Champs au format swig moins courant -> sous garde individuelle : un build qui
    # exposerait une autre signature degrade a None plutot que de casser TOUT le
    # snapshot (fail-soft, cf. discipline no-regression).
    try:
        proj["name"] = RPR_GetProjectName(0, "", 512)[1] or ""  # noqa: F821
    except Exception:  # noqa: BLE001
        proj["name"] = None
    try:
        ts = RPR_TimeMap_GetTimeSigAtTime(0, 0.0, 0, 0, 0)  # noqa: F821
        proj["time_signature"] = "%d/%d" % (int(ts[2]), int(ts[3]))
    except Exception:  # noqa: BLE001
        proj["time_signature"] = None
    try:
        sr = int(RPR_GetSetProjectInfo(0, "PROJECT_SRATE", 0, False))  # noqa: F821
        proj["sample_rate"] = sr or None  # 0 = "suit le peripherique audio"
    except Exception:  # noqa: BLE001
        proj["sample_rate"] = None

    snap = {"project": proj}
    if detail == "summary":
        return snap
    full = (detail == "full")
    n = int(RPR_CountTracks(0))  # noqa: F821
    tracks = []
    for i in range(n):
        tr = RPR_GetTrack(0, i)  # noqa: F821
        t = {
            "index": i,
            "guid": _track_guid(tr),
            "name": _track_name(tr),
            "volume_db": round(_ratio_to_db(RPR_GetMediaTrackInfo_Value(tr, "D_VOL")), 2),  # noqa: F821
            "pan": round(RPR_GetMediaTrackInfo_Value(tr, "D_PAN"), 3),  # noqa: F821
            "mute": bool(RPR_GetMediaTrackInfo_Value(tr, "B_MUTE")),  # noqa: F821
            "solo": bool(RPR_GetMediaTrackInfo_Value(tr, "I_SOLO")),  # noqa: F821
        }
        if full:
            t["fx_count"] = int(RPR_TrackFX_GetCount(tr))  # noqa: F821
            t["item_count"] = int(RPR_CountTrackMediaItems(tr))  # noqa: F821
        tracks.append(t)
    snap["tracks"] = tracks
    return snap


def _cmd_undo(args):
    """Annule le dernier point d'annulation REAPER (operation agent encadree).

    Chaque mutation du pont est encadree dans un bloc Undo (un Cmd-Z = une
    operation). Renvoie le libelle de ce qui A ETE annule, ou rien a annuler.
    """
    label = RPR_Undo_CanUndo2(0) if _IN_REAPER else ""  # noqa: F821  ('' si rien)
    if not label:
        return {"undone": False, "label": None, "reason": "rien a annuler"}
    RPR_Undo_DoUndo2(0)  # noqa: F821
    _refresh()
    return {"undone": True, "label": label}


def _cmd_redo(args):
    """Retablit le dernier point annule. Renvoie le libelle retabli, ou rien."""
    label = RPR_Undo_CanRedo2(0) if _IN_REAPER else ""  # noqa: F821
    if not label:
        return {"redone": False, "label": None, "reason": "rien a retablir"}
    RPR_Undo_DoRedo2(0)  # noqa: F821
    _refresh()
    return {"redone": True, "label": label}


def _cmd_set_mode(args):
    """Regle le mode d'execution du pont : read_only | assisted | autonomous.

    read_only bloque toute commande qui modifie le projet (garde-fou dur, cf.
    _WRITE_CMDS). Defaut autonomous (historique). Renvoie le mode effectif.
    """
    global _MODE
    m = str(args.get("mode", "")).strip().lower()
    if m not in _MODES:
        raise ValueError("mode invalide %r (attendu: %s)" % (m, ", ".join(_MODES)))
    _MODE = m
    return {"mode": _MODE}


def _cmd_get_mode(args):
    """Renvoie le mode d'execution courant + la liste des modes (lecture pure)."""
    return {"mode": _MODE, "modes": list(_MODES)}


# -- Handlers P2 (FX / routing / markers) ------------------------------------
# Portee musicale (spec DAW agentique 7.3 / 7.6 / 7.8). Toutes les sigs RPR_*
# verifiees dans reaper_python.py avant code. FX/sends/markers = mutations ->
# encadrees Undo (cf. _UNDO_LABELS) ; get_fx_params est lecture pure.


def _fx_name(tr, i):
    """Nom de l'effet i. TrackFX_GetFXName -> (r,tr,fx,name,sz) ; name = index 3."""
    res = RPR_TrackFX_GetFXName(tr, i, "", 256)  # noqa: F821
    if isinstance(res, tuple) and len(res) >= 4:
        return res[3] or ""
    return ""


def _resolve_fx(tr, fx):
    """Resout un effet : entier/chaine-numerique = index ; chaine = 1er effet dont
    le nom CONTIENT la chaine (insensible a la casse). Leve si absent/hors borne."""
    # Garde tot : fx absent (None) -> str(None)='none' passerait le test de nom et
    # chercherait un effet 'none' (erreur trompeuse). On exige fx explicite.
    if fx is None or (isinstance(fx, str) and not fx.strip()):
        raise ValueError("fx requis (index ou nom d'effet)")
    cnt = int(RPR_TrackFX_GetCount(tr))  # noqa: F821
    s = fx
    if isinstance(s, str) and s.strip().lstrip("-").isdigit():
        s = int(s.strip())
    if isinstance(s, int) and not isinstance(s, bool):
        if s < 0 or s >= cnt:
            raise IndexError("fx index %d hors borne (%d effet(s) sur la piste)" % (s, cnt))
        return s
    needle = str(fx).strip().lower()
    if not needle:
        raise ValueError("fx requis (index ou nom d'effet)")
    for i in range(cnt):
        if needle in _fx_name(tr, i).lower():
            return i
    raise IndexError("aucun effet dont le nom contient %r (%d effet(s))" % (fx, cnt))


def _resolve_param(tr, fxi, param):
    """Resout un parametre d'effet : index numerique, ou 1re correspondance de nom."""
    if param is None or (isinstance(param, str) and not param.strip()):
        raise ValueError("param requis (index ou nom)")
    npar = int(RPR_TrackFX_GetNumParams(tr, fxi))  # noqa: F821
    s = param
    if isinstance(s, str) and s.strip().lstrip("-").isdigit():
        s = int(s.strip())
    if isinstance(s, int) and not isinstance(s, bool):
        if s < 0 or s >= npar:
            raise IndexError("param index %d hors borne (%d parametre(s))" % (s, npar))
        return s
    needle = str(param).strip().lower()
    if not needle:
        raise ValueError("param requis (index ou nom)")
    for p in range(npar):
        nm = RPR_TrackFX_GetParamName(tr, fxi, p, "", 256)[4] or ""  # noqa: F821  (name=idx4)
        if needle in nm.lower():
            return p
    raise IndexError("aucun parametre dont le nom contient %r" % param)


def _cmd_add_fx(args):
    """Ajoute (ou retrouve) un effet par nom sur une piste. Idempotent : query
    d'abord (TrackFX_AddByName instantiate=0), cree seulement si absent (-1). REAPER
    resout le nom (sous-chaine). Erreur claire si l'effet n'est pas installe -- on ne
    suppose JAMAIS un plugin present (spec 7.6)."""
    tr, idx = _resolve_track(args)
    name = (args.get("name") or "").strip()
    if not name:
        raise ValueError("name d'effet requis")
    fxi = int(RPR_TrackFX_AddByName(tr, name, False, 0))  # noqa: F821  (query existant)
    created = False
    if fxi < 0:
        fxi = int(RPR_TrackFX_AddByName(tr, name, False, -1))  # noqa: F821  (cree)
        created = fxi >= 0
    if fxi < 0:
        raise RuntimeError("effet introuvable: %r (est-il installe dans REAPER ?)" % name)
    _refresh()
    return {"track_index": idx, "guid": _track_guid(tr), "fx_index": fxi,
            "fx_name": _fx_name(tr, fxi), "created": created}


def _cmd_remove_fx(args):
    """Supprime un effet (index ou nom) d'une piste."""
    tr, idx = _resolve_track(args)
    fxi = _resolve_fx(tr, args.get("fx"))
    nm = _fx_name(tr, fxi)
    ok = bool(RPR_TrackFX_Delete(tr, fxi))  # noqa: F821
    _refresh()
    return {"track_index": idx, "guid": _track_guid(tr), "removed_fx": nm, "ok": ok}


def _cmd_bypass_fx(args):
    """Bypass (True) ou reactive (False) un effet (enabled = not bypass)."""
    tr, idx = _resolve_track(args)
    fxi = _resolve_fx(tr, args.get("fx"))
    bypass = _as_bool(args.get("bypass", True))
    RPR_TrackFX_SetEnabled(tr, fxi, not bypass)  # noqa: F821
    _refresh()
    return {"track_index": idx, "guid": _track_guid(tr), "fx_index": fxi,
            "fx_name": _fx_name(tr, fxi), "bypassed": bypass,
            "enabled": bool(RPR_TrackFX_GetEnabled(tr, fxi))}  # noqa: F821


def _cmd_get_fx_params(args):
    """Lecture pure. Sans 'fx' : liste les effets (index, nom, enabled). Avec 'fx'
    (index/nom) : liste ses parametres (index, nom, valeur normalisee 0..1, valeur
    reelle)."""
    tr, idx = _resolve_track(args)
    cnt = int(RPR_TrackFX_GetCount(tr))  # noqa: F821
    fx = args.get("fx")
    if fx is None or fx == "":
        fxs = [{"fx_index": i, "fx_name": _fx_name(tr, i),
                "enabled": bool(RPR_TrackFX_GetEnabled(tr, i))} for i in range(cnt)]  # noqa: F821
        return {"track_index": idx, "guid": _track_guid(tr), "fx_count": cnt, "fx": fxs}
    fxi = _resolve_fx(tr, fx)
    npar = int(RPR_TrackFX_GetNumParams(tr, fxi))  # noqa: F821
    params = []
    for p in range(npar):
        pname = RPR_TrackFX_GetParamName(tr, fxi, p, "", 256)[4] or ""  # noqa: F821
        norm = round(float(RPR_TrackFX_GetParamNormalized(tr, fxi, p)), 5)  # noqa: F821
        raw = RPR_TrackFX_GetParam(tr, fxi, p, 0, 0)  # noqa: F821  (val, tr, fx, param, min, max)
        params.append({"param_index": p, "name": pname, "normalized": norm,
                       "value": round(float(raw[0]), 5)})
    return {"track_index": idx, "guid": _track_guid(tr), "fx_index": fxi,
            "fx_name": _fx_name(tr, fxi), "param_count": npar, "params": params}


def _cmd_set_fx_param(args):
    """Regle un parametre d'effet. Effet par index/nom, parametre par index/nom.
    Valeur NORMALISEE 0..1 par defaut (surface exposee par REAPER, spec 7.6) ;
    raw=True pour une valeur en unites natives du plugin."""
    tr, idx = _resolve_track(args)
    fxi = _resolve_fx(tr, args.get("fx"))
    p = _resolve_param(tr, fxi, args.get("param"))
    val = float(args.get("value", 0.0))
    if _as_bool(args.get("raw", False)):
        RPR_TrackFX_SetParam(tr, fxi, p, val)  # noqa: F821
    else:
        val = max(0.0, min(1.0, val))
        RPR_TrackFX_SetParamNormalized(tr, fxi, p, val)  # noqa: F821
    _refresh()
    pname = RPR_TrackFX_GetParamName(tr, fxi, p, "", 256)[4] or ""  # noqa: F821
    return {"track_index": idx, "guid": _track_guid(tr), "fx_index": fxi,
            "fx_name": _fx_name(tr, fxi), "param_index": p, "param_name": pname,
            "normalized": round(float(RPR_TrackFX_GetParamNormalized(tr, fxi, p)), 5)}  # noqa: F821


def _resolve_dest_track(args):
    """Cible 'dest' d'un send : par dest_guid (prioritaire) sinon dest_index."""
    guid = (args.get("dest_guid") or "").strip()
    if guid:
        hit = _track_by_guid(guid)
        if hit is None:
            raise IndexError("aucune piste avec le GUID dest %s" % guid)
        return hit
    return _track_at(args.get("dest_index"))


def _track_addr(tr):
    """Adresse entiere d'un pointeur de piste ('(MediaTrack*)0x..'). Sert a comparer
    une piste a un P_DESTTRACK (double) pour l'idempotence des sends. Sur macOS les
    adresses utilisateur tiennent sous 2**53 -> int(double) exact."""
    m = re.search(r"0x([0-9a-fA-F]+)", str(tr))
    return int(m.group(1), 16) if m else None


def _send_index_to(src_tr, dest_tr):
    """Index du send src->dest s'il existe deja, sinon -1 (idempotence des sends)."""
    dest_addr = _track_addr(dest_tr)
    if dest_addr is None:
        return -1  # comparaison impossible -> on laissera creer
    n = int(RPR_GetTrackNumSends(src_tr, 0))  # noqa: F821  (0 = sends)
    for i in range(n):
        d = RPR_GetTrackSendInfo_Value(src_tr, 0, i, "P_DESTTRACK")  # noqa: F821
        # int(d) = adresse du pointeur dest (dest_addr est garanti non nul : on a
        # renvoye -1 plus haut si _track_addr a echoue). Pas de garde `and d` :
        # int(0.0)=0 != dest_addr de toute facon, et `and d` masquerait un compare.
        if int(d) == dest_addr:
            return i
    return -1


def _cmd_create_send(args):
    """Cree un send src -> dest (idempotent : pas de doublon vers la meme
    destination, spec 4.5). src par guid/index, dest par dest_guid/dest_index.
    vol_db / pan optionnels."""
    src_tr, src_idx = _resolve_track(args)
    dest_tr, dest_idx = _resolve_dest_track(args)
    if _track_addr(src_tr) is not None and _track_addr(src_tr) == _track_addr(dest_tr):
        raise ValueError("src et dest sont la meme piste")
    existing = _send_index_to(src_tr, dest_tr)
    if existing >= 0:
        send_idx, created = existing, False
    else:
        send_idx = int(RPR_CreateTrackSend(src_tr, dest_tr))  # noqa: F821
        created = send_idx >= 0
        if not created:
            raise RuntimeError("echec de creation du send")
    if args.get("vol_db") is not None:
        RPR_SetTrackSendInfo_Value(src_tr, 0, send_idx, "D_VOL", _db_to_ratio(float(args["vol_db"])))  # noqa: F821
    if args.get("pan") is not None:
        RPR_SetTrackSendInfo_Value(src_tr, 0, send_idx, "D_PAN", max(-1.0, min(1.0, float(args["pan"]))))  # noqa: F821
    _refresh()
    return {"src_index": src_idx, "src_guid": _track_guid(src_tr),
            "dest_index": dest_idx, "dest_guid": _track_guid(dest_tr),
            "send_index": send_idx, "created": created}


def _cmd_create_bus(args):
    """Cree (ou retrouve) une piste-bus par nom. Idempotent : si une piste porte deja
    ce nom exact, la renvoie sans en creer une seconde (spec 4.5)."""
    name = (args.get("name") or "").strip()
    if not name:
        raise ValueError("name requis pour le bus")
    n = int(RPR_CountTracks(0))  # noqa: F821
    for i in range(n):
        tr = RPR_GetTrack(0, i)  # noqa: F821
        if _track_name(tr) == name:
            return {"index": i, "guid": _track_guid(tr), "name": name, "created": False}
    RPR_InsertTrackAtIndex(n, True)  # noqa: F821  (append)
    RPR_TrackList_AdjustWindows(False)  # noqa: F821
    tr = RPR_GetTrack(0, n)  # noqa: F821
    RPR_GetSetMediaTrackInfo_String(tr, "P_NAME", name, True)  # noqa: F821
    RPR_UpdateArrange()  # noqa: F821
    return {"index": n, "guid": _track_guid(tr), "name": name, "created": True}


# Tolerance de position pour la dedup marqueur/region : 1 microseconde. Assez
# fine pour ne PAS fusionner deux reperes distincts proches (< 0.1 echantillon a
# 96 kHz), assez large pour absorber le bruit ULP d'un aller-retour float JSON ->
# un re-appel a l'identique (meme position) est bien vu comme doublon.
_MARKER_EPS = 1e-6


def _marker_exists(isrgn, pos, end):
    """True si un marqueur/region de meme type est deja a ~cette position (a
    _MARKER_EPS pres). Le nom n'est PAS expose de facon fiable par
    EnumProjectMarkers dans ce build -> dedup par type+position (spec 4.5)."""
    total = int(RPR_CountProjectMarkers(0, 0, 0)[0])  # noqa: F821
    for i in range(total):
        res = RPR_EnumProjectMarkers(i, 0, 0.0, 0.0, "", 0)  # noqa: F821  (res[2]=isrgn,[3]=pos,[4]=end)
        if bool(res[2]) != bool(isrgn):
            continue
        if abs(res[3] - pos) < _MARKER_EPS and (not isrgn or abs(res[4] - end) < _MARKER_EPS):
            return True
    return False


def _marker_color(color):
    """Entier RGB -> valeur couleur REAPER (drapeau 0x1000000 = custom) ; 0 = defaut."""
    return (int(color) | 0x1000000) if color else 0


def _cmd_add_marker(args):
    """Ajoute un marqueur a `position` (sec). Idempotent par type+position. color
    optionnel (entier RGB ; 0 = couleur par defaut)."""
    pos = float(args.get("position", 0.0))
    name = args.get("name") or ""
    if _marker_exists(False, pos, pos):
        return {"position": round(pos, 4), "name": name, "created": False}
    mid = int(RPR_AddProjectMarker2(0, False, pos, 0.0, name, -1, _marker_color(args.get("color", 0))))  # noqa: F821
    _refresh()
    return {"position": round(pos, 4), "name": name, "marker_id": mid, "created": True}


def _cmd_add_region(args):
    """Ajoute une region [start, end] (sec). Idempotent par type+position. color
    optionnel."""
    start = float(args.get("start", 0.0))
    end = float(args.get("end", start))
    if end <= start:
        raise ValueError("end doit etre > start")
    name = args.get("name") or ""
    if _marker_exists(True, start, end):
        return {"start": round(start, 4), "end": round(end, 4), "name": name, "created": False}
    rid = int(RPR_AddProjectMarker2(0, True, start, end, name, -1, _marker_color(args.get("color", 0))))  # noqa: F821
    _refresh()
    return {"start": round(start, 4), "end": round(end, 4), "name": name,
            "region_id": rid, "created": True}


# Table de dispatch. PHASE 2 = uniquement ping + get_track_count.
# PHASE 3 (effets de bord lourds) : ajouter ici en regard des outils MCP, mais
# TOUJOURS sans sauvegarde implicite du projet. TODO cibles cote serveur MCP.
_DISPATCH = {
    "ping": _cmd_ping,
    "get_track_count": _cmd_get_track_count,
    "list_tracks": _cmd_list_tracks,
    "get_play_position": _cmd_get_play_position,
    "add_track": _cmd_add_track,
    "rename_track": _cmd_rename_track,
    "delete_track": _cmd_delete_track,
    "set_track_volume": _cmd_set_track_volume,
    "set_track_pan": _cmd_set_track_pan,
    "set_track_mute": _cmd_set_track_mute,
    "set_track_solo": _cmd_set_track_solo,
    "transport_play": _cmd_transport_play,
    "transport_stop": _cmd_transport_stop,
    "transport_record": _cmd_transport_record,
    "insert_midi_note": _cmd_insert_midi_note,
    "insert_midi_notes": _cmd_insert_midi_notes,
    "list_midi_notes": _cmd_list_midi_notes,
    "render_region": _cmd_render_region,
    "render_project": _cmd_render_project,
    "render_track_isolated": _cmd_render_track_isolated,
    "get_project_snapshot": _cmd_get_project_snapshot,
    "undo": _cmd_undo,
    "redo": _cmd_redo,
    "set_mode": _cmd_set_mode,
    "get_mode": _cmd_get_mode,
    "add_fx": _cmd_add_fx,
    "remove_fx": _cmd_remove_fx,
    "bypass_fx": _cmd_bypass_fx,
    "get_fx_params": _cmd_get_fx_params,
    "set_fx_param": _cmd_set_fx_param,
    "create_send": _cmd_create_send,
    "create_bus": _cmd_create_bus,
    "add_marker": _cmd_add_marker,
    "add_region": _cmd_add_region,
}

# Commandes qui modifient l'ETAT DU PROJET -> encadrees dans un bloc Undo REAPER
# (un seul Cmd-Z annule l'operation agent ; spec DAW agentique 4.4) ET bloquees en
# mode read_only. Lectures, transport play/stop et render_* (ecriture fichier sur
# un out_path explicite, pas l'etat projet -> sert l'analyse) en sont exclus.
_UNDO_LABELS = {
    "add_track": "ajout piste",
    "rename_track": "renommage piste",
    "delete_track": "suppression piste",
    "set_track_volume": "volume piste",
    "set_track_pan": "pan piste",
    "set_track_mute": "mute piste",
    "set_track_solo": "solo piste",
    "insert_midi_note": "note MIDI",
    "insert_midi_notes": "melodie MIDI",
    "add_fx": "ajout effet",
    "remove_fx": "suppression effet",
    "bypass_fx": "bypass effet",
    "set_fx_param": "parametre effet",
    "create_send": "creation send",
    "create_bus": "creation bus",
    "add_marker": "ajout marqueur",
    "add_region": "ajout region",
}
# transport_record ecrit de l'audio dans le projet -> bloque aussi en read_only
# (pas d'undo : c'est un effet transport, pas une mutation d'etat encadrable).
_WRITE_CMDS = set(_UNDO_LABELS) | {"transport_record"}


def _handle_line(raw):
    """Parse une ligne JSON, dispatche, renvoie l'enveloppe reponse (dict)."""
    try:
        req = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": "JSON invalide: %s" % exc}
    cmd = req.get("cmd")
    args = req.get("args") or {}
    handler = _DISPATCH.get(cmd)
    if handler is None:
        return {"ok": False, "error": "commande inconnue: %r" % cmd}
    if _MODE == "read_only" and cmd in _WRITE_CMDS:
        return {"ok": False,
                "error": "mode read_only : commande '%s' (mutation) refusee" % cmd}
    label = _UNDO_LABELS.get(cmd)
    try:
        if label and _IN_REAPER:
            # Encadre la mutation dans UN bloc Undo : un seul Cmd-Z annule toute
            # l'operation agent (reversibilite, spec DAW agentique 4.4). finally
            # garantit la fermeture du bloc meme si le handler leve une exception.
            RPR_Undo_BeginBlock2(0)  # noqa: F821
            try:
                result = handler(args)
            finally:
                RPR_Undo_EndBlock2(0, "klody: " + label, -1)  # noqa: F821
        else:
            result = handler(args)
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001  (jamais de crash silencieux)
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def _send(conn, obj):
    # INVARIANT protocole : 1 reponse = 1 ligne. json.dumps echappe deja tout
    # '\n' interne aux chaines en `\n` (2 caracteres) -> la serialisation ne
    # contient jamais de saut de ligne litteral, meme pour un traceback Phase 3.
    # Best-effort : si le client a deja ferme, rien a faire (envoi perdu, attendu).
    with contextlib.suppress(OSError):
        conn.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"))


def _drain_client(fileno):
    """Lit ce qui est dispo sur un client, traite les lignes completes."""
    conn, buf = _clients[fileno]
    try:
        chunk = conn.recv(_RECV)
    except OSError:
        chunk = b""
    if not chunk:  # client ferme
        _close_client(fileno)
        return
    buf.extend(chunk)
    if len(buf) > _MAX_BUF and b"\n" not in buf:  # ligne geante sans fin -> coupe
        _send(conn, {"ok": False, "error": "ligne > 64KiB sans '\\n' -- connexion fermee"})
        _close_client(fileno)
        return
    while b"\n" in buf:
        line, _, rest = buf.partition(b"\n")
        del buf[:]
        buf.extend(rest)
        if line.strip():
            _send(conn, _handle_line(line.decode("utf-8", "replace")))


def _close_client(fileno):
    pair = _clients.pop(fileno, None)
    if pair:
        # Best-effort : fermer un socket deja mort peut lever, sans consequence.
        with contextlib.suppress(OSError):
            pair[0].close()


def _prune_dead_clients():
    """Un seul fd client en erreur fait echouer TOUT le select et figerait le
    pont (faux 'REAPER injoignable' cote MCP). On isole et ferme les morts."""
    for fileno in list(_clients):
        conn = _clients[fileno][0]
        try:
            select.select([conn], [], [], 0)
        except (OSError, ValueError):
            _close_client(fileno)


def _tick():
    """Un cycle non bloquant : accepte + lit. select timeout=0 = ne fige rien."""
    rlist = [_server_sock] + [p[0] for p in _clients.values()]
    try:
        readable, _, _ = select.select(rlist, [], [], 0)
    except (OSError, ValueError):
        _prune_dead_clients()  # purge le(s) fd fautif(s), garde le listener vivant
        return
    for s in readable:
        if s is _server_sock:
            # Best-effort : un accept rate (client parti avant) -> on saute ce cycle.
            with contextlib.suppress(OSError):
                conn, _ = _server_sock.accept()
                conn.setblocking(False)
                _clients[conn.fileno()] = [conn, bytearray()]
        else:
            _drain_client(s.fileno())


def _start():
    """Bind + listen. Renvoie True si actif, False si port occupe (pas de crash)."""
    global _server_sock, _running
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, PORT))
    except OSError as exc:
        sock.close()
        _log("port %d occupe (%s) -- une instance du pont tourne deja ? "
             "Termine-la via la liste d'actions avant de relancer." % (PORT, exc))
        return False
    sock.listen(_BACKLOG)
    sock.setblocking(False)
    _server_sock = sock
    _running = True
    # Le re-arm defer evalue la CHAINE "_serve()" au cycle suivant : on publie le
    # nom dans __main__ pour qu'il resolve quel que soit le namespace d'eval du
    # build REAPER. Une fois _serve trouve, son __globals__ (ce module) resout
    # _tick/_running/_clients/etc. -> pas de mort muette de la boucle.
    try:
        import __main__
        __main__._serve = _serve
        __main__._stop = _stop
    except Exception:  # noqa: BLE001
        pass
    # Libere le socket a la terminaison de l'action (atexit hote, comme defer).
    if _IN_REAPER and "RPR_atexit" in globals():
        try:
            RPR_atexit("_stop()")  # noqa: F821
        except Exception:  # noqa: BLE001
            pass
    _log("pont actif sur %s:%d (protocole v%d)" % (HOST, PORT, PROTOCOL_VERSION))
    return True


def _stop():
    global _running
    _running = False
    for fileno in list(_clients):
        _close_client(fileno)
    if _server_sock is not None:
        # Best-effort : libere le port meme si close() rale sur un sock deja KO.
        with contextlib.suppress(OSError):
            _server_sock.close()
    _log("pont arrete.")


# -- Boucle defer REAPER ------------------------------------------------------
# IDIOME CRITIQUE (source classique de bug) : en Python ReaScript, RPR_defer
# prend une CHAINE de code Python re-executee dans ce namespace au cycle UI
# suivant. On re-arme donc avec la chaine "_serve()". Tant qu'on re-arme, REAPER
# reste responsive et le pont vit en tache de fond.
def _serve():
    if not _running:
        return
    try:
        _tick()
    except Exception:  # noqa: BLE001
        _log("erreur tick:\n" + traceback.format_exc())
    RPR_defer("_serve()")  # noqa: F821  (injecte par REAPER)


def main():
    if not _IN_REAPER:
        print(
            "Ce script doit etre lance DEPUIS REAPER (ReaScript Python).\n"
            "Hors REAPER il sert seulement au test de syntaxe.\n"
            "Voir reaper_bridge/README.md pour l'installation."
        )
        return
    if _running:  # meme instance relancee -> arret propre (toggle intra-instance)
        _stop()
        return
    if _start():
        _serve()


if __name__ == "__main__":
    main()
