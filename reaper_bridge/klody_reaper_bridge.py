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

Securite : lecture seule pour l'instant. AUCUN appel qui modifie ou sauvegarde le
projet (pas de RPR_Main_SaveProject).
"""

import json
import math
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
    return {"pong": True, "protocol": PROTOCOL_VERSION, "reaper": ver}


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
    if name:
        tr = RPR_GetTrack(0, idx)  # noqa: F821
        RPR_GetSetMediaTrackInfo_String(tr, "P_NAME", name, True)  # noqa: F821
    RPR_UpdateArrange()  # noqa: F821
    return {"inserted_index": idx, "name": name, "track_count": int(RPR_CountTracks(0))}  # noqa: F821


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
    """MediaTrack a `index` (0-based) ou IndexError -> {ok:false} cote appelant."""
    count = int(RPR_CountTracks(0))  # noqa: F821
    i = int(index)
    if i < 0 or i >= count:
        msg = "aucune piste dans le projet" if count == 0 else "index %d hors borne (0..%d)" % (i, count - 1)
        raise IndexError(msg)
    return RPR_GetTrack(0, i), count  # noqa: F821


def _track_name(tr):
    """Nom d'une piste. RPR_GetTrackName renvoie (retval, ptr_str, NAME, size) :
    le nom est l'index 2 ; l'index 1 est la chaine pointeur '(MediaTrack*)0x..'
    (non vide) -- NE PAS scanner 'premiere chaine non vide', ca renverrait le ptr.
    """
    res = RPR_GetTrackName(tr, "", 1024)  # noqa: F821
    if isinstance(res, tuple) and len(res) >= 3:
        return res[2] or ""
    return str(res or "")


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
    tr, _ = _track_at(args.get("index"))
    name = args.get("name") or ""
    RPR_GetSetMediaTrackInfo_String(tr, "P_NAME", name, True)  # noqa: F821
    _refresh()
    return {"index": int(args.get("index")), "name": name}


def _cmd_delete_track(args):
    """Supprime la piste `index`. DESTRUCTIF (annulable via Cmd-Z REAPER), sans save."""
    tr, _ = _track_at(args.get("index"))
    RPR_DeleteTrack(tr)  # noqa: F821
    _refresh()
    return {"deleted_index": int(args.get("index")), "track_count": int(RPR_CountTracks(0))}  # noqa: F821


def _cmd_set_track_volume(args):
    """Regle le volume (dB) de la piste `index`. Effet de bord, sans save."""
    tr, _ = _track_at(args.get("index"))
    db = float(args.get("db", 0.0))
    RPR_SetMediaTrackInfo_Value(tr, "D_VOL", _db_to_ratio(db))  # noqa: F821
    _refresh()
    return {"index": int(args.get("index")), "volume_db": db}


def _cmd_set_track_pan(args):
    """Regle le pan [-1..1] de la piste `index`. Effet de bord, sans save."""
    tr, _ = _track_at(args.get("index"))
    pan = max(-1.0, min(1.0, float(args.get("pan", 0.0))))
    RPR_SetMediaTrackInfo_Value(tr, "D_PAN", pan)  # noqa: F821
    _refresh()
    return {"index": int(args.get("index")), "pan": pan}


def _cmd_set_track_mute(args):
    """Mute/unmute la piste `index`. Effet de bord, sans save."""
    tr, _ = _track_at(args.get("index"))
    mute = _as_bool(args.get("mute", True))
    RPR_SetMediaTrackInfo_Value(tr, "B_MUTE", 1.0 if mute else 0.0)  # noqa: F821
    _refresh()
    return {"index": int(args.get("index")), "mute": mute}


def _cmd_set_track_solo(args):
    """Solo/unsolo la piste `index`. Effet de bord, sans save."""
    tr, _ = _track_at(args.get("index"))
    solo = _as_bool(args.get("solo", True))
    # I_SOLO : 0=off, 1=solo, 2=SIP. On expose un booleen (solo oui/non) -> 1.0.
    RPR_SetMediaTrackInfo_Value(tr, "I_SOLO", 1.0 if solo else 0.0)  # noqa: F821
    _refresh()
    return {"index": int(args.get("index")), "solo": solo}


def _cmd_transport_play(args):
    """Lance la lecture. Effet de bord (etat transport)."""
    RPR_OnPlayButton()  # noqa: F821
    return {"playing": True}


def _cmd_transport_stop(args):
    """Arrete la lecture/enregistrement. Effet de bord."""
    RPR_OnStopButton()  # noqa: F821
    return {"playing": False}


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
}


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
    try:
        return {"ok": True, "result": handler(args)}
    except Exception as exc:  # noqa: BLE001  (jamais de crash silencieux)
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def _send(conn, obj):
    # INVARIANT protocole : 1 reponse = 1 ligne. json.dumps echappe deja tout
    # '\n' interne aux chaines en `\n` (2 caracteres) -> la serialisation ne
    # contient jamais de saut de ligne litteral, meme pour un traceback Phase 3.
    try:
        conn.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"))
    except OSError:
        pass


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
        try:
            pair[0].close()
        except OSError:
            pass


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
            try:
                conn, _ = _server_sock.accept()
                conn.setblocking(False)
                _clients[conn.fileno()] = [conn, bytearray()]
            except OSError:
                pass
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
        try:
            _server_sock.close()
        except OSError:
            pass
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
