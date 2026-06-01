"""Fixtures pytest pour les tests d'intégration replay.

Wire un Orchestrator dont le LLM/Router sont stubés. Test = scénario figé.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """PROJECT_ROOT isolé par test."""
    return tmp_path


@pytest.fixture
def fixture_loader():
    """Helper pour charger une fixture par nom (sans .json)."""
    def _load(name: str) -> dict:
        path = FIXTURES_DIR / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Fixture inconnue: {name}. Fixtures disponibles: "
                f"{[p.stem for p in FIXTURES_DIR.glob('*.json')]}"
            )
        return json.loads(path.read_text(encoding="utf-8"))
    return _load


@pytest.fixture
def fake_orchestrator(project_root: Path, monkeypatch: pytest.MonkeyPatch):
    """Factory qui retourne (orchestrator, fake_llm) câblés à partir d'une fixture.

    Usage:
        orch, llm = fake_orchestrator(fixture_dict)
        orch.run(fixture_dict["user_prompt"])
        assert llm.consumed >= 2
    """
    from tests.integration.replay_llm import FakeLLMClient, FakeRouter

    # Isole le projet
    monkeypatch.setenv("PROJECT_ROOT", str(project_root))

    def _make(fixture: dict, *, max_iterations: int = 6):
        # Import retardé pour que monkeypatch.setenv soit pris en compte
        from agent import orchestrator as orch_mod
        from agent.memory import ConversationMemory

        # Désactive Best-of-N (coûteux et pas le sujet du replay)
        monkeypatch.setattr(orch_mod, "BEST_OF_N_ENABLED", False)
        # Cap les itérations au cas où la fixture diverge
        monkeypatch.setattr(orch_mod, "MAX_ITERATIONS", max_iterations)
        # Désactive auto-exec sandbox sur .py (pollue les tests)
        monkeypatch.setattr(orch_mod, "SANDBOX_AUTO_EXEC", False)

        # Patch LLMClient : retournera notre FakeLLMClient
        fake_llm = FakeLLMClient(fixture)
        monkeypatch.setattr(orch_mod, "LLMClient", lambda *_a, **_kw: fake_llm)

        # Patch Router si la fixture en spécifie un
        router_decision = fixture.get("router_decision")
        if router_decision:
            fake_router = FakeRouter(router_decision)
            # Le Router est instancié lazy par property — on remplace la classe importée
            from agent import router as router_mod
            monkeypatch.setattr(
                router_mod, "Router", lambda *_a, **_kw: fake_router
            )

        # Désactive le profiler (suggestions + threading)
        noop_profiler = SimpleNamespace(
            track_request=lambda *_a, **_kw: None,
            track_tool_usage=lambda *_a, **_kw: None,
            get_suggestions=lambda *_a, **_kw: [],
            get_profile_for_prompt=lambda *_a, **_kw: "",
            stats=lambda: {},
        )
        monkeypatch.setattr(orch_mod, "get_profiler", lambda: noop_profiler)

        # Désactive l'extraction mid-session (thread daemon qui pollue les logs)
        monkeypatch.setattr(
            orch_mod.Orchestrator, "_mid_session_extract", lambda self: None
        )

        # Désactive load_skills (lit un fichier global)
        monkeypatch.setattr(orch_mod, "load_skills", lambda: [])

        memory = ConversationMemory()
        orch = orch_mod.Orchestrator(memory)
        # Force le file_manager sur tmp_path (PROJECT_ROOT n'est résolu qu'au boot config)
        from tools.file_manager import FileManager
        orch.file_manager = FileManager(root=project_root)
        return orch, fake_llm

    return _make
