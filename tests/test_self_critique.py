"""Tests de l'auto-critique (Levier 3) : Orchestrator._maybe_self_critique.

Conservatrice et flag-gée : ne tourne que sur explain/hard (brain), réécrit la
réponse seulement si la relecture le justifie (sinon sentinel INCHANGÉ), et reste
silencieuse/non bloquante en cas d'erreur. OFF par défaut."""
from types import SimpleNamespace

import pytest


def _make_orch(*, task="explain", diff="hard", draft="D" * 250,
               revised="INCHANGÉ", raises=False, code=False,
               interactive=False, done=False):
    from agent.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)
    o._code_model_active = code
    o._interactive_skill_active = interactive
    o._self_critique_done = done
    o.last_routing = SimpleNamespace(task_type=task, difficulty=diff)
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": draft},
    ]
    o.memory = SimpleNamespace(
        messages=msgs,
        get_messages_for_api=lambda: list(msgs),
        save=lambda: None,
    )
    calls: list = []

    def _fake_stream(messages, tools=None, silent=False, enable_thinking=False):
        calls.append(messages)
        if raises:
            raise RuntimeError("llm indisponible")
        return (revised, None)

    o.llm = SimpleNamespace(stream_chat=_fake_stream)
    o._calls = calls
    return o


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr("agent.orchestrator.SELF_CRITIQUE_ENABLED", True)


class TestGardes:
    def test_off_par_defaut_aucun_appel(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.SELF_CRITIQUE_ENABLED", False)
        o = _make_orch()
        o._maybe_self_critique("D" * 250)
        assert o._calls == []

    def test_coder_actif_skip(self):
        o = _make_orch(code=True)
        o._maybe_self_critique("D" * 250)
        assert o._calls == []

    def test_skill_interactif_skip(self):
        o = _make_orch(interactive=True)
        o._maybe_self_critique("D" * 250)
        assert o._calls == []

    def test_deja_fait_skip(self):
        o = _make_orch(done=True)
        o._maybe_self_critique("D" * 250)
        assert o._calls == []

    def test_task_type_non_raisonnement_skip(self):
        o = _make_orch(task="edit", diff="medium")
        o._maybe_self_critique("D" * 250)
        assert o._calls == []

    def test_reponse_trop_courte_skip(self):
        o = _make_orch(draft="court")
        o._maybe_self_critique("court")
        assert o._calls == []


class TestComportement:
    def test_sentinel_inchange_garde_le_brouillon(self):
        o = _make_orch(revised="INCHANGÉ")
        o._maybe_self_critique("D" * 250)
        assert len(o._calls) == 1                      # critique appelée
        assert o.memory.messages[-1]["content"] == "D" * 250  # brouillon conservé

    def test_revision_remplace_la_reponse(self):
        o = _make_orch(revised="Voici la version corrigée et complète de la réponse.")
        o._maybe_self_critique("D" * 250)
        assert o.memory.messages[-1]["content"].startswith("Voici la version corrigée")

    def test_inchange_insensible_a_la_casse_et_accents(self):
        o = _make_orch(revised="inchange")
        o._maybe_self_critique("D" * 250)
        assert o.memory.messages[-1]["content"] == "D" * 250

    def test_erreur_llm_silencieuse(self):
        o = _make_orch(raises=True)
        o._maybe_self_critique("D" * 250)  # ne lève pas
        assert o.memory.messages[-1]["content"] == "D" * 250  # brouillon intact

    def test_critique_en_mode_silencieux(self):
        # La critique ne doit pas re-streamer du texte à l'UI (silent=True implicite
        # via stream_chat) : on vérifie que la consigne de critique est bien posée.
        o = _make_orch(revised="INCHANGÉ")
        o._maybe_self_critique("D" * 250)
        last_msg = o._calls[0][-1]
        assert "INCHANGÉ" in last_msg["content"]  # le prompt de critique contient le sentinel
