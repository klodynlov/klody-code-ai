"""Tests du lint Three.js déterministe (tools/threejs_lint)."""

from tools.threejs_lint import fix_threejs

_CORE = '<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>'
_OBJ_SCRIPT = '<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/OBJLoader.js"></script>'
_GLTF_SCRIPT = '<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/GLTFLoader.js"></script>'


def _doc(head_scripts: str, body_js: str) -> str:
    return f"<!DOCTYPE html><html><head>{head_scripts}</head><body><script>\n{body_js}\n</script></body></html>"


# ── Cas dominant : loader texte chargeant un .glb binaire → bascule GLTFLoader ──

def test_objloader_loading_glb_swaps_to_gltfloader():
    js = (
        "const loader = new THREE.OBJLoader();\n"
        "loader.load('https://cdn.jsdelivr.net/gh/KhronosGroup/glTF-Sample-Models@master/2.0/Duck/glTF-Binary/Duck.glb',\n"
        "  (object) => { scene.add(object); object.scale.set(2, 2, 2); });"
    )
    doc = _doc(_CORE + _OBJ_SCRIPT, js)
    fixed, notes = fix_threejs(doc)

    assert "new THREE.GLTFLoader()" in fixed
    assert "new THREE.OBJLoader()" not in fixed
    assert "loaders/GLTFLoader.js" in fixed
    assert "loaders/OBJLoader.js" not in fixed
    # callback adapté : le wrapper glTF est ramené à sa scène avant scene.add
    assert "object = (object && object.scene) ? object.scene : object;" in fixed
    # l'URL .glb (déjà cohérente avec GLTFLoader) est conservée
    assert "Duck.glb" in fixed
    assert any("GLTFLoader" in n for n in notes)


def test_callback_rebind_handles_function_keyword_and_arrow_noparen():
    for cb in ("function (m) { scene.add(m); }", "m => { scene.add(m); }"):
        js = f"const l = new THREE.OBJLoader();\nl.load('http://x/y.glb', {cb});"
        fixed, _ = fix_threejs(_doc(_CORE + _OBJ_SCRIPT, js))
        assert "new THREE.GLTFLoader()" in fixed
        assert "m = (m && m.scene) ? m.scene : m;" in fixed


# ── Repli URL-swap : on garde le loader, on lui donne un modèle de son format ───

def test_gltfloader_loading_obj_swaps_url_keeps_loader():
    # GLTFLoader (wrapper) chargeant un .obj : on NE peut pas adapter en sens
    # inverse sûrement → on garde GLTFLoader et on remplace l'URL par un .glb.
    js = "const l = new THREE.GLTFLoader();\nl.load('http://x/model.obj', (g) => { scene.add(g.scene); });"
    fixed, notes = fix_threejs(_doc(_CORE + _GLTF_SCRIPT, js))
    assert "new THREE.GLTFLoader()" in fixed  # loader inchangé
    assert "model.obj" not in fixed
    assert "Duck.glb" in fixed  # URL native du loader
    assert any("URL" in n for n in notes)


def test_unparseable_callback_falls_back_to_url_swap():
    # onLoad = référence nommée → callback non analysable → repli URL-swap (.obj).
    js = (
        "function onLoad(o) { scene.add(o); }\n"
        "const loader = new THREE.OBJLoader();\n"
        "loader.load('http://x/Duck.glb', onLoad);"
    )
    fixed, _ = fix_threejs(_doc(_CORE + _OBJ_SCRIPT, js))
    assert "new THREE.OBJLoader()" in fixed  # loader conservé (pas de swap risqué)
    assert "male02.obj" in fixed  # URL alignée sur OBJLoader
    assert "Duck.glb" not in fixed


# ── Cœur three chargé en double ───────────────────────────────────────────────

def test_dedupes_duplicate_core_three():
    doc = _doc(_CORE + _CORE, "const s = new THREE.Scene();")
    fixed, notes = fix_threejs(doc)
    assert fixed.count("three.min.js") == 1
    assert any("double" in n for n in notes)


def test_dedupe_keeps_loader_scripts():
    # examples/js/...Loader.js ne doit JAMAIS être confondu avec le cœur.
    doc = _doc(_CORE + _CORE + _GLTF_SCRIPT, "const s = new THREE.Scene();")
    fixed, _ = fix_threejs(doc)
    assert fixed.count("three.min.js") == 1
    assert "loaders/GLTFLoader.js" in fixed


# ── No-op : code correct, non-Three.js, faux positifs ─────────────────────────

def test_noop_on_correct_gltf_page():
    js = "const l = new THREE.GLTFLoader();\nl.load('http://x/Duck.glb', (g) => { scene.add(g.scene); });"
    doc = _doc(_CORE + _GLTF_SCRIPT, js)
    fixed, notes = fix_threejs(doc)
    assert fixed == doc
    assert notes == []


