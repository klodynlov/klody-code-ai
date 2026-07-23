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

    # Capacités étendues (Roadmap v2 #10)

    @pytest.mark.parametrize("task_type", ["security", "migrate", "perf", "test_gen"])
    def test_medium_types_multietapes_activent_planner(self, task_type):
        s = _decide_strategy("medium", task_type)
        assert s["use_planner"] is True
        assert s["use_best_of_n"] is False  # best-of-N réservé à hard / self_dev

    @pytest.mark.parametrize("task_type", ["review", "docs"])
    def test_medium_types_legers_pas_de_planner(self, task_type):
        # Revue et doc restent mono-passe sur une tâche medium.
        s = _decide_strategy("medium", task_type)
        assert s["use_planner"] is False

    @pytest.mark.parametrize(
        "task_type", ["review", "test_gen", "security", "docs", "perf", "migrate"]
    )
    def test_hard_types_etendus_max_iter(self, task_type):
        s = _decide_strategy("hard", task_type)
        assert s["max_iterations"] == 25
        assert s["use_planner"] is True


# ── Écriture non-code (creative) → généraliste ───────────────────────────────


class TestCreativeRouting:
    """`creative` = écriture non-code (paroles, histoire, email). Doit router sur
    le généraliste (brain), JAMAIS le coder — corrige le misroute observé en live
    (chanson classée `edit`/`feature` → coder, mauvais pour le créatif FR)."""

    def test_creative_hors_code_task_types(self):
        # Invariant de routage : hors _CODE_TASK_TYPES → _route_model reste sur
        # le généraliste (cf. orchestrator._route_model).
        from agent.orchestrator import _CODE_TASK_TYPES
        assert "creative" not in _CODE_TASK_TYPES

    def test_creative_valide_dans_le_schema(self):
        from agent.router import _RouterClassification
        c = _RouterClassification(
            difficulty="medium", task_type="creative", reasoning="paroles")
        assert c.task_type == "creative"

    @pytest.mark.parametrize("diff,planner,bon", [
        ("easy", False, False),
        ("medium", False, False),   # pas dans _PLANNER_MEDIUM_TYPES
        ("hard", True, True),
    ])
    def test_creative_strategy(self, diff, planner, bon):
        s = _decide_strategy(diff, "creative")
        assert s["use_planner"] is planner
        assert s["use_best_of_n"] is bon


