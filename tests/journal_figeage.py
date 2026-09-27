"""La suite ne DOIT PAS écrire dans le vrai journal de figeage de l'API.

Constaté le 2026-09-27 par une sonde d'audit (`sys.addaudithook`) sur la suite
complète : l'import d'`api/server.py` exécute `_install_hang_dumper()` AU NIVEAU
MODULE, qui ouvre en append le VRAI `~/Library/Logs/klody-api-hang.log`, y écrit
« === dumper armé, pid N === » et branche `faulthandler` sur SIGUSR1 dans le
processus pytest. Chaque session de tests qui importait l'API — cinq fichiers le
font au niveau module, donc dès la collecte, d'autres au fil des tests — ajoutait
une ligne au journal.

Ce journal ne sert qu'une fois : APRÈS un figeage de l'API, pour savoir quand
elle a (re)démarré et lire les piles que le watchdog lui a fait dumper. Les
lignes de la suite y sont indiscernables des vrais démarrages — elles ne portent
qu'un pid. Le fichier en comptait 334 ce jour-là, prod et tests confondus, et
rien ne permet plus de les départager après coup. Même famille que
`conftest._pas_de_pollution_du_log_prod` : des traces de test relues comme des
événements réels.

Pourquoi l'environnement et pas un `monkeypatch` : l'armement a lieu à l'IMPORT,
c'est-à-dire pendant la collecte, avant la première fixture. Seul un réglage posé
avant que le moindre module de test n'importe `api.server` arrive à temps — d'où
`detourner()`, appelé depuis `tests/conftest.py`. L'environnement suit en prime
les sous-processus qui importeraient l'API.

La sonde (`armements`) cherche les lignes armées par UN pid, après UNE position :
deux processus vivants en même temps n'ont jamais le même pid, donc une API de
prod qui redémarre pendant la suite ne peut pas faire rougir le test à tort.

⚠️ Ce module ne doit RIEN importer du projet : il est chargé avant `api.server`.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

VARIABLE = "KLODY_API_HANG_LOG"
_DEFAUT = Path.home() / "Library" / "Logs" / "klody-api-hang.log"

# Capturés à l'import, AVANT `detourner()` : une fois la variable détournée, plus
# rien dans le processus ne sait où vit le vrai journal. Une surcharge déjà posée
# par le développeur est honorée — c'est là que l'API écrirait.
VRAI_JOURNAL = Path(os.environ.get(VARIABLE) or _DEFAUT)


def _taille(chemin: Path) -> int:
    try:
        return chemin.stat().st_size
    except OSError:
        return 0


# Tout ce que la suite écrirait se trouve APRÈS cette position. Absent (CI Linux,
# machine neuve) ⇒ 0 : un fichier qui apparaît en cours de suite est lu en entier.
TAILLE_INITIALE = _taille(VRAI_JOURNAL)


def detourner() -> Path:
    """Pointe le journal de figeage sur un fichier jetable. Rend ce fichier.

    À appeler avant tout import d'`api.server` : après, l'armement a déjà eu
    lieu dans le vrai journal, et le détourner ne rattraperait rien.
    """
    if "api.server" in sys.modules:
        raise RuntimeError(
            "api.server est déjà importé : le dumper de figeage a été armé dans "
            f"{VRAI_JOURNAL} avant le détournement (import anticipé par un plugin, "
            "`tests/__init__.py` ou le haut de `conftest.py`)."
        )
    dossier = Path(tempfile.mkdtemp(prefix="klody-tests-figeage-"))
    atexit.register(shutil.rmtree, dossier, True)
    jetable = dossier / _DEFAUT.name
    os.environ[VARIABLE] = str(jetable)
    return jetable


def armements(chemin: Path, pid: int, depuis: int = 0) -> list[str]:
    """Les lignes « dumper armé » écrites par `pid` dans `chemin`, après l'octet `depuis`.

    Fichier absent ⇒ aucune. Fichier plus court que `depuis` (tronqué, remplacé)
    ⇒ relu depuis le début : ce qu'il contient alors a été écrit depuis.
    """
    try:
        with chemin.open("rb") as f:
            f.seek(depuis if _taille(chemin) >= depuis else 0)
            texte = f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return []
    motif = f"=== dumper armé, pid {pid} ==="
    return [ligne for ligne in texte.splitlines() if ligne.strip() == motif]
