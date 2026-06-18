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
ENABLE_SKELETON = os.getenv("REAPER_ENABLE_SKELETON", "0") == "1"

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
# PHASE 3c — squelette des outils LOURDS restants. NON enregistré par défaut.   #
#                                                                              #
# Activation : REAPER_ENABLE_SKELETON=1. Effets de bord risqués (écrit audio /  #
# disque) ou complexes (MIDI ppq) -> laissés en TODO explicite, à implémenter   #
# avec une intention utilisateur claire. JAMAIS de sauvegarde implicite.        #
# ---------------------------------------------------------------------------- #

_TODO = {"error": "outil non encore activé (squelette Phase 3c) — ce n'est pas un échec d'exécution, ne pas réessayer"}


def _register_skeleton() -> None:
    # ----- Transport risqué ------------------------------------------------- #
    @mcp.tool()
    async def transport_record() -> dict:
        """Démarre l'enregistrement. Effet de bord RISQUÉ (écrit de l'audio).
        TODO: RPR_OnRecordButton. Exiger une intention explicite."""
        return _TODO

    # ----- MIDI ------------------------------------------------------------- #
    @mcp.tool()
    async def insert_midi_note(
        track_index: int, pitch: int, start: float, length: float, velocity: int = 96
    ) -> dict:
        """Insère une note MIDI sur la piste `track_index`. Effet de bord.
        Crée un item MIDI si besoin. TODO: RPR_CreateNewMIDIItemInProj +
        RPR_MIDI_InsertNote (temps→ppq via RPR_MIDI_GetPPQPosFromProjTime)."""
        return _TODO

    @mcp.tool()
    async def list_midi_notes(track_index: int, item_index: int = 0) -> dict:
        """Liste les notes MIDI d'un item (pitch, start, length, vel). Lecture pure.
        TODO: RPR_MIDI_CountEvts + RPR_MIDI_GetNote en boucle."""
        return _TODO

    # ----- Rendu ------------------------------------------------------------ #
    @mcp.tool()
    async def render_region(region_index: int, out_path: str = "") -> dict:
        """Rend une région en fichier audio. Effet de bord (écrit sur disque).
        TODO: configurer RENDER_* via RPR_GetSetProjectInfo* puis
        RPR_Main_OnCommand(render). NE PAS sauvegarder le .rpp."""
        return _TODO

    @mcp.tool()
    async def render_project(out_path: str = "") -> dict:
        """Rend le projet entier (master) en fichier audio. Effet de bord lourd.
        TODO: idem render_region, bornes = projet entier."""
        return _TODO


if ENABLE_SKELETON:
    _register_skeleton()


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