class TestMusicRouting:
    """`music` = produire du SON (MIDI/DAW), distinct de `creative` (des mots).

    Corrige le misroute observé en vivo (session 4034ccc7, 22/07) : faute de type
    dédié, « génère-moi une progression IV-I-V » tombait en `explain` + `easy` →
    prompt « analyse de code, ne modifie rien » et 6 itérations → la mélodie n'a
    jamais été posée.
    """

    def test_music_hors_code_task_types(self):
        # Produire de la musique n'est pas coder → généraliste, pas le coder slim
        # (qui n'injecte aucun skill).
        from agent.orchestrator import _CODE_TASK_TYPES
        assert "music" not in _CODE_TASK_TYPES

    def test_music_valide_dans_le_schema(self):
        from agent.router import _RouterClassification
        c = _RouterClassification(
            difficulty="easy", task_type="music", reasoning="progression d'accords")
        assert c.task_type == "music"

    def test_easy_est_remonte_a_medium(self):
        """Le cœur du fix : une demande musicale ne peut pas valoir 6 itérations."""
        s = _decide_strategy("easy", "music")
        assert s["max_iterations"] == 14, "plancher `medium` non appliqué"
        assert s["use_planner"] is True, "une production musicale se décompose"

    def test_medium_active_le_planner(self):
        s = _decide_strategy("medium", "music")
        assert s["max_iterations"] == 14
        assert s["use_planner"] is True
        assert s["use_best_of_n"] is False  # best-of-N reste hard / self_dev

    def test_hard_reste_hard(self):
        s = _decide_strategy("hard", "music")
        assert s["max_iterations"] == 25
        assert s["use_planner"] is True
        assert s["use_best_of_n"] is True

    def test_le_plancher_ne_deborde_pas_sur_les_autres_types(self):
        # Régression : le plancher est ciblé, il ne relève PAS tout le monde.
        assert _decide_strategy("easy", "edit")["max_iterations"] == 6
        assert _decide_strategy("easy", "explain")["max_iterations"] == 6
        assert _decide_strategy("easy", "creative")["max_iterations"] == 6

    def test_music_a_un_prompt_dedie(self):
        from agent.prompts import _TASK_PROMPT_FILES
        assert _TASK_PROMPT_FILES["music"] == "music.md"

    def test_le_prompt_du_router_enseigne_music(self):
        """Sans mention dans _ROUTER_SYSTEM, le LLM n'émettra jamais ce type."""
        from agent.router import _ROUTER_SYSTEM
        assert "|music" in _ROUTER_SYSTEM, "absent de l'enum JSON"
        assert "- music" in _ROUTER_SYSTEM, "pas de description du type"
        # La confusion à éviter : paroles (creative) vs son (music).
        assert "creative vs music" in _ROUTER_SYSTEM


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

    def test_difficulty_invalide_renvoie_none(self, router):
        # _parse_response ne fait plus de fallback : il signale l'échec par None
        # (Pydantic rejette le Literal hors-domaine). Le fallback est porté par classify().
        raw = '{"difficulty": "trivial", "task_type": "edit", "reasoning": "x"}'
        assert router._parse_response(raw, "x") is None

    def test_task_type_invalide_renvoie_none(self, router):
        raw = '{"difficulty": "easy", "task_type": "garbage", "reasoning": "x"}'
        assert router._parse_response(raw, "x") is None

    def test_json_casse_renvoie_none(self, router):
        raw = "ce n'est pas du JSON"
        assert router._parse_response(raw, "x") is None

    def test_champ_manquant_renvoie_none(self, router):
        # task_type absent → champ requis manquant → ValidationError → None.
        raw = '{"difficulty": "easy", "reasoning": "x"}'
        assert router._parse_response(raw, "x") is None

    def test_think_inline_nettoye_puis_valide(self, router):
        # Le bloc <think> est nettoyé (jamais une garantie) avant validation.
        raw = '<think>je réfléchis</think>{"difficulty":"easy","task_type":"edit","reasoning":"r"}'
        d = router._parse_response(raw, "x")
        assert d is not None and d.difficulty == "easy" and d.task_type == "edit"

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

    @staticmethod
    def _resp(content: str) -> MagicMock:
        r = MagicMock()
        r.choices = [MagicMock()]
        r.choices[0].message.content = content
        return r

    def test_classify_retry_puis_succes(self):
        # 1er appel : JSON invalide → 2e appel (après correction) : JSON valide.
        bad = self._resp('{"difficulty":"trivial","task_type":"edit","reasoning":"x"}')
        good = self._resp('{"difficulty":"easy","task_type":"edit","reasoning":"ok"}')
        with patch("agent.router.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = [bad, good]
            mock_openai.return_value = mock_client
            router = Router()
            d = router.classify("renomme x")

        assert d.difficulty == "easy" and d.task_type == "edit"
        assert "[fallback" not in d.reasoning
        assert mock_client.chat.completions.create.call_count == 2

    def test_classify_retry_epuise_puis_fallback(self):
        # 3 réponses invalides (1 + 2 retries) → fallback medium/explain.
        bad = self._resp('pas du json')
        with patch("agent.router.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = [bad, bad, bad]
            mock_openai.return_value = mock_client
            router = Router()
            d = router.classify("x")

        assert d.difficulty == "medium" and d.task_type == "explain"
        assert "[fallback" in d.reasoning
        assert mock_client.chat.completions.create.call_count == 3  # _ROUTER_MAX_RETRIES + 1

    def test_classify_injecte_message_correction(self):
        # Au 2e appel, l'historique doit contenir la réponse fautive + la correction.
        bad = self._resp('nope')
        good = self._resp('{"difficulty":"easy","task_type":"edit","reasoning":"ok"}')
        with patch("agent.router.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = [bad, good]
            mock_openai.return_value = mock_client
            router = Router()
            router.classify("x")
            second_call_messages = mock_client.chat.completions.create.call_args_list[1].kwargs["messages"]

        roles = [m["role"] for m in second_call_messages]
        assert roles[-2:] == ["assistant", "user"]  # réponse fautive + correction
        assert "JSON" in second_call_messages[-1]["content"]

    def test_classify_erreur_reseau_pas_de_retry(self):
        # Erreur transport → fallback immédiat, AUCUN retry de parse.
        with patch("agent.router.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = ConnectionError("down")
            mock_openai.return_value = mock_client
            router = Router()
            d = router.classify("x")

        assert "[fallback" in d.reasoning
        assert mock_client.chat.completions.create.call_count == 1


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
