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
from config import (
    THINKING_BUDGET_HIGH,
    THINKING_BUDGET_LOW,
    THINKING_BUDGET_MED,
    THINKING_MAX_TOKENS,
)

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
    def test_enable_thinking_pose_extra_body_et_booste_tokens(self, monkeypatch):
        # Penalty neutralisée : on teste le contrat thinking seul (la forme exacte
        # d'extra_body avec penalty active est couverte par TestRepetitionPenalty).
        monkeypatch.setattr("agent.llm.LLM_REPETITION_PENALTY", 1.0)
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

    def test_sans_thinking_pas_d_extra_body(self, monkeypatch):
        monkeypatch.setattr("agent.llm.LLM_REPETITION_PENALTY", 1.0)
        chunks = [_chunk(_Delta(content="ok"))]
        client = _make_client(chunks)
        client.stream_chat([{"role": "user", "content": "q"}], silent=True, max_tokens=8192)
        cap = client.client.chat.completions.captured
        assert "extra_body" not in cap
        assert cap["max_tokens"] == 8192


# ── stream_chat : thinking budget PAR REQUÊTE (additif, inerte si non fourni) ─


class TestStreamChatThinkingBudget:
    def test_budget_ne_module_PAS_max_tokens(self, monkeypatch):
        # Forward-compat : le budget par-tâche ne touche pas max_tokens (le plafond
        # ne sait qu'élargir). Comportement historique : boost au global THINKING_MAX_TOKENS.
        monkeypatch.setattr("agent.llm.LLM_REPETITION_PENALTY", 1.0)
        client = _make_client([_chunk(_Delta(content="ok"))])
        client.stream_chat(
            [{"role": "user", "content": "q"}], silent=True,
            enable_thinking=True, max_tokens=256, thinking_budget=2048,
        )
        cap = client.client.chat.completions.captured
        assert cap["max_tokens"] == THINKING_MAX_TOKENS  # max(256, 8192), budget ignoré

    def test_thinking_budget_forwarde_dans_chat_template_kwargs(self, monkeypatch):
        monkeypatch.setattr("agent.llm.LLM_REPETITION_PENALTY", 1.0)
        monkeypatch.setattr("agent.llm.THINKING_BUDGET_FORWARD", True)
        client = _make_client([_chunk(_Delta(content="ok"))])
        client.stream_chat(
            [{"role": "user", "content": "q"}], silent=True,
            enable_thinking=True, thinking_budget=2048,
        )
        cap = client.client.chat.completions.captured
        assert cap["extra_body"]["chat_template_kwargs"] == {
            "enable_thinking": True, "thinking_budget": 2048,
        }

    def test_forward_off_n_envoie_que_enable_thinking(self, monkeypatch):
        monkeypatch.setattr("agent.llm.LLM_REPETITION_PENALTY", 1.0)
        monkeypatch.setattr("agent.llm.THINKING_BUDGET_FORWARD", False)
        client = _make_client([_chunk(_Delta(content="ok"))])
        client.stream_chat(
            [{"role": "user", "content": "q"}], silent=True,
            enable_thinking=True, thinking_budget=2048,
        )
        cap = client.client.chat.completions.captured
        assert cap["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True}

    def test_sans_budget_forme_historique_preservee(self, monkeypatch):
        # Aucun budget passé → contrat byte-identique à l'historique.
        monkeypatch.setattr("agent.llm.LLM_REPETITION_PENALTY", 1.0)
        client = _make_client([_chunk(_Delta(content="ok"))])
        client.stream_chat(
            [{"role": "user", "content": "q"}], silent=True, enable_thinking=True,
        )
        cap = client.client.chat.completions.captured
        assert cap["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


# ── stream_chat : repetition_penalty (filet anti-boucle, opt-in) ─────────────


class TestRepetitionPenalty:
    def test_penalty_active_part_dans_extra_body(self, monkeypatch):
        monkeypatch.setattr("agent.llm.LLM_REPETITION_PENALTY", 1.05)
        client = _make_client([_chunk(_Delta(content="ok"))])
        client.stream_chat([{"role": "user", "content": "q"}], silent=True)
        cap = client.client.chat.completions.captured
        assert cap["extra_body"] == {"repetition_penalty": 1.05}

    def test_penalty_fusionne_avec_thinking(self, monkeypatch):
        monkeypatch.setattr("agent.llm.LLM_REPETITION_PENALTY", 1.05)
        client = _make_client([_chunk(_Delta(content="ok"))])
        client.stream_chat(
            [{"role": "user", "content": "q"}], silent=True, enable_thinking=True,
        )
        cap = client.client.chat.completions.captured
        assert cap["extra_body"] == {
            "repetition_penalty": 1.05,
            "chat_template_kwargs": {"enable_thinking": True},
        }

    def test_penalty_a_1_desactivee(self, monkeypatch):
        # 1.0 = neutre : le param n'est pas envoyé (comportement historique).
        monkeypatch.setattr("agent.llm.LLM_REPETITION_PENALTY", 1.0)
        client = _make_client([_chunk(_Delta(content="ok"))])
        client.stream_chat([{"role": "user", "content": "q"}], silent=True)
        assert "extra_body" not in client.client.chat.completions.captured

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


# ── Orchestrator._thinking_budget : modulation par type de tâche ─────────────


class TestThinkingBudget:
    def test_off_quand_thinking_off(self, monkeypatch):
        # edit/medium → thinking off → budget 0 (pas de CoT).
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o.last_routing = _routing(task_type="edit", difficulty="medium")
        assert o._thinking_budget() == 0

    def test_explain_easy_budget_bas(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o.last_routing = _routing(task_type="explain", difficulty="easy")
        assert o._thinking_budget() == THINKING_BUDGET_LOW

    def test_explain_medium_budget_moyen(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o.last_routing = _routing(task_type="explain", difficulty="medium")
        assert o._thinking_budget() == THINKING_BUDGET_MED

    def test_hard_budget_haut(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o.last_routing = _routing(task_type="feature", difficulty="hard")
        assert o._thinking_budget() == THINKING_BUDGET_HIGH

    def test_coder_budget_nul(self, monkeypatch):
        # Même hard/explain : sur le coder, jamais de CoT → budget 0.
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o._code_model_active = True
        o.last_routing = _routing(task_type="explain", difficulty="hard")
        assert o._thinking_budget() == 0

    def test_modulation_demontree(self, monkeypatch):
        # Budget forwardé modulé par tâche : off(0) < explain/easy(LOW) <
        # explain/medium(MED) < hard(HIGH). (Effet = forward-compat, pas max_tokens.)
        monkeypatch.setattr("agent.orchestrator.THINKING_ENABLED", True)
        o = _orch()
        o.last_routing = _routing(task_type="edit", difficulty="easy")
        off = o._thinking_budget()
        o.last_routing = _routing(task_type="explain", difficulty="easy")
        low = o._thinking_budget()
        o.last_routing = _routing(task_type="explain", difficulty="medium")
        med = o._thinking_budget()
        o.last_routing = _routing(task_type="bug_fix", difficulty="hard")
        high = o._thinking_budget()
        assert off < low < med < high
