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
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

# Ajouter le repo root au sys.path pour pouvoir importer agent.* config.*
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bench import metrics, provenance  # noqa: E402
from bench.framework import (  # noqa: E402
    Result,
    Task,
    discover_tasks,
    filter_tasks,
    stopwatch,
)

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


# Le workdir du bench est annoncé au modèle en toutes lettres (voir _run_klody).
# Le tmpdir par défaut de macOS — /var/folders/6d/8b_zjbj96t55q3n5_t_r11wh0000gn/T/ —
# fait 62 caractères de bruit : mesuré le 2026-07-29, le modèle le « normalise » en
# /tmp/klody-bench, y fait le travail, et validate() ne voit rien. Un chemin court
# retire la tentation. `dir=None` retombe sur le défaut là où /tmp n'existe pas.
#
# nosec B108 : bandit signale le chemin en dur, à raison dans le cas général.
# Ici le chemin prévisible est le PARENT — TemporaryDirectory passe par mkdtemp,
# qui crée dessous un sous-dossier au nom aléatoire en 0700 et échoue s'il existe
# déjà. C'est exactement la protection que demande CWE-377. Surchargeable par
# BENCH_TMP_ROOT pour qui veut un autre parent.
_TMP_ROOT = os.getenv("BENCH_TMP_ROOT") or (  # nosec B108
    "/tmp" if Path("/tmp").is_dir() else None  # nosec B108
)


def _run_one_inprocess(task_cls: type[Task]) -> Result:
    task = task_cls()
    with tempfile.TemporaryDirectory(prefix="kb-", dir=_TMP_ROOT) as tmp:
        # Résolu tout de suite : sur macOS /tmp est un lien vers /private/tmp, et
        # file_manager.root est resolve() plus bas. Sans ça le chemin annoncé au
        # modèle et celui que path_safety contrôle ne seraient pas le même.
        workdir = Path(tmp).resolve()
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


# Une tâche = un processus neuf. Mesuré le 2026-07-29 : en processus partagé, la
# MÊME tâche rendait ✅ à la 1ʳᵉ passe et ❌ à la 2ᵉ — `FileManager.allowed_roots`
# est figé dans __init__ et _run_klody ne repatchait que `.root`, si bien que les
# tâches 2..N gardaient les racines d'un workdir déjà supprimé. Les cinq tâches
# `easy` passaient seules et une seule passait en lot : le banc mesurait sa propre
# fuite d'état, pas le modèle. Repatcher `allowed_roots` aurait corrigé CE cas ;
# un processus par tâche corrige aussi ceux qu'on n'a pas encore trouvés.
_TASK_TIMEOUT_S = int(os.getenv("BENCH_TASK_TIMEOUT", "600"))


