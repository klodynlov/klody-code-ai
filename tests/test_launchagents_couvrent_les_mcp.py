"""Tout serveur MCP lançable depuis ce dépôt a son agent launchd versionné.

`launchagents/README.md` nomme le mode de panne que ce dossier ferme : « le
script de service présent et l'agent qui le déclenche absent — le service ne
démarrait jamais, sans la moindre erreur ». Rien ne vérifiait la règle, et
`reaper` y est passé au travers : `.env` le déclare consommé sur `:8089`,
`scripts/start-reaper-mcp.sh` existe, aucun agent ne le démarrait, et Klody
perdait tous ses outils REAPER à chaque boot en silence (`config.py` :
« un serveur injoignable est ignoré au boot »).

Un mode de panne silencieux qui n'a pas de test finit par revenir.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LANCEURS = REPO / "scripts"
AGENTS = REPO / "launchagents"

# Lanceurs MCP sans agent, et pourquoi. Ce n'est PAS une liste où l'on range ce
# qu'on n'a pas eu le temps de faire : chaque entrée doit porter une raison, et
# l'intention est qu'elle se vide.
#
# Elle est VIDE depuis le 2026-08-03 : `gadget` était la dernière exception —
# même situation que reaper (déclaré dans KLODY_MCP_SERVERS sur :8093, lanceur
# présent, agent absent), son agent a été créé.
SANS_AGENT: set[str] = set()


def _nom_du_service(lanceur: Path) -> str:
    """`scripts/start-reaper-mcp.sh` -> `reaper`."""
    m = re.fullmatch(r"start-(.+)-mcp\.sh", lanceur.name)
    assert m, lanceur.name
    return m.group(1)


def _lanceurs_mcp() -> list[Path]:
    trouves = sorted(LANCEURS.glob("start-*-mcp.sh"))
    assert trouves, "aucun lanceur MCP trouvé — le motif de nommage a changé"
    return trouves


def _agents() -> set[str]:
    trouves = {p.stem.removeprefix("com.klody.") for p in AGENTS.glob("*.plist")}
    assert trouves, "aucun agent versionné trouvé — le dossier a changé de place"
    return trouves


@pytest.mark.parametrize("lanceur", _lanceurs_mcp(), ids=_nom_du_service)
def test_chaque_lanceur_mcp_a_son_agent(lanceur):
    service = _nom_du_service(lanceur)
    if service in SANS_AGENT:
        pytest.skip(f"{service} : exception documentée dans SANS_AGENT")
    assert f"{service}-mcp" in _agents(), (
        f"{lanceur.name} n'a pas d'agent launchd versionné.\n"
        f"Sans lui, le service ne démarre jamais et rien ne le signale — "
        f"créer launchagents/com.klody.{service}-mcp.plist, ou l'inscrire "
        f"dans SANS_AGENT avec sa raison."
    )


def test_la_liste_d_exceptions_ne_grossit_pas_en_douce():
    """Verrouille l'état connu : une exception de plus doit être un choix
    explicite, pas un effet de bord d'un ajout de serveur."""
    assert set() == SANS_AGENT


def test_aucune_exception_ne_masque_un_agent_existant():
    """Une entrée de `SANS_AGENT` dont l'agent existe est un résidu : elle
    ferait passer le test au vert pour une mauvaise raison."""
    agents = _agents()
    residus = {s for s in SANS_AGENT if f"{s}-mcp" in agents}
    assert not residus, f"à retirer de SANS_AGENT, leur agent existe : {residus}"


def test_aucun_agent_orphelin():
    """Un agent sans lanceur pointerait vers un script disparu."""
    services = {_nom_du_service(p) for p in _lanceurs_mcp()}
    orphelins = {
        a.removesuffix("-mcp") for a in _agents()
        if a.endswith("-mcp") and a.removesuffix("-mcp") not in services
    }
    assert not orphelins, f"agents MCP sans lanceur : {orphelins}"
