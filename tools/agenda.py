"""Agenda, app Rappels et météo pour Klody — partout (app, CLI, iMessage).

Demandé le 2026-11-26 (« élargir le champ de compétences de Klody », choix
« Agenda, Rappels, météo », partout). Trois outils, tous en LECTURE (sauf la
demande de briefing, qui ne fait que déposer un fichier) :

- ``agenda`` : rendez-vous de l'app Calendrier et rappels non faits de l'app
  Rappels, sur 1 à 14 jours ;
- ``meteo`` : prévisions Open-Meteo (sans clé) pour Baulon et Le Rheu par
  défaut, ou une ville nommée ;
- ``briefing_maintenant`` : demande le briefing iMessage tout de suite.

⚠️ TCC suit le processus RESPONSABLE (vécu le 2026-11-21). L'accès Calendriers
et Rappels n'est accordé qu'à ``KlodyMessages.app`` :

- sous la coque (canal iMessage, ``KLODY_IMSG_LAUNCHER`` posé), le binaire est
  relancé directement — son responsable reste l'app ;
- ailleurs (API ``:8000``, CLI), il est lancé par ``open -n -W`` : LaunchServices
  fait de l'app son propre responsable. Lancé en enfant de Python, macOS
  demanderait l'accès au nom de Python — donc à tout script du venv. ``open`` ne
  relaie pas stdout : l'app écrit dans ``~/.klody/data/agenda/`` (seul dossier
  qu'elle accepte, cf. ``Agenda.swift``), on lit puis on efface.

Ce qui sort du Mac : le nom de la ville demandée et des coordonnées
(Open-Meteo). Agenda et Rappels restent locaux (EventKit).
"""

from __future__ import annotations

import json
import logging
import os
import re

# Binaires fixes (/usr/bin/open, coque signée), argv sans shell.
import urllib.parse
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from canaux.briefing import JOURS, LIEUX, WMO, http_get, lancer

logger = logging.getLogger("klody.agenda")

APP_DEFAUT = Path.home() / "Applications" / "KlodyMessages.app"
# Fixé par la coque (Agenda.swift::sortieAutorisee) : PAS ``KLODY_DATA_DIR``.
DOSSIER_SORTIE = Path.home() / ".klody" / "data" / "agenda"
DELAI_AGENDA_S = 60.0
JOURS_MAX = 14
METEO_JOURS_MAX = 7
# Un nom de ville, pas une phrase : c'est la seule donnée qui part sur le réseau.
# Un mail piégé lu par Klody ne doit pas pouvoir y glisser autre chose.
_LIEU_OK = re.compile(r"^[^\W\d_][\w\s'’.\-]{1,58}$")

Run = Callable[[list[str], float], str]


# ── Agenda / Rappels ─────────────────────────────────────────────────────────

def _app() -> Path:
    lanceur = os.environ.get("KLODY_IMSG_LAUNCHER")
    return Path(lanceur) if lanceur else APP_DEFAUT


def _binaire(app: Path) -> Path:
    return app / "Contents" / "MacOS" / "KlodyMessages"


def _coque(args: list[str], run: Run, dossier: Path, sous_coque: bool | None,
           app: Path, delai: float) -> dict:
    """Lance la coque avec ``args`` et rend son JSON.

    Sous la coque : relance directe (le responsable TCC reste l'app). Ailleurs :
    ``open -g -n -W`` + ``--sortie`` dans ``dossier``, lu puis effacé. Lève
    ``FileNotFoundError`` si la coque n'a rien écrit (accès refusé, coque trop
    ancienne pour ce mode)."""
    if sous_coque is None:
        sous_coque = bool(os.environ.get("KLODY_IMSG_LAUNCHER"))
    if sous_coque:
        return dict(json.loads(run([str(_binaire(app)), *args], delai)))
    dossier.mkdir(parents=True, exist_ok=True)
    f = dossier / f"{uuid.uuid4().hex}.json"
    try:
        run(["/usr/bin/open", "-g", "-n", "-W", "-a", str(app), "--args",
             *args, "--sortie", str(f)], delai)
        return dict(json.loads(f.read_text()))
    finally:
        f.unlink(missing_ok=True)


