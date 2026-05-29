"""Outils audio — analyse, édition, séparation de stems.

Toutes les fonctions valident leurs chemins contre les racines autorisées
(config.ALLOWED_ROOTS) : impossible de lire/écrire hors sandbox via l'audio.
"""

import subprocess
from pathlib import Path

from config import PROJECT_ROOT, build_allowed_roots, match_allowed_root

try:
    import librosa
    import numpy as np
    import soundfile as sf
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False


# Racines autorisées pour la lecture/écriture audio. Calculées une fois au
# chargement du module (cohérentes avec FileManager/Search).
_AUDIO_ROOTS = build_allowed_roots(PROJECT_ROOT)


class AudioSandboxViolation(PermissionError):
    """Chemin audio hors des racines autorisées."""


def _validated_path(path: str, *, must_exist: bool = True) -> str:
    """Résout `path` et vérifie qu'il tombe sous une racine autorisée.

    Renvoie le chemin absolu résolu (str). Lève AudioSandboxViolation si le
    chemin est hors sandbox, FileNotFoundError s'il doit exister mais n'existe
    pas. Les chemins relatifs sont résolus contre PROJECT_ROOT.
    """
    p = Path(path).expanduser()
    resolved = p.resolve() if p.is_absolute() else (PROJECT_ROOT / p).resolve()
    if match_allowed_root(resolved, _AUDIO_ROOTS) is None:
        raise AudioSandboxViolation(
            f"Chemin hors des racines autorisées: {path}"
        )
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"Fichier non trouvé: {path}")
    return str(resolved)


def analyze_audio(path: str) -> dict:
    """Analyse complète d'un fichier audio.
    
    Retourne : durée, BPM, key, RMS, loudness, nombre de canaux, sample rate.
    """
    try:
        path = _validated_path(path)
    except (AudioSandboxViolation, FileNotFoundError) as exc:
        return {"error": str(exc)}
    if not HAS_LIBROSA:
        return {"error": "librosa non installé — pip install librosa soundfile numpy"}
    try:
        y, sr = librosa.load(path, sr=None)
        duration = librosa.get_duration(y=y, sr=sr)
        rms = float(np.mean(librosa.feature.rms(y=y)[0]))
        
        # BPM
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo) if isinstance(tempo, (int, float, np.floating)) else float(np.mean(tempo))
        
        # Key estimation (chroma)
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        mean_chroma = np.mean(chroma, axis=1)
        key_map = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        key_idx = int(np.argmax(mean_chroma))
        mode = "major" if key_idx < 6 else "minor"
        key = f"{key_map[key_idx]} {mode}"
        
        # Stereo info
        n_channels = 1 if y.ndim == 1 else y.shape[0]
        
        # Pic dynamique
        peak = float(np.max(np.abs(y)))
        
        return {
            "file": path,
            "duration_sec": round(duration, 3),
            "duration_mm_ss": f"{int(duration//60):02d}:{int(duration%60):02d}",
            "sample_rate": sr,
            "bpm": round(bpm, 1),
            "key": key,
            "rms_db": round(20 * np.log10(rms + 1e-10), 1),
            "peak_db": round(20 * np.log10(peak + 1e-10), 1),
            "channels": n_channels,
            "samples": len(y),
        }
    except Exception as e:
        return {"error": str(e)}


def edit_wav(path: str, start: float = None, end: float = None,
             fade_in: float = None, fade_out: float = None,
             normalize: bool = False, output: str = None) -> dict:
    """Édition basique d'un fichier audio.
    
    Args:
        path: fichier source
        start: début en secondes (None = 0)
        end: fin en secondes (None = fin)
        fade_in: durée fade in en secondes
        fade_out: durée fade out en secondes
        normalize: normaliser au peak -1.0
        output: chemin de sortie (None = écrase l'original)
    """
    try:
        path = _validated_path(path)
        out_path = _validated_path(output or path, must_exist=False)
    except (AudioSandboxViolation, FileNotFoundError) as exc:
        return {"error": str(exc)}
    if not HAS_LIBROSA:
        return {"error": "librosa non installé"}
    try:
        y, sr = librosa.load(path, sr=None)

        # Trim
        sr_int = int(sr)
        start_samples = int((start or 0) * sr_int)
        end_samples = int((end or len(y)/sr) * sr_int)
        y = y[start_samples:end_samples]
        
        # Fade in
        if fade_in and fade_in > 0:
            fade_samples = int(fade_in * sr_int)
            fade_in_curve = np.linspace(0, 1, fade_samples)
            y[:fade_samples] *= fade_in_curve[:len(y[:fade_samples])]
        
        # Fade out
        if fade_out and fade_out > 0:
            fade_samples = int(fade_out * sr_int)
            fade_out_curve = np.linspace(1, 0, fade_samples)
            y[-fade_samples:] *= fade_out_curve[-len(y[-fade_samples]):]
        
        # Normalize
        if normalize:
            peak = np.max(np.abs(y))
            if peak > 0:
                y = y / peak
        
        # Output (déjà validé en haut)
        sf.write(out_path, y, sr)
        
        return {
            "status": "ok",
            "output": out_path,
            "duration_sec": round(len(y) / sr, 3),
            "sample_rate": sr,
            "normalized": normalize,
        }
    except Exception as e:
        return {"error": str(e)}


