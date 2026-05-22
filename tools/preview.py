"""Aperçu local de code HTML/CSS/JS — serveur HTTP éphémère + ouverture navigateur."""
from __future__ import annotations

import atexit
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


def preview_code(html: str, css: str = "", js: str = "", title: str = "Preview") -> str:
    """
    Écrit un fichier HTML autonome (avec CSS/JS inline) dans PREVIEW_DIR,
    démarre le serveur HTTP local si besoin, et ouvre le navigateur.
    Retourne l'URL de prévisualisation.
    """
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    slug = _slugify(title)
    filename = f"{slug}.html"

    css_block = f"\n<style>\n{css}\n</style>" if css.strip() else ""
    js_block = f"\n<script>\n{js}\n</script>" if js.strip() else ""

    full_html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>{css_block}
</head>
<body>
{html}
{js_block}
</body>
</html>"""

    out_path = PREVIEW_DIR / filename
    out_path.write_text(full_html, encoding="utf-8")
    logger.info("[Preview] Fichier écrit: %s (%d o)", out_path, len(full_html))

    base_url = _ensure_server()
    url = f"{base_url}/{filename}"

    webbrowser.open(url)

    return (
        f"Aperçu créé avec succès !\n"
        f"  Fichier : {out_path}\n"
        f"  URL     : {url}\n"
        f"Le navigateur s'ouvre automatiquement."
    )


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
    dest = PREVIEW_DIR / source.name
    dest.write_text(source.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

    base_url = _ensure_server()
    url = f"{base_url}/{source.name}"

    webbrowser.open(url)

    return (
        f"Aperçu ouvert !\n"
        f"  Fichier : {dest}\n"
        f"  URL     : {url}"
    )


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
