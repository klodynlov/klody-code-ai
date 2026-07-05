"""Tests du coaching vocal & AutoTune (klody_mcp.vocal_coach).

Le cœur (evaluer_justesse_f0, recommander_autotune) est pur : testable sans audio."""
from __future__ import annotations

import json
import math

from klody_mcp import vocal_coach as vc


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12.0)


# --------------------------------------------------------------------------- #
# evaluer_justesse_f0                                                         #
# --------------------------------------------------------------------------- #


def test_justesse_parfaite_sur_notes_de_gamme():
    f0 = [_hz(m) for m in (60, 62, 64, 65, 67, 69, 71)] * 3  # gamme de Do
    r = vc.evaluer_justesse_f0(f0, "C")
    assert r["note_justesse"] == 100
    assert r["ecart_moyen_cents"] == 0.0
    assert r["verdict"] == "très juste"
    assert r["pct_dans_tolerance"] == 1.0


def test_justesse_biais_dièse_detecte():
    f0 = [_hz(m + 0.30) for m in (60, 64, 67)] * 4  # +30 cents partout
    r = vc.evaluer_justesse_f0(f0, "C")
    assert r["tendance_cents"] > 0
    assert "♯" in r["tendance"]
    assert r["note_justesse"] == 0  # 30c > tolérance 25c


def test_justesse_biais_bémol_detecte():
    f0 = [_hz(m - 0.30) for m in (60, 64, 67)] * 4
    r = vc.evaluer_justesse_f0(f0, "C")
    assert r["tendance_cents"] < 0
    assert "♭" in r["tendance"]


def test_justesse_petit_ecart_reste_dans_tolerance():
    f0 = [_hz(m + 0.10) for m in (60, 62, 64, 65, 67)] * 3  # +10 cents
    r = vc.evaluer_justesse_f0(f0, "C")
    assert r["note_justesse"] == 100  # 10c < 25c
    assert r["ecart_moyen_cents"] > 0


def test_justesse_mineur_utilise_gamme_mineure():
    # En La mineur, Do/Mi/Sol/La sont dans la gamme.
    f0 = [_hz(m) for m in (57, 60, 64, 69)] * 3
    r = vc.evaluer_justesse_f0(f0, "Am")
    assert r["ton"] == "A mineur"
    assert r["note_justesse"] == 100


def test_justesse_tolerance_personnalisee():
    f0 = [_hz(m + 0.30) for m in (60, 64, 67)] * 4  # 30c
    r = vc.evaluer_justesse_f0(f0, "C", tolerance_cents=50.0)
    assert r["note_justesse"] == 100  # 30c < 50c


def test_justesse_ton_illisible():
    assert "error" in vc.evaluer_justesse_f0([440.0] * 10, "ZZZ")


def test_justesse_pas_assez_de_frames():
    assert "error" in vc.evaluer_justesse_f0([440.0, 441.0], "C")


def test_justesse_ignore_valeurs_non_finies():
    f0 = [_hz(60), float("nan"), 0.0, -5.0, _hz(64), _hz(67), _hz(60), _hz(64)]
    r = vc.evaluer_justesse_f0(f0, "C")
    assert r["frames"] == 5  # 3 valeurs invalides écartées


# --------------------------------------------------------------------------- #
# recommander_autotune                                                        #
# --------------------------------------------------------------------------- #


def test_autotune_gamme_et_reference():
    r = vc.recommander_autotune("C", "pop")
    assert r["note_reference"] == "C"
    assert r["gamme_a_charger"] == ["C", "D", "E", "F", "G", "A", "B"]


def test_autotune_vitesse_trap_rapide_vs_ballade_lente():
    trap = vc.recommander_autotune("Am", "trap")
    ballade = vc.recommander_autotune("Am", "ballade")
    assert trap["retune_speed_ms"] < ballade["retune_speed_ms"]
    assert trap["retune_speed_ms"] == 0


def test_autotune_alias_trap_soul():
    r = vc.recommander_autotune("C", "trap soul")
    assert r["style"] == "soul"


def test_autotune_force_module_par_justesse():
    juste = vc.recommander_autotune("C", "pop", justesse_cents=10)
    faux = vc.recommander_autotune("C", "pop", justesse_cents=50)
    assert juste["force_pct"] < faux["force_pct"]
    assert faux["force_pct"] == 100


def test_autotune_style_inconnu_retombe_pop():
    r = vc.recommander_autotune("C", "polka")
    assert r["style"] == "pop"


def test_autotune_ton_illisible():
    assert "error" in vc.recommander_autotune("ZZZ", "pop")


def test_autotune_mineur():
    r = vc.recommander_autotune("Am", "rnb")
    assert r["gamme_a_charger"] == ["A", "B", "C", "D", "E", "F", "G"]


# --------------------------------------------------------------------------- #
# Robustesse                                                                  #
# --------------------------------------------------------------------------- #


def test_hz_to_midi_a440():
    assert math.isclose(vc._hz_to_midi(440.0), 69.0, abs_tol=1e-9)


def test_sorties_json_serialisables():
    f0 = [_hz(m) for m in (60, 62, 64, 65, 67)] * 3
    for r in (vc.evaluer_justesse_f0(f0, "C"), vc.recommander_autotune("C", "pop", 20)):
        json.dumps(r, ensure_ascii=False)
