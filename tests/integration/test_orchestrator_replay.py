"""Tests d'intégration replay — orchestrator + LLM stubbé.

Chaque fixture JSON décrit un scénario complet : prompt utilisateur, décision
router, suite de réponses LLM (content + tool_calls), et expectations.

Le scénario #5 fige la régression du 27/05 (max_tokens non défini → HTML
tronqué) : on doit pouvoir traiter un tool_call avec JSON arguments tronqués
SANS crasher, et le LLM doit pouvoir retry.
"""
from __future__ import annotations

from pathlib import Path

import pytest


SCENARIOS = [
    "01_explain_simple",
    "02_write_file_python",
    "03_multi_iteration_refactor",
    "04_anti_stall_recovery",
    "05_max_tokens_truncated_regression",
    "06_text_to_action_fallback",
    "07_anti_stall_escalation",
    "08_read_then_write",
    "09_unknown_tool",
    "10_max_iterations_cap",
    "11_search_then_explain",
]


def _seed_files(project_root: Path, files: dict) -> None:
    for rel, content in files.items():
        path = project_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _collect_tool_invocations(orch) -> list[str]:
    """Liste des noms de tool calls effectivement invoqués (dans l'ordre)."""
    invoked = []
    for m in orch.memory.messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                invoked.append(tc["function"]["name"])
    return invoked


def _final_assistant_content(orch) -> str:
    for m in reversed(orch.memory.messages):
        if m.get("role") == "assistant" and m.get("content"):
            return m["content"]
    return ""


def _assert_expectations(
    orch, fake_llm, fixture: dict, project_root: Path
) -> None:
    exp = fixture.get("expectations", {})

    # 1. Appels LLM dans la plage attendue
    if "min_llm_calls" in exp:
        assert fake_llm.consumed >= exp["min_llm_calls"], (
            f"Attendu ≥{exp['min_llm_calls']} appels LLM, observé {fake_llm.consumed}"
        )
    if "max_llm_calls" in exp:
        assert fake_llm.consumed <= exp["max_llm_calls"], (
            f"Attendu ≤{exp['max_llm_calls']} appels, observé {fake_llm.consumed} "
            f"(orchestrator part en boucle ?)"
        )

    # 2. Tool calls invoqués
    invoked = _collect_tool_invocations(orch)
    if "tool_calls_invoked" in exp:
        assert invoked == exp["tool_calls_invoked"], (
            f"Tool calls attendus {exp['tool_calls_invoked']}, observé {invoked}"
        )
    if exp.get("no_tool_calls"):
        assert not invoked, f"Aucun tool call attendu, observé {invoked}"

    # 3. Contenu final
    final = _final_assistant_content(orch)
    for needle in exp.get("final_content_contains", []):
        assert needle in final, (
            f"'{needle}' absent du contenu final. Final: {final[:200]!r}"
        )

    # 4. Fichier créé
    file_check = exp.get("file_created")
    if file_check:
        path = project_root / file_check["path"]
        assert path.exists(), f"Fichier attendu absent: {path}"
        body = path.read_text(encoding="utf-8")
        if "content_contains" in file_check:
            assert file_check["content_contains"] in body, (
                f"'{file_check['content_contains']}' absent de {path}. "
                f"Contenu: {body[:200]!r}"
            )
        if "content_excludes" in file_check:
            assert file_check["content_excludes"] not in body, (
                f"'{file_check['content_excludes']}' devrait être absent de {path}"
            )

    # 5. Anti-stall (fixture #4) — détecté via le message nudge injecté en memory
    # (les flags _anti_stall_fired sont reset en fin de run, donc on cherche la trace)
    if exp.get("anti_stall_fired"):
        nudge_seen = any(
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and (
                "Ta dernière réponse était vide" in m["content"]
                or "Tu as annoncé un plan" in m["content"]
            )
            for m in orch.memory.messages
        )
        assert nudge_seen, (
            "Anti-stall n'a PAS injecté de nudge alors que la fixture le prévoit. "
            f"Messages: {[m.get('role') for m in orch.memory.messages]}"
        )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_replay_scenario(scenario, fake_orchestrator, fixture_loader, project_root):
    """Joue chaque fixture en bout-en-bout et vérifie ses expectations."""
    fixture = fixture_loader(scenario)

    # Seed files éventuels (pour les scénarios refactor)
    if "seed_files" in fixture:
        _seed_files(project_root, fixture["seed_files"])

    kwargs = {}
    if "max_iterations" in fixture:
        kwargs["max_iterations"] = fixture["max_iterations"]
    orch, fake_llm = fake_orchestrator(fixture, **kwargs)

    # Le scénario #5 ne doit PAS crasher malgré un tool_args JSON tronqué
    orch.run(fixture["user_prompt"])

    _assert_expectations(orch, fake_llm, fixture, project_root)
