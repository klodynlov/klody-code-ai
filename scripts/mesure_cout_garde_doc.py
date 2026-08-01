#!/usr/bin/env python3
"""Coût du garde « décisions du projet jamais ouvertes » sur un VRAI dépôt.

Le banc ne peut pas voir ce coût : ses dossiers de travail sont vides de
documentation hors `discovery`, donc `_documentation_du_projet()` y rend une
liste vide et le garde ne se déclenche jamais. Ce script mesure sur un dépôt
réellement documenté — celui-ci par défaut.

**Le garde a DEUX coûts, et ce script n'en mesure qu'un.** Les confondre
donnerait un chiffre faux dans les deux sens :

  A. le BALAYAGE de l'inventaire (`os.walk` élagué) — mesuré ici, exactement,
     sans gateway, en appelant la vraie méthode de `Orchestrator` ;
  B. le TOUR SUPPLÉMENTAIRE (un `read_file` + une itération + l'aller-retour
     LLM) — demande un agent qui tourne, donc le gateway. **Pas mesuré ici**,
     et le script le dit plutôt que de le passer sous silence.

Ce que A vaut la peine d'être mesuré : le balayage n'est PAS payé à chaque tour.
`_should_force_doc_read` place `bool(self._documentation_du_projet())` en
DERNIER, après trois conditions bon marché — il ne s'exécute donc que dans le
tour où le garde est sur le point de se déclencher, au plus une fois par run
(cache `_doc_inventaire`). Un balayage coûteux resterait néanmoins visible : il
tomberait pile au moment où l'utilisateur attend une conclusion.

Le script mesure aussi ce que le balayage RAMÈNE, ce qui n'est pas une question
de coût mais de justesse : l'inventaire est trié alphabétiquement puis tronqué à
`_DOC_NUDGE_MAX` dans le message injecté. Sur les tâches `discovery` du banc il
n'y a qu'un document, la question ne se posait pas. Sur un vrai dépôt, les six
documents nommés sont-ils ceux qui portent les décisions ?

Usage :
    python scripts/mesure_cout_garde_doc.py
    python scripts/mesure_cout_garde_doc.py --racine ~/Projets/autre --passes 5
    python scripts/mesure_cout_garde_doc.py --json cout_garde.json

Codes de sortie :
    0 = mesure effectuée
    1 = mesure impossible (racine illisible)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.orchestrator import (
    _DOC_NUDGE_MAX,
    _DOC_SCAN_MAX,
    Orchestrator,
)


class MesureImpossible(RuntimeError):
    """Impossible de produire un chiffre. Distinct d'un chiffre élevé."""


class _Racine:
    """Le strict minimum consommé par `_documentation_du_projet`."""

    def __init__(self, root: Path) -> None:
        self.root = root


def _sonde(racine: Path) -> Orchestrator:
    """Orchestrator SANS `__init__` — on veut la vraie méthode, pas ses voisins.

    Construire un vrai Orchestrator ici tirerait le client LLM, la découverte
    MCP (réseau) et la mémoire long terme : des secondes de préparation qui
    n'ont rien à voir avec le balayage, et une dépendance au gateway que ce
    script n'a pas besoin d'avoir. Réimplémenter le balayage serait pire encore
    — on mesurerait une copie, pas le code qui tourne en production.
    """
    sonde = Orchestrator.__new__(Orchestrator)
    sonde.file_manager = _Racine(racine)  # type: ignore[assignment]
    sonde._doc_ecrits = set()
    sonde._doc_inventaire = None
    return sonde


