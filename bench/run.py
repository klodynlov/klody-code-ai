"""Runner principal du bench Klody.

Usage:
    python -m bench.run                          # tout
    python -m bench.run --category easy          # filtre catégorie
    python -m bench.run --task easy/rename_var   # une tâche
    python -m bench.run --dry-run                # affiche les tâches sans exécuter
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

# Ajouter le repo root au sys.path pour pouvoir importer agent.* config.*
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bench.framework import (  # noqa: E402
    Result,
    Task,
    discover_tasks,
    filter_tasks,
    stopwatch,
)
from bench import metrics  # noqa: E402


RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _run_klody(prompt: str, workdir: Path) -> None:
    """Lance Klody sur prompt avec PROJECT_ROOT=workdir.

    Stratégie de patching (3 niveaux) :
    1. os.environ   → pris en compte si un module relit l'env au runtime
    2. config.*     → reload du module config + patch de l'attr
    3. modules déjà importés ayant capturé PROJECT_ROOT → patch direct
    4. FileManager.root sur l'instance créée → override ultime

    On préfixe aussi le prompt avec le chemin de workdir pour que
    le modèle sache où chercher les fichiers.
    """
    import importlib

    # Niveau 1 : env
    os.environ["PROJECT_ROOT"] = str(workdir)

    # Niveau 2 : reload config
    import config as klody_config
    importlib.reload(klody_config)
    klody_config.PROJECT_ROOT = workdir

    # Niveau 3 : patcher tous les modules qui ont capturé PROJECT_ROOT
    _MODULES_WITH_ROOT = [
        "tools.file_manager",
        "agent.orchestrator",
        "tools.terminal",
        "config",
    ]
    for mod_name in _MODULES_WITH_ROOT:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            if hasattr(mod, "PROJECT_ROOT"):
                mod.PROJECT_ROOT = workdir

    from agent.memory import ConversationMemory
    from agent.orchestrator import Orchestrator

    memory = ConversationMemory()
    orch = Orchestrator(memory=memory)

    # Niveau 4 : override direct de l'instance FileManager
    orch.file_manager.root = workdir.resolve()

    # Patch aussi le system message déjà injecté par Orchestrator.__init__
    # (il contient l'ancien PROJECT_ROOT en texte)
    for msg in orch.memory.messages:
        if msg.get("role") == "system" and "content" in msg:
            msg["content"] = msg["content"].replace(
                "Dossier projet actif:",
                f"Dossier projet actif (bench): {workdir}\n#",
            )

    # Prompt enrichi : contexte workdir + instruction explicite d'utiliser write_file
    full_prompt = (
        f"[Répertoire de travail : {workdir}]\n"
        f"Les fichiers sont dans ce répertoire. "
        f"Utilise read_file pour lire, write_file pour sauvegarder toute modification.\n\n"
        f"{prompt}"
    )
    orch.run(full_prompt)


def _run_one(task_cls: type[Task]) -> Result:
    task = task_cls()
    with tempfile.TemporaryDirectory(prefix="klody-bench-") as tmp:
        workdir = Path(tmp)
        try:
            task.setup(workdir)
        except Exception as exc:
            return Result(
                task_id=task.id,
                category=task.category,
                success=False,
                detail="setup failed",
                latency_s=0.0,
                tokens_generated=0,
                tokens_per_sec=0.0,
                tool_calls_total=0,
                tool_calls_broken=0,
                iterations=0,
                error=f"setup: {exc}",
            )

        _, elapsed = stopwatch()
        err: str | None = None
        with metrics.capture() as m:
            try:
                _run_klody(task.prompt, workdir)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"

        latency = elapsed()
        tps = (m.tokens_generated / latency) if latency > 0 else 0.0

        try:
            success, detail = task.validate(workdir)
        except Exception as exc:
            success, detail = False, f"validate raised: {exc}"

        return Result(
            task_id=task.id,
            category=task.category,
            success=success,
            detail=detail,
            latency_s=round(latency, 2),
            tokens_generated=m.tokens_generated,
            tokens_per_sec=round(tps, 1),
            tool_calls_total=m.tool_calls_total,
            tool_calls_broken=m.tool_calls_broken,
            iterations=m.iterations,
            error=err,
        )


def _write_json(results: list[Result], path: Path) -> None:
    path.write_text(
        json.dumps(
            [r.__dict__ for r in results],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_markdown(results: list[Result], path: Path, label: str) -> None:
    lines = [
        f"# Bench Klody — {label}",
        "",
        f"_{len(results)} tâches_  ",
        f"Succès global : **{sum(1 for r in results if r.success)}/{len(results)}**",
        "",
        "| Task | Cat | OK | Latence | Tokens | tok/s | Tools | Cassés | Iter | Détail |",
        "|------|-----|----|---------|--------|-------|-------|--------|------|--------|",
    ]
    for r in results:
        ok = "✅" if r.success else "❌"
        lines.append(
            f"| `{r.task_id}` | {r.category} | {ok} | {r.latency_s}s | "
            f"{r.tokens_generated} | {r.tokens_per_sec} | "
            f"{r.tool_calls_total} | {r.tool_calls_broken} | "
            f"{r.iterations} | {r.detail[:60]} |"
        )

    # Stats agrégées par catégorie
    lines += ["", "## Par catégorie", ""]
    cats = sorted({r.category for r in results})
    for cat in cats:
        rs = [r for r in results if r.category == cat]
        ok = sum(1 for r in rs if r.success)
        avg_lat = sum(r.latency_s for r in rs) / max(1, len(rs))
        avg_tps = sum(r.tokens_per_sec for r in rs) / max(1, len(rs))
        lines.append(
            f"- **{cat}** : {ok}/{len(rs)} OK, "
            f"latence moy {avg_lat:.1f}s, {avg_tps:.0f} tok/s moy"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bench Klody")
    p.add_argument("--category", choices=["easy", "medium", "hard"], default=None)
    p.add_argument("--task", default=None, help="ID exact, ex: easy/rename_var")
    p.add_argument("--dry-run", action="store_true", help="liste sans exécuter")
    p.add_argument("--label", default=None, help="label du run pour les fichiers de sortie")
    args = p.parse_args(argv)

    all_tasks = discover_tasks()
    selected = list(filter_tasks(all_tasks, args.category, args.task))
    if not selected:
        print("Aucune tâche sélectionnée.")
        return 1

    print(f"→ {len(selected)} tâche(s) sélectionnée(s)")
    for cls in selected:
        print(f"  · {cls.id}  [{cls.category}]")

    if args.dry_run:
        return 0

    if not metrics.install_patches():
        print("⚠️  Impossible d'installer le monkey-patch métriques. "
              "Le bench tournera mais sans tokens/tool_calls.")

    RESULTS_DIR.mkdir(exist_ok=True)
    results: list[Result] = []
    for cls in selected:
        print(f"\n=== {cls.id} ===")
        r = _run_one(cls)
        results.append(r)
        status = "✅" if r.success else "❌"
        print(f"  {status} {r.latency_s}s — {r.detail}")
        if r.error:
            print(f"  ERROR: {r.error.splitlines()[0]}")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    label = args.label or "run"
    json_path = RESULTS_DIR / f"{timestamp}_{label}.json"
    md_path = RESULTS_DIR / f"{timestamp}_{label}.md"
    _write_json(results, json_path)
    _write_markdown(results, md_path, label=f"{timestamp} — {label}")
    (RESULTS_DIR / "latest.json").write_text(
        json_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    print(f"\n📊  JSON → {json_path}")
    print(f"📊  MD   → {md_path}")
    ok = sum(1 for r in results if r.success)
    print(f"\nRésultat : {ok}/{len(results)} succès")
    return 0 if ok == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
