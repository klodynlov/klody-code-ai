"""Gate de non-régression du bench : compare un run à la baseline versionnée.

Vit ici — et non plus en heredoc dans `.github/workflows/bench-nightly.yml` —
parce que la version inline lisait un format qui n'a jamais existé : elle faisait
`base.get("counts_by_category")` alors que `bench/run.py` sérialise une **liste
plate** de `Result`. `json.loads()` rendant une `list`, le `.get()` levait
`AttributeError` dès qu'une baseline était présente. Bug jamais vu parce que la
baseline était `.gitignore`-ée et n'existait donc jamais côté CI.

Sous `bench/`, le module est couvert par ruff et testable (tests/test_bench_gate.py),
ce qui empêche la dérive de format de se reproduire silencieusement.

Usage:
    python -m bench.gate                                  # défauts CI
    python -m bench.gate --max-drop 0.10                  # seuil explicite
    python -m bench.gate --baseline a.json --latest b.json

Codes de sortie : 0 = OK (ou rien de comparable), 1 = régression ou entrée illisible.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Chute de taux de succès tolérée avant de casser le build (9 points).
#
# ⚠️ Ce seuil est un POURCENTAGE : sa sensibilité en nombre de tâches dépend de
# la taille de la baseline, ce qu'aucune lecture de la constante ne laisse voir.
# Le test est `delta < -max_drop`, donc strict : un écart qui vaut exactement le
# seuil PASSE. Première perte qui fait rougir, MESURÉE en appelant `compare()`
# (cf. tests/test_gate_sensibilite.py, qui verrouille exactement ce tableau) :
#
#   seuil   baseline 20/20   baseline 29/30   baseline 30/30
#   0.06         2                3                2
#   0.09         2                4                3
#   0.10         3                5                4
#
# ⚠️ **La colonne 29/30 était FAUSSE d'une unité dans ce commentaire**, du
# 2026-07-30 matin jusqu'à sa vérification le soir. Elle annonçait 2/3/4 là où
# le gate rend 3/4/5. La justification écrite du passage 0.10 → 0.09 en
# découlait et ne tenait pas : elle prétendait ramener le gate à « 3 tâches
# cassées, comme celui à 20 tâches sous 0.10 », alors qu'il en fallait 4. La
# porte est restée plus lâche qu'annoncé toute la journée.
#
# Rien ne vérifiait ce tableau : il avait été calculé de tête, dans le même
# commit que le changement de seuil qu'il justifiait. D'où le test — un
# commentaire qui chiffre une sensibilité est un réglage, et un réglage qui
# ment est pire qu'un réglage absent.
#
# Ce que la promotion de la baseline à 30/30 change (2026-07-30 soir, après le
# garde « décisions jamais ouvertes ») : la ligne utile passe de 4 à **3**, ce
# qui atteint enfin la cible que 0.09 visait — la sensibilité du gate historique
# à 20 tâches sous 0.10.
#
# ⚠️ AUCUN seuil en pourcentage ne donne la même sensibilité absolue à deux
# tailles de baseline : il faudrait ≥ 0.10 pour 20 tâches et < 0.10 pour 30.
# C'est structurel, un seuil relatif ne conserve pas un compte absolu. On
# optimise donc pour la taille RÉELLE de la baseline (30) ; l'effet de bord est
# que les intersections plus petites (`--category easy`, 5 tâches) deviennent
# plus strictes, ce qui est le bon sens de l'erreur.
#
# ⚠️ Corollaire : tout nouveau palier de tâches oblige à RECALCULER ce seuil.
# Un banc qui grandit sans ça devient de plus en plus permissif et rien ne le
# signale — le mode de défaillance dominant du dépôt (« un garde-fou qui ne peut
# pas rougir est indiscernable d'un garde-fou vert »). Le tableau se reproduit
# avec `tests/test_bench_gate.py::TestSensibiliteSelonTaille`.
DEFAULT_MAX_DROP = 0.09


def load_run(path: Path) -> tuple[dict, list[dict]]:
    """Charge un fichier de run et retourne (métadonnées, résultats).

    Deux formats sont acceptés, et le resteront :
    - l'enveloppe courante `{"meta": {...}, "results": [...]}` ;
    - la liste plate historique, écrite avant que la provenance soit enregistrée.
      Les baselines déjà promues sont dans ce format — les casser rendrait le gate
      à nouveau inopérant, ce qu'on vient précisément de réparer.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        meta, results = {}, raw
    elif isinstance(raw, dict) and isinstance(raw.get("results"), list):
        meta = raw.get("meta") or {}
        results = raw["results"]
    else:
        raise ValueError(
            f"{path}: format inattendu ({type(raw).__name__}) — "
            "attendu une liste de résultats produite par bench.run"
        )

    for item in results:
        if not isinstance(item, dict) or "task_id" not in item:
            raise ValueError(f"{path}: entrée sans task_id — {item!r}")
    return meta, results


