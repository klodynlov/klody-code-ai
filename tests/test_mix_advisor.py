"""Tests des cœurs de conseil mixage (klody_mcp.mix_advisor).

Purement déterministes : travaillent sur des dicts de métriques (pas de fichier audio,
pas de numpy)."""
from __future__ import annotations

import json

from klody_mcp import mix_advisor as ma


def _an(sub, low, mid, presence, air) -> dict:
    return {"band_energy": {"sub": sub, "low": low, "mid": mid,
                            "presence": presence, "air": air}}


# --------------------------------------------------------------------------- #
# recommander_eq                                                              #
# --------------------------------------------------------------------------- #


def test_recommander_eq_bandes_ok_quand_proche_reference():
    ref = ma._REFERENCES["neutre"]
    analyse = _an(**ref)
    r = ma.recommander_eq(analyse, "neutre")
    assert all(m["action"] == "OK" for m in r["mouvements"])
    assert r["prioritaires"] == []


def test_recommander_eq_creuser_bande_trop_forte():
    analyse = _an(0.05, 0.20, 0.55, 0.12, 0.08)  # mid saturé
    r = ma.recommander_eq(analyse, "neutre")
    mids = next(m for m in r["mouvements"] if m["bande"] == "mid")
    assert mids["action"] == "creuser"
    assert mids["db_indicatif"] > 0
    assert "mid" in r["prioritaires"]


def test_recommander_eq_renforcer_bande_trop_faible():
    analyse = _an(0.02, 0.30, 0.33, 0.25, 0.10)  # sub trop bas
    r = ma.recommander_eq(analyse, "neutre")
    sub = next(m for m in r["mouvements"] if m["bande"] == "sub")
    assert sub["action"] == "renforcer"
    assert sub["db_indicatif"] < 0


def test_recommander_eq_db_borne():
    analyse = _an(0.9, 0.02, 0.02, 0.02, 0.04)  # sub écrasant
    r = ma.recommander_eq(analyse, "neutre")
    for m in r["mouvements"]:
        assert -4.0 <= m["db_indicatif"] <= 4.0


def test_recommander_eq_style_alias_trap_soul():
    r = ma.recommander_eq(_an(0.1, 0.3, 0.3, 0.2, 0.1), "trap soul")
    assert r["style"] == "trap"


def test_recommander_eq_style_inconnu_retombe_neutre():
    r = ma.recommander_eq(_an(0.1, 0.3, 0.3, 0.2, 0.1), "grindcore")
    assert r["style"] == "neutre"


def test_recommander_eq_sans_band_energy():
    assert "error" in ma.recommander_eq({}, "pop")
    assert "error" in ma.recommander_eq({"band_energy": {}}, "pop")


def test_recommander_eq_band_energy_nul():
    assert "error" in ma.recommander_eq(_an(0, 0, 0, 0, 0), "pop")


# --------------------------------------------------------------------------- #
# detecter_masquage                                                           #
# --------------------------------------------------------------------------- #


def test_detecter_masquage_bande_partagee():
    lead = _an(0.05, 0.20, 0.40, 0.25, 0.10)
    accomp = _an(0.15, 0.35, 0.30, 0.12, 0.08)
    r = ma.detecter_masquage(lead, accomp, seuil=0.12)
    bandes = {x["bande"] for x in r["risques"]}
    assert "mid" in bandes  # 0.40 vs 0.30, tous deux > 0.12
    assert r["bande_la_plus_masquee"] == "mid"  # sévérité = min = 0.30, la plus haute


def test_detecter_masquage_severite_est_min():
    # band_energy est normalisé en fractions : la sévérité = min des DEUX fractions.
    lead = _an(0.10, 0.10, 0.50, 0.20, 0.10)   # mid = 0.50
    accomp = _an(0.10, 0.10, 0.20, 0.30, 0.30)  # mid = 0.20
    r = ma.detecter_masquage(lead, accomp, seuil=0.15)
    mid = next(x for x in r["risques"] if x["bande"] == "mid")
    assert mid["severite"] == 0.2  # min(0.50, 0.20)


def test_detecter_masquage_aucun_risque():
    lead = _an(0.0, 0.0, 1.0, 0.0, 0.0)
    accomp = _an(1.0, 0.0, 0.0, 0.0, 0.0)  # énergies dans des bandes disjointes
    r = ma.detecter_masquage(lead, accomp, seuil=0.2)
    assert r["risques"] == []
    assert r["bande_la_plus_masquee"] is None


def test_detecter_masquage_sans_band_energy():
    assert "error" in ma.detecter_masquage({}, _an(0.2, 0.2, 0.2, 0.2, 0.2))
    assert "error" in ma.detecter_masquage(_an(0.2, 0.2, 0.2, 0.2, 0.2), {})


# --------------------------------------------------------------------------- #
# analyser_balance_tonale                                                     #
# --------------------------------------------------------------------------- #


def test_balance_tonale_parfaite_quand_egale_reference():
    ref = ma._REFERENCES["zouk"]
    r = ma.analyser_balance_tonale(_an(**ref), "zouk")
    assert r["score"] == 1.0
    assert r["verdict"].startswith("équilibrée")
    assert all(v == "ok" for v in r["par_bande"].values())


def test_balance_tonale_desequilibree():
    analyse = _an(0.6, 0.1, 0.1, 0.1, 0.1)  # tout dans le sub
    r = ma.analyser_balance_tonale(analyse, "neutre")
    assert r["score"] < 0.75
    assert r["par_bande"]["sub"] == "trop"
    assert r["par_bande"]["mid"] == "pas assez"


def test_balance_tonale_score_borne_0_1():
    r = ma.analyser_balance_tonale(_an(1.0, 0, 0, 0, 0), "air" and "neutre")
    assert 0.0 <= r["score"] <= 1.0


def test_balance_tonale_sans_band_energy():
    assert "error" in ma.analyser_balance_tonale({}, "pop")


# --------------------------------------------------------------------------- #
# Références                                                                  #
# --------------------------------------------------------------------------- #


def test_references_couvrent_les_styles_du_studio():
    for s in ("zouk", "rnb", "afro", "trap", "reggae", "pop", "neutre"):
        assert s in ma._REFERENCES
        # chaque bande présente
        assert set(ma._REFERENCES[s]) == set(ma._ORDRE)


def test_references_normalisent_a_un():
    for s, courbe in ma._REFERENCES.items():
        norm = ma._normaliser(courbe)
        assert abs(sum(norm.values()) - 1.0) < 1e-9, s


def test_sorties_json_serialisables():
    lead = _an(0.1, 0.3, 0.3, 0.2, 0.1)
    accomp = _an(0.2, 0.3, 0.3, 0.1, 0.1)
    for r in (
        ma.recommander_eq(lead, "zouk"),
        ma.detecter_masquage(lead, accomp),
        ma.analyser_balance_tonale(lead, "trap"),
    ):
        json.dumps(r, ensure_ascii=False)
