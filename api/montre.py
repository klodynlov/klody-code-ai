"""Route de l'app Apple Watch : ``/api/montre/*`` — lecture seule, jeton exigé.

Demandée le 2026-11-26 : une app Watch native pour Klody. La montre ne parle
JAMAIS au Mac directement : elle passe par l'app iPhone KlodyPocket
(WatchConnectivity), qui garde le jeton et appelle, sur le tailnet seulement ::

    https://<mac>.<tailnet>.ts.net/klody/<route>
      └─ tailscale serve --set-path /klody → http://127.0.0.1:8000/api/montre/<route>

Pourquoi une route DÉDIÉE plutôt que ``/api/siri`` ou le WebSocket : l'API n'a
aucune authentification et n'écoute que sur 127.0.0.1. L'ouvrir telle quelle au
tailnet donnerait à tout appareil du tailnet l'agent complet. Ici, seul ce
routeur est exposé (``--set-path /klody`` ne proxifie que ``/api/montre``), et il
n'offre que le régime du canal iMessage :

- jeton ``KLODY_MONTRE_TOKEN`` (``.env``, posé par l'utilisateur), comparé en
  temps constant ; absent ou trop court ⇒ TOUT répond 503 « non configuré » ;
- ``/demande`` = ``canaux/imessage.py::preparer_orchestrateur`` : modèle épinglé,
  liste blanche de LECTURE revérifiée à l'exécution, pas d'outil web ;
- seules écritures : une note et un rappel dans ``proactif.db`` (le registre du
  canal, qui les reprend au briefing ou les envoie par iMessage à l'heure), et le
  dépôt de ``briefing.maintenant`` (le vrai briefing part par iMessage).

Contrat (l'app Watch/iPhone est écrite contre lui) : toutes les réponses en JSON,
toute erreur nommée ``{"erreur": "…"}`` avec un code HTTP parlant — jamais un 500
nu, jamais un 422 au format FastAPI. Paramètres et corps sont donc lus à la main :
le jeton est vérifié AVANT toute validation (un 422 ne doit pas répondre à un
inconnu avant le 401).

TCC : l'agenda est lu par ``tools/agenda.py`` (coque ``KlodyMessages.app`` lancée
par ``open -n -W``), jamais par ce processus.
"""

from __future__ import annotations

import asyncio
import functools
import hmac
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

import config
from canaux.briefing import http_get
from canaux.proactif import JOURS, REQUETE_MAILS, GmailMCP, Registre, formater_moment, outils_locaux
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from tools import agenda as ag

logger = logging.getLogger("klody.api.montre")

PREFIXE = "/api/montre"
VERSION = 1
ENV_JETON = "KLODY_MONTRE_TOKEN"
# 32 caractères au moins : `openssl rand -hex 32` en donne 64. Un jeton court
# exposé au tailnet se devine ; on préfère refuser de servir.
JETON_MIN = 32
TEXTE_MAX = 1000
DELAI_DEMANDE_S = 120.0
# L'agenda passe par la coque (`open -n -W`, jusqu'à 60 s) : la complication et
# l'app iPhone sondent souvent, un instantané d'une minute suffit.
CACHE_AGENDA_S = 60.0
RAPPEL_MAX = timedelta(days=366)
MOMENTS = ag.MOMENTS
_AUJOURDHUI = frozenset({"aujourd'hui", "aujourd’hui", "aujourdhui"})

# Dépendances remplaçables (tests) — lues À L'APPEL, jamais figées à l'import.
HTTP_GET: Callable[[str], bytes] = http_get
LIRE_AGENDA: Callable[[int], dict] = ag.lire_agenda


def maintenant() -> datetime:
    """Heure locale AVEC fuseau (les dates rendues portent leur décalage)."""
    return datetime.now().astimezone()


def chercheur_mails() -> Callable[[str, int], list[dict]] | None:
    url = config.MCP_SERVERS.get("gmail")
    return GmailMCP(url).chercher if url else None


