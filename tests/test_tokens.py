"""Tests de agent/tokens.py — comptage de tokens + repli heuristique robuste."""

import agent.tokens as tokens
import pytest


@pytest.fixture
def heuristic(monkeypatch):
    """Force l'absence de tokenizer exact → repli heuristique."""
    monkeypatch.setattr(tokens, "_tried", True)
    monkeypatch.setattr(tokens, "_tokenizer", None)


class TestHeuristique:
    def test_texte_vide_vaut_zero(self, heuristic):
        assert tokens.count_tokens("") == 0

    def test_repli_chars_sur_4(self, heuristic):
        assert tokens.count_tokens("a" * 40) == 10

    def test_non_exact_en_repli(self, heuristic):
        assert tokens.tokenizer_is_exact() is False


class TestTokenizerExact:
    def test_utilise_encode_si_dispo(self, monkeypatch):
        class FakeTok:
            def encode(self, text):
                return text.split()  # 1 token par mot

        monkeypatch.setattr(tokens, "_tried", True)
        monkeypatch.setattr(tokens, "_tokenizer", FakeTok())
        assert tokens.count_tokens("un deux trois") == 3
        assert tokens.tokenizer_is_exact() is True

    def test_encode_qui_leve_retombe_sur_heuristique(self, monkeypatch):
        class BoomTok:
            def encode(self, text):
                raise RuntimeError("boom")

        monkeypatch.setattr(tokens, "_tried", True)
        monkeypatch.setattr(tokens, "_tokenizer", BoomTok())
        assert tokens.count_tokens("a" * 40) == 10  # repli silencieux

    def test_chargement_echoue_donne_none(self, monkeypatch):
        """transformers absent / modèle non-HF → _load_tokenizer renvoie None."""
        monkeypatch.setattr(tokens, "_tried", False)
        monkeypatch.setattr(tokens, "_tokenizer", None)
        monkeypatch.setattr(tokens.config, "LLM_MODEL", "modele-inexistant-xyz:42")
        assert tokens._load_tokenizer() is None
