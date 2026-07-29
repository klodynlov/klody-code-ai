"""Tests du gate de non-régression du bench (bench/gate.py).

Le bug d'origine : le gate vivait en heredoc dans le workflow et lisait
`base.get("counts_by_category")` alors que `bench.run` sérialise une liste plate.
Personne ne pouvait l'attraper — ni ruff, ni pytest. Ces tests verrouillent le
contrat de format entre `bench.run` et `bench.gate`.
"""
from __future__ import annotations

import json

import pytest
from bench.gate import compare, load_results, main, success_rate


def _result(task_id: str, success: bool, category: str = "easy") -> dict:
    """Un Result minimal, au format réellement écrit par bench.run."""
    return {
        "task_id": task_id,
        "category": category,
        "success": success,
        "detail": "",
        "latency_s": 1.0,
        "tokens_generated": 10,
        "tokens_per_sec": 10.0,
        "tool_calls_total": 1,
        "tool_calls_broken": 0,
        "iterations": 1,
        "error": None,
    }


def _write(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


# --- format ----------------------------------------------------------------


def test_load_results_lit_la_liste_plate_de_bench_run(tmp_path):
    """Le format nominal — celui que _write_json produit réellement."""
    p = tmp_path / "run.json"
    _write(p, [_result("easy/a", True), _result("easy/b", False)])

    assert [r["task_id"] for r in load_results(p)] == ["easy/a", "easy/b"]


def test_load_results_tolere_un_objet_enveloppant(tmp_path):
    p = tmp_path / "run.json"
    _write(p, {"results": [_result("easy/a", True)]})

    assert len(load_results(p)) == 1


def test_load_results_rejette_un_format_inconnu(tmp_path):
    """Le mode d'échec historique : un dict d'agrégats sans `results`."""
    p = tmp_path / "run.json"
    _write(p, {"counts_by_category": {"easy": 5}})

    with pytest.raises(ValueError, match="format inattendu"):
        load_results(p)


def test_load_results_rejette_une_entree_sans_task_id(tmp_path):
    p = tmp_path / "run.json"
    _write(p, [{"success": True}])

    with pytest.raises(ValueError, match="sans task_id"):
        load_results(p)


# --- comparaison -----------------------------------------------------------


def test_success_rate_vide_vaut_zero():
    assert success_rate([]) == 0.0


def test_compare_accepte_un_run_stable():
    base = [_result("easy/a", True), _result("easy/b", True)]
    ok, msg = compare(base, list(base))

    assert ok
    assert "Pas de régression" in msg


def test_compare_rejette_une_chute_au_dela_du_seuil():
    base = [_result(f"easy/{i}", True) for i in range(4)]
    latest = [_result("easy/0", False)] + [_result(f"easy/{i}", True) for i in range(1, 4)]

    ok, msg = compare(base, latest)

    assert not ok
    assert "::error::" in msg
    # Le message nomme la tâche passée au rouge — sinon le gate est inexploitable.
    assert "easy/0" in msg


def test_compare_tolere_une_chute_sous_le_seuil():
    base = [_result(f"easy/{i}", True) for i in range(20)]
    latest = [_result("easy/0", False)] + [_result(f"easy/{i}", True) for i in range(1, 20)]

    ok, _ = compare(base, latest)  # -5 pts, seuil -10

    assert ok


def test_compare_ignore_les_taches_absentes_de_la_baseline():
    """Un run filtré `--category easy` ne doit pas être jugé sur un périmètre absent.

    Sans intersection, la baseline complète (2 tâches, 100 %) face à un run easy
    (1 tâche) ferait apparaître un delta qui ne mesure que le périmètre.
    """
    base = [_result("easy/a", True), _result("hard/z", True, category="hard")]
    latest = [_result("easy/a", True)]

    ok, msg = compare(base, latest)

    assert ok
    assert "1 tâche(s) commune(s)" in msg


def test_compare_signale_les_taches_hors_baseline():
    base = [_result("easy/a", True)]
    latest = [_result("easy/a", True), _result("easy/nouveau", False)]

    ok, msg = compare(base, latest)

    assert ok  # la nouvelle tâche n'est pas jugée
    assert "hors baseline" in msg


def test_compare_reste_neutre_sans_tache_commune():
    ok, msg = compare([_result("easy/a", True)], [_result("hard/z", False, category="hard")])

    assert ok
    assert "::warning::" in msg


# --- CLI -------------------------------------------------------------------


def test_main_echoue_si_latest_absent(tmp_path, capsys):
    code = main(["--latest", str(tmp_path / "nope.json"), "--baseline", str(tmp_path / "b.json")])

    assert code == 1
    assert "::error::" in capsys.readouterr().out


def test_main_neutre_mais_annote_sans_baseline(tmp_path, capsys):
    """Le comportement historique sortait en 0 SANS trace : un gate mort était
    indiscernable d'un gate vert. On reste non bloquant, mais annoté."""
    latest = tmp_path / "latest.json"
    _write(latest, [_result("easy/a", True)])

    code = main(["--latest", str(latest), "--baseline", str(tmp_path / "absent.json")])

    assert code == 0
    assert "::warning::" in capsys.readouterr().out


def test_main_echoue_sur_regression(tmp_path):
    base, latest = tmp_path / "base.json", tmp_path / "latest.json"
    _write(base, [_result("easy/a", True), _result("easy/b", True)])
    _write(latest, [_result("easy/a", False), _result("easy/b", False)])

    assert main(["--baseline", str(base), "--latest", str(latest)]) == 1


def test_main_echoue_sur_baseline_illisible(tmp_path, capsys):
    base, latest = tmp_path / "base.json", tmp_path / "latest.json"
    base.write_text("{pas du json", encoding="utf-8")
    _write(latest, [_result("easy/a", True)])

    assert main(["--baseline", str(base), "--latest", str(latest)]) == 1
    assert "::error::" in capsys.readouterr().out
