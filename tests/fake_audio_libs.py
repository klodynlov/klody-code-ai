"""Doubles de `librosa` et `soundfile` pour tester tools/audio.py.

Ces deux paquets sont **optionnels par conception** (cf. requirements.txt) et
absents en CI : `HAS_LIBROSA` y vaut False, chaque fonction de tools/audio.py sort
sur son message « librosa non installé », et rien du traitement du signal n'était
exécuté — 25 % de couverture.

Le double reste honnête sur l'essentiel : **numpy est réel** et les fichiers le
sont aussi. `load`/`write` passent par le module `wave` de la stdlib, donc un WAV
écrit par un test est un vrai WAV relisible. Trim, fades, normalisation, gains et
sommation de stems restent donc de vraies opérations numpy sur de vrais
échantillons — c'est bien tools/audio.py qui est jugé.

Ce qui est approximé : `beat_track`, dont un vrai suivi de tempo n'a pas sa place
ici (tools/audio.py se contente d'en formater la sortie ; ce qui compte est qu'il
encaisse les DEUX formes de retour que librosa a connues selon ses versions,
scalaire ou tableau).
"""
from __future__ import annotations

import types
import wave

import numpy as np

# Tempo rendu par le faux beat_track. Modifiable par test via
# `set_beat_track_return()` pour couvrir les deux formes de retour de librosa.
_BEAT_RETURN: object = np.float64(120.0)


def set_beat_track_return(value) -> None:
    global _BEAT_RETURN
    _BEAT_RETURN = value


# ── I/O réelle (stdlib wave) ─────────────────────────────────────────────────


def write_wav(path, y, sr: int) -> None:
    """Écrit un WAV PCM 16 bits mono. Sert de `soundfile.write` ET d'utilitaire
    aux tests pour fabriquer leurs fixtures."""
    data = np.asarray(y, dtype=np.float64)
    if data.ndim > 1:                      # stéréo → mono, comme librosa.load
        data = data.mean(axis=0)
    i16 = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(i16.tobytes())


def _read_wav(path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        raw = w.readframes(n)
    y = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32767.0
    if ch > 1:
        y = y.reshape(-1, ch).mean(axis=1)
    return y, sr


# ── librosa ──────────────────────────────────────────────────────────────────


def load(path, sr=None, **_kw):
    """`sr=None` = fréquence native, seul mode utilisé par tools/audio.py."""
    y, native_sr = _read_wav(path)
    if sr is not None and sr != native_sr:
        y = resample(y, orig_sr=native_sr, target_sr=sr)
        native_sr = sr
    return y, native_sr


def get_duration(y=None, sr=None, **_kw) -> float:
    return len(y) / float(sr)


def resample(y, orig_sr=None, target_sr=None, **_kw):
    """Ré-échantillonnage linéaire — suffisant : tools/audio.py n'en attend qu'un
    tableau à la bonne longueur, pour aligner des stems de fréquences distinctes."""
    if orig_sr == target_sr:
        return np.asarray(y, dtype=np.float64)
    n_out = max(1, round(len(y) * target_sr / orig_sr))
    return np.interp(
        np.linspace(0, len(y) - 1, n_out),
        np.arange(len(y)),
        np.asarray(y, dtype=np.float64),
    )


def _rms(y=None, frame_length=2048, hop_length=512, **_kw):
    """RMS par trame, en (1, n_frames) comme librosa."""
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return np.zeros((1, 1))
    frames = [
        y[i : i + frame_length]
        for i in range(0, max(1, len(y) - frame_length + 1), hop_length)
    ] or [y]
    return np.array([[float(np.sqrt(np.mean(f**2))) for f in frames]])


def _chroma_stft(y=None, sr=None, **_kw):
    """Chroma 12 × n_frames, réellement dérivé du spectre.

    Simplifié (une trame, FFT globale) mais pas arbitraire : un La 440 ressort en
    classe « A », ce qui rend l'estimation de tonalité de analyze_audio
    vérifiable plutôt que tautologique.
    """
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return np.zeros((12, 1))
    spectre = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(len(y), 1.0 / sr)
    chroma = np.zeros(12)
    utiles = (freqs > 20) & (freqs < sr / 2)
    # MIDI = 69 + 12·log2(f/440) ; la classe de hauteur est ce nombre modulo 12.
    midi = 69 + 12 * np.log2(np.maximum(freqs[utiles], 1e-9) / 440.0)
    for classe, poids in zip(np.mod(np.round(midi), 12).astype(int), spectre[utiles],
                             strict=False):
        chroma[classe] += poids
    total = chroma.sum()
    if total > 0:
        chroma = chroma / total
    return chroma.reshape(12, 1)


def _beat_track(y=None, sr=None, **_kw):
    return _BEAT_RETURN, np.array([0, 1, 2])


# ── Construction des modules ─────────────────────────────────────────────────


def build() -> tuple[types.ModuleType, types.ModuleType]:
    """Retourne (librosa, soundfile) prêts à être POSÉS sur tools.audio.

    Volontairement pas d'injection dans `sys.modules` ni de `importlib.reload` :
    tools/audio.py lit `librosa` et `sf` comme de simples globals, donc les poser
    suffit. Recharger le module créerait au passage une NOUVELLE classe
    `AudioSandboxViolation`, que le `pytest.raises` de tests/test_audio_wiring.py
    — qui l'a importée par son nom — ne reconnaîtrait plus.
    """
    librosa = types.ModuleType("librosa")
    librosa.load = load
    librosa.get_duration = get_duration
    librosa.resample = resample

    feature = types.ModuleType("librosa.feature")
    feature.rms = _rms
    feature.chroma_stft = _chroma_stft

    beat = types.ModuleType("librosa.beat")
    beat.beat_track = _beat_track

    librosa.feature = feature
    librosa.beat = beat

    soundfile = types.ModuleType("soundfile")
    soundfile.write = write_wav

    return librosa, soundfile
