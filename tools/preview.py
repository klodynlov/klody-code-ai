"""Aperçu local de code HTML/CSS/JS — serveur HTTP éphémère + ouverture navigateur."""
from __future__ import annotations

import atexit
import json
import logging
import re
import threading
import webbrowser
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler

from config import PREVIEW_DIR, PREVIEW_PORT, PROJECT_ROOT

logger = logging.getLogger(__name__)

_server: HTTPServer | None = None
_thread: threading.Thread | None = None

# Librairies front courantes : URL CDN connue + détection d'usage + marqueurs
# permettant de savoir si la lib est déjà incluse dans le document.
_CDN_LIBS: dict[str, str] = {
    "THREE": "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js",
    "OrbitControls": "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js",
    "Chart": "https://cdn.jsdelivr.net/npm/chart.js@4",
    "d3": "https://cdn.jsdelivr.net/npm/d3@7",
    "gsap": "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js",
    "anime": "https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.1/anime.min.js",
    "p5": "https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.9.0/p5.min.js",
    "confetti": "https://cdn.jsdelivr.net/npm/canvas-confetti@1/dist/confetti.browser.min.js",
    "PIXI": "https://cdn.jsdelivr.net/npm/pixi.js@7/dist/pixi.min.js",
    "Matter": "https://cdnjs.cloudflare.com/ajax/libs/matter-js/0.19.0/matter.min.js",
    "Phaser": "https://cdn.jsdelivr.net/npm/phaser@3/dist/phaser.min.js",
}

# Regex repérant l'utilisation de chaque lib dans le code généré.
_LIB_USAGE: dict[str, str] = {
    "THREE": r"\bTHREE\.",
    "OrbitControls": r"\bTHREE\.OrbitControls\b|\bnew\s+OrbitControls\b",
    "Chart": r"\bnew\s+Chart\b|\bChart\s*\(",
    "d3": r"\bd3\.",
    "gsap": r"\bgsap\.|\bTweenMax\b|\bgsap\s*\(",
    "anime": r"\banime\s*\(|\banime\.",
    "p5": r"\bcreateCanvas\s*\(|\bnew\s+p5\b",
    "confetti": r"\bconfetti\s*\(",
    "PIXI": r"\bPIXI\.",
    "Matter": r"\bMatter\.",
    "Phaser": r"\bnew\s+Phaser\b|\bPhaser\.",
}

# Sous-chaînes prouvant qu'une lib est déjà chargée (script src déjà présent).
_LIB_MARKERS: dict[str, tuple[str, ...]] = {
    "THREE": ("three.min.js", "three.module", "three@", "/three/"),
    "OrbitControls": ("OrbitControls.js", "OrbitControls.min.js"),
    "Chart": ("chart.js", "chart.umd", "chart@"),
    "d3": ("d3.min.js", "d3@", "/d3/", "d3.v"),
    "gsap": ("gsap",),
    "anime": ("anime.min.js", "animejs", "anime@"),
    "p5": ("p5.min.js", "p5.js", "p5@"),
    "confetti": ("canvas-confetti", "confetti.browser"),
    "PIXI": ("pixi.min.js", "pixi.js", "pixi@"),
    "Matter": ("matter.min.js", "matter-js", "matter@"),
    "Phaser": ("phaser.min.js", "phaser@", "phaser.js"),
}

# Libs détectées mais non auto-injectables (setup multi-fichiers) → simple avertissement.
_WARN_ONLY: dict[str, str] = {
    "React": r"\bReactDOM\b|\bReact\.createElement\b",
    "Vue": r"\bVue\.createApp\b|\bnew\s+Vue\b",
}
_WARN_ONLY_MARKERS: dict[str, tuple[str, ...]] = {
    "React": ("react.", "react@", "/react/"),
    "Vue": ("vue.global", "vue.min", "vue@", "/vue/"),
}

# ── Support des modules ES ────────────────────────────────────────────────────
# Un <script> classique ne peut pas contenir `import`/`export` (→ SyntaxError
# "Cannot use import statement outside a module"). Le JS ESM doit être servi en
# <script type="module">, et les imports par nom nu (`from 'three'`) exigent une
# import map pointant vers le build ESM CDN.
_ESM_RE = re.compile(r"^\s*(?:import(?![\w(])|export\b)", re.M)

# Racine de package importée → entrées d'import map (URLs ESM CDN).
_ESM_IMPORTMAP: dict[str, dict[str, str]] = {
    "three": {
        "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
        "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/",
    },
}


