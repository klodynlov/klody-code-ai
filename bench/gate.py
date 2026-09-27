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
from fractions import Fraction
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
#
# ── Avec N passes (`bench.run --repeat N`) — ajouté le 2026-09-27 ──────────────
#
# Le tableau ci-dessus suppose UNE passe. Depuis que `compare()` juge toutes les
# passes (moyenne des taux par tâche, cf. son docstring), une passe en échec vaut
# 1/N de tâche cassée. Première rouge, comptée en PASSES en échec, pour un run
# courant à N passes face à une baseline à une passe — MESURÉE, et identique
# quelle que soit la place des échecs (tests/test_gate_sensibilite.py,
# `TABLEAU_PASSES`) :
#
#   seuil 0.09         N=1   N=3   N=5
#   baseline 30/30      3     9    14
#   baseline 29/30      4    12    19
#   baseline 20/20      2     6    10
#   intersection 5/5    1     2     3      (`--category discovery`, par ex.)
#
# Lecture : en tâches ENTIÈREMENT cassées, rien ne change — 3 sur 30 à N=1
# comme à N=3 (9 passes), c'est un seuil sur un taux. Ce qui change, c'est qu'un
# échec ÉPARS pèse sa fraction : une passe ratée sur 3 = un tiers de tâche. Le
# cas vécu (discovery × 3, une passe ratée sur 15) rend Δ −6,7 % : VERT sous
# 0.09, et c'est voulu — un échec sur trois passes n'est pas une tâche cassée.
# Mais il n'est plus invisible : `compare()` nomme en `::notice::` toute tâche
# en baisse sous le seuil. Il en faut 2 pour rougir.
#
# ⚠️ À N=5 et 30 tâches, 14 passes = 2,8 tâches : la granularité devient plus
# fine que la tâche, la porte n'est donc PAS desserrée par la répétition.
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


def success_rate(results: list[dict]) -> Fraction:
    """Taux de succès EXACT sur l'ensemble fourni. 0 si vide.

    Exact, et non flottant, depuis que des taux fractionnaires (2/3, 4/5) sont
    additionnés — cf. `_taux_moyen`.
    """
    if not results:
        return Fraction(0)
    return Fraction(sum(1 for r in results if r.get("success")), len(results))


def passes_par_tache(results: list[dict]) -> dict[str, list[dict]]:
    """Regroupe les résultats par `task_id` en gardant TOUTES les passes.

    `bench.run --repeat N` écrit N entrées par tâche dans le même fichier. Un dict
    `{task_id: résultat}` n'en garde que la dernière : c'est exactement ce que
    faisait `compare()` jusqu'au 2026-09-27 (cf. son docstring).
    """
    par_tache: dict[str, list[dict]] = {}
    for r in results:
        par_tache.setdefault(r["task_id"], []).append(r)
    return par_tache


def _taux_moyen(par_tache: dict[str, list[dict]], taches: list[str]) -> Fraction:
    """Moyenne des taux PAR TÂCHE — chaque tâche pèse 1, quel que soit son nombre
    de passes. Un taux poolé (succès / passes, toutes tâches confondues) ferait
    peser davantage une tâche plus répétée que les autres.

    ⚠️ En arithmétique EXACTE, arrondie une seule fois (dans `compare`). Sommer
    des flottants 0,8 fait dériver le dernier bit, et sur la frontière du seuil le
    verdict dépendait alors de QUELLES passes avaient échoué, à taux identique —
    mesuré le 2026-09-27 : baseline 29/30, seuil 0.06, 5 passes ⇒ rouge à 14 ou à
    15 passes en échec selon leur place.
    """
    return sum((success_rate(par_tache[t]) for t in taches), Fraction(0)) / len(taches)


def _passes(par_tache: dict[str, list[dict]], taches: list[str]) -> str:
    """« 1 passe », « 3 passes », ou « 1 à 3 passes » si elles diffèrent."""
    n = sorted({len(par_tache[t]) for t in taches})
    if len(n) > 1:
        return f"{n[0]} à {n[-1]} passes"
    return f"{n[0]} passe" + ("s" if n[0] > 1 else "")


def _compte(entries: list[dict]) -> str:
    return f"{sum(1 for e in entries if e.get('success'))}/{len(entries)}"


