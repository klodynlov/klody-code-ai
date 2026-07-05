"""Tests des cœurs de composition avancée (klody_mcp.music_theory).

Déterministes, sans MCP ni REAPER. music21 requis (skip propre sinon)."""
from __future__ import annotations

import pytest
from klody_mcp import music_theory as mt

music21 = pytest.importorskip("music21", reason="music21 requis pour la théorie")


# --------------------------------------------------------------------------- #
# analyser_progression                                                        #
# --------------------------------------------------------------------------- #


def test_analyser_progression_degres_et_fonctions():
    r = mt.analyser_progression(["C", "G", "Am", "F"], ton="C")
    assert r["ton"] == "C majeur"
    assert r["ton_infere"] is False
    degres = [a["degre"] for a in r["accords"]]
    assert degres == ["I", "V", "vi", "IV"]
    fonctions = [a["fonction"] for a in r["accords"]]
    assert fonctions == ["Tonique", "Dominante", "Tonique", "Sous-dominante"]
    assert all(a["diatonique"] for a in r["accords"])


def test_analyser_progression_infere_tonalite():
    r = mt.analyser_progression(["C", "G", "Am", "F"])  # pas de ton
    assert r["ton_infere"] is True
    assert r["ton"] == "C majeur"
    assert r["confiance_tonalite"] == 1.0


def test_analyser_progression_cadence_parfaite():
    r = mt.analyser_progression(["C", "F", "G", "C"], ton="C")
    assert r["cadence_finale"].startswith("parfaite")


def test_analyser_progression_demi_cadence():
    r = mt.analyser_progression(["C", "Am", "F", "G"], ton="C")
    assert "demi-cadence" in r["cadence_finale"]


def test_analyser_progression_accord_chromatique_non_diatonique():
    r = mt.analyser_progression(["C", "E-", "F", "C"], ton="C")  # E♭ chromatique
    eb = r["accords"][1]
    assert eb["diatonique"] is False


def test_analyser_progression_liste_vide():
    assert "error" in mt.analyser_progression([])


def test_analyser_progression_accord_illisible():
    assert "error" in mt.analyser_progression(["C", "XYZ"], ton="C")


# --------------------------------------------------------------------------- #
# reharmoniser                                                                #
# --------------------------------------------------------------------------- #


def test_reharmoniser_relatif_diatonique():
    r = mt.reharmoniser(["C", "Am", "F", "G"], ton="C")
    props = {p["accord"]: p for p in r["propositions"]}
    types_c = {s["type"] for s in props["C"]["substituts"]}
    assert "relatif diatonique" in types_c
    # I -> vi (Am) proposé
    accords_c = {s["accord"] for s in props["C"]["substituts"]}
    assert "Am" in accords_c


def test_reharmoniser_substitut_tritonique_sur_dominante():
    r = mt.reharmoniser(["G7", "C"], ton="C")
    subs = r["propositions"][0]["substituts"]
    tri = [s for s in subs if s["type"] == "substitut tritonique"]
    assert tri, "un substitut tritonique attendu sur G7"
    # G7 -> substitut tritonique = D♭7
    assert tri[0]["accord"] == "D♭7"


def test_reharmoniser_dominante_secondaire_approche():
    r = mt.reharmoniser(["C", "Am"], ton="C")
    subs = r["propositions"][0]["substituts"]
    sec = [s for s in subs if s["type"] == "dominante secondaire (approche)"]
    assert sec, "une dominante secondaire attendue avant Am"
    assert sec[0]["accord"] == "E7"  # V7/vi en Do


def test_reharmoniser_liste_vide():
    assert "error" in mt.reharmoniser([])


# --------------------------------------------------------------------------- #
# moduler                                                                     #
# --------------------------------------------------------------------------- #


def test_moduler_pivots_tonalites_proches():
    r = mt.moduler("C", "G")
    assert r["distance_quintes"] == 1
    pivots = {p["accord"] for p in r["pivots"]}
    # G est V de C et I de G ; C est I de C et IV de G.
    assert "G" in pivots and "C" in pivots
    for p in r["pivots"]:
        if p["accord"] == "G":
            assert p["degre_depart"] == "V" and p["degre_arrivee"] == "I"


def test_moduler_sans_pivot_tonalites_eloignees():
    r = mt.moduler("C", "F#")
    assert r["distance_quintes"] == 6
    assert r["pivots"] == []
    assert "approche_dominante" in r
    assert r["approche_dominante"]["dominante_arrivee"] == "C♯7"


def test_moduler_tonalite_illisible():
    assert "error" in mt.moduler("C", "ZZZ")


# --------------------------------------------------------------------------- #
# generer_basse                                                               #
# --------------------------------------------------------------------------- #


def test_generer_basse_fondamentale():
    r = mt.generer_basse(["C", "G"], motif="fondamentale", duree_accord=2.0)
    assert r["count"] == 2
    assert r["events"][0]["length"] == 2.0
    for e in r["events"]:
        assert mt._BASSE_LO <= e["pitch"] <= mt._BASSE_HI
    assert r["events"][0]["start"] == 0.0
    assert r["events"][1]["start"] == 2.0


