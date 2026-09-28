"""Le contexte d'une requête (retrieval, skills) suit le tour user, pas le système.

Le cache de préfixe de mlx_lm (ArraysCache du MoE, non rognable) ne réutilise
qu'un préfixe EXACT, et le template Qwen écrit les schémas d'outils PUIS le
système dans le même bloc. Mesuré le 2026-09-27 : le dernier token du système
changé ⇒ cached=0, 11,15 s ; rejeu exact ⇒ cached=11 517/11 518, 0,55 s. Les
skills variant avec la requête, profil + skills n'étaient identiques que sur 9 %
des paires de messages réels consécutifs : presque chaque premier appel d'un
message refaisait le prefill des outils depuis le token 0.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import agent.orchestrator as orch_mod
import pytest
from agent.memory import ConversationMemory
from agent.orchestrator import Orchestrator

_PERMANENT = {"name": "Profil studio", "slug": "utilisateur_studio",
              "description": "le studio de l'utilisateur", "content": "Scarlett 2i2"}

_SKILLS = [
    _PERMANENT,
    {"name": "Mixage et mastering", "slug": "mixage_mastering",
     "description": "comment mixer et masteriser un morceau", "content": "compresse le bus"},
    {"name": "Gammes et modes", "slug": "gammes_modes",
     "description": "choisir une gamme pour une mélodie", "content": "la mineur naturel"},
]

_PISTES = "\n\n## Fichiers du projet probablement pertinents\n- `notes.md` (pertinence 0.59)"


@pytest.fixture
def memoire(tmp_path, monkeypatch):
    monkeypatch.setattr("config.MEMORY_DIR", tmp_path)
    return ConversationMemory(session_id="ctx")


def _orchestrateur(memoire, monkeypatch, *, coder=False, pistes=_PISTES):
    """Orchestrateur partiel (MagicMock) sur une VRAIE mémoire : seul
    _inject_system_prompt tourne, avec ses sections stables figées."""
    monkeypatch.setattr(orch_mod, "load_skills", lambda: _SKILLS)
    monkeypatch.setattr(orch_mod, "SKILLS_ROUTER_ENABLED", False)
    monkeypatch.setattr(orch_mod, "SKILLS_ON_CODER_ENABLED", True)
    o = MagicMock()
    o.memory = memoire
    o._code_model_active = coder
    o._on_skills_selected = None
    o._relevant_files_section.return_value = pistes
    o.lt_memory.format_for_prompt.return_value = "\n\n## Mémoire longue terme\n- aime le zouk"
    o.profiler.get_profile_for_prompt.return_value = "\n\n## Profil\n- python"
    o.conventions.detect.return_value.format_for_prompt.return_value = ""
    o.error_memory.format_for_prompt.return_value = ""
    return o


def _tour(o, memoire, requete, task_type="explain"):
    memoire.add_message("user", requete)
    Orchestrator._inject_system_prompt(o, task_type=task_type, query=requete)
    return memoire.get_messages_for_api()


class TestSystemeStable:
    def test_systeme_identique_quelles_que_soient_les_skills(self, memoire, monkeypatch):
        o = _orchestrateur(memoire, monkeypatch)
        premier = _tour(o, memoire, "mixer et masteriser mon morceau")
        assert "Mixage et mastering" in o._contexte_tour
        memoire.add_message("assistant", "ok")
        second = _tour(o, memoire, "quelle gamme pour une mélodie triste")
        assert "Gammes et modes" in o._contexte_tour
        assert premier[0] == second[0]
        systeme = second[0]["content"]
        for variable in ("Mixage", "Gammes", "notes.md", "Contexte préparé"):
            assert variable not in systeme
        # Ce qui tient la session reste bien dans le système.
        assert "Dossier projet actif" in systeme
        assert "aime le zouk" in systeme and "## Profil" in systeme

    def test_les_skills_permanents_restent_dans_le_systeme(self, memoire, monkeypatch):
        """utilisateur_*/conventions_* : du profil, stable sur la session. Dans
        le système ils sont en cache ; dans le tour, ~3,4 k tokens seraient
        re-prefillés à chaque message."""
        o = _orchestrateur(memoire, monkeypatch)
        rendu = _tour(o, memoire, "mixer et masteriser mon morceau")
        assert "Scarlett 2i2" in rendu[0]["content"]
        assert "Scarlett 2i2" not in o._contexte_tour
        assert "Mixage et mastering" in o._contexte_tour

    def test_coder_slim_fige_skills_et_pistes_au_tour(self, memoire, monkeypatch):
        compatible = [{**s, "code_compatible": True} for s in _SKILLS]
        o = _orchestrateur(memoire, monkeypatch, coder=True)
        monkeypatch.setattr(orch_mod, "load_skills", lambda: compatible)
        rendu = _tour(o, memoire, "mixer et masteriser mon morceau", task_type="feature")
        assert rendu[0]["content"] == orch_mod._CODER_SLIM_PROMPT
        assert "notes.md" in rendu[-1]["content"]
        assert "Mixage et mastering" in rendu[-1]["content"]

    def test_sans_skill_ni_piste_le_message_part_tel_quel(self, memoire, monkeypatch):
        o = _orchestrateur(memoire, monkeypatch, pistes="")
        rendu = _tour(o, memoire, "bonjour")
        assert o._contexte_tour == ""
        assert rendu[-1]["content"] == "bonjour"


class TestAncrage:
    def test_contexte_rendu_sur_le_seul_message_courant(self, memoire, monkeypatch):
        o = _orchestrateur(memoire, monkeypatch)
        _tour(o, memoire, "mixer et masteriser mon morceau")
        memoire.add_message("assistant", "fait")
        rendu = _tour(o, memoire, "quelle gamme pour une mélodie triste")
        users = [m["content"] for m in rendu if m["role"] == "user"]
        # L'ancien tour repart NU : pas d'accumulation de contextes.
        assert users[0] == "mixer et masteriser mon morceau"
        assert users[1].startswith("quelle gamme pour une mélodie triste\n\n---\n")
        assert users[1].endswith(o._contexte_tour)
        assert sum("Contexte préparé" in u for u in users) == 1

    def test_jamais_dans_content_ni_sur_disque(self, memoire, monkeypatch):
        o = _orchestrateur(memoire, monkeypatch)
        _tour(o, memoire, "mixer et masteriser mon morceau")
        assert memoire.messages[-1]["content"] == "mixer et masteriser mon morceau"
        memoire.save()
        disque = json.loads(memoire.memory_file.read_text(encoding="utf-8"))
        assert "Mixage et mastering" not in json.dumps(disque, ensure_ascii=False)

    def test_les_relances_ne_deplacent_pas_le_prefixe(self, memoire, monkeypatch):
        """Anti-stall, auto-continue, synthèse forcée… ajoutent des `user` en
        cours de tour : chaque appel doit PROLONGER le précédent, sinon le
        préfixe du tour est recalculé à chaque itération ReAct."""
        o = _orchestrateur(memoire, monkeypatch)
        appel_1 = _tour(o, memoire, "mixer et masteriser mon morceau")
        memoire.add_tool_call_message([{"id": "c1", "type": "function",
                                        "function": {"name": "read_file", "arguments": "{}"}}])
        memoire.add_tool_result("c1", "read_file", "contenu")
        appel_2 = memoire.get_messages_for_api()
        memoire.messages.append({"role": "user", "content": "Continue.", "timestamp": None})
        appel_3 = memoire.get_messages_for_api()
        assert appel_2[:len(appel_1)] == appel_1
        assert appel_3[:len(appel_2)] == appel_2
        assert appel_3[-1]["content"] == "Continue."

    def test_la_fenetre_glissante_ne_perd_pas_le_contexte(self, memoire):
        """Le contexte vivait dans le système, que la fenêtre ne rogne jamais :
        si son ancre est évincée, il passe au message user suivant."""
        memoire.add_message("user", "question")
        memoire.ancrer_contexte_tour("\n\nCTX")
        memoire.messages.append({"role": "user", "content": "Continue.", "timestamp": None})
        assert memoire._pop_oldest_group()
        assert memoire.get_messages_for_api()[-1]["content"] == "Continue.\n\nCTX"

    def test_le_contexte_compte_dans_le_budget(self, memoire):
        memoire.add_message("user", "question")
        nu = ConversationMemory._estimate_tokens(memoire.messages[-1])
        memoire.ancrer_contexte_tour(" mot" * 500)
        assert ConversationMemory._estimate_tokens(memoire.messages[-1]) > nu + 100
