"""Tests de la comparaison A/B de runs (bench/compare.py)."""
from __future__ import annotations

import json

from bench.compare import aggregate, build_report, load_side, main, short_label


def _result(task_id: str, success: bool, category: str = "easy", **over) -> dict:
    base = {
        "task_id": task_id,
        "category": category,
        "success": success,
        "detail": "",
        "latency_s": 10.0,
        "tokens_generated": 100,
        "tokens_per_sec": 50.0,
        "tool_calls_total": 2,
        "tool_calls_broken": 0,
        "iterations": 3,
        "error": None,
    }
    return base | over


def _write(path, results) -> None:
    path.write_text(json.dumps(results), encoding="utf-8")


def _side(tmp_path, label: str, runs: list[list[dict]]):
    paths = []
    for i, run in enumerate(runs):
        p = tmp_path / f"{label}_{i}.json"
        _write(p, run)
        paths.append(p)
    return load_side(label, paths)


# --- agrégation ------------------------------------------------------------


def test_aggregate_compte_les_succes_sur_n_repetitions():
    runs = [
        [_result("easy/a", True)],
        [_result("easy/a", False)],
        [_result("easy/a", True)],
    ]
    stats = aggregate(runs)

    assert stats["easy/a"].runs == 3
    assert stats["easy/a"].successes == 2
    assert stats["easy/a"].success_rate == 2 / 3


def test_aggregate_moyenne_les_metriques_continues():
    runs = [
        [_result("easy/a", True, latency_s=10.0, tokens_per_sec=40.0)],
        [_result("easy/a", True, latency_s=20.0, tokens_per_sec=60.0)],
    ]
    stats = aggregate(runs)

    assert stats["easy/a"].latency_s == 15.0
    assert stats["easy/a"].tokens_per_sec == 50.0


def test_aggregate_success_rate_sans_run_vaut_zero():
    assert aggregate([[]]) == {}


# --- rapport ---------------------------------------------------------------


def test_report_compare_sur_l_intersection(tmp_path):
    """Une tâche absente d'un côté ne doit pas peser sur les agrégats."""
    a = _side(tmp_path, "a", [[_result("easy/x", True), _result("hard/y", False, "hard")]])
    b = _side(tmp_path, "b", [[_result("easy/x", True)]])

    report = build_report(a, b)

    assert "1 tâche(s) commune(s)" in report
    assert "Ignorées (absentes de b) : hard/y" in report


def test_report_signale_regressions_et_gains(tmp_path):
    a = _side(tmp_path, "a", [[_result("easy/x", True), _result("easy/y", False)]])
    b = _side(tmp_path, "b", [[_result("easy/x", False), _result("easy/y", True)]])

    report = build_report(a, b)

    assert "Régressions" in report
    assert "easy/x (100% → 0%)" in report
    assert "Gains" in report
    assert "easy/y (0% → 100%)" in report


def test_report_sans_bascule_le_dit(tmp_path):
    run = [_result("easy/x", True)]
    a = _side(tmp_path, "a", [run])
    b = _side(tmp_path, "b", [run])

    assert "Aucune : mêmes tâches réussies" in build_report(a, b)


def test_report_avertit_quand_un_seul_run(tmp_path):
    """Le garde-fou central : un A/B en one-shot n'est pas concluant."""
    a = _side(tmp_path, "a", [[_result("easy/x", True)]])
    b = _side(tmp_path, "b", [[_result("easy/x", False)]])

    assert "distinguables du bruit" in build_report(a, b)


def test_report_n_avertit_pas_avec_des_repetitions(tmp_path):
    run = [_result("easy/x", True)]
    a = _side(tmp_path, "a", [run, run, run])
    b = _side(tmp_path, "b", [run, run, run])

    assert "distinguables du bruit" not in build_report(a, b)


def test_report_ecarts_en_points_pas_en_relatif(tmp_path):
    """50% → 100% est +50 points, pas +100 % : la confusion rendrait le rapport faux."""
    a = _side(tmp_path, "a", [[_result("easy/x", True), _result("easy/y", False)]])
    b = _side(tmp_path, "b", [[_result("easy/x", True), _result("easy/y", True)]])

    assert "+50 pts" in build_report(a, b)


def test_report_gere_une_reference_nulle(tmp_path):
    """Aucun tool call cassé côté A : l'écart relatif est indéfini, pas une division par zéro."""
    a = _side(tmp_path, "a", [[_result("easy/x", True, tool_calls_broken=0)]])
    b = _side(tmp_path, "b", [[_result("easy/x", True, tool_calls_broken=3)]])

    assert "n/a" in build_report(a, b)


def test_report_sans_tache_commune(tmp_path):
    a = _side(tmp_path, "a", [[_result("easy/x", True)]])
    b = _side(tmp_path, "b", [[_result("hard/z", True, "hard")]])

    assert "Aucune tâche commune" in build_report(a, b)


def test_report_markdown_produit_des_tables(tmp_path):
    a = _side(tmp_path, "a", [[_result("easy/x", True)]])
    b = _side(tmp_path, "b", [[_result("easy/x", True)]])

    report = build_report(a, b, fmt="md")

    assert report.startswith("# Comparaison")
    assert "| Métrique |" in report
    assert "|---|" in report


def test_report_ventile_par_categorie(tmp_path):
    a = _side(tmp_path, "a", [[_result("easy/x", True), _result("hard/y", True, "hard")]])
    b = _side(tmp_path, "b", [[_result("easy/x", True), _result("hard/y", False, "hard")]])

    report = build_report(a, b)

    assert "Par catégorie" in report
    assert "hard (1)" in report


