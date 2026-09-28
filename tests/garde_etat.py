"""La suite ne DOIT JAMAIS écrire dans l'état persistant réel (`~/.klody/data`).

Constaté le 2026-09-27 : le dossier comptait ~6 000 sessions `memory_*.json`
quand la mémoire projet en annonçait 1 624. Relevé par contenu
(`python scripts/etat_pollue.py`, qui le recompte) :

- 4 456 sessions de la suite — messages ET réponses scriptés (« conçois mon
  algo pas à pas », « dis bonjour », « Lis pi.txt jusqu'à comprendre. »…) :
  `tests/integration/test_websocket_chat.py` et les scénarios de rejeu
  instancient l'API ou l'Orchestrator sans détourner `config.MEMORY_DIR`,
  contrairement à `test_api_routes.py` qui l'isolait ;
- 1 236 énoncés du banc (« [Répertoire de travail : /private/tmp/kb-… »).

L'historique KlodyAI de l'utilisateur en était noyé, `klody --continue`
(`ConversationMemory.load_latest`) pouvait rouvrir une session de test, et le
profil appris (`user_profile.json`, injecté dans le prompt système) comptait
les requêtes des tests comme celles de l'utilisateur.

Pourquoi un garde de SUITE plutôt qu'un patch dans chaque fichier :
`test_api_routes.py` faisait déjà la chose juste, et ça n'a pas empêché
`test_websocket_chat.py`, écrit à côté, de ne pas la faire. Un correctif local
protège le fichier corrigé ; celui-ci protège le prochain.

Deux étages, parce qu'un seul ne suffit pas :

1. **Redirection** (`rediriger`), AVANT le premier `import config` :
   `KLODY_DATA_DIR` et `SEMANTIC_MEMORY_DB` pointent sur un dossier jetable.
   L'environnement plutôt qu'un `monkeypatch` parce que trois modules figent le
   chemin À L'IMPORT (`agent.long_term_memory._STORAGE`,
   `agent.profiler._PROFILE_FILE`, `agent.greeting.MEMORY_DIR`) : un patch de
   `config.MEMORY_DIR` ne les atteint pas. L'environnement atteint aussi les
   sous-processus et les `importlib.reload(config)`.
2. **Dénonciation** (`installer_garde`) : un hook d'audit (PEP 578) REFUSE toute
   écriture sous le vrai dossier et la consigne. Refuser ne suffit pas —
   `ConversationMemory.save` avale l'`OSError` et journalise, le test resterait
   vert. D'où la consignation, relue après chaque test par une fixture autouse
   qui le fait rougir en nommant le fichier visé.

> Un garde-fou qui ne peut pas rougir est indiscernable d'un garde-fou vert.
  (CLAUDE.md, mode de défaillance dominant du dépôt.)

⚠️ Ce module ne doit RIEN importer du projet : il est chargé avant `config`.
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

# Capturés AVANT toute redirection : une fois `KLODY_DATA_DIR` détourné, plus
# rien dans le processus ne sait où vit le vrai dossier. On protège le défaut
# ET une éventuelle surcharge du développeur (second profil) : les deux sont
# des données réelles.
#
# Un pytest FILS de la suite (`tests/test_hermeticite_mcp.py` en lance un) hérite
# de l'environnement du test qui l'a lancé, donc d'un `KLODY_DATA_DIR` posé par la
# redirection ou par la fixture `_etat_persistant_isole` — un dossier JETABLE. Le
# lire comme « surcharge du développeur » le protégeait, et le conftest du fils
# refusait alors de démarrer sur son propre dossier de test : vécu le 2026-09-27,
# `main` rouge dès la fusion de #285 (fils pytest) sur #279 (ce garde), chacun
# vert seul. Le parent transmet donc SA liste (`_ENV_HERITAGE`) : un descendant
# protège ce que l'ancêtre protégeait, plus son propre défaut (un fils lancé sous
# un autre `HOME` a un autre « vrai » dossier), et ne lit plus `KLODY_DATA_DIR`.
_ENV_HERITAGE = "_KLODY_TESTS_VRAIS_DOSSIERS"
_DEFAUT = Path.home() / ".klody" / "data"
_HERITAGE = os.environ.get(_ENV_HERITAGE)
_SURCHARGE = None if _HERITAGE is not None else os.environ.get("KLODY_DATA_DIR")
VRAIS_DOSSIERS: tuple[Path, ...] = tuple(
    dict.fromkeys(
        Path(p).expanduser().absolute()
        for p in (_DEFAUT, _SURCHARGE, *(_HERITAGE or "").split(os.pathsep))
        if p
    )
)

_racines: list[str] = [str(p) for p in VRAIS_DOSSIERS]
_violations: list[str] = []
_verrou = threading.Lock()
# Hook posé ? Un conteneur plutôt qu'un booléen `global` : un addaudithook est
# définitif (PEP 578), un second appel doublerait chaque refus.
_hooks_poses: list[object] = []

# Ouverture en écriture : `open()` passe un mode texte, `os.open` des drapeaux.
_DRAPEAUX_ECRITURE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
# `os.replace` lève `os.rename` : l'écriture atomique (tmp + rename) est couverte.
# `sqlite3.connect` : `semantic_memory.db` vit dans le même dossier ; une simple
# ouverture de la vraie base est déjà une fuite, lecture comprise.
_EVENEMENTS = frozenset({
    "open", "os.rename", "os.remove", "os.rmdir", "os.mkdir",
    "shutil.rmtree", "os.truncate", "sqlite3.connect",
})


def rediriger() -> Path:
    """Pointe l'état persistant sur un dossier jetable. À appeler avant `import config`.

    Idempotent : un second appel garde le premier dossier.
    """
    # Avant tout retour : les sous-processus lancés par les tests en héritent.
    os.environ[_ENV_HERITAGE] = os.pathsep.join(str(p) for p in VRAIS_DOSSIERS)
    deja = os.environ.get("_KLODY_TESTS_DATA_DIR")
    if deja:
        return Path(deja)
    dossier = Path(tempfile.mkdtemp(prefix="klody-tests-etat-"))
    os.environ["_KLODY_TESTS_DATA_DIR"] = str(dossier)
    os.environ["KLODY_DATA_DIR"] = str(dossier)
    # Sinon hérité d'un `SEMANTIC_MEMORY_DB` exporté, qui ne suit pas KLODY_DATA_DIR.
    os.environ["SEMANTIC_MEMORY_DB"] = str(dossier / "semantic_memory.db")
    return dossier


def est_protege(chemin: str | os.PathLike[str]) -> bool:
    """Vrai si `chemin` est, ou est sous, un dossier d'état réel protégé."""
    absolu = os.path.abspath(os.fspath(chemin))
    return any(absolu == r or absolu.startswith(r + os.sep) for r in _racines)


