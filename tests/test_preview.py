"""Tests pour tools/preview.py — assemblage du document, dépendances CDN, feedback."""

import re
import threading
from contextlib import suppress
from types import SimpleNamespace

import pytest
import tools.preview as preview_mod
from tools.preview import (
    _as_list,
    _build_document,
    _const_reassign_fix,
    _ensure_server,  # référence directe au VRAI objet (l'autouse stube preview_mod._ensure_server)
    _inject_missing_libs,
    _is_full_document,
    _SilentHandler,
    _stop_server,
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


# ── Tolérance d'extension du serveur (_SilentHandler.send_head) ───────────────


class TestHtmlExtensionFallback:
    """Régression « Error 404 » : l'URL d'aperçu perd parfois son « .html » en
    route (recopie, réécriture par le modèle, ouverture OS d'une URL accentuée).
    Le serveur doit alors servir « <chemin>.html » au lieu d'un 404 sec."""

    @staticmethod
    def _serve(directory: str):
        """Démarre un serveur éphémère avec le handler réel ; retourne (srv, base)."""
        import threading
        from functools import partial
        from http.server import HTTPServer

        srv = HTTPServer(("127.0.0.1", 0), partial(_SilentHandler, directory=directory))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    @staticmethod
    def _get(url: str) -> int:
        import urllib.error
        import urllib.request

        try:
            return urllib.request.urlopen(url, timeout=4).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_chemin_sans_extension_sert_le_html(self, tmp_path):
        import urllib.parse

        # Nom accentué + tronqué, exactement comme la regression terrain.
        name = "crée_une_page_html_avec_un_titre_hello_w"
        (tmp_path / f"{name}.html").write_text("<h1>ok</h1>", encoding="utf-8")
        srv, base = self._serve(str(tmp_path))
        try:
            enc = urllib.parse.quote(name)
            assert self._get(f"{base}/{enc}.html") == 200   # cas nominal
            assert self._get(f"{base}/{enc}") == 200         # ← sans .html : servi quand même
        finally:
            srv.shutdown()

    def test_chemin_vraiment_absent_reste_404(self, tmp_path):
        """Le repli ne doit pas masquer un fichier réellement manquant."""
        srv, base = self._serve(str(tmp_path))
        try:
            assert self._get(f"{base}/nexiste_pas") == 404
        finally:
            srv.shutdown()

    def test_dedup_et_tolerance_extension_cohabitent(self, tmp_path):
        """Un nom dédupliqué « foo-2.html » reste atteignable sans extension."""
        (tmp_path / "foo.html").write_text("<h1>1</h1>", encoding="utf-8")
        (tmp_path / "foo-2.html").write_text("<h1>2</h1>", encoding="utf-8")
        srv, base = self._serve(str(tmp_path))
        try:
            assert self._get(f"{base}/foo-2") == 200
        finally:
            srv.shutdown()


# ── Nommage du fichier d'aperçu (fragilité « nom = prompt tronqué ») ──────────


class TestSlugify:
    def test_accents_translitteres(self):
        assert preview_mod._slugify("créé café déjà") == "cree_cafe_deja"

    def test_coupe_sur_frontiere_de_mot_pas_d_accent(self):
        s = preview_mod._slugify("crée une page html avec un titre hello world")
        assert "é" not in s                       # accents translittérés
        assert not s.endswith("_w")               # plus de coupe en plein « world »
        assert all(part for part in s.split("_"))  # pas de séparateur orphelin

    def test_vide_ou_non_ascii_repli_preview(self):
        assert preview_mod._slugify("") == "preview"
        assert preview_mod._slugify("🎉") == "preview"
        assert preview_mod._slugify("你好") == "preview"

    def test_mot_unique_geant_borne(self):
        assert len(preview_mod._slugify("a" * 200)) <= preview_mod._SLUG_MAXLEN


class TestExtractDocTitle:
    def test_titre_present(self):
        doc = "<html><head><title>Hello World</title></head><body>x</body></html>"
        assert preview_mod._extract_doc_title(doc) == "Hello World"

    def test_titre_vide_none(self):
        assert preview_mod._extract_doc_title("<title>   </title>") is None

    def test_pas_de_titre_none(self):
        assert preview_mod._extract_doc_title("<html><body>x</body></html>") is None

    def test_entites_decodees(self):
        assert preview_mod._extract_doc_title("<title>Tom &amp; Jerry</title>") == "Tom & Jerry"

    def test_multiligne(self):
        assert preview_mod._extract_doc_title("<title>\n  Ma Page  \n</title>") == "Ma Page"


class TestUniqueFilename:
    def test_dossier_vide(self, tmp_path):  # PREVIEW_DIR=tmp_path via isolated_preview
        assert preview_mod._unique_filename("foo") == "foo.html"

    def test_collision_suffixe(self, tmp_path):
        (tmp_path / "foo.html").write_text("x", encoding="utf-8")
        assert preview_mod._unique_filename("foo") == "foo-2.html"

    def test_collision_multiple(self, tmp_path):
        (tmp_path / "foo.html").write_text("x", encoding="utf-8")
        (tmp_path / "foo-2.html").write_text("x", encoding="utf-8")
        assert preview_mod._unique_filename("foo") == "foo-3.html"


class TestPreviewCodeNaming:
    def test_nom_derive_du_title_pas_du_prompt(self, tmp_path):
        doc = "<!DOCTYPE html><html><head><title>Hello World</title></head><body>hi</body></html>"
        preview_code(doc, title="crée une page html avec un titre hello world")
        assert [f.name for f in tmp_path.glob("*.html")] == ["hello_world.html"]

    def test_dedup_pas_d_ecrasement(self, tmp_path):
        doc = "<!DOCTYPE html><html><head><title>Hello World</title></head><body>v1</body></html>"
        preview_code(doc, title="x")
        preview_code(doc.replace("v1", "v2"), title="x")
        names = sorted(f.name for f in tmp_path.glob("*.html"))
        assert names == ["hello_world-2.html", "hello_world.html"]

    def test_title_svg_inline_ne_vole_pas_le_nom(self, tmp_path):
        """Un <title> de SVG a11y dans le <body> ne doit pas devenir le nom : on
        confine l'extraction au <head> → repli sur le paramètre `title`."""
        doc = (
            "<!DOCTYPE html><html><head></head><body>"
            "<svg><title>icone fermer</title></svg></body></html>"
        )
        preview_code(doc, title="Mon Jeu")
        assert [f.name for f in tmp_path.glob("*.html")] == ["mon_jeu.html"]

    def test_self_heal_injecte_dans_le_document(self, tmp_path):
        """Garde-fou anti-suppression : le JS de self-heal doit être présent."""
        preview_code("<p>hi</p>", title="X")
        out = _written(tmp_path)
        assert "visibilitychange" in out and "__klody_heal" in out


# ── Cycle de vie du serveur (fragilité « mort au restart API ») ───────────────


class _FakeServer:
    """Faux HTTPServer : compte les instanciations, ne bind aucun port. serve_forever
    BLOQUE (comme un vrai) jusqu'à shutdown → le thread reste vivant (is_alive True)."""

    instances = 0

    def __init__(self, addr, handler):
        type(self).instances += 1
        self._stop = threading.Event()
        self.did_shutdown = False
        self.closed = False

    def serve_forever(self):
        self._stop.wait()

    def shutdown(self):
        self.did_shutdown = True
        self._stop.set()

    def server_close(self):
        self.closed = True


@pytest.fixture
def server_globals(monkeypatch):
    """Réinitialise les globals du serveur preview, neutralise atexit, et arrête
    tout faux serveur démarré à la fin du test (le thread démon bloqué sort)."""
    monkeypatch.setattr(preview_mod, "_server", None)
    monkeypatch.setattr(preview_mod, "_thread", None)
    monkeypatch.setattr(preview_mod, "_atexit_registered", False)
    monkeypatch.setattr(preview_mod.atexit, "register", lambda *a, **k: None)
    yield
    with suppress(Exception):
        _stop_server()


class TestServerLifecycle:
    def test_ensure_server_idempotent(self, server_globals, monkeypatch):
        _FakeServer.instances = 0
        monkeypatch.setattr(preview_mod, "HTTPServer", _FakeServer)
        u1 = _ensure_server()
        u2 = _ensure_server()
        assert u1 == u2 == f"http://localhost:{preview_mod.PREVIEW_PORT}"
        assert _FakeServer.instances == 1  # un seul bind (thread toujours vivant)

    def test_ensure_server_concurrent_un_seul_bind(self, server_globals, monkeypatch):
        import threading as _t

        _FakeServer.instances = 0
        monkeypatch.setattr(preview_mod, "HTTPServer", _FakeServer)
        gate = _t.Barrier(8)

        def worker():
            gate.wait()
            _ensure_server()

        threads = [_t.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert _FakeServer.instances == 1  # lock + double-check → pas de double-bind

    def test_ensure_server_tolere_eaddrinuse_sans_lever(self, server_globals, monkeypatch):
        def boom(*a, **k):
            raise OSError("address already in use")

        monkeypatch.setattr(preview_mod, "HTTPServer", boom)
        monkeypatch.setattr(preview_mod.time, "sleep", lambda *_a: None)
        url = _ensure_server()  # ne doit PAS lever
        assert url == f"http://localhost:{preview_mod.PREVIEW_PORT}"
        assert preview_mod._server is None  # un prochain appel réessaiera

    def test_ensure_server_retry_eaddrinuse_puis_succes(self, server_globals, monkeypatch):
        """Le scénario VISÉ par le correctif : 1er bind échoue (ancien socket pas
        relâché), le 2e réussit → serveur démarré, pas d'exception."""
        _FakeServer.instances = 0
        calls = {"n": 0}

        def flaky(addr, handler):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("address already in use")
            return _FakeServer(addr, handler)

        monkeypatch.setattr(preview_mod, "HTTPServer", flaky)
        monkeypatch.setattr(preview_mod.time, "sleep", lambda *_a: None)
        url = _ensure_server()
        assert url == f"http://localhost:{preview_mod.PREVIEW_PORT}"
        assert preview_mod._server is not None and calls["n"] == 2
        assert _FakeServer.instances == 1

    def test_stop_server_ferme_et_idempotent(self, server_globals, monkeypatch):
        fake = _FakeServer(None, None)
        monkeypatch.setattr(preview_mod, "_server", fake)
        monkeypatch.setattr(preview_mod, "_thread", SimpleNamespace(is_alive=lambda: True))
        _stop_server()
        assert fake.did_shutdown and fake.closed
        assert preview_mod._server is None and preview_mod._thread is None
        _stop_server()  # 2e appel : aucun crash
