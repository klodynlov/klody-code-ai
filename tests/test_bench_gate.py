"""Tests du gate de non-régression du bench (bench/gate.py).

Le bug d'origine : le gate vivait en heredoc dans le workflow et lisait
`base.get("counts_by_category")` alors que `bench.run` sérialise une liste plate.
Personne ne pouvait l'attraper — ni ruff, ni pytest. Ces tests verrouillent le
contrat de format entre `bench.run` et `bench.gate`.
"""
from __future__ import annotations

import json

import pytest
from bench.gate import DEFAULT_MAX_DROP, compare, load_results, main, success_rate


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

    Rien dans `DEFAULT_MAX_DROP` ne le laisse voir, et c'est ce qui a failli
    passer inaperçu en promouvant la baseline de 20 à 30 tâches le 2026-07-30 : à
    0.10, agrandir le banc AFFAIBLISSAIT le gate — 4 tâches cassées nécessaires
    contre 3 avant — parce qu'une baseline à 29/30 n'est plus à 100 % et que
    l'arithmétique en pourcentage donne du mou. Même piège à la promotion à 35
    tâches (2026-09-28) : sous 0.09, 3 pertes sur 35 passaient.

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
            # baseline 20/20 : 2 tâches perdues suffisent.
            (20, 20, 1, False),
            (20, 20, 2, True),
            # baseline 29/30 : il en faut 3 — soit la sensibilité qu'avait le
            # gate à 20 tâches sous l'ancien 0.10.
            (30, 29, 1, False),
            (30, 29, 2, False),
            (30, 29, 3, True),
            # baseline 35/35 (depuis le 2026-09-28) : 3 aussi, pas 4.
            (35, 35, 2, False),
            (35, 35, 3, True),
        ],
        ids=["20-1", "20-2", "30-1", "30-2", "30-3", "35-2", "35-3"],
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

    def test_l_ancien_seuil_etait_permissif_a_35_taches(self):
        """Le même piège, un palier plus loin : sous 0.09, trois tâches perdues
        sur une baseline 35/35 (−8,57 %) PASSAIENT — il en fallait 4. D'où 0.075
        à la promotion du palier `real_repo`."""
        base = self._run(35, 35)
        courant = self._run(35, 32)  # 3 perdues
        assert compare(base, courant, max_drop=0.09)[0] is True
        assert compare(base, courant)[0] is False

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


