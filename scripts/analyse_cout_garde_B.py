#!/usr/bin/env python3
"""Coût B du garde « décisions jamais ouvertes » — le tour supplémentaire.

Le coût A (balayage de l'inventaire) est mesuré par `mesure_cout_garde_doc.py`.
Ce script mesure le coût B : quand le garde se déclenche, il injecte un
`read_file` + une itération + un aller-retour LLM. Ce coût-ci ne peut PAS
être mesuré par un balayage statique — il faut un agent qui tourne, donc le
bench.

Ce script lit les résultats d'un `bench.run --category real_repo --repeat N`
(qui enregistre `doc_guard_fired` et `doc_consulte` depuis le lot 2.2) et
produit le tableau demandé par le lot 2.2 du plan d'optimisation :

  Par tâche × passe : garde déclenché (oui/non), docs consulté spontanément
  (oui/non), appels d'outils, itérations, verdict.

  Puis la séparation :
  - « tâches où le garde a SAUVÉ le verdict » : garde déclenché ET succès
  - « tâches où il a COÛTÉ un tour pour rien » : garde déclenché ET échec

Usage :
    # 1. Lancer le bench avec instrumentation
    python -m bench.run --category real_repo --repeat 5 --label garde_B

    # 2. Analyser les résultats
    python scripts/analyse_cout_garde_B.py bench/results/*garde_B*.json
    python scripts/analyse_cout_garde_B.py bench/results/*garde_B*.json --json cout_B.json

Codes de sortie :
    0 = analyse effectuée
    1 = fichier illisible ou sans champ `doc_guard_fired`
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.gate import load_run


def _charger(chemin: Path) -> list[dict]:
    _, resultats = load_run(chemin)
    sans_champ = [r for r in resultats if "doc_guard_fired" not in r]
    if sans_champ:
        print(
            f"⚠️  {len(sans_champ)}/{len(resultats)} résultats sans `doc_guard_fired`.",
            file=sys.stderr,
        )
        print(
            "    Ce bench a été lancé avant le lot 2.2 (ou en BENCH_ISOLATION=0 "
            "sans les patches). Seuls les résultats instrumentés seront analysés.",
            file=sys.stderr,
        )
    return [r for r in resultats if "doc_guard_fired" in r]


def _tableau(resultats: list[dict]) -> None:
    print()
    print("┌─ Coût B du garde « décisions jamais ouvertes »")
    print("│")
    print("│  tâche                              garde  docs   outils  iter  verdict")
    print("│  ─────                              ─────  ────   ──────  ────  ───────")
    for r in resultats:
        tid = r["task_id"].ljust(36)
        garde = "OUI" if r["doc_guard_fired"] else "non"
        docs = "OUI" if r.get("doc_consulte") else "non"
        outils = str(r.get("tool_calls_total", "?")).rjust(6)
        iters = str(r.get("iterations", "?")).rjust(4)
        verdict = "✅" if r["success"] else "❌"
        print(f"│  {tid} {garde:>5}  {docs:>4}  {outils}  {iters}  {verdict}")
    print("└" + "─" * 78)


def _synthese(resultats: list[dict]) -> dict:
    total = len(resultats)
    garde_declenche = [r for r in resultats if r["doc_guard_fired"]]
    garde_non = [r for r in resultats if not r["doc_guard_fired"]]

    sauve = [r for r in garde_declenche if r["success"]]
    cout_pour_rien = [r for r in garde_declenche if not r["success"]]

    taches_uniques = {r["task_id"] for r in resultats}
    taches_avec_garde = {r["task_id"] for r in garde_declenche}
    taches_cout_pour_rien = {r["task_id"] for r in cout_pour_rien}

    # Itérations moyennes par groupe
    def _moy_iter(rs: list[dict]) -> float:
        vals = [r["iterations"] for r in rs if "iterations" in r]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    def _moy_outils(rs: list[dict]) -> float:
        vals = [r["tool_calls_total"] for r in rs if "tool_calls_total" in r]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    synth = {
        "total_passes": total,
        "garde_declenche": len(garde_declenche),
        "garde_non_declenche": len(garde_non),
        "sauve_verdict": len(sauve),
        "cout_pour_rien": len(cout_pour_rien),
        "taux_declenchement": f"{len(garde_declenche)}/{total}",
        "taux_sauvetage": (
            f"{len(sauve)}/{len(garde_declenche)}"
            if garde_declenche else "n/a"
        ),
        "taux_cout_pour_rien": (
            f"{len(cout_pour_rien)}/{len(garde_declenche)}"
            if garde_declenche else "n/a"
        ),
        "taches_uniques": len(taches_uniques),
        "taches_avec_garde": sorted(taches_avec_garde),
        "taches_cout_pour_rien_seulement": sorted(taches_cout_pour_rien - {
            r["task_id"] for r in sauve
        }),
        "iter_moy_avec_garde": _moy_iter(garde_declenche),
        "iter_moy_sans_garde": _moy_iter(garde_non),
        "outils_moy_avec_garde": _moy_outils(garde_declenche),
        "outils_moy_sans_garde": _moy_outils(garde_non),
    }

    print()
    print(f"  Passes totales : {total}")
    print(f"  Garde déclenché : {len(garde_declenche)}/{total}")
    if garde_declenche:
        print(f"    → sauvé le verdict : {len(sauve)}")
        print(f"    → coûté pour rien  : {len(cout_pour_rien)}")
    print()
    print("  Itérations moyennes :")
    print(f"    avec garde  : {synth['iter_moy_avec_garde']}")
    print(f"    sans garde  : {synth['iter_moy_sans_garde']}")
    print("  Appels d'outils moyens :")
    print(f"    avec garde  : {synth['outils_moy_avec_garde']}")
    print(f"    sans garde  : {synth['outils_moy_sans_garde']}")
    print()

    ratio = len(taches_cout_pour_rien) / len(taches_uniques) if taches_uniques else 0
    print(f"  Tâches où le coût est POUR RIEN (garde + échec) : "
          f"{len(taches_cout_pour_rien)}/{len(taches_uniques)} "
          f"({ratio:.0%})")
    if ratio > 0.5:
        print("  ⚠️  SEUIL DÉPASSÉ (>50 %) — affiner la condition de déclenchement")
    else:
        print("  ✅  Sous le seuil de 50 % — garde laissé tel quel")

    return synth


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fichiers", nargs="+", type=str,
                        help="Fichier(s) de résultats bench (JSON)")
    parser.add_argument("--json", type=str, default=None, metavar="FICHIER",
                        help="Exporter la synthèse en JSON")
    args = parser.parse_args()

    tous: list[dict] = []
    for f in args.fichiers:
        p = Path(f)
        if not p.exists():
            print(f"✗ Fichier introuvable : {p}", file=sys.stderr)
            return 1
        tous.extend(_charger(p))

    if not tous:
        print("✗ Aucun résultat instrumenté (avec doc_guard_fired).", file=sys.stderr)
        print("  Relancer le bench avec la version ≥ lot 2.2.", file=sys.stderr)
        return 1

    _tableau(tous)
    synth = _synthese(tous)

    if args.json:
        Path(args.json).write_text(
            json.dumps(synth, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n  → {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