def fabrique_runner(session: str, bloques: list[str]) -> Callable[[str], str]:
    """Orchestrateur au régime du canal iMessage, conversation ``memory_montre.json``.

    Outils locaux du canal (``programmer_rappel``, ``noter``) : même registre
    ``proactif.db`` que lui — un rappel dicté à la montre part par iMessage.
    """
    from canaux.imessage import make_agent_runner

    return make_agent_runner(config.MEMORY_DIR, outils_locaux(_registre()),
                             session=session, bloques=bloques)


def _registre() -> Registre:
    return Registre(config.MEMORY_DIR / "proactif.db")


# ── Erreurs nommées ──────────────────────────────────────────────────────────

class ErreurMontre(Exception):
    def __init__(self, statut: int, message: str) -> None:
        super().__init__(message)
        self.statut = statut
        self.message = message


def _json_erreur(statut: int, message: str) -> JSONResponse:
    return JSONResponse({"erreur": message}, status_code=statut)


def verifier_jeton(autorisation: str | None, attendu: str | None) -> None:
    """Lève ``ErreurMontre`` 503 (non configuré) ou 401. Fonction pure.

    ``hmac.compare_digest`` sur des octets : temps constant, et un en-tête non
    ASCII ne lève pas ``TypeError``.
    """
    attendu = (attendu or "").strip()
    if not attendu:
        raise ErreurMontre(503, f"route montre non configurée ({ENV_JETON} absent du .env)")
    if len(attendu) < JETON_MIN:
        raise ErreurMontre(503, f"route montre non configurée ({ENV_JETON} trop court : "
                                f"{JETON_MIN} caractères au moins)")
    schema, _, fourni = (autorisation or "").partition(" ")
    if schema.lower() != "bearer" or not hmac.compare_digest(
            fourni.strip().encode(), attendu.encode()):
        raise ErreurMontre(401, "jeton absent ou invalide")


Handler = Callable[..., Awaitable[Any]]


def _protege(fn: Handler) -> Handler:
    """Jeton d'abord, puis le handler ; toute exception devient une erreur NOMMÉE."""

    @functools.wraps(fn)
    async def enveloppe(request: Request, *args: Any, **kwargs: Any) -> Any:
        try:
            verifier_jeton(request.headers.get("authorization"), os.environ.get(ENV_JETON))
            return await fn(request, *args, **kwargs)
        except ErreurMontre as e:
            if e.statut == 401:
                logger.warning("[montre] %s %s : jeton refusé", request.method, request.url.path)
            return _json_erreur(e.statut, e.message)
        except Exception as e:
            logger.error("[montre] %s %s : %s", request.method, request.url.path, e,
                         exc_info=True)
            return _json_erreur(500, f"erreur interne ({type(e).__name__})")

    return enveloppe


async def _corps(request: Request) -> dict:
    try:
        d = await request.json()
    except Exception as e:
        raise ErreurMontre(400, "corps JSON illisible") from e
    if not isinstance(d, dict):
        raise ErreurMontre(400, "corps JSON attendu : un objet")
    return d


def _texte(d: dict, cle: str = "texte") -> str:
    v = d.get(cle)
    if not isinstance(v, str) or not v.strip():
        raise ErreurMontre(400, f"« {cle} » manquant ou vide")
    v = v.strip()
    if len(v) > TEXTE_MAX:
        raise ErreurMontre(413, f"« {cle} » trop long ({len(v)} > {TEXTE_MAX} caractères)")
    return v


def _entier(brut: str | None, nom: str, defaut: int, mini: int, maxi: int) -> int:
    if brut is None or brut == "":
        return defaut
    try:
        n = int(brut)
    except ValueError as e:
        raise ErreurMontre(400, f"« {nom} » doit être un entier ({mini} à {maxi})") from e
    if not mini <= n <= maxi:
        raise ErreurMontre(400, f"« {nom} » hors bornes ({mini} à {maxi})")
    return n