class TestToutesLesPasses:
    """`bench.run --repeat N` écrit N entrées par tâche dans le même fichier.

    Jusqu'au 2026-09-27, `compare()` indexait `{task_id: résultat}` : seule la
    DERNIÈRE passe de chaque tâche était jugée, et les N−1 autres étaient
    comptées « hors baseline, non jugée(s) ». Un échec en passe 1 était donc
    invisible, et le message affirmait un fait faux.
    """

    DISCOVERY = (
        "discovery/hidden_invariant",
        "discovery/config_precedence",
        "discovery/error_contract",
        "discovery/data_contract",
        "discovery/first_write_method",
    )

    def _baseline_30(self) -> list[dict]:
        """La baseline réelle : 30 tâches, une passe, 30/30."""
        autres = [_result(f"autre/{i:02d}", True, category="hard") for i in range(25)]
        return autres + [_result(t, True, category="discovery") for t in self.DISCOVERY]

    def test_un_echec_en_passe_1_n_est_plus_masque(self):
        """Le cas vécu, à l'identique : `--category discovery --repeat 3` = 14/15,
        `config_precedence` ❌ en passe 1, ✅ en passes 2 et 3.

        L'ancienne porte rendait « courant=100.0% Δ=+0.0% (5 tâche(s)
        commune(s) — 10 hors baseline, non jugée(s)) ✓ »."""
        courant = [
            _result(t, not (passe == 1 and t == "discovery/config_precedence"), "discovery")
            for passe in (1, 2, 3)
            for t in self.DISCOVERY
        ]
        assert sum(r["success"] for r in courant) == 14

        ok, msg = compare(self._baseline_30(), courant)

        # (4 × 1 + 2/3) / 5 = 93,3 %, et non les 100 % de la dernière passe.
        assert "courant=93.3%" in msg, msg
        assert "Δ=-6.7%" in msg, msg
        # Des passes ne sont pas des tâches.
        assert "hors baseline" not in msg, msg
        assert "5 tâche(s) commune(s), courant 3 passes, baseline 1 passe" in msg, msg
        # La tâche en baisse est NOMMÉE, même sous le seuil.
        assert "discovery/config_precedence (1/1 → 2/3)" in msg, msg
        # Sous le seuil (0.075), un tiers de tâche cassée reste vert — c'est le
        # tableau de sensibilité à N passes (bench/gate.py), pas un oubli.
        assert ok, msg

    def test_deux_passes_ratees_sur_quinze_rougissent(self):
        # La ligne « intersection 5/5, N=3 » du tableau : il en faut 2.
        rates = {(1, "discovery/config_precedence"), (2, "discovery/data_contract")}
        courant = [
            _result(t, (passe, t) not in rates, "discovery")
            for passe in (1, 2, 3)
            for t in self.DISCOVERY
        ]

        ok, msg = compare(self._baseline_30(), courant)

        assert not ok, msg
        assert "discovery/config_precedence (1/1 → 2/3)" in msg
        assert "discovery/data_contract (1/1 → 2/3)" in msg

    def test_la_baseline_repetee_est_jugee_sur_toutes_ses_passes(self):
        """Même écrasement côté baseline : une baseline promue depuis un run
        répété n'était lue que sur sa dernière passe.

        Ici deux tâches ratent UNIQUEMENT la dernière passe de la baseline (vrai
        taux 86,7 %) : l'ancienne porte y lisait 60 %, et un run courant à 60 %
        passait donc avec Δ=+0.0 %. La direction dangereuse — une porte qui
        juge contre une référence artificiellement basse est trop LÂCHE."""
        taches = [f"easy/{i}" for i in range(5)]
        base = [
            _result(t, not (passe == 3 and t in taches[:2]))
            for passe in (1, 2, 3)
            for t in taches
        ]
        courant = [_result(t, t not in taches[:2]) for t in taches]

        ok, msg = compare(base, courant)

        assert "baseline=86.7%" in msg, msg
        assert "courant=60.0%" in msg, msg
        assert "courant 1 passe, baseline 3 passes" in msg, msg
        assert not ok, msg

    def test_hors_baseline_compte_des_taches_pas_des_passes(self):
        base = [_result("easy/a", True)]
        courant = [
            _result(t, True) for _ in range(3) for t in ("easy/a", "easy/nouveau")
        ]

        ok, msg = compare(base, courant)

        assert ok
        assert "1 tâche(s) hors baseline" in msg, msg

    def test_chaque_tache_pese_un_quel_que_soit_son_nombre_de_passes(self):
        """Moyenne des taux PAR TÂCHE, pas taux poolé : sinon une tâche plus
        répétée que les autres pèserait davantage. Ici poolé = 25 %, par tâche =
        50 %."""
        base = [_result("easy/a", True), _result("easy/b", True)]
        courant = [_result("easy/a", True)] + [_result("easy/b", False)] * 3

        _, msg = compare(base, courant, max_drop=1.0)

        assert "courant=50.0%" in msg, msg
        assert "courant 1 à 3 passes" in msg, msg

    def test_un_run_repete_stable_reste_vert_et_muet(self):
        base = [_result(f"easy/{i}", True) for i in range(5)]
        courant = base * 3

        ok, msg = compare(base, courant)

        assert ok
        assert "::notice::" not in msg
        assert "courant=100.0%" in msg


class TestNightlyInchange:
    """Le nightly (`.github/workflows/bench-nightly.yml`) ne passe pas `--repeat`,
    et sa baseline est à une passe. Juger toutes les passes ne doit rien y
    changer : ni le verdict, ni les taux affichés, ni la forme du message."""

    @pytest.mark.parametrize("n", [5, 10, 20, 25, 30, 35])
    def test_a_une_passe_le_verdict_est_celui_de_l_ancien_calcul(self, n):
        # Tailles réelles d'intersection : une catégorie (5), les baselines
        # historiques (20, 30), la baseline courante (35), et les unions de
        # paliers.
        for base_ok in range(n + 1):
            base = [_result(f"t/{i:02d}", i < base_ok) for i in range(n)]
            for cour_ok in range(n + 1):
                courant = [_result(f"t/{i:02d}", i < cour_ok) for i in range(n)]
                ok, msg = compare(base, courant)
                # L'ancien `succès / tâches` en flottants, sur la dernière (et
                # unique) passe.
                ancien_delta = cour_ok / n - base_ok / n
                assert ok is not (ancien_delta < -DEFAULT_MAX_DROP), (n, base_ok, cour_ok)
                assert f"courant={cour_ok / n:.1%}" in msg
                assert f"Δ={ancien_delta:+.1%}" in msg
                assert "passe" not in msg.splitlines()[0]

    def test_un_ecart_egal_au_seuil_passe_meme_la_ou_le_flottant_disait_non(self):
        """`delta < -max_drop` est strict : un écart qui VAUT le seuil passe.
        L'ancien calcul (`19/20 − 20/20` en flottants = −0,05000000000000004)
        rougissait pourtant sous `--max-drop 0.05` — 853 égalités exactes de ce
        genre sur n ≤ 100 et dix seuils ; sous le seuil par défaut (0.075), une
        passe n'en rencontre qu'à 40 et 80 tâches."""
        base = [_result(f"t/{i}", True) for i in range(20)]
        une_perdue = [_result(f"t/{i}", i > 0) for i in range(20)]
        deux_perdues = [_result(f"t/{i}", i > 1) for i in range(20)]

        assert compare(base, une_perdue, max_drop=0.05)[0] is True
        assert compare(base, deux_perdues, max_drop=0.05)[0] is False