def test_noop_on_non_threejs_doc():
    doc = "<html><body><script>console.log('hello')</script></body></html>"
    assert fix_threejs(doc) == (doc, [])


def test_no_false_positive_on_textureloader():
    # TextureLoader n'est pas un loader de MODÈLE → on n'y touche pas, et .png
    # n'est pas une extension de modèle.
    js = "const t = new THREE.TextureLoader();\nt.load('texture.png', (tex) => {});"
    doc = _doc(_CORE, js)
    fixed, notes = fix_threejs(doc)
    assert fixed == doc
    assert notes == []


def test_unknown_extension_left_untouched():
    js = "const l = new THREE.OBJLoader();\nl.load('http://x/scene.json', (o) => {});"
    doc = _doc(_CORE + _OBJ_SCRIPT, js)
    fixed, notes = fix_threejs(doc)
    assert fixed == doc  # .json non mappé → aucune correction
    assert notes == []


def test_matching_loader_and_extension_untouched():
    js = "const l = new THREE.OBJLoader();\nl.load('http://x/model.obj', (o) => { scene.add(o); });"
    doc = _doc(_CORE + _OBJ_SCRIPT, js)
    fixed, notes = fix_threejs(doc)
    assert fixed == doc
    assert notes == []


# ── JSONLoader (retiré r85) ───────────────────────────────────────────────────

def test_flags_jsonloader():
    doc = _doc(_CORE, "const l = new THREE.JSONLoader();")
    _, notes = fix_threejs(doc)
    assert any("JSONLoader" in n for n in notes)


# ── Idempotence ───────────────────────────────────────────────────────────────

def test_esm_module_page_is_not_touched():
    # Page à modules ES : loaders importés de three/addons (`new OBJLoader()`),
    # incompatibles avec un <script> UMD → le lint ne doit RIEN éditer, juste noter.
    doc = (
        '<!DOCTYPE html><html><head>'
        '<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",'
        '"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>'
        '</head><body><script type="module">\n'
        "import * as THREE from 'three';\n"
        "import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';\n"
        "const loader = new OBJLoader();\n"
        "loader.load('http://x/Duck.glb', (object) => { scene.add(object); });\n"
        "</script></body></html>"
    )
    fixed, notes = fix_threejs(doc)
    assert fixed == doc  # aucune édition (ni swap ni injection UMD)
    assert "GLTFLoader.js" not in fixed
    assert any("module" in n.lower() for n in notes)


def test_multiple_loads_on_one_var_use_url_swap():
    # Deux .load() sur le MÊME loader : basculer le constructeur casserait les
    # callbacks suivants (non rebindés) → repli URL-swap, complet et sûr.
    js = (
        "const loader = new THREE.OBJLoader();\n"
        "loader.load('http://x/a.glb', (o) => { scene.add(o); });\n"
        "loader.load('http://x/b.glb', (o) => { scene.add(o); });"
    )
    fixed, _ = fix_threejs(_doc(_CORE + _OBJ_SCRIPT, js))
    assert "new THREE.OBJLoader()" in fixed  # constructeur conservé
    assert "GLTFLoader" not in fixed
    assert fixed.count("male02.obj") == 2  # les DEUX URLs alignées
    assert "a.glb" not in fixed and "b.glb" not in fixed


def test_backtick_url_is_recognized():
    js = "const loader = new THREE.OBJLoader();\nloader.load(`http://x/y.glb`, (object) => { scene.add(object); });"
    fixed, _ = fix_threejs(_doc(_CORE + _OBJ_SCRIPT, js))
    assert "new THREE.GLTFLoader()" in fixed
    assert "object = (object && object.scene) ? object.scene : object;" in fixed


def test_injects_loader_script_after_core_when_absent():
    # Loader utilisé + .glb mais aucun <script> de loader : injection APRÈS le cœur.
    js = "const loader = new THREE.OBJLoader();\nloader.load('http://x/m.glb', (o) => { scene.add(o); });"
    fixed, _ = fix_threejs(_doc(_CORE, js))
    assert "loaders/GLTFLoader.js" in fixed
    assert fixed.index("three.min.js") < fixed.index("GLTFLoader.js")


def test_idempotent():
    js = (
        "const loader = new THREE.OBJLoader();\n"
        "loader.load('http://x/Duck.glb', (object) => { scene.add(object); });"
    )
    doc = _doc(_CORE + _CORE + _OBJ_SCRIPT, js)
    once, _ = fix_threejs(doc)
    twice, notes2 = fix_threejs(once)
    assert twice == once
    assert notes2 == []