def _moment(brut: object, heure: int) -> str:
    if brut is None or brut == "":
        return "matin" if heure < 11 else "midi" if heure < 17 else "soir"
    m = str(brut).strip().lower()
    if m not in MOMENTS:
        raise ErreurMontre(400, f"moment inconnu « {m} » : matin, midi ou soir")
    return m


# ── Agenda : instantané partagé ──────────────────────────────────────────────

_cache_agenda: dict[int, tuple[float, dict]] = {}
_verrou_agenda = threading.Lock()


def agenda_brut(jours: int) -> dict:
    """``{evenements, rappels, erreurs}`` de la coque, gardé ``CACHE_AGENDA_S``.

    Un seul lancement de coque à la fois : dix sondages simultanés ne doivent pas
    ouvrir dix instances de ``KlodyMessages.app``.
    """
    with _verrou_agenda:
        t = time.monotonic()
        vu = _cache_agenda.get(jours)
        if vu is not None and t - vu[0] < CACHE_AGENDA_S:
            return vu[1]
        d = LIRE_AGENDA(jours)
        _cache_agenda[jours] = (time.monotonic(), d)
        return d


def vider_cache() -> None:
    with _verrou_agenda:
        _cache_agenda.clear()


def _date_locale(iso: object) -> datetime | None:
    """ISO de la coque → datetime local AVEC fuseau (naïf ⇒ supposé local)."""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return dt.astimezone()


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat(timespec="seconds")


def prochain_rdv(d: dict, now: datetime) -> dict | None:
    """Prochain rendez-vous HORAIRE qui commence après ``now``, aujourd'hui ou demain."""
    limite = now.date() + timedelta(days=1)
    candidats = []
    for e in d.get("evenements") or []:
        if e.get("journee"):
            continue
        debut = _date_locale(e.get("debut"))
        if debut is None or debut <= now or debut.date() > limite:
            continue
        candidats.append((debut, e))
    if not candidats:
        return None
    debut, e = min(candidats, key=lambda c: c[0])
    lieu = str(e.get("lieu") or "").strip()
    return {"titre": str(e.get("titre") or "(sans titre)"), "debut": _iso(debut),
            "fin": _iso(_date_locale(e.get("fin"))),
            "lieu": lieu.splitlines()[0][:80] if lieu else None}


def fenetre(d: dict, debut: date, fin: date) -> tuple[list[dict], list[dict]]:
    """Événements dont le début tombe dans [debut, fin] ; rappels non faits
    échus au plus tard ``fin`` (ou sans date) — même règle que ``ag.formater``."""
    evs = []
    for e in d.get("evenements") or []:
        dt = _date_locale(e.get("debut"))
        if dt is not None and debut <= dt.date() <= fin:
            evs.append(e)
    raps = []
    for r in d.get("rappels") or []:
        ech = _date_locale(r.get("echeance")) if r.get("echeance") else None
        if ech is None or ech.date() <= fin:
            raps.append(r)
    return evs, raps


def _premier_jour(brut: str | None) -> int:
    v = (brut or "aujourd'hui").strip().lower()
    if v in _AUJOURDHUI:
        return 0
    if v == "demain":
        return 1
    raise ErreurMontre(400, f"« a_partir_de » inconnu « {v} » : aujourd'hui ou demain")


def _agenda_texte(decalage: int, jours: int, now: datetime) -> tuple[str, dict, date, date]:
    debut = now.date() + timedelta(days=decalage)
    fin = debut + timedelta(days=jours - 1)
    d = agenda_brut(decalage + jours)
    return ag.formater(d, debut, fin, now.date()), d, debut, fin


# ── Briefing express (sans modèle) ───────────────────────────────────────────

def _rubrique(nom: str, fn: Callable[[], str]) -> str:
    try:
        return fn()
    except Exception as e:
        logger.warning("[montre] briefing %s : %s", nom, e)
        return f"{nom} : indisponible ({type(e).__name__})"


def _mails() -> str:
    chercher = chercheur_mails()
    if chercher is None:
        return "📬 Mails : Gmail non configuré"
    n = len(chercher(REQUETE_MAILS, 20))
    return ("📬 " + (f"{n} mail(s) important(s) non lu(s)" if n
                    else "aucun mail important non lu"))


