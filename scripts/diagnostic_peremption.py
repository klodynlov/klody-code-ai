#!/usr/bin/env python3
"""Quels services klody servent du code de dépendances périmé ?

Écrit après l'incident du 2026-08-05 : la PR #206 a bumpé `openai`
2.41.1 → 2.53.0, pip a réécrit `site-packages/openai/` SOUS `com.klody.api`
qui tournait depuis 28 h, et l'API a rendu sur CHAQUE requête

    cannot import name 'path_template' from 'openai._utils'

~23 h plus tard, sans corrélation visible avec la mise à jour et avec un venv
parfaitement sain. Rien ne signalait l'écart : ni sonde, ni log, ni statut. Le
remède tenait en une commande, encore fallait-il savoir laquelle.

Ce script la nomme, service par service. Il couvre TOUS les `com.klody.*` — les
serveurs MCP n'ont aucun `/health`, l'inspection du dehors est le seul angle qui
les atteigne.

Usage :
    python scripts/diagnostic_peremption.py
    python scripts/diagnostic_peremption.py --corriger

⚠️ `--corriger` fait un `launchctl kickstart -k` sur les services périmés, et
`-k` TUE le process avant de le relancer : sur `com.klody.api`, cela coupe la
session de travail en cours. C'est pour ça que ce n'est pas le comportement par
défaut, et que rien dans le dépôt ne le déclenche automatiquement.

Codes de sortie — trois, parce que « je n'ai pas pu juger » n'est pas « tout va
bien » :
    0 = jugé, aucun service périmé
    1 = jugé, au moins un service périmé (la sortie dit lesquels et quoi faire)
    2 = rien n'a pu être jugé (aucun agent chargé, ou site-packages introuvable)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import peremption

_SYMBOLES = {
    peremption.A_JOUR: "✓",
    peremption.PERIMEES: "✗",
    peremption.NON_JUGE: "?",
}
_CODES_SORTIE = {peremption.A_JOUR: 0, peremption.PERIMEES: 1, peremption.NON_JUGE: 2}


def _corriger(examens: list[peremption.ServiceExamine]) -> int:
    """Relance les services périmés. Rend le nombre d'échecs de relance."""
    echecs = 0
    for examen in examens:
        if examen.statut != peremption.PERIMEES or not examen.remede:
            continue
        print(f"  $ {examen.remede}")
        # Commande construite par `agent.peremption`, pas par l'utilisateur.
        rc = subprocess.run(  # nosec
            examen.remede.split(), capture_output=True, text=True, check=False
        )
        if rc.returncode == 0:
            print(f"    relancé : {examen.label}")
        else:
            echecs += 1
            print(f"    ÉCHEC ({rc.returncode}) : {rc.stderr.strip() or 'sans message'}")
    return echecs


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parseur.add_argument(
        "--corriger", action="store_true",
        help="relance les services périmés (kickstart -k : TUE le process en cours)",
    )
    args = parseur.parse_args(argv)

    empreinte = peremption.empreinte_disque()
    print("Péremption des dépendances — klody-code-ai")
    print(f"  site-packages : {', '.join(empreinte.racines) or 'introuvable'}")
    print(f"  {empreinte.nb_paquets} paquet(s), dernière écriture : "
          f"{peremption.iso(empreinte.horodatage) or '—'}")
    print()

    services = peremption.services_versionnes()
    if not services:
        print("Aucun LaunchAgent versionné dans launchagents/ — rien à juger.")
        return 2

    examens = peremption.examiner_services(services, empreinte=empreinte)
    for examen in examens:
        print(f"  {_SYMBOLES[examen.statut]} {examen.label:<28} {examen.raison}")

    global_ = peremption.verdict(examens)
    perimes = [e for e in examens if e.statut == peremption.PERIMEES]
    print()

    if perimes:
        print(f"{len(perimes)} service(s) périmé(s). Remède :")
        if args.corriger:
            echecs = _corriger(perimes)
            if echecs:
                print(f"\n{echecs} relance(s) en échec — vérifier à la main.")
                return 1
            print("\nTous relancés. Re-lancer ce script pour confirmer.")
            return 0
        for examen in perimes:
            print(f"  {examen.remede}")
        print("\n  (ou `--corriger` pour les relancer tous — kickstart -k TUE le process)")
    elif global_ == peremption.NON_JUGE:
        print("Rien n'a pu être jugé : aucun service datable. Ce n'est PAS un feu vert.")
    else:
        print("Tous les services résidents servent le code de dépendances du disque.")

    return _CODES_SORTIE[global_]


if __name__ == "__main__":
    raise SystemExit(main())