def _is_esm(js: str) -> bool:
    """Vrai si le JS a un import/export de haut niveau (≠ import() dynamique)."""
    return bool(js) and bool(_ESM_RE.search(js))


def _imported_specifiers(js: str) -> set[str]:
    """Specifiers de module importés : `from '...'` et `import '...'` (side-effect)."""
    specs = set(re.findall(r"""from\s*['"]([^'"]+)['"]""", js))
    specs |= set(re.findall(r"""import\s*['"]([^'"]+)['"]""", js))
    return specs


def _esm_managed_roots(js: str) -> set[str]:
    """Racines de packages couvertes par l'import map (three, …)."""
    return {
        spec.split("/", 1)[0]
        for spec in _imported_specifiers(js)
        if spec.split("/", 1)[0] in _ESM_IMPORTMAP
    }


def _build_importmap(js: str) -> str:
    """<script type=importmap> pour les libs ESM connues utilisées par le JS."""
    if not _is_esm(js):
        return ""
    imports: dict[str, str] = {}
    for root in _esm_managed_roots(js):
        imports.update(_ESM_IMPORTMAP[root])
    if not imports:
        return ""
    payload = json.dumps({"imports": imports}, indent=2)
    return f'\n<script type="importmap">\n{payload}\n</script>'


def _js_script_tag(js: str) -> str:
    """Balise <script> du JS inline — type=module si le code est un module ES."""
    if not js.strip():
        return ""
    type_attr = ' type="module"' if _is_esm(js) else ""
    return f"\n<script{type_attr}>\n{js}\n</script>"


# ── Réparation JS : const réassigné → let ─────────────────────────────────────
# Le code généré déclare souvent en `const` une variable qu'il mute ensuite
# (ex. `const radius = 14;` puis `radius += …` à la molette) → TypeError
# "Assignment to constant variable" à chaque event. On rétrograde en `let` les
# const effectivement réassignés (`let` est toujours valide là où `const` l'était).
_CONST_DECL_RE = re.compile(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=")
_DECL_KW_RE = re.compile(r"\b(?:const|let|var)\s+$")
# Écriture sur une variable : ++/--, ou `=` (hors == === =>), ou opérateur composé `OP=`.
_ASSIGN_OP = r"(?:\+\+|--|(?:\*\*|<<|>>|&&|\|\||\?\?|[+\-*/%&|^])?=(?![=>]))"


def _const_reassign_fix(js: str) -> str:
    """Rétrograde en `let` les `const` réassignés plus loin (sinon TypeError navigateur)."""
    names = set(_CONST_DECL_RE.findall(js))
    if not names:
        return js
    reassigned: set[str] = set()
    for name in names:
        write_re = re.compile(r"(?<![\w$.])" + re.escape(name) + r"\s*" + _ASSIGN_OP)
        for m in write_re.finditer(js):
            # On ignore la déclaration elle-même (`const name =`), seule une
            # écriture NON précédée d'un mot-clé de déclaration compte.
            if _DECL_KW_RE.search(js[max(0, m.start() - 12) : m.start()]):
                continue
            reassigned.add(name)
            break
    if not reassigned:
        return js

    def _swap(m: re.Match) -> str:
        return ("let" + m.group(1)) if m.group(2) in reassigned else m.group(0)

    return re.sub(r"\bconst(\s+([A-Za-z_$][\w$]*)\s*=)", _swap, js)


class _SilentHandler(SimpleHTTPRequestHandler):
    """Handler HTTP sans logs dans le terminal."""

    def log_message(self, format: str, *args: object) -> None:
        logger.debug(format, *args)


def _stop_server() -> None:
    global _server, _thread
    if _server:
        _server.shutdown()
        _server = None
        _thread = None
        logger.info("[Preview] Serveur arrêté")


def _ensure_server() -> str:
    """Démarre le serveur HTTP si besoin. Retourne l'URL de base."""
    global _server, _thread

    if _server is not None:
        return f"http://localhost:{PREVIEW_PORT}"

    handler = partial(_SilentHandler, directory=str(PREVIEW_DIR))
    _server = HTTPServer(("127.0.0.1", PREVIEW_PORT), handler)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True, name="preview-http")
    _thread.start()
    atexit.register(_stop_server)
    logger.info("[Preview] Serveur démarré sur le port %d → %s", PREVIEW_PORT, PREVIEW_DIR)
    return f"http://localhost:{PREVIEW_PORT}"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\-.]", "_", name.strip().lower())
    return slug or "preview"


