"""Garde d'origine et d'hôte de l'API locale — ferme la voie « page web → agent ».

L'API n'écoute que sur 127.0.0.1, mais le NAVIGATEUR de l'utilisateur, lui, peut
l'atteindre depuis n'importe quel site ouvert. Constaté à l'audit du 2026-09-27 :

- `GET /api/siri?q=…` lance l'agent COMPLET (outils à effet de bord compris).
  CORS ne bloque pas un GET simple : `<img src="http://127.0.0.1:8000/api/siri?q=…">`
  sur une page quelconque suffisait — la réponse est illisible pour l'attaquant,
  l'effet, lui, a lieu.
- Starlette lit du JSON quel que soit le `Content-Type` : un POST `text/plain`
  (requête « simple », sans preflight CORS) modifiait `/api/config` ou injectait
  un fait dans `/api/memories`, donc dans le prompt système.
- Le WebSocket faisait `accept()` sans regarder l'`Origin` — les WebSockets
  échappent à CORS. Une page tierce pouvait piloter l'agent ET répondre
  elle-même aux demandes d'approbation, puisqu'elles partent vers le client
  connecté.
- DNS rebinding : une page servie par `evil.example` re-résolue vers 127.0.0.1
  devient « same-origin » pour le navigateur ; seul l'en-tête `Host` la trahit.

Règles, dans l'ordre :

1. `Host` doit nommer la boucle locale (127.0.0.1, localhost, ::1). Absent ⇒
   client non-navigateur, accepté (un navigateur l'envoie toujours).
2. Un `Origin` présent doit appartenir à l'UI (`ORIGINES_UI`) — ou, pour la
   seule route `/api/preview_error`, au serveur d'aperçu (`:PREVIEW_PORT`), dont
   les pages exécutent du code généré et n'ont droit à rien d'autre.
   `Origin: null` (iframe sandboxée, `file://`) est refusé.
3. Sans `Origin` : un navigateur l'omet sur un GET « no-cors » (image,
   navigation). Pour une méthode à effet de bord, ou pour `/api/siri` en GET,
   `Sec-Fetch-Site` doit être absent (client non-navigateur : Raccourcis,
   curl), `none` (adresse tapée par l'utilisateur) ou `same-origin`.

Ce qui reste accepté par construction : le raccourci Siri et `scripts/klody-siri.sh`
(aucun en-tête de navigateur), l'UI Tauri (`tauri://localhost`, dev
`http://localhost:1420`), les sondes `curl` du watchdog, `scripts/probe.py`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from config import PREVIEW_PORT

logger = logging.getLogger("klody.api.garde")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# Source UNIQUE : aussi passée à CORSMiddleware par api/server.py. Deux listes
# qui doivent dire la même chose finissent par diverger.
ORIGINES_UI: tuple[str, ...] = (
    "http://localhost",
    "http://localhost:1420",  # Tauri dev
    "http://localhost:1421",
    "http://localhost:5173",  # Vite dev
    "http://localhost:5174",
    "http://localhost:3000",
    "http://127.0.0.1",
    "http://127.0.0.1:1420",
    "tauri://localhost",  # Tauri production (macOS)
)

ORIGINES_APERCU: tuple[str, ...] = (
    f"http://localhost:{PREVIEW_PORT}",
    f"http://127.0.0.1:{PREVIEW_PORT}",
)
ROUTE_APERCU = "/api/preview_error"

HOTES_LOCAUX = frozenset({"127.0.0.1", "localhost", "::1"})

METHODES_SURES = frozenset({"GET", "HEAD", "OPTIONS"})
# GET qui déclenchent un effet de bord : traités comme une méthode non sûre.
GET_A_EFFET = frozenset({"/api/siri"})
SEC_FETCH_SITE_ADMIS = frozenset({"none", "same-origin"})


def _hote_sans_port(hote: str) -> str:
    hote = hote.strip().lower()
    if hote.startswith("["):  # IPv6 : « [::1]:8000 »
        return hote[1 : hote.find("]")] if "]" in hote else hote
    return hote.rsplit(":", 1)[0] if hote.count(":") == 1 else hote


def motif_de_refus(
    *,
    type_: str,
    methode: str,
    chemin: str,
    hote: str | None,
    origine: str | None,
    sec_fetch_site: str | None,
) -> str | None:
    """Pourquoi refuser cette requête, ou None si elle passe. Fonction pure."""
    if hote is not None and _hote_sans_port(hote) not in HOTES_LOCAUX:
        return f"hôte « {hote} » non local (DNS rebinding ?)"

    if origine is not None:
        if origine in ORIGINES_UI:
            return None
        if type_ == "http" and chemin == ROUTE_APERCU and origine in ORIGINES_APERCU:
            return None
        return f"origine « {origine} » non autorisée"

    a_effet = type_ == "websocket" or methode not in METHODES_SURES or chemin in GET_A_EFFET
    if a_effet and sec_fetch_site is not None and sec_fetch_site not in SEC_FETCH_SITE_ADMIS:
        return f"requête inter-sites sans origine (Sec-Fetch-Site: {sec_fetch_site})"
    return None


def _en_tete(scope: Scope, nom: bytes) -> str | None:
    for cle, valeur in scope.get("headers") or ():
        if cle.lower() == nom:
            return str(valeur.decode("latin-1"))
    return None


class GardeOrigine:
    """Middleware ASGI : applique `motif_de_refus` au HTTP et au WebSocket."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        motif = motif_de_refus(
            type_=scope["type"],
            methode=scope.get("method", "GET"),
            chemin=scope.get("path", ""),
            hote=_en_tete(scope, b"host"),
            origine=_en_tete(scope, b"origin"),
            sec_fetch_site=_en_tete(scope, b"sec-fetch-site"),
        )
        if motif is None:
            await self.app(scope, receive, send)
            return

        logger.warning(
            "[Garde] %s %s refusé : %s", scope["type"], scope.get("path", ""), motif
        )
        if scope["type"] == "websocket":
            # Fermer AVANT accept() : le serveur répond 403 à la poignée de main.
            await send({"type": "websocket.close", "code": 1008, "reason": "origine refusée"})
            return
        corps = json.dumps({"detail": f"Refusé : {motif}."}, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(corps)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": corps})
