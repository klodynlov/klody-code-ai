"""Tests de tools/audio.py contre des doubles de librosa/soundfile.

`librosa` et `soundfile` sont optionnels par conception et absents en CI : chaque
fonction y sortait sur « librosa non installé » et le traitement du signal n'était
jamais exécuté (25 % de couverture). Voir tests/fake_audio_libs.py — numpy et les
fichiers WAV sont réels, seul le paquet manquant est doublé.

Deux volets : le **sandbox** (qui doit tenir même sans librosa) et le
**traitement** (trim, fades, normalisation, mixage), vérifié en relisant les WAV
produits plutôt qu'en se fiant au dict de retour.
"""
from __future__ import annotations

import numpy as np
import pytest

from tests import fake_audio_libs


def _sine(path, freq=440.0, sr=8000, dur=1.0, amp=0.5):
    t = np.arange(int(sr * dur)) / sr
    fake_audio_libs.write_wav(path, amp * np.sin(2 * np.pi * freq * t), sr)
    return path


@pytest.fixture
def audio(tmp_path, monkeypatch):
    """`tools.audio` avec les doubles POSÉS sur ses globals, sandbox dans tmp_path.

    Ni `sys.modules` ni `importlib.reload` : le module lit `librosa` et `sf`
    comme de simples globals. Un reload créerait une nouvelle classe
    `AudioSandboxViolation` que les tests l'ayant importée par son nom
    (tests/test_audio_wiring.py) cesseraient de reconnaître.
    """
    from tools import audio as module

    librosa, soundfile = fake_audio_libs.build()
    monkeypatch.setattr(module, "librosa", librosa, raising=False)
    monkeypatch.setattr(module, "sf", soundfile, raising=False)
    # `numpy` est importé dans le MÊME try que librosa : celui-ci échouant en
    # premier, `np` n'est pas lié non plus, bien que numpy soit installé.
    monkeypatch.setattr(module, "np", np, raising=False)
    monkeypatch.setattr(module, "HAS_LIBROSA", True)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "_AUDIO_ROOTS", [tmp_path])

    module._faux_librosa = librosa   # accès pour les tests qui simulent une panne
    try:
        yield module
    finally:
        del module._faux_librosa
        fake_audio_libs.set_beat_track_return(np.float64(120.0))


@pytest.fixture
def sans_libs(tmp_path, monkeypatch):
    """`tools.audio` tel qu'il est réellement en CI : sans librosa."""
    from tools import audio as module

    monkeypatch.setattr(module, "HAS_LIBROSA", False)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "_AUDIO_ROOTS", [tmp_path])
    return module


# --- sandbox : tient AVEC ou SANS librosa ---------------------------------


@pytest.mark.parametrize(
    "appel",
    [
        lambda m, p: m.analyze_audio(p),
        lambda m, p: m.edit_wav(p),
        lambda m, p: m.mix_stems([p]),
        lambda m, p: m.convert_format(p),
        lambda m, p: m.get_waveform_data(p),
    ],
)
def test_chemin_hors_sandbox_refuse(sans_libs, appel):
    """La validation précède le test de disponibilité : le sandbox ne doit pas
    dépendre de la présence d'une dépendance optionnelle."""
    out = appel(sans_libs, "/etc/passwd")
    assert "hors des racines autorisées" in out["error"]


def test_generate_silence_hors_sandbox_refuse(sans_libs):
    assert "hors des racines" in sans_libs.generate_silence(1.0, output="/tmp/x.wav")["error"]


def test_fichier_absent_signale(sans_libs, tmp_path):
    assert "non trouvé" in sans_libs.analyze_audio(str(tmp_path / "fantome.wav"))["error"]


def test_traversee_par_dotdot_refusee(sans_libs, tmp_path):
    (tmp_path / "sous").mkdir()
    assert "hors des racines" in sans_libs.analyze_audio(str(tmp_path / "sous" / ".." / ".." / "x.wav"))["error"]


