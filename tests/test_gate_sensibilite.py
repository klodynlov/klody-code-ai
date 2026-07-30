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