def _inventaire_sans_plafond(racine: Path) -> list[str]:
    """Le même balayage, sans `_DOC_SCAN_MAX` — pour savoir ce que le plafond écarte.

    `_documentation_du_projet` coupe la marche dès 40 documents collectés PUIS
    trie. Le sous-ensemble retenu dépend donc de l'ordre d'`os.walk`, pas des
    noms : ce n'est ni « les 40 premiers alphabétiquement » ni « les 40 plus
    pertinents », c'est « les 40 premiers rencontrés ». Savoir lesquels tombent
    n'est pas une question de coût, mais de justesse du message injecté.
    """
    import os

    from agent.orchestrator import _DOC_SCAN_DEPTH, _DOC_SUFFIXES

    tous: list[str] = []
    for dossier, sous_dossiers, fichiers in os.walk(racine):
        rel_dir = Path(dossier).relative_to(racine)
        profondeur = 0 if rel_dir == Path(".") else len(rel_dir.parts)
        if profondeur + 1 >= _DOC_SCAN_DEPTH:
            sous_dossiers[:] = []
        else:
            sous_dossiers[:] = [d for d in sous_dossiers if not d.startswith((".", "_"))]
        for nom in fichiers:
            if nom.startswith((".", "_")):
                continue
            if Path(nom).suffix.lower() not in _DOC_SUFFIXES:
                continue
            tous.append((rel_dir / nom).as_posix().removeprefix("./"))
    return sorted(tous)


def _balayage_naif(racine: Path) -> tuple[float, int]:
    """Le `rglob("*")` que le garde a délibérément évité — témoin du gain.

    Le commentaire de `_documentation_du_projet` chiffre ce témoin (88 706
    entrées, 0,79 s). Un commentaire qui chiffre EST un réglage : on le
    recalcule au lieu de le croire.
    """
    debut = time.perf_counter()
    n = sum(1 for _ in racine.rglob("*"))
    return time.perf_counter() - debut, n


def mesurer(racine: Path, passes: int, avec_temoin: bool) -> dict[str, Any]:
    racine = racine.expanduser().resolve()
    if not racine.is_dir():
        raise MesureImpossible(f"racine introuvable ou illisible : {racine}")

    latences: list[float] = []
    inventaire: list[str] = []
    for _ in range(passes):
        sonde = _sonde(racine)  # inventaire NEUF : sinon on mesure le cache
        debut = time.perf_counter()
        inventaire = sonde._documentation_du_projet()
        latences.append(time.perf_counter() - debut)

    # Le cache est le chemin réel des tours suivants du MÊME run : il doit être
    # gratuit, et c'est vérifiable plutôt que supposé.
    sonde = _sonde(racine)
    sonde._documentation_du_projet()
    debut = time.perf_counter()
    sonde._documentation_du_projet()
    latence_cache = time.perf_counter() - debut

    temoin: dict[str, Any] | None = None
    if avec_temoin:
        duree, entrees = _balayage_naif(racine)
        temoin = {"duree_s": round(duree, 4), "entrees": entrees}

    tous = _inventaire_sans_plafond(racine)
    ecartes = sorted(set(tous) - set(inventaire))

    # Le coût du balayage vient des branches qu'il élague. Un clone frais n'en a
    # aucune, donc y mesurer une latence et l'annoncer comme « le coût sur un
    # vrai dépôt » serait un chiffre juste là où il est pris et faux là où il
    # compte — le mode de défaillance dominant de ce dépôt.
    copie_de_travail = any(
        (racine / d).exists() for d in (".venv", "node_modules", "_downloads", "venv")
    )

    return {
        "racine": str(racine),
        "copie_de_travail": copie_de_travail,
        "passes": passes,
        "latences_s": [round(x, 4) for x in latences],
        "mediane_s": round(statistics.median(latences), 4),
        "latence_cache_s": round(latence_cache, 6),
        "documents": len(inventaire),
        "documents_sans_plafond": len(tous),
        "tronque_scan": len(inventaire) >= _DOC_SCAN_MAX,
        "ecartes_par_le_plafond": ecartes,
        "nommes_dans_le_nudge": inventaire[:_DOC_NUDGE_MAX],
        "inventaire": inventaire,
        "temoin_rglob": temoin,
    }