def _run_one(task_cls: type[Task]) -> Result:
    """Exécute une tâche dans un sous-processus dédié.

    `BENCH_ISOLATION=0` retombe sur l'exécution en processus partagé — utile pour
    débogguer sous pdb, à ne pas utiliser pour mesurer.
    """
    if os.getenv("BENCH_ISOLATION") == "0":
        return _run_one_inprocess(task_cls)

    def _failed(detail: str, error: str) -> Result:
        return Result(
            task_id=task_cls.id,
            category=task_cls.category,
            success=False,
            detail=detail,
            latency_s=0.0,
            tokens_generated=0,
            tokens_per_sec=0.0,
            tool_calls_total=0,
            tool_calls_broken=0,
            iterations=0,
            error=error,
        )

    with tempfile.TemporaryDirectory(prefix="kb-out-", dir=_TMP_ROOT) as out_dir:
        out_path = Path(out_dir) / "result.json"
        cmd = [
            sys.executable, "-m", "bench.run",
            "--child-task", task_cls.id,
            "--child-out", str(out_path),
        ]
        try:
            # stdout hérité : on garde l'affichage live du parcours de l'agent.
            # Le résultat transite par le fichier, donc le bruit du transcript
            # (rich, barres de chargement) ne peut pas le corrompre.
            proc = subprocess.run(cmd, cwd=REPO_ROOT, timeout=_TASK_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return _failed(
                f"timeout après {_TASK_TIMEOUT_S}s",
                f"le sous-processus n'a pas rendu la main en {_TASK_TIMEOUT_S}s",
            )

        if not out_path.exists():
            # Le fils est mort avant d'écrire : sans ça on rendrait un succès muet.
            return _failed(
                f"sous-processus sorti en code {proc.returncode} sans résultat",
                f"bench.run --child-task {task_cls.id} : code {proc.returncode}, "
                f"aucun {out_path.name} écrit",
            )
        try:
            return Result(**json.loads(out_path.read_text(encoding="utf-8")))
        except Exception as exc:
            return _failed("résultat du fils illisible", f"{type(exc).__name__}: {exc}")


def _write_json(results: list[Result], path: Path, meta: dict | None = None) -> None:
    """Écrit l'enveloppe {meta, results}.

    Le format a changé de « liste plate » à « enveloppe » pour porter la provenance
    (quel modèle a produit ces chiffres). `bench.gate.load_run` lit toujours les deux,
    donc les baselines déjà promues restent valides.
    """
    path.write_text(
        json.dumps(
            {"meta": meta or {}, "results": [r.__dict__ for r in results]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _scope_label(results: list[Result]) -> str:
    """« 20 tâches » ou « 20 tâches × 3 passes » — sans quoi un run répété
    afficherait 60 tâches, ce qui n'est pas ce qui a été mesuré."""
    distinct = len({r.task_id for r in results})
    if not distinct:
        return "0 tâche"
    passes = len(results) // distinct
    return f"{distinct} tâches" + (f" × {passes} passes" if passes > 1 else "")


def _write_markdown(results: list[Result], path: Path, label: str) -> None:
    lines = [
        f"# Bench Klody — {label}",
        "",
        f"_{_scope_label(results)}_  ",
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
    p.add_argument(
        "--promote-baseline",
        action="store_true",
        help="écrit aussi results/baseline.json (référence du gate de non-régression)",
    )
    p.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="répète la sélection N fois (défaut 1) — nécessaire pour comparer "
             "deux configurations : un run unique ne sépare pas un écart du bruit",
    )
    # Mode fils : exécute UNE tâche et écrit son Result. Jamais appelé à la main.
    p.add_argument("--child-task", default=None, help=argparse.SUPPRESS)
    p.add_argument("--child-out", default=None, help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.child_task:
        if not args.child_out:
            print("--child-task exige --child-out.")
            return 1
        matches = list(filter_tasks(discover_tasks(), task_id=args.child_task))
        if not matches:
            print(f"Tâche inconnue : {args.child_task}")
            return 1
        metrics.install_patches()
        result = _run_one_inprocess(matches[0])
        Path(args.child_out).write_text(
            json.dumps(result.__dict__, ensure_ascii=False), encoding="utf-8"
        )
        return 0

    if args.repeat < 1:
        print("--repeat doit valoir au moins 1.")
        return 1

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

    # Provenance capturée AVANT les tâches : si le modèle est basculé en cours de
    # route, on veut la configuration qui a réellement démarré le run.
    started_at = datetime.now().isoformat(timespec="seconds")
    meta = provenance.describe_config()
    print(f"→ configuration : {provenance.describe_short(meta)}")

    RESULTS_DIR.mkdir(exist_ok=True)
    results: list[Result] = []
    # N passes complètes plutôt que N exécutions consécutives par tâche : répéter une
    # tâche dos à dos la sert depuis un cache de prompt chaud, ce qui fabriquerait une
    # latence artificiellement basse sur les répétitions.
    for pass_no in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"\n──── passe {pass_no}/{args.repeat} ────")
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
    meta |= {
        "label": label,
        "started_at": started_at,
        "repeat": args.repeat,
        "category": args.category,
        "task": args.task,
        "tasks_selected": len(selected),
    }
    _write_json(results, json_path, meta=meta)
    _write_markdown(results, md_path, label=f"{timestamp} — {label}")
    (RESULTS_DIR / "latest.json").write_text(
        json_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    if args.promote_baseline:
        baseline_path = RESULTS_DIR / "baseline.json"
        baseline_path.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"📌  baseline → {baseline_path} (à committer)")

    print(f"\n📊  JSON → {json_path}")
    print(f"📊  MD   → {md_path}")
    ok = sum(1 for r in results if r.success)
    print(f"\nRésultat : {ok}/{len(results)} succès")
    if args.repeat > 1:
        print(f"({len(selected)} tâche(s) × {args.repeat} passe(s))")
    return 0 if ok == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