def briefing_express(moment: str, now: datetime) -> list[str]:
    """Météo, agenda + rappels, nombre de mails importants non lus — en
    parallèle, chaque rubrique en panne NOMMÉE. Le soir regarde demain."""
    decalage = 1 if moment == "soir" else 0
    salut = {"matin": "☀️ Bonjour", "midi": "🍽 Point de midi", "soir": "🌙 Bonsoir"}[moment]
    jour = now.date() + timedelta(days=decalage)
    entete = f"{salut} — {'demain, ' if decalage else ''}{JOURS[jour.weekday()]} {jour:%d/%m}"
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="montre-briefing") as pool:
        f_meteo = pool.submit(_rubrique, "🌤 Météo", lambda: ag.meteo(
            "", 1 + decalage, get=HTTP_GET, maintenant=now.replace(tzinfo=None)))
        f_agenda = pool.submit(_rubrique, "📅 Agenda",
                               lambda: _agenda_texte(decalage, 1, now)[0])
        f_mails = pool.submit(_rubrique, "📬 Mails", _mails)
        return [f"{entete}\n\n{f_meteo.result()}", f_agenda.result(), f_mails.result()]


# ── Demande à Klody ──────────────────────────────────────────────────────────

_verrou_demande = threading.Lock()
# Exécuteur DÉDIÉ, un seul fil : une demande au-delà du délai continue de tourner
# (on ne tue pas un fil), et l'exécuteur par défaut de la boucle n'a pas à
# l'attendre à l'arrêt ni à lui prêter un de ses fils.
_executeur_demande = ThreadPoolExecutor(max_workers=1, thread_name_prefix="montre-demande")
# Outils refusés par la garde (audit, lu par les tests et le journal).
bloques: list[str] = []


def _executer_demande(texte: str) -> tuple[str, float]:
    t0 = time.monotonic()
    try:
        reponse = fabrique_runner("montre", bloques)(texte)
    finally:
        _verrou_demande.release()
    return reponse, round(time.monotonic() - t0, 2)


# ── Routes ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix=PREFIXE, tags=["montre"])


@router.get("/etat")
@_protege
async def etat(request: Request) -> dict:
    """Complication : prochain rendez-vous horaire, aujourd'hui ou demain."""
    now = maintenant()
    d = await run_in_threadpool(agenda_brut, 2)
    return {"ok": True, "version": VERSION, "prochain_rdv": prochain_rdv(d, now),
            "maj": _iso(now), "erreurs": [str(x) for x in d.get("erreurs") or []]}


@router.get("/agenda")
@_protege
async def agenda(request: Request, jours: str | None = None,
                 a_partir_de: str | None = None) -> dict:
    """Rendez-vous et rappels non faits, ``jours`` (1-14) à partir d'aujourd'hui/demain."""
    n = _entier(jours, "jours", 1, 1, ag.JOURS_MAX)
    decalage = _premier_jour(a_partir_de)
    now = maintenant()
    texte, d, debut, fin = await run_in_threadpool(_agenda_texte, decalage, n, now)
    evs, raps = fenetre(d, debut, fin)
    return {"texte": texte, "evenements": evs, "rappels": raps}


@router.get("/meteo")
@_protege
async def meteo(request: Request, lieu: str | None = None, jours: str | None = None) -> dict:
    """Prévisions : Baulon + Le Rheu sans ``lieu``, sinon un nom de ville."""
    n = _entier(jours, "jours", 2, 1, ag.METEO_JOURS_MAX)
    lieu = (lieu or "").strip()
    if lieu and not ag._LIEU_OK.match(lieu):
        raise ErreurMontre(400, "lieu refusé : un simple nom de ville "
                                "(lettres, espaces, tirets, 60 caractères au plus)")
    texte = await run_in_threadpool(ag.meteo, lieu, n, HTTP_GET)
    return {"texte": texte}


