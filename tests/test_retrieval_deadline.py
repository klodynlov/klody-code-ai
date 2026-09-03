"""Retrieval proactif borné par échéance — lot 1.4.

Vérifie que _relevant_files_section ne bloque jamais le tour, même quand
l'index ou l'embedding dort.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import RETRIEVAL_BUILD_DEADLINE_S

# -- helpers ----------------------------------------------------------------- #

def _fake_embed_batch_slow(texts, timeout=60.0):
    """Simule un _embed_batch qui dort 10 s (bien au-delà de l'échéance)."""
    time.sleep(10)
    return [[0.1] * 1024 for _ in texts]


def _fake_embed_batch_fast(texts, timeout=60.0):
    """_embed_batch instantané qui rend des vecteurs valides."""
    return [[0.1] * 1024 for _ in texts]


def _make_orchestrator(monkeypatch, tmp_path, embed_batch_fn=None):
    """Construit un Orchestrator minimal avec retrieval actif."""
    from agent import orchestrator as orch_mod, router as router_mod
    from agent.memory import ConversationMemory
    from tools import code_search as cs_mod
    from tools.file_manager import FileManager

    project_root = tmp_path / "project"
    project_root.mkdir(exist_ok=True)
    (project_root / "example.py").write_text("def hello(): pass\n")

    monkeypatch.setenv("PROJECT_ROOT", str(project_root))
    monkeypatch.setattr(orch_mod, "BEST_OF_N_ENABLED", False)
    monkeypatch.setattr(orch_mod, "MAX_ITERATIONS", 1)
    monkeypatch.setattr(orch_mod, "SANDBOX_AUTO_EXEC", False)
    monkeypatch.setattr(orch_mod, "ROUTER_ENABLED", False)
    monkeypatch.setattr(orch_mod, "RETRIEVAL_INJECT_ENABLED", True)
    monkeypatch.setattr(orch_mod, "RETRIEVAL_MIN_SCORE", 0.0)

    noop_profiler = SimpleNamespace(
        track_request=lambda *a, **kw: None,
        track_tool_usage=lambda *a, **kw: None,
        get_suggestions=lambda *a, **kw: [],
        get_profile_for_prompt=lambda *a, **kw: "",
        stats=lambda: {},
    )
    monkeypatch.setattr(orch_mod, "get_profiler", lambda: noop_profiler)
    monkeypatch.setattr(orch_mod.Orchestrator, "_mid_session_extract", lambda self: None)
    monkeypatch.setattr(orch_mod, "load_skills", lambda: [])

    if embed_batch_fn:
        monkeypatch.setattr(cs_mod, "_embed_batch", embed_batch_fn)

    from tools import embeddings as emb_mod
    monkeypatch.setattr(emb_mod, "is_available", lambda: True)

    memory = ConversationMemory()
    orch = orch_mod.Orchestrator(memory)
    orch.file_manager = FileManager(root=project_root)
    return orch


# -- tests ------------------------------------------------------------------- #

class TestRetrievalDeadline:

    def test_deadline_config_existe(self):
        assert isinstance(RETRIEVAL_BUILD_DEADLINE_S, float)
        assert RETRIEVAL_BUILD_DEADLINE_S > 0

    def test_embed_lent_ne_bloque_pas(self, monkeypatch, tmp_path):
        """Un _embed_batch qui dort 10 s ne bloque pas le tour."""
        monkeypatch.setattr(
            "config.RETRIEVAL_BUILD_DEADLINE_S", 0.3,
        )
        monkeypatch.setattr(
            "agent.orchestrator.RETRIEVAL_BUILD_DEADLINE_S", 0.3,
        )
        orch = _make_orchestrator(monkeypatch, tmp_path, _fake_embed_batch_slow)

        t0 = time.perf_counter()
        result = orch._relevant_files_section("hello world")
        elapsed = time.perf_counter() - t0

        assert elapsed < 1.0, f"retrieval a bloqué {elapsed:.1f} s au lieu de respecter l'échéance"
        assert result == "", "devrait rendre '' quand l'échéance est dépassée"

    def test_embed_rapide_rend_des_pistes(self, monkeypatch, tmp_path):
        """Un retrieval rapide rend des pistes normalement."""
        monkeypatch.setattr(
            "config.RETRIEVAL_BUILD_DEADLINE_S", 5.0,
        )
        monkeypatch.setattr(
            "agent.orchestrator.RETRIEVAL_BUILD_DEADLINE_S", 5.0,
        )
        orch = _make_orchestrator(monkeypatch, tmp_path, _fake_embed_batch_fast)

        result = orch._relevant_files_section("hello function")
        assert "example.py" in result, f"devrait trouver example.py, reçu: {result!r}"

    def test_retrieval_desactive_retourne_vide(self, monkeypatch, tmp_path):
        """RETRIEVAL_INJECT_ENABLED=False → '' immédiat, pas de thread."""
        monkeypatch.setattr(
            "agent.orchestrator.RETRIEVAL_INJECT_ENABLED", False,
        )
        orch = _make_orchestrator(monkeypatch, tmp_path)

        t0 = time.perf_counter()
        result = orch._relevant_files_section("test")
        elapsed = time.perf_counter() - t0

        assert result == ""
        assert elapsed < 0.1

    def test_query_vide_retourne_vide(self, monkeypatch, tmp_path):
        """Requête vide → '' sans thread."""
        orch = _make_orchestrator(monkeypatch, tmp_path)
        assert orch._relevant_files_section("") == ""
        assert orch._relevant_files_section("   ") == ""

    def test_exception_dans_thread_silencieuse(self, monkeypatch, tmp_path):
        """Une exception dans le retrieval ne fuit pas."""
        def _explode(texts, timeout=60.0):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "agent.orchestrator.RETRIEVAL_BUILD_DEADLINE_S", 2.0,
        )
        orch = _make_orchestrator(monkeypatch, tmp_path, _explode)
        result = orch._relevant_files_section("test")
        assert result == ""

    def test_log_echeance_depassee(self, monkeypatch, tmp_path, caplog):
        """Un warning est loggé quand l'échéance est dépassée."""
        import logging

        monkeypatch.setattr(
            "config.RETRIEVAL_BUILD_DEADLINE_S", 0.1,
        )
        monkeypatch.setattr(
            "agent.orchestrator.RETRIEVAL_BUILD_DEADLINE_S", 0.1,
        )
        orch = _make_orchestrator(monkeypatch, tmp_path, _fake_embed_batch_slow)

        with caplog.at_level(logging.WARNING, logger="agent.orchestrator"):
            orch._relevant_files_section("test")

        assert any("échéance" in r.message for r in caplog.records), (
            f"devrait logger un warning d'échéance, logs: {[r.message for r in caplog.records]}"
        )
