"""Génération de classeurs Excel téléchargeables : module, câblage, endpoint.

Aucun réseau ni navigateur. openpyxl est requis pour la génération réelle
(importorskip) ; le chemin « openpyxl absent » est couvert en monkeypatchant le
flag. Le dossier de téléchargements est redirigé vers un tmp_path par fixture
pour ne jamais polluer config.DOWNLOADS_DIR réel.
"""
import json
from pathlib import Path

import pytest

from tools.excel import generate_excel
from tools.registry import get_tool_names

openpyxl = pytest.importorskip("openpyxl")


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    d = tmp_path / "_downloads"
    d.mkdir()
    monkeypatch.setattr("tools.excel.DOWNLOADS_DIR", d)
    monkeypatch.setattr("config.DOWNLOADS_DIR", d)
    return d


@pytest.fixture
def orch():
    """Orchestrator minimal : __new__ sans __init__ + profiler stubé.

    Les handlers de dispatch capturent `self` mais ne déréférencent leurs
    dépendances qu'à l'appel — generate_excel n'a besoin d'aucune (hormis
    `_emit`, optionnel). Même montage que tests/test_audio_wiring.py.
    """
    from agent.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)

    class _Stub:
        def __getattr__(self, _):
            return lambda *a, **kw: None

    o.profiler = _Stub()
    return o


# ── Registre / câblage ───────────────────────────────────────────────────────

def test_generate_excel_est_enregistre():
    assert "generate_excel" in set(get_tool_names())


def test_le_dispatch_a_un_handler(orch):
    assert "generate_excel" in orch._dispatch


# ── Génération ───────────────────────────────────────────────────────────────

def test_feuille_simple(downloads):
    res = generate_excel("ventes.xlsx", [
        {"name": "Ventes", "columns": ["Produit", "Qté"], "rows": [["A", 3], ["B", 5]]},
    ])
    assert res["status"] == "ok"
    assert res["filename"] == "ventes.xlsx"
    assert res["download_url"] == "/api/files/ventes.xlsx"
    assert res["rows"] == 2
    f = downloads / "ventes.xlsx"
    assert f.exists() and res["path"] == str(f.resolve())

    ws = openpyxl.load_workbook(f)["Ventes"]
    assert [c.value for c in ws[1]] == ["Produit", "Qté"]
    assert ws[1][0].font.bold is True          # en-tête en gras
    assert ws.freeze_panes == "A2"             # en-tête figé
    assert [c.value for c in ws[2]] == ["A", 3]


def test_multi_feuilles(downloads):
    res = generate_excel("multi.xlsx", [
        {"name": "F1", "columns": ["x"], "rows": [[1]]},
        {"name": "F2", "columns": ["y"], "rows": [[2], [3]]},
    ])
    assert res["status"] == "ok"
    assert res["sheets"] == ["F1", "F2"]
    assert res["rows"] == 3
    assert openpyxl.load_workbook(downloads / "multi.xlsx").sheetnames == ["F1", "F2"]


def test_lignes_en_dicts_derivent_les_entetes(downloads):
    res = generate_excel("dicts.xlsx", [
        {"name": "D", "rows": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]},
    ])
    assert res["status"] == "ok"
    ws = openpyxl.load_workbook(downloads / "dicts.xlsx")["D"]
    assert [c.value for c in ws[1]] == ["a", "b"]
    assert [c.value for c in ws[2]] == [1, 2]


def test_feuille_unique_en_dict(downloads):
    # tolère une feuille passée directement comme dict (pas dans une liste)
    res = generate_excel("uno.xlsx", {"name": "U", "rows": [["v"]]})
    assert res["status"] == "ok"
    assert res["sheets"] == ["U"]


def test_extension_forcee(downloads):
    assert generate_excel("rapport", [{"rows": [["x"]]}])["filename"] == "rapport.xlsx"


def test_nom_accentue_preserve(downloads):
    # Appli FR : les lettres accentuées ne doivent pas être remplacées par des « _ ».
    res = generate_excel("résumé février.xlsx", [{"rows": [["x"]]}])
    assert res["status"] == "ok"
    assert res["filename"] == "résumé février.xlsx"
    assert res["download_url"] == "/api/files/résumé février.xlsx"
    assert Path(res["path"]).exists()


