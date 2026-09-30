"""
journal_client.py — émission d'événements vers le journal d'usage du gateway.

Brique 1 assistant proactif (klody-core `docs/JOURNAL-USAGE-SPEC.md`) : l'agent
pousse ses appels d'outils et bornes de session sur `POST /journal/event` du
gateway :8090 ; les requêtes LLM, elles, sont journalisées PAR le gateway
lui-même (l'agent se contente d'y poser les en-têtes `X-Klody-App` /
`X-Klody-Source` / `X-Klody-Session` — cf. agent/llm.py).

Fire-and-forget ABSOLU : queue bornée + un thread daemon, urllib (stdlib, pas
de dépendance), timeout court, toute erreur avalée. L'agent ne ralentit ni ne
casse JAMAIS à cause du journal — gateway absent/ancien (endpoint inconnu) =
silencieux, queue pleine = événement jeté.

Discriminant de source (klody-core `docs/JOURNAL-USAGE-SPEC.md` § 3.6) : le
miner d'habitudes ne retient que `source='user'`. Or le gateway :8090 est le
même pour l'utilisateur et pour la machinerie qui fait tourner l'agent — le
banc surtout, qui appelle le vrai gateway depuis ses processus fils. Sans
déclaration, tout ce qui porte `X-Klody-App: klody-ai` est classé `user` :
relevé dans `state/journal.db` le 2026-09-30, la journée du 2026-09-28 (run de
promotion de la baseline, 35 tâches × 3 passes) y figure pour **1 554**
événements `llm` et **1 772** `tool` en `user`, contre 2 bornes de session
seulement. D'où `source()` : `KLODY_SOURCE` si la machinerie l'a posée
(`bench/run.py` pose `system`), `user` sinon.

Config :
  KLODY_JOURNAL=0        coupe l'émission (défaut : active)
  KLODY_JOURNAL_URL=…    racine du gateway (défaut : MLX_BASE_URL sans /v1)
  KLODY_SOURCE=…         source déclarée : user | system | test (défaut : user)
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
# Whitelist de klody-core (`gateway/journal.py::_SOURCES`). Une valeur hors liste
# ne doit jamais partir telle quelle : le gateway la rejetterait en silence et
# retomberait sur la dérivation par `app` — soit `user` pour `klody-ai`.
_SOURCES = ("user", "system", "test")
_QUEUE_MAX = 256
_TIMEOUT_S = 1.0

_queue: queue.Queue | None = None
_lock = threading.Lock()
_sources_inconnues: set[str] = set()   # déjà signalées : un avertissement, pas un par appel


def gateway_root() -> str:
    """Racine HTTP du gateway (sans /v1) — `POST {root}/journal/event`."""
    override = os.getenv("KLODY_JOURNAL_URL")
    if override:
        return override.rstrip("/")
    base = config.MLX_BASE_URL.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def source() -> str:
    """Source déclarée de nos appels au gateway : `KLODY_SOURCE`, sinon `user`.

    Relue à CHAQUE appel, pas figée à l'import : `bench/run.py` la pose dans
    `main()`, après que ses propres imports ont pu charger ce module.

    Une valeur posée mais inconnue (`systeme`, `bench`…) rend `system`, pas
    `user` : même sens de repli que `journal.resolve_source` côté gateway — un
    trafic mal classé `user` fabrique de fausses habitudes, un trafic mal classé
    `system` rend seulement le miner myope. Qui pose la variable dit « pas
    l'utilisateur » ; une faute de frappe ne doit pas le lui faire dire à
    l'envers.

    ⚠️ Pas de détection de pytest ici (la version du 2026-09-22, jamais poussée,
    en avait une) : la suite ne joint plus le gateway du tout —
    `tests/conftest.py` pose `KLODY_JOURNAL=0` et `tests/garde_reseau.py` refuse
    tout le loopback sous les ports éphémères (#287, #290).
    """
    brute = (os.getenv("KLODY_SOURCE") or "").strip().lower()
    if not brute:
        return "user"
    if brute in _SOURCES:
        return brute
    if brute not in _sources_inconnues:
        _sources_inconnues.add(brute)
        logger.warning("KLODY_SOURCE=%r inconnue (attendu : %s) — déclarée « system »",
                       brute, " | ".join(_SOURCES))
    return "system"


def en_tetes() -> dict[str, str]:
    """En-têtes d'identité de nos clients OpenAI vers le gateway (spec § 3.5).

    Un seul endroit pour `agent/llm.py` et `tools/vision.py` : deux copies
    finiraient par diverger, et la seconde classerait son trafic `user`.
    """
    return {"X-Klody-App": APP, "X-Klody-Source": source()}


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
            "source": source(),
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
        # Défense absolue : l'observabilité ne fait JAMAIS échouer l'appelant
        # (l'agent est en plein tour). Erreur inattendue tracée en debug only.
        logger.debug("émission journal impossible", exc_info=True)


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