@router.get("/briefing")
@_protege
async def briefing_rapide(request: Request, moment: str | None = None) -> dict:
    """Briefing EXPRESS, sans modèle : météo, agenda + rappels, mails importants."""
    now = maintenant()
    m = _moment(moment, now.hour)
    return {"messages": await run_in_threadpool(briefing_express, m, now)}


@router.post("/briefing")
@_protege
async def briefing_complet(request: Request) -> dict:
    """Demande le VRAI briefing : il part par iMessage (canal), sous une minute."""
    d = await _corps(request)
    m = d.get("moment")
    if m not in (None, "") and str(m).strip().lower() not in MOMENTS:
        raise ErreurMontre(400, f"moment inconnu « {m} » : matin, midi ou soir")
    detail = ag.briefing_maintenant(config.MEMORY_DIR, str(m or ""))
    return {"texte": "demandé", "detail": detail}


@router.post("/demande")
@_protege
async def demande(request: Request) -> Any:
    """« Demander à Klody » : régime iMessage, une demande à la fois, 120 s au plus."""
    texte = _texte(await _corps(request))
    if not _verrou_demande.acquire(blocking=False):
        raise ErreurMontre(429, "Klody répond déjà à une demande de la montre")
    logger.info("[montre] demande (%d car.)", len(texte))
    tache = asyncio.get_running_loop().run_in_executor(
        _executeur_demande, _executer_demande, texte)
    try:
        reponse, duree = await asyncio.wait_for(asyncio.shield(tache), DELAI_DEMANDE_S)
    except TimeoutError:
        # Le fil continue (on ne tue pas un fil) et rend le verrou en finissant :
        # d'ici là, les demandes suivantes reçoivent 429, pas une file d'attente.
        logger.warning("[montre] demande au-delà de %.0f s", DELAI_DEMANDE_S)
        return _json_erreur(504, f"pas de réponse en {DELAI_DEMANDE_S:.0f} s")
    return {"reponse": reponse, "duree_s": duree}


@router.post("/note")
@_protege
async def note(request: Request) -> dict:
    """Note dictée → ``proactif.db`` ; reprise au prochain briefing."""
    texte = _texte(await _corps(request))
    n = _registre().ajouter_note(texte, time.time())
    return {"texte": f"Note n°{n} enregistrée.", "id": n}


def lire_quand(brut: object, now: datetime) -> datetime:
    """ISO 8601 → datetime local naïf ; refuse passé (en citant maintenant) et > 1 an."""
    try:
        quand = datetime.fromisoformat(str(brut or "").strip())
    except ValueError as e:
        raise ErreurMontre(400, "« quand » illisible : ISO 8601 attendu "
                                "(AAAA-MM-JJTHH:MM[:SS][±HH:MM])") from e
    local = now.replace(tzinfo=None)
    if quand.tzinfo is not None:
        quand = quand.astimezone().replace(tzinfo=None)
    if quand <= local:
        raise ErreurMontre(400, f"{quand:%Y-%m-%dT%H:%M} est déjà passé "
                                f"(maintenant : {local:%Y-%m-%dT%H:%M})")
    if quand - local > RAPPEL_MAX:
        raise ErreurMontre(400, "rappel à plus d'un an : refusé")
    return quand


@router.post("/rappel")
@_protege
async def rappel(request: Request) -> dict:
    """« Rappelle-moi » → ``proactif.db`` ; le canal l'envoie par iMessage à l'heure."""
    d = await _corps(request)
    texte = _texte(d)
    now = maintenant()
    quand = lire_quand(d.get("quand"), now)
    n = _registre().ajouter_rappel(quand.timestamp(), texte, now.timestamp())
    logger.info("[montre] rappel n°%d programmé pour %s", n, f"{quand:%Y-%m-%dT%H:%M}")
    return {"texte": f"Rappel n°{n} programmé pour {formater_moment(quand)}.", "id": n,
            "quand": _iso(quand.astimezone())}


def installer(app: FastAPI) -> None:
    app.include_router(router)


__all__ = ["PREFIXE", "installer", "router", "verifier_jeton"]