def mix_stems(paths: list[str], gains: list[float] = None, output: str = None) -> dict:
    """Mixe plusieurs fichiers audio ensemble.
    
    Args:
        paths: liste de chemins audio
        gains: liste de gains en dB (None = 0dB pour tous)
        output: chemin de sortie
    """
    try:
        paths = [_validated_path(p) for p in paths]
        out_path = _validated_path(output or "mixed_output.wav", must_exist=False)
    except (AudioSandboxViolation, FileNotFoundError) as exc:
        return {"error": str(exc)}
    if not HAS_LIBROSA:
        return {"error": "librosa non installé"}
    try:
        # Charger tous les stems
        stems = []
        sr_ref = None
        for i, p in enumerate(paths):
            y, sr = librosa.load(p, sr=None)
            if sr_ref is None:
                sr_ref = sr
            else:
                y = librosa.resample(y, orig_sr=sr, target_sr=sr_ref)
            
            # Appliquer gain
            if gains and gains[i] != 0:
                gain_linear = 10 ** (gains[i] / 20)
                y = y * gain_linear
            
            stems.append(y)
        
        # Pad au même length
        max_len = max(len(s) for s in stems)
        mixed = np.zeros(max_len)
        for s in stems:
            mixed[:len(s)] += s
        
        # Limiter pour éviter le clipping
        peak = np.max(np.abs(mixed))
        if peak > 1.0:
            mixed = mixed / peak
        
        # Écrire (out_path déjà validé en haut)
        sf.write(out_path, mixed, sr_ref)
        
        return {
            "status": "ok",
            "output": out_path,
            "duration_sec": round(max_len / sr_ref, 3),
            "sample_rate": sr_ref,
            "stems_count": len(paths),
            "peak_db": round(20 * np.log10(peak + 1e-10), 1),
        }
    except Exception as e:
        return {"error": str(e)}


def generate_silence(duration: float, sr: int = 44100, output: str = "silence.wav") -> dict:
    """Génère un fichier de silence."""
    try:
        output = _validated_path(output, must_exist=False)
    except AudioSandboxViolation as exc:
        return {"error": str(exc)}
    if not HAS_LIBROSA:
        return {"error": "soundfile non installé — pip install soundfile numpy"}
    try:
        y = np.zeros(int(duration * sr))
        sf.write(output, y, sr)
        return {"status": "ok", "output": output, "duration_sec": duration, "sample_rate": sr}
    except Exception as e:
        return {"error": str(e)}


def convert_format(path: str, target_format: str = "wav", output: str = None) -> dict:
    """Convertit un fichier audio vers un format cible.
    
    Utilise ffmpeg en fallback.
    """
    try:
        path = _validated_path(path)
        ext_map = {"wav": ".wav", "mp3": ".mp3", "flac": ".flac", "ogg": ".ogg"}
        ext = ext_map.get(target_format, ".wav")
        out_path = _validated_path(
            output or (Path(path).stem + ext), must_exist=False
        )
    except (AudioSandboxViolation, FileNotFoundError) as exc:
        return {"error": str(exc)}

    if HAS_LIBROSA:
        try:
            y, sr = librosa.load(path, sr=None)
            sf.write(out_path, y, sr)
            return {"status": "ok", "output": out_path, "format": target_format}
        except Exception:
            pass

    # Fallback ffmpeg (path/out_path déjà validés et résolus)
    try:
        cmd = ["ffmpeg", "-y", "-i", path, out_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return {"status": "ok", "output": out_path, "format": target_format}
        return {"error": f"ffmpeg: {result.stderr[:200]}"}
    except FileNotFoundError:
        return {"error": "ffmpeg non installé"}
    except Exception as e:
        return {"error": str(e)}


def get_waveform_data(path: str, num_points: int = 256) -> dict:
    """Extrait les données de waveform pour visualisation.
    
    Retourne un tableau de valeurs RMS par segment.
    """
    try:
        path = _validated_path(path)
    except (AudioSandboxViolation, FileNotFoundError) as exc:
        return {"error": str(exc)}
    if not HAS_LIBROSA:
        return {"error": "librosa non installé"}
    try:
        y, sr = librosa.load(path, sr=None)
        rms = librosa.feature.rms(y=y)[0]
        
        # Downsample to num_points
        step = max(1, len(rms) // num_points)
        segments = rms[::step]
        values = [round(float(v), 4) for v in segments[:num_points]]
        
        return {
            "file": path,
            "num_points": len(values),
            "sample_rate": sr,
            "values": values,
        }
    except Exception as e:
        return {"error": str(e)}