@pytest.mark.parametrize(
    "appel,attendu",
    [
        (lambda m, p: m.analyze_audio(p), "librosa non installé"),
        (lambda m, p: m.edit_wav(p), "librosa non installé"),
        (lambda m, p: m.mix_stems([p]), "librosa non installé"),
        (lambda m, p: m.get_waveform_data(p), "librosa non installé"),
    ],
)
def test_message_clair_sans_librosa(sans_libs, tmp_path, appel, attendu):
    """Dégradation douce : un message actionnable, jamais une ImportError."""
    f = _sine(tmp_path / "a.wav")
    assert attendu in appel(sans_libs, str(f))["error"]


def test_generate_silence_sans_libs_nomme_soundfile(sans_libs, tmp_path):
    out = sans_libs.generate_silence(0.1, output=str(tmp_path / "s.wav"))
    assert "soundfile non installé" in out["error"]


# --- analyze_audio ---------------------------------------------------------


def test_analyze_audio_mesure_le_signal(audio, tmp_path):
    f = _sine(tmp_path / "la.wav", freq=440.0, sr=8000, dur=2.0, amp=0.5)
    out = audio.analyze_audio(str(f))

    assert out["sample_rate"] == 8000
    assert out["duration_sec"] == pytest.approx(2.0, abs=0.01)
    assert out["duration_mm_ss"] == "00:02"
    assert out["samples"] == 16000
    assert out["channels"] == 1
    # Sinus d'amplitude 0.5 : pic ≈ -6 dB, RMS ≈ -9 dB (0.5/√2).
    assert out["peak_db"] == pytest.approx(-6.0, abs=0.5)
    assert out["rms_db"] == pytest.approx(-9.0, abs=0.5)


def test_analyze_audio_estime_la_tonalite(audio, tmp_path):
    """Un La 440 doit ressortir en classe « A » — sinon le chroma n'est pas lu."""
    out = audio.analyze_audio(str(_sine(tmp_path / "la.wav", freq=440.0)))
    assert out["key"].startswith("A")


@pytest.mark.parametrize(
    "retour,attendu",
    [
        (np.float64(128.0), 128.0),          # scalaire : librosa récent
        (np.array([90.0, 110.0]), 100.0),    # tableau : librosa ancien → moyenne
    ],
)
def test_analyze_audio_encaisse_les_deux_formes_de_tempo(audio, tmp_path, retour, attendu):
    """`beat_track` a rendu tantôt un scalaire, tantôt un tableau selon les
    versions de librosa — les deux doivent donner un BPM."""
    fake_audio_libs.set_beat_track_return(retour)
    assert audio.analyze_audio(str(_sine(tmp_path / "a.wav")))["bpm"] == pytest.approx(attendu)


def test_analyze_audio_erreur_moteur_devient_un_dict(audio, tmp_path, monkeypatch):
    """Aucune fonction du module ne lève : l'appelant est la boucle ReAct."""
    monkeypatch.setattr(audio._faux_librosa, "load", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("codec inconnu")))
    assert "codec inconnu" in audio.analyze_audio(str(_sine(tmp_path / "a.wav")))["error"]


# --- edit_wav : le traitement, vérifié sur le fichier produit ---------------


def test_edit_wav_decoupe(audio, tmp_path):
    f = _sine(tmp_path / "src.wav", sr=8000, dur=4.0)
    out = audio.edit_wav(str(f), start=1.0, end=3.0, output=str(tmp_path / "coupe.wav"))

    assert out["status"] == "ok"
    assert out["duration_sec"] == pytest.approx(2.0, abs=0.01)
    y, sr = fake_audio_libs.load(tmp_path / "coupe.wav")
    assert len(y) == pytest.approx(2.0 * sr, abs=2)


def test_edit_wav_normalise_au_pic(audio, tmp_path):
    f = _sine(tmp_path / "faible.wav", amp=0.1)
    audio.edit_wav(str(f), normalize=True, output=str(tmp_path / "fort.wav"))

    y, _ = fake_audio_libs.load(tmp_path / "fort.wav")
    assert float(np.max(np.abs(y))) == pytest.approx(1.0, abs=0.01)


