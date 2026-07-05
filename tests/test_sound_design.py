"""Tests du sound design (klody_mcp.sound_design) : presets + organisation de banques.

Presets et categoriser_fichiers sont purs ; organiser_banque testé sur un dossier temp."""
from __future__ import annotations

import json
import os

from klody_mcp import sound_design as sd

# --------------------------------------------------------------------------- #
# generer_preset_synth                                                        #
# --------------------------------------------------------------------------- #


def test_preset_basse_structure_complete():
    r = sd.generer_preset_synth("basse", "neutre")
    p = r["patch"]
    assert r["role"] == "basse"
    for k in ("oscillateurs", "filtre", "amp_env", "filtre_env", "unison", "polyphonie", "effets"):
        assert k in p
    assert p["oscillateurs"], "au moins un oscillateur"


def test_preset_alias_808_vers_basse():
    r = sd.generer_preset_synth("808")
    assert r["role"] == "basse"


def test_preset_caractere_sombre_baisse_le_cutoff():
    neutre = sd.generer_preset_synth("lead", "neutre")["patch"]["filtre"]["cutoff"]
    sombre = sd.generer_preset_synth("lead", "sombre")["patch"]["filtre"]["cutoff"]
    assert sombre < neutre


def test_preset_caractere_brillant_monte_le_cutoff():
    neutre = sd.generer_preset_synth("lead", "neutre")["patch"]["filtre"]["cutoff"]
    brillant = sd.generer_preset_synth("lead", "brillant")["patch"]["filtre"]["cutoff"]
    assert brillant > neutre


def test_preset_cutoff_reste_borne_0_1():
    for car in sd._CARACTERES:
        for role in sd._ROLES:
            f = sd.generer_preset_synth(role, car)["patch"]["filtre"]
            assert 0.0 <= f["cutoff"] <= 1.0
            assert 0.0 <= f["resonance"] <= 1.0


def test_preset_large_ajoute_unison():
    base = sd.generer_preset_synth("pad", "neutre")["patch"]["unison"]
    large = sd.generer_preset_synth("pad", "large")["patch"]["unison"]
    assert large > base
    assert large <= 9


def test_preset_agressif_ajoute_drive():
    r = sd.generer_preset_synth("lead", "agressif")
    assert "drive" in r["patch"]
    assert 0.0 <= r["patch"]["drive"] <= 1.0


def test_preset_ton_reference_reporte():
    r = sd.generer_preset_synth("bass", "chaud", ton="F#")
    assert r.get("note_reference") == "F#"


def test_preset_role_inconnu():
    assert "error" in sd.generer_preset_synth("kazoo")


def test_preset_caractere_inconnu():
    assert "error" in sd.generer_preset_synth("lead", "psychedelique")


def test_preset_deepcopy_independant():
    # Modifier un preset ne doit pas polluer le modèle de base.
    a = sd.generer_preset_synth("lead", "brillant")
    a["patch"]["filtre"]["cutoff"] = 0.999
    b = sd.generer_preset_synth("lead", "brillant")
    assert b["patch"]["filtre"]["cutoff"] != 0.999


# --------------------------------------------------------------------------- #
# categoriser_fichiers                                                        #
# --------------------------------------------------------------------------- #


def test_categoriser_percussions_et_types():
    noms = ["Kick_01.wav", "trap_snare.wav", "closed_hat.wav", "VoxAdlib.aiff",
            "riser_fx.wav", "piano_C3.wav"]
    r = sd.categoriser_fichiers(noms)
    assert "Kick_01.wav" in r["categories"]["kick"]
    assert "trap_snare.wav" in r["categories"]["snare"]
    assert "closed_hat.wav" in r["categories"]["hat"]
    assert "VoxAdlib.aiff" in r["categories"]["vocal"]
    assert "riser_fx.wav" in r["categories"]["fx"]
    assert "piano_C3.wav" in r["categories"]["keys"]


def test_categoriser_808_prioritaire_sur_bass():
    r = sd.categoriser_fichiers(["808_sub_bass.wav"])
    assert "808_sub_bass.wav" in r["categories"]["808"]


def test_categoriser_midi_et_presets_separes():
    r = sd.categoriser_fichiers(["line.mid", "patch.fxp", "kick.wav"])
    assert r["midi"] == ["line.mid"]
    assert r["presets"] == ["patch.fxp"]
    assert r["counts"]["midi"] == 1
    assert r["counts"]["presets"] == 1


def test_categoriser_ignore_non_audio():
    r = sd.categoriser_fichiers(["readme.txt", "cover.jpg", "kick.wav"])
    assert "readme.txt" in r["ignores"]
    assert "cover.jpg" in r["ignores"]
    assert "kick.wav" in r["categories"]["kick"]


def test_categoriser_non_categorises():
    r = sd.categoriser_fichiers(["mystery_sound.wav"])
    assert "mystery_sound.wav" in r["non_categorises"]


def test_categoriser_arborescence_suggeree():
    r = sd.categoriser_fichiers(["kick.wav", "snare.wav", "line.mid"])
    assert "kick/" in r["arborescence_suggeree"]
    assert "MIDI/" in r["arborescence_suggeree"]


def test_categoriser_liste_vide():
    r = sd.categoriser_fichiers([])
    assert r["categories"] == {}
    assert r["counts"] == {}


# --------------------------------------------------------------------------- #
# organiser_banque (dossier temporaire — $TMPDIR est une racine autorisée)     #
# --------------------------------------------------------------------------- #


def test_organiser_banque_sur_dossier(tmp_path):
    (tmp_path / "drums").mkdir()
    (tmp_path / "drums" / "kick_deep.wav").write_bytes(b"RIFF")
    (tmp_path / "snare_tight.wav").write_bytes(b"RIFF")
    (tmp_path / "notes.txt").write_text("x")
    r = sd.organiser_banque(str(tmp_path))
    assert "error" not in r
    assert r["total_fichiers"] == 3
    # le kick est dans un sous-dossier : le nom relatif contient 'kick'
    kicks = r["categories"].get("kick", [])
    assert any("kick" in n for n in kicks)
    assert "kick/" in r["arborescence_suggeree"] or "kick" in r["categories"]


def test_organiser_banque_sans_racine():
    # Ni root ni KLODY_SAMPLES_DIR -> erreur claire.
    old = os.environ.pop("KLODY_SAMPLES_DIR", None)
    try:
        assert "error" in sd.organiser_banque(None)
    finally:
        if old is not None:
            os.environ["KLODY_SAMPLES_DIR"] = old


def test_organiser_banque_dossier_inexistant(tmp_path):
    assert "error" in sd.organiser_banque(str(tmp_path / "nope"))


def test_sorties_json_serialisables():
    for r in (
        sd.generer_preset_synth("pad", "large"),
        sd.categoriser_fichiers(["kick.wav", "line.mid", "patch.fxp", "x.txt"]),
    ):
        json.dumps(r, ensure_ascii=False)
