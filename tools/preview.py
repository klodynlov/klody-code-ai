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
from pathlib import Path

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
    js_block = f"\n<script>\n{js}\n</script>" if js.strip() else ""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>{_link_tags(styles)}{_script_tags(scripts)}{css_block}
</head>
<body>
{html}{js_block}
</body>
</html>"""


def _inject_into_document(doc: str, css: str, js: str, scripts: list[str], styles: list[str]) -> str:
    """Injecte CSS/JS/scripts/styles supplémentaires dans un document HTML déjà complet."""
    head_extra = _link_tags(styles) + _script_tags(scripts)
    if css.strip():
        head_extra += f"\n<style>\n{css}\n</style>"
    if head_extra:
        doc = _insert_before(doc, "</head>", head_extra, fallback_end=False)
    if js.strip():
        doc = _insert_before(doc, "</body>", f"\n<script>\n{js}\n</script>", fallback_end=True)
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


def _build_document(
    html: str, css: str, js: str, scripts: list[str], styles: list[str], title: str
) -> tuple[str, list[str]]:
    """Assemble le document final puis complète les dépendances manquantes."""
    if _is_full_document(html):
        doc = _inject_into_document(html, css, js, scripts, styles)
    else:
        doc = _wrap_fragment(html, css, js, scripts, styles, title)
    return _inject_missing_libs(doc)


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
