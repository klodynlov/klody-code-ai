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
import json
import logging
import os
import socket

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

logger = logging.getLogger(__name__)

BRIDGE_HOST = os.getenv("REAPER_BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = int(os.getenv("REAPER_BRIDGE_PORT", "9000"))
BRIDGE_TIMEOUT = float(os.getenv("REAPER_BRIDGE_TIMEOUT", "5"))

mcp = FastMCP("REAPER")

_UNREACHABLE = (
    "pont REAPER injoignable (%s:%d) — vérifie que (1) REAPER est lancé et "
    "(2) le script reaper_bridge/klody_reaper_bridge.py est chargé et actif "
    "(Actions > Load ReaScript puis lancer l'action)." % (BRIDGE_HOST, BRIDGE_PORT)
)
# Diagnostic DISTINCT : socket accepté mais pas de réponse complète. NE PAS dire
# « REAPER injoignable » (faux) — c'est plutôt une boucle defer arrêtée / REAPER figé.
_NO_REPLY = (
    "pont REAPER joignable (%s:%d) mais pas de réponse complète avant %ss — "
    "boucle defer du script pont arrêtée ou REAPER figé ? Relance l'action du pont."
    % (BRIDGE_HOST, BRIDGE_PORT, BRIDGE_TIMEOUT)
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
    except socket.timeout:
        return {"error": _NO_REPLY}  # connecté mais muet → pas un faux « down »
    except OSError as exc:
        return {"error": "erreur socket pont REAPER (%s:%d): %s" % (BRIDGE_HOST, BRIDGE_PORT, exc)}

    if b"\n" not in buf:  # connexion fermée sur réponse partielle/vide
        return {"error": _NO_REPLY if buf else "réponse vide du pont REAPER"}
    line = bytes(buf).split(b"\n", 1)[0]
    try:
        resp = json.loads(line)
    except (ValueError, TypeError) as exc:
        return {"error": "réponse illisible du pont REAPER: %s" % exc}

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

    Lecture pure : ne modifie jamais le projet.

    Returns:
        {"count": <int>, "tracks": [{"index","name","volume_db","pan","mute","solo"}, ...]}.
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
async def rename_track(index: int, name: str) -> dict:
    """Renomme la piste `index` (0-based). Modifie le projet, sans le sauvegarder."""
    return await _bridge_call("rename_track", {"index": index, "name": name})


@mcp.tool()
async def delete_track(index: int) -> dict:
    """Supprime la piste `index` (0-based). DESTRUCTIF mais annulable (Cmd-Z REAPER).
    Modifie le projet, sans le sauvegarder.

    Returns:
        {"deleted_index": <int>, "track_count": <int>} ou {"error": ...}.
    """
    return await _bridge_call("delete_track", {"index": index})


@mcp.tool()
async def set_track_volume(index: int, db: float) -> dict:
    """Règle le volume de la piste `index` en dB (0 = unité, négatif = plus bas).
    Modifie le projet, sans le sauvegarder."""
    return await _bridge_call("set_track_volume", {"index": index, "db": db})


@mcp.tool()
async def set_track_pan(index: int, pan: float) -> dict:
    """Règle le pan de la piste `index` : -1.0 (gauche) .. 0.0 (centre) .. 1.0 (droite).
    Hors borne = clampé. Modifie le projet, sans le sauvegarder."""
    return await _bridge_call("set_track_pan", {"index": index, "pan": pan})


@mcp.tool()
async def set_track_mute(index: int, mute: bool = True) -> dict:
    """Mute (True) ou démute (False) la piste `index`. Sans sauvegarde."""
    return await _bridge_call("set_track_mute", {"index": index, "mute": mute})


@mcp.tool()
async def set_track_solo(index: int, solo: bool = True) -> dict:
    """Solo (True) ou unsolo (False) la piste `index`. Sans sauvegarde."""
    return await _bridge_call("set_track_solo", {"index": index, "solo": solo})


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
    track_index: int,
    pitch: int,
    start: float,
    length: float,
    velocity: int = 96,
    channel: int = 0,
) -> dict:
    """Insère une note MIDI dans un nouvel item MIDI sur la piste `track_index`.

    Chaque appel crée son propre item [start, start+length]. Sans sauvegarde.

    Args:
        track_index: piste cible (0-based).
        pitch: note MIDI 0-127 (60 = Do central).
        start: début en secondes (temps projet).
        length: durée en secondes.
        velocity: vélocité 1-127 (défaut 96).
        channel: canal MIDI 0-15 (défaut 0).
    """
    return await _bridge_call("insert_midi_note", {
        "track_index": track_index, "pitch": pitch, "start": start,
        "length": length, "velocity": velocity, "channel": channel,
    })


@mcp.tool()
async def list_midi_notes(track_index: int, item_index: int = 0) -> dict:
    """Liste les notes MIDI d'un item (pitch, start, length, velocity, channel).

    Lecture pure.

    Args:
        track_index: piste (0-based).
        item_index: item MIDI de la piste (0-based, défaut 0).

    Returns:
        {"note_count": <int>, "notes": [{"index","pitch","start","length","velocity","channel","muted"}, ...]}.
    """
    return await _bridge_call("list_midi_notes", {"track_index": track_index, "item_index": item_index})


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