def _as_list(value) -> list[str]:
    """Normalise une entrée scripts/styles (None, str JSON, str séparée, liste) en liste d'URLs."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                return [str(v).strip() for v in json.loads(text) if str(v).strip()]
            except (json.JSONDecodeError, TypeError):
                pass
        sep = "\n" if "\n" in text else ","
        return [s.strip() for s in text.split(sep) if s.strip()]
    return []


def _script_tags(urls: list[str]) -> str:
    return "".join(f'\n  <script src="{u}"></script>' for u in urls)


def _link_tags(urls: list[str]) -> str:
    return "".join(f'\n  <link rel="stylesheet" href="{u}">' for u in urls)


def _is_full_document(html: str) -> bool:
    head = html.lstrip()[:200].lower()
    return "<!doctype" in head or "<html" in head


def _insert_before(doc: str, tag: str, snippet: str, *, fallback_end: bool) -> str:
    """Insère snippet juste avant tag (insensible à la casse). Sinon, ajoute au début/fin."""
    if not snippet:
        return doc
    match = re.search(re.escape(tag), doc, flags=re.IGNORECASE)
    if match:
        return doc[: match.start()] + snippet + "\n" + doc[match.start() :]
    return doc + snippet if fallback_end else snippet + doc


def _wrap_fragment(html: str, css: str, js: str, scripts: list[str], styles: list[str], title: str) -> str:
    css_block = f"\n<style>\n{css}\n</style>" if css.strip() else ""
    js_block = _js_script_tag(js)
    # L'import map doit précéder tout <script type=module> : on la place en tête de <head>.
    importmap = _build_importmap(js)
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>{importmap}{_link_tags(styles)}{_script_tags(scripts)}{css_block}
</head>
<body>
{html}{js_block}
</body>
</html>"""


def _inject_into_document(doc: str, css: str, js: str, scripts: list[str], styles: list[str]) -> str:
    """Injecte CSS/JS/scripts/styles supplémentaires dans un document HTML déjà complet."""
    head_extra = _build_importmap(js) + _link_tags(styles) + _script_tags(scripts)
    if css.strip():
        head_extra += f"\n<style>\n{css}\n</style>"
    if head_extra:
        doc = _insert_before(doc, "</head>", head_extra, fallback_end=False)
    if js.strip():
        doc = _insert_before(doc, "</body>", _js_script_tag(js), fallback_end=True)
    return doc


def _inject_missing_libs(doc: str) -> tuple[str, list[str]]:
    """Détecte les libs utilisées mais non incluses, injecte les CDN connus, retourne les avertissements."""
    warnings: list[str] = []
    to_add: list[str] = []

    for lib, usage_re in _LIB_USAGE.items():
        if not re.search(usage_re, doc):
            continue
        if any(m in doc for m in _LIB_MARKERS.get(lib, ())):
            continue
        cdn = _CDN_LIBS[lib]
        to_add.append(cdn)
        warnings.append(f"'{lib}' était utilisé sans être inclus → {cdn} ajouté automatiquement.")

    for lib, usage_re in _WARN_ONLY.items():
        if not re.search(usage_re, doc):
            continue
        if any(m in doc for m in _WARN_ONLY_MARKERS.get(lib, ())):
            continue
        warnings.append(
            f"'{lib}' est utilisé mais aucune librairie n'est incluse — "
            f"ajoute les <script src> nécessaires via le paramètre scripts=[...]."
        )

    if to_add:
        doc = _insert_before(doc, "</head>", _script_tags(to_add), fallback_end=False)
    return doc, warnings


