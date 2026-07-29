"""Comparaison libre de deux runs (ou deux séries de runs) du bench.

Complète `bench.gate`, qui ne sait comparer qu'un run à la baseline versionnée avec
un verdict binaire pour la CI. Ici l'usage est l'**A/B** : mettre deux configurations
face à face — typiquement deux cerveaux — et lire où l'écart se situe réellement.

Le parsing des fichiers est délégué à `bench.gate.load_results` : un seul endroit
connaît le format écrit par `bench.run`, ce qui évite la dérive qui avait rendu le
gate inopérant.

**Répétitions.** Un agent LLM est non-déterministe : une paire de runs ne sépare pas
un vrai écart d'un coup de dé. Chaque côté accepte donc plusieurs fichiers, agrégés
par `task_id`. En attendant un `bench.run --repeat`, lancer le bench N fois et passer
les N fichiers fait le travail :

    for i in 1 2 3; do python -m bench.run --label qwen_$i; done

Usage:
    python -m bench.compare results/a.json results/b.json
    python -m bench.compare -a a1.json a2.json -b b1.json b2.json \\
        --label-a qwen3.6 --label-b gpt-oss-120b
    python -m bench.compare a.json b.json --format md > compare.md
"""
from __future__ import annotations

import argparse
import dataclasses
import statistics
import sys
from pathlib import Path

from bench.gate import load_results

RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclasses.dataclass
class TaskStats:
    """Agrégat d'une tâche sur N répétitions d'un même côté."""

    task_id: str
    category: str
    runs: int
    successes: int
    latency_s: float
    tokens_per_sec: float
    tool_calls_broken: float
    iterations: float

    @property
    def success_rate(self) -> float:
        return self.successes / self.runs if self.runs else 0.0


@dataclasses.dataclass
class Side:
    """Un côté de l'A/B : un label, N fichiers, les stats par tâche."""

    label: str
    paths: list[Path]
    stats: dict[str, TaskStats]

    @property
    def repeats(self) -> int:
        return len(self.paths)


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def aggregate(runs: list[list[dict]]) -> dict[str, TaskStats]:
    """Agrège N runs en stats par `task_id` (moyennes, succès sur N)."""
    by_task: dict[str, list[dict]] = {}
    for run in runs:
        for result in run:
            by_task.setdefault(result["task_id"], []).append(result)

    stats: dict[str, TaskStats] = {}
    for task_id, entries in by_task.items():
        stats[task_id] = TaskStats(
            task_id=task_id,
            category=entries[0].get("category", "?"),
            runs=len(entries),
            successes=sum(1 for e in entries if e.get("success")),
            latency_s=_mean([e.get("latency_s", 0.0) for e in entries]),
            tokens_per_sec=_mean([e.get("tokens_per_sec", 0.0) for e in entries]),
            tool_calls_broken=_mean([e.get("tool_calls_broken", 0) for e in entries]),
            iterations=_mean([e.get("iterations", 0) for e in entries]),
        )
    return stats


def load_side(label: str, paths: list[Path]) -> Side:
    return Side(label=label, paths=paths, stats=aggregate([load_results(p) for p in paths]))


def _pct(value: float) -> str:
    return f"{value:.0%}"


def _delta_pct(before: float, after: float) -> str:
    """Écart relatif lisible. « n/a » quand la référence est nulle."""
    if before == 0:
        return "n/a"
    return f"{(after - before) / before:+.0%}"


def _aggregate_of(side: Side, task_ids: list[str]) -> dict[str, float]:
    picked = [side.stats[t] for t in task_ids]
    return {
        "success_rate": _mean([s.success_rate for s in picked]),
        "latency_s": _mean([s.latency_s for s in picked]),
        "tokens_per_sec": _mean([s.tokens_per_sec for s in picked]),
        "tool_calls_broken": _mean([s.tool_calls_broken for s in picked]),
        "iterations": _mean([s.iterations for s in picked]),
    }


