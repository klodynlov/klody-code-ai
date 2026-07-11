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
import os
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
# Chargement du modèle (~6 s à froid) + synthèse. La synthèse tourne HORS-LIGNE
# (cf. _synth_env) : aucun fetch réseau ne peut la faire stagner, donc pas besoin
# des 180 s d'antan — 90 s laissent une marge confortable même sur machine chargée.
_SYNTH_TIMEOUT = 90.0

# Modèle TTS provisionné À PART (jamais téléchargé en cours de synthèse, cf.
# _synth_env). Repo du modèle préféré du personnage « Klody » — cité tel quel dans
# l'indice de remédiation pour que l'échec soit ACTIONNABLE d'un copier-coller.
_MODEL_REPO = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"
_MODEL_REMEDY = (
    f"Le modèle TTS est peut-être incomplet/absent. Provisionne-le UNE fois avec : "
    f"hf download {_MODEL_REPO}"
)

# Marqueurs d'un échec « modèle introuvable hors-ligne » (huggingface_hub / mlx-lm)
# → on requalifie l'échec avec l'indice de remédiation plutôt qu'un stderr opaque.
_MODEL_MISSING_MARKERS: tuple[str, ...] = (
    "hf_hub_offline", "offline mode", "outgoing traffic has been disabled",
    "cannot find the requested files", "not found in the local cache",
    "localentrynotfounderror", "no such file",
)


def _looks_like_model_missing(text: str) -> bool:
    """Vrai si l'erreur dénote un modèle absent du cache local (mode hors-ligne)."""
    low = (text or "").lower()
    return any(marker in low for marker in _MODEL_MISSING_MARKERS)


def _synth_env() -> dict[str, str]:
    """Env de la synthèse : force le mode HORS-LIGNE de HuggingFace.

    speak ne doit JAMAIS télécharger un modèle pendant la synthèse : un fetch HF
    qui traîne fait stagner l'appel jusqu'au timeout (constaté 11/07 : poids
    Qwen3-TTS à moitié téléchargés — 2 shards `.incomplete`, 0 `.safetensors` —
    → chaque speak stallait 180 s → aucun son). Le modèle est provisionné À PART
    (hf download) ; ici on l'UTILISE seulement. Poids présents → chargement local
    rapide ; poids absents → échec IMMÉDIAT (au lieu d'un stall réseau), remonté
    avec un indice actionnable. Même leçon que le gate d'éval nightly (HF_HUB_OFFLINE).
    """
    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    return env

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
            env=_synth_env(),
        )
    except FileNotFoundError:
        return (
            f"speak indisponible : CLI VocalBrain introuvable ({config.VOICE_CLI}). "
            "Installe vocalbrain dans le venv local-suno ou ajuste VOICE_CLI."
        )
    except subprocess.TimeoutExpired:
        return (
            f"speak : synthèse trop longue (> {int(_SYNTH_TIMEOUT)}s) — abandonnée. "
            f"{_MODEL_REMEDY}"
        )

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        logger.warning("speak : vocalbrain generate rc=%s : %s", proc.returncode, tail)
        hint = f" {_MODEL_REMEDY}" if _looks_like_model_missing(tail) else ""
        return f"speak : échec de la synthèse VocalBrain — {tail or 'erreur inconnue'}{hint}"

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
