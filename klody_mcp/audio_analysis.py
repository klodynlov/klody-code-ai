"""Analyse audio hors REAPER — le service « oreilles » du DAW agentique (P3).

Lit un WAV (rendu par REAPER ou fourni) et calcule des métriques OBJECTIVES :
niveau (peak/true-peak/RMS/crest), dynamique, spectre (centroïde, rolloff, énergie
par bandes), stéréo (corrélation, largeur), silence, clipping. Optionnellement LUFS
(pyloudnorm) et tempo/tonalité (librosa) si ces libs sont présentes.

Pas de dépendance REAPER : on analyse un fichier sur disque. Le socle (numpy + scipy
+ stdlib `wave`) suffit et tourne en CI ; les libs lourdes (librosa, pyloudnorm,
soundfile) sont OPTIONNELLES — dégradation propre (champ à None, jamais d'erreur).

Toute conclusion subjective (« manque de présence »…) relève de l'appelant et doit
rester une hypothèse : ce module ne renvoie que des nombres.
"""
from __future__ import annotations

import contextlib
import math
import wave

import numpy as np

_EPS = 1e-12
_FLOOR_DB = -150.0  # plancher dB (silence) — évite -inf non sérialisable en JSON


def _db(x: float, floor: float = _FLOOR_DB) -> float:
    """Amplitude linéaire (0..1) -> dBFS, planché à `floor` (jamais -inf)."""
    if x <= 0:
        return floor
    return max(floor, 20.0 * math.log10(x))


