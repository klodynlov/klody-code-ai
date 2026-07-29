"""Isolation par processus du bench (bench/run.py).

Le bug d'origine, mesuré le 2026-07-29 : toutes les tâches tournaient dans UN
processus. `FileManager.allowed_roots` étant figé dans `__init__` et `_run_klody`
ne repatchant que `.root`, les tâches 2..N gardaient les racines d'un workdir
déjà supprimé. L'agent répondait « hors des racines autorisées » et dictait le
code au lieu de l'écrire — pendant que `write_file` affichait « ✏️ Modifié ».

Symptôme mesurable : la MÊME tâche rendait ✅ à la 1ʳᵉ passe et ❌ à la 2ᵉ.
La catégorie `easy` sortait à 1/5 en lot pour 5/5 tâche par tâche, si bien que
le banc mesurait sa propre fuite d'état et non le modèle.

Ces tests verrouillent le remède : une tâche = un processus neuf. Ils ne
touchent ni la gateway ni le modèle — tout est vérifié à la couture
`subprocess.run`, sinon la suite prendrait des minutes et exigerait un LLM.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import sys

import pytest
from bench import run as bench_run
from bench.framework import Result


class TacheFactice:
    """Suffit à `_run_one`, qui ne lit que `.id` et `.category` de la classe."""

    id = "easy/factice"
    category = "easy"


def _result(**overrides) -> Result:
    champs = {
        "task_id": "easy/factice",
        "category": "easy",
        "success": True,
        "detail": "ok",
        "latency_s": 1.5,
        "tokens_generated": 10,
        "tokens_per_sec": 6.7,
        "tool_calls_total": 2,
        "tool_calls_broken": 0,
        "iterations": 3,
        "error": None,
    }
    return Result(**{**champs, **overrides})


def _arg(cmd: list[str], nom: str) -> str:
    """Valeur de `--nom` dans la ligne de commande du fils."""
    return cmd[cmd.index(nom) + 1]


@pytest.fixture(autouse=True)
def _pas_d_echappatoire(monkeypatch):
    """BENCH_ISOLATION peut traîner dans l'env du développeur — les tests qui
    veulent l'échappatoire la posent eux-mêmes."""
    monkeypatch.delenv("BENCH_ISOLATION", raising=False)


# --- le sous-processus est le chemin par défaut ------------------------------


def test_run_one_delegue_a_un_sous_processus(monkeypatch):
    """LE test de non-régression : exécuter la tâche dans le processus courant
    est exactement ce qui rendait le banc faux."""
    appels = []

    def faux_run(cmd, **kwargs):
        appels.append((cmd, kwargs))
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            json.dump(_result(detail="venu du fils").__dict__, f)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    monkeypatch.setattr(
        bench_run,
        "_run_one_inprocess",
        lambda _: pytest.fail("la tâche a tourné dans le processus du parent"),
    )

    r = bench_run._run_one(TacheFactice)

    assert len(appels) == 1
    assert r.success is True
    assert r.detail == "venu du fils"


def test_le_fils_recoit_l_id_de_la_tache_et_un_chemin_de_sortie(monkeypatch):
    cmd_vue = []

    def faux_run(cmd, **kwargs):
        cmd_vue.append(cmd)
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            json.dump(_result().__dict__, f)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    bench_run._run_one(TacheFactice)

    cmd = cmd_vue[0]
    assert _arg(cmd, "--child-task") == "easy/factice"
    assert _arg(cmd, "--child-out").endswith(".json")
    # `sys.executable` et pas « python » : sous un venv, un interpréteur nu ne
    # verrait ni les dépendances du projet ni le modèle servi.
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "bench.run"]


def test_le_fils_tourne_depuis_la_racine_du_depot(monkeypatch):
    """Sans `cwd`, `python -m bench.run` dépend du dossier courant de l'appelant."""
    vu = {}

    def faux_run(cmd, **kwargs):
        vu.update(kwargs)
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            json.dump(_result().__dict__, f)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    bench_run._run_one(TacheFactice)

    assert vu["cwd"] == bench_run.REPO_ROOT


