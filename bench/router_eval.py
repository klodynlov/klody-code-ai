"""Évaluation du Router sur le bench — F1 macro sur difficulty.

Usage :
    BACKEND=mlx python -m bench.router_eval                  # toutes les tâches
    BACKEND=mlx python -m bench.router_eval --label my_run   # avec label custom

Objectif Roadmap v2 #4 : F1 macro > 0.8 sur la classification easy/medium/hard.

Sortie : bench/results/<timestamp>_router_<label>.json + .md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# repo root sur sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bench.framework import discover_tasks, filter_tasks  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ---------------------------------------------------------------------------- #
# Métriques                                                                    #
# ---------------------------------------------------------------------------- #


def _precision_recall_f1(y_true: list[str], y_pred: list[str], label: str) -> dict:
    tp = sum(1 for t, p in zip(y_true, y_pred, strict=False) if t == label and p == label)
    fp = sum(1 for t, p in zip(y_true, y_pred, strict=False) if t != label and p == label)
    fn = sum(1 for t, p in zip(y_true, y_pred, strict=False) if t == label and p != label)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}


def _macro_f1(y_true: list[str], y_pred: list[str], labels: list[str]) -> tuple[float, dict]:
    per_label = {lbl: _precision_recall_f1(y_true, y_pred, lbl) for lbl in labels}
    macro = sum(v["f1"] for v in per_label.values()) / len(labels)
    return macro, per_label


def _confusion(y_true: list[str], y_pred: list[str], labels: list[str]) -> dict:
    """Matrice de confusion {true_label: {pred_label: count}}."""
    cm: dict[str, dict[str, int]] = {lab: dict.fromkeys(labels, 0) for lab in labels}
    for t, p in zip(y_true, y_pred, strict=False):
        if t in cm and p in cm[t]:
            cm[t][p] += 1
    return cm


# ---------------------------------------------------------------------------- #
# Runner                                                                       #
# ---------------------------------------------------------------------------- #


def _run_router_on_tasks(label: str | None = None) -> Path:
    from agent.router import Router

    tasks = discover_tasks()
    selected = [
        cls for cls in filter_tasks(tasks)
        # Garder TOUTES les tâches, y compris les stubs medium/hard
        # (leurs prompts sont déjà définis, le router classifie sans exécuter)
    ]
    print(f"→ {len(selected)} tâches à classifier")

    router = Router()
    rows = []
    t0 = time.perf_counter()

    for cls in selected:
        task = cls()
        prompt = task.prompt
        t_task = time.perf_counter()
        decision = router.classify(prompt)
        latency = time.perf_counter() - t_task
        rows.append({
            "task_id": task.id,
            "expected": task.category,
            "predicted": decision.difficulty,
            "task_type": decision.task_type,
            "reasoning": decision.reasoning,
            "latency_s": round(latency, 2),
            "match": task.category == decision.difficulty,
        })
        marker = "✅" if task.category == decision.difficulty else "❌"
        print(f"  {marker} {task.id:35s} expected={task.category:6s} pred={decision.difficulty:6s} ({latency:.1f}s) — {decision.reasoning[:60]}")

    total_time = time.perf_counter() - t0

    # Métriques
    y_true = [r["expected"] for r in rows]
    y_pred = [r["predicted"] for r in rows]
    labels = ["easy", "medium", "hard"]
    macro_f1, per_label = _macro_f1(y_true, y_pred, labels)
    accuracy = sum(1 for r in rows if r["match"]) / len(rows)
    cm = _confusion(y_true, y_pred, labels)

    summary = {
        "n_tasks": len(rows),
        "accuracy": round(accuracy, 3),
        "macro_f1": round(macro_f1, 3),
        "per_label": per_label,
        "confusion_matrix": cm,
        "total_latency_s": round(total_time, 2),
        "avg_latency_per_task_s": round(total_time / max(1, len(rows)), 2),
        "distribution_predicted": dict(Counter(y_pred)),
        "distribution_expected": dict(Counter(y_true)),
    }

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  ACCURACY : {accuracy:.1%}")
    print(f"  MACRO F1 : {macro_f1:.3f}  (cible roadmap : > 0.800)")
    print(f"  Latence moy. : {summary['avg_latency_per_task_s']}s/tâche")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Sortie
    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    json_path = RESULTS_DIR / f"{timestamp}_router{suffix}.json"
    md_path = RESULTS_DIR / f"{timestamp}_router{suffix}.md"

    json_path.write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_markdown(md_path, summary, rows, label or "")

    print(f"\n📊  JSON → {json_path}")
    print(f"📊  MD   → {md_path}")
    return json_path


def _write_markdown(path: Path, summary: dict, rows: list[dict], label: str) -> None:
    lines = [
        f"# Router eval — {label or 'baseline'}",
        "",
        f"**Accuracy** : {summary['accuracy']:.1%}",
        f"**Macro F1** : {summary['macro_f1']:.3f}  (cible : > 0.800)",
        f"**Latence moyenne** : {summary['avg_latency_per_task_s']}s/tâche",
        "",
        "## Par label",
        "",
        "| Label | Precision | Recall | F1 | Support |",
        "|-------|-----------|--------|----|---------| ",
    ]
    for lbl, m in summary["per_label"].items():
        lines.append(
            f"| {lbl} | {m['precision']:.2f} | {m['recall']:.2f} | "
            f"{m['f1']:.2f} | {m['support']} |"
        )

    lines += ["", "## Matrice de confusion (lignes=vrai, colonnes=prédit)", ""]
    labels = list(summary["confusion_matrix"].keys())
    lines.append("|       | " + " | ".join(labels) + " |")
    lines.append("|-------|" + "|".join(["----"] * len(labels)) + "|")
    for true_lbl in labels:
        cells = [str(summary["confusion_matrix"][true_lbl].get(p, 0)) for p in labels]
        lines.append(f"| **{true_lbl}** | " + " | ".join(cells) + " |")

    lines += ["", "## Détail par tâche", "",
              "| Task | Expected | Predicted | OK | Type | Latence | Reasoning |",
              "|------|----------|-----------|----|------|---------|-----------|"]
    for r in rows:
        ok = "✅" if r["match"] else "❌"
        lines.append(
            f"| `{r['task_id']}` | {r['expected']} | {r['predicted']} | {ok} | "
            f"{r['task_type']} | {r['latency_s']}s | {r['reasoning'][:80]} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Évalue le Router sur le bench")
    p.add_argument("--label", default=None, help="Label du run (ex: mlx_qwen3coder)")
    args = p.parse_args(argv)
    _run_router_on_tasks(args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
