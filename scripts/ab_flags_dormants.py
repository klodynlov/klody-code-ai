#!/usr/bin/env python3
"""A/B des flags dormants au banc — lot 2.3 du plan d'optimisation.

Trois flags jamais jugés au banc :
  - SELF_CRITIQUE_ENABLED (auto-critique du brouillon, 1 appel LLM de plus)
  - SKILLS_ROUTER_ENABLED (routage par skills, bench dédié existant)
  - PREVIEW_FEEDBACK_TIMEOUT_S (attente feedback preview, actif en .env)

Protocole par flag : --repeat 3 ON, --repeat 3 OFF, alternés (ON-OFF-ON-OFF-ON-OFF),
sur les paliers expert + discovery (+ real_repo si disponible).

Gate : le défaut de config.py ne change que si les verdicts ON > OFF avec au moins
2 TÂCHES d'écart sur 3 passes. Sous ce seuil = bruit. Résultat négatif = flag
laissé OFF, piste close.

Usage :
    # Un seul flag
    python scripts/ab_flags_dormants.py --flag SELF_CRITIQUE_ENABLED

    # Tous les flags (séquentiel)
    python scripts/ab_flags_dormants.py --all

    # Analyser des résultats existants (sans relancer le bench)
    python scripts/ab_flags_dormants.py --flag SELF_CRITIQUE_ENABLED \
        --on-results bench/results/*self_critique_ON*.json \
        --off-results bench/results/*self_critique_OFF*.json

    # Dry-run : afficher les commandes sans les exécuter
    python scripts/ab_flags_dormants.py --flag SELF_CRITIQUE_ENABLED --dry-run

Codes de sortie :
    0 = analyse effectuée, verdict rendu
    1 = erreur (bench planté, fichier illisible)
    2 = flag ON gagne avec ≥ 2 tâches d'écart (le défaut devrait changer)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.compare import load_side

FLAGS = {
    "SELF_CRITIQUE_ENABLED": {
        "env_on": {"SELF_CRITIQUE_ENABLED": "true"},
        "env_off": {"SELF_CRITIQUE_ENABLED": "false"},
        "label": "self_critique",
    },
    "SKILLS_ROUTER_ENABLED": {
        "env_on": {"SKILLS_ROUTER_ENABLED": "true"},
        "env_off": {"SKILLS_ROUTER_ENABLED": "false"},
        "label": "skills_router",
    },
    "PREVIEW_FEEDBACK_TIMEOUT_S": {
        "env_on": {"PREVIEW_FEEDBACK_TIMEOUT_S": "5"},
        "env_off": {"PREVIEW_FEEDBACK_TIMEOUT_S": "0"},
        "label": "preview_feedback",
    },
}

CATEGORIES = ["expert", "discovery"]
REPEAT = 3
SEUIL_TACHES = 2
PYTHON = os.path.expanduser("~/Projets/klody-code-ai/.venv/bin/python")


def _categories_disponibles() -> list[str]:
    """Détecte les catégories disponibles au bench."""
    try:
        proc = subprocess.run(
            [PYTHON, "-m", "bench.run", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        if "real_repo" in proc.stdout:
            return [*CATEGORIES, "real_repo"]
    except (subprocess.SubprocessError, FileNotFoundError):
        pass  # bench.run absent ou non exécutable — catégories par défaut
    return list(CATEGORIES)


def _run_bench(
    label: str,
    env_extra: dict[str, str],
    categories: list[str],
    repeat: int,
    dry_run: bool = False,
) -> list[Path]:
    """Lance le bench pour chaque catégorie, retourne les fichiers de résultats."""
    fichiers: list[Path] = []
    env = {**os.environ, **env_extra}

    for cat in categories:
        cmd = [
            PYTHON, "-m", "bench.run",
            "--category", cat,
            "--repeat", str(repeat),
            "--label", label,
        ]
        print(f"\n{'='*60}")
        print(f"  {' '.join(cmd)}")
        print(f"  env: {env_extra}")
        print(f"{'='*60}")

        if dry_run:
            print("  [dry-run] commande non exécutée")
            continue

        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True,
            timeout=3600,
        )
        if proc.returncode not in (0, 2):
            print(f"  ✗ bench planté (exit {proc.returncode})", file=sys.stderr)
            print(proc.stderr[-500:] if proc.stderr else "", file=sys.stderr)
            return []

        # Trouver le fichier produit (dernier modifié dans bench/results/)
        results_dir = Path("bench/results")
        candidates = sorted(
            results_dir.glob(f"*{label}*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            fichiers.append(candidates[0])
            print(f"  → {candidates[0]}")

    return fichiers


def _analyser(
    flag_name: str,
    fichiers_on: list[Path],
    fichiers_off: list[Path],
) -> dict:
    """Compare ON vs OFF, applique le gate à 2 tâches d'écart."""
    side_on = load_side("ON", fichiers_on)
    side_off = load_side("OFF", fichiers_off)

    # Par tâche : succès ON vs OFF
    toutes_taches = sorted(set(side_on.stats) | set(side_off.stats))

    lignes: list[dict] = []
    on_gagne = 0
    off_gagne = 0
    egalite = 0

    print(f"\n{'─'*70}")
    print(f"  A/B : {flag_name}")
    print(f"{'─'*70}")
    print(f"  {'tâche':<40} {'ON':>6} {'OFF':>6}  delta")
    print(f"  {'─'*40} {'─'*6} {'─'*6}  {'─'*6}")

    for tid in toutes_taches:
        s_on = side_on.stats.get(tid)
        s_off = side_off.stats.get(tid)
        rate_on = s_on.success_rate if s_on else 0.0
        rate_off = s_off.success_rate if s_off else 0.0
        delta = rate_on - rate_off

        lignes.append({
            "task_id": tid,
            "on_rate": rate_on,
            "off_rate": rate_off,
            "on_runs": s_on.runs if s_on else 0,
            "off_runs": s_off.runs if s_off else 0,
            "on_successes": s_on.successes if s_on else 0,
            "off_successes": s_off.successes if s_off else 0,
            "delta": delta,
            "on_iterations": s_on.iterations if s_on else 0,
            "off_iterations": s_off.iterations if s_off else 0,
            "on_tool_calls_broken": s_on.tool_calls_broken if s_on else 0,
            "off_tool_calls_broken": s_off.tool_calls_broken if s_off else 0,
        })

        if delta > 0.01:
            on_gagne += 1
            marqueur = "← ON"
        elif delta < -0.01:
            off_gagne += 1
            marqueur = "← OFF"
        else:
            egalite += 1
            marqueur = ""

        print(f"  {tid:<40} {rate_on:>5.0%} {rate_off:>5.0%}  {delta:>+.0%} {marqueur}")

    # Verdict
    ecart = on_gagne - off_gagne
    if ecart >= SEUIL_TACHES:
        verdict = "ON_GAGNE"
        exit_code = 2
    elif ecart <= -SEUIL_TACHES:
        verdict = "OFF_GAGNE"
        exit_code = 0
    else:
        verdict = "PAS_D_ECART"
        exit_code = 0

    print(f"\n  ON gagne : {on_gagne} tâches")
    print(f"  OFF gagne : {off_gagne} tâches")
    print(f"  Égalité : {egalite} tâches")
    print(f"  Écart : {ecart:+d} (seuil : ±{SEUIL_TACHES})")
    print()

    if verdict == "ON_GAGNE":
        print(f"  ⚠️  {flag_name} ON gagne avec ≥ {SEUIL_TACHES} tâches d'écart.")
        print("  → Le défaut de config.py devrait passer à ON.")
    elif verdict == "OFF_GAGNE":
        print(f"  ✅  {flag_name} OFF gagne. Flag laissé OFF.")
    else:
        print(f"  ✅  Pas d'écart significatif (< {SEUIL_TACHES} tâches).")
        print(f"  → {flag_name} laissé à son défaut actuel (OFF).")

    return {
        "flag": flag_name,
        "verdict": verdict,
        "exit_code": exit_code,
        "on_gagne": on_gagne,
        "off_gagne": off_gagne,
        "egalite": egalite,
        "ecart": ecart,
        "seuil": SEUIL_TACHES,
        "taches": lignes,
        "fichiers_on": [str(f) for f in fichiers_on],
        "fichiers_off": [str(f) for f in fichiers_off],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--flag", choices=list(FLAGS), default=None,
                    help="Flag à tester")
    p.add_argument("--all", action="store_true",
                    help="Tester les 3 flags séquentiellement")
    p.add_argument("--repeat", type=int, default=REPEAT,
                    help=f"Passes par bras (défaut {REPEAT})")
    p.add_argument("--on-results", nargs="+", type=Path, default=None,
                    metavar="JSON", help="Résultats ON existants (skip bench)")
    p.add_argument("--off-results", nargs="+", type=Path, default=None,
                    metavar="JSON", help="Résultats OFF existants (skip bench)")
    p.add_argument("--json", type=str, default=None, metavar="FICHIER",
                    help="Exporter les verdicts en JSON")
    p.add_argument("--dry-run", action="store_true",
                    help="Afficher les commandes sans exécuter")
    args = p.parse_args(argv)

    if not args.flag and not args.all:
        p.error("--flag ou --all requis")

    flags_a_tester = list(FLAGS) if args.all else [args.flag]
    categories = _categories_disponibles()
    verdicts: list[dict] = []
    exit_code = 0

    for flag_name in flags_a_tester:
        spec = FLAGS[flag_name]

        if args.on_results and args.off_results:
            fichiers_on = args.on_results
            fichiers_off = args.off_results
        else:
            label_on = f"{spec['label']}_ON"
            label_off = f"{spec['label']}_OFF"

            fichiers_on = _run_bench(
                label_on, spec["env_on"], categories, args.repeat, args.dry_run,
            )
            fichiers_off = _run_bench(
                label_off, spec["env_off"], categories, args.repeat, args.dry_run,
            )

        if args.dry_run:
            print(f"\n  [dry-run] Analyse de {flag_name} sautée")
            continue

        if not fichiers_on or not fichiers_off:
            print(f"✗ Résultats manquants pour {flag_name}", file=sys.stderr)
            return 1

        synth = _analyser(flag_name, fichiers_on, fichiers_off)
        verdicts.append(synth)
        if synth["exit_code"] > exit_code:
            exit_code = synth["exit_code"]

    if args.json and verdicts:
        Path(args.json).write_text(
            json.dumps(verdicts, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"\n  → {args.json}")

    if len(verdicts) > 1:
        print(f"\n{'='*60}")
        print("  RÉCAPITULATIF")
        print(f"{'='*60}")
        for v in verdicts:
            symbole = "⚠️ " if v["verdict"] == "ON_GAGNE" else "✅"
            print(f"  {symbole} {v['flag']}: {v['verdict']} "
                  f"(écart {v['ecart']:+d}, seuil ±{v['seuil']})")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
