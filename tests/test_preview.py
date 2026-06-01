"""Tests pour tools/preview.py — assemblage du document, dépendances CDN, feedback."""

import re

import pytest
import tools.preview as preview_mod
from tools.preview import (
    _as_list,
    _build_document,
    _const_reassign_fix,
    _inject_missing_libs,
    _is_full_document,
    preview_code,
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


# ── régression écran noir : JS ESM (import) dans un <script> classique ────────

class TestEsmModules:
    """Le JS avec `import`/`export` doit être servi en module + import map,
    sinon le navigateur lève 'Cannot use import statement outside a module'."""

    _ESM_THREE = (
        "import * as THREE from 'three';\n"
        "import { OrbitControls } from 'three/addons/controls/OrbitControls.js';\n"
        "const scene = new THREE.Scene();\n"
        "new OrbitControls(camera, renderer.domElement);\n"
    )

    def test_import_genere_script_module(self, tmp_path):
        preview_code("<canvas id='c'></canvas>", js=self._ESM_THREE, title="EarthESM")
        out = _written(tmp_path)
        assert '<script type="module">' in out
        # le code import est conservé tel quel
        assert "import * as THREE from 'three'" in out

    def test_import_genere_importmap(self, tmp_path):
        preview_code("<canvas id='c'></canvas>", js=self._ESM_THREE, title="EarthESM")
        out = _written(tmp_path)
        assert '<script type="importmap">' in out
        assert '"three":' in out and "three.module.js" in out
        assert '"three/addons/":' in out
        # l'import map précède le script module (contrainte navigateur)
        assert out.index('type="importmap"') < out.index('type="module"')

    def test_three_classique_supprime_en_mode_esm(self, tmp_path):
        """Un <script src> three classique passé avec du JS ESM est retiré
        (un build module chargé en script classique planterait)."""
        preview_code(
            "<canvas id='c'></canvas>",
            js=self._ESM_THREE,
            scripts=["https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"],
            title="EarthESM",
        )
        out = _written(tmp_path)
        # aucun <script src="...three..."> classique (l'import map gère three)
        assert not re.search(r'<script src="[^"]*three[^"]*"></script>', out)
        # pas de double-injection du UMD r128 non plus
        assert "three.js/r128" not in out

    def test_js_classique_reste_script_simple(self, tmp_path):
        """Non-régression : sans import/export, on garde un <script> classique."""
        preview_code("<div></div>", js="const x = new THREE.Scene();", title="Classic")
        out = _written(tmp_path)
        assert '<script type="module">' not in out
        assert '<script type="importmap">' not in out

    def test_import_dynamique_reste_classique(self, tmp_path):
        """import() dynamique est valide en script classique → pas de type=module."""
        preview_code("<div></div>", js="import('./x.js').then(m => m.run());", title="Dyn")
        out = _written(tmp_path)
        assert '<script type="module">' not in out


# ── régression "Assignment to constant variable" : const muté → let ───────────

class TestConstReassignFix:
    """Le JS généré déclare souvent en `const` une variable qu'il mute ensuite
    (ex. `const radius = 14;` puis `radius += …`) → TypeError navigateur."""

    def test_const_reassigne_devient_let(self):
        js = "const radius = 14;\nradius += 0.01;"
        out = _const_reassign_fix(js)
        assert "let radius = 14;" in out
        assert "const radius" not in out

    def test_const_compound_assign(self):
        out = _const_reassign_fix("const s = 0;\ns *= 2;")
        assert "let s = 0;" in out

    def test_const_incrementé_devient_let(self):
        out = _const_reassign_fix("const i = 0;\ni++;")
        assert "let i = 0;" in out

    def test_const_jamais_reassigné_reste_const(self):
        js = "const PI = 3.14;\nconst area = PI * 2;"
        out = _const_reassign_fix(js)
        assert out == js  # aucune modification

    def test_mutation_de_membre_ne_declenche_pas(self):
        """`obj.x = 5` mute l'objet, pas le binding → const reste valide."""
        js = "const obj = {};\nobj.x = 5;\nobj.y = 6;"
        out = _const_reassign_fix(js)
        assert out == js

    def test_egalite_ne_declenche_pas(self):
        """`==`, `===`, `=>` ne sont pas des réassignations."""
        js = "const n = 1;\nif (n === 1) {}\nconst f = () => n;"
        out = _const_reassign_fix(js)
        assert out == js

    def test_seul_le_const_mute_est_touché(self):
        js = "const a = 1;\nconst b = 2;\na += 1;"
        out = _const_reassign_fix(js)
        assert "let a = 1;" in out
        assert "const b = 2;" in out

    def test_bout_en_bout_via_preview_code(self, tmp_path):
        """Reproduit le bug maison 3D : const radius muté à la molette."""
        js = (
            "let rotY = 0, rotX = 0.3;\n"
            "const radius = 14;\n"
            "canvas.addEventListener('wheel', e => {\n"
            "  radius += e.deltaY * 0.01;\n"
            "  radius = Math.max(6, Math.min(25, radius));\n"
            "});"
        )
        preview_code("<canvas id='house'></canvas>", js=js, title="Maison")
        out = _written(tmp_path)
        assert "let radius = 14;" in out
        assert "const radius" not in out
