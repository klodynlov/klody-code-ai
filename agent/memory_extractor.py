"""
Extraction automatique de mémoire longue terme depuis une conversation.

Après chaque session, analyse les messages et extrait les faits importants
(préférences, projets, profil utilisateur) via un appel LLM léger.

⚠️ Vécu le 2026-09-27 : l'extraction était MORTE en production depuis le
2026-07-18 02:20, sans que rien ne le signale. Le client visait `OLLAMA_BASE_URL`
avec `MODEL_FALLBACK` (`mistral:latest`, un nom Ollama) QUEL QUE SOIT `BACKEND` ;
or en `BACKEND=mlx` — le nominal — Ollama n'est même pas installé (:11434
fermé). Relevé dans `logs/agent.log` : 164 « Erreur LLM » (127 en fin de
session, 37 en mi-session), toutes en WARNING, que personne ne lisait. D'où :

- la cible suit `BACKEND` (`_cible()`), comme la boucle principale ;
- chaque échec NOMME la cible et le remède, et au-delà de
  `SEUIL_HORS_SERVICE` échecs consécutifs le log passe en ERROR ;
- `etat_extraction()` distingue « jamais tentée », « opérationnelle » et
  « en échec » — la CLI et `/api/status` le lisent.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import config
from openai import OpenAI

from agent.erreurs_llm import expliquer_erreur_llm, pour_journal
from agent.journal_client import APP as _APP_JOURNAL

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

_MIN_USER_MESSAGES = 2  # Ne pas extraire pour les sessions trop courtes
_MID_SESSION_INTERVAL = 8  # Extraire tous les N messages user mid-session
_last_mid_extraction_count: int = 0


def _cible() -> tuple[str, str, str]:
    """(base_url, api_key, modèle) de l'extraction — la cible suit `BACKEND`.

    Les URL et clés sont celles de la boucle principale (`config.LLM_*`, résolues
    UNE fois dans config.py selon `BACKEND`) : l'extraction ne décide pas seule
    de parler à un autre serveur que l'agent.

    Modèle, par ordre de priorité :
    - `MEMORY_EXTRACTOR_MODEL` s'il est posé (une entrée dédiée du registre, un
      jour — jamais une surcharge de `brain`) ;
    - en `mlx` : `LLM_MODEL`, l'alias du gateway (`brain`). ⚠️ JAMAIS
      `MODEL_FALLBACK` : c'est un nom OLLAMA, que le gateway rejette en 404
      « modèle inconnu » — même piège que `LLMClient._fallback_model_utilisable`
      (2026-09-20) ;
    - en `ollama` : `MODEL_FALLBACK`, le choix historique (un modèle plus léger
      que le principal), sinon `LLM_MODEL`.

    Coût mesuré sur `brain` le 2026-09-27 (conversation maximale, 30 messages
    tronqués à 400 caractères ⇒ 2 775 tokens de prompt, 161 générés) : 3,0 s.
    C'est le chiffre réel : la conversation change d'un appel à l'autre, et le
    cache du MoE ne sert qu'un préfixe EXACT (#270) — les 2,1 s d'un rejeu à
    l'identique ne se produisent pas en usage. Le cache de préfixe du chat
    (69 schémas d'outils) SURVIT à une extraction : 0,18 s avant, 0,18 s après
    — le worker garde plusieurs préfixes (`--prompt-cache-bytes 8G`). Un tour
    de chat lancé PENDANT une extraction paie +0,56 s, pas 3 s : le worker sert
    en parallèle (`--decode-concurrency 8`).

    ⚠️ Coût INDIRECT, non mesuré : un fait nouveau change `lt_section` du prompt
    système (`Orchestrator`, `LongTermMemory.format_for_prompt`), donc le tour
    suivant rate le cache depuis le token 0. Aujourd'hui masqué — skills et
    retrieval, placés AVANT, varient déjà sur 91 % des paires de messages
    (#270) — mais il deviendra visible le jour où ils sortiront du prompt
    système. Le juge est `grep -F '[cache]' logs/agent.log`.
    """
    if config.MEMORY_EXTRACTOR_MODEL:
        modele = config.MEMORY_EXTRACTOR_MODEL
    elif config.BACKEND == "mlx":
        modele = config.LLM_MODEL
    else:
        modele = config.MODEL_FALLBACK or config.LLM_MODEL
    return config.LLM_BASE_URL, config.LLM_API_KEY, modele


# Identité vue par le journal d'usage du gateway (klody-core
# `docs/JOURNAL-USAGE-SPEC.md` §3.5-3.6). `X-Klody-Source: system` est
# indispensable : sans lui, `app=klody-ai` est classé `user`, et chaque
# extraction — une par message WebSocket — deviendrait un « tour utilisateur »
# pour le miner d'habitudes, qui fabrique alors de fausses habitudes (le bug que
# l'amendement du 2026-09-22 a corrigé ailleurs).
_EN_TETES = {"X-Klody-App": _APP_JOURNAL, "X-Klody-Source": "system"}

# Client OpenAI PARTAGÉ du module, créé paresseusement et jamais fermé (durée de
# vie = process). Avant : un client (pool httpx) NEUF par appel, jamais fermé —
# or l'extraction tourne après CHAQUE message WebSocket, un des sites de la
# fuite ~5-6 Gio/jour de com.klody.api (audit 2026-08-09). Les clients OpenAI
# sont thread-safe : le partage entre threads d'extraction est sûr.
#
# La clé (classe, base_url, api_key) voyage avec le client, qui est reconstruit
# si elle a changé. En production aucun des trois ne change ⇒ un seul client.
# Sous les tests, `@patch("agent.memory_extractor.OpenAI")` pose une classe
# fraîche par test et `monkeypatch` peut changer la config — sans cette garde,
# le client d'un test fuirait dans le suivant, pointé sur la cible d'avant.
_client_partage: tuple[tuple[type, str, str], OpenAI] | None = None
_verrou_client = threading.Lock()


def _client_llm() -> OpenAI:
    """Client OpenAI du module, RÉUTILISÉ entre les appels d'extraction."""
    global _client_partage
    base_url, api_key, _ = _cible()
    cle = (OpenAI, base_url, api_key)
    with _verrou_client:
        if _client_partage is None or _client_partage[0] != cle:
            ancien = _client_partage[1] if _client_partage is not None else None
            _client_partage = (
                cle,
                OpenAI(
                    base_url=base_url,
                    api_key=api_key,
                    timeout=config.LLM_HTTP_TIMEOUT,
                    max_retries=config.LLM_MAX_RETRIES,
                    default_headers=dict(_EN_TETES),
                ),
            )
            # Le remplacé est FERMÉ (cf. LLMClient.set_session) : n'arrive que si
            # la cible change, donc jamais en production — mais un pool orphelin
            # par changement serait la fuite de 2026-08-09 par une autre porte.
            if ancien is not None:
                try:
                    ancien.close()
                except Exception as exc:  # pragma: no cover - défense
                    logger.debug("[Extractor] fermeture de l'ancien client KO : %s", exc)
        return _client_partage[1]


# --------------------------------------------------------------------------- #
# État de l'extraction — « jamais tentée » n'est pas « ça marche »             #
# --------------------------------------------------------------------------- #

# Au-delà, un échec n'est plus un incident mais une panne : le log passe en
# ERROR. Trois, parce qu'un 503 RAM transitoire (réessayé côté boucle principale,
# pas ici) peut en rendre un ou deux d'affilée sans que rien ne soit cassé.
SEUIL_HORS_SERVICE = 3

NON_TENTEE = "non_tentee"
OPERATIONNELLE = "operationnelle"
EN_ECHEC = "en_echec"

_verrou_etat = threading.Lock()
_etat: dict[str, Any] = {
    "echecs_consecutifs": 0,
    "derniere_reussite": None,
    "premier_echec": None,
    "derniere_erreur": None,
}


def _maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _noter_reussite() -> None:
    with _verrou_etat:
        echecs = _etat["echecs_consecutifs"]
        _etat.update(
            echecs_consecutifs=0,
            derniere_reussite=_maintenant(),
            premier_echec=None,
            derniere_erreur=None,
        )
    if echecs >= SEUIL_HORS_SERVICE:
        logger.warning("[Extractor] extraction RÉTABLIE après %d échec(s) consécutif(s)", echecs)


def _noter_echec(prefixe: str, exc: BaseException, modele: str) -> None:
    """Journalise un échec en NOMMANT la cible et le remède, et l'escalade.

    Le message d'origine était « Erreur LLM : Connection error. » — sans URL ni
    modèle, il ne disait ni quoi ni où. `expliquer_erreur_llm` est la même
    formulation que la CLI et l'UI : « Impossible de joindre le gateway Klody
    Core (http://localhost:8090/v1) — le démarrer… ».
    """
    base_url, _, _ = _cible()
    cible = SimpleNamespace(model=modele, _backend=config.BACKEND, _base_url=base_url)
    cause = pour_journal(expliquer_erreur_llm(exc, cible), max_len=400)
    with _verrou_etat:
        _etat["echecs_consecutifs"] += 1
        echecs = _etat["echecs_consecutifs"]
        if _etat["premier_echec"] is None:
            _etat["premier_echec"] = _maintenant()
        _etat["derniere_erreur"] = cause
        depuis = _etat["premier_echec"]
        reussite = _etat["derniere_reussite"]
    if echecs < SEUIL_HORS_SERVICE:
        logger.warning("%s Erreur LLM (%d/%d) : %s", prefixe, echecs, SEUIL_HORS_SERVICE, cause)
        return
    logger.error(
        "%s HORS SERVICE — %d échecs consécutifs depuis %s (dernière réussite de ce "
        "processus : %s). L'extraction automatique de faits ne mémorise plus rien. "
        "Cause : %s",
        prefixe, echecs, depuis, reussite or "aucune", cause,
    )


def etat_extraction() -> dict[str, Any]:
    """État de l'extraction dans CE processus, pour la CLI et `/api/status`.

    Trois verdicts, pas deux : `non_tentee` (aucun appel LLM depuis le démarrage
    — session trop courte, ou API fraîchement relancée) ne doit pas se lire
    « tout va bien », et `en_echec` porte le nombre d'échecs consécutifs et la
    cause. Ne lève jamais.
    """
    base_url, _, modele = _cible()
    with _verrou_etat:
        etat = dict(_etat)
    if etat["echecs_consecutifs"]:
        verdict = EN_ECHEC
    elif etat["derniere_reussite"]:
        verdict = OPERATIONNELLE
    else:
        verdict = NON_TENTEE
    return {
        "verdict": verdict,
        "hors_service": etat["echecs_consecutifs"] >= SEUIL_HORS_SERVICE,
        "cible": {"backend": config.BACKEND, "base_url": base_url, "modele": modele},
        **etat,
    }


def _appeler_llm(
    prefixe: str, consigne: str, model: str | None, session_id: str | None
) -> str | None:
    """Un appel d'extraction. Rend le texte brut, ou None si l'appel a échoué
    (l'échec est alors déjà journalisé et compté)."""
    modele = model or _cible()[2]
    # Session posée PAR REQUÊTE : le client est partagé entre toutes les
    # sessions, ses en-têtes par défaut ne peuvent porter que l'identité fixe.
    extra = {"X-Klody-Session": session_id} if session_id else None
    try:
        response = _client_llm().chat.completions.create(
            model=modele,
            messages=[
                {"role": "system", "content": _EXTRACTION_PROMPT},
                {"role": "user", "content": consigne},
            ],
            temperature=0.1,
            stream=False,
            extra_headers=extra,
        )
        raw = response.choices[0].message.content or "[]"
    except Exception as e:
        _noter_echec(prefixe, e, modele)
        return None
    _noter_reussite()
    return raw

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
    model: str | None = None,
    session_id: str | None = None,
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

    raw = _appeler_llm(
        "[Extractor-mid]",
        f"Conversation récente à analyser :\n\n{conversation}",
        model,
        session_id,
    )
    if raw is None:
        return []

    facts = _parse_json_facts(raw)
    saved = _save_facts(facts, lt_memory, "[Extractor-mid]")
    if saved:
        logger.info("[Extractor-mid] %d fait(s) extraits mid-session", len(saved))
    return saved


def extract_and_save(
    messages: list[dict],
    lt_memory: LongTermMemory,
    model: str | None = None,
    session_id: str | None = None,
) -> list[dict]:
    """
    Extrait les faits importants d'une liste de messages et les sauvegarde.

    Args:
        messages: Messages de la session (tous rôles confondus)
        lt_memory: Instance LongTermMemory à mettre à jour
        model: Modèle LLM à utiliser (défaut : celui de `_cible()`, selon BACKEND)
        session_id: Session à attribuer dans le journal d'usage du gateway

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

    raw = _appeler_llm(
        "[Extractor]", f"Conversation à analyser :\n\n{conversation}", model, session_id
    )
    if raw is None:
        return []
    logger.debug("[Extractor] Réponse brute : %s", raw[:200])

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
