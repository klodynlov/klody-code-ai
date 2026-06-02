"""Tampon des erreurs JS runtime remontées par l'overlay de preview.

Le code généré par Klody tourne dans le navigateur, HORS de sa boucle ReAct :
il croit avoir réussi (`preview_code` → « succès ») alors que son JS peut
planter à l'exécution. L'overlay d'erreurs (`tools.preview._ERROR_OVERLAY`)
capte ces exceptions et les renvoie en beacon vers `POST /api/preview_error` ;
ce module les conserve — bornées — pour que l'agent puisse les relire et se
corriger. C'est le maillon « navigateur → Klody » de la boucle de feedback.

Pur et thread-safe : le beacon arrive sur le thread serveur, l'agent lit depuis
son thread worker. `record(now=...)` permet des tests déterministes.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

_MAX_REPORTS = 50           # rapports conservés au total (anti-fuite mémoire)
_MAX_ERRORS_PER_REPORT = 20  # erreurs gardées par rapport
_MAX_FIELD_LEN = 2000        # troncature de msg/src trop longs


@dataclass(frozen=True)
class PreviewError:
    """Une erreur JS unique captée dans la preview."""

    label: str
    msg: str
    src: str


@dataclass(frozen=True)
class PreviewErrorReport:
    """Lot d'erreurs remonté par une preview à un instant donné."""

    url: str
    errors: tuple[PreviewError, ...]
    ts: float


@dataclass(frozen=True)
class PreviewLoad:
    """Signal « preview chargée sans erreur » (ping propre de l'overlay).

    Permet à l'agent de conclure « RAS » tôt au lieu d'attendre le timeout.
    """

    url: str
    ts: float


_lock = threading.Lock()
_reports: list[PreviewErrorReport] = []
_loads: list[PreviewLoad] = []


def _clean(value: object) -> str:
    """Stringifie et borne un champ (jamais None, jamais géant)."""
    return ("" if value is None else str(value))[:_MAX_FIELD_LEN]


def record(url: str, errors: list[dict], *, now: float | None = None) -> PreviewErrorReport:
    """Enregistre un rapport d'erreurs pour une URL de preview (borné)."""
    cleaned = tuple(
        PreviewError(
            label=_clean(e.get("label")),
            msg=_clean(e.get("msg")),
            src=_clean(e.get("src")),
        )
        for e in errors[:_MAX_ERRORS_PER_REPORT]
        if isinstance(e, dict)
    )
    report = PreviewErrorReport(
        url=_clean(url),
        errors=cleaned,
        ts=time.time() if now is None else now,
    )
    with _lock:
        _reports.append(report)
        if len(_reports) > _MAX_REPORTS:
            del _reports[: len(_reports) - _MAX_REPORTS]
    return report


def recent(url: str | None = None, *, since: float | None = None) -> list[PreviewErrorReport]:
    """Rapports récents (du plus ancien au plus récent), filtrables par url/ts."""
    with _lock:
        items = list(_reports)
    if url is not None:
        items = [r for r in items if r.url == url]
    if since is not None:
        items = [r for r in items if r.ts >= since]
    return items


def mark_loaded(url: str, *, now: float | None = None) -> None:
    """Marque une preview comme chargée proprement (ping de l'overlay, 0 erreur)."""
    load = PreviewLoad(url=_clean(url), ts=time.time() if now is None else now)
    with _lock:
        _loads.append(load)
        if len(_loads) > _MAX_REPORTS:
            del _loads[: len(_loads) - _MAX_REPORTS]


def loaded(url: str | None = None, *, since: float | None = None) -> list[PreviewLoad]:
    """Pings « chargé proprement » récents, filtrables par url/ts."""
    with _lock:
        items = list(_loads)
    if url is not None:
        items = [x for x in items if x.url == url]
    if since is not None:
        items = [x for x in items if x.ts >= since]
    return items


def clear(url: str | None = None) -> None:
    """Vide le tampon — tout, ou seulement une url (reset de boucle / tests)."""
    with _lock:
        if url is None:
            _reports.clear()
            _loads.clear()
        else:
            kept = [r for r in _reports if r.url != url]
            _reports.clear()
            _reports.extend(kept)
            kept_loads = [x for x in _loads if x.url != url]
            _loads.clear()
            _loads.extend(kept_loads)
