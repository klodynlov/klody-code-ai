"""Tests de agent/memory.py — persistance, format API, fenêtre glissante."""

import json
import os
from pathlib import Path

import pytest
from agent.memory import ConversationMemory, _message_budget


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """ConversationMemory avec MEMORY_DIR pointant sur tmp_path."""
    monkeypatch.setattr("config.MEMORY_DIR", tmp_path)
    m = ConversationMemory(session_id="testmem")
    m.memory_file = tmp_path / "memory_testmem.json"
    return m


# ------------------------------------------------------------------ #
# Ajout de messages                                                    #
# ------------------------------------------------------------------ #

class TestAddMessage:
    def test_ajout_message_user(self, mem):
        mem.add_message("user", "Bonjour Klody")
        assert any(
            m["role"] == "user" and m["content"] == "Bonjour Klody"
            for m in mem.messages
        )

    def test_ajout_message_assistant(self, mem):
        mem.add_message("assistant", "Bonjour !")
        assert any(m["role"] == "assistant" for m in mem.messages)

    def test_timestamp_present(self, mem):
        mem.add_message("user", "msg")
        assert mem.messages[-1]["timestamp"] is not None

    def test_fenetre_glissante_limite(self, mem, monkeypatch):
        """Fenêtre glissante : ne dépasse pas MAX_MESSAGES messages non-system."""
        monkeypatch.setattr("config.MAX_MESSAGES", 5)
        mem.messages.append({"role": "system", "content": "sys", "timestamp": None})
        for i in range(10):
            mem.add_message("user", f"Message {i}")
        non_system = [m for m in mem.messages if m["role"] != "system"]
        assert len(non_system) <= 5

    def test_fenetre_glissante_conserve_system(self, mem, monkeypatch):
        """Le system prompt n'est jamais supprimé par la fenêtre glissante."""
        monkeypatch.setattr("config.MAX_MESSAGES", 3)
        mem.messages.insert(0, {"role": "system", "content": "sys", "timestamp": None})
        for i in range(10):
            mem.add_message("user", f"msg {i}")
        assert any(m["role"] == "system" for m in mem.messages)


# ------------------------------------------------------------------ #
# Budget de tokens (en plus du plafond par nombre de messages)         #
# ------------------------------------------------------------------ #

