"""Garde DURE anti-répétition dégénérée du streaming (agent/stream_guard.py).

Couvre les deux détecteurs (lignes / sous-chaîne), le seuil de répétitions, la
longueur mini du motif (anti faux-positif sur code & Markdown), la correction de
la troncature et le throttle de LoopGuard. Sans LLM.
"""
from agent.stream_guard import LoopGuard, degenerate_cut


UNIT = "Je répète exactement la même phrase. "  # 37 chars, > min_unit


class TestPasDeFauxPositif:
    def test_texte_normal(self):
        txt = (
            "Voici une explication détaillée du fonctionnement de l'algorithme. "
            "Chaque étape diffère de la précédente et fait avancer le calcul."
        )
        assert degenerate_cut(txt) is None

    def test_vide(self):
        assert degenerate_cut("") is None

    def test_accolades_repetees_sont_legitimes(self):
        # Pile de `}` en fin de gros bloc de code : motif < min_unit → pas coupé.
        code = "function f() {\n  if (x) {\n    g();\n  }\n}\n}\n}\n}\n"
        assert degenerate_cut(code) is None

    def test_bordure_markdown_legitimes(self):
        md = "| col |\n| --- |\n" + "----\n----\n----\n----\n"
        assert degenerate_cut(md) is None

    def test_ligne_courte_repetee_sous_min_unit(self):
        # 10 chars répétés 5× : sous le seuil min_unit=16 → légitime.
        assert degenerate_cut("abcdefghi\n" * 5) is None

    def test_trois_repetitions_insuffisant(self):
        # reps=4 par défaut : 3 copies ne déclenchent pas.
        assert degenerate_cut("Préambule. " + UNIT * 3) is None


class TestDetecteurSousChaine:
    def test_phrase_repetee_sans_retour_ligne(self):
        txt = "Réponse : " + UNIT * 4
        cut = degenerate_cut(txt)
        assert cut is not None
        # Garde le préfixe + UNE copie, jette les 3 autres.
        assert txt[:cut] == "Réponse : " + UNIT

    def test_garde_une_seule_copie(self):
        txt = UNIT * 6
        cut = degenerate_cut(txt)
        assert txt[:cut] == UNIT

    def test_motif_court_capte_a_un_multiple(self):
        # Motif fondamental "ha " (3 chars) répété beaucoup : capté à un multiple
        # >= min_unit (pas de retour ligne → c'est le détecteur sous-chaîne).
        assert degenerate_cut("ha " * 40) is not None


class TestDetecteurLignes:
    def test_lignes_finales_identiques(self):
        line = "Cette ligne se répète sans fin.\n"  # > min_unit
        txt = "Intro légitime.\n" + line * 4
        cut = degenerate_cut(txt)
        assert cut is not None
        assert txt[:cut] == "Intro légitime.\n" + line

    def test_refrain_avec_lignes_blanches(self):
        line = "Le même refrain revient.\n"
        txt = "Début.\n" + (line + "\n") * 4
        cut = degenerate_cut(txt)
        assert cut is not None
        assert txt[:cut].count("Le même refrain revient.") == 1


class TestLoopGuard:
    def test_throttle_avant_seuil(self):
        g = LoopGuard()
        # En dessous de reps*min_unit, on ne scanne même pas.
        assert g.cut("court") is None

    def test_detecte_apres_accumulation(self):
        g = LoopGuard()
        txt = "Préambule. " + UNIT * 4
        # Premier appel au-dessus du seuil → scanne et coupe.
        cut = g.cut(txt)
        assert cut is not None
        assert txt[:cut] == "Préambule. " + UNIT

    def test_desactive_via_reps_eleve(self):
        # Un motif répété 4× n'est pas coupé si on exige 6 répétitions.
        assert degenerate_cut(UNIT * 4, reps=6) is None
