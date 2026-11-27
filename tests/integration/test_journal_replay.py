"""Journal de projet joué dans la vraie boucle ReAct (faux LLM, tout le reste réel).

Deux promesses : ce que le modèle note survit à la session (base SQLite, numéro
de session consigné), et un projet NOMMÉ dans la demande ramène son journal dans
le contexte du tour — jamais dans le système, qui doit rester un préfixe stable.
"""
from __future__ import annotations

import json
import sqlite3

import config
import pytest
from tools import journal_projet as jp

DECISION = {"difficulty": "medium", "task_type": "explain", "reasoning": "test"}


@pytest.fixture(autouse=True)
def _dossier(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _appel(nom: str, **args) -> dict:
    return {"content": "", "tool_calls": [{
        "id": "c0", "type": "function",
        "function": {"name": nom, "arguments": json.dumps(args, ensure_ascii=False)},
    }]}


def test_une_decision_notee_survit_a_la_session(fake_orchestrator):
    fixture = {"name": "journal_noter", "router_decision": DECISION, "llm_responses": [
        _appel("journal_projet", action="noter", projet="KaribTruck", type="decision",
               texte="Pas de caisse : hors périmètre HACCP"),
        {"content": "C'est noté.", "tool_calls": None},
    ]}
    orch, llm = fake_orchestrator(fixture)
    orch.run("Note qu'on ne fait pas de caisse dans KaribTruck.")
    assert llm.consumed == 2
    with sqlite3.connect(jp.chemin_base()) as cx:
        ligne = cx.execute("SELECT nom, type, texte, session FROM entrees").fetchone()
    assert ligne == ("KaribTruck", "decision", "Pas de caisse : hors périmètre HACCP",
                     orch.memory.session_id)
    resultat = [m for m in orch.memory.messages if m.get("role") == "tool"]
    assert "Noté dans le journal de KaribTruck : décision #1." in resultat[0]["content"]


def test_projet_nomme_ramene_son_journal_dans_le_tour(fake_orchestrator, monkeypatch):
    jp.noter("KaribTruck", "etat", "TestFlight 0.1.0 (40), PR #14 brouillon")
    jp.noter("KaribTruck", "tache", "Finir la PR #14")
    boucliers: list[str] = []
    from agent import orchestrator as orch_mod
    vrai_bouclier = orch_mod._shield

    def bouclier(section: str, label: str) -> str:
        boucliers.append(label)
        return vrai_bouclier(section, label)

    monkeypatch.setattr(orch_mod, "_shield", bouclier)
    fixture = {"name": "journal_tour", "router_decision": DECISION, "llm_responses": [
        {"content": "On en était à la PR #14.", "tool_calls": None},
    ]}
    orch, llm = fake_orchestrator(fixture)
    orch.run("On reprend KaribTruck ?")
    assert llm.consumed == 1
    assert "## Journal de projet" in orch._contexte_tour
    assert "TestFlight 0.1.0 (40)" in orch._contexte_tour and "☐ Finir la PR #14" in orch._contexte_tour
    assert "Journal de projet" not in orch.memory.messages[0]["content"]
    assert "journal" in boucliers


def test_projet_non_nomme_rien_dans_le_tour(fake_orchestrator):
    jp.noter("KaribTruck", "etat", "TestFlight")
    fixture = {"name": "journal_absent", "router_decision": DECISION, "llm_responses": [
        {"content": "Il fait beau.", "tool_calls": None},
    ]}
    orch, _ = fake_orchestrator(fixture)
    orch.run("Quel temps fait-il ?")
    assert "Journal de projet" not in (orch._contexte_tour or "")
