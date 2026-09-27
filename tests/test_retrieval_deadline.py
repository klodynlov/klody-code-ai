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


class _IndexEspion:
    """Index d'embeddings qui NOTE chaque accès et ne cherche rien.

    Juge des tests « le retrieval ne part pas » : un temps de réponse court ne
    prouvait rien. Vécu le 2026-09-27 : sans retrieval coupé, le vrai
    `_embed_batch` levait `NotConfiguredError` en quelques ms en isolé — vert —
    et chargeait bge-m3 en suite complète, où klody_memory avait été configuré
    en amont — rouge à 2,07 s. Le même test mesurait l'état du processus, pas
    la garde. Un accès à l'index, lui, ne dépend de rien d'autre que du code.
    """

    def __init__(self):
        self.appels: list[str] = []

    def is_available(self):
        self.appels.append("is_available")
        return True

    def search(self, query, k=5):
        self.appels.append(f"search({query!r})")
        return []


def _make_orchestrator(monkeypatch, tmp_path, embed_batch_fn=None, *, retrieval_actif=True):
    """Construit un Orchestrator minimal.

    `retrieval_actif` est un PARAMÈTRE, pas un réglage à poser avant l'appel :
    ce helper pose lui-même le flag, et un `monkeypatch.setattr(…, False)` écrit
    AVANT lui était donc écrasé en silence — le test « retrieval désactivé » a
    exercé le retrieval ACTIF depuis sa création (constaté le 2026-09-27).

    `embed_batch_fn` absent ⇒ faux instantané, jamais le vrai : le vrai charge
    bge-m3 (plusieurs secondes) ou lève, selon ce que les tests précédents ont
    laissé dans le processus ; et quand il lève, `_constater_panne` coupe
    `tools.embeddings.is_available()` pour 10 min, au niveau du MODULE — état
    qui fuit vers tous les tests suivants.
    """
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
    monkeypatch.setattr(orch_mod, "RETRIEVAL_INJECT_ENABLED", retrieval_actif)
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

    monkeypatch.setattr(cs_mod, "_embed_batch", embed_batch_fn or _fake_embed_batch_fast)

    from tools import embeddings as emb_mod
    monkeypatch.setattr(emb_mod, "is_available", lambda: True)

    memory = ConversationMemory()
    orch = orch_mod.Orchestrator(memory)
    orch.file_manager = FileManager(root=project_root)
    # L'index est paresseux et figé sur la racine du CONSTRUCTEUR : si __init__
    # l'a touché, il pointerait encore sur le vrai PROJECT_ROOT. Vu en mutant la
    # garde « requête vide » : un test voisin rendait `skull_generator.py`.
    orch._embed_index = None
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
        """RETRIEVAL_INJECT_ENABLED=False → '' immédiat, sans même toucher l'index.

        Le JUGE est l'index, pas le chronomètre : seul, `elapsed < 0.1` passait
        en isolé avec le retrieval ACTIF (repli rapide sur erreur) et rougissait
        en suite complète (bge-m3 chargé) — il jugeait l'état du processus, pas
        la garde. Le seuil reste en second verrou (« immédiat ») : flag coupé,
        le retour se compte en microsecondes, il ne peut plus rougir à tort."""
        orch = _make_orchestrator(monkeypatch, tmp_path, retrieval_actif=False)
        espion = _IndexEspion()
        orch._embed_index = espion

        t0 = time.perf_counter()
        result = orch._relevant_files_section("test")
        elapsed = time.perf_counter() - t0

        assert result == ""
        assert espion.appels == [], f"retrieval coupé mais index interrogé : {espion.appels}"
        assert elapsed < 0.1

    def test_query_vide_retourne_vide(self, monkeypatch, tmp_path):
        """Requête vide → '' sans toucher l'index (retrieval pourtant actif)."""
        orch = _make_orchestrator(monkeypatch, tmp_path)
        espion = _IndexEspion()
        orch._embed_index = espion

        assert orch._relevant_files_section("") == ""
        assert orch._relevant_files_section("   ") == ""
        assert espion.appels == [], f"requête vide mais index interrogé : {espion.appels}"

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
