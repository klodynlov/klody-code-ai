"""La mémoire longue terme du prompt système est figée pour la durée d'une session.

Le cache de préfixe de mlx_lm ne réutilise qu'un préfixe EXACT (#270). L'extraction
automatique écrit des faits après CHAQUE message WebSocket ; relue à chaque
message, la section « mémoire longue terme » du système changeait donc entre deux
messages d'une même session, et le tour suivant refaisait le prefill des schémas
d'outils depuis le token 0. Mesure : `scripts/mesure_stabilite_memoire.py`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from agent.long_term_memory import (
    LongTermMemory,
    invalider_section_de_session,
    section_de_session,
)
from agent.memory import ConversationMemory
from agent.orchestrator import Orchestrator


@pytest.fixture
def lt(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.long_term_memory._STORAGE", tmp_path / "long_term.json")
    monkeypatch.setattr("agent.long_term_memory._instance", None)
    m = LongTermMemory()
    m.remember("langage", "Python avant tout", "preference")
    return m


class TestSectionDeSession:
    def test_un_fait_extrait_en_cours_de_session_ne_change_pas_la_section(self, lt):
        session = ConversationMemory()
        avant = section_de_session(session, lt)
        lt.remember("projet_courant", "klody-code-ai", "project")  # l'extraction
        assert section_de_session(session, lt) == avant

    def test_la_session_suivante_voit_le_fait(self, lt):
        section_de_session(ConversationMemory(), lt)
        lt.remember("projet_courant", "klody-code-ai", "project")
        assert "klody-code-ai" in section_de_session(ConversationMemory(), lt)

    def test_invalider_fait_relire_la_memoire(self, lt):
        session = ConversationMemory()
        section_de_session(session, lt)
        lt.forget("langage")
        invalider_section_de_session(session)
        assert "Python avant tout" not in section_de_session(session, lt)

    def test_clear_relit_la_memoire(self, lt):
        # `/clear` efface l'historique d'où venaient les faits extraits : les
        # garder invisibles jusqu'à la fin de la session les perdrait tout à fait.
        session = ConversationMemory()
        section_de_session(session, lt)
        lt.remember("projet_courant", "klody-code-ai", "project")
        session.clear()
        assert "klody-code-ai" in section_de_session(session, lt)

    def test_une_memoire_vide_se_fige_aussi(self, tmp_path, monkeypatch):
        # "" est une valeur figée légitime : sans ce cas, une session ouverte sur
        # une mémoire vide relirait la mémoire à chaque message — et casserait le
        # cache au premier fait extrait, exactement le défaut corrigé.
        monkeypatch.setattr("agent.long_term_memory._STORAGE", tmp_path / "vide.json")
        vide = LongTermMemory()
        session = ConversationMemory()
        assert section_de_session(session, vide) == ""
        vide.remember("cle", "un fait", "user")
        assert section_de_session(session, vide) == ""

    def test_un_porteur_sans_valeur_str_est_recalcule(self, lt):
        # Sous les tests, `self.memory` est souvent un MagicMock : getattr y rend
        # un MagicMock, qui ne doit jamais finir dans le prompt.
        porteur = MagicMock()
        assert section_de_session(porteur, lt) == lt.format_for_prompt()


def _orch(lt: LongTermMemory, memoire: ConversationMemory) -> MagicMock:
    """Orchestrateur minimal pour `_inject_system_prompt` (chemin généraliste)."""
    o = MagicMock()
    o._code_model_active = False
    o.memory = memoire
    o.lt_memory = lt
    o._on_skills_selected = None
    o._relevant_files_section.return_value = ""
    o.profiler.get_profile_for_prompt.return_value = ""
    o.conventions.detect.return_value.format_for_prompt.return_value = ""
    o.error_memory.format_for_prompt.return_value = ""
    return o


class TestPromptSysteme:
    @pytest.fixture(autouse=True)
    def _sans_skills(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.SKILLS_ROUTER_ENABLED", False)
        monkeypatch.setattr("agent.orchestrator.load_skills", lambda: [])

    def test_extraction_entre_deux_messages_systeme_identique(self, lt):
        memoire = ConversationMemory()
        Orchestrator._inject_system_prompt(_orch(lt, memoire), task_type="explain", query="a")
        premier = memoire.messages[0]["content"]
        assert "Python avant tout" in premier
        # Ce que fait `_extract_memory_bg` après le message, dans un autre thread.
        lt.remember("decision", "garder Qwen3.6 comme cerveau", "context")
        # L'API reconstruit un Orchestrator par message, sur la même mémoire.
        Orchestrator._inject_system_prompt(_orch(lt, memoire), task_type="explain", query="b")
        assert memoire.messages[0]["content"] == premier

    def test_remember_fact_explicite_visible_au_message_suivant(self, lt):
        memoire = ConversationMemory()
        o = _orch(lt, memoire)
        Orchestrator._inject_system_prompt(o, task_type="explain", query="a")
        Orchestrator._tool_remember_fact(
            o, {"key": "prenom", "content": "Klodynlov", "category": "user"})
        Orchestrator._inject_system_prompt(_orch(lt, memoire), task_type="explain", query="b")
        assert "Klodynlov" in memoire.messages[0]["content"]

    def test_forget_fact_explicite_retire_le_fait_au_message_suivant(self, lt):
        memoire = ConversationMemory()
        o = _orch(lt, memoire)
        Orchestrator._inject_system_prompt(o, task_type="explain", query="a")
        assert "Python avant tout" in memoire.messages[0]["content"]
        assert Orchestrator._tool_forget_fact(o, {"key": "langage"}) == "Oublié : langage"
        Orchestrator._inject_system_prompt(_orch(lt, memoire), task_type="explain", query="b")
        assert "Python avant tout" not in memoire.messages[0]["content"]
