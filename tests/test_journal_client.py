"""
test_journal_client.py — émission d'événements vers le journal d'usage du gateway
(agent/journal_client.py + en-têtes X-Klody-* de agent/llm.py + instrumentation
_execute_tool de l'orchestrateur).

Hermétique : aucun réseau — urlopen mocké, événements capturés côté queue/HTTP.
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest
from agent import journal_client
from agent.llm import LLMClient


@pytest.fixture(autouse=True)
def _worker_propre(monkeypatch):
    """Chaque test repart sans worker (le singleton est module-global)."""
    monkeypatch.setattr(journal_client, "_queue", None)
    monkeypatch.delenv("KLODY_JOURNAL", raising=False)
    monkeypatch.delenv("KLODY_JOURNAL_URL", raising=False)
    yield
    monkeypatch.setattr(journal_client, "_queue", None)


def _drain(q, timeout=2.0):
    """Attend que la queue soit vidée par le worker."""
    deadline = time.monotonic() + timeout
    while not q.empty() and time.monotonic() < deadline:
        time.sleep(0.01)


# ── gateway_root ──────────────────────────────────────────────────────────────
def test_gateway_root_derive_de_mlx_base_url(monkeypatch):
    monkeypatch.setattr(journal_client.config, "MLX_BASE_URL", "http://localhost:8090/v1")
    assert journal_client.gateway_root() == "http://localhost:8090"


def test_gateway_root_override_env(monkeypatch):
    monkeypatch.setenv("KLODY_JOURNAL_URL", "http://127.0.0.1:9999/")
    assert journal_client.gateway_root() == "http://127.0.0.1:9999"


# ── emit ──────────────────────────────────────────────────────────────────────
def test_emit_poste_le_payload(monkeypatch):
    vus = []

    def fake_urlopen(req, timeout=None):
        vus.append((req.full_url, json.loads(req.data)))
        return MagicMock()

    with patch.object(journal_client.urllib.request, "urlopen", side_effect=fake_urlopen):
        journal_client.emit(kind="tool", name="search_books", status="ok",
                            session_id="s-1", latency_ms=42, meta={"mcp": False})
        _drain(journal_client._queue)

    (url, body), = vus
    assert url.endswith("/journal/event")
    assert body == {"app": "klody-ai", "kind": "tool", "name": "search_books",
                    "status": "ok", "session_id": "s-1", "latency_ms": 42,
                    "meta": {"mcp": False}}


def test_emit_desactive_par_env(monkeypatch):
    monkeypatch.setenv("KLODY_JOURNAL", "0")
    with patch.object(journal_client.urllib.request, "urlopen") as up:
        journal_client.emit(kind="tool", name="x")
    assert journal_client._queue is None       # worker jamais démarré
    up.assert_not_called()


def test_emit_avale_gateway_mort():
    """Gateway down : aucune exception ne remonte, l'appelant ne voit rien."""
    with patch.object(journal_client.urllib.request, "urlopen",
                      side_effect=OSError("connexion refusée")):
        journal_client.emit(kind="session", name="start", session_id="s-2")
        _drain(journal_client._queue)          # le worker survit à l'erreur
    journal_client.emit(kind="session", name="end", session_id="s-2")   # toujours OK


# ── En-têtes X-Klody-* du client LLM ─────────────────────────────────────────
def test_llm_client_entetes_app_et_session():
    llm = LLMClient()
    assert llm.client.default_headers["X-Klody-App"] == "klody-ai"
    assert "X-Klody-Session" not in llm.client.default_headers

    llm.set_session("abc123")
    assert llm.client.default_headers["X-Klody-Session"] == "abc123"
    assert llm.client.default_headers["X-Klody-App"] == "klody-ai"

    client_avant = llm.client
    llm.set_session("abc123")                  # no-op : même id
    assert llm.client is client_avant


def test_llm_switch_to_preserve_les_entetes():
    llm = LLMClient()
    llm.set_session("abc123")
    llm.switch_to("autre-modele", "http://localhost:8090/v1", "sk-x")
    assert llm.client.default_headers["X-Klody-App"] == "klody-ai"
    assert llm.client.default_headers["X-Klody-Session"] == "abc123"
    assert llm.model == "autre-modele"


# ── Instrumentation _execute_tool ─────────────────────────────────────────────
def _orchestrateur_minimal():
    """Orchestrator sans __init__ (pas de MCP/mémoire longue) : juste ce qu'il
    faut pour _execute_tool → dispatch + memory.session_id."""
    from agent.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.memory = MagicMock(session_id="s-42")
    orch.mcp = None
    return orch


def test_execute_tool_journalise_ok_et_erreur():
    orch = _orchestrateur_minimal()
    orch.__dict__["_dispatch_table"] = {"ok_tool": lambda args: "résultat propre",
                                        "bad_tool": lambda args: "ERREUR: cassé"}
    with patch.object(journal_client, "emit") as emit:
        assert orch._execute_tool("ok_tool", {}) == "résultat propre"
        assert orch._execute_tool("bad_tool", {}) == "ERREUR: cassé"
        assert orch._execute_tool("inconnu", {}) == "ERREUR: Outil inconnu 'inconnu'"

    (ok, bad, inconnu) = [c.kwargs for c in emit.call_args_list]
    assert ok["kind"] == "tool" and ok["name"] == "ok_tool" and ok["status"] == "ok"
    assert ok["session_id"] == "s-42" and ok["latency_ms"] >= 0
    assert bad["status"] == "error"
    assert inconnu["status"] == "error"


def test_execute_tool_exception_journalisee_error():
    orch = _orchestrateur_minimal()

    def boom(args):
        raise RuntimeError("explosé")

    orch.__dict__["_dispatch_table"] = {"boom": boom}
    with patch.object(journal_client, "emit") as emit:
        result = orch._execute_tool("boom", {})
    assert result.startswith("ERREUR")
    assert emit.call_args.kwargs["status"] == "error"
