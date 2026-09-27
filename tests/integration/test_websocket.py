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

# URL ABSOLUE : le client WS de Starlette force sinon `Host: testserver`, que le
# garde d'hôte (api/garde_origine.py, anti-DNS-rebinding) refuse à juste titre.
WS_URL = "ws://127.0.0.1:8000/api/ws"



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

    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
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
    with client.websocket_connect(WS_URL) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "session_init"
        assert isinstance(msg["session_id"], str)
        assert msg["session_id"]  # non-vide
        assert "model" in msg


def test_ws_ping_pong(client):
    """ping → pong."""
    with client.websocket_connect(WS_URL) as ws:
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
    with client.websocket_connect(WS_URL) as ws:
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

    avant = set(server._stop_flags_actifs)
    with client.websocket_connect(WS_URL) as ws:
        # Consomme session_init
        while True:
            msg = ws.receive_json()
            if msg["type"] == "session_init":
                break
        # Le drapeau PROPRE à cette connexion (un par connexion depuis 2026-09-27).
        (cle,) = set(server._stop_flags_actifs) - avant
        drapeau = server._stop_flags_actifs[cle]
        # Le ping place le handler DANS sa boucle de réception : ce test vise
        # le chemin de production, `WebSocketDisconnect` reçu dans la boucle.
        # (Il a d'abord servi de RUSTINE : sans lui la fermeture tombait dans
        # le prologue, alors hors du try/finally — cf. test suivant.)
        ws.send_json({"type": "ping"})
        while ws.receive_json()["type"] != "pong":
            pass

    # Pas d'attente : la sortie du `with` JOINT la tâche ASGI (le TestClient
    # fait `fut.result()` après l'annulation), son `finally` a donc déjà couru.
    assert drapeau[0] is True, "stop_flag pas levé après disconnect"
    assert cle not in server._stop_flags_actifs, "registre non nettoyé"


def test_fermeture_pendant_le_prologue_rend_tout(client, monkeypatch):
    """Vécu le 2026-09-27 : fermée pendant le chargement des conventions, une
    connexion sortait sans drapeau levé, avec son entrée de registre et la
    jauge ws_active en fuite. Le TestClient ANNULE la tâche ASGI à la
    fermeture ; `CancelledError` (BaseException) tombait dans le prologue,
    alors hors du try/finally, et contournait tous les `except`."""
    import threading

    from api import server
    from prometheus_client import REGISTRY

    # Prologue bloqué EXPRÈS : la fermeture y tombe à coup sûr, là où la course
    # ne l'y plaçait que 11 fois sur 13.
    entre, libere = threading.Event(), threading.Event()

    def conventions_bloquees():
        entre.set()
        libere.wait(10)
        return {"conventions": [], "recurrent_errors": []}

    monkeypatch.setattr(server, "_load_project_info", conventions_bloquees)
    jauge_avant = REGISTRY.get_sample_value("klody_ws_active")
    avant = set(server._stop_flags_actifs)
    try:
        with client.websocket_connect(WS_URL) as ws:
            while ws.receive_json()["type"] != "session_init":
                pass
            (cle,) = set(server._stop_flags_actifs) - avant
            drapeau = server._stop_flags_actifs[cle]
            assert entre.wait(10), "le handler n'est jamais entré dans le prologue"
    finally:
        libere.set()

    assert drapeau[0] is True, "fermée dans le prologue : drapeau non levé"
    assert cle not in server._stop_flags_actifs, "registre non nettoyé"
    assert REGISTRY.get_sample_value("klody_ws_active") == jauge_avant, "jauge ws_active en fuite"


def test_deconnexion_de_b_n_arrete_pas_a(client):
    """Audit 2026-09-27 : le drapeau était GLOBAL — la fermeture d'une connexion
    B coupait la génération de A (0/30 tokens puis `done`)."""
    from api import server

    def ouvrir_et_saisir(ws, avant):
        while ws.receive_json()["type"] != "session_init":
            pass
        (cle,) = set(server._stop_flags_actifs) - avant
        return server._stop_flags_actifs[cle]

    # Instantané AVANT d'ouvrir : la poignée de main enregistre déjà le drapeau.
    avant_a = set(server._stop_flags_actifs)
    with client.websocket_connect(WS_URL) as ws_a:
        drapeau_a = ouvrir_et_saisir(ws_a, avant_a)
        avant_b = set(server._stop_flags_actifs)
        with client.websocket_connect(WS_URL) as ws_b:
            drapeau_b = ouvrir_et_saisir(ws_b, avant_b)
        # Pas d'attente : la sortie du `with` a joint la tâche ASGI de B. Le
        # sondage de 1 s qui était ici ne pouvait rien rattraper : fermée dans
        # son prologue, B ne levait JAMAIS son drapeau (rouge 11 fois sur 13).
        assert drapeau_b[0] is True, "B fermée : son drapeau doit être levé"
        assert drapeau_a[0] is False, "A vivante : sa génération ne doit pas être coupée"
