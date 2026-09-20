"""Traduction des erreurs du backend LLM en messages lisibles, avec remède.

Vécu le 2026-09-20 : KlodyAI affichait, en rouge et sans rien d'autre,

    Error code: 503 - {'error': "RAM insuffisante pour brain (~44 Go) : libre
    80/80 Go virtuel, RAM réelle 46 Go (plancher 12), rien d'évinçable de plus"}

C'est la représentation `str()` de l'exception du SDK OpenAI, renvoyée telle
quelle par `api/server.py` (`queue.put({"type": "error", "content": str(e)})`).
Trois défauts pour l'utilisateur : la forme (un dict Python dans une bulle de
chat), l'absence de remède (que faire ?), et l'ambiguïté du chiffre « libre
80/80 » qui semble dire que tout va bien — alors que le budget du gateway est
VIRTUEL (cf. CLAUDE.md, « `libre` du gateway est un budget VIRTUEL ») et que
c'est la RAM RÉELLE de la machine qui manquait (46 Go < 44 + plancher 12).

Ce module est le SEUL endroit qui formule ces messages : la CLI (`main.py`) et
l'API WebSocket (`api/server.py`) l'appellent tous deux, pour que les deux
surfaces racontent la même chose. Il ne lève jamais et ne dépend pas de Rich.
"""
from __future__ import annotations

import logging
from typing import Any

import config
from openai import APIConnectionError, APIStatusError, APITimeoutError

__all__ = [
    "attente_reessai_503",
    "detail_http",
    "est_503",
    "expliquer_erreur_llm",
    "message_reessai_503",
    "pour_journal",
    "resume_exception",
]

logger = logging.getLogger(__name__)

# Marqueurs, en minuscules, d'un 503 « mémoire » du gateway Klody Core
# (`klody-core/gateway/sysmem.py`). Le gateway nomme la cause dans son message ;
# on s'en sert pour choisir le remède, jamais pour décider du réessai (tout 503
# est transitoire par définition, RFC 9110 §15.6.4).
_MARQUEURS_RAM = ("ram insuffisante", "ram réelle", "mémoire", "memoire", "évinçable")


def detail_http(exc: BaseException) -> str:
    """Le message porté par le corps d'une réponse d'erreur HTTP, sans la
    ferraille `Error code: NNN - {...}` du SDK. Chaîne vide si rien d'exploitable."""
    body: Any = getattr(exc, "body", None)
    if isinstance(body, dict):
        for cle in ("error", "message", "detail"):
            val = body.get(cle)
            if isinstance(val, dict):
                val = val.get("message")
            if isinstance(val, str) and val.strip():
                return val.strip()
    if isinstance(body, str) and body.strip():
        return body.strip()
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return str(exc).strip()


def est_503(exc: BaseException) -> bool:
    """L'exception est-elle une réponse HTTP 503 du backend ?"""
    return isinstance(exc, APIStatusError) and getattr(exc, "status_code", None) == 503


def attente_reessai_503(exc: BaseException, essai: int) -> float | None:
    """Durée à attendre avant de REJOUER l'appel, ou None s'il ne faut pas.

    Une seule décision pour les deux chemins de streaming (`LLMClient.stream_chat`
    côté CLI, `api/streaming.py::stream_api` côté WebSocket) : un 503 est rendu
    AVANT toute génération (le gateway refuse de charger le modèle), donc le
    rejouer ne coûte rien, et il est transitoire (garde mémoire qui suspend des
    processus, `vm_stat` qui sous-estime la RAM juste après un chargement).
    Backoff ATTENTE × 2^essai, borné à ESSAIS essais — cf. config.LLM_503_*.
    Les réglages sont lus À L'APPEL, pour rester surchargeables par l'env et
    les tests.
    """
    if not est_503(exc):
        return None
    if essai >= max(0, int(config.LLM_503_ESSAIS)):
        return None
    return float(config.LLM_503_ATTENTE_S) * (2 ** essai)


def message_reessai_503(exc: BaseException, essai: int, attente: float) -> str:
    """Ligne de statut, identique en CLI et dans l'UI, pendant l'attente."""
    return (
        f"Backend indisponible ({detail_http(exc)}) — nouvel essai "
        f"{essai + 1}/{max(0, int(config.LLM_503_ESSAIS))} dans {attente:.0f} s…"
    )