def test_edit_wav_fade_in_part_du_silence(audio, tmp_path):
    f = _sine(tmp_path / "src.wav", sr=8000, dur=2.0)
    audio.edit_wav(str(f), fade_in=0.5, output=str(tmp_path / "fade.wav"))

    y, _ = fake_audio_libs.load(tmp_path / "fade.wav")
    assert abs(float(y[0])) < 0.01
    # Le fade est monotone : le premier quart est plus discret que la fin.
    assert float(np.max(np.abs(y[:1000]))) < float(np.max(np.abs(y[-1000:])))


def test_edit_wav_fade_out_finit_en_silence(audio, tmp_path):
    f = _sine(tmp_path / "src.wav", sr=8000, dur=2.0)
    audio.edit_wav(str(f), fade_out=0.5, output=str(tmp_path / "fade.wav"))

    y, _ = fake_audio_libs.load(tmp_path / "fade.wav")
    assert abs(float(y[-1])) < 0.02


def test_edit_wav_sans_output_ecrase_la_source(audio, tmp_path):
    f = _sine(tmp_path / "src.wav", sr=8000, dur=2.0)
    out = audio.edit_wav(str(f), end=1.0)

    assert out["output"] == str(f)
    y, sr = fake_audio_libs.load(f)
    assert len(y) == pytest.approx(sr, abs=2)


# --- mix_stems -------------------------------------------------------------


def test_mix_stems_somme_les_pistes(audio, tmp_path):
    a = _sine(tmp_path / "a.wav", freq=440.0, amp=0.3)
    b = _sine(tmp_path / "b.wav", freq=880.0, amp=0.3)
    out = audio.mix_stems([str(a), str(b)], output=str(tmp_path / "mix.wav"))

    assert out["status"] == "ok"
    assert out["stems_count"] == 2
    y, _ = fake_audio_libs.load(tmp_path / "mix.wav")
    # La somme dépasse chaque piste prise isolément.
    assert float(np.max(np.abs(y))) > 0.3


def test_mix_stems_applique_les_gains_en_db(audio, tmp_path):
    a = _sine(tmp_path / "a.wav", amp=0.5)
    audio.mix_stems([str(a)], gains=[-6.0], output=str(tmp_path / "attenue.wav"))

    y, _ = fake_audio_libs.load(tmp_path / "attenue.wav")
    assert float(np.max(np.abs(y))) == pytest.approx(0.25, abs=0.02)


def test_mix_stems_limite_le_clipping(audio, tmp_path):
    """Deux pistes fortes en phase dépasseraient 1.0 : le mix doit être ramené."""
    a = _sine(tmp_path / "a.wav", freq=440.0, amp=0.9)
    b = _sine(tmp_path / "b.wav", freq=440.0, amp=0.9)
    audio.mix_stems([str(a), str(b)], output=str(tmp_path / "mix.wav"))

    y, _ = fake_audio_libs.load(tmp_path / "mix.wav")
    assert float(np.max(np.abs(y))) <= 1.0


def test_mix_stems_aligne_les_frequences(audio, tmp_path):
    """Un stem à 16 kHz mixé avec un autre à 8 kHz doit être ré-échantillonné,
    sinon il jouerait à la mauvaise vitesse."""
    a = _sine(tmp_path / "a.wav", sr=8000, dur=1.0)
    b = _sine(tmp_path / "b.wav", sr=16000, dur=1.0)
    out = audio.mix_stems([str(a), str(b)], output=str(tmp_path / "mix.wav"))

    assert out["sample_rate"] == 8000
    assert out["duration_sec"] == pytest.approx(1.0, abs=0.01)


def test_mix_stems_pad_sur_la_plus_longue(audio, tmp_path):
    a = _sine(tmp_path / "a.wav", sr=8000, dur=1.0)
    b = _sine(tmp_path / "b.wav", sr=8000, dur=3.0)
    out = audio.mix_stems([str(a), str(b)], output=str(tmp_path / "mix.wav"))

    assert out["duration_sec"] == pytest.approx(3.0, abs=0.01)