def _flips(a: Side, b: Side, task_ids: list[str]) -> tuple[list[str], list[str]]:
    """(régressions, gains), triées par amplitude décroissante."""
    regressions, gains = [], []
    for task_id in task_ids:
        rate_a, rate_b = a.stats[task_id].success_rate, b.stats[task_id].success_rate
        if rate_b < rate_a:
            regressions.append((rate_a - rate_b, task_id, rate_a, rate_b))
        elif rate_b > rate_a:
            gains.append((rate_b - rate_a, task_id, rate_a, rate_b))

    def fmt(rows: list[tuple[float, str, float, float]]) -> list[str]:
        return [
            f"{task_id} ({_pct(rate_a)} → {_pct(rate_b)})"
            for _, task_id, rate_a, rate_b in sorted(rows, reverse=True)
        ]

    return fmt(regressions), fmt(gains)


def build_report(a: Side, b: Side, fmt: str = "text") -> str:
    """Rapport comparatif sur l'intersection des task_id des deux côtés."""
    common = sorted(a.stats.keys() & b.stats.keys())
    lines: list[str] = []

    title = f"Comparaison — {a.label} vs {b.label}"
    lines += [f"# {title}", ""] if fmt == "md" else [title, "=" * len(title), ""]

    if not common:
        lines.append(
            f"Aucune tâche commune ({len(a.stats)} côté {a.label}, "
            f"{len(b.stats)} côté {b.label}) — rien à comparer."
        )
        return "\n".join(lines) + "\n"

    only_a = sorted(a.stats.keys() - b.stats.keys())
    only_b = sorted(b.stats.keys() - a.stats.keys())
    lines.append(
        f"{len(common)} tâche(s) commune(s) · "
        f"{a.label} : {a.repeats} run(s) · {b.label} : {b.repeats} run(s)"
    )
    if only_a:
        lines.append(f"Ignorées (absentes de {b.label}) : {', '.join(only_a)}")
    if only_b:
        lines.append(f"Ignorées (absentes de {a.label}) : {', '.join(only_b)}")

    # Un seul run par côté ne permet aucune conclusion : la variance d'un agent
    # LLM sur une tâche dépasse couramment les écarts qu'on cherche à mesurer.
    if min(a.repeats, b.repeats) < 2:
        lines += [
            "",
            "⚠️  Un seul run d'un côté au moins : les écarts ci-dessous ne sont pas "
            "distinguables du bruit. Relancer le bench ≥3 fois par côté pour conclure.",
        ]

    agg_a, agg_b = _aggregate_of(a, common), _aggregate_of(b, common)
    lines += ["", "## Agrégats" if fmt == "md" else "Agrégats", ""]

    rows = [
        (
            "Taux de succès",
            _pct(agg_a["success_rate"]),
            _pct(agg_b["success_rate"]),
            f"{(agg_b['success_rate'] - agg_a['success_rate']) * 100:+.0f} pts",
        ),
        (
            "Latence moyenne",
            f"{agg_a['latency_s']:.1f}s",
            f"{agg_b['latency_s']:.1f}s",
            _delta_pct(agg_a["latency_s"], agg_b["latency_s"]),
        ),
        (
            "Débit moyen",
            f"{agg_a['tokens_per_sec']:.1f} tok/s",
            f"{agg_b['tokens_per_sec']:.1f} tok/s",
            _delta_pct(agg_a["tokens_per_sec"], agg_b["tokens_per_sec"]),
        ),
        (
            "Tool calls cassés",
            f"{agg_a['tool_calls_broken']:.2f}/tâche",
            f"{agg_b['tool_calls_broken']:.2f}/tâche",
            _delta_pct(agg_a["tool_calls_broken"], agg_b["tool_calls_broken"]),
        ),
        (
            "Itérations ReAct",
            f"{agg_a['iterations']:.1f}",
            f"{agg_b['iterations']:.1f}",
            _delta_pct(agg_a["iterations"], agg_b["iterations"]),
        ),
    ]
    lines += _table(["Métrique", a.label, b.label, "Δ"], rows, fmt)

    # Par catégorie — un modèle peut gagner sur easy et perdre sur hard.
    cats = sorted({a.stats[t].category for t in common})
    if len(cats) > 1:
        lines += ["", "## Par catégorie" if fmt == "md" else "Par catégorie", ""]
        cat_rows = []
        for cat in cats:
            ids = [t for t in common if a.stats[t].category == cat]
            ca, cb = _aggregate_of(a, ids), _aggregate_of(b, ids)
            cat_rows.append(
                (
                    f"{cat} ({len(ids)})",
                    _pct(ca["success_rate"]),
                    _pct(cb["success_rate"]),
                    f"{(cb['success_rate'] - ca['success_rate']) * 100:+.0f} pts",
                )
            )
        lines += _table(["Catégorie", a.label, b.label, "Δ succès"], cat_rows, fmt)

    regressions, gains = _flips(a, b, common)
    lines += ["", "## Bascules" if fmt == "md" else "Bascules", ""]
    if not regressions and not gains:
        lines.append("Aucune : mêmes tâches réussies des deux côtés.")
    else:
        for label, items in (("Régressions", regressions), ("Gains", gains)):
            if items:
                lines.append(f"**{label}** ({len(items)})" if fmt == "md" else f"{label} :")
                lines += [f"- {item}" for item in items]
                lines.append("")

    lines += ["", "## Détail par tâche" if fmt == "md" else "Détail par tâche", ""]
    detail_rows = [
        (
            task_id,
            a.stats[task_id].category,
            f"{a.stats[task_id].successes}/{a.stats[task_id].runs}",
            f"{b.stats[task_id].successes}/{b.stats[task_id].runs}",
            f"{a.stats[task_id].latency_s:.1f}s",
            f"{b.stats[task_id].latency_s:.1f}s",
            _delta_pct(a.stats[task_id].latency_s, b.stats[task_id].latency_s),
        )
        for task_id in common
    ]
    lines += _table(
        ["Tâche", "Cat", f"OK {a.label}", f"OK {b.label}", f"Lat {a.label}",
         f"Lat {b.label}", "Δ lat"],
        detail_rows,
        fmt,
    )

    return "\n".join(lines) + "\n"


