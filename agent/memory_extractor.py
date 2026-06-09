"""
Extraction automatique de mémoire longue terme depuis une conversation.

Après chaque session, analyse les messages et extrait les faits importants
(préférences, projets, profil utilisateur) via un appel LLM léger.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from config import (
    LLM_HTTP_TIMEOUT,
    LLM_MAX_RETRIES,
    MODEL_FALLBACK,
    OLLAMA_API_KEY,
    OLLAMA_BASE_URL,
)
from openai import OpenAI

if TYPE_CHECKING:
    from agent.long_term_memory import LongTermMemory

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
Analyse cette conversation entre un utilisateur et Klody AI.
Extrais UNIQUEMENT les faits importants et durables à mémoriser pour les sessions futures.

Critères INCLURE :
- Préférences de l'utilisateur (style de code, outils, langages favoris)
- Projets en cours (nom, stack, objectif, état)
- Profil utilisateur (expertise, rôle, contraintes)
- Décisions techniques importantes prises pendant la session

Critères EXCLURE :
- Questions ponctuelles sans portée générale
- Détails de code spécifiques à un fichier
- Informations déjà triviales ou évidentes

Réponds UNIQUEMENT avec du JSON valide, aucun autre texte :
[{"key": "snake_case_court", "content": "une phrase concise et utile", "category": "user|project|preference|context"}]

Si rien d'important à retenir : []
"""

# Utilise le modèle fallback (plus rapide) pour l'extraction
_MIN_USER_MESSAGES = 2  # Ne pas extraire pour les sessions trop courtes
_MID_SESSION_INTERVAL = 8  # Extraire tous les N messages user mid-session
_last_mid_extraction_count: int = 0

_VALID_CATEGORIES = ("user", "project", "preference", "context")


