"""Tests du socle d'analyse audio (klody_mcp.audio_analysis).

Le socle (numpy + scipy + stdlib `wave`) tourne en CI ; librosa/pyloudnorm/soundfile
sont optionnels -> on n'EXIGE jamais leurs métriques (lufs/tempo/key peuvent être
None). On génère un WAV PCM 16 bits synthétique (sinus + silence) avec la stdlib et
on vérifie les métriques objectives.
"""
from __future__ import annotations

import math
import wave

import numpy as np
import pytest
from klody_mcp import audio_analysis as aa


def _write_sine_wav(path, sr=44100, freq=440.0, amp=0.9, dur=1.0, silence=0.5, ch=2):
    n = int(sr * dur)
    ns = int(sr * silence)
    t = np.arange(n) / sr
    sig = np.concatenate([amp * np.sin(2 * np.pi * freq * t), np.zeros(ns)])
    i16 = (np.clip(sig, -1.0, 1.0) * 32767).astype("<i2")
    if ch == 2:
        inter = np.empty(i16.size * 2, dtype="<i2")
        inter[0::2] = i16
        inter[1::2] = i16
    else:
        inter = i16
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(inter.tobytes())
    return n + ns


def test_analyze_sine_levels_and_format(tmp_path):
    p = tmp_path / "sine.wav"
    _write_sine_wav(p)
    m = aa.analyze_file(str(p))

    assert m["sample_rate"] == 44100
    assert m["channels"] == 2
    assert m["duration_s"] == pytest.approx(1.5, abs=0.01)
    # sinus à 0.9 -> peak ~ 20*log10(0.9) = -0.92 dBFS, pas de clipping
    assert m["peak_dbfs"] == pytest.approx(20 * math.log10(0.9), abs=0.3)
    assert m["clipping"] is False
    assert m["clip_fraction"] == 0.0
    # crest factor d'un sinus ~ 3 dB (>0 en tout cas)
    assert m["crest_factor_db"] > 1.0
    assert m["rms_dbfs"] < m["peak_dbfs"]
    # 0.5s de silence sur 1.5s -> ~1/3 des fenêtres silencieuses
    assert m["silence_ratio"] > 0.2
    # canaux identiques -> corrélation ~1
    assert m["stereo_correlation"] == pytest.approx(1.0, abs=0.01)
    assert isinstance(m["used"], list)


def test_spectral_centroid_near_fundamental(tmp_path):
    p = tmp_path / "sine.wav"
    _write_sine_wav(p, freq=440.0)
    m = aa.analyze_file(str(p))
    # le centroïde d'un sinus à 440 Hz tombe dans le grave/bas-médium
    assert 250.0 < m["spectral_centroid_hz"] < 900.0
    be = m["band_energy"]
    assert be is not None
    # 440 Hz appartient à la bande 'low' (120-500) -> elle doit dominer
    assert be["low"] > be["mid"]
    assert be["low"] > be["sub"]


def test_optional_metrics_present_but_may_be_none(tmp_path):
    p = tmp_path / "sine.wav"
    _write_sine_wav(p)
    m = aa.analyze_file(str(p))
    # ces clés EXISTENT toujours ; leur valeur est None si la lib optionnelle manque
    for k in ("lufs_integrated", "tempo_bpm", "key", "key_confidence", "true_peak_dbfs"):
        assert k in m
    assert m["lufs_integrated"] is None or isinstance(m["lufs_integrated"], (int, float))
    assert m["tempo_bpm"] is None or isinstance(m["tempo_bpm"], (int, float))


def test_stdlib_reader_decodes_pcm16(tmp_path):
    """Le repli stdlib (chemin CI sans soundfile) décode bien le PCM 16 bits."""
    p = tmp_path / "sine.wav"
    _write_sine_wav(p, dur=0.5, silence=0.0, ch=1)
    data, sr = aa._read_wav_stdlib(str(p))
    assert sr == 44100
    assert data.shape[1] == 1
    assert data.shape[0] == pytest.approx(0.5 * 44100, abs=2)
    assert float(np.max(np.abs(data))) == pytest.approx(0.9, abs=0.01)


def test_compare_versions_delta(tmp_path):
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _write_sine_wav(a, amp=0.9)
    _write_sine_wav(b, amp=0.45)  # -6 dB environ
    cmp = aa.compare(aa.analyze_file(str(a)), aa.analyze_file(str(b)))
    assert set(cmp) == {"a", "b", "delta"}
    # b est ~6 dB plus bas -> delta de peak négatif et proche de -6
    assert cmp["delta"]["peak_dbfs"] == pytest.approx(-6.0, abs=0.5)


def test_silence_is_json_safe(tmp_path):
    """Silence -> LUFS pyloudnorm = -inf : le résultat doit rester JSON-strict
    (allow_nan=False, comme côté MCP), donc aucun inf/nan ne doit fuiter."""
    import json

    p = tmp_path / "silence.wav"
    _write_sine_wav(p, amp=0.0, dur=1.0, silence=0.0)  # amplitude 0 = silence pur
    m = aa.analyze_file(str(p))

    assert m["lufs_integrated"] is None or math.isfinite(m["lufs_integrated"])
    assert m["tempo_bpm"] is None or (isinstance(m["tempo_bpm"], (int, float)) and m["tempo_bpm"] > 0)
    json.dumps(m, allow_nan=False)  # ne doit PAS lever (aucun inf/nan)

    def _all_finite(o):
        if isinstance(o, float):
            return math.isfinite(o)
        if isinstance(o, dict):
            return all(_all_finite(v) for v in o.values())
        if isinstance(o, list):
            return all(_all_finite(v) for v in o)
        return True

    assert _all_finite(m)


def test_missing_file_raises(tmp_path):
    # ASI02 : un chemin HORS des racines autorisées est refusé AVANT tout accès
    # disque (ne fuite pas l'existence hors sandbox) → PermissionError.
    with pytest.raises(PermissionError):
        aa.analyze_file("/nonexistent/path/to/nowhere.wav")
    # Un fichier absent mais DANS une racine autorisée ($TMPDIR) → FileNotFoundError.
    with pytest.raises(FileNotFoundError):
        aa.analyze_file(str(tmp_path / "absent.wav"))
    with pytest.raises(ValueError):
        aa.analyze_file("")
