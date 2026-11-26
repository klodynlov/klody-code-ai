#!/usr/bin/env python3
"""Prévenir Klody qu'une tâche longue est finie : il t'écrira par iMessage.

    klody-notifier.py "Rendu Local Suno terminé" --statut ok --source local-suno
    klody-notifier.py "Import LibraryBrain" --statut echec --detail "3 pages illisibles"

Dépose un JSON dans ``$KLODY_DATA_DIR/notifications/`` (défaut
``~/.klody/data/notifications``), que le moteur proactif du canal iMessage
(``canaux/proactif.py``) relève toutes les 30 s. Un dossier plutôt qu'un port :
aucun service réseau de plus, et n'importe quel script (shell, Python 3.9 de
``/usr/bin/python3``, launchd) peut l'appeler — stdlib seule, compatible 3.9.

Écriture atomique (``.tmp`` puis renommage) : le moteur ne lit jamais un fichier
à moitié écrit.
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

STATUTS = ("ok", "echec", "info")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("titre", nargs="?", default="")
    ap.add_argument("--briefing", action="store_true",
                    help="demande un briefing MAINTENANT (relevé sous une minute)")
    ap.add_argument("--moment", choices=("matin", "midi", "soir"),
                    help="avec --briefing : lequel (défaut : celui de l'heure qu'il est)")
    ap.add_argument("--statut", choices=STATUTS, default="info")
    ap.add_argument("--source", default="")
    ap.add_argument("--detail", default="")
    a = ap.parse_args(argv)
    base = Path(os.environ.get("KLODY_DATA_DIR") or Path.home() / ".klody" / "data")
    if a.briefing:
        base.mkdir(parents=True, exist_ok=True)
        # Le moment voulu, sinon un instant (ancien format) : le canal choisit
        # alors le dernier moment commencé.
        (base / "briefing.maintenant").write_text(a.moment or str(time.time()))
        return 0
    if not a.titre.strip():
        ap.error("titre vide")
    dossier = base / "notifications"
    dossier.mkdir(parents=True, exist_ok=True)
    nom = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    tmp = dossier / (nom + ".tmp")
    tmp.write_text(json.dumps({"titre": a.titre.strip(), "statut": a.statut,
                               "source": a.source, "detail": a.detail,
                               "ts": time.time()}, ensure_ascii=False))
    tmp.replace(dossier / (nom + ".json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
