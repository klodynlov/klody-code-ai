"""Tests pour agent.best_of_n — génération de candidats + reranker LLM-as-judge."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.best_of_n import BestOfN, Candidate, _format_candidates_for_rerank


# ── Candidate.summary ─────────────────────────────────────────────────────────


class TestCandidateSummary:
    def test_texte_seul(self):
        c = Candidate(idx=0, temperature=0.5, content="Voici ma réponse", tool_calls=None)
        s = c.summary()
        assert "Texte:" in s
        assert "Voici ma réponse" in s

    def test_tool_calls_seuls(self):
        c = Candidate(
            idx=1,
            temperature=0.7,
            content="",
            tool_calls=[
                {"function": {"name": "read_file", "arguments": '{"path":"x.py"}'}},
            ],
        )
        s = c.summary()
        assert "Tool calls: read_file" in s
        assert "read_file" in s
        assert "x.py" in s

    def test_args_tronques(self):
        big = "x" * 500
        c = Candidate(
            idx=0,
            temperature=0.5,
            content="",
            tool_calls=[
                {"function": {"name": "write_file",
                              "arguments": f'{{"path":"a.py","content":"{big}"}}'}},
            ],
        )
        s = c.summary()
        # La string longue doit être tronquée à 120 chars + '…'
        assert "…" in s
        assert len(s) < 500

    def test_args_invalides_robuste(self):
        c = Candidate(
            idx=0,
            temperature=0.5,
            content="",
            tool_calls=[{"function": {"name": "broken", "arguments": "not json"}}],
        )
        s = c.summary()
        assert "broken" in s
        assert "args invalides" in s

    def test_vide(self):
        c = Candidate(idx=0, temperature=0.5, content="", tool_calls=None)
        assert "vide" in c.summary().lower()


# ── _format_candidates_for_rerank ─────────────────────────────────────────────


class TestFormatRerank:
    def test_contient_user_prompt_et_indices_1_based(self):
        cands = [
            Candidate(idx=0, temperature=0.3, content="opt A", tool_calls=None),
            Candidate(idx=1, temperature=0.6, content="opt B", tool_calls=None),
        ]
        s = _format_candidates_for_rerank("fix bug X", cands)
        assert "fix bug X" in s
        assert "[1]" in s
        assert "[2]" in s
        assert "opt A" in s
        assert "opt B" in s


# ── BestOfN avec LLM mocké ────────────────────────────────────────────────────


@pytest.fixture
def mock_llm():
    return MagicMock()


class TestGenerateCandidates:
    def test_n_appels_temperatures_varies(self, mock_llm):
        # Chaque appel renvoie (content, tool_calls)
        mock_llm.stream_chat.side_effect = [
            ("candidat A", None),
            ("candidat B", None),
            ("candidat C", None),
        ]
        bn = BestOfN(mock_llm, n=3)
        cands = bn.generate_candidates([{"role": "user", "content": "x"}], tools=None)
        assert len(cands) == 3
        assert {c.content for c in cands} == {"candidat A", "candidat B", "candidat C"}
        # Vérifier les températures variées
        temps = [c.temperature for c in cands]
        assert temps == bn.temperatures

    def test_appel_silencieux(self, mock_llm):
        mock_llm.stream_chat.return_value = ("x", None)
        bn = BestOfN(mock_llm, n=2)
        bn.generate_candidates([{"role": "user", "content": "x"}], tools=[])
        # Chaque appel doit être en silent=True
        for call in mock_llm.stream_chat.call_args_list:
            assert call.kwargs.get("silent") is True

    def test_exception_devient_candidat_vide(self, mock_llm):
        mock_llm.stream_chat.side_effect = [("ok", None), Exception("boom"), ("ok2", None)]
        bn = BestOfN(mock_llm, n=3)
        cands = bn.generate_candidates([{"role": "user", "content": "x"}], tools=None)
        assert len(cands) == 3
        assert cands[1].content == ""
        assert cands[1].tool_calls is None


class TestRerankParser:
    def test_json_valide(self):
        idx, r = BestOfN._parse_rerank_response('{"choice": 2, "reasoning": "B est mieux"}', n=3)
        assert idx == 1  # 1-based → 0-based
        assert "B est mieux" in r

    def test_json_avec_markdown(self):
        idx, _ = BestOfN._parse_rerank_response('```json\n{"choice": 3, "reasoning": "c"}\n```', n=3)
        assert idx == 2

    def test_fallback_nombre_isole(self):
        idx, r = BestOfN._parse_rerank_response("Je choisis le 2", n=3)
        assert idx == 1
        assert "fallback" in r

    def test_choix_hors_bornes_renvoie_0(self):
        idx, _ = BestOfN._parse_rerank_response('{"choice": 99}', n=3)
        assert idx == 0  # fallback

    def test_reponse_garbage_renvoie_0(self):
        idx, r = BestOfN._parse_rerank_response("aucun sens", n=3)
        assert idx == 0
        assert "fallback" in r


class TestRerank:
    def test_avec_1_candidat_renvoie_0(self, mock_llm):
        bn = BestOfN(mock_llm, n=1)
        cands = [Candidate(idx=0, temperature=0.5, content="x", tool_calls=None)]
        idx, _ = bn.rerank(cands, "task")
        assert idx == 0
        mock_llm.stream_chat.assert_not_called()

    def test_choisit_via_llm_si_aucun_actionnable(self, mock_llm):
        """LLM-judge utilisé uniquement quand aucun candidat n'a de tool_calls."""
        mock_llm.stream_chat.return_value = ('{"choice": 2, "reasoning": "best"}', None)
        bn = BestOfN(mock_llm, n=3)
        cands = [Candidate(idx=i, temperature=0.5, content=f"c{i}", tool_calls=None) for i in range(3)]
        idx, r = bn.rerank(cands, "task")
        assert idx == 1
        assert "best" in r

    def test_rerank_silencieux_quand_llm_judge(self, mock_llm):
        mock_llm.stream_chat.return_value = ('{"choice": 1}', None)
        bn = BestOfN(mock_llm, n=2)
        cands = [Candidate(idx=i, temperature=0.5, content=f"c{i}", tool_calls=None) for i in range(2)]
        bn.rerank(cands, "task")
        call = mock_llm.stream_chat.call_args
        assert call.kwargs.get("silent") is True
        assert call.kwargs.get("temperature") == 0.0  # reranker = déterministe


