"""Tests du mode raisonnement (thinking) : câblage stream_chat + routage.

Le brain Qwen3 émet un CoT (`delta.reasoning`) AVANT la réponse quand
`chat_template_kwargs.enable_thinking=true`. On vérifie que stream_chat :
- pose extra_body + élargit max_tokens UNIQUEMENT si enable_thinking ;
- capte le reasoning sans le confondre avec le content, sans planter si le CoT
  est seul ;
et que l'orchestrateur n'active le thinking que sur le brain pour explain/hard.
"""
from types import SimpleNamespace

import pytest
from agent.llm import LLMClient
from config import THINKING_MAX_TOKENS

# ── Faux stream OpenAI ──────────────────────────────────────────────────────


class _Delta(SimpleNamespace):
    """Delta minimal : attributs content / reasoning / tool_calls / model_extra."""

    def __init__(self, content=None, reasoning=None, tool_calls=None, model_extra=None):
        super().__init__(
            content=content, reasoning=reasoning,
            tool_calls=tool_calls, model_extra=model_extra or {},
        )


def _chunk(delta):
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.captured: dict = {}

    def create(self, **params):
        self.captured.update(params)
        return iter(self._chunks)


def _make_client(chunks):
    c = LLMClient.__new__(LLMClient)
    c.model = "brain"
    c.total_tokens = 0
    c._backend = "mlx"
    c.client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(chunks)))
    return c


# ── _delta_reasoning ────────────────────────────────────────────────────────


class TestDeltaReasoning:
    def test_depuis_attribut_reasoning(self):
        assert LLMClient._delta_reasoning(_Delta(reasoning="je réfléchis")) == "je réfléchis"

    def test_depuis_model_extra(self):
        d = _Delta(reasoning=None, model_extra={"reasoning": "via extra"})
        assert LLMClient._delta_reasoning(d) == "via extra"

    def test_absent_retourne_vide(self):
        assert LLMClient._delta_reasoning(_Delta(content="réponse")) == ""


# ── stream_chat : câblage du flag ───────────────────────────────────────────


class TestStreamChatThinking:
    def test_enable_thinking_pose_extra_body_et_booste_tokens(self):
        chunks = [_chunk(_Delta(reasoning="hmm ")), _chunk(_Delta(content="42"))]
        client = _make_client(chunks)
        content, tools = client.stream_chat(
            [{"role": "user", "content": "q"}], silent=True,
            enable_thinking=True, max_tokens=1024,
        )
        cap = client.client.chat.completions.captured
        assert cap["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}
        assert cap["max_tokens"] == THINKING_MAX_TOKENS  # max(1024, 8192) → boost
        assert content == "42"  # le reasoning n'est PAS dans le content
        assert tools is None

    def test_sans_thinking_pas_d_extra_body(self):
        chunks = [_chunk(_Delta(content="ok"))]
        client = _make_client(chunks)
        client.stream_chat([{"role": "user", "content": "q"}], silent=True, max_tokens=8192)
        cap = client.client.chat.completions.captured
        assert "extra_body" not in cap
        assert cap["max_tokens"] == 8192

    def test_reasoning_seul_ne_plante_pas_et_content_vide(self):
        # Cas probe : le CoT consomme tout, aucune réponse.
        chunks = [_chunk(_Delta(reasoning="r1 ")), _chunk(_Delta(reasoning="r2"))]
        client = _make_client(chunks)
        content, tools = client.stream_chat(
            [{"role": "user", "content": "q"}], silent=True, enable_thinking=True,
        )
        assert content == ""
        assert tools is None

    def test_thinking_booste_seulement_si_inferieur(self):
        # max_tokens déjà > THINKING_MAX_TOKENS → on ne le réduit pas.
        chunks = [_chunk(_Delta(content="x"))]
        client = _make_client(chunks)
        big = THINKING_MAX_TOKENS + 5000
        client.stream_chat(
            [{"role": "user", "content": "q"}], silent=True,
            enable_thinking=True, max_tokens=big,
        )
        assert client.client.chat.completions.captured["max_tokens"] == big


# ── Orchestrator._should_think ──────────────────────────────────────────────


def _orch():
    from agent.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)
    o._code_model_active = False
    o._interactive_skill_active = False
    o.last_routing = None
    return o


def _routing(task_type="explain", difficulty="medium"):
    return SimpleNamespace(task_type=task_type, difficulty=difficulty)


class TestShouldThink:
    def test_explain_active(self, monkeypatch):
        # `explain` est le seul task_type qui reste sur le brain → c'est LE cas où
        # le thinking fire réellement (le TTFT aveugle est désormais corrigé en
        # diffusant le CoT à l'UI). Gate large justifié.
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o.last_routing = _routing(task_type="explain", difficulty="easy")
        assert o._should_think() is True

    def test_hard_active(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o.last_routing = _routing(task_type="feature", difficulty="hard")
        assert o._should_think() is True

    def test_explain_hard_active(self, monkeypatch):
        # `explain` ET `hard` → actif (les deux conditions concordent).
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o.last_routing = _routing(task_type="explain", difficulty="hard")
        assert o._should_think() is True

    def test_medium_edit_inactif(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o.last_routing = _routing(task_type="edit", difficulty="medium")
        assert o._should_think() is False

    def test_coder_jamais(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o._code_model_active = True
        o.last_routing = _routing(task_type="explain", difficulty="hard")
        assert o._should_think() is False

    def test_skill_interactif_jamais(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o._interactive_skill_active = True
        o.last_routing = _routing(task_type="explain", difficulty="hard")
        assert o._should_think() is False

    def test_flag_global_off(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", False)
        o = _orch()
        o.last_routing = _routing(task_type="explain", difficulty="hard")
        assert o._should_think() is False

    def test_sans_routing_inactif(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        assert o._should_think() is False
