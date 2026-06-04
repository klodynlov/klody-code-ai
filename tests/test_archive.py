"""bundle_zip : archive multi-fichiers, anti zip-slip, câblage, endpoint.

Le dossier de téléchargements est redirigé vers un tmp_path par fixture. Même
montage que tests/test_excel.py.
"""
import io
import json
import zipfile

import pytest

from tools.archive import bundle_zip
from tools.registry import get_tool_names


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    d = tmp_path / "_downloads"
    d.mkdir()
    monkeypatch.setattr("tools.archive.DOWNLOADS_DIR", d)
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
    assert "bundle_zip" in set(get_tool_names())


def test_dispatch(orch):
    assert "bundle_zip" in orch._dispatch


# ── Génération ───────────────────────────────────────────────────────────────

def test_archive_simple(downloads):
    res = bundle_zip("projet.zip", [
        {"name": "README.md", "content": "# Projet"},
        {"name": "src/app.py", "content": "print(1)"},
    ])
    assert res["status"] == "ok"
    assert res["filename"] == "projet.zip"
    assert res["download_url"] == "/api/files/projet.zip"
    assert set(res["entries"]) == {"README.md", "src/app.py"}
    with zipfile.ZipFile(downloads / "projet.zip") as zf:
        assert zf.read("src/app.py").decode() == "print(1)"
        assert sorted(zf.namelist()) == ["README.md", "src/app.py"]


def test_extension_forcee(downloads):
    assert bundle_zip("paquet", [{"name": "a.txt", "content": "x"}])["filename"] == "paquet.zip"


def test_anti_zip_slip(downloads):
    res = bundle_zip("z.zip", [
        {"name": "../../../etc/passwd", "content": "x"},
        {"name": "/abs/secret.txt", "content": "y"},
    ])
    assert res["status"] == "ok"
    with zipfile.ZipFile(downloads / "z.zip") as zf:
        for n in zf.namelist():
            assert not n.startswith("/")
            assert ".." not in n.split("/")


def test_sous_dossiers_conserves(downloads):
    res = bundle_zip("p.zip", [{"name": "src/components/Btn.tsx", "content": "x"}])
    assert "src/components/Btn.tsx" in res["entries"]


def test_noms_dupliques_uniquifies(downloads):
    res = bundle_zip("d.zip", [
        {"name": "a.txt", "content": "1"},
        {"name": "a.txt", "content": "2"},
    ])
    assert len(res["entries"]) == 2 == len(set(res["entries"]))
    with zipfile.ZipFile(downloads / "d.zip") as zf:
        assert len(zf.namelist()) == 2


def test_liste_vide_erreur(downloads):
    assert "error" in bundle_zip("v.zip", [])
    assert "error" in bundle_zip("v.zip", None)


def test_entrees_non_dict_ignorees(downloads):
    res = bundle_zip("m.zip", [{"name": "ok.txt", "content": "x"}, "pas un dict", 42])
    assert res["status"] == "ok"
    assert res["entries"] == ["ok.txt"]


def test_taille_max_nettoie(downloads, monkeypatch):
    monkeypatch.setattr("tools.archive._MAX_TOTAL_BYTES", 10)
    res = bundle_zip("big.zip", [{"name": "a.txt", "content": "x" * 50}])
    assert "error" in res
    assert not (downloads / "big.zip").exists()   # fichier partiel nettoyé


# ── Orchestrateur + endpoint ─────────────────────────────────────────────────

def test_execute_tool_et_event(orch, downloads):
    events: list[dict] = []
    orch._emit = events.append
    out = orch._execute_tool("bundle_zip", {
        "filename": "e2e.zip", "files": [{"name": "a.txt", "content": "x"}],
    })
    assert json.loads(out)["status"] == "ok"
    ready = [e for e in events if e.get("type") == "file_ready"]
    assert ready and ready[0]["kind"] == "zip" and ready[0]["filename"] == "e2e.zip"


class TestEndpointSertLeZip:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from api.server import app
        return TestClient(app)

    def test_telecharge_zip(self, client, downloads):
        bundle_zip("dl.zip", [{"name": "a.txt", "content": "hello"}])
        r = client.get("/api/files/dl.zip")
        assert r.status_code == 200
        assert "attachment" in r.headers.get("content-disposition", "")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:   # vrai zip servi
            assert zf.read("a.txt").decode() == "hello"
