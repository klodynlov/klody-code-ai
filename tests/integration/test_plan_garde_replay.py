"""Garde « plan laissé ouvert », joué de bout en bout dans la vraie boucle ReAct.

Le faux LLM rejoue des réponses scriptées ; tout le reste est réel : dispatch de
`plan_tache`, plan porté par la mémoire de session, garde de fin de tour, rappel
du plan dans le contexte du tour. Les drapeaux de garde sont remis à zéro en fin
de `run()` : on lit `trace_dernier_tour` et les traces laissées en mémoire
(piège « Les flags de garde sont remis à zéro en fin de run », CLAUDE.md).
"""
from __future__ import annotations

import json

from tools.plan_tache import EN_COURS, FAIT

DECISION = {"difficulty": "medium", "task_type": "explain", "reasoning": "test"}
RELANCE = "STOP — ne conclus pas encore. Ton plan a des étapes ouvertes"


def _appel(nom: str, i: int, **args) -> dict:
    return {"content": "", "tool_calls": [{
        "id": f"c{i}", "type": "function",
        "function": {"name": nom, "arguments": json.dumps(args, ensure_ascii=False)},
    }]}


def _texte(t: str) -> dict:
    return {"content": t, "tool_calls": None}


def _relances(orch) -> list[str]:
    return [m["content"] for m in orch.memory.messages
            if m.get("role") == "user" and RELANCE in str(m.get("content"))]


def test_conclure_avec_des_etapes_ouvertes_est_refuse_une_fois(fake_orchestrator):
    fixture = {"name": "plan_ouvert", "router_decision": DECISION, "llm_responses": [
        _appel("plan_tache", 0, action="definir",
               etapes=["Lire le fichier", "Compter les lignes", "Rédiger le résumé"]),
        _appel("plan_tache", 1, action="cocher", numero=1, statut="fait"),
        _texte("Voilà le résumé demandé."),                       # conclusion prématurée
        _appel("plan_tache", 2, action="cocher", numero=2, statut="fait"),
        _appel("plan_tache", 3, action="cocher", numero=3, statut="abandonne",
               note="le fichier est vide"),
        _texte("Fait : lecture et comptage. Résumé abandonné : fichier vide."),
    ]}
    orch, llm = fake_orchestrator(fixture)
    orch.run("Lis le fichier, compte ses lignes et résume-le.")

    assert llm.consumed == 6
    assert len(_relances(orch)) == 1
    assert "2. Compter les lignes" in _relances(orch)[0]
    assert orch.trace_dernier_tour["plan_guard_fired"] is True
    assert [e["statut"] for e in orch.memory.plan] == [FAIT, FAIT, "abandonne"]
    assert orch.memory.plan[2]["note"] == "le fichier est vide"


def test_une_seule_relance_meme_si_le_plan_reste_ouvert(fake_orchestrator):
    fixture = {"name": "plan_ouvert_obstine", "router_decision": DECISION, "llm_responses": [
        _appel("plan_tache", 0, action="definir", etapes=["A", "B"]),
        _texte("Terminé."),
        _texte("Terminé, vraiment."),
    ]}
    orch, llm = fake_orchestrator(fixture)
    orch.run("fais A puis B")
    assert llm.consumed == 3
    assert len(_relances(orch)) == 1
    assert orch.memory.plan[0]["statut"] == EN_COURS


def test_plan_clos_ne_relance_pas(fake_orchestrator):
    fixture = {"name": "plan_clos", "router_decision": DECISION, "llm_responses": [
        _appel("plan_tache", 0, action="definir", etapes=["A", "B"]),
        _appel("plan_tache", 1, action="cocher", numero=1),
        _appel("plan_tache", 2, action="cocher", numero=2),
        _texte("A et B sont faits."),
    ]}
    orch, llm = fake_orchestrator(fixture)
    orch.run("fais A puis B")
    assert llm.consumed == 4
    assert _relances(orch) == []
    assert orch.trace_dernier_tour["plan_guard_fired"] is False


def test_plan_ancien_non_touche_ne_relance_pas_mais_est_rappele(fake_orchestrator):
    """Une question hors sujet pendant qu'un plan est ouvert : pas de relance
    (le plan n'a pas été touché pendant ce tour), mais le plan est rappelé dans
    le contexte du tour — jamais dans le système, qui doit rester stable."""
    fixture = {"name": "plan_ancien", "router_decision": DECISION, "llm_responses": [
        _texte("Il est midi."),
    ]}
    orch, llm = fake_orchestrator(fixture)
    orch.memory.plan = [{"titre": "Étape ancienne", "statut": EN_COURS, "note": ""},
                        {"titre": "Suite", "statut": "a_faire", "note": ""}]
    orch.run("quelle heure est-il ?")
    assert llm.consumed == 1
    assert _relances(orch) == []
    assert "## Plan en cours" in orch._contexte_tour
    assert "Étape ancienne" in orch._contexte_tour
    assert "Plan en cours" not in orch.memory.messages[0]["content"]


def test_voir_le_plan_n_arme_pas_la_garde(fake_orchestrator):
    fixture = {"name": "plan_vu", "router_decision": DECISION, "llm_responses": [
        _appel("plan_tache", 0, action="voir"),
        _texte("Il reste l'étape 2."),
    ]}
    orch, llm = fake_orchestrator(fixture)
    orch.memory.plan = [{"titre": "A", "statut": FAIT, "note": ""},
                        {"titre": "B", "statut": EN_COURS, "note": ""}]
    orch.run("où en est le plan ?")
    assert llm.consumed == 2
    assert _relances(orch) == []
