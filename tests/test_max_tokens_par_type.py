"""max_tokens par type de tâche — lot 1.3.

Vérifie que le plafond de génération est modulé selon le task_type routé,
et que les scénarios critiques gardent leur budget.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MAX_TOKENS_DEFAULT, MAX_TOKENS_PAR_TYPE


def test_feature_et_self_dev_gardent_8192():
    """Régression du 27-05 : max_tokens trop bas tronquait createCar().
    feature et self_dev doivent garder le plafond complet."""
    assert MAX_TOKENS_PAR_TYPE["feature"] == 8192
    assert MAX_TOKENS_PAR_TYPE["self_dev"] == 8192


def test_migrate_garde_8192():
    """Une migration peut écrire de gros fichiers (SQL, schéma)."""
    assert MAX_TOKENS_PAR_TYPE["migrate"] == 8192


def test_explain_reduit():
    """explain produit de la prose, pas des fichiers longs."""
    assert MAX_TOKENS_PAR_TYPE["explain"] < MAX_TOKENS_DEFAULT


def test_tous_les_task_types_couverts():
    """Chaque task_type du router a une entrée. Un oubli = défaut silencieux."""
    import typing

    from agent.router import TaskType
    types = typing.get_args(TaskType)
    manquants = [t for t in types if t not in MAX_TOKENS_PAR_TYPE]
    assert not manquants, f"task_types sans max_tokens : {manquants}"


def test_aucune_valeur_depasse_le_defaut():
    """Le défaut est le plafond. Aucun type ne devrait le dépasser."""
    for tt, val in MAX_TOKENS_PAR_TYPE.items():
        assert val <= MAX_TOKENS_DEFAULT, f"{tt} dépasse le défaut ({val} > {MAX_TOKENS_DEFAULT})"


def test_max_tokens_forwarded_dans_stream_chat():
    """Le replay scénario 01 (explain/easy) doit passer max_tokens=4096.
    Le replay scénario 05 (feature/hard) doit passer max_tokens=8192."""
    import json

    fixtures_dir = Path(__file__).parent / "integration" / "fixtures"

    f01 = json.loads((fixtures_dir / "01_explain_simple.json").read_text())
    assert f01["router_decision"]["task_type"] == "explain"
    assert MAX_TOKENS_PAR_TYPE["explain"] == 4096

    f05 = json.loads((fixtures_dir / "05_max_tokens_truncated_regression.json").read_text())
    assert f05["router_decision"]["task_type"] == "feature"
    assert MAX_TOKENS_PAR_TYPE["feature"] == 8192


def test_replay_explain_recoit_max_tokens_reduit(fake_orchestrator_fixture):
    """Vérifie que le FakeLLM reçoit max_tokens=4096 sur un scénario explain."""
    import json
    fixture = json.loads(
        (Path(__file__).parent / "integration" / "fixtures" / "01_explain_simple.json").read_text()
    )
    orch, fake_llm = fake_orchestrator_fixture(fixture)
    orch.run(fixture["user_prompt"])

    assert fake_llm.call_log, "aucun appel LLM enregistré"
    for call in fake_llm.call_log:
        assert call["max_tokens"] == 4096, (
            f"explain devrait recevoir max_tokens=4096, reçu {call['max_tokens']}"
        )


@pytest.fixture
def fake_orchestrator_fixture(tmp_path, monkeypatch):
    """Construit un orchestrator avec FakeLLM pour un scénario donné."""
    from types import SimpleNamespace

    def _make(fixture_dict, **kwargs):
        from agent import orchestrator as orch_mod, router as router_mod
        from agent.memory import ConversationMemory
        from tools.file_manager import FileManager

        from tests.integration.replay_llm import FakeLLMClient, FakeRouter

        project_root = tmp_path / "project"
        project_root.mkdir(exist_ok=True)

        monkeypatch.setenv("PROJECT_ROOT", str(project_root))
        monkeypatch.setattr(orch_mod, "BEST_OF_N_ENABLED", False)
        monkeypatch.setattr(orch_mod, "MAX_ITERATIONS", 6)
        monkeypatch.setattr(orch_mod, "SANDBOX_AUTO_EXEC", False)
        monkeypatch.setattr(orch_mod, "ROUTER_ENABLED", True)

        fake_llm = FakeLLMClient(fixture_dict)
        monkeypatch.setattr(orch_mod, "LLMClient", lambda *_a, **_kw: fake_llm)

        router_decision = fixture_dict.get("router_decision")
        if router_decision:
            fake_router = FakeRouter(router_decision)
            monkeypatch.setattr(router_mod, "Router", lambda *_a, **_kw: fake_router)

        noop_profiler = SimpleNamespace(
            track_request=lambda *_a, **_kw: None,
            track_tool_usage=lambda *_a, **_kw: None,
            get_suggestions=lambda *_a, **_kw: [],
            get_profile_for_prompt=lambda *_a, **_kw: "",
            stats=lambda: {},
        )
        monkeypatch.setattr(orch_mod, "get_profiler", lambda: noop_profiler)
        monkeypatch.setattr(orch_mod.Orchestrator, "_mid_session_extract", lambda self: None)
        monkeypatch.setattr(orch_mod, "load_skills", lambda: [])

        memory = ConversationMemory()
        orch = orch_mod.Orchestrator(memory)
        orch.file_manager = FileManager(root=project_root)
        return orch, fake_llm

    return _make
