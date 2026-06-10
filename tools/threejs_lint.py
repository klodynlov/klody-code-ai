"""Lint déterministe post-génération des aperçus Three.js.

Le modèle coder produit de façon RÉCURRENTE des anti-patterns Three.js que la
prose injectée (skill `threejs_scenes_3d_modeles`) n'arrive PAS à empêcher — cf.
mémoire `klody_skill_routing_distillation` : sur 4 générations « modèle OBJ », 4/4
écrivent `new THREE.OBJLoader()` puis `.load('…Duck.glb')`. Un `.glb` (binaire)
donné à OBJLoader (parseur texte) ⇒ le modèle ne se charge jamais, sans lever
d'erreur JS (juste des warnings « Unexpected line ») — donc la boucle
`preview_error` existante le rate. Il faut un MÉCANISME, pas une règle.

Ce module corrige le HTML généré AVANT écriture/service (branché dans
`tools.preview._build_document`, à côté de `_const_reassign_fix` /
`_inject_missing_libs`). Pur (`str -> (str, list[str])`), sans I/O → testable.

Deux corrections :
1. Loader ↔ extension : l'URL pointe vers un fichier d'un format DÉFINI →
   c'est la vérité. On aligne le loader sur le format réel du fichier. Cas
   dominant (loader texte chargeant un `.glb`/`.gltf` binaire) : on bascule vers
   GLTFLoader, on remplace son `<script>` en place, et on adapte le callback
   onLoad par une ligne `param = param.scene` (le wrapper glTF n'est pas
   ajoutable tel quel à la scène). Si le callback n'est pas analysable sûrement,
   repli : on échange l'URL contre un modèle d'exemple du format natif du loader
   (le callback reste correct, intouché).
2. Cœur chargé en double : on retire les `<script>` `three(.min).js`
   redondants (le coder en met 2-3 ; le 2e surcharge silencieusement le 1er).

Jamais destructif : toute édition incertaine est abandonnée au profit d'un repli
sûr ou d'un simple avertissement. Aucune dépendance hors stdlib.
"""

from __future__ import annotations

import re

# Extension de fichier modèle → loader Three.js qui sait la lire.
_EXT_TO_LOADER: dict[str, str] = {
    "obj": "OBJLoader",
    "gltf": "GLTFLoader",
    "glb": "GLTFLoader",
    "dae": "ColladaLoader",
    "fbx": "FBXLoader",
    "stl": "STLLoader",
    "ply": "PLYLoader",
}

_MODEL_LOADERS: frozenset[str] = frozenset(_EXT_TO_LOADER.values())

# Loaders dont l'onLoad reçoit un WRAPPER `{ scene, … }` (et non un objet
# ajoutable directement à la scène). Basculer vers l'un d'eux impose d'adapter le
# callback (`obj` → `obj.scene`).
_SCENE_WRAPPER_LOADERS: frozenset[str] = frozenset({"GLTFLoader", "ColladaLoader"})

# URL d'exemple VÉRIFIÉE par loader (repli « URL-swap » : on garde le loader,
# on lui donne un modèle de son format natif). Limité aux formats sûrs.
_CANONICAL_URL: dict[str, str] = {
    "OBJLoader": "https://threejs.org/examples/models/obj/male02/male02.obj",
    "GLTFLoader": "https://cdn.jsdelivr.net/gh/KhronosGroup/glTF-Sample-Models@master/2.0/Duck/glTF-Binary/Duck.glb",
    "ColladaLoader": "https://threejs.org/examples/models/collada/elf/elf.dae",
}

# Script CDN d'un loader (MÊME version r128 que le cœur injecté par
# `tools.preview._CDN_LIBS["THREE"]`, pour éviter tout décalage de version).
_THREE_VER = "0.128.0"
def _loader_script_url(loader: str) -> str:
    return f"https://cdn.jsdelivr.net/npm/three@{_THREE_VER}/examples/js/loaders/{loader}.js"


def _url_ext(url: str) -> str:
    """Extension (sans point, minuscule) du chemin d'une URL, query/fragment retirés."""
    path = re.split(r"[?#]", url, maxsplit=1)[0]
    last = path.rsplit("/", 1)[-1]
    return last.rsplit(".", 1)[-1].lower() if "." in last else ""


def _is_core_three_src(src: str) -> bool:
    """`src` désigne-t-il le BUILD CŒUR de three (et non un add-on examples/js) ?"""
    low = src.lower()
    if any(seg in low for seg in ("/examples/", "/jsm/", "/loaders/", "/controls/", "addons")):
        return False
    return bool(re.search(r"three(\.module)?(\.min)?\.js(?:$|[?#])", low) or re.search(r"/build/three", low))


# ── Édition par spans (appliquées en ordre décroissant → offsets stables) ──────