def _resume(m: dict[str, Any]) -> None:
    print(f"┌─ Balayage de l'inventaire — {m['racine']}")
    print(f"│ médiane sur {m['passes']} passes   {m['mediane_s']:.4f} s")
    print(f"│ min / max                {min(m['latences_s']):.4f} / {max(m['latences_s']):.4f} s")
    print(f"│ 2ᵉ appel du même run     {m['latence_cache_s']:.6f} s   (cache)")
    if m["temoin_rglob"]:
        t = m["temoin_rglob"]
        print(f"│ témoin rglob('*')        {t['duree_s']:.4f} s pour {t['entrees']} entrées")
        if m["mediane_s"] > 0:
            print(f"│ gain de l'élagage        ×{t['duree_s'] / m['mediane_s']:.0f}")
    print("└" + "─" * 62)
    print()

    print(f"  {m['documents']} document(s) retenu(s) sur "
          f"{m['documents_sans_plafond']} présent(s)"
          + (f"  ⚠️ PLAFOND ATTEINT (_DOC_SCAN_MAX={_DOC_SCAN_MAX})"
             if m["tronque_scan"] else ""))
    if m["ecartes_par_le_plafond"]:
        print("  écarté(s) par le plafond — la marche s'arrête à 40 collectés PUIS")
        print("  trie, donc le sous-ensemble dépend de l'ordre d'os.walk, pas des noms :")
        for doc in m["ecartes_par_le_plafond"][:10]:
            print(f"    ✗ {doc}")
    print(f"  nommés dans le message injecté (les {_DOC_NUDGE_MAX} premiers, tri alphabétique) :")
    for doc in m["nommes_dans_le_nudge"]:
        print(f"    · {doc}")
    if m["documents"] > _DOC_NUDGE_MAX:
        print(f"    (+{m['documents'] - _DOC_NUDGE_MAX} autre(s), non nommé(s))")
    print()
    if m["copie_de_travail"] is False:
        print("⚠️  ARBRE SANS DOSSIERS LOURDS (.venv, node_modules, _downloads…).")
        print("    C'est probablement un clone frais, pas une copie de travail — or")
        print("    le coût du balayage vient précisément des branches qu'il élague.")
        print("    Le commentaire de `_documentation_du_projet` chiffre 88 706")
        print("    entrées pour rglob('*') ; en mesurer 746 ici veut dire que ce")
        print("    N'EST PAS le dépôt qui compte. À rejouer sur la machine de")
        print("    travail avant d'en conclure quoi que ce soit sur la latence.")
        print()

    print("⚠️  CE SCRIPT NE MESURE QUE LE BALAYAGE.")
    print("    Le second coût du garde — un `read_file` de plus, une itération de")
    print("    plus, un aller-retour LLM de plus — demande un agent qui tourne,")
    print("    donc le gateway. Il n'est pas dans ces chiffres.")
    print("    Borné à UNE fois par run (`_doc_guard_fired`), et seulement quand")
    print("    l'agent a écrit du code sans ouvrir un seul document.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--racine", type=str, default=None,
                        help="Dossier de travail à mesurer (défaut : PROJECT_ROOT)")
    parser.add_argument("--passes", type=int, default=5)
    parser.add_argument("--sans-temoin", action="store_true",
                        help="Ne pas lancer le rglob('*') de comparaison (lent)")
    parser.add_argument("--json", type=str, default=None, metavar="FICHIER")
    args = parser.parse_args()

    if args.racine:
        racine = Path(args.racine)
    else:
        from config import PROJECT_ROOT
        racine = Path(PROJECT_ROOT)

    try:
        mesure = mesurer(racine, args.passes, avec_temoin=not args.sans_temoin)
    except MesureImpossible as exc:
        print(f"✗ MESURE IMPOSSIBLE — {exc}", file=sys.stderr)
        return 1

    _resume(mesure)
    if args.json:
        Path(args.json).write_text(json.dumps(mesure, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n  → {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
