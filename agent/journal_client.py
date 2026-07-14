"""
journal_client.py — émission d'événements vers le journal d'usage du gateway.

Brique 1 assistant proactif (klody-core `docs/JOURNAL-USAGE-SPEC.md`) : l'agent
pousse ses appels d'outils et bornes de session sur `POST /journal/event` du
gateway :8090 ; les requêtes LLM, elles, sont journalisées PAR le gateway
lui-même (l'agent se contente d'y poser les en-têtes `X-Klody-App` /
`X-Klody-Session` — cf. agent/llm.py).

Fire-and-forget ABSOLU : queue bornée + un thread daemon, urllib (stdlib, pas
de dépendance), timeout court, toute erreur avalée. L'agent ne ralentit ni ne
casse JAMAIS à cause du journal — gateway absent/ancien (endpoint inconnu) =
silencieux, queue pleine = événement jeté.

Config :
  KLODY_JOURNAL=0        coupe l'émission (défaut : active)
  KLODY_JOURNAL_URL=…    racine du gateway (défaut : MLX_BASE_URL sans /v1)
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import urllib.request

import config

logger = logging.getLogger(__name__)

APP = "klody-ai"           # valeur X-Klody-App / champ app de tous nos événements
_QUEUE_MAX = 256
_TIMEOUT_S = 1.0

_queue: queue.Queue | None = None
_lock = threading.Lock()


def gateway_root() -> str:
    """Racine HTTP du gateway (sans /v1) — `POST {root}/journal/event`."""
    override = os.getenv("KLODY_JOURNAL_URL")
    if override:
        return override.rstrip("/")
    base = config.MLX_BASE_URL.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def emit(*, kind: str, name: str | None = None, status: str = "ok",
         session_id: str | None = None, latency_ms: int | None = None,
         meta: dict | None = None) -> None:
    """Pousse un événement dans la queue d'envoi. Jamais bloquant, jamais levant."""
    try:
        if os.getenv("KLODY_JOURNAL", "1") == "0":
            return
        q = _ensure_worker()
        q.put_nowait({
            "app": APP,
            "kind": kind,
            "name": name,
            "status": status,
            "session_id": session_id,
            "latency_ms": latency_ms,
            "meta": meta,
        })
    except queue.Full:
        pass                             # journal saturé : on jette, jamais d'attente
    except Exception:
        pass


def _ensure_worker() -> queue.Queue:
    global _queue
    if _queue is None:
        with _lock:
            if _queue is None:
                q: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
                threading.Thread(target=_worker_loop, args=(q,),
                                 daemon=True, name="klody-journal-client").start()
                _queue = q
    return _queue


def _worker_loop(q: queue.Queue) -> None:
    url = gateway_root() + "/journal/event"
    if not url.startswith(("http://", "https://")):
        logger.debug("journal coupé : URL gateway non-HTTP (%s)", url)
        return
    while True:
        event = q.get()
        try:
            body = json.dumps({k: v for k, v in event.items() if v is not None},
                              ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=_TIMEOUT_S).close()  # nosec B310 — schéma http vérifié ci-dessus, gateway loopback
        except Exception as e:
            logger.debug("journal event non envoyé : %s", e)