def lire_agenda(jours: int, run: Run = lancer, dossier: Path = DOSSIER_SORTIE,
                sous_coque: bool | None = None, app: Path | None = None) -> dict:
    """JSON de la coque ``{evenements, rappels, erreurs}`` ; toute panne est NOMMÉE
    dans ``erreurs`` (jamais un agenda vide qui ment — leçon du 2026-11-22)."""
    jours = max(1, min(JOURS_MAX, int(jours)))
    app = app or _app()
    if not _binaire(app).exists():
        return {"evenements": [], "rappels": [],
                "erreurs": [f"coque KlodyMessages absente ({app})"]}
    try:
        return _coque(["--agenda", str(jours)], run, dossier, sous_coque, app, DELAI_AGENDA_S)
    except FileNotFoundError:
        return {"evenements": [], "rappels": [],
                "erreurs": ["la coque n'a rien écrit (accès Calendriers/Rappels refusé ?)"]}
    except Exception as e:
        logger.warning("[agenda] %s", e)
        return {"evenements": [], "rappels": [], "erreurs": [f"agenda : {type(e).__name__}"]}


def _local(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _jour(d: date) -> str:
    return f"{JOURS[d.weekday()]} {d:%d/%m}"


def formater(d: dict, debut: date, fin: date, aujourdhui: date) -> str:
    """Rendez-vous groupés par jour sur [debut, fin], puis rappels non faits."""
    par_jour: dict[date, list[str]] = {}
    for e in d.get("evenements", []):
        dt, df = _local(e.get("debut", "")), _local(e.get("fin", ""))
        if dt is None or not (debut <= dt.date() <= fin):
            continue
        quand = "journée" if e.get("journee") else (
            f"{dt:%H:%M}" + (f"–{df:%H:%M}" if df else ""))
        ligne = f"• {quand} {e.get('titre') or '(sans titre)'}"
        if e.get("lieu"):
            ligne += f" ({str(e['lieu']).splitlines()[0][:50]})"
        par_jour.setdefault(dt.date(), []).append(ligne)
    blocs = []
    j = debut
    while j <= fin:
        blocs.append(f"📅 {_jour(j)}\n" + ("\n".join(par_jour[j]) if j in par_jour
                                           else "aucun rendez-vous"))
        j += timedelta(days=1)
    rap = []
    for r in d.get("rappels", []):
        ech = _local(r.get("echeance", ""))
        if ech is not None and ech.date() > fin:
            continue
        ligne = f"• {r.get('titre') or '(sans titre)'}"
        if ech is not None:
            if ech.date() < aujourdhui:
                ligne += f" (en retard, {ech:%d/%m})"
            elif r.get("avec_heure"):
                ligne += f" ({_jour(ech.date())} {ech:%H:%M})"
            else:
                ligne += f" ({_jour(ech.date())})"
        rap.append(ligne)
    blocs.append("✅ Rappels non faits\n" + ("\n".join(rap[:20]) if rap else "aucun"))
    erreurs = [str(x) for x in d.get("erreurs") or []]
    if erreurs:
        blocs.append("⚠️ Incomplet : " + "; ".join(erreurs))
    return "\n\n".join(blocs)


def agenda(jours: int = 1, a_partir_de: str = "aujourd'hui", run: Run = lancer,
           dossier: Path = DOSSIER_SORTIE, maintenant: datetime | None = None,
           sous_coque: bool | None = None, app: Path | None = None) -> str:
    """Outil ``agenda`` : rendez-vous et rappels, ``jours`` jours à partir
    d'aujourd'hui ou de demain."""
    now = maintenant or datetime.now()
    jours = max(1, min(JOURS_MAX, int(jours or 1)))
    decalage = 1 if str(a_partir_de).strip().lower() == "demain" else 0
    debut = now.date() + timedelta(days=decalage)
    fin = debut + timedelta(days=jours - 1)
    d = lire_agenda(decalage + jours, run, dossier, sous_coque, app)
    return formater(d, debut, fin, now.date())


# ── Création (rappel, événement) : APRÈS validation humaine seulement ────────
#
# Demandé le 2026-11-26 (chantier « Actions avec ton oui »). Ces deux outils
# ÉCRIVENT dans l'app Rappels / Calendrier : `agent/approval.py` les garde (boîte
# de validation de l'app), le canal iMessage les met en attente d'un « oui »
# (`canaux/confirmation.py`). D'où la séparation préparer / exécuter : la
# préparation valide et rend le récapitulatif EXACT de ce qui sera écrit, sans
# rien toucher ; l'exécution n'écrit que ce récapitulatif-là.

DELAI_CREATION_S = 60.0
TITRE_MAX = 200
NOTES_MAX = 1000
# Une date à plus d'un jour dans le passé est presque toujours une année fausse
# (vécu le 2026-11-22 avec programmer_rappel) ; au-delà de 3 ans, une coquille.
PASSE_MAX = timedelta(days=1)
FUTUR_MAX = timedelta(days=3 * 366)
_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True)
class Preparation:
    """Ce qui sera écrit, validé : arguments de la coque + récapitulatif lisible."""

    type: str  # "rappel" | "evenement"
    args_coque: tuple[str, ...]
    resume: str


