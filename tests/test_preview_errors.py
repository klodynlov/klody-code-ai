"""Boucle de feedback preview — tampon d'erreurs JS + endpoint beacon + overlay.

Couvre le maillon « navigateur → Klody » : l'overlay POST ses erreurs runtime à
`/api/preview_error`, le backend les bufferise (borné) pour relecture par l'agent.
"""
import json

from agent import preview_errors
from api.server import app
from fastapi.testclient import TestClient

# ── Tampon (agent.preview_errors) ─────────────────────────────────────────────


class TestStore:
    def setup_method(self):
        preview_errors.clear()

    def test_record_puis_recent(self):
        preview_errors.record(
            "http://x/a.html",
            [{"label": "Error", "msg": "boom", "src": "a.html:1:2"}],
            now=100.0,
        )
        reps = preview_errors.recent()
        assert len(reps) == 1
        assert reps[0].url == "http://x/a.html"
        assert reps[0].errors[0].msg == "boom"
        assert reps[0].errors[0].src == "a.html:1:2"

    def test_recent_filtre_par_url(self):
        preview_errors.record("http://x/a.html", [{"msg": "a"}], now=1.0)
        preview_errors.record("http://x/b.html", [{"msg": "b"}], now=2.0)
        msgs = [e.msg for r in preview_errors.recent("http://x/b.html") for e in r.errors]
        assert msgs == ["b"]

    def test_recent_since(self):
        preview_errors.record("u", [{"msg": "old"}], now=10.0)
        preview_errors.record("u", [{"msg": "new"}], now=20.0)
        reps = preview_errors.recent(since=15.0)
        assert len(reps) == 1
        assert reps[0].errors[0].msg == "new"

    def test_borne_nombre_de_rapports(self):
        for i in range(preview_errors._MAX_REPORTS + 10):
            preview_errors.record("u", [{"msg": str(i)}], now=float(i))
        reps = preview_errors.recent()
        assert len(reps) == preview_errors._MAX_REPORTS
        # On garde les plus récents (FIFO drop des plus vieux).
        assert reps[-1].errors[0].msg == str(preview_errors._MAX_REPORTS + 9)

    def test_clip_erreurs_par_rapport(self):
        many = [{"msg": str(i)} for i in range(50)]
        preview_errors.record("u", many, now=1.0)
        assert len(preview_errors.recent()[0].errors) == preview_errors._MAX_ERRORS_PER_REPORT

    def test_champs_tronques(self):
        long = "z" * 5000
        preview_errors.record("u", [{"label": "Error", "msg": long, "src": long}], now=1.0)
        e = preview_errors.recent()[0].errors[0]
        assert len(e.msg) == preview_errors._MAX_FIELD_LEN
        assert len(e.src) == preview_errors._MAX_FIELD_LEN

    def test_clear_par_url(self):
        preview_errors.record("a", [{"msg": "x"}], now=1.0)
        preview_errors.record("b", [{"msg": "y"}], now=2.0)
        preview_errors.clear("a")
        assert [r.url for r in preview_errors.recent()] == ["b"]

    def test_record_ignore_entrees_non_dict(self):
        preview_errors.record("u", [{"msg": "ok"}, "pas un dict", 42], now=1.0)  # type: ignore[list-item]
        assert len(preview_errors.recent()[0].errors) == 1

    def test_mark_loaded_et_loaded(self):
        preview_errors.mark_loaded("http://x/a.html", now=5.0)
        loads = preview_errors.loaded()
        assert len(loads) == 1
        assert loads[0].url == "http://x/a.html"

    def test_loaded_filtre_url_et_since(self):
        preview_errors.mark_loaded("http://x/a.html", now=1.0)
        preview_errors.mark_loaded("http://x/b.html", now=2.0)
        assert [x.url for x in preview_errors.loaded("http://x/b.html")] == ["http://x/b.html"]
        assert len(preview_errors.loaded(since=1.5)) == 1

    def test_clear_vide_aussi_les_loads(self):
        preview_errors.mark_loaded("u", now=1.0)
        preview_errors.clear()
        assert preview_errors.loaded() == []


# ── Endpoint beacon (POST /api/preview_error) ─────────────────────────────────


class TestBeaconEndpoint:
    def setup_method(self):
        preview_errors.clear()
        self.client = TestClient(app)

    def test_post_text_plain_bufferise(self):
        body = json.dumps({
            "url": "http://localhost:8899/x.html",
            "errors": [{"label": "Error", "msg": "TypeError toString", "src": "x.html:572:37"}],
        })
        r = self.client.post(
            "/api/preview_error", content=body, headers={"Content-Type": "text/plain"}
        )
        assert r.status_code == 204
        reps = preview_errors.recent("http://localhost:8899/x.html")
        assert len(reps) == 1
        assert reps[0].errors[0].msg == "TypeError toString"

    def test_corps_vide_ne_plante_pas(self):
        r = self.client.post("/api/preview_error", content=b"", headers={"Content-Type": "text/plain"})
        assert r.status_code == 204
        assert preview_errors.recent() == []

    def test_json_invalide_ne_plante_pas(self):
        r = self.client.post("/api/preview_error", content=b"{pas du json", headers={"Content-Type": "text/plain"})
        assert r.status_code == 204
        assert preview_errors.recent() == []

    def test_sans_erreurs_ne_bufferise_rien(self):
        body = json.dumps({"url": "http://x/y.html", "errors": []})
        r = self.client.post("/api/preview_error", content=body, headers={"Content-Type": "text/plain"})
        assert r.status_code == 204
        assert preview_errors.recent() == []

    def test_ping_ok_marque_charge(self):
        body = json.dumps({"url": "http://localhost:8899/x.html", "ok": True})
        r = self.client.post("/api/preview_error", content=body, headers={"Content-Type": "text/plain"})
        assert r.status_code == 204
        assert len(preview_errors.loaded("http://localhost:8899/x.html")) == 1
        assert preview_errors.recent() == []  # aucune erreur enregistrée

    def test_url_accentuee_est_decodee(self):
        # location.href encode les accents → le backend décode pour matcher le slug.
        body = json.dumps({
            "url": "http://localhost:8899/d%C3%A9mo.html",
            "errors": [{"label": "Error", "msg": "x", "src": ""}],
        })
        r = self.client.post("/api/preview_error", content=body, headers={"Content-Type": "text/plain"})
        assert r.status_code == 204
        reps = preview_errors.recent()
        assert len(reps) == 1
        assert reps[0].url == "http://localhost:8899/démo.html"


# ── L'overlay injecté contient bien le beacon ─────────────────────────────────


def test_overlay_contient_le_beacon():
    from tools.preview import _ERROR_OVERLAY

    assert "sendBeacon" in _ERROR_OVERLAY
    assert "/api/preview_error" in _ERROR_OVERLAY
    # On n'envoie que le nouveau (anti double-comptage) et on flush au pagehide.
    assert "_sent" in _ERROR_OVERLAY
    assert "pagehide" in _ERROR_OVERLAY
    # Ping « chargé proprement » (tranche 2) pour conclure vite sans erreur.
    assert "ok: true" in _ERROR_OVERLAY
    assert "'load'" in _ERROR_OVERLAY
