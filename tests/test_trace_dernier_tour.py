"""Les drapeaux de garde : remis à zéro même sur un tour interrompu, et LUS avant.

Deux défauts d'un même endroit (audit du 2026-09-27) :

1. La remise à zéro vivait à la fin de `run()`, hors `finally`. En CLI
   l'orchestrateur sert tous les messages : un Ctrl+C ou une erreur LLM en plein
   tour laissait `_anti_stall_fired` & co. levés pour le message suivant.
2. `bench/run.py` lisait `_doc_guard_fired` APRÈS `run()` — donc toujours False.
   L'instrument du coût du garde doc (lot 2.2) ne pouvait pas le voir tirer ; ses
   tests ne vérifiaient que la sérialisation de `Result` fabriqués à la main.
"""

from __future__ import annotations

from pathlib import Path

import bench.run as banc
import pytest
from agent.orchestrator import Orchestrator
from bench.framework import Task


def _orch_dont_le_tour(fait) -> Orchestrator:
    o = Orchestrator.__new__(Orchestrator)
    o._run_tour = fait  # type: ignore[method-assign]
    return o


def test_tour_interrompu_ne_laisse_aucun_drapeau_leve():
    o = None

    def tour_coupe(_msg):
        o._anti_stall_fired = True
        o._t2a_fired = True
        o._doc_guard_fired = True
        raise KeyboardInterrupt

    o = _orch_dont_le_tour(tour_coupe)
    with pytest.raises(KeyboardInterrupt):
        o.run("salut")
    assert (o._anti_stall_fired, o._t2a_fired, o._doc_guard_fired) == (False, False, False)
    assert o.trace_dernier_tour["doc_guard_fired"] is True


def test_la_trace_survit_a_la_remise_a_zero():
    o = None

    def tour(_msg):
        o._doc_guard_fired = True
        o._doc_consulte = True

    o = _orch_dont_le_tour(tour)
    o.run("écris la fonction")
    assert o._doc_guard_fired is False
    assert o.trace_dernier_tour == {"doc_guard_fired": True, "doc_consulte": True}


class _TacheBidon(Task):
    id = "real_repo/bidon"
    category = "real_repo"
    prompt = "écris x"

    def setup(self, workdir: Path) -> None:
        pass

    def validate(self, workdir: Path) -> tuple[bool, str]:
        return True, "ok"


def test_le_banc_enregistre_un_garde_qui_a_tire(monkeypatch):
    """Chemin RÉEL de capture : `_run_one_inprocess` sur un orchestrateur dont
    le tour a déclenché le garde. Avant le correctif : False."""

    def faux_run_klody(_prompt, _workdir):
        o = None

        def tour(_msg):
            o._doc_guard_fired = True
            o._doc_consulte = True

        o = _orch_dont_le_tour(tour)
        o.run(_prompt)
        return o

    monkeypatch.setattr(banc, "_run_klody", faux_run_klody)
    r = banc._run_one_inprocess(_TacheBidon)
    assert r.doc_guard_fired is True
    assert r.doc_consulte is True