_ERROR_OVERLAY = """
<style id="__klody_overlay_style">
#__klody_err_overlay {
  position: fixed; left: 0; right: 0; bottom: 0;
  max-height: 50vh; overflow-y: auto;
  background: #2a0f0f; color: #ffd2cc;
  font: 12px/1.45 'JetBrains Mono', 'Menlo', monospace;
  border-top: 2px solid #dc3545; box-shadow: 0 -6px 24px rgba(0,0,0,0.4);
  z-index: 2147483647; padding: 10px 14px; display: none;
}
#__klody_err_overlay header {
  display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
  font-weight: 600; color: #fff;
}
#__klody_err_overlay .__klody_close {
  margin-left: auto; cursor: pointer; opacity: 0.7;
  background: transparent; border: none; color: #fff;
  font-size: 16px; line-height: 1; padding: 0 4px;
}
#__klody_err_overlay .__klody_close:hover { opacity: 1; }
#__klody_err_overlay pre {
  margin: 4px 0; padding: 6px 8px; background: rgba(0,0,0,0.25);
  border-radius: 4px; white-space: pre-wrap; word-break: break-word;
}
#__klody_err_overlay .__klody_count {
  background: #dc3545; color: white; border-radius: 999px;
  padding: 1px 8px; font-size: 10.5px;
}
</style>
<script id="__klody_overlay_script">
(function(){
  var entries = [];
  var box;
  function ensureBox() {
    if (box) return box;
    box = document.createElement('div');
    box.id = '__klody_err_overlay';
    box.innerHTML = '<header>⚠ Erreur(s) JS détectée(s)<span class="__klody_count">0</span>' +
                    '<button class="__klody_close" title="Fermer">✕</button></header><div></div>';
    box.querySelector('.__klody_close').onclick = function(){ box.style.display='none'; };
    (document.body || document.documentElement).appendChild(box);
    return box;
  }
  function render() {
    var b = ensureBox();
    b.style.display = 'block';
    b.querySelector('.__klody_count').textContent = entries.length;
    var list = b.querySelector('div');
    list.innerHTML = entries.slice(-20).map(function(e){
      return '<pre>' + (e.label ? '[' + e.label + '] ' : '') +
             escapeHtml(e.msg) + (e.src ? '\\n  → ' + escapeHtml(e.src) : '') + '</pre>';
    }).join('');
  }
  function escapeHtml(s){ return String(s).replace(/[&<>]/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]; }); }
  // Beacon → backend Klody (boucle de feedback). text/plain + sendBeacon = requête
  // « simple » : pas de preflight CORS, fire-and-forget. On n'envoie que le NOUVEAU.
  var _api = (window.__KLODY_API_ORIGIN__ || 'http://localhost:8000');
  var _sent = 0, _timer = null;
  function flushErrors(){
    _timer = null;
    if (entries.length <= _sent) return;
    var batch = entries.slice(_sent); _sent = entries.length;
    try {
      var body = JSON.stringify({ url: location.href, errors: batch });
      var blob = new Blob([body], { type: 'text/plain' });
      if (!(navigator.sendBeacon && navigator.sendBeacon(_api + '/api/preview_error', blob))) {
        fetch(_api + '/api/preview_error', { method:'POST', body: body, keepalive:true, mode:'no-cors' });
      }
    } catch(_){ }
  }
  function scheduleFlush(){ if (!_timer) _timer = setTimeout(flushErrors, 700); }
  window.addEventListener('pagehide', flushErrors);
  function add(label, msg, src){ entries.push({label:label, msg:msg, src:src}); render(); scheduleFlush(); }
  window.addEventListener('error', function(ev){
    var src = ev.filename ? ev.filename + ':' + ev.lineno + ':' + ev.colno : '';
    add('Error', (ev.message || ev.error || 'unknown'), src);
  }, true);
  window.addEventListener('unhandledrejection', function(ev){
    add('Promise', (ev.reason && (ev.reason.stack || ev.reason.message || ev.reason)) || 'rejected');
  });
  var _ce = console.error;
  console.error = function(){
    try { add('console.error', Array.prototype.map.call(arguments, function(a){
      return typeof a === 'object' ? JSON.stringify(a) : String(a); }).join(' ')); }
    catch(_){ }
    return _ce.apply(console, arguments);
  };
})();
</script>"""


def _inject_error_overlay(doc: str) -> str:
    """Injecte l'overlay d'erreur JS de Klody en tout début de <head>.

    Doit être placé AVANT les autres scripts pour pouvoir capter leurs erreurs.
    """
    if "__klody_overlay_script" in doc:
        return doc
    m = re.search(r"<head[^>]*>", doc, re.IGNORECASE)
    if m:
        return doc[: m.end()] + _ERROR_OVERLAY + doc[m.end() :]
    # Fallback : si pas de <head>, on insère au début
    return _ERROR_OVERLAY + doc