def load_results(path: Path) -> list[dict]:
    """Résultats seuls — le gate n'a que faire de la provenance."""
    return load_run(path)[1]


def success_rate(results: list[dict]) -> float:
    """Taux de succès sur l'ensemble fourni. 0.0 si vide."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.get("success")) / len(results)


def compare(
    baseline: list[dict],
    latest: list[dict],
    max_drop: float = DEFAULT_MAX_DROP,
) -> tuple[bool, str]:
    """Compare deux runs sur l'INTERSECTION de leurs task_id.

    Comparer les taux globaux serait trompeur : le nightly accepte un filtre
    `--category`, donc un run partiel (5 tâches easy) face à une baseline complète
    (20 tâches) produirait un delta qui ne mesure que la différence de périmètre.
    On ne compare donc que les tâches présentes des deux côtés.

    Retourne (ok, message). ok=False ⇒ régression au-delà du seuil.
    """
    base_by_id = {r["task_id"]: r for r in baseline}
    latest_by_id = {r["task_id"]: r for r in latest}
    common = sorted(base_by_id.keys() & latest_by_id.keys())

    if not common:
        return True, (
            f"::warning::Aucune tâche commune entre baseline ({len(baseline)}) et "
            f"run courant ({len(latest)}) — rien de comparable, gate neutre."
        )

    base_rate = success_rate([base_by_id[t] for t in common])
    new_rate = success_rate([latest_by_id[t] for t in common])
    delta = new_rate - base_rate

    scope = f"{len(common)} tâche(s) commune(s)"
    if len(common) < len(latest):
        scope += f" — {len(latest) - len(common)} hors baseline, non jugée(s)"

    header = (
        f"Succès baseline={base_rate:.1%}  courant={new_rate:.1%}  "
        f"Δ={delta:+.1%}  ({scope})"
    )

    if delta < -max_drop:
        broken = [
            t
            for t in common
            if base_by_id[t].get("success") and not latest_by_id[t].get("success")
        ]
        detail = f" Tâches passées au rouge : {', '.join(broken)}." if broken else ""
        return False, (
            f"{header}\n::error::Régression : le taux de succès chute de "
            f"{delta:+.1%} (seuil {-max_drop:+.1%}).{detail}"
        )

    return True, f"{header}\n✓ Pas de régression significative."


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Gate de non-régression du bench Klody")
    p.add_argument("--baseline", type=Path, default=RESULTS_DIR / "baseline.json")
    p.add_argument("--latest", type=Path, default=RESULTS_DIR / "latest.json")
    p.add_argument(
        "--max-drop",
        type=float,
        default=DEFAULT_MAX_DROP,
        help=f"chute de taux de succès tolérée (défaut {DEFAULT_MAX_DROP})",
    )
    args = p.parse_args(argv)

    if not args.latest.exists():
        print(f"::error::{args.latest} absent — le bench n'a pas écrit ses résultats")
        return 1

    if not args.baseline.exists():
        # Non bloquant (une pile fraîchement installée n'a pas encore de référence),
        # mais ANNOTÉ : la version précédente sortait en 0 sans trace, ce qui rendait
        # un gate mort indiscernable d'un gate vert.
        print(
            f"::warning::Pas de baseline ({args.baseline}) — gate neutralisé. "
            "Promouvoir un run de référence : `python -m bench.run --promote-baseline`, "
            "puis committer bench/results/baseline.json."
        )
        return 0

    try:
        baseline = load_results(args.baseline)
        latest = load_results(args.latest)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"::error::Lecture des résultats impossible — {exc}")
        return 1

    ok, message = compare(baseline, latest, max_drop=args.max_drop)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
