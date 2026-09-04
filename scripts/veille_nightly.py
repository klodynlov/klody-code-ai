#!/usr/bin/env python3
"""Veille sur la santé du nightly bench — piloté par com.klody.veille-nightly (24 h).

Le nightly bench est le SEUL juge du projet (principe directeur n°2 : « aucune
amélioration ne passe sans gain chiffré au bench »). S'il est muet, personne ne
le voit — c'est le mode de défaillance dominant du dépôt, un garde-fou incapable
de rougir.

En août 2026, le nightly a été muet 4 jours sur 5 (8/40 runs verts).  Personne
ne l'a vu.  Ce script ferme la boucle : si aucun run vert depuis MUETTE_JOURS,
il le dit via notification macOS au lieu de se taire.

Deux codes de sortie, jamais un seul :
  0 = interrogation réussie, situation examinée (bonne OU mauvaise)
  1 = interrogation échouée, rien n'a été regardé

Un `|| true` confondrait les deux — c'est exactement la panne qui a coûté
~13 % au banc pendant des mois.

Usage :
    veille_nightly.py                     interroge, notifie si nécessaire, écrit l'état
    veille_nightly.py --check             affiche l'état, n'écrit rien, ne notifie pas
    veille_nightly.py --tester-notification  vérifie que le canal d'alerte marche
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC
from pathlib import Path
from typing import Any

MUETTE_JOURS = 3

REPO = "klodynlov/klody-code-ai"
WORKFLOW = "bench-nightly.yml"
RUNS_A_EXAMINER = 10

ETAT_DIR = Path.home() / "Library" / "Caches" / "klody"
ETAT_FICHIER = ETAT_DIR / "veille-nightly.json"


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} veille-nightly: {message}", flush=True)


def notifier(titre: str, corps: str) -> bool:
    """Notification macOS via osascript. Passe par `on run argv` : aucun
    échappement à faire, donc aucun contenu de run ne peut casser le script."""
    script = (
        "on run argv\n"
        "display notification (item 1 of argv) with title (item 2 of argv)\n"
        "end run"
    )
    try:
        subprocess.run(
            ["osascript", "-e", script, corps, titre],
            check=True,
            capture_output=True,
            timeout=15,
        )
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        log(f"ÉCHEC de la notification ({exc}) — le diagnostic reste dans ce journal")
        return False


def lister_runs() -> list[dict[str, Any]]:
    """Interroge gh pour les derniers runs du nightly.  Lève sur tout échec —
    l'appelant DOIT distinguer « pas de run vert » de « pas pu regarder »."""
    result = subprocess.run(
        [
            "gh", "run", "list",
            f"--workflow={WORKFLOW}",
            f"--repo={REPO}",
            f"--limit={RUNS_A_EXAMINER}",
            "--json=status,conclusion,startedAt,databaseId",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh run list a échoué (code {result.returncode}): {result.stderr.strip()}")
    runs = json.loads(result.stdout)
    if not isinstance(runs, list):
        raise RuntimeError(f"gh run list n'a pas rendu une liste: {type(runs)}")
    return runs


def diagnostiquer(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyse les runs et produit un diagnostic structuré."""
    total = len(runs)
    verts = [r for r in runs if r.get("conclusion") == "success"]
    rouges = [r for r in runs if r.get("conclusion") == "failure"]
    annules = [r for r in runs if r.get("conclusion") == "cancelled"]

    dernier_vert = verts[0]["startedAt"][:10] if verts else None

    age_dernier_vert: float | None = None
    if verts:
        from datetime import datetime
        dt_str = verts[0]["startedAt"]
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        age_dernier_vert = (datetime.now(UTC) - dt).total_seconds() / 86400

    return {
        "total": total,
        "verts": len(verts),
        "rouges": len(rouges),
        "annules": len(annules),
        "dernier_vert": dernier_vert,
        "age_dernier_vert_jours": round(age_dernier_vert, 1) if age_dernier_vert is not None else None,
    }


def charger_etat() -> dict[str, Any]:
    try:
        etat = json.loads(ETAT_FICHIER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"dernier_succes": None, "derniere_erreur": None, "dernier_diagnostic": None}
    if not isinstance(etat, dict):
        return {"dernier_succes": None, "derniere_erreur": None, "dernier_diagnostic": None}
    etat.setdefault("dernier_succes", None)
    etat.setdefault("derniere_erreur", None)
    etat.setdefault("dernier_diagnostic", None)
    return etat


def ecrire_etat(etat: dict[str, Any]) -> None:
    ETAT_DIR.mkdir(parents=True, exist_ok=True)
    ETAT_FICHIER.write_text(json.dumps(etat, indent=2, ensure_ascii=False), encoding="utf-8")


def executer(check_seulement: bool) -> int:
    etat = charger_etat()

    try:
        runs = lister_runs()
    except (RuntimeError, subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        message = str(exc)
        log(f"ÉCHEC de l'interrogation — {message}")
        if not check_seulement:
            etat["derniere_erreur"] = message
            ecrire_etat(etat)
            dernier = etat.get("dernier_succes")
            if dernier is not None:
                age = (time.time() - dernier) / 86400
                if age > MUETTE_JOURS:
                    notifier(
                        "Veille nightly MUETTE",
                        f"Impossible d'interroger GitHub depuis {age:.1f} j. "
                        f"Dernière erreur : {message}",
                    )
            else:
                notifier(
                    "Veille nightly MUETTE",
                    "Aucune interrogation GitHub n'a jamais abouti. "
                    "Voir ~/Library/Logs/klody-veille-nightly.log",
                )
        return 1

    diag = diagnostiquer(runs)

    if not check_seulement:
        etat["dernier_succes"] = time.time()
        etat["derniere_erreur"] = None
        etat["dernier_diagnostic"] = diag

    log(
        f"{diag['verts']}/{diag['total']} verts, "
        f"{diag['rouges']} rouges, {diag['annules']} annulés"
        + (f" — dernier vert : {diag['dernier_vert']}" if diag["dernier_vert"] else " — AUCUN vert")
    )

    age = diag.get("age_dernier_vert_jours")
    if age is not None and age > MUETTE_JOURS and not check_seulement:
        notifier(
            "Nightly bench MUET",
            f"Aucun run vert depuis {age:.0f} j ({diag['verts']}/{diag['total']} verts). "
            f"Annulés : {diag['annules']}, rouges : {diag['rouges']}.",
        )
    elif diag["verts"] == 0 and not check_seulement:
        notifier(
            "Nightly bench MUET",
            f"Aucun run vert sur les {diag['total']} derniers. "
            f"Annulés : {diag['annules']}, rouges : {diag['rouges']}.",
        )

    if not check_seulement:
        ecrire_etat(etat)

    return 0


def main(argv: list[str]) -> int:
    if "--tester-notification" in argv:
        ok = notifier("Veille nightly", "Canal d'alerte vérifié — ceci est un test.")
        log(f"test de notification : {'OK' if ok else 'ÉCHEC'}")
        return 0 if ok else 1

    check = "--check" in argv
    if check:
        etat = charger_etat()
        dernier = etat.get("dernier_succes")
        age = None if dernier is None else (time.time() - dernier) / 86400
        log(f"état : {ETAT_FICHIER}")
        log(
            "  dernière réussite     : "
            + ("jamais" if age is None else f"il y a {age:.2f} j")
        )
        log(f"  dernière erreur       : {etat.get('derniere_erreur')}")
        diag = etat.get("dernier_diagnostic")
        if diag:
            log(f"  dernier diagnostic    : {diag['verts']}/{diag['total']} verts, dernier vert {diag.get('dernier_vert')}")
    return executer(check_seulement=check)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
