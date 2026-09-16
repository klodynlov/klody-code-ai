"""Bibliothèques SampleBrain déclarées — UNE lecture de `SAMPLEBRAIN_URLS`.

Partagée par `samplebrain_server.py` (recherche exposée comme domaine) et
`reaper_samples.py` (placement dans REAPER) : depuis MISSION-D 6.2 les deux
passent par HTTP et doivent voir les MÊMES index — c'est le sens de « un seul
chemin d'accès ». Aucune dépendance : lisible depuis n'importe quel serveur.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAUT = {"principale": "http://127.0.0.1:8788"}


def lire_bibliotheques(env: dict | None = None) -> dict[str, str]:
    """{nom: url} depuis `SAMPLEBRAIN_URLS` (`nom=url,nom=url`).

    Repli sur `SAMPLEBRAIN_URL` seul, sous le nom `principale` : la variable
    historique reste donc valide telle quelle. Conf vide ou bancale → défaut
    utilisable, jamais un dict vide (un outil muet sans raison est indiagnosticable).
    """
    env = os.environ if env is None else env
    brut = (env.get("SAMPLEBRAIN_URLS") or "").strip()
    if not brut:
        seule = env.get("SAMPLEBRAIN_URL", DEFAUT["principale"])
        return {"principale": seule.rstrip("/")}
    out: dict[str, str] = {}
    for morceau in brut.split(","):
        morceau = morceau.strip()
        if not morceau:
            continue
        nom, _, url = morceau.partition("=")
        nom, url = nom.strip(), url.strip().rstrip("/")
        if not nom or not url:
            logger.warning("SAMPLEBRAIN_URLS : entrée ignorée (%r)", morceau)
            continue
        out[nom] = url
    return out or dict(DEFAUT)


__all__ = ["DEFAUT", "lire_bibliotheques"]
