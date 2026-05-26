"""Tests pour tools/preview.py — assemblage du document, dépendances CDN, feedback."""

import pytest

import tools.preview as preview_mod
from tools.preview import (
    preview_code,
    _as_list,
    _is_full_document,
    _build_document,
    _inject_missing_libs,
)


@pytest.fixture(autouse=True)
def isolated_preview(tmp_path, monkeypatch):
    """Isole PREVIEW_DIR, neutralise le serveur HTTP et l'ouverture du navigateur."""
    monkeypatch.setattr(preview_mod, "PREVIEW_DIR", tmp_path)
    monkeypatch.setattr(preview_mod, "_ensure_server", lambda: "http://localhost:8899")
    monkeypatch.setattr(preview_mod, "webbrowser", type("W", (), {"open": staticmethod(lambda url: None)}))
    return tmp_path


def _written(tmp_path) -> str:
    files = list(tmp_path.glob("*.html"))
    assert len(files) == 1, f"attendu 1 fichier, trouvé {files}"
    return files[0].read_text(encoding="utf-8")


# ── _as_list ─────────────────────────────────────────────────────────────────

class TestAsList:
    def test_none(self):
        assert _as_list(None) == []

    def test_liste(self):
        assert _as_list(["a", "b"]) == ["a", "b"]

    def test_chaine_json(self):
        assert _as_list('["a", "b"]') == ["a", "b"]

    def test_chaine_csv(self):
        assert _as_list("a, b ,c") == ["a", "b", "c"]

    def test_vide(self):
        assert _as_list("") == []
        assert _as_list([]) == []


# ── détection document complet ───────────────────────────────────────────────

class TestIsFullDocument:
    def test_doctype(self):
        assert _is_full_document("<!DOCTYPE html><html></html>")

    def test_balise_html(self):
        assert _is_full_document("  <html lang='fr'>...")

    def test_fragment(self):
        assert not _is_full_document("<div>coucou</div>")


# ── pas d'imbrication quand on passe un document complet ─────────────────────

class TestUnwrapFullDocument:
    def test_document_complet_non_reemballe(self, tmp_path):
        full = (
            "<!DOCTYPE html><html lang='fr'><head><title>X</title></head>"
            "<body><h1>Salut</h1></body></html>"
        )
        preview_code(full, title="Doc")
        out = _written(tmp_path)
        assert out.count("<!DOCTYPE") == 1
        assert out.count("<html") == 1
        assert "<h1>Salut</h1>" in out

    def test_fragment_est_emballe(self, tmp_path):
        preview_code("<p>fragment</p>", title="Frag")
        out = _written(tmp_path)
        assert out.count("<!DOCTYPE") == 1
        assert "<p>fragment</p>" in out
        assert "<title>Frag</title>" in out


# ── injection scripts / styles ───────────────────────────────────────────────

class TestExternalDeps:
    def test_scripts_injectes_dans_head(self, tmp_path):
        preview_code(
            "<div id='app'></div>",
            js="Chart;",
            scripts=["https://cdn.example/chart.js"],
            title="WithDep",
        )
        out = _written(tmp_path)
        assert '<script src="https://cdn.example/chart.js"></script>' in out
        head = out.split("</head>")[0]
        assert "chart.js" in head

    def test_styles_injectes(self, tmp_path):
        preview_code("<div></div>", styles=["https://cdn.example/style.css"], title="WithStyle")
        out = _written(tmp_path)
        assert '<link rel="stylesheet" href="https://cdn.example/style.css">' in out


# ── auto-injection des libs manquantes + avertissements ──────────────────────

class TestMissingLibDetection:
    def test_three_manquant_auto_injecte(self, tmp_path):
        result = preview_code(
            "<div id='globe'></div>",
            js="const s = new THREE.Scene();",
            title="Globe",
        )
        out = _written(tmp_path)
        assert "three.min.js" in out
        assert "⚠" in result
        assert "THREE" in result

    def test_three_present_pas_de_doublon(self, tmp_path):
        result = preview_code(
            "<div></div>",
            js="new THREE.Scene();",
            scripts=["https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"],
            title="GlobeOk",
        )
        out = _written(tmp_path)
        assert out.count("three.min.js") == 1
        assert "⚠" not in result

    def test_pas_de_lib_pas_d_avertissement(self, tmp_path):
        result = preview_code("<p>hello</p>", css="p{color:red}", title="Plain")
        assert "⚠" not in result

    def test_chart_detecte(self, tmp_path):
        _, warnings = _build_document(
            "<canvas id='c'></canvas>", "", "new Chart(ctx, {});", [], [], "T"
        )
        assert any("Chart" in w for w in warnings)

    def test_orbitcontrols_auto_injecte_avec_three(self, tmp_path):
        result = preview_code(
            "<div></div>",
            js="const c = new THREE.OrbitControls(camera, renderer.domElement);",
            title="OrbitDemo",
        )
        out = _written(tmp_path)
        assert "three.min.js" in out
        assert "OrbitControls.js" in out
        # THREE doit être chargé AVANT OrbitControls (sinon OrbitControls casse)
        assert out.index("three.min.js") < out.index("OrbitControls.js")
        assert "OrbitControls" in result

    def test_react_avertit_sans_injecter(self, tmp_path):
        doc, warnings = _inject_missing_libs("<script>ReactDOM.render()</script>")
        assert any("React" in w for w in warnings)
        # React n'est pas auto-injecté (setup multi-fichiers)
        assert "react.production" not in doc


# ── régression du bug observé : document complet collé dans html ─────────────

class TestRegressionNestedDocument:
    def test_globe_three_document_complet(self, tmp_path):
        """Reproduit le test réel : un doc complet Three.js passé dans html."""
        full = (
            "<!DOCTYPE html><html><head><title>Globe</title></head><body>"
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>'
            "<script>new THREE.Scene();</script>"
            "</body></html>"
        )
        result = preview_code(full, title="Globe 3D")
        out = _written(tmp_path)
        # un seul document, pas d'imbrication
        assert out.count("<!DOCTYPE") == 1
        assert out.count("<body") == 1
        # three.js déjà présent → pas réinjecté, pas d'avertissement
        assert out.count("three.min.js") == 1
        assert "⚠" not in result