def pour_journal(valeur: object, max_len: int = 120) -> str:
    """Rend une valeur d'origine EXTERNE (nom de modèle choisi par l'UI, détail
    d'une réponse HTTP) sûre pour une ligne de log : retours à la ligne et
    caractères de contrôle retirés, longueur bornée. CodeQL `py/log-injection`
    sur `self.model` — le sélecteur de modèle de l'UI arrive par WebSocket."""
    texte = "".join(c if c.isprintable() else " " for c in str(valeur))
    return texte[:max_len]


def resume_exception(exc: BaseException) -> str:
    """Résumé court (≤ ~40 car.) pour un en-tête d'UI ou un log d'une ligne.

    Ex. « HTTP 503 », « connexion refusée », « timeout », « ValueError ».
    """
    if isinstance(exc, APITimeoutError):
        return "timeout"
    if isinstance(exc, APIConnectionError):
        return "connexion refusée"
    if isinstance(exc, APIStatusError):
        return f"HTTP {getattr(exc, 'status_code', '?')}"
    return type(exc).__name__


def _url_admin(base_url: str) -> str:
    """`http://localhost:8090/v1` → `http://localhost:8090/admin/status`."""
    racine = base_url.rstrip("/")
    if racine.endswith("/v1"):
        racine = racine[: -len("/v1")]
    return f"{racine}/admin/status"


def expliquer_erreur_llm(exc: BaseException, llm: Any = None) -> str:
    """Message destiné à l'UTILISATEUR pour une exception levée par un appel LLM.

    `llm` est un `LLMClient` (ou n'importe quel objet portant `model`,
    `_backend`, `_base_url`) ; absent, on lit la config. Toujours une chaîne
    non vide, jamais d'exception : ce helper vit sur le chemin d'erreur, il ne
    doit pas en ajouter une.
    """
    try:
        return _expliquer(exc, llm)
    except Exception as err:  # pragma: no cover - défense du chemin d'erreur
        logger.debug("expliquer_erreur_llm KO (%s) → str(exc)", err)
        return str(exc) or type(exc).__name__


def _expliquer(exc: BaseException, llm: Any) -> str:
    modele = str(getattr(llm, "model", None) or config.LLM_MODEL)
    backend = str(getattr(llm, "_backend", None) or config.BACKEND)
    base_url = str(getattr(llm, "_base_url", None) or config.LLM_BASE_URL)
    cible = "le gateway Klody Core" if backend == "mlx" else "Ollama"

    # Ordre : Timeout AVANT Connection (APITimeoutError hérite d'APIConnectionError).
    if isinstance(exc, APITimeoutError):
        lecture = getattr(config.LLM_HTTP_TIMEOUT, "read", None)
        delai = f" ({lecture:.0f} s sans un seul token)" if isinstance(lecture, (int, float)) else ""
        return (
            f"Le modèle « {modele} » n'a pas répondu dans le délai{delai}. "
            f"{cible[0].upper()}{cible[1:]} charge peut-être un modèle (brain ≈ 44 Go, "
            "coder ≈ 30 Go) : renvoie le message dans quelques secondes."
        )

    if isinstance(exc, APIConnectionError):
        remede = (
            f"le démarrer, puis vérifier sa mémoire : {_url_admin(base_url)}"
            if backend == "mlx"
            else "lancer `ollama serve`"
        )
        return f"Impossible de joindre {cible} ({base_url}) — {remede}."

    if isinstance(exc, APIStatusError):
        statut = getattr(exc, "status_code", None)
        detail = detail_http(exc)
        if statut == 503:
            if any(m in detail.lower() for m in _MARQUEURS_RAM):
                return (
                    f"Pas assez de mémoire pour charger le modèle « {modele} » : "
                    f"{detail}. C'est la RAM réelle de la machine qui manque, pas le "
                    "budget du gateway — ferme des applications gourmandes (navigateur, "
                    "ChatGPT, DAW…) ou attends que la garde mémoire en suspende, puis "
                    f"renvoie le message. Contrôle : {_url_admin(base_url)}"
                )
            return (
                f"{cible[0].upper()}{cible[1:]} a refusé la requête (503, indisponible "
                f"temporairement) : {detail}. Renvoie le message dans quelques secondes."
            )
        if statut == 404:
            conseil = (
                " En mode gateway, MLX_MODEL doit être un ALIAS du registre "
                "(brain, coder) — jamais un id HuggingFace (incident du 2026-07-03)."
                if backend == "mlx"
                else ""
            )
            return f"Le modèle « {modele} » est inconnu de {cible} (404) : {detail}.{conseil}"
        return f"{cible[0].upper()}{cible[1:]} a rendu une erreur HTTP {statut} : {detail}"

    texte = str(exc).strip()
    return texte or type(exc).__name__
