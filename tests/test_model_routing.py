"""Routage modèle code : LLMClient.switch_to + Orchestrator._route_model.

Les tâches de code (edit/refactor/bug_fix/feature/self_dev) sont routées vers le
modèle coder dédié ; `explain` reste sur le généraliste. Voir config.CODE_MODEL.
"""

from unittest.mock import MagicMock

from agent.llm import LLMClient
from agent.orchestrator import Orchestrator

# ── LLMClient.switch_to ───────────────────────────────────────────────────────


class TestSwitchTo:
    def _client(self, model: str) -> LLMClient:
        c = LLMClient.__new__(LLMClient)  # sans __init__ (pas de client réseau)
        c.model = model
        c.client = object()  # sentinelle
        return c

    def test_bascule_change_modele_et_recree_client(self):
        c = self._client("general")
        old_client = c.client
        c.switch_to("coder", "http://localhost:8081/v1", "mlx")
        assert c.model == "coder"
        assert c.client is not old_client

    def test_no_op_si_meme_modele(self):
        c = self._client("coder")
        sentinel = object()
        c.client = sentinel
        c.switch_to("coder", "http://localhost:8081/v1", "mlx")
        assert c.client is sentinel  # client NON recréé


# ── Orchestrator._route_model ─────────────────────────────────────────────────


def _fake_orch(model: str) -> MagicMock:
    """Faux orchestrateur minimal : un .llm dont switch_to mute le modèle."""
    o = MagicMock()
    o.llm.model = model

    def _switch(m, b, k):
        o.llm.model = m

    o.llm.switch_to.side_effect = _switch
    return o


def _set_models(monkeypatch, *, code="coder-30b", general="general-35b"):
    monkeypatch.setattr("agent.orchestrator.CODE_MODEL", code)
    monkeypatch.setattr("agent.orchestrator.CODE_BASE_URL", "http://localhost:8081/v1")
    monkeypatch.setattr("agent.orchestrator.CODE_API_KEY", "mlx")
    monkeypatch.setattr("agent.orchestrator.LLM_MODEL", general)
    monkeypatch.setattr("agent.orchestrator.LLM_BASE_URL", "http://localhost:8080/v1")
    monkeypatch.setattr("agent.orchestrator.LLM_API_KEY", "mlx")


class TestRouteModel:
    def test_tache_code_va_au_coder(self, monkeypatch):
        _set_models(monkeypatch)
        o = _fake_orch("general-35b")
        Orchestrator._route_model(o, "feature")
        o.llm.switch_to.assert_called_once_with("coder-30b", "http://localhost:8081/v1", "mlx")
        assert o._code_model_active is True  # → prompt slim

    def test_bug_fix_et_refactor_aussi(self, monkeypatch):
        _set_models(monkeypatch)
        for tt in ("bug_fix", "refactor", "edit", "self_dev"):
            o = _fake_orch("general-35b")
            Orchestrator._route_model(o, tt)
            o.llm.switch_to.assert_called_once_with("coder-30b", "http://localhost:8081/v1", "mlx")

    def test_explain_revient_au_generaliste(self, monkeypatch):
        _set_models(monkeypatch)
        o = _fake_orch("coder-30b")  # on était sur le coder
        Orchestrator._route_model(o, "explain")
        o.llm.switch_to.assert_called_once_with("general-35b", "http://localhost:8080/v1", "mlx")
        assert o._code_model_active is False  # → prompt agentique normal

    def test_no_op_si_pas_de_modele_code(self, monkeypatch):
        _set_models(monkeypatch, code="")
        o = _fake_orch("general-35b")
        Orchestrator._route_model(o, "feature")
        o.llm.switch_to.assert_not_called()

    def test_respecte_un_choix_manuel_de_modele(self, monkeypatch):
        # L'utilisateur a sélectionné un 3e modèle dans l'UI → on n'y touche pas.
        _set_models(monkeypatch)
        o = _fake_orch("un-modele-choisi-a-la-main")
        Orchestrator._route_model(o, "feature")
        o.llm.switch_to.assert_not_called()
        assert o._code_model_active is False


# ── Prompt slim injecté quand le coder est actif ──────────────────────────────


class TestSlimPromptCoder:
    def test_coder_actif_injecte_un_prompt_slim(self):
        o = MagicMock()
        o._code_model_active = True
        o.memory.messages = []
        o._on_skills_selected = None
        Orchestrator._inject_system_prompt(o, task_type="feature", query="une horloge")
        sysmsg = o.memory.messages[0]
        assert sysmsg["role"] == "system"
        assert "générateur de code" in sysmsg["content"]
        assert "```html" in sysmsg["content"]
        # Slim = bien plus court que le prompt agentique géant (~12k tokens).
        assert len(sysmsg["content"]) < 2000
        assert o._injected_skill_slugs == []