def _apply(doc: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        doc = doc[:start] + repl + doc[end:]
    return doc


# ── 1. Cœur three chargé plusieurs fois ───────────────────────────────────────

_SCRIPT_SRC_RE = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*(['"])(?P<src>.*?)\1[^>]*>\s*</script>\s*""",
    re.IGNORECASE | re.DOTALL,
)


def _dedupe_core(doc: str, edits: list[tuple[int, int, str]], notes: list[str]) -> None:
    seen = False
    for m in _SCRIPT_SRC_RE.finditer(doc):
        if not _is_core_three_src(m.group("src")):
            continue
        if not seen:
            seen = True  # on garde le premier
            continue
        edits.append((m.start(), m.end(), ""))
        notes.append("three.min.js chargé en double → occurrence superflue retirée.")


# ── 2. Loader ↔ extension du fichier modèle ───────────────────────────────────

# Déclaration : `const loader = new THREE.OBJLoader(` → (var, loader).
_LOADER_DEF_RE = re.compile(
    r"\b(?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)\s*=\s*new\s+THREE\.(?P<loader>[A-Za-z_$][\w$]*Loader)\s*\(",
)


def _find_loader_vars(doc: str) -> dict[str, str]:
    return {m.group("var"): m.group("loader") for m in _LOADER_DEF_RE.finditer(doc)
            if m.group("loader") in _MODEL_LOADERS}


def _adapt_callback_span(doc: str, var: str, url: str) -> tuple[int, str] | None:
    """Trouve le `{` du callback onLoad de `<var>.load('<url>', <cb>` et renvoie
    (position_après_accolade, nom_du_param) si le callback est une fonction inline
    à UN param simple. Sinon None (→ repli sûr)."""
    load_re = re.compile(
        re.escape(var) + r"\s*\.\s*load\s*\(\s*(['\"`])" + re.escape(url) + r"\1\s*,\s*"
        r"(?:\(\s*(?P<p1>[A-Za-z_$][\w$]*)\s*\)|(?P<p2>[A-Za-z_$][\w$]*)|"
        r"function\s*\(\s*(?P<p3>[A-Za-z_$][\w$]*)\s*\))\s*(?:=>)?\s*\{",
    )
    m = load_re.search(doc)
    if not m:
        return None
    param = m.group("p1") or m.group("p2") or m.group("p3")
    return (m.end(), param) if param else None


_ESM_DOC_RE = re.compile(r"""<script[^>]*\btype\s*=\s*["']?(?:module|importmap)""", re.IGNORECASE)


def _is_esm_doc(doc: str) -> bool:
    """Document à modules ES (import map / `<script type="module">`) : les loaders
    y sont importés de `three/addons` (`new OBJLoader()`, sans `THREE.`) et un
    `<script>` UMD `examples/js` y est incompatible → le lint ne touche PAS au
    JS/scripts, il signale seulement."""
    return bool(_ESM_DOC_RE.search(doc))


# `<var>.load('<url>'…)` — accepte ', " et ` (backtick) comme délimiteur.
_LOAD_CALL_RE = re.compile(
    r"(?P<var>[A-Za-z_$][\w$]*)\s*\.\s*load\s*\(\s*(?P<q>['\"`])(?P<url>[^'\"`]+?)(?P=q)",
)


def _fix_loader_mismatches(doc: str, edits: list[tuple[int, int, str]], notes: list[str]) -> None:
    if _is_esm_doc(doc):
        if re.search(r"new\s+(?:THREE\.)?[A-Za-z_$][\w$]*Loader\b", doc):
            notes.append(
                "Page ES module : importe les loaders depuis 'three/addons/…' et "
                "appelle-les sans préfixe (`new GLTFLoader()`), en accord avec "
                "l'extension du fichier — pas de <script> UMD examples/js ici."
            )
        return

    loader_vars = _find_loader_vars(doc)
    if not loader_vars:
        return

    # Regroupe les `.load()` par variable : basculer le CONSTRUCTEUR d'un var
    # affecte TOUS ses `.load()` → on ne le fait que si le var n'a qu'UN load
    # (sinon les 2e+ callbacks, non rebindés, casseraient). Repli multi-load : un
    # URL-swap par load (loader/callbacks inchangés → corrects).
    loads_by_var: dict[str, list[re.Match[str]]] = {}
    for m in _LOAD_CALL_RE.finditer(doc):
        if m.group("var") in loader_vars:
            loads_by_var.setdefault(m.group("var"), []).append(m)

    for var, loads in loads_by_var.items():
        loader = loader_vars[var]
        mismatched: list[tuple[re.Match[str], str, str]] = []
        for m in loads:
            ext = _url_ext(m.group("url"))
            expected = _EXT_TO_LOADER.get(ext)
            if expected is not None and expected != loader:
                mismatched.append((m, ext, expected))
        if not mismatched:
            continue

        m0, _ext0, expected0 = mismatched[0]
        cb = _adapt_callback_span(doc, var, m0.group("url"))
        single = len(loads) == 1 and len(mismatched) == 1
        if (single and cb is not None
                and expected0 in _SCENE_WRAPPER_LOADERS
                and loader not in _SCENE_WRAPPER_LOADERS):
            # Bascule fidèle : un seul load, vers un loader wrapper, callback
            # analysable → on change le loader, son <script>, et on adapte le callback.
            _swap_loader(doc, var, loader, expected0, cb, edits, notes)
        else:
            for m, ext, expected in mismatched:
                _swap_url(m, loader, expected, ext, edits, notes)


def _swap_loader(
    doc: str, var: str, loader: str, expected: str,
    cb: tuple[int, str], edits: list[tuple[int, int, str]], notes: list[str],
) -> None:
    """Bascule `loader` → `expected` : constructeur, <script>, et rebind du param
    onLoad (`p = p.scene` car le wrapper n'est pas ajoutable directement)."""
    # a) constructeur de CE var : `<var> = new THREE.<loader>(` → `<expected>`
    def_re = re.compile(re.escape(var) + r"\s*=\s*new\s+THREE\.(" + re.escape(loader) + r")\s*\(")
    dm = def_re.search(doc)
    if dm:
        edits.append((dm.start(1), dm.end(1), expected))
    # b) <script> du loader sortant → loader entrant (en place, bon ordre conservé)
    _replace_loader_script(doc, loader, expected, edits)
    # c) rebind du param : insérer `p = p && p.scene ? p.scene : p;` après le `{`
    pos, param = cb
    rebind = f" {param} = ({param} && {param}.scene) ? {param}.scene : {param};"
    edits.append((pos, pos, rebind))
    notes.append(
        f"{loader} chargeait un fichier {expected.replace('Loader', '').lower()} "
        f"binaire (modèle invisible) → basculé vers {expected} (+ callback adapté)."
    )


def _replace_loader_script(doc: str, loader: str, expected: str, edits: list[tuple[int, int, str]]) -> None:
    """Remplace le <script> `…/<loader>.js` par `…/<expected>.js` en place ; si
    absent, injecte le script du loader entrant juste avant le 1er <script> inline."""
    for m in _SCRIPT_SRC_RE.finditer(doc):
        src = m.group("src")
        if re.search(rf"/{re.escape(loader)}\.js(?:$|[?#])", src):
            new_src = re.sub(rf"/{re.escape(loader)}\.js", f"/{expected}.js", src)
            edits.append((m.start("src"), m.end("src"), new_src))
            return
    # Pas de <script> du loader sortant : injecter celui de l'entrant APRÈS le
    # script cœur (THREE doit exister avant que le loader s'y enregistre), sinon
    # avant le 1er <script> inline.
    inj = f'\n  <script src="{_loader_script_url(expected)}"></script>'
    pos: int | None = None
    for m in _SCRIPT_SRC_RE.finditer(doc):
        if _is_core_three_src(m.group("src")):
            pos = m.end()
            break
    if pos is None:
        inline = re.search(r"<script(?:>|\s+(?![^>]*\bsrc\b)[^>]*>)", doc, re.IGNORECASE)
        pos = inline.start() if inline else len(doc)
    edits.append((pos, pos, inj))


def _swap_url(
    m: re.Match[str], loader: str, expected: str, ext: str,
    edits: list[tuple[int, int, str]], notes: list[str],
) -> None:
    """Repli sûr : on GARDE le loader (et son callback, correct pour lui) et on
    remplace l'URL par un modèle d'exemple du format natif du loader."""
    canonical = _CANONICAL_URL.get(loader)
    if not canonical:
        notes.append(
            f"{loader} reçoit une URL .{ext} incompatible (le loader attend un autre "
            f"format) → à corriger : aligne le loader sur l'extension du fichier."
        )
        return
    # group "url" = contenu de l'URL (entre délimiteurs), sans les délimiteurs.
    edits.append((m.start("url"), m.end("url"), canonical))
    notes.append(
        f"{loader} recevait une URL .{ext} qu'il ne sait pas lire → URL remplacée "
        f"par un modèle .{_url_ext(canonical)} compatible."
    )


# ── 3. JSONLoader (retiré de three depuis r85) ────────────────────────────────


def _flag_jsonloader(doc: str, notes: list[str]) -> None:
    if re.search(r"\bTHREE\.JSONLoader\b|\bnew\s+JSONLoader\b", doc):
        notes.append(
            "THREE.JSONLoader est utilisé mais n'existe plus (retiré en r85) → "
            "remplace-le par GLTFLoader (.glb/.gltf)."
        )


# ── API publique ──────────────────────────────────────────────────────────────


def fix_threejs(doc: str) -> tuple[str, list[str]]:
    """Corrige les anti-patterns Three.js d'un document HTML. Retourne (doc_corrigé,
    notes). No-op (mêmes doc, notes=[]) si le document n'utilise pas Three.js."""
    if "THREE" not in doc and "three" not in doc.lower():
        return doc, []
    edits: list[tuple[int, int, str]] = []
    notes: list[str] = []
    _dedupe_core(doc, edits, notes)
    _fix_loader_mismatches(doc, edits, notes)
    fixed = _apply(doc, edits)
    _flag_jsonloader(fixed, notes)
    return fixed, notes