def test_generer_basse_walking_quatre_notes_par_accord():
    r = mt.generer_basse(["C", "G", "Am", "F"], motif="walking", duree_accord=2.0)
    assert r["count"] == 16  # 4 accords * 4 temps
    # temps cumulés cohérents (0.5s par note)
    assert r["events"][1]["start"] == pytest.approx(0.5)
    assert r["duree_totale_sec"] == pytest.approx(8.0)


def test_generer_basse_quinte_et_octave():
    q = mt.generer_basse(["C"], motif="quinte", duree_accord=2.0)
    assert q["count"] == 2
    o = mt.generer_basse(["C"], motif="octave", duree_accord=2.0)
    # octave : 2e note = fondamentale + 12 (repliée dans le registre)
    assert o["count"] == 2


def test_generer_basse_registre_replie():
    # Un accord aigu doit être ramené dans le registre grave.
    r = mt.generer_basse(["C"], motif="fondamentale")
    assert r["events"][0]["pitch"] <= mt._BASSE_HI


def test_generer_basse_motif_inconnu():
    assert "error" in mt.generer_basse(["C"], motif="reggaeton")


def test_generer_basse_liste_vide():
    assert "error" in mt.generer_basse([])


def test_generer_basse_duree_invalide():
    assert "error" in mt.generer_basse(["C"], duree_accord=0)


# --------------------------------------------------------------------------- #
# harmonies_vocales                                                           #
# --------------------------------------------------------------------------- #


def test_harmonies_vocales_tierce_diatonique_haut():
    r = mt.harmonies_vocales(["C4", "D4", "E4"], ton="C", intervalle="tierce", direction="haut")
    assert r["count"] == 3
    # C->E, D->F, E->G (tierces diatoniques en Do majeur)
    notes = [e["note"] for e in r["events"]]
    assert notes == ["E4", "F4", "G4"]
    assert all(e["melodie"] for e in r["events"])


def test_harmonies_vocales_direction_bas():
    r = mt.harmonies_vocales(["E4", "F4", "G4"], ton="C", intervalle="tierce", direction="bas")
    notes = [e["note"] for e in r["events"]]
    assert notes == ["C4", "D4", "E4"]


def test_harmonies_vocales_reste_dans_tonalite():
    r = mt.harmonies_vocales(["C4", "D4", "E4", "F4", "G4"], ton="C", intervalle="tierce")
    pcs_gamme = {0, 2, 4, 5, 7, 9, 11}  # Do majeur
    for e in r["events"]:
        assert e["pitch"] % 12 in pcs_gamme


def test_harmonies_vocales_double_octave():
    r = mt.harmonies_vocales(["C4"], ton="C", double_octave=True)
    assert "double_octave" in r
    assert r["double_octave"][0]["pitch"] == 48  # C4 (60) - 12


def test_harmonies_vocales_falsetto_signale():
    r = mt.harmonies_vocales(["C5", "D5"], ton="C", passaggio=64)
    assert len(r["falsetto"]) == 2
    assert "note_falsetto" in r


def test_harmonies_vocales_pas_de_falsetto_en_grave():
    r = mt.harmonies_vocales(["C3", "D3"], ton="C", passaggio=64)
    assert r["falsetto"] == []
    assert "note_falsetto" not in r


def test_harmonies_vocales_durees_explicites():
    r = mt.harmonies_vocales(["C4", "D4"], ton="C", durees=[1.0, 0.5])
    assert r["events"][0]["length"] == 1.0
    assert r["events"][1]["length"] == 0.5
    assert r["events"][1]["start"] == 1.0


def test_harmonies_vocales_durees_mauvaise_longueur():
    assert "error" in mt.harmonies_vocales(["C4", "D4"], ton="C", durees=[1.0])


def test_harmonies_vocales_intervalle_inconnu():
    assert "error" in mt.harmonies_vocales(["C4"], ton="C", intervalle="neuvieme")


def test_harmonies_vocales_note_illisible():
    assert "error" in mt.harmonies_vocales(["NOPE"], ton="C")


def test_harmonies_vocales_ton_illisible():
    assert "error" in mt.harmonies_vocales(["C4"], ton="ZZZ")


def test_harmonies_vocales_liste_vide():
    assert "error" in mt.harmonies_vocales([], ton="C")


# --------------------------------------------------------------------------- #
# Robustesse générale                                                         #
# --------------------------------------------------------------------------- #


def test_sorties_json_serialisables():
    import json
    for r in (
        mt.analyser_progression(["C", "G"], ton="C"),
        mt.reharmoniser(["C", "G7"], ton="C"),
        mt.moduler("C", "A"),
        mt.generer_basse(["C", "G"], motif="walking"),
        mt.harmonies_vocales(["C4", "E4"], ton="C", double_octave=True),
    ):
        json.dumps(r, ensure_ascii=False)  # ne lève pas
