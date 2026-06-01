"""Tests d'intégration WebSocket FastAPI.

Vérifie le plumbing du endpoint /api/ws sans nécessiter de LLM réel :
- session_init au connect
- ping/pong
- session_new
- disconnect → _stop_flag levé (filet de sécurité contre MLX zombie)

NB : on ne joue PAS un round-trip chat complet ici (nécessiterait de mocker
le client OpenAI bas niveau). Les scénarios chat replay sont couverts par
test_orchestrator_replay.py.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """TestClient FastAPI avec services LibraryBrain mockés."""
    # Désactive le boot LibraryBrain (sinon spawn d'un serveur RAG)
    monkeypatch.setattr("services.ensure_librarybrain", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        "services.get_librarybrain_status",
        lambda: {"running": False, "books": 0, "url": ""},
    )

    from api.server import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def test_health_endpoint_degraded_when_llm_down(client, monkeypatch):
    """Sans backend LLM joignable, /health doit retourner 503 + 'degraded'."""
    # Sonde forcée à down (sinon le test dépend de l'environnement : il échouait
    # quand un vrai MLX/Ollama tournait sur la machine de dev).
    async def _down_probe(*_a, **_kw):
        return False
    monkeypatch.setattr("api.server._probe_url", _down_probe)
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["service"] == "klody-api"
    assert "checks" in body
    assert body["checks"]["llm_backend"] == "down"


def test_health_endpoint_ok_when_llm_reachable(client, monkeypatch):
    """Avec backend LLM joignable (mocké), /health doit retourner 200 + 'ok'."""
    async def _ok_probe(*_a, **_kw):
        return True
    monkeypatch.setattr("api.server._probe_url", _ok_probe)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["llm_backend"] == "ok"


def test_metrics_endpoint_exposes_prometheus_format(client):
    """/metrics doit exposer le format texte Prometheus avec nos compteurs."""
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    body = r.text
    # Au moins quelques-uns de nos compteurs Klody doivent apparaître
    assert "klody_ws_connections_total" in body
    assert "klody_chat_requests_total" in body
    assert "klody_tool_calls_total" in body


def test_status_endpoint(client):
    """/api/status doit retourner backend + model + librarybrain state."""
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    # Champs minimaux attendus
    assert "model" in body
    assert "backend" in body or "librarybrain" in body  # selon version du payload


def test_ws_session_init_on_connect(client):
    """Le WS doit envoyer session_init dès l'accept."""
    with client.websocket_connect("/api/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "session_init"
        assert isinstance(msg["session_id"], str)
        assert msg["session_id"]  # non-vide
        assert "model" in msg


def test_ws_ping_pong(client):
    """ping → pong."""
    with client.websocket_connect("/api/ws") as ws:
        # consomme session_init + tout event de boot (conventions_loaded, recurrent_errors)
        seen_init = False
        while not seen_init:
            msg = ws.receive_json()
            if msg["type"] == "session_init":
                seen_init = True

        # Drain les messages de boot non-bloquants (timeout court)
        ws.send_json({"type": "ping"})
        # Cherche le pong (en absorbant d'éventuels événements de boot intercalés)
        for _ in range(5):
            reply = ws.receive_json()
            if reply["type"] == "pong":
                return
        pytest.fail("Pas de pong reçu après ping")


def test_ws_session_new_creates_fresh_session(client):
    """session_new → nouveau session_id différent."""
    with client.websocket_connect("/api/ws") as ws:
        first_init = None
        while first_init is None:
            msg = ws.receive_json()
            if msg["type"] == "session_init":
                first_init = msg

        first_sid = first_init["session_id"]

        ws.send_json({"type": "session_new"})

        # Trouver le 2e session_init
        for _ in range(5):
            msg = ws.receive_json()
            if msg["type"] == "session_init":
                assert msg["session_id"] != first_sid, (
                    "session_new doit générer un nouveau session_id"
                )
                return
        pytest.fail("Pas de session_init après session_new")


def test_ws_disconnect_sets_stop_flag(client):
    """Filet de sécurité : disconnect → _stop_flag levé (évite MLX zombie)."""
    from api import server

    # Reset flag avant le test
    server._stop_flag[0] = False

    with client.websocket_connect("/api/ws") as ws:
        # Consomme session_init
        while True:
            msg = ws.receive_json()
            if msg["type"] == "session_init":
                break

    # Sortie du with → close → handler doit setter le stop_flag
    # Note: TestClient ferme proprement, peut prendre un tick.
    import time
    for _ in range(20):
        if server._stop_flag[0]:
            return
        time.sleep(0.05)
    pytest.fail("_stop_flag pas levé après disconnect")
