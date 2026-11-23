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
import time
from typing import Any

import config
from openai import APIConnectionError, APIStatusError, APITimeoutError

__all__ = [
    "attente_reessai_503",
    "detail_http",
    "est_503",
    "expliquer_erreur_llm",
    "message_reessai_503",
    "message_repli_503",
    "modele_repli_503",
    "noter_repli_503",
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


def _est_503_ram(exc: BaseException) -> bool:
    """Un 503 dont le gateway attribue lui-même la cause à la mémoire."""
    return est_503(exc) and any(m in detail_http(exc).lower() for m in _MARQUEURS_RAM)


def modele_repli_503(exc: BaseException, llm: Any = None) -> str | None:
    """Alias sur lequel REJOUER le tour quand le 503 mémoire persiste, ou None.

    Mesuré le 2026-11-15 (`~/klody-core/state/journal.db`, 45 jours) : 2 852
    requêtes utilisateur `klody-ai` réussies, 46 refusées en 503, 2 injoignables
    — le 503 « RAM insuffisante pour brain » est le SEUL mode d'échec mesuré du
    chat. Les 5 refus du jour (06:44–06:46) : « RAM insuffisante pour brain
    (~52 Go) : libre 55/95 Go virtuel, RAM réelle 54 Go (plancher 12), rien
    d'évinçable de plus ». Le réessai (5 s puis 10 s) n'a pas suffi, la mémoire
    est restée prise plusieurs minutes. Or `coder` (même famille
    Qwen3.6-35B-A3B, 4 bits, ~40 Go) tenait : 54 − 40 = 14 ≥ plancher 12.

    Le repli ne vaut que pour l'appel en cours : l'appelant passe l'alias au
    rejeu et ne touche PAS `llm.model`. Muter le modèle de session est le piège
    de `MODEL_FALLBACK` (2026-09-20) : une panne passagère empoisonnait tout le
    reste de la session.

    Conditions, toutes nécessaires :
    - `config.LLM_503_REPLI` ;
    - 503 dont le message nomme la mémoire — un 503 d'une autre nature (worker
      qui démarre, gateway qui s'arrête) ne serait pas mieux servi par `coder` ;
    - `BACKEND=mlx` : en mode ollama il n'y a ni gateway ni alias `coder` ;
    - le modèle refusé est le généraliste (`config.LLM_MODEL`) et un modèle de
      code DISTINCT est configuré — jamais de repli coder → coder, ni depuis un
      modèle tiers choisi dans le sélecteur ;
    - même point d'entrée pour les deux alias (mode gateway). En mode autonome
      (`:8080`/`:8081`, deux serveurs) le rejeu partirait sur le mauvais port.
    """
    if not config.LLM_503_REPLI or not _est_503_ram(exc):
        return None
    backend = str(getattr(llm, "_backend", None) or config.BACKEND)
    if backend != "mlx":
        return None
    modele = str(getattr(llm, "model", None) or config.LLM_MODEL)
    repli = str(config.CODE_MODEL or "")
    if not repli or repli == modele or modele != config.LLM_MODEL:
        return None
    base_url = str(getattr(llm, "_base_url", None) or config.LLM_BASE_URL)
    if base_url.rstrip("/") != str(config.CODE_BASE_URL).rstrip("/"):
        return None
    return repli


def noter_repli_503(llm: Any) -> None:
    """Retient, sur le client, l'instant du dernier repli (cf. attente_reessai_503)."""
    try:
        llm._repli_503_a = time.monotonic()
    except Exception as err:  # pragma: no cover - objet figé : on perd l'optimisation
        logger.debug("noter_repli_503 KO (%s)", err)


def _repli_recent(llm: Any) -> bool:
    instant = getattr(llm, "_repli_503_a", None)
    if not isinstance(instant, (int, float)):
        return False
    return (time.monotonic() - instant) < float(config.LLM_503_REPLI_MEMOIRE_S)


def attente_reessai_503(exc: BaseException, essai: int, llm: Any = None) -> float | None:
    """Durée à attendre avant de REJOUER l'appel, ou None s'il ne faut pas.

    Une seule décision pour les deux chemins de streaming (`LLMClient.stream_chat`
    côté CLI, `api/streaming.py::stream_api` côté WebSocket) : un 503 est rendu
    AVANT toute génération (le gateway refuse de charger le modèle), donc le
    rejouer ne coûte rien, et il est transitoire (garde mémoire qui suspend des
    processus, `vm_stat` qui sous-estime la RAM juste après un chargement).
    Backoff ATTENTE × 2^essai, borné à ESSAIS essais — cf. config.LLM_503_*.
    Les réglages sont lus À L'APPEL, pour rester surchargeables par l'env et
    les tests.

    `llm` fourni : si ce client vient d'être servi en repli (`noter_repli_503`,
    il y a moins de `LLM_503_REPLI_MEMOIRE_S`) et que ce refus y a droit aussi,
    on n'attend plus. Un tour ReAct enchaîne plusieurs appels ; sans cette
    mémoire, CHACUN repaierait 15 s d'attente avant d'arriver au même repli.
    Le modèle nominal reste essayé en premier à chaque appel (un refus est
    immédiat) : dès que la mémoire revient, le tour repasse dessus tout seul.
    """
    if not est_503(exc):
        return None
    if essai >= max(0, int(config.LLM_503_ESSAIS)):
        return None
    if llm is not None and _repli_recent(llm) and modele_repli_503(exc, llm):
        return None
    return float(config.LLM_503_ATTENTE_S) * (2 ** essai)


def message_repli_503(exc: BaseException, llm: Any, repli: str) -> str:
    """Ligne de statut, identique en CLI et dans l'UI, quand le tour est rejoué
    sur le modèle de repli. Dit que c'est un mode DÉGRADÉ : la réponse ne vient
    pas du modèle attendu, l'utilisateur doit pouvoir le savoir."""
    modele = str(getattr(llm, "model", None) or config.LLM_MODEL)
    return (
        f"Mémoire insuffisante pour « {modele} » ({detail_http(exc)}) — mode dégradé : "
        f"ce tour est servi par « {repli} ». « {modele} » sera réessayé au prochain appel."
    )


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
            if _est_503_ram(exc):
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
