"""Tests d'intégration des routes REST + WebSocket non couvertes par
test_websocket.py (qui se concentre sur le handshake WS).

Couvre : /api/sessions, /api/memories (GET + DELETE), /api/stop,
/api/sessions/{id}/export, ws session_load (ok + not found), ws model_change.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """TestClient FastAPI avec MEMORY_DIR isolé sur tmp_path."""
    # Désactive LibraryBrain (sinon spawn d'un serveur RAG en boot)
    monkeypatch.setattr("services.ensure_librarybrain", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        "services.get_librarybrain_status",
        lambda: {"running": False, "books": 0, "url": ""},
    )
    # Isole le répertoire mémoire dans tmp_path
    fake_mem_dir = tmp_path / "memory"
    fake_mem_dir.mkdir()
    monkeypatch.setattr("api.server.MEMORY_DIR", fake_mem_dir)

    from fastapi.testclient import TestClient
    from api.server import app

    with TestClient(app) as c:
        yield c, fake_mem_dir


class TestSessions:
    def test_empty_list(self, client):
        c, _ = client
        r = c.get("/api/sessions")
        assert r.status_code == 200
        assert r.json() == []

    def test_lists_sessions_sorted_by_mtime(self, client):
        c, mem_dir = client
        # Crée 2 sessions
        for i, sid in enumerate(["older", "newer"]):
            f = mem_dir / f"memory_{sid}.json"
            f.write_text(json.dumps({
                "session_id": sid,
                "title": f"Session {sid}",
                "messages": [
                    {"role": "user", "content": f"hello {sid}"},
                ],
            }))
            # Force mtime ordre
            import os
            os.utime(f, (1000 + i * 10, 1000 + i * 10))
        r = c.get("/api/sessions")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        # newer en premier (sorted reverse=True)
        assert body[0]["id"] == "newer"
        assert body[1]["id"] == "older"
        assert body[0]["preview"].startswith("hello newer")

    def test_corrupted_session_skipped(self, client):
        c, mem_dir = client
        (mem_dir / "memory_good.json").write_text(json.dumps({
            "session_id": "good", "messages": []
        }))
        (mem_dir / "memory_bad.json").write_text("{not json")
        r = c.get("/api/sessions")
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()]
        assert "good" in ids
        assert "bad" not in ids


class TestSessionExport:
    def test_404_when_missing(self, client):
        c, _ = client
        r = c.get("/api/sessions/missing-session/export")
        assert r.status_code == 404
        assert "introuvable" in r.text.lower()

    def test_exports_markdown(self, client):
        c, mem_dir = client
        sid = "export-test"
        (mem_dir / f"memory_{sid}.json").write_text(json.dumps({
            "session_id": sid,
            "title": "Ma session de test",
            "messages": [
                {"role": "user", "content": "Bonjour"},
                {"role": "assistant", "content": "Salut !"},
                {"role": "system", "content": "ignored"},
                {"role": "tool", "content": "ignored"},
            ],
        }))
        r = c.get(f"/api/sessions/{sid}/export")
        assert r.status_code == 200
        assert "text/markdown" in r.headers["content-type"]
        body = r.text
        assert "Ma session de test" in body
        assert "Bonjour" in body and "Salut" in body
        # system/tool exclus
        assert "ignored" not in body


class TestMemoriesEndpoints:
    def test_list(self, client, monkeypatch):
        c, _ = client
        fake_entries = [
            {"key": "user.name", "content": "Alice", "category": "user", "updated_at": "2026-05-27"},
            {"key": "stack", "content": "Python", "category": "context", "updated_at": "2026-05-27"},
        ]

        class FakeLTM:
            def list_all(self): return fake_entries
            def forget(self, k): return f"Oublié : {k}" if k == "user.name" else "Non trouvé"

        monkeypatch.setattr("api.server.get_long_term_memory", lambda: FakeLTM())
        r = c.get("/api/memories")
        assert r.status_code == 200
        assert r.json() == fake_entries

    def test_delete_existing(self, client, monkeypatch):
        c, _ = client

        class FakeLTM:
            def list_all(self): return []
            def forget(self, k):
                return f"Oublié : {k}" if k == "knownkey" else "Non trouvé"

        monkeypatch.setattr("api.server.get_long_term_memory", lambda: FakeLTM())
        r = c.delete("/api/memories/knownkey")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_unknown(self, client, monkeypatch):
        c, _ = client

        class FakeLTM:
            def list_all(self): return []
            def forget(self, k): return "Non trouvé"

        monkeypatch.setattr("api.server.get_long_term_memory", lambda: FakeLTM())
        r = c.delete("/api/memories/unknown")
        assert r.status_code == 200
        assert r.json()["ok"] is False

    def test_add_memory(self, client, monkeypatch):
        c, _ = client
        captured = {}

        class FakeLTM:
            def list_all(self): return []
            def remember(self, key, content, category):
                captured.update(key=key, content=content, category=category)
                return f"Mémorisé : [{category}] {key}"

        monkeypatch.setattr("api.server.get_long_term_memory", lambda: FakeLTM())
        r = c.post("/api/memories", json={
            "key": "Stack Préférée", "content": "Python + FastAPI", "category": "preference",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert captured == {
            "key": "Stack Préférée", "content": "Python + FastAPI", "category": "preference",
        }

    def test_add_memory_categorie_invalide_retombe_sur_context(self, client, monkeypatch):
        c, _ = client
        captured = {}

        class FakeLTM:
            def list_all(self): return []
            def remember(self, key, content, category):
                captured["category"] = category
                return "Mémorisé"

        monkeypatch.setattr("api.server.get_long_term_memory", lambda: FakeLTM())
        r = c.post("/api/memories", json={"key": "k", "content": "v", "category": "n_importe_quoi"})
        assert r.status_code == 200
        assert captured["category"] == "context"

    def test_add_memory_champs_manquants(self, client):
        c, _ = client
        r = c.post("/api/memories", json={"key": "", "content": ""})
        assert r.status_code == 200
        assert r.json()["ok"] is False


class TestSkillsEndpoints:
    def test_list(self, client, monkeypatch):
        c, _ = client
        fake = [
            {"name": "Snippet FastAPI", "slug": "snippet_fastapi",
             "description": "boot rapide", "content": "...", "updated": "2026-05-29"},
        ]
        monkeypatch.setattr("api.server.load_skills", lambda: fake)
        r = c.get("/api/skills")
        assert r.status_code == 200
        assert r.json() == fake

    def test_delete_existing(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(
            "api.server.delete_skill",
            lambda slug: f"Compétence « {slug} » supprimée." if slug == "known" else "introuvable",
        )
        r = c.delete("/api/skills/known")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete_unknown(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr("api.server.delete_skill", lambda slug: f"Skill '{slug}' introuvable.")
        r = c.delete("/api/skills/ghost")
        assert r.status_code == 200
        assert r.json()["ok"] is False


class TestStopGeneration:
    def test_sets_stop_flag(self, client):
        c, _ = client
        from api import server
        server._stop_flag[0] = False
        r = c.post("/api/stop")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert server._stop_flag[0] is True


class TestWebSocketRouting:
    def test_model_change(self, client):
        c, _ = client
        with c.websocket_connect("/api/ws") as ws:
            # consomme session_init et events de boot
            while True:
                msg = ws.receive_json()
                if msg["type"] == "session_init":
                    break
            ws.send_json({"type": "model_change", "model": "qwen2.5-coder:32b"})
            # Boucle jusqu'à model_changed (peut être précédé de conventions_loaded)
            for _ in range(5):
                reply = ws.receive_json()
                if reply["type"] == "model_changed":
                    assert reply["model"] == "qwen2.5-coder:32b"
                    return
            pytest.fail("Pas de model_changed reçu")

    def test_session_load_missing(self, client):
        c, _ = client
        with c.websocket_connect("/api/ws") as ws:
            while True:
                msg = ws.receive_json()
                if msg["type"] == "session_init":
                    break
            ws.send_json({"type": "session_load", "session_id": "nonexistent-xyz"})
            for _ in range(5):
                reply = ws.receive_json()
                if reply["type"] == "error":
                    assert "introuvable" in reply["content"].lower()
                    return
            pytest.fail("Pas de réponse 'error' pour session_load inconnu")

    def test_session_load_existing(self, client):
        c, mem_dir = client
        sid = "saved-session"
        (mem_dir / f"memory_{sid}.json").write_text(json.dumps({
            "session_id": sid,
            "created_at": "2026-05-27T20:00:00",
            "title": "Test load",
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "ping"},
                {"role": "assistant", "content": "pong"},
            ],
        }))

        with c.websocket_connect("/api/ws") as ws:
            while True:
                msg = ws.receive_json()
                if msg["type"] == "session_init":
                    break
            ws.send_json({"type": "session_load", "session_id": sid})
            for _ in range(5):
                reply = ws.receive_json()
                if reply["type"] == "session_loaded":
                    assert reply["session_id"] == sid
                    # Doit exporter user + assistant, pas system
                    msgs = reply["messages"]
                    roles = [m["role"] for m in msgs]
                    assert "user" in roles and "assistant" in roles
                    assert "system" not in roles
                    return
            pytest.fail("Pas de session_loaded reçu")