class TestTokenBudget:
    def test_gros_messages_declenchent_le_trim_tokens(self, mem, monkeypatch):
        """Même SOUS le plafond de messages, un contexte volumineux est rogné
        pour rester sous le budget messages (= CW − réserves outils/réponse)."""
        import config

        # MAX_MESSAGES large (le trim par nombre ne doit pas intervenir) ;
        # fenêtre + réserves réduites → c'est le budget de tokens qui tranche.
        monkeypatch.setattr("config.MAX_MESSAGES", 1000)
        monkeypatch.setattr(config, "CONTEXT_WINDOW", 6000)
        monkeypatch.setattr(config, "CONTEXT_TOOLS_RESERVE", 2000)
        monkeypatch.setattr(config, "CONTEXT_RESPONSE_RESERVE", 1000)  # budget = 3000 tokens
        msg = "x" * 1000  # ~255 tokens chacun, < budget
        for _ in range(30):  # ~7650 tokens au total → doit être rogné
            mem.add_message("user", msg)
        budget = _message_budget()
        assert mem._total_estimated_tokens() <= budget
        assert 1 <= mem._count_non_system() < 30  # rognage effectif

    def test_dernier_groupe_toujours_conserve(self, mem, monkeypatch):
        """Un unique message plus gros que le budget n'est PAS supprimé."""
        import config

        monkeypatch.setattr(config, "CONTEXT_WINDOW", 50)  # budget = 40 tokens
        mem.add_message("user", "y" * 8000)  # ~2000 tokens, > budget
        assert mem._count_non_system() == 1

    def test_trim_tokens_preserve_l_invariant(self, mem, monkeypatch):
        """Le rognage par tokens ne laisse jamais de tool result orphelin."""
        import config

        monkeypatch.setattr("config.MAX_MESSAGES", 1000)
        monkeypatch.setattr(config, "CONTEXT_WINDOW", 200)
        big = "z" * 4000
        for i in range(8):
            cid = f"c{i}"
            mem.add_tool_call_message([{
                "id": cid, "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }])
            mem.add_tool_result(cid, "read_file", big)
            mem.add_message("user", "suite")  # déclenche _apply_sliding_window
        assert mem._orphan_tool_results() == []

    def test_tour_react_outils_seuls_est_rogne(self, mem, monkeypatch):
        """Régression : un tour ReAct enchaîne add_tool_call_message /
        add_tool_result (jusqu'à MAX_ITERATIONS fois) SANS repasser par
        add_message. Avant le fix ces deux méthodes ne rognaient pas → le
        contexte gonflait sans borne sur un seul tour (session molécule 3D
        saturée à 32k/32.8k). Désormais elles appliquent aussi la fenêtre."""
        import config

        monkeypatch.setattr("config.MAX_MESSAGES", 1000)
        monkeypatch.setattr(config, "CONTEXT_WINDOW", 6000)
        monkeypatch.setattr(config, "CONTEXT_TOOLS_RESERVE", 2000)
        monkeypatch.setattr(config, "CONTEXT_RESPONSE_RESERVE", 1000)  # budget = 3000
        big = "d" * 4000  # ~1004 tokens par result
        for i in range(15):  # ~15k tokens de tool results seuls → doit rogner
            cid = f"c{i}"
            mem.add_tool_call_message([{
                "id": cid, "type": "function",
                "function": {"name": "run_in_sandbox", "arguments": "{}"},
            }])
            mem.add_tool_result(cid, "run_in_sandbox", big)
        assert mem._total_estimated_tokens() <= _message_budget()
        assert mem._orphan_tool_results() == []


class TestContextBudgetReserves:
    """Le budget messages doit réserver la place du prompt RÉEL = messages +
    schémas d'outils (envoyés hors `messages`) + génération de la réponse.
    Borner sur CONTEXT_WINDOW seul saturait la fenêtre (jauge ~32k/32.8k) et
    ne laissait plus de place pour répondre → génération bloquée."""

    def test_prompt_reel_tient_sous_la_fenetre(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "CONTEXT_WINDOW", 32768)
        monkeypatch.setattr(config, "CONTEXT_TOOLS_RESERVE", 8192)
        monkeypatch.setattr(config, "CONTEXT_RESPONSE_RESERVE", 4096)
        budget = _message_budget()
        # messages (au max du budget) + schémas d'outils ⩽ fenêtre du modèle,
        # avec assez de marge restante pour générer la réponse.
        prompt_reel_max = budget + config.CONTEXT_TOOLS_RESERVE
        assert prompt_reel_max <= config.CONTEXT_WINDOW
        assert config.CONTEXT_WINDOW - prompt_reel_max >= config.CONTEXT_RESPONSE_RESERVE

    def test_plancher_budget(self, monkeypatch):
        """Réserves > fenêtre → budget plancher (jamais négatif ni nul)."""
        import config

        monkeypatch.setattr(config, "CONTEXT_WINDOW", 4000)
        monkeypatch.setattr(config, "CONTEXT_TOOLS_RESERVE", 8192)
        monkeypatch.setattr(config, "CONTEXT_RESPONSE_RESERVE", 4096)
        assert _message_budget() >= 2048


# ------------------------------------------------------------------ #
# Messages tool calls                                                  #
# ------------------------------------------------------------------ #

class TestToolCallMessages:
    def test_ajout_tool_call_message(self, mem):
        tool_calls = [{
            "id": "call_abc",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'},
        }]
        mem.add_tool_call_message(tool_calls)
        assert any(m.get("tool_calls") for m in mem.messages)

    def test_ajout_tool_result(self, mem):
        mem.add_tool_result("call_abc", "read_file", "contenu du fichier")
        tool_msg = next((m for m in mem.messages if m["role"] == "tool"), None)
        assert tool_msg is not None
        assert tool_msg["tool_call_id"] == "call_abc"
        assert tool_msg["name"] == "read_file"
        assert tool_msg["content"] == "contenu du fichier"


# ------------------------------------------------------------------ #
# Format API                                                           #
# ------------------------------------------------------------------ #

class TestGetMessagesForApi:
    def test_format_messages_simples(self, mem):
        mem.add_message("user", "Question")
        mem.add_message("assistant", "Réponse")
        api = mem.get_messages_for_api()
        roles = [m["role"] for m in api]
        assert "user" in roles
        assert "assistant" in roles

    def test_pas_de_timestamp_dans_api(self, mem):
        mem.add_message("user", "msg")
        for m in mem.get_messages_for_api():
            assert "timestamp" not in m

    def test_format_tool_message_api(self, mem):
        tool_calls = [{"id": "c1", "type": "function",
                       "function": {"name": "list_files", "arguments": "{}"}}]
        mem.add_tool_call_message(tool_calls)
        mem.add_tool_result("c1", "list_files", "📄 main.py")
        api = mem.get_messages_for_api()
        tool_msg = next((m for m in api if m["role"] == "tool"), None)
        assert tool_msg is not None
        assert tool_msg["tool_call_id"] == "c1"
        assert tool_msg["content"] == "📄 main.py"

    def test_assistant_avec_tool_calls_dans_api(self, mem):
        tool_calls = [{"id": "c2", "type": "function",
                       "function": {"name": "read_file", "arguments": '{"path":"f.py"}'}}]
        mem.add_tool_call_message(tool_calls)
        api = mem.get_messages_for_api()
        asst = next((m for m in api if m["role"] == "assistant"), None)
        assert asst is not None
        assert "tool_calls" in asst


# ------------------------------------------------------------------ #
# Persistance JSON                                                     #
# ------------------------------------------------------------------ #

class TestSaveLoad:
    def test_sauvegarde_cree_fichier_json(self, mem, tmp_path):
        mem.add_message("user", "test persistance")
        assert mem.memory_file.exists()

    def test_contenu_json_valide(self, mem, tmp_path):
        mem.add_message("user", "test JSON")
        data = json.loads(mem.memory_file.read_text(encoding="utf-8"))
        assert data["session_id"] == "testmem"
        assert any(m["content"] == "test JSON" for m in data["messages"])

    def test_chargement_depuis_fichier(self, mem, tmp_path):
        mem.add_message("user", "Message persisté")
        loaded = ConversationMemory.load_from_file(mem.memory_file)
        assert loaded.session_id == "testmem"
        assert any(m["content"] == "Message persisté" for m in loaded.messages)

    def test_load_latest_retourne_none_si_vide(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.MEMORY_DIR", tmp_path)
        result = ConversationMemory.load_latest()
        assert result is None

    def test_load_latest_retourne_plus_recent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.MEMORY_DIR", tmp_path)
        m1 = ConversationMemory(session_id="older")
        m1.memory_file = tmp_path / "memory_older.json"
        m1.add_message("user", "ancien")
        import time; time.sleep(0.01)
        m2 = ConversationMemory(session_id="newer")
        m2.memory_file = tmp_path / "memory_newer.json"
        m2.add_message("user", "récent")
        latest = ConversationMemory.load_latest()
        assert latest.session_id == "newer"


# ------------------------------------------------------------------ #
# Clear et stats                                                       #
# ------------------------------------------------------------------ #

class TestClearAndStats:
    def test_clear_supprime_messages_non_system(self, mem):
        mem.messages.append({"role": "system", "content": "System", "timestamp": None})
        mem.add_message("user", "à effacer")
        mem.clear()
        assert not any(m["role"] == "user" for m in mem.messages)

    def test_clear_preserve_system(self, mem):
        mem.messages.append({"role": "system", "content": "System", "timestamp": None})
        mem.add_message("user", "msg")
        mem.clear()
        assert any(m["role"] == "system" for m in mem.messages)

    def test_stats_retourne_dict_complet(self, mem):
        mem.add_message("user", "u1")
        mem.add_message("assistant", "a1")
        stats = mem.stats()
        assert stats["session_id"] == "testmem"
        assert stats["messages_user"] == 1
        assert stats["messages_assistant"] == 1
        assert "fichier" in stats
