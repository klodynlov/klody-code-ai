"""Verrouille le TABLEAU DE SENSIBILITÉ écrit dans `bench/gate.py`.

Ce seuil est un pourcentage, mais ce qu'on veut savoir est un compte : combien de
tâches doivent casser avant que le build rougisse. La constante seule ne le dit
pas — la réponse dépend de la taille de la baseline ET du fait qu'elle soit ou
non à 100 %.

⚠️ Le commentaire qui donnait ce tableau était FAUX d'une unité sur toute sa
colonne `29/30`, du 2026-07-30 matin jusqu'au soir : il annonçait 2/3/4 là où le
gate rend 3/4/5. Pire, la justification du passage de 0.10 à 0.09 en découlait —
elle prétendait ramener le gate à 3 tâches cassées quand il en fallait 4. Le
tableau avait été calculé de tête, dans le commit même qui changeait le seuil, et
rien ne le vérifiait.

Un commentaire qui chiffre une sensibilité EST un réglage. Ces tests le traitent
comme tel.
"""
import copy

import pytest
from bench.gate import DEFAULT_MAX_DROP, compare


def _run(n: int, succes: int) -> list[dict]:
    """`n` tâches d'identifiants stables, dont les `succes` premières passent."""
    return [{"task_id": f"t/{i:02d}", "success": i < succes} for i in range(n)]


def _premiere_rouge(n: int, base_succes: int, seuil: float) -> int | None:
    """Nombre de tâches à casser (depuis un run PARFAIT) pour faire rougir."""
    base = _run(n, base_succes)
    for perdues in range(1, n + 1):
        courant = copy.deepcopy(_run(n, n))
        for r in courant[:perdues]:
            r["success"] = False
        ok, _ = compare(base, courant, max_drop=seuil)
        if not ok:
            return perdues
    return None


# Le tableau du commentaire de `bench/gate.py`, à l'unité près.
TABLEAU = [
    #  seuil,  (n, succès baseline),  1re perte qui rougit
    (0.06, (20, 20), 2),
    (0.06, (30, 29), 3),
    (0.06, (30, 30), 2),
    (0.09, (20, 20), 2),
    (0.09, (30, 29), 4),
    (0.09, (30, 30), 3),
    (0.10, (20, 20), 3),
    (0.10, (30, 29), 5),
    (0.10, (30, 30), 4),
]


@pytest.mark.parametrize("seuil,baseline,attendu", TABLEAU)
def test_tableau_de_sensibilite(seuil, baseline, attendu):
    n, succes = baseline
    assert _premiere_rouge(n, succes, seuil) == attendu


class TestSeuilCourant:
    def test_le_defaut_vaut_toujours_0_09(self):
        # Changer le seuil sans toucher au tableau ci-dessus rendrait le
        # commentaire faux une seconde fois. Ce test force à faire les deux.
        assert DEFAULT_MAX_DROP == 0.09

    def test_baseline_a_100_pourcent_rougit_a_trois(self):
        # La ligne réellement en vigueur depuis la promotion du 2026-07-30 soir.
        assert _premiere_rouge(30, 30, DEFAULT_MAX_DROP) == 3

    def test_une_baseline_non_parfaite_est_plus_LACHE(self):
        # Le piège qui a coûté la journée : figer un échec attendu dans la
        # baseline ne rend pas le gate neutre, il le DESSERRE — l'arithmétique en
        # pourcentage donne du mou dès que la référence n'est plus à 100 %.
        assert _premiere_rouge(30, 29, DEFAULT_MAX_DROP) > _premiere_rouge(
            30, 30, DEFAULT_MAX_DROP
        )

    def test_un_ecart_egal_au_seuil_passe(self):
        # `delta < -max_drop` est strict — documenté, donc vérifié.
        base = _run(100, 100)
        courant = _run(100, 91)  # −9,0 points, exactement le seuil
        ok, _ = compare(base, courant, max_drop=0.09)
        assert ok is True
        ok, _ = compare(base, _run(100, 90), max_drop=0.09)  # −10,0 points
        assert ok is False


