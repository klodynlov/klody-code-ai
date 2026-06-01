"""Tests de agent/memory_extractor.py — extraction automatique de mémoire."""

import json
from unittest.mock import MagicMock, patch

from agent.memory_extractor import _parse_json_facts, extract_and_save

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _lt_mock():
    """LongTermMemory mock simple."""
    lt = MagicMock()
    lt.remember.return_value = "Mémorisé : [context] key"
    return lt


def _msgs(n_user: int = 3) -> list[dict]:
    """Génère une conversation minimale avec n messages user."""
    msgs = []
    for i in range(n_user):
        msgs.append({"role": "user", "content": f"Question {i}"})
        msgs.append({"role": "assistant", "content": f"Réponse {i}"})
    return msgs


def _llm_response(facts: list[dict]) -> MagicMock:
    """Simule une réponse LLM retournant des facts en JSON."""
    choice = MagicMock()
    choice.message.content = json.dumps(facts)
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── _parse_json_facts ─────────────────────────────────────────────────────────

class TestParseJsonFacts:
    def test_json_valide_direct(self):
        raw = '[{"key": "k", "content": "v", "category": "context"}]'
        result = _parse_json_facts(raw)
        assert len(result) == 1
        assert result[0]["key"] == "k"

    def test_liste_vide(self):
        assert _parse_json_facts("[]") == []

    def test_json_avec_texte_autour(self):
        raw = 'Voici les faits:\n[{"key": "k", "content": "v", "category": "user"}]\nFin.'
        result = _parse_json_facts(raw)
        assert len(result) == 1
        assert result[0]["key"] == "k"

    def test_json_invalide_retourne_vide(self):
        assert _parse_json_facts("pas du json") == []

    def test_json_invalide_partiel(self):
        assert _parse_json_facts("[{broken json") == []

    def test_plusieurs_facts(self):
        facts = [
            {"key": "a", "content": "va", "category": "user"},
            {"key": "b", "content": "vb", "category": "project"},
        ]
        result = _parse_json_facts(json.dumps(facts))
        assert len(result) == 2

    def test_whitespace_autour(self):
        raw = '  \n  [{"key": "k", "content": "v", "category": "context"}]  \n  '
        result = _parse_json_facts(raw)
        assert len(result) == 1


# ── extract_and_save — session trop courte ────────────────────────────────────

class TestExtractTooShort:
    def test_zero_user_messages(self):
        lt = _lt_mock()
        msgs = [{"role": "assistant", "content": "Bonjour"}]
        result = extract_and_save(msgs, lt)
        assert result == []
        lt.remember.assert_not_called()

    def test_un_seul_user_message(self):
        lt = _lt_mock()
        msgs = [
            {"role": "user", "content": "Question unique"},
            {"role": "assistant", "content": "Réponse"},
        ]
        result = extract_and_save(msgs, lt)
        assert result == []
        lt.remember.assert_not_called()

    def test_messages_sans_contenu_ignores(self):
        lt = _lt_mock()
        msgs = [
            {"role": "user", "content": None},
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "ok"},
        ]
        result = extract_and_save(msgs, lt)
        assert result == []


# ── extract_and_save — LLM mocké ─────────────────────────────────────────────

class TestExtractWithMockedLLM:
    @patch("agent.memory_extractor.OpenAI")
    def test_facts_sauvegardes(self, mock_openai_cls):
        facts = [
            {"key": "langage", "content": "Python", "category": "preference"},
            {"key": "projet", "content": "Klody AI", "category": "project"},
        ]
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert len(result) == 2
        assert lt.remember.call_count == 2

    @patch("agent.memory_extractor.OpenAI")
    def test_llm_retourne_liste_vide(self, mock_openai_cls):
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response([])

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert result == []
        lt.remember.assert_not_called()

    @patch("agent.memory_extractor.OpenAI")
    def test_categorie_invalide_corrigee(self, mock_openai_cls):
        facts = [{"key": "k", "content": "v", "category": "inconnu"}]
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert result[0]["category"] == "context"

    @patch("agent.memory_extractor.OpenAI")
    def test_fact_sans_key_ignore(self, mock_openai_cls):
        facts = [{"content": "v", "category": "context"}]  # pas de key
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert result == []

    @patch("agent.memory_extractor.OpenAI")
    def test_fact_sans_content_ignore(self, mock_openai_cls):
        facts = [{"key": "k", "category": "context"}]  # pas de content
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert result == []

    @patch("agent.memory_extractor.OpenAI")
    def test_erreur_llm_retourne_vide(self, mock_openai_cls):
        mock_openai_cls.return_value.chat.completions.create.side_effect = Exception("timeout")

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert result == []
        lt.remember.assert_not_called()

    @patch("agent.memory_extractor.OpenAI")
    def test_llm_repond_json_avec_texte(self, mock_openai_cls):
        """LLM qui entoure le JSON de texte — doit quand même parser."""
        choice = MagicMock()
        choice.message.content = 'Voici:\n[{"key": "k", "content": "v", "category": "user"}]'
        resp = MagicMock()
        resp.choices = [choice]
        mock_openai_cls.return_value.chat.completions.create.return_value = resp

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        assert len(result) == 1

    @patch("agent.memory_extractor.OpenAI")
    def test_messages_tronques_a_30(self, mock_openai_cls):
        """Ne plante pas avec une très longue conversation."""
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response([])
        lt = _lt_mock()
        long_msgs = _msgs(50)  # 100 messages
        result = extract_and_save(long_msgs, lt)
        assert result == []
        # Vérifier que le LLM a bien été appelé (pas skippé)
        mock_openai_cls.return_value.chat.completions.create.assert_called_once()

    @patch("agent.memory_extractor.OpenAI")
    def test_categories_valides_conservees(self, mock_openai_cls):
        facts = [
            {"key": "a", "content": "va", "category": "user"},
            {"key": "b", "content": "vb", "category": "project"},
            {"key": "c", "content": "vc", "category": "preference"},
            {"key": "d", "content": "vd", "category": "context"},
        ]
        mock_openai_cls.return_value.chat.completions.create.return_value = _llm_response(facts)

        lt = _lt_mock()
        result = extract_and_save(_msgs(3), lt)

        cats = {r["category"] for r in result}
        assert cats == {"user", "project", "preference", "context"}
