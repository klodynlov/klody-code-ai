"""Comptage de tokens — vrai tokenizer du modèle si dispo, sinon heuristique.

But : fiabiliser le budget de contexte (cf. agent/memory) et la jauge UI. Le
`chars // 4` historique SOUS-estime systématiquement le code et le JSON (≈3
chars/token) — or Klody lit beaucoup de code et de gros tool results. La fenêtre
se croyait donc moins remplie qu'en réalité → le prompt réel dépassait côté
serveur (saturation ~32k/32.8k, génération bloquée).

Contraintes de conception :
- ZÉRO dépendance dure. `transformers` est transitif (tiré par mlx_lm) et absent
  en CI/Ollama. Tout échec — import KO, modèle non-HF (ex. "qwen2.5-coder:32b" en
  Ollama), tokenizer pas dans le cache HF — retombe silencieusement sur
  l'heuristique. `local_files_only=True` ⇒ JAMAIS d'accès réseau, donc jamais de
  blocage au chargement.
- Chargé une seule fois (lazy + cache module). Le tokenizer du modèle généraliste
  (config.LLM_MODEL) sert de référence ; coder et brain partagent la même famille
  Qwen3 (même vocabulaire) — l'écart de budget est négligeable.
"""
from __future__ import annotations

import logging

import config

logger = logging.getLogger(__name__)

# ~4 chars/token : repli alignant le comportement historique (chars // 4).
_HEURISTIC_DIVISOR = 4

_tokenizer = None       # objet tokenizer HF, ou None si indisponible
_tried = False          # le chargement paresseux a-t-il déjà été tenté ?


def _load_tokenizer():
    """Charge (une fois) le tokenizer exact du modèle actif, ou None."""
    global _tokenizer, _tried
    if _tried:
        return _tokenizer
    _tried = True
    model_id = config.LLM_MODEL
    try:
        from transformers import AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained(model_id, local_files_only=True)
        logger.info("[tokens] tokenizer exact chargé : %s", model_id)
    except Exception as exc:  # transformers absent, modèle non-HF, hors cache…
        logger.info(
            "[tokens] tokenizer exact indisponible (%s) → heuristique ~chars/%d",
            type(exc).__name__, _HEURISTIC_DIVISOR,
        )
        _tokenizer = None
    return _tokenizer


def count_tokens(text: str) -> int:
    """Nombre de tokens de `text` : exact si le tokenizer du modèle est chargé,
    sinon repli heuristique (~chars/4, comportement historique)."""
    if not text:
        return 0
    tok = _load_tokenizer()
    if tok is not None:
        try:
            return len(tok.encode(text))
        except Exception:  # un encodage qui échoue ne doit jamais casser le budget
            pass
    return len(text) // _HEURISTIC_DIVISOR


def tokenizer_is_exact() -> bool:
    """True si le comptage utilise le vrai tokenizer du modèle (pas l'heuristique)."""
    return _load_tokenizer() is not None