def compare(
    baseline: list[dict],
    latest: list[dict],
    max_drop: float = DEFAULT_MAX_DROP,
) -> tuple[bool, str]:
    """Compare deux runs sur l'INTERSECTION de leurs task_id, TOUTES passes jugées.

    Comparer les taux globaux serait trompeur : le nightly accepte un filtre
    `--category`, donc un run partiel (5 tâches easy) face à une baseline complète
    (20 tâches) produirait un delta qui ne mesure que la différence de périmètre.
    On ne compare donc que les tâches présentes des deux côtés.

    Le taux d'un côté est la moyenne des taux par tâche (succès / passes), des
    DEUX côtés : la baseline peut elle-même avoir été promue depuis un run répété.

    ⚠️ Jusqu'au 2026-09-27, les deux côtés étaient indexés `{task_id: résultat}` :
    sur un run `--repeat N`, seule la DERNIÈRE passe de chaque tâche était jugée,
    et les N−1 autres étaient comptées comme « hors baseline, non jugée(s) ».
    Vécu : `--category discovery --repeat 3` = 14/15, `config_precedence` ❌ en
    passe 1 ; la porte a rendu « courant=100.0% Δ=+0.0% (5 tâche(s) commune(s) —
    10 hors baseline, non jugée(s)) ✓ ». L'échec était invisible et le message
    affirmait un fait faux. Même écrasement rejoué sur
    `reference_2026-07-30_garde_arret_apres.json` (24/25 lu 100 %) et
    `reference_2026-07-30_lot_trace_ouverture_docs.json` (68 % lu 80 %, deux
    tâches en baisse non nommées).

    Retourne (ok, message). ok=False ⇒ régression au-delà du seuil.
    """
    base = passes_par_tache(baseline)
    cour = passes_par_tache(latest)
    common = sorted(base.keys() & cour.keys())

    if not common:
        return True, (
            f"::warning::Aucune tâche commune entre baseline ({len(base)} tâche(s)) et "
            f"run courant ({len(cour)} tâche(s)) — rien de comparable, gate neutre."
        )

    base_exact = _taux_moyen(base, common)
    new_exact = _taux_moyen(cour, common)
    # UN seul arrondi, sur l'écart exact : un écart qui vaut exactement le seuil
    # tombe alors sur le même flottant que `max_drop` et PASSE, comme documenté.
    # L'ancien `succès/n − succès/n` en flottants rougissait sur 853 égalités
    # exactes (n ≤ 100, dix seuils de 0.05 à 0.25) — 20/20 → 19/20 sous
    # `--max-drop 0.05`, par exemple. Aucune à 0.09 sous 100 tâches : le nightly
    # (30 tâches) ne voit pas la différence.
    delta = float(new_exact - base_exact)
    base_rate, new_rate = float(base_exact), float(new_exact)

    scope = f"{len(common)} tâche(s) commune(s)"
    passes_base, passes_cour = _passes(base, common), _passes(cour, common)
    if (passes_base, passes_cour) != ("1 passe", "1 passe"):
        # Des passes ne sont PAS des tâches : les nommer à part, sans quoi un
        # `--repeat 3` se lirait « 10 hors baseline ».
        scope += f", courant {passes_cour}, baseline {passes_base}"
    hors = len(cour.keys() - base.keys())
    if hors:
        scope += f" — {hors} tâche(s) hors baseline, non jugée(s)"

    header = (
        f"Succès baseline={base_rate:.1%}  courant={new_rate:.1%}  "
        f"Δ={delta:+.1%}  ({scope})"
    )

    en_baisse = [
        f"{t} ({_compte(base[t])} → {_compte(cour[t])})"
        for t in common
        if success_rate(cour[t]) < success_rate(base[t])
    ]

    if delta < -max_drop:
        detail = f" Tâches en baisse : {', '.join(en_baisse)}." if en_baisse else ""
        return False, (
            f"{header}\n::error::Régression : le taux de succès chute de "
            f"{delta:+.1%} (seuil {-max_drop:+.1%}).{detail}"
        )

    message = f"{header}\n✓ Pas de régression significative."
    if en_baisse:
        # Sous le seuil, donc pas rouge — mais NOMMÉ : un échec en passe 1 sur 3
        # ne doit plus pouvoir disparaître derrière un ✓.
        message += f"\n::notice::En baisse sous le seuil : {', '.join(en_baisse)}."
    return True, message


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