# --- generate_silence / convert_format / waveform --------------------------


def test_generate_silence_ecrit_un_wav_muet(audio, tmp_path):
    out = audio.generate_silence(0.5, sr=8000, output=str(tmp_path / "s.wav"))

    assert out["status"] == "ok"
    y, sr = fake_audio_libs.load(tmp_path / "s.wav")
    assert sr == 8000
    assert len(y) == 4000
    assert float(np.max(np.abs(y))) == 0.0


def test_convert_format_passe_par_librosa(audio, tmp_path):
    f = _sine(tmp_path / "src.wav")
    out = audio.convert_format(str(f), target_format="wav", output=str(tmp_path / "out.wav"))

    assert out == {"status": "ok", "output": str(tmp_path / "out.wav"), "format": "wav"}
    assert (tmp_path / "out.wav").exists()


def test_convert_format_deduit_le_nom_de_sortie(audio, tmp_path):
    f = _sine(tmp_path / "morceau.wav")
    out = audio.convert_format(str(f), target_format="flac")
    assert out["output"].endswith("morceau.flac")


def test_convert_format_bascule_sur_ffmpeg(audio, tmp_path, monkeypatch):
    """librosa en échec → repli ffmpeg, sans que l'appelant n'ait rien à faire."""
    import subprocess

    monkeypatch.setattr(audio._faux_librosa, "load", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("format illisible")))
    appels = []

    def faux_run(cmd, **kw):
        appels.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", faux_run)
    out = audio.convert_format(str(_sine(tmp_path / "src.wav")), target_format="mp3")

    assert out["status"] == "ok"
    assert appels and appels[0][0] == "ffmpeg"


def test_convert_format_ffmpeg_absent(audio, tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(audio._faux_librosa, "load", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("nope")))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError))

    assert "ffmpeg non installé" in audio.convert_format(str(_sine(tmp_path / "s.wav")))["error"]


def test_convert_format_remonte_l_erreur_ffmpeg(audio, tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(audio._faux_librosa, "load", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("nope")))
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "Invalid data found"),
    )

    assert "Invalid data found" in audio.convert_format(str(_sine(tmp_path / "s.wav")))["error"]


@pytest.mark.parametrize(
    "appel",
    [
        lambda m, p, d: m.edit_wav(p, output=str(d / "o.wav")),
        lambda m, p, d: m.mix_stems([p], output=str(d / "o.wav")),
        lambda m, p, d: m.generate_silence(0.1, output=str(d / "o.wav")),
    ],
)
def test_ecriture_en_echec_devient_un_dict(audio, tmp_path, monkeypatch, appel):
    """Contrat commun à tout le module : un disque plein ou un format refusé rend
    un dict d'erreur. La boucle ReAct n'a pas à attraper d'exception."""
    monkeypatch.setattr(audio.sf, "write", lambda *a, **k: (_ for _ in ()).throw(
        OSError("disque plein")))
    out = appel(audio, str(_sine(tmp_path / "src.wav")), tmp_path)

    assert "disque plein" in out["error"]


def test_get_waveform_data_erreur_moteur_devient_un_dict(audio, tmp_path, monkeypatch):
    monkeypatch.setattr(audio._faux_librosa.feature, "rms", lambda **k: (_ for _ in ()).throw(
        RuntimeError("trame invalide")))
    assert "trame invalide" in audio.get_waveform_data(str(_sine(tmp_path / "a.wav")))["error"]


def test_get_waveform_data_borne_les_points(audio, tmp_path):
    f = _sine(tmp_path / "long.wav", sr=8000, dur=10.0)
    out = audio.get_waveform_data(str(f), num_points=32)

    assert out["num_points"] <= 32
    assert len(out["values"]) == out["num_points"]
    assert all(v >= 0 for v in out["values"])
    assert out["sample_rate"] == 8000