def _texte(v: object, n: int, lignes: bool = False) -> str:
    s = _CONTROLE.sub(" ", str(v or ""))
    if not lignes:
        s = " ".join(s.split())
    return s.strip()[:n]


def _quand(v: object, now: datetime) -> tuple[datetime, bool] | str:
    """``AAAA-MM-JJ`` ou ``AAAA-MM-JJTHH:MM`` (heure locale si sans fuseau) →
    (date-heure locale naïve, avec_heure), sinon le motif du refus."""
    s = str(v or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            dt, avec_heure = datetime.fromisoformat(s), False
        else:
            dt, avec_heure = datetime.fromisoformat(s), True
    except ValueError:
        return f"date illisible « {s[:40]} » : AAAA-MM-JJ ou AAAA-MM-JJTHH:MM"
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    if dt < now - PASSE_MAX:
        return f"date déjà passée ({dt:%d/%m/%Y}) — maintenant : {now:%d/%m/%Y %H:%M}"
    if dt > now + FUTUR_MAX:
        return f"date trop lointaine ({dt:%d/%m/%Y})"
    return dt, avec_heure


def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def preparer_rappel(titre: str, echeance: str = "", liste: str = "", notes: str = "",
                    maintenant: datetime | None = None) -> Preparation | str:
    """Valide un rappel de l'app Rappels ; rend la préparation ou le motif du refus."""
    now = maintenant or datetime.now()
    t = _texte(titre, TITRE_MAX)
    if not t:
        return "titre du rappel vide"
    args = ["--creer-rappel", "--titre", t]
    quand = ""
    if str(echeance or "").strip():
        q = _quand(echeance, now)
        if isinstance(q, str):
            return q
        dt, avec_heure = q
        args += ["--echeance", _iso(dt) if avec_heure else f"{dt:%Y-%m-%d}"]
        quand = f" — {_jour(dt.date())}" + (f" {dt:%H:%M}" if avec_heure else "")
    li = _texte(liste, 60)
    if li:
        args += ["--liste", li]
    n = _texte(notes, NOTES_MAX, lignes=True)
    if n:
        args += ["--notes", n]
    resume = (f"✅ Créer le rappel « {t} »{quand}"
              + (f" (liste {li})" if li else " (liste par défaut)")
              + (f"\nNotes : {n}" if n else ""))
    return Preparation("rappel", tuple(args), resume)


def preparer_evenement(titre: str, debut: str, fin: str = "", duree_min: int = 60,
                       lieu: str = "", calendrier: str = "", journee: bool = False,
                       notes: str = "", maintenant: datetime | None = None) -> Preparation | str:
    """Valide un événement du Calendrier ; rend la préparation ou le motif du refus."""
    now = maintenant or datetime.now()
    t = _texte(titre, TITRE_MAX)
    if not t:
        return "titre de l'événement vide"
    q = _quand(debut, now)
    if isinstance(q, str):
        return "début : " + q
    d, avec_heure = q
    journee = bool(journee) or not avec_heure
    if str(fin or "").strip():
        qf = _quand(fin, now)
        if isinstance(qf, str):
            return "fin : " + qf
        f = qf[0]
    elif journee:
        f = d
    else:
        try:
            minutes = int(duree_min or 60)
        except (TypeError, ValueError):
            return "durée illisible"
        if not 5 <= minutes <= 24 * 60:
            return "durée hors bornes (5 min à 24 h)"
        f = d + timedelta(minutes=minutes)
    if journee:
        d = datetime.combine(d.date(), datetime.min.time())
        f = datetime.combine(f.date(), datetime.min.time()).replace(hour=23, minute=59)
    if f < d:
        return "la fin précède le début"
    args = ["--creer-evenement", "--titre", t, "--debut", _iso(d), "--fin", _iso(f)]
    if journee:
        args.append("--journee")
    li, cal = _texte(lieu, 120), _texte(calendrier, 60)
    if li:
        args += ["--lieu", li]
    if cal:
        args += ["--calendrier", cal]
    n = _texte(notes, NOTES_MAX, lignes=True)
    if n:
        args += ["--notes", n]
    if journee:
        quand = _jour(d.date()) + ("" if f.date() == d.date() else f" → {_jour(f.date())}") + ", journée"
    elif f.date() == d.date():
        quand = f"{_jour(d.date())} {d:%H:%M}–{f:%H:%M}"
    else:
        quand = f"{_jour(d.date())} {d:%H:%M} → {_jour(f.date())} {f:%H:%M}"
    resume = (f"📅 Créer l'événement « {t} » — {quand}"
              + (f" ({li})" if li else "")
              + (f", calendrier {cal}" if cal else ", calendrier par défaut")
              + (f"\nNotes : {n}" if n else ""))
    return Preparation("evenement", tuple(args), resume)


def executer(prep: Preparation, run: Run = lancer, dossier: Path = DOSSIER_SORTIE,
             sous_coque: bool | None = None, app: Path | None = None) -> str:
    """Écrit la préparation par la coque ; le résultat est NOMMÉ, jamais muet."""
    app = app or _app()
    quoi = "Rappel" if prep.type == "rappel" else "Événement"
    if not _binaire(app).exists():
        return f"❌ {quoi} non créé : coque KlodyMessages absente ({app})"
    try:
        r = _coque(list(prep.args_coque), run, dossier, sous_coque, app, DELAI_CREATION_S)
    except FileNotFoundError:
        return (f"❌ {quoi} non créé : la coque n'a rien écrit "
                "(accès Calendriers/Rappels refusé, ou coque à reconstruire ?)")
    except Exception as e:
        logger.warning("[agenda] création : %s", e)
        return f"❌ {quoi} non créé : {type(e).__name__}"
    if not r.get("ok"):
        return f"❌ {quoi} non créé : {r.get('erreur') or 'erreur inconnue'}"
    return f"✅ {quoi} créé : « {r.get('titre', '')} » ({r.get('liste') or 'par défaut'})."


def creer_rappel(titre: str, echeance: str = "", liste: str = "", notes: str = "",
                 run: Run = lancer, maintenant: datetime | None = None) -> str:
    """Outil ``creer_rappel`` (validé en amont par l'humain)."""
    p = preparer_rappel(titre, echeance, liste, notes, maintenant)
    return f"Rappel refusé : {p}." if isinstance(p, str) else executer(p, run)


def creer_evenement(titre: str, debut: str, fin: str = "", duree_min: int = 60,
                    lieu: str = "", calendrier: str = "", journee: bool = False,
                    notes: str = "", run: Run = lancer,
                    maintenant: datetime | None = None) -> str:
    """Outil ``creer_evenement`` (validé en amont par l'humain)."""
    p = preparer_evenement(titre, debut, fin, duree_min, lieu, calendrier, journee, notes,
                           maintenant)
    return f"Événement refusé : {p}." if isinstance(p, str) else executer(p, run)


# ── Météo ────────────────────────────────────────────────────────────────────

def geocoder(lieu: str, get: Callable[[str], bytes] = http_get) -> tuple[str, float, float]:
    q = urllib.parse.urlencode({"name": lieu, "count": 1, "language": "fr", "format": "json"})
    res = json.loads(get(f"https://geocoding-api.open-meteo.com/v1/search?{q}")).get("results") or []
    if not res:
        raise LookupError(f"lieu inconnu : {lieu}")
    r = res[0]
    nom = r.get("name", lieu) + (f" ({r['admin1']})" if r.get("admin1") else "")
    return nom, float(r["latitude"]), float(r["longitude"])


def previsions(get: Callable[[str], bytes], nom: str, lat: float, lon: float,
               jours: int, aujourdhui: date) -> str:
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "Europe/Paris", "forecast_days": jours,
        "daily": "weather_code,temperature_2m_min,temperature_2m_max,"
                 "precipitation_probability_max,precipitation_sum,wind_speed_10m_max"})
    d = json.loads(get(f"https://api.open-meteo.com/v1/forecast?{q}"))["daily"]
    lignes = [f"🌤 {nom}"]
    for i in range(min(jours, len(d["weather_code"]))):
        j = aujourdhui + timedelta(days=i)
        etiquette = "aujourd'hui" if i == 0 else "demain" if i == 1 else _jour(j)
        ligne = (f"• {etiquette} : {WMO.get(int(d['weather_code'][i]), 'temps variable')}, "
                 f"{d['temperature_2m_min'][i]:.0f}→{d['temperature_2m_max'][i]:.0f} °C")
        pluie = (d.get("precipitation_probability_max") or [None] * (i + 1))[i]
        if pluie:
            ligne += f", pluie {pluie:.0f} % ({d['precipitation_sum'][i]:.1f} mm)"
        vent = (d.get("wind_speed_10m_max") or [None] * (i + 1))[i]
        if vent and vent >= 40:
            ligne += f", vent jusqu'à {vent:.0f} km/h"
        lignes.append(ligne)
    return "\n".join(lignes)