class TestRerankActionOverride:
    """L'override objectif : préfère systématiquement les candidats avec tool_calls."""

    def test_un_seul_actionnable_gagne_sans_llm(self, mock_llm):
        bn = BestOfN(mock_llm, n=3)
        cands = [
            Candidate(idx=0, temperature=0.3, content="plan textuel", tool_calls=None, latency_s=2.0),
            Candidate(idx=1, temperature=0.6, content="", tool_calls=[
                {"function": {"name": "write_file", "arguments": '{"path":"x"}'}}
            ], latency_s=3.5),
            Candidate(idx=2, temperature=0.9, content="autre plan", tool_calls=None, latency_s=1.8),
        ]
        idx, reason = bn.rerank(cands, "task")
        assert idx == 1, "doit choisir l'unique candidat avec tool_calls"
        assert "action override" in reason
        mock_llm.stream_chat.assert_not_called()  # pas de LLM-judge appelé

    def test_plusieurs_actionnables_prend_le_plus_rapide(self, mock_llm):
        bn = BestOfN(mock_llm, n=3)
        tc = [{"function": {"name": "read_file", "arguments": '{"path":"x"}'}}]
        cands = [
            Candidate(idx=0, temperature=0.3, content="", tool_calls=tc, latency_s=4.2),
            Candidate(idx=1, temperature=0.6, content="", tool_calls=tc, latency_s=1.5),
            Candidate(idx=2, temperature=0.9, content="", tool_calls=tc, latency_s=2.8),
        ]
        idx, reason = bn.rerank(cands, "task")
        assert idx == 1, "doit choisir le plus rapide"
        assert "tous actionnables" in reason
        mock_llm.stream_chat.assert_not_called()

    def test_aucun_actionnable_fallback_llm_judge(self, mock_llm):
        mock_llm.stream_chat.return_value = ('{"choice": 3, "reasoning": "le plus clair"}', None)
        bn = BestOfN(mock_llm, n=3)
        cands = [Candidate(idx=i, temperature=0.5, content=f"plan {i}", tool_calls=None) for i in range(3)]
        idx, _ = bn.rerank(cands, "task")
        assert idx == 2
        mock_llm.stream_chat.assert_called_once()  # LLM-judge consulté


class TestBest:
    def test_pipeline_complet(self, mock_llm):
        # 3 candidats + 1 rerank = 4 appels
        mock_llm.stream_chat.side_effect = [
            ("cand A", None),
            ("cand B", None),
            ("cand C", None),
            ('{"choice": 3, "reasoning": "C wins"}', None),
        ]
        bn = BestOfN(mock_llm, n=3)
        winner, all_c, reasoning = bn.best(
            [{"role": "user", "content": "task"}],
            tools=None,
            user_prompt="task",
        )
        assert len(all_c) == 3
        assert winner.idx == 2
        assert winner.content == "cand C"
        assert "C wins" in reasoning