# --- répétitions & provenance ----------------------------------------------


def test_repeats_compte_les_passes_pas_les_fichiers(tmp_path):
    """`bench.run --repeat 3` écrit UN fichier de 3 passes : compter les fichiers
    ferait passer un run répété pour un one-shot."""
    p = tmp_path / "trois_passes.json"
    _write(p, [_result("easy/x", True) for _ in range(3)])
    side = load_side("a", [p])

    assert side.repeats == 3
    assert side.stats["easy/x"].runs == 3


def test_repeats_prend_le_minimum_observe(tmp_path):
    p = tmp_path / "inegal.json"
    _write(p, [_result("easy/x", True), _result("easy/x", True), _result("easy/y", True)])

    assert load_side("a", [p]).repeats == 1


def test_pas_d_avertissement_avec_un_fichier_repete(tmp_path):
    """Le corollaire : un seul fichier `--repeat 3` par côté suffit à conclure."""
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _write(a, [_result("easy/x", True) for _ in range(3)])
    _write(b, [_result("easy/x", True) for _ in range(3)])

    assert "distinguables du bruit" not in build_report(load_side("a", [a]), load_side("b", [b]))


def test_label_par_defaut_vient_du_modele_servi(tmp_path):
    p = tmp_path / "run.json"
    p.write_text(
        json.dumps(
            {
                "meta": {"model_configured": "brain", "model_served": "Qwen3.6-35B"},
                "results": [_result("easy/x", True)],
            }
        ),
        encoding="utf-8",
    )

    assert load_side(None, [p]).label == "Qwen3.6-35B"


def test_label_auto_raccourci_pour_rester_lisible(tmp_path):
    """Un id HF complet sert d'en-tête à quatre colonnes : il doit être compact."""
    p = tmp_path / "run.json"
    p.write_text(
        json.dumps(
            {
                "meta": {"model_served": "unsloth/Qwen3.6-35B-A3B-MLX-8bit"},
                "results": [_result("easy/x", True)],
            }
        ),
        encoding="utf-8",
    )
    label = load_side(None, [p]).label

    assert "unsloth/" not in label
    assert len(label) <= 20
    # L'id intégral reste lisible dans la ligne de provenance.
    assert "unsloth/Qwen3.6-35B-A3B-MLX-8bit" in build_report(
        load_side(None, [p]), load_side("b", [p])
    )


def test_label_retombe_sur_le_nom_de_fichier_sans_metadonnees(tmp_path):
    p = tmp_path / "vieux_run.json"
    _write(p, [_result("easy/x", True)])

    assert load_side(None, [p]).label == "vieux_run"


def test_label_explicite_prime_sur_les_metadonnees(tmp_path):
    p = tmp_path / "run.json"
    p.write_text(
        json.dumps({"meta": {"model_served": "auto"}, "results": [_result("easy/x", True)]}),
        encoding="utf-8",
    )

    assert load_side("choisi", [p]).label == "choisi"


def test_rapport_affiche_la_provenance(tmp_path):
    """Sans provenance, deux colonnes de chiffres ne disent pas quoi a été comparé."""
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(
        json.dumps(
            {
                "meta": {"model_configured": "brain", "model_served": "qwen", "backend": "mlx"},
                "results": [_result("easy/x", True)],
            }
        ),
        encoding="utf-8",
    )
    _write(b, [_result("easy/x", True)])

    report = build_report(load_side(None, [a]), load_side("b", [b]))

    assert "brain → qwen · backend=mlx" in report
    assert "configuration inconnue" in report  # côté B, run sans métadonnées


# --- CLI -------------------------------------------------------------------


def test_main_accepte_deux_positionnels(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _write(a, [_result("easy/x", True)])
    _write(b, [_result("easy/x", False)])

    assert main([str(a), str(b)]) == 0
    assert "Comparaison — a vs b" in capsys.readouterr().out


def test_main_accepte_des_series_avec_labels(tmp_path, capsys):
    paths = []
    for name in ("a1", "a2", "b1", "b2"):
        p = tmp_path / f"{name}.json"
        _write(p, [_result("easy/x", True)])
        paths.append(str(p))

    code = main(["-a", *paths[:2], "-b", *paths[2:], "--label-a", "qwen", "--label-b", "oss"])

    assert code == 0
    assert "qwen : 2 run(s)" in capsys.readouterr().out


def test_main_echoue_si_un_fichier_manque(tmp_path):
    a = tmp_path / "a.json"
    _write(a, [_result("easy/x", True)])

    assert main([str(a), str(tmp_path / "absent.json")]) == 1


def test_main_rend_une_erreur_d_usage_sans_lever(tmp_path, capsys):
    """Le code de retour, pas un SystemExit : testable, et pas de variable non liée."""
    a = tmp_path / "a.json"
    _write(a, [_result("easy/x", True)])

    assert main([str(a)]) == 2
    assert "Usage" in capsys.readouterr().err


def test_main_echoue_sur_format_invalide(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _write(a, {"counts_by_category": {"easy": 1}})  # l'ancien format fantôme
    _write(b, [_result("easy/x", True)])

    assert main([str(a), str(b)]) == 1


def test_short_label_tombe_l_organisation_et_tronque():
    assert short_label("unsloth/Qwen3.6-35B") == "Qwen3.6-35B"
    assert short_label("court") == "court"
    assert short_label("a" * 30).endswith("…")
    assert len(short_label("a" * 30)) == 20
