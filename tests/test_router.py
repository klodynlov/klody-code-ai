"""Tests pour agent.router — parsing + fallback + dérivation stratégie."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.router import Router, RoutingDecision, _decide_strategy


# ── Stratégie dérivée ────────────────────────────────────────────────────────


class TestStrategie:
    def test_easy_pas_de_planner(self):
        s = _decide_strategy("easy", "edit")
        assert s["max_iterations"] == 6
        assert s["use_planner"] is False
        assert s["use_best_of_n"] is False

    def test_hard_planner_et_best_of_n(self):
        s = _decide_strategy("hard", "bug_fix")
        assert s["max_iterations"] == 25
        assert s["use_planner"] is True
        assert s["use_best_of_n"] is True

    def test_medium_feature_active_planner(self):
        s = _decide_strategy("medium", "feature")
        assert s["max_iterations"] == 14
        assert s["use_planner"] is True
        assert s["use_best_of_n"] is False

    def test_medium_edit_pas_de_planner(self):
        s = _decide_strategy("medium", "edit")
        assert s["use_planner"] is False


# ── Parsing de réponse ────────────────────────────────────────────────────────


@pytest.fixture
def router():
    return Router.__new__(Router)  # pas de connexion LLM


class TestParsing:
    def test_json_pur(self, router):
        raw = '{"difficulty": "easy", "task_type": "edit", "reasoning": "rename 1 fichier"}'
        d = router._parse_response(raw, "renomme x en y")
        assert d.difficulty == "easy"
        assert d.task_type == "edit"
        assert d.max_iterations == 6
        assert d.use_planner is False

    def test_json_avec_markdown(self, router):
        raw = '```json\n{"difficulty": "hard", "task_type": "bug_fix", "reasoning": "async"}\n```'
        d = router._parse_response(raw, "race condition")
        assert d.difficulty == "hard"
        assert d.use_best_of_n is True

    def test_json_avec_texte_avant(self, router):
        raw = 'Voici la classification :\n{"difficulty":"medium","task_type":"refactor","reasoning":"extract"}'
        d = router._parse_response(raw, "extract")
        assert d.difficulty == "medium"
        assert d.task_type == "refactor"

    def test_difficulty_invalide_fallback(self, router):
        raw = '{"difficulty": "trivial", "task_type": "edit", "reasoning": "x"}'
        d = router._parse_response(raw, "x")
        assert d.difficulty == "medium"  # fallback
        assert "[fallback" in d.reasoning

    def test_task_type_invalide_fallback(self, router):
        raw = '{"difficulty": "easy", "task_type": "garbage", "reasoning": "x"}'
        d = router._parse_response(raw, "x")
        assert d.difficulty == "medium"  # fallback

    def test_json_casse_fallback(self, router):
        raw = "ce n'est pas du JSON"
        d = router._parse_response(raw, "x")
        assert d.difficulty == "medium"
        assert "[fallback" in d.reasoning

    def test_reasoning_tronque_a_200(self, router):
        long_reason = "x" * 500
        raw = f'{{"difficulty":"easy","task_type":"edit","reasoning":"{long_reason}"}}'
        d = router._parse_response(raw, "x")
        assert len(d.reasoning) <= 200


# ── classify() avec LLM mocké ─────────────────────────────────────────────────


class TestClassifyMocked:
    def test_appel_llm_avec_bonne_reponse(self, monkeypatch):
        # Mock OpenAI client
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = (
            '{"difficulty":"easy","task_type":"edit","reasoning":"rename"}'
        )

        with patch("agent.router.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_resp
            mock_openai.return_value = mock_client

            router = Router()
            d = router.classify("renomme `usr` en `user`")

        assert d.difficulty == "easy"
        assert d.task_type == "edit"
        assert d.max_iterations == 6

    def test_exception_llm_renvoie_fallback(self):
        with patch("agent.router.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = ConnectionError("server down")
            mock_openai.return_value = mock_client

            router = Router()
            d = router.classify("anything")

        assert d.difficulty == "medium"
        assert "[fallback" in d.reasoning


# ── Sérialisation ────────────────────────────────────────────────────────────


class TestSerialisation:
    def test_to_dict_contient_tous_les_champs(self):
        d = RoutingDecision(
            difficulty="easy",
            task_type="edit",
            max_iterations=3,
            use_planner=False,
            use_best_of_n=False,
            reasoning="test",
        )
        out = d.to_dict()
        assert out["difficulty"] == "easy"
        assert out["task_type"] == "edit"
        assert out["max_iterations"] == 3
        assert "reasoning" in out
