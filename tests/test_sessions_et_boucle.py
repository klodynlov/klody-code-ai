"""Sessions et boucle d'événements de l'API — quatre défauts de l'audit du 2026-09-27.

1. Renommer / archiver la session OUVERTE : la route n'écrit que le fichier, et
   le `save()` suivant de la conversation réécrivait l'état périmé — titre
   restauré, archivage annulé au message suivant.
2. Export d'une session au titre accentué (« ’ », « œ », emoji) : HTTP 500,
   l'en-tête Content-Disposition étant encodé en latin-1 (22 sessions réelles).
3. Le scan des conventions descendait dans `.venv` (1,9 s contre 11 ms).
4. La construction de l'orchestrateur (découverte MCP, jusqu'à 8,7 s) tournait
   DANS la boucle d'événements, gelant toutes les connexions et /health.
"""

from __future__ import annotations

import asyncio
import json
import time

import config
import pytest
from agent.memory import ConversationMemory
from fastapi.testclient import TestClient


@pytest.fixture
def dossier_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path)
    return tmp_path


def _modifier_sur_disque(mem: ConversationMemory, **champs):
    """Ce que font les routes rename/archive : réécrire le seul fichier."""
    data = json.loads(mem.memory_file.read_text(encoding="utf-8"))
    data.update(champs)
    mem.memory_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestMetaExterne:
    def test_renommage_externe_survit_au_message_suivant(self, dossier_sessions):
        mem = ConversationMemory()
        mem.add_message("user", "premier message")
        mem.save()
        _modifier_sur_disque(mem, title="Mon beau titre")
        mem.add_message("assistant", "réponse")
        mem.save()
        assert json.loads(mem.memory_file.read_text())["title"] == "Mon beau titre"

    def test_archivage_externe_survit_au_message_suivant(self, dossier_sessions):
        mem = ConversationMemory()
        mem.add_message("user", "premier message")
        mem.save()
        _modifier_sur_disque(mem, archived=True)
        mem.add_message("user", "encore")
        mem.save()
        assert json.loads(mem.memory_file.read_text())["archived"] is True

    def test_titre_pose_par_la_conversation_reste_ecrit(self, dossier_sessions):
        """Sans modification externe, c'est la mémoire qui décide."""
        mem = ConversationMemory()
        mem.save()
        mem.add_message("user", "un premier message qui donne le titre")
        mem.save()
        assert json.loads(mem.memory_file.read_text())["title"] == mem.title != ""


def test_export_titre_accentue_ne_rend_plus_500(dossier_sessions):
    import api.server as srv

    mem = ConversationMemory()
    mem.add_message("user", "salut")
    mem.title = "L’œuvre de Klody 🎵 — idées"
    mem.save()
    client = TestClient(srv.app, base_url="http://127.0.0.1:8000")
    r = client.get(f"/api/sessions/{mem.session_id}/export")
    assert r.status_code == 200
    dispo = r.headers["content-disposition"]
    assert "filename*=UTF-8''" in dispo
    dispo.encode("latin-1")  # l'en-tête lui-même doit rester encodable


def test_le_scan_des_conventions_n_entre_pas_dans_venv(tmp_path):
    from agent.conventions import _iter_source_files

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    lib = tmp_path / ".venv" / "lib" / "site-packages"
    lib.mkdir(parents=True)
    (lib / "b.py").write_text("y = 2\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "c.js").write_text("z")
    assert [p.relative_to(tmp_path).as_posix() for p in _iter_source_files(tmp_path)] == ["src/a.py"]


def test_l_orchestrateur_se_construit_hors_de_la_boucle(monkeypatch):
    """Le constructeur ne doit pas trouver de boucle en cours dans son thread."""
    import api.server as srv

    monkeypatch.setattr("services.ensure_librarybrain", lambda *_a, **_kw: True)
    monkeypatch.setattr("services.get_librarybrain_status",
                        lambda: {"running": False, "books": 0, "url": ""})
    vu: dict = {}

    def constructeur_espion(*_a, **_kw):
        try:
            asyncio.get_running_loop()
            vu["dans_la_boucle"] = True
        except RuntimeError:
            vu["dans_la_boucle"] = False
        raise RuntimeError("arrêt du test après la construction")

    monkeypatch.setattr(srv, "_build_streaming_orchestrator", constructeur_espion)
    client = TestClient(srv.app, base_url="http://127.0.0.1:8000")
    with client.websocket_connect("ws://127.0.0.1:8000/api/ws") as ws:
        while ws.receive_json()["type"] != "session_init":
            pass
        ws.send_json({"type": "chat", "content": "bonjour"})
        for _ in range(100):
            if "dans_la_boucle" in vu:
                break
            time.sleep(0.02)
    assert vu.get("dans_la_boucle") is False