def meteo(lieu: str = "", jours: int = 2, get: Callable[[str], bytes] = http_get,
          maintenant: datetime | None = None) -> str:
    """Outil ``meteo`` : Baulon et Le Rheu par défaut, sinon la ville demandée."""
    jours = max(1, min(METEO_JOURS_MAX, int(jours or 2)))
    aujourdhui = (maintenant or datetime.now()).date()
    lieu = (lieu or "").strip()
    if lieu and not _LIEU_OK.match(lieu):
        return "Lieu refusé : donne un simple nom de ville (lettres, espaces, tirets)."
    try:
        lieux = [geocoder(lieu, get)] if lieu else list(LIEUX)
    except LookupError as e:
        return str(e)
    except Exception as e:
        logger.warning("[meteo] géocodage %s : %s", lieu, e)
        return f"Météo indisponible (géocodage : {type(e).__name__})."
    blocs = []
    for nom, lat, lon in lieux:
        try:
            blocs.append(previsions(get, nom, lat, lon, jours, aujourdhui))
        except Exception as e:
            logger.warning("[meteo] %s : %s", nom, e)
            blocs.append(f"🌤 {nom} : indisponible ({type(e).__name__})")
    return "\n\n".join(blocs)


# ── Briefing à la demande ────────────────────────────────────────────────────

MOMENTS = ("matin", "midi", "soir")


def briefing_maintenant(dossier: Path | None = None, moment: str = "") -> str:
    """Dépose ``briefing.maintenant`` (même contrat que ``klody-notifier.py
    --briefing``) ; le canal iMessage le relève sous une minute."""
    if dossier is None:
        # Lu À L'APPEL, pas à l'import : les tests redirigent l'état, et CodeQL
        # (alertes 334/335, 2026-11-26) refusait la valeur figée à l'import.
        import config

        dossier = Path(config.MEMORY_DIR)
    moment = (moment or "").strip().lower()
    if moment and moment not in MOMENTS:
        return f"Moment inconnu « {moment} » : matin, midi ou soir."
    f = dossier / "briefing.maintenant"
    if f.exists():
        return "Un briefing est déjà demandé : il part sous une minute."
    dossier.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(moment)
    tmp.replace(f)
    return ("Briefing demandé" + (f" ({moment})" if moment else "")
            + " : il arrive par iMessage sous une minute, si le canal Klody tourne.")
