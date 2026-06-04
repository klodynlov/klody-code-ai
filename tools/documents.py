"""Génération de fichiers texte/code téléchargeables (.txt, .md, .csv, .html, .py…).

Même modèle que `tools/excel.py` : l'artefact est écrit dans `config.DOWNLOADS_DIR`
(servi par l'API sur `/api/files/<nom>`), JAMAIS sous le projet ni une racine
arbitraire. Le nom est réduit à un basename assaini ; l'extension est conservée si
elle figure dans un allowlist sûr, sinon ramenée à `.txt`. Aucune dépendance
externe : on écrit du texte UTF-8 brut.

Le fichier est servi en pièce jointe (`Content-Disposition: attachment`), jamais
exécuté ni rendu comme page — l'extension n'est qu'un indice de nommage/type.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

# Garde-fou mémoire/disque : un fichier texte généré reste raisonnable.
_MAX_BYTES = 20 * 1024 * 1024  # 20 Mo

# Caractères interdits dans un nom de fichier → allowlist Unicode-friendly
# (garde les lettres accentuées : appli FR). L'anti-traversée ne repose PAS sur
# cette regex (cosmétique) mais sur `Path(...).name` + `resolve()` + vérif parent.
_UNSAFE_NAME = re.compile(r"[^\w.\- ]+")

# Extensions texte/code autorisées telles quelles (sinon → .txt).
_ALLOWED_EXTS: frozenset[str] = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".html", ".htm", ".css", ".scss", ".less", ".js", ".mjs", ".cjs",
    ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".py", ".php", ".rb", ".go",
    ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".swift",
    ".sh", ".bash", ".zsh", ".sql", ".rtf", ".tex", ".log", ".srt", ".vtt",
})
# Secrets : jamais servis en téléchargement (miroir de tools/file_manager).
_BLOCKED_EXTS: frozenset[str] = frozenset({
    ".env", ".key", ".pem", ".p12", ".pfx", ".cer", ".crt", ".ppk", ".p8",
})


def _safe_filename(filename: str) -> str:
    """Basename assaini ; extension conservée si sûre, sinon `.txt`."""
    base = _UNSAFE_NAME.sub("_", Path(str(filename or "").strip()).name).strip(". ")
    p = Path(base or "document")
    stem = p.stem or "document"
    ext = p.suffix.lower()
    if ext in _BLOCKED_EXTS or ext not in _ALLOWED_EXTS:
        ext = ".txt"
    return f"{stem}{ext}"


def generate_text_file(filename: str, content: str = "") -> dict:
    """Écrit `content` (texte UTF-8) dans un fichier téléchargeable de `DOWNLOADS_DIR`.

    Args:
        filename: nom souhaité (assaini ; extension conservée si sûre, sinon `.txt`).
        content: contenu texte du fichier.

    Returns:
        `{"status": "ok", "filename", "path", "download_url", "size"}` ou `{"error"}`.
    """
    text = "" if content is None else str(content)
    data = text.encode("utf-8")
    if len(data) > _MAX_BYTES:
        return {"error": f"Contenu trop volumineux ({len(data)} o > {_MAX_BYTES} o)."}

    safe_name = _safe_filename(filename)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = (DOWNLOADS_DIR / safe_name).resolve()
    if dest.parent != DOWNLOADS_DIR.resolve():
        return {"error": f"Nom de fichier invalide : {filename!r}"}

    dest.write_bytes(data)
    size = dest.stat().st_size
    logger.info("Fichier texte généré : %s (%d o)", safe_name, size)
    return {
        "status": "ok",
        "filename": safe_name,
        "path": str(dest),
        "download_url": f"/api/files/{safe_name}",
        "size": size,
    }