def test_anti_traversee_de_chemin(downloads):
    res = generate_excel("../../../etc/passwd.xlsx", [{"rows": [["x"]]}])
    assert res["status"] == "ok"
    assert res["filename"] == "passwd.xlsx"
    assert Path(res["path"]).parent == downloads.resolve()   # reste sous downloads


def test_titre_onglet_assaini_et_unique(downloads):
    res = generate_excel("t.xlsx", [
        {"name": "Aaa/Bbb:Ccc", "rows": [[1]]},
        {"name": "x" * 40, "rows": [[2]]},
        {"name": "x" * 40, "rows": [[3]]},   # doublon → suffixe distinct
    ])
    names = res["sheets"]
    assert all(len(n) <= 31 for n in names)
    assert all(not (set(n) & set("[]:*?/\\")) for n in names)
    assert len(set(names)) == 3


def test_valeurs_non_scalaires_stringifiees(downloads):
    res = generate_excel("coerce.xlsx", [{"columns": ["c"], "rows": [[{"k": 1}], [[1, 2]]]}])
    assert res["status"] == "ok"
    ws = openpyxl.load_workbook(downloads / "coerce.xlsx").active
    assert isinstance(ws[2][0].value, str)   # dict → str
    assert isinstance(ws[3][0].value, str)   # list → str


def test_sans_donnees_renvoie_erreur(downloads):
    assert "error" in generate_excel("vide.xlsx", [])
    assert "error" in generate_excel("vide.xlsx", None)


def test_openpyxl_absent_degrade_proprement(downloads, monkeypatch):
    monkeypatch.setattr("tools.excel.HAS_OPENPYXL", False)
    assert "openpyxl" in generate_excel("x.xlsx", [{"rows": [["a"]]}])["error"]


# ── Orchestrateur : exécution + event file_ready ─────────────────────────────

def test_execute_tool_genere_et_renvoie_json(orch, downloads):
    out = orch._execute_tool("generate_excel", {
        "filename": "e2e.xlsx",
        "sheets": [{"name": "S", "columns": ["a"], "rows": [["1"]]}],
    })
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["download_url"] == "/api/files/e2e.xlsx"
    assert (downloads / "e2e.xlsx").exists()


def test_emet_event_file_ready_si_emit_present(orch, downloads):
    events: list[dict] = []
    orch._emit = events.append
    orch._execute_tool("generate_excel", {
        "filename": "ev.xlsx", "sheets": [{"rows": [["x"]]}],
    })
    ready = [e for e in events if e.get("type") == "file_ready"]
    assert len(ready) == 1
    assert ready[0]["filename"] == "ev.xlsx"
    assert ready[0]["download_url"] == "/api/files/ev.xlsx"
    assert ready[0]["kind"] == "xlsx"
    assert ready[0]["size"] > 0


def test_pas_d_event_sans_emit_cli(orch, downloads):
    # CLI/tests : `_emit` absent → aucun event, aucun crash.
    out = orch._execute_tool("generate_excel", {"filename": "cli.xlsx", "sheets": [{"rows": [["x"]]}]})
    assert json.loads(out)["status"] == "ok"


# ── Endpoint de téléchargement ───────────────────────────────────────────────

class TestDownloadEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from api.server import app
        return TestClient(app)

    def test_telecharge_un_xlsx(self, client, downloads):
        (downloads / "served.xlsx").write_bytes(b"PK\x03\x04fake-xlsx")
        r = client.get("/api/files/served.xlsx")
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers["content-type"]
        assert "attachment" in r.headers.get("content-disposition", "")
        assert r.content == b"PK\x03\x04fake-xlsx"

    def test_404_si_absent(self, client, downloads):
        assert client.get("/api/files/nope.xlsx").status_code == 404

    def test_ne_sert_que_le_dossier_downloads(self, client, downloads, tmp_path):
        # un fichier hors du dossier de téléchargements n'est jamais servi
        (tmp_path / "secret.txt").write_text("nope")
        assert client.get("/api/files/secret.txt").status_code == 404
