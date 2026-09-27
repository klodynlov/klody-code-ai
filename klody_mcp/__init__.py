"""Serveurs MCP de Klody et leurs modules partagés.

Le ``.env`` de Klody est chargé ICI, parce que c'est le seul endroit qui passe
avant TOUT module du paquet : Python exécute ce fichier avant le premier import
de ``klody_mcp.<quoi que ce soit>``. Plusieurs modules lisent leurs réglages À
L'IMPORT — ``song_structure`` (``KLODY_SONG_*``, ``ACESTEP_*``,
``ACE_STEP_VERSION``), ``_pathguard`` (``KLODY_MCP_AUDIO_ROOTS``) — et un
``load_dotenv()`` placé après leur import arrive trop tard.

Vécu le 2026-09-27 : ``vocalbrain_server`` et ``klody_music_server`` importaient
``song_structure`` puis appelaient ``load_dotenv()`` ; six serveurs faisaient de
même avec ``_pathguard``. Tout réglage posé dans le ``.env`` pour ces variables
était ignoré en silence — y compris une restriction des racines audio, qui
laissait le garde de chemins sur ses défauts, plus larges. Seules les variables
exportées (plist launchd, ``scripts/start-*-mcp.sh``) comptaient. Verrouillé par
``tests/test_klody_mcp_dotenv.py``.

``override=False`` (défaut) : une variable exportée gagne sur le ``.env``, qui ne
complète que ce qui manque. Les ``load_dotenv()`` des serveurs restent : un
serveur lancé en script (``python klody_mcp/x_server.py``) qui n'importe rien du
paquet ne passe jamais par ici, et un second appel ne change rien.
"""
from dotenv import load_dotenv

load_dotenv()