def _table(headers: list[str], rows: list[tuple], fmt: str) -> list[str]:
    """Table Markdown ou colonnes alignées, selon `fmt`."""
    cells = [[str(c) for c in row] for row in rows]
    if fmt == "md":
        return [
            "| " + " | ".join(headers) + " |",
            "|" + "|".join("---" for _ in headers) + "|",
            *("| " + " | ".join(row) + " |" for row in cells),
        ]

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in cells)) if cells else len(headers[i])
        for i in range(len(headers))
    ]
    fmt_row = "  ".join(f"{{:<{w}}}" for w in widths)
    return [
        fmt_row.format(*headers).rstrip(),
        fmt_row.format(*("-" * w for w in widths)).rstrip(),
        *(fmt_row.format(*row).rstrip() for row in cells),
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Compare deux runs (ou deux séries de runs) du bench Klody",
    )
    p.add_argument("runs", nargs="*", type=Path, help="deux fichiers : <avant> <après>")
    p.add_argument("-a", nargs="+", type=Path, default=None, metavar="JSON",
                   help="fichier(s) du côté A (répétitions agrégées)")
    p.add_argument("-b", nargs="+", type=Path, default=None, metavar="JSON",
                   help="fichier(s) du côté B")
    p.add_argument("--label-a", default=None, help="nom du côté A (défaut : nom de fichier)")
    p.add_argument("--label-b", default=None, help="nom du côté B")
    p.add_argument("--format", choices=["text", "md"], default="text")
    args = p.parse_args(argv)

    if args.a and args.b:
        paths_a, paths_b = args.a, args.b
    elif len(args.runs) == 2:
        paths_a, paths_b = [args.runs[0]], [args.runs[1]]
    else:
        p.error("fournir soit deux fichiers positionnels, soit -a … -b …")

    missing = [p_ for p_ in (*paths_a, *paths_b) if not p_.exists()]
    if missing:
        print(f"Fichier(s) introuvable(s) : {', '.join(str(m) for m in missing)}", file=sys.stderr)
        return 1

    label_a = args.label_a or (paths_a[0].stem if len(paths_a) == 1 else f"A ({len(paths_a)} runs)")
    label_b = args.label_b or (paths_b[0].stem if len(paths_b) == 1 else f"B ({len(paths_b)} runs)")

    try:
        side_a = load_side(label_a, paths_a)
        side_b = load_side(label_b, paths_b)
    except ValueError as exc:
        print(f"Lecture impossible — {exc}", file=sys.stderr)
        return 1

    print(build_report(side_a, side_b, fmt=args.format), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
