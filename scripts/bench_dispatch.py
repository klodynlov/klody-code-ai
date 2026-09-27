#!/usr/bin/env python3
"""Déclenche le nightly bench DEPUIS le Mac — piloté par com.klody.bench-dispatch.

Le cron GitHub `0 3 * * *` ne tirait pas à 03:00 UTC. Mesuré sur 90 runs
planifiés : entre 03:37 et 14:55 UTC, et chaque jour de septembre 2026 entre
07:26 et 08:36 UTC — 4 h 30 à 5 h 30 de retard, dérive croissante. Le réveil
matériel de 03:50 local tombait donc à côté du run, qui arrivait vers 10 h sur
un portable en veille : la sentinelle l'annulait 15 min plus tard.

Le runner EST ce Mac : c'est le seul endroit qui sache quand il est éveillé.
L'agent launchd porte plusieurs créneaux par jour ; un créneau manqué pendant le
sommeil est rattrapé au réveil suivant (sémantique de `StartCalendarInterval`).
Chaque créneau déclenche le workflow par `workflow_dispatch`, SAUF si un run
récent le couvre déjà — en vol, ou jugé (vert OU rouge). Un rouge est une
mesure : le relancer à chaque créneau ne ferait que multiplier le même verdict.
Seul un run jamais exécuté (annulé, absent) appelle un nouveau déclenchement.

Deux codes de sortie, jamais un seul (même contrat que les veilles) :
  0 = situation examinée : run déclenché, OU déjà couvert
  1 = pas pu interroger GitHub, ou pas pu déclencher

⚠️ Tourne sous `/usr/bin/python3` (3.9) : compatibilité verrouillée par
`tests/test_scripts_python_systeme.py`.

Usage :
    bench_dispatch.py            examine, déclenche si nécessaire
    bench_dispatch.py --check    examine et dit ce qu'il ferait, sans rien déclencher
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = "klodynlov/klody-code-ai"
WORKFLOW = "bench-nightly.yml"
BRANCHE = "main"

# Un run créé il y a moins de FENETRE_H heures couvre la journée. 20 h et non
# 24 : un créneau du lendemain matin doit pouvoir relancer après un run tardif
# de la veille au soir, sans qu'on double jamais dans la même journée.
FENETRE_H = 20
RUNS_A_EXAMINER = 10

# Statuts GitHub d'un run qui n'est pas terminé.
EN_VOL = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})
# Conclusions d'un run qui a réellement mesuré quelque chose.
JUGES = frozenset({"success", "failure"})

# Le temps de grâce de la sentinelle du workflow : le Mac doit rester éveillé
# au moins aussi longtemps pour qu'un runner prenne le job. Le job `bench` lance
# ensuite son propre caffeinate.
EVEIL_S = 900

# launchd ne donne que PATH=/usr/bin:/bin:/usr/sbin:/sbin — `gh` (Homebrew) n'y
# est pas. Cf. veille_nightly.py, qui a eu ce défaut.
GH_CANDIDATS = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} bench-dispatch: {message}", flush=True)


def trouver_gh() -> str:
    chemin = shutil.which("gh")
    if chemin:
        return chemin
    for candidat in GH_CANDIDATS:
        if Path(candidat).is_file():
            return candidat
    raise RuntimeError(
        "gh introuvable (ni dans le PATH, ni dans " + ", ".join(GH_CANDIDATS) + ")"
    )


def _gh(*args: str, timeout: int = 30) -> str:
    """Lance gh ; lève sur tout échec (l'appelant doit rendre 1, jamais 0)."""
    result = subprocess.run(
        [trouver_gh(), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh {args[0]} {args[1]} a échoué (code {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def lister_runs() -> list[dict[str, Any]]:
    sortie = _gh(
        "run", "list",
        f"--workflow={WORKFLOW}",
        f"--repo={REPO}",
        f"--limit={RUNS_A_EXAMINER}",
        "--json=status,conclusion,createdAt,databaseId,event",
    )
    runs = json.loads(sortie)
    if not isinstance(runs, list):
        raise RuntimeError(f"gh run list n'a pas rendu une liste: {type(runs)}")
    return runs


def _date(iso: str) -> datetime:
    # `fromisoformat` de 3.9 ne lit pas le suffixe « Z ».
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def run_couvrant(
    runs: list[dict[str, Any]], maintenant: datetime | None = None
) -> dict[str, Any] | None:
    """Le run récent qui rend un nouveau déclenchement inutile, ou None.

    Couvre : créé il y a moins de FENETRE_H heures ET (en vol OU jugé). Un run
    annulé n'a rien mesuré — c'est précisément le cas à rattraper.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    limite = maintenant - timedelta(hours=FENETRE_H)
    for run in runs:
        cree = run.get("createdAt")
        if not cree or _date(cree) < limite:
            continue
        if run.get("status") in EN_VOL or run.get("conclusion") in JUGES:
            return run
    return None


def garder_eveille() -> None:
    """Tient le Mac éveillé le temps qu'un runner prenne le job.

    Session séparée pour survivre à la sortie de ce script (le plist pose aussi
    `AbandonProcessGroup`). Best-effort : un caffeinate raté ne doit pas
    empêcher le déclenchement, la sentinelle dira si personne ne l'a pris.
    """
    try:
        subprocess.Popen(
            ["/usr/bin/caffeinate", "-ims", "-t", str(EVEIL_S)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log(f"caffeinate impossible ({exc}) — déclenchement quand même")


def declencher() -> None:
    _gh("workflow", "run", WORKFLOW, f"--repo={REPO}", f"--ref={BRANCHE}", timeout=60)


def executer(check_seulement: bool) -> int:
    try:
        runs = lister_runs()
    except (RuntimeError, subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        log(f"ÉCHEC de l'interrogation — {exc}")
        return 1

    couvrant = run_couvrant(runs)
    if couvrant is not None:
        etat = couvrant.get("conclusion") or couvrant.get("status")
        log(
            f"déjà couvert : run {couvrant.get('databaseId')} "
            f"({couvrant.get('event')}, {etat}, créé {couvrant.get('createdAt')}) "
            "— rien à déclencher"
        )
        return 0

    if check_seulement:
        log(f"aucun run jugé ni en vol depuis {FENETRE_H} h — un déclenchement AURAIT lieu")
        return 0

    garder_eveille()
    try:
        declencher()
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        log(f"ÉCHEC du déclenchement — {exc}")
        return 1
    log(f"nightly déclenché ({WORKFLOW} sur {BRANCHE}), Mac tenu éveillé {EVEIL_S // 60} min")
    return 0


def main(argv: list[str]) -> int:
    return executer(check_seulement="--check" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