def _chemins_ecrits(evenement: str, args: tuple) -> tuple:
    if evenement == "open":
        chemin, mode, drapeaux = args
        if isinstance(mode, str):
            ecrit = any(c in mode for c in "wax+")
        else:
            ecrit = isinstance(drapeaux, int) and bool(drapeaux & _DRAPEAUX_ECRITURE)
        return (chemin,) if ecrit else ()
    if evenement == "os.rename":
        return tuple(args[:2])
    return tuple(args[:1])


def _hook(evenement: str, args: tuple) -> None:
    # Appelé pour CHAQUE événement d'audit du processus : sortir au plus vite.
    if evenement not in _EVENEMENTS:
        return
    for brut in _chemins_ecrits(evenement, args):
        if brut is None or isinstance(brut, int):  # descripteur : pas de chemin
            continue
        try:
            chemin = os.path.abspath(os.fsdecode(os.fspath(brut)))
        except (TypeError, ValueError):
            continue
        if not est_protege(chemin):
            continue
        message = (
            f"{evenement} {chemin} "
            f"(fil « {threading.current_thread().name} »)"
        )
        with _verrou:
            _violations.append(message)
        raise PermissionError(
            f"[tests] écriture REFUSÉE dans l'état persistant réel : {chemin} "
            f"({evenement}). Un test n'écrit que sous tmp_path — cf. tests/garde_etat.py."
        )


def installer_garde() -> None:
    """Pose le hook d'audit, une seule fois par processus."""
    if _hooks_poses:
        return
    sys.addaudithook(_hook)
    _hooks_poses.append(_hook)


def marque() -> int:
    """Position courante du registre : ce qui sera consigné après appartient au test."""
    with _verrou:
        return len(_violations)


def violations_depuis(position: int) -> list[str]:
    with _verrou:
        return list(_violations[position:])


def oublier_depuis(position: int) -> None:
    """Réservé à l'auto-test du garde : efface ce qu'il vient de provoquer exprès."""
    with _verrou:
        del _violations[position:]


@contextlib.contextmanager
def racine_protegee(dossier: Path) -> Iterator[None]:
    """Protège temporairement `dossier` — pour prouver que le garde rougit sans
    jamais toucher au vrai `~/.klody/data`."""
    r = str(Path(dossier).absolute())
    _racines.append(r)
    try:
        yield
    finally:
        _racines.remove(r)
