"""generate_text_file : génération, allowlist d'extensions, anti-traversée, câblage.

Le dossier de téléchargements est redirigé vers un tmp_path par fixture pour ne
jamais polluer config.DOWNLOADS_DIR réel. Même montage que tests/test_excel.py.
"""
import json
from pathlib import Path

import pytest

import tools.documents as documents
from tools.documents import generate_text_file
from tools.registry import get_tool_names


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    d = tmp_path / "_downloads"
    d.mkdir()
    monkeypatch.setattr(documents, "DOWNLOADS_DIR", d)
    monkeypatch.setattr("config.DOWNLOADS_DIR", d)
    return d


@pytest.fixture
def orch():
    from agent.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)

    class _Stub:
        def __getattr__(self, _):
            return lambda *a, **kw: None

    o.profiler = _Stub()
    return o


# ── Registre / câblage ───────────────────────────────────────────────────────

def test_enregistre():
    assert "generate_text_file" in set(get_tool_names())


def test_dispatch(orch):
    assert "generate_text_file" in orch._dispatch


# ── Génération ───────────────────────────────────────────────────────────────

def test_fichier_txt(downloads):
    res = generate_text_file("notes.txt", "bonjour\nmonde")
    assert res["status"] == "ok"
    assert res["filename"] == "notes.txt"
    assert res["download_url"] == "/api/files/notes.txt"
    f = downloads / "notes.txt"
    assert f.read_text(encoding="utf-8") == "bonjour\nmonde"
    assert res["path"] == str(f.resolve())


def test_extension_code_preservee(downloads):
    for name, ext in [("app.py", ".py"), ("Main.tsx", ".tsx"), ("data.csv", ".csv"),
                      ("index.html", ".html"), ("style.css", ".css"), ("q.sql", ".sql")]:
        assert generate_text_file(name, "x")["filename"].endswith(ext)


def test_extension_inconnue_devient_txt(downloads):
    assert generate_text_file("rapport.exe", "x")["filename"] == "rapport.txt"
    assert generate_text_file("sans_ext", "x")["filename"] == "sans_ext.txt"


def test_extension_secret_bloquee(downloads):
    # un .env ne doit jamais être servi tel quel
    assert generate_text_file("config.env", "SECRET=1")["filename"] == "config.txt"


def test_nom_accentue_preserve(downloads):
    res = generate_text_file("résumé février.md", "# Titre")
    assert res["filename"] == "résumé février.md"
    assert Path(res["path"]).exists()


def test_anti_traversee(downloads):
    res = generate_text_file("../../../etc/passwd.txt", "x")
    assert res["status"] == "ok"
    assert res["filename"] == "passwd.txt"
    assert Path(res["path"]).parent == downloads.resolve()


def test_contenu_trop_volumineux(downloads, monkeypatch):
    monkeypatch.setattr(documents, "_MAX_BYTES", 10)
    assert "error" in generate_text_file("big.txt", "x" * 50)


def test_contenu_vide_ok(downloads):
    res = generate_text_file("vide.txt", "")
    assert res["status"] == "ok"
    assert (downloads / "vide.txt").read_text(encoding="utf-8") == ""


# ── Orchestrateur : exécution + event file_ready ─────────────────────────────

def test_execute_tool(orch, downloads):
    out = orch._execute_tool("generate_text_file", {"filename": "a.md", "content": "# Hi"})
    data = json.loads(out)
    assert data["status"] == "ok"
    assert (downloads / "a.md").read_text(encoding="utf-8") == "# Hi"


def test_event_file_ready(orch, downloads):
    events: list[dict] = []
    orch._emit = events.append
    orch._execute_tool("generate_text_file", {"filename": "x.py", "content": "print(1)"})
    ready = [e for e in events if e.get("type") == "file_ready"]
    assert len(ready) == 1
    assert ready[0]["filename"] == "x.py"
    assert ready[0]["kind"] == "py"            # kind = extension du fichier
    assert ready[0]["download_url"] == "/api/files/x.py"


def test_pas_d_event_sans_emit(orch, downloads):
    out = orch._execute_tool("generate_text_file", {"filename": "cli.txt", "content": "x"})
    assert json.loads(out)["status"] == "ok"
