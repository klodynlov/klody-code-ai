"""Rappels juste avant : rendez-vous de l'agenda et échéances de l'app Rappels.

Troisième brique de « Klody écrit le premier » (demandée le 2026-11-22, après le
moteur #333 et le briefing #334) :

- **rendez-vous** : un message ``KLODY_RDV_AVANCE_MIN`` minutes avant le début
  (30 par défaut) de chaque événement HORAIRE de l'app Calendrier — les
  événements « journée » sont déjà dans le briefing ;
- **app Rappels** : un message à l'échéance de chaque rappel AVEC heure (ceux
  sans heure sont listés au briefing).

Lecture par la coque (``KlodyMessages --agenda 2``, EventKit, mêmes droits TCC
que le briefing) : rien de neuf à autoriser. Hors quota — ce sont TES rendez-vous
et TES rappels. Une clé par occurrence (``rdv:<id>:<début>``) : un événement
déplacé est rappelé à sa nouvelle heure, jamais deux fois à la même.

Mac endormi : un rendez-vous déjà commencé n'est plus annoncé, un rappel échu
depuis plus de ``RETARD_MAX_MIN`` minutes non plus (le briefing les reprend) —
un « dans 30 min » reçu après coup serait faux.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from canaux.briefing import agenda, lancer
from canaux.proactif import Alerte

logger = logging.getLogger("klody.rdv")

AVANCE_MIN = 30
RETARD_MAX_MIN = 30
DELAI_AGENDA_S = 20.0  # plus court que le briefing : ne pas bloquer les rappels dictés


def _date(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _dans(minutes: float) -> str:
    m = max(1, round(minutes))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d}" if m % 60 else f"{m // 60} h"


def alertes_agenda(d: dict, now: datetime, avance_min: int = AVANCE_MIN,
                   retard_max_min: int = RETARD_MAX_MIN) -> list[Alerte]:
    out: list[Alerte] = []
    horizon = now + timedelta(minutes=avance_min)
    for e in d.get("evenements", []):
        if e.get("journee"):
            continue
        debut, fin = _date(e.get("debut", "")), _date(e.get("fin", ""))
        if debut is None or not (now < debut <= horizon):
            continue
        texte = f"📅 Dans {_dans((debut - now).total_seconds() / 60)} : {e.get('titre') or '(sans titre)'}"
        texte += f"\n{debut:%H:%M}" + (f"–{fin:%H:%M}" if fin else "")
        if e.get("lieu"):
            texte += f" · {e['lieu'].splitlines()[0][:60]}"
        out.append(Alerte(f"rdv:{e.get('id') or e.get('titre')}:{debut:%Y-%m-%dT%H:%M}", texte,
                          "rendez_vous", hors_quota=True))
    for r in d.get("rappels", []):
        if not r.get("avec_heure"):
            continue
        ech = _date(r.get("echeance", ""))
        if ech is None or not (ech <= now < ech + timedelta(minutes=retard_max_min)):
            continue
        out.append(Alerte(f"rappel_app:{r.get('id') or r.get('titre')}:{ech:%Y-%m-%dT%H:%M}",
                          f"✅ Rappel : {r.get('titre') or '(sans titre)'} ({ech:%H:%M})",
                          "rendez_vous", hors_quota=True))
    return out


def source_rendez_vous(lanceur: Path | None,
                       run: Callable[[list[str], float], str] = lancer,
                       avance_min: int = AVANCE_MIN) -> Callable[[float], list[Alerte]]:
    def run_court(argv: list[str], timeout: float) -> str:
        return run(argv, min(timeout, DELAI_AGENDA_S))

    def collecter(now: float) -> list[Alerte]:
        if lanceur is None:
            return []
        d = agenda(lanceur, run_court, jours=2)
        if d.get("erreurs"):
            logger.warning("[rdv] agenda partiel : %s", "; ".join(map(str, d["erreurs"])))
        return alertes_agenda(d, datetime.fromtimestamp(now), avance_min)

    return collecter
