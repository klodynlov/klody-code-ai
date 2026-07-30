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


class TestSensibiliteSelonTaille:
    """La sensibilité du gate en NOMBRE DE TÂCHES dépend de la taille de la
    baseline, pas seulement du seuil.

    Rien dans `DEFAULT_MAX_DROP = 0.09` ne le laisse voir, et c'est ce qui a
    failli passer inaperçu en promouvant la baseline de 20 à 30 tâches le
    2026-07-30 : à 0.10, agrandir le banc AFFAIBLISSAIT le gate — 4 tâches
    cassées nécessaires contre 3 avant — parce qu'une baseline à 29/30 n'est plus
    à 100 % et que l'arithmétique en pourcentage donne du mou.

    Ces tests reproduisent le tableau cité dans `bench/gate.py`. Ils existent pour
    qu'un futur palier ne puisse pas rendre le gate permissif en silence : c'est
    le mode de défaillance dominant du dépôt.
    """

    @staticmethod
    def _run(total: int, reussies: int) -> list[dict]:
        return [_result(f"t{i}", i < reussies) for i in range(total)]

    @pytest.mark.parametrize(
        ("total", "base_ok", "perdues", "doit_echouer"),
        [
            # baseline 20/20 sous 0.09 : 2 tâches perdues suffisent.
            (20, 20, 1, False),
            (20, 20, 2, True),
            # baseline 29/30 sous 0.09 : il en faut 3 — soit la sensibilité
            # qu'avait le gate à 20 tâches sous l'ancien 0.10.
            (30, 29, 1, False),
            (30, 29, 2, False),
            (30, 29, 3, True),
        ],
        ids=["20-1", "20-2", "30-1", "30-2", "30-3"],
    )
    def test_seuil_par_defaut(self, total, base_ok, perdues, doit_echouer):
        base = self._run(total, base_ok)
        courant = self._run(total, base_ok - perdues)
        ok, msg = compare(base, courant)
        assert ok is not doit_echouer, msg

    def test_l_ancien_seuil_etait_permissif_a_30_taches(self):
        """Le piège lui-même. À 0.10, trois tâches perdues sur une baseline 29/30
        PASSAIENT. Si quelqu'un remonte le seuil, ce test dit ce qu'il rachète."""
        base = self._run(30, 29)
        courant = self._run(30, 26)  # 3 perdues
        assert compare(base, courant, max_drop=0.10)[0] is True
        assert compare(base, courant, max_drop=0.09)[0] is False

    def test_aucun_seuil_ne_donne_trois_taches_aux_deux_tailles(self):
        """Le fait structurel, verrouillé — et l'erreur que j'ai d'abord commise
        en annonçant que 0.09 « rétablit 3 tâches sur les deux tailles ».

        Il faudrait un seuil >= 0.10 pour que 2 pertes sur 20 passent, ET < 0.10
        pour que 3 pertes sur 30 échouent. Contraintes incompatibles : un seuil
        RELATIF ne conserve pas une sensibilité ABSOLUE quand la taille change.
        """
        def premiere_perte_fatale(total, base_ok, seuil):
            base = self._run(total, base_ok)
            return next(
                (p for p in range(1, 8)
                 if not compare(base, self._run(total, base_ok - p), max_drop=seuil)[0]),
                None,
            )

        for seuil in (0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.14):
            a = premiere_perte_fatale(20, 20, seuil)
            b = premiere_perte_fatale(30, 29, seuil)
            assert not (a == b == 3), f"seuil {seuil} donnerait 3 aux deux tailles"
        # Une égalité EXISTE, mais à 2 tâches, pas à 3.
        assert premiere_perte_fatale(20, 20, 0.06) == premiere_perte_fatale(30, 29, 0.06) == 2

    def test_un_ecart_egal_au_seuil_passe(self):
        """La comparaison est stricte (`delta < -max_drop`). C'est ce détail qui
        rend le calcul non intuitif — et qui m'a fait annoncer « 2 tâches » dans
        le corps de #173 là où il en fallait 3 sous l'ancien seuil."""
        base = self._run(20, 20)
        courant = self._run(20, 18)  # Δ = -10,0 % exactement
        assert compare(base, courant, max_drop=0.10)[0] is True
        assert compare(base, courant, max_drop=0.099)[0] is False