def _build_document(
    html: str, css: str, js: str, scripts: list[str], styles: list[str], title: str
) -> tuple[str, list[str]]:
    """Assemble le document final puis complète les dépendances manquantes."""
    # Répare le piège récurrent du JS généré : const muté → let.
    js = _const_reassign_fix(js)
    # En mode ESM, l'import map gère les libs concernées (three…). On retire les
    # <script src> classiques de ces mêmes libs : chargés en script classique, un
    # build ESM lèverait une erreur, et on aurait un double chargement.
    if _is_esm(js):
        roots = _esm_managed_roots(js)
        if roots:
            scripts = [u for u in scripts if not any(r in u.lower() for r in roots)]
    if _is_full_document(html):
        doc = _inject_into_document(html, css, js, scripts, styles)
    else:
        doc = _wrap_fragment(html, css, js, scripts, styles, title)
    doc, warnings = _inject_missing_libs(doc)
    doc = _inject_error_overlay(doc)
    return doc, warnings


def preview_code(
    html: str,
    css: str = "",
    js: str = "",
    title: str = "Preview",
    scripts=None,
    styles=None,
) -> str:
    """
    Écrit un fichier HTML autonome dans PREVIEW_DIR, démarre le serveur HTTP local
    si besoin, et ouvre le navigateur. Retourne l'URL de prévisualisation.

    - html peut être un fragment de body OU un document HTML complet (les deux marchent).
    - scripts/styles : URLs CDN externes (ex: three.js, chart.js) injectées dans <head>.
    - Les librairies courantes utilisées mais non incluses sont ajoutées automatiquement,
      et signalées dans la valeur de retour pour permettre l'auto-correction.
    """
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    full_html, warnings = _build_document(
        html, css, js, _as_list(scripts), _as_list(styles), title
    )

    slug = _slugify(title)
    filename = f"{slug}.html"
    out_path = PREVIEW_DIR / filename
    out_path.write_text(full_html, encoding="utf-8")
    logger.info("[Preview] Fichier écrit: %s (%d o)", out_path, len(full_html))

    base_url = _ensure_server()
    url = f"{base_url}/{filename}"

    webbrowser.open(url)

    msg = (
        f"Aperçu créé avec succès !\n"
        f"  Fichier : {out_path}\n"
        f"  URL     : {url}\n"
        f"Le navigateur s'ouvre automatiquement."
    )
    if warnings:
        msg += "\n\n⚠ Avertissements (corrige si l'aperçu est vide) :\n" + "\n".join(
            f"  - {w}" for w in warnings
        )
    return msg


def preview_file(path: str) -> str:
    """
    Copie un fichier HTML existant du projet dans PREVIEW_DIR et l'ouvre dans le navigateur.
    """
    source = (PROJECT_ROOT / path).resolve()
    try:
        source.relative_to(PROJECT_ROOT)
    except ValueError:
        return f"ERREUR: Chemin hors du projet: {path}"
    if not source.exists():
        return f"ERREUR: Fichier introuvable: {path}"
    if source.suffix.lower() not in (".html", ".htm"):
        return f"ERREUR: Seuls les fichiers .html/.htm sont supportés (reçu: {source.suffix})"

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    content = source.read_text(encoding="utf-8", errors="replace")
    content, warnings = _inject_missing_libs(content)
    dest = PREVIEW_DIR / source.name
    dest.write_text(content, encoding="utf-8")

    base_url = _ensure_server()
    url = f"{base_url}/{source.name}"

    webbrowser.open(url)

    msg = (
        f"Aperçu ouvert !\n"
        f"  Fichier : {dest}\n"
        f"  URL     : {url}"
    )
    if warnings:
        msg += "\n\n⚠ Avertissements (corrige si l'aperçu est vide) :\n" + "\n".join(
            f"  - {w}" for w in warnings
        )
    return msg


def list_previews() -> str:
    """Liste les fichiers HTML présents dans le dossier de prévisualisation."""
    if not PREVIEW_DIR.exists():
        return "Aucun aperçu. Le dossier de prévisualisation n'existe pas encore."

    files = sorted(PREVIEW_DIR.glob("*.html"))
    if not files:
        return "Aucun aperçu HTML dans le dossier de prévisualisation."

    base_url = _ensure_server()
    lines = []
    for f in files:
        size = f.stat().st_size
        url = f"{base_url}/{f.name}"
        lines.append(f"  📄 {f.name}  ({size:,} o)  →  {url}")

    return f"Aperçus disponibles ({len(files)}) :\n" + "\n".join(lines)


def stop_preview_server() -> str:
    """Arrête le serveur de prévisualisation."""
    if _server is None:
        return "Le serveur de prévisualisation n'est pas en cours d'exécution."
    _stop_server()
    return "Serveur de prévisualisation arrêté."