def test_bench_isolation_0_revient_au_processus_partage(monkeypatch):
    """Échappatoire de débogage — elle doit rester explicite et opt-in."""
    monkeypatch.setenv("BENCH_ISOLATION", "0")
    monkeypatch.setattr(
        bench_run.subprocess,
        "run",
        lambda *a, **k: pytest.fail("BENCH_ISOLATION=0 doit éviter le sous-processus"),
    )
    monkeypatch.setattr(bench_run, "_run_one_inprocess", lambda _: _result(detail="en direct"))

    assert bench_run._run_one(TacheFactice).detail == "en direct"


# --- un fils qui meurt ne doit pas passer pour un succès ---------------------


def test_fils_mort_sans_resultat_rend_un_echec_qui_nomme_le_code(monkeypatch):
    """Une dégradation silencieuse serait le pire des deux mondes : ni le
    résultat, ni l'erreur qui le signale."""
    monkeypatch.setattr(
        bench_run.subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 3),  # n'écrit rien
    )

    r = bench_run._run_one(TacheFactice)

    assert r.success is False
    assert r.task_id == "easy/factice"
    assert "3" in r.detail
    assert "easy/factice" in r.error


def test_fils_expire_rend_un_echec_qui_nomme_le_delai(monkeypatch):
    def faux_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)
    monkeypatch.setattr(bench_run, "_TASK_TIMEOUT_S", 42)

    r = bench_run._run_one(TacheFactice)

    assert r.success is False
    assert "42" in r.detail


def test_resultat_illisible_rend_un_echec_et_pas_une_exception(monkeypatch):
    def faux_run(cmd, **kwargs):
        with open(_arg(cmd, "--child-out"), "w", encoding="utf-8") as f:
            f.write("{ pas du json")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_run.subprocess, "run", faux_run)

    r = bench_run._run_one(TacheFactice)

    assert r.success is False
    assert r.error


# --- le canal parent ↔ fils ne perd aucun champ ------------------------------


def test_le_mode_fils_ecrit_tous_les_champs_de_result(monkeypatch, tmp_path):
    """Le parent reconstruit par `Result(**payload)`. Un champ oublié à
    l'écriture ferait lever le parent sur une tâche par ailleurs réussie."""
    monkeypatch.setattr(bench_run.metrics, "install_patches", lambda: True)
    attendu = _result(task_id="easy/rename_var", detail="rename complet", iterations=7)
    monkeypatch.setattr(bench_run, "_run_one_inprocess", lambda _: attendu)
    sortie = tmp_path / "result.json"

    code = bench_run.main(
        ["--child-task", "easy/rename_var", "--child-out", str(sortie)]
    )

    assert code == 0
    charge = json.loads(sortie.read_text(encoding="utf-8"))
    assert set(charge) == {f.name for f in dataclasses.fields(Result)}
    assert Result(**charge) == attendu


def test_le_mode_fils_n_ecrit_rien_dans_results(monkeypatch, tmp_path):
    """Un fils qui écrirait latest.json — ou pire, baseline.json — écraserait
    le run du parent vingt fois de suite."""
    monkeypatch.setattr(bench_run.metrics, "install_patches", lambda: True)
    monkeypatch.setattr(bench_run, "_run_one_inprocess", lambda _: _result())
    resultats = tmp_path / "results"
    resultats.mkdir()
    monkeypatch.setattr(bench_run, "RESULTS_DIR", resultats)

    bench_run.main(
        ["--child-task", "easy/rename_var", "--child-out", str(tmp_path / "r.json")]
    )

    assert list(resultats.iterdir()) == []


def test_le_mode_fils_refuse_une_tache_inconnue(monkeypatch, tmp_path):
    monkeypatch.setattr(bench_run.metrics, "install_patches", lambda: True)
    monkeypatch.setattr(
        bench_run,
        "_run_one_inprocess",
        lambda _: pytest.fail("aucune tâche ne devait être exécutée"),
    )

    code = bench_run.main(
        ["--child-task", "easy/inexistante", "--child-out", str(tmp_path / "r.json")]
    )

    assert code == 1


def test_le_mode_fils_exige_un_chemin_de_sortie():
    assert bench_run.main(["--child-task", "easy/rename_var"]) == 1