def _coerce_text(value: object) -> str:
    """Coerce en texte propre une valeur renvoyée par le LLM.

    Le modèle renvoie parfois une liste (`["Three.js", "Blender"]`) ou un objet
    là où l'on attend une string. `fact.get("key", "").strip()` plantait alors
    sur `'list' object has no attribute 'strip'`, et comme l'appelant attrape
    en bloc, c'est TOUTE la fournée de faits qui était perdue. On normalise
    sans jamais lever.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return ", ".join(_coerce_text(v) for v in value if v is not None).strip()
    if value is None:
        return ""
    return str(value).strip()


def _save_facts(facts: list, lt_memory: LongTermMemory, log_prefix: str) -> list[dict]:
    """Valide, normalise et persiste les faits extraits.

    Robuste aux faits malformés : un fait non-dict ou aux valeurs non-string
    est normalisé ou ignoré individuellement, sans faire échouer les autres.
    """
    saved: list[dict] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        key = _coerce_text(fact.get("key"))
        content = _coerce_text(fact.get("content"))
        category = fact.get("category", "context")
        if category not in _VALID_CATEGORIES:
            category = "context"
        if key and content:
            result = lt_memory.remember(key, content, category)
            logger.info("%s %s", log_prefix, result)
            saved.append({"key": key, "content": content, "category": category})
    return saved


def extract_mid_session(
    messages: list[dict],
    lt_memory: LongTermMemory,
    model: str = MODEL_FALLBACK,
) -> list[dict]:
    """Extraction proactive mid-session : tourne toutes les _MID_SESSION_INTERVAL
    requêtes utilisateur pour capturer les préférences en temps réel.

    Plus légère que extract_and_save : ne regarde que les 10 derniers messages.
    """
    global _last_mid_extraction_count

    user_count = sum(1 for m in messages if m.get("role") == "user" and m.get("content"))
    if user_count < _MIN_USER_MESSAGES:
        return []
    if user_count - _last_mid_extraction_count < _MID_SESSION_INTERVAL:
        return []

    _last_mid_extraction_count = user_count

    recent = [
        m for m in messages[-15:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if len(recent) < 3:
        return []

    convo_lines = []
    for m in recent:
        role = "Utilisateur" if m["role"] == "user" else "Klody"
        convo_lines.append(f"{role}: {str(m['content'])[:300]}")
    conversation = "\n".join(convo_lines)

    try:
        client = OpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key=OLLAMA_API_KEY,
            timeout=LLM_HTTP_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _EXTRACTION_PROMPT},
                {"role": "user", "content": f"Conversation récente à analyser :\n\n{conversation}"},
            ],
            temperature=0.1,
            stream=False,
        )
        raw = response.choices[0].message.content or "[]"
    except Exception as e:
        logger.warning("[Extractor-mid] Erreur LLM : %s", e)
        return []

    facts = _parse_json_facts(raw)
    saved = _save_facts(facts, lt_memory, "[Extractor-mid]")
    if saved:
        logger.info("[Extractor-mid] %d fait(s) extraits mid-session", len(saved))
    return saved


def extract_and_save(
    messages: list[dict],
    lt_memory: LongTermMemory,
    model: str = MODEL_FALLBACK,
) -> list[dict]:
    """
    Extrait les faits importants d'une liste de messages et les sauvegarde.

    Args:
        messages: Messages de la session (tous rôles confondus)
        lt_memory: Instance LongTermMemory à mettre à jour
        model: Modèle LLM à utiliser (défaut: MODEL_FALLBACK)

    Returns:
        Liste des faits extraits et sauvegardés
    """
    # Filtrer : user + assistant uniquement, avec contenu
    relevant = [
        m for m in messages
        if m.get("role") in ("user", "assistant")
        and m.get("content")
        and not isinstance(m.get("content"), type(None))
    ]

    user_msgs = [m for m in relevant if m["role"] == "user"]
    if len(user_msgs) < _MIN_USER_MESSAGES:
        logger.debug("[Extractor] Session trop courte (%d msgs user) — skip", len(user_msgs))
        return []

    # Construire la conversation à analyser (limitée aux 30 derniers messages)
    convo_lines = []
    for m in relevant[-30:]:
        role = "Utilisateur" if m["role"] == "user" else "Klody"
        content = str(m["content"])[:400]  # Tronquer chaque message
        convo_lines.append(f"{role}: {content}")
    conversation = "\n".join(convo_lines)

    try:
        client = OpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key=OLLAMA_API_KEY,
            timeout=LLM_HTTP_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _EXTRACTION_PROMPT},
                {"role": "user", "content": f"Conversation à analyser :\n\n{conversation}"},
            ],
            temperature=0.1,
            stream=False,
        )
        raw = response.choices[0].message.content or "[]"
        logger.debug("[Extractor] Réponse brute : %s", raw[:200])
    except Exception as e:
        logger.warning("[Extractor] Erreur LLM : %s", e)
        return []

    # Parser le JSON — robuste aux réponses avec du texte autour
    facts = _parse_json_facts(raw)
    if not facts:
        logger.debug("[Extractor] Aucun fait extrait")
        return []

    saved = _save_facts(facts, lt_memory, "[Extractor]")
    logger.info("[Extractor] %d fait(s) sauvegardé(s)", len(saved))
    return saved


def _parse_json_facts(raw: str) -> list[dict]:
    """Parse le JSON des faits extraits, robuste aux réponses imparfaites."""
    raw = raw.strip()

    # Cas direct
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Extraire le JSON entre [ ... ]
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # Array tronqué (réponse coupée en plein milieu, sans `]` final) :
    # on ferme proprement après le dernier objet complet `}`.
    last_obj = raw.rfind("}")
    if start != -1 and last_obj > start:
        snippet = raw[start:last_obj + 1].rstrip().rstrip(",")
        try:
            data = json.loads(snippet + "]")
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    logger.warning("[Extractor] JSON non parseable : %s", raw[:200])
    return []
