"""Voix parlée de Klody — synthèse TTS via la CLI VocalBrain, lecture afplay.

Pont LÉGER : n'importe ni mlx_audio ni torch. Il appelle la CLI `vocalbrain`
(installée dans le venv local-suno) en subprocess pour générer un WAV avec la
voix du personnage dédié « Klody » (projet VocalBrain `klody-voice`,
Qwen3-TTS 0.6B multilingue), puis le joue en arrière-plan avec `afplay` —
la boucle ReAct n'attend pas la fin de la lecture.

À ne pas confondre avec mcp__vocalbrain__generer_chanson (chant, pipeline
local-suno complet, minutes) : ici c'est de la PAROLE courte, quelques secondes.
"""
from __future__ import annotations

import logging
import re
import subprocess
import uuid
import wave
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# Au-delà, la synthèse traîne et le résultat n'est plus une « annonce » :
# on tronque à la dernière phrase complète plutôt que de refuser.
_TEXT_CAP = 600
# Chargement du modèle (~6 s à froid) + synthèse : large marge.
_SYNTH_TIMEOUT = 180.0

# Qwen3-TTS attend des noms de langue complets (codec_language_id) — un code
# inconnu bascule en détection auto, moins fiable pour le français.
_LANG_MAP = {
    "fr": "french", "en": "english", "es": "spanish", "de": "german",
    "it": "italian", "pt": "portuguese", "ja": "japanese", "ko": "korean",
    "ru": "russian", "zh": "chinese",
}

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def _truncate(text: str) -> str:
    """Coupe à _TEXT_CAP en respectant si possible une fin de phrase."""
    if len(text) <= _TEXT_CAP:
        return text
    cut = text[:_TEXT_CAP]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > _TEXT_CAP // 2:
            return cut[: idx + 1]
    return cut + "…"


def _segment_sentences(text: str) -> str:
    """Une phrase par ligne — Qwen3-TTS segmente sur '\\n' (split_pattern).

    Un texte multi-phrases envoyé d'un bloc fait dérailler le 0.6B Base : il
    n'émet jamais l'EOS et sature max_tokens (vécu : 103 caractères → WAV de
    163,8 s). Phrase par phrase, l'EOS est fiable.
    """
    return "\n".join(s.strip() for s in _SENTENCE_END.split(text) if s.strip())


def _wav_duration(path: Path) -> float:
    """Durée d'un WAV en secondes — 0.0 si illisible (jamais bloquant)."""
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / rate if rate else 0.0
    except Exception:
        return 0.0


def _play(path: Path) -> bool:
    """Lance la lecture audio en arrière-plan. True si le player a démarré."""
    try:
        subprocess.Popen(
            [config.VOICE_PLAY_CMD, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception as exc:
        logger.warning("speak : lecture audio impossible (%s) : %s",
                       config.VOICE_PLAY_CMD, exc)
        return False


def speak(text: str, language: str = "fr") -> str:
    """Dit `text` à voix haute avec la voix Klody. Retourne un compte rendu LLM.

    Synthèse synchrone (quelques secondes), lecture asynchrone (afplay détaché).
    Toutes les erreurs reviennent en message lisible — jamais d'exception.
    """
    text = (text or "").strip()
    if not text:
        return "speak : texte vide — rien à dire."
    text = _truncate(text)
    lang = (language or "fr").strip().lower() or "fr"
    lang = _LANG_MAP.get(lang, lang)

    # segment-id imposé → on retrouve le WAV par glob, sans parser stdout.
    seg = uuid.uuid4().hex[:8]
    cmd = [
        config.VOICE_CLI, "generate",
        "-p", config.VOICE_PROJECT_ID,
        "-c", config.VOICE_CHARACTER,
        "-t", _segment_sentences(text),
        "--lang", lang,
        "--segment-id", seg,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_SYNTH_TIMEOUT,
        )
    except FileNotFoundError:
        return (
            f"speak indisponible : CLI VocalBrain introuvable ({config.VOICE_CLI}). "
            "Installe vocalbrain dans le venv local-suno ou ajuste VOICE_CLI."
        )
    except subprocess.TimeoutExpired:
        return f"speak : synthèse trop longue (> {int(_SYNTH_TIMEOUT)}s) — abandonnée."

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        logger.warning("speak : vocalbrain generate rc=%s : %s", proc.returncode, tail)
        return f"speak : échec de la synthèse VocalBrain — {tail or 'erreur inconnue'}"

    audio_root = config.VOICE_AUDIO_DIR / config.VOICE_PROJECT_ID
    wavs = sorted(audio_root.glob(f"*/{seg}_take*.wav"))
    if not wavs:
        logger.error("speak : WAV introuvable pour segment %s sous %s", seg, audio_root)
        return "speak : synthèse terminée mais fichier audio introuvable."

    wav = wavs[0]
    duration = _wav_duration(wav)
    played = _play(wav)

    preview = text if len(text) <= 120 else text[:117] + "…"
    status = "joué sur les haut-parleurs" if played else f"généré (lecture impossible : {wav})"
    dur_txt = f" ({duration:.1f}s)" if duration else ""
    return f"🔊 Dit à voix haute{dur_txt}, {status} : « {preview} »"
