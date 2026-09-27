"""L'affichage d'un résultat d'outil ne doit JAMAIS faire tomber le tour.

Audit du 2026-09-27 : Rich interprète le markup, et une sortie d'outil en contient
souvent sans le vouloir — `[/Users/…]`, `[/]`, ou le `[red]…[/red]` d'un fichier
de CE dépôt trouvé par `search_in_files`. `MarkupError` remontait jusqu'à
`run()` : l'outil avait tourné, son résultat n'était jamais enregistré, et le
`tool_call` restait orphelin en mémoire.
"""

from __future__ import annotations

import io

import agent.orchestrator as mod
import pytest
from agent.orchestrateur.outils import _format_search_results
from agent.orchestrator import Orchestrator
from rich.console import Console

PIEGE = 'agent/x.py:12:    console.print("[red]erreur[/] voir [/Users/klodynlov] et [/]")'


@pytest.fixture
def ecran(monkeypatch):
    """Console réelle (markup actif), sortie capturée."""
    tampon = io.StringIO()
    monkeypatch.setattr(mod, "console", Console(file=tampon, width=200))
    return tampon


def _orch(resultat: str) -> Orchestrator:
    o = Orchestrator.__new__(Orchestrator)
    o._execute_tool = lambda _nom, _args: resultat  # type: ignore[method-assign]
    o._sandbox_auto_exec = False
    return o


def test_recherche_dans_un_depot_qui_ecrit_du_markup():
    """Le cas d'origine : chercher « erreur » dans un fichier qui contient `[/]`."""
    tampon = io.StringIO()
    Console(file=tampon, width=200).print(_format_search_results(PIEGE, "erreur"))
    sortie = tampon.getvalue()
    assert "[/Users/klodynlov]" in sortie, "le texte doit s'afficher tel quel, pas être avalé"


@pytest.mark.parametrize("outil", ["search_in_files", "un_outil_mcp_inconnu", "library_catalog"])
def test_le_tour_survit_et_le_resultat_est_rendu(outil, ecran):
    resultat = mod.Orchestrator._execute_and_display(_orch(PIEGE), outil, {"pattern": "erreur", "query": "[/q]"})
    assert resultat == PIEGE


def test_un_rendu_qui_leve_retombe_en_texte_brut(ecran, monkeypatch):
    """Filet : quel que soit le branchement oublié, le tour continue."""
    o = _orch("[/boum]")

    def rendu_casse(*_a, **_kw):
        raise RuntimeError("rendu cassé")

    monkeypatch.setattr(o, "_display_tool_result", rendu_casse)
    assert mod.Orchestrator._execute_and_display(o, "read_file", {"path": "x"}) == "[/boum]"
    assert "[/boum]" in ecran.getvalue()