# --- Avec N passes (`bench.run --repeat N`), depuis le 2026-09-27 -------------
#
# Le tableau ci-dessus suppose UNE passe. Depuis que `compare()` juge toutes les
# passes (moyenne des taux par tâche), sa sensibilité se compte en PASSES en
# échec. Même principe : le tableau du commentaire, verrouillé case par case.


def _run_passes(n: int, passes: int) -> list[dict]:
    """`n` tâches parfaites × `passes` passes, dans l'ordre d'écriture de
    `bench.run` : passe 1 en entier, puis passe 2, etc."""
    return [
        {"task_id": f"t/{i:02d}", "success": True}
        for _ in range(passes)
        for i in range(n)
    ]


def _premiere_rouge_en_passes(
    n: int, base_succes: int, seuil: float, passes: int, *, eparses: bool
) -> int | None:
    """Nombre de PASSES à faire échouer pour rougir.

    `eparses=True` : une passe par tâche avant d'en toucher une seconde (le cas
    vécu, un échec isolé ici ou là). `eparses=False` : des tâches entièrement
    cassées, passe après passe.
    """
    base = _run(n, base_succes)
    if eparses:
        ordre = [p * n + i for p in range(passes) for i in range(n)]
    else:
        ordre = [p * n + i for i in range(n) for p in range(passes)]
    for perdues in range(1, n * passes + 1):
        courant = _run_passes(n, passes)
        for k in ordre[:perdues]:
            courant[k]["success"] = False
        ok, _ = compare(base, courant, max_drop=seuil)
        if not ok:
            return perdues
    return None


# Le tableau « Avec N passes » de `bench/gate.py`, à l'unité près.
TABLEAU_PASSES = [
    #  (n, succès baseline),  {N passes: 1re rouge en passes en échec}
    ((30, 30), {1: 3, 3: 9, 5: 14}),
    ((30, 29), {1: 4, 3: 12, 5: 19}),
    ((20, 20), {1: 2, 3: 6, 5: 10}),
    ((5, 5), {1: 1, 3: 2, 5: 3}),
]


@pytest.mark.parametrize(
    "baseline,passes,attendu",
    [(b, n, a) for b, par_n in TABLEAU_PASSES for n, a in par_n.items()],
)
def test_tableau_de_sensibilite_avec_n_passes(baseline, passes, attendu):
    n, succes = baseline
    eparses = _premiere_rouge_en_passes(n, succes, DEFAULT_MAX_DROP, passes, eparses=True)
    entieres = _premiere_rouge_en_passes(n, succes, DEFAULT_MAX_DROP, passes, eparses=False)
    assert eparses == entieres == attendu


def test_a_une_passe_le_tableau_historique_est_intact():
    # La colonne N=1 du tableau à N passes EST le tableau historique à 0.09 :
    # juger toutes les passes ne déplace rien quand il n'y en a qu'une.
    historique = {b: a for s, b, a in TABLEAU if s == DEFAULT_MAX_DROP}
    for baseline, par_n in TABLEAU_PASSES:
        if baseline in historique:
            assert par_n[1] == historique[baseline]


def test_en_taches_entieres_la_repetition_ne_desserre_pas():
    # 3 tâches cassées sur 30 rougissent à 1 passe comme à 3 : c'est un seuil
    # sur un taux, la répétition ne l'achète pas.
    assert _premiere_rouge_en_passes(30, 30, DEFAULT_MAX_DROP, 3, eparses=False) == 3 * 3


@pytest.mark.parametrize("seuil", [0.06, 0.09, 0.10])
@pytest.mark.parametrize("baseline", [(30, 30), (30, 29), (20, 20)])
def test_le_verdict_ne_depend_pas_de_la_place_des_echecs(seuil, baseline):
    """Mesuré le 2026-09-27 avec des taux en FLOTTANTS : baseline 29/30, seuil
    0.06, 5 passes ⇒ rouge à 14 ou à 15 passes en échec selon leur place, à taux
    rigoureusement identique. Sommer des 0,8 fait dériver le dernier bit, et sur
    la frontière du seuil ce bit décide. D'où le calcul exact de `compare()`."""
    n, succes = baseline
    assert _premiere_rouge_en_passes(
        n, succes, seuil, 5, eparses=True
    ) == _premiere_rouge_en_passes(n, succes, seuil, 5, eparses=False)