def _json_safe(obj):
    """Remplace tout float non fini (inf/nan) par None, récursivement. Filet de
    sécurité : un inf/nan (ex. LUFS d'un silence par pyloudnorm) casse la
    sérialisation JSON stricte côté MCP (allow_nan=False)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# Lecture WAV : soundfile si présent (tous formats), sinon stdlib wave (PCM).  #
# --------------------------------------------------------------------------- #


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    """Renvoie (échantillons float64 [frames, channels] dans [-1, 1], sample_rate)."""
    try:
        import soundfile as sf
    except ImportError:
        return _read_wav_stdlib(path)
    data, sr = sf.read(path, always_2d=True, dtype="float64")
    return np.asarray(data, dtype=np.float64), int(sr)


def _read_wav_stdlib(path: str) -> tuple[np.ndarray, int]:
    """Repli sans soundfile : décode un WAV PCM (8/16/24/32 bits) via la stdlib."""
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 1:  # PCM 8 bits = non signé, centré sur 128
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif sw == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sw == 3:  # PCM 24 bits little-endian : réassemble 3 octets -> int signé
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        a = v.astype(np.float64) / 8388608.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"largeur d'échantillon non supportée: {sw} octet(s)")
    ch = max(1, ch)
    return a.reshape(-1, ch), int(sr)


# --------------------------------------------------------------------------- #
# Métriques                                                                    #
# --------------------------------------------------------------------------- #


def _silence_ratio(mono: np.ndarray, sr: int, thr_db: float = -60.0,
                   win_s: float = 0.05) -> float:
    """Fraction de fenêtres ~50ms sous `thr_db` dBFS RMS."""
    if sr <= 0 or mono.size == 0:
        return 0.0
    w = max(1, int(sr * win_s))
    n = mono.size // w
    if n == 0:
        return 0.0
    seg = mono[:n * w].reshape(n, w)
    rms = np.sqrt(np.mean(seg ** 2, axis=1))
    return float(np.mean(rms < 10.0 ** (thr_db / 20.0)))


def _true_peak_db(data: np.ndarray, sr: int) -> float:
    """Vrai pic (inter-échantillon) par sur-échantillonnage 4x. Repli = pic brut
    si scipy absent ou signal très long (> 10 min : évite le coût mémoire 4x)."""
    if data.shape[0] == 0:
        return _FLOOR_DB
    raw_peak = float(np.max(np.abs(data)))
    if sr <= 0 or data.shape[0] > sr * 600:
        return _db(raw_peak)
    try:
        from scipy import signal
    except ImportError:
        return _db(raw_peak)
    up = signal.resample_poly(data, 4, 1, axis=0)
    return _db(float(np.max(np.abs(up))))


def _spectral(mono: np.ndarray, sr: int) -> dict:
    """Centroïde, rolloff 85%, énergie relative par bandes (Welch, scipy)."""
    empty = {"spectral_centroid_hz": None, "spectral_rolloff_hz": None,
             "band_energy": None}
    if mono.size < 32 or sr <= 0:
        return empty
    try:
        from scipy import signal
    except ImportError:
        return empty
    f, pxx = signal.welch(mono, fs=sr, nperseg=min(8192, mono.size))
    total = float(np.sum(pxx)) + _EPS
    centroid = float(np.sum(f * pxx) / total)
    cum = np.cumsum(pxx)
    rolloff = float(f[int(np.searchsorted(cum, 0.85 * cum[-1]))]) if cum[-1] > 0 else 0.0
    bands = [("sub", 0, 120), ("low", 120, 500), ("mid", 500, 2000),
             ("presence", 2000, 6000), ("air", 6000, sr / 2.0)]
    be = {nm: round(float(np.sum(pxx[(f >= lo) & (f < hi)]) / total), 4)
          for nm, lo, hi in bands}
    return {"spectral_centroid_hz": round(centroid, 1),
            "spectral_rolloff_hz": round(rolloff, 1), "band_energy": be}


def _stereo(data: np.ndarray) -> dict:
    """Corrélation L/R et largeur stéréo (RMS side / RMS mid)."""
    left = data[:, 0]
    right = data[:, 1]
    if left.size < 2:
        return {"stereo_correlation": None, "stereo_width": None}
    corr = float(np.corrcoef(left, right)[0, 1]) if np.std(left) > 0 and np.std(right) > 0 else 1.0
    mid = (left + right) / 2.0
    side = (left - right) / 2.0
    mid_rms = float(np.sqrt(np.mean(mid ** 2)))
    side_rms = float(np.sqrt(np.mean(side ** 2)))
    return {"stereo_correlation": round(corr, 4),
            "stereo_width": round(side_rms / (mid_rms + _EPS), 4)}


def _lufs(data: np.ndarray, sr: int) -> float | None:
    """LUFS intégré (ITU-R BS.1770) via pyloudnorm, ou None si absent/trop court."""
    try:
        import pyloudnorm as pyln
    except ImportError:
        return None
    if sr <= 0 or data.shape[0] < sr * 0.4:  # pyloudnorm exige >= 400ms
        return None
    try:
        loudness = float(pyln.Meter(sr).integrated_loudness(data))
    except Exception:
        return None
    # silence -> integrated_loudness = -inf (non sérialisable JSON) -> None
    return loudness if math.isfinite(loudness) else None


# Profils Krumhansl-Schmuckler (corrélation chroma -> tonalité), normalisés ensuite.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _estimate_key(y: np.ndarray, sr: int, librosa) -> tuple[str | None, float]:
    """Tonalité estimée (Krumhansl sur chroma CQT) + confiance [0..1]. HYPOTHÈSE."""
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    prof = chroma.mean(axis=1)
    if prof.sum() <= 0:
        return None, 0.0
    prof = prof / prof.sum()
    best = (-1.0, None)
    for i in range(12):
        for mode, tmpl in (("maj", _MAJOR), ("min", _MINOR)):
            t = np.roll(tmpl, i)
            t = t / t.sum()
            c = float(np.corrcoef(prof, t)[0, 1])
            if c > best[0]:
                best = (c, f"{_PITCHES[i]} {mode}")
    return best[1], max(0.0, best[0])


def _tempo_key(mono: np.ndarray, sr: int) -> tuple[dict, bool]:
    """Tempo (BPM) + tonalité via librosa. (résultats, librosa_disponible)."""
    out: dict = {"tempo_bpm": None, "key": None, "key_confidence": None}
    try:
        import librosa
    except ImportError:
        return out, False
    y = mono.astype(np.float32)
    # librosa peut échouer sur de l'audio court/pathologique -> on laisse les champs
    # à None. contextlib.suppress plutôt qu'un except-vide (que CodeQL py/empty-except
    # signale, bloquant via required_conversation_resolution).
    with contextlib.suppress(Exception):
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(np.atleast_1d(tempo)[0])
        out["tempo_bpm"] = round(bpm, 1) if bpm > 0 else None  # 0 = pas de tempo clair
    with contextlib.suppress(Exception):
        key, conf = _estimate_key(y, sr, librosa)
        out["key"] = key
        out["key_confidence"] = round(conf, 3)
    return out, True


def analyze_wav(path: str) -> dict:
    """Analyse complète d'un WAV. Renvoie un dict de métriques (voir module)."""
    data, sr = _read_wav(path)
    frames, ch = data.shape
    mono = data.mean(axis=1) if ch > 1 else data[:, 0]
    peak = float(np.max(np.abs(data))) if frames else 0.0
    rms = float(np.sqrt(np.mean(mono ** 2))) if frames else 0.0
    out: dict = {
        "path": path,
        "sample_rate": int(sr),
        "channels": int(ch),
        "duration_s": round(frames / float(sr), 4) if sr else 0.0,
        "peak_dbfs": round(_db(peak), 2),
        "true_peak_dbfs": round(_true_peak_db(data, sr), 2),
        "rms_dbfs": round(_db(rms), 2),
        "crest_factor_db": round(_db(peak) - _db(rms), 2),
        "dc_offset": round(float(np.mean(mono)), 6) if frames else 0.0,
        "clip_fraction": round(float(np.mean(np.abs(data) >= 0.999)), 6) if frames else 0.0,
        "clipping": bool(np.any(np.abs(data) >= 0.999)) if frames else False,
        "silence_ratio": round(_silence_ratio(mono, sr), 4),
        "used": [],
    }
    out.update(_spectral(mono, sr))
    if ch >= 2:
        out.update(_stereo(data))
    else:
        out["stereo_correlation"] = None
        out["stereo_width"] = None
    lufs = _lufs(data, sr)
    out["lufs_integrated"] = round(lufs, 2) if lufs is not None else None
    if lufs is not None:
        out["used"].append("pyloudnorm")
    tk, ran = _tempo_key(mono, sr)
    out.update(tk)
    if ran:
        out["used"].append("librosa")
    return _json_safe(out)  # garantit un dict JSON-sûr (aucun inf/nan)


def analyze_file(path: str) -> dict:
    """Valide le chemin puis analyse. Lève FileNotFoundError/ValueError si invalide."""
    import os
    if not isinstance(path, str) or not path:
        raise ValueError("chemin requis")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"fichier introuvable: {path}")
    return analyze_wav(path)


# Champs numériques comparés en avant/après (delta = b - a).
_COMPARE_KEYS = [
    "peak_dbfs", "true_peak_dbfs", "rms_dbfs", "crest_factor_db", "lufs_integrated",
    "spectral_centroid_hz", "spectral_rolloff_hz", "silence_ratio", "stereo_width",
]


def compare(a: dict, b: dict) -> dict:
    """Compare deux analyses : deltas (b - a) sur les métriques numériques clés."""
    delta = {}
    for k in _COMPARE_KEYS:
        va, vb = a.get(k), b.get(k)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta[k] = round(vb - va, 3)
        else:
            delta[k] = None
    return {"a": a, "b": b, "delta": delta}
