"""Création d'archives `.zip` téléchargeables (stdlib `zipfile`, 0 dépendance).

Même modèle que `tools/excel.py` : l'archive est écrite dans `config.DOWNLOADS_DIR`
(servie sur `/api/files/<nom>`), nom basenamé + extension `.zip` forcée. Les noms
d'entrées DANS l'archive sont assainis pour empêcher le « zip-slip » (chemins
absolus ou `..` qui s'échapperaient du dossier à l'extraction).

Entrée : une liste `[{name, content}]` (contenu fourni en clair par l'appelant) —
pas de lecture du disque, donc aucune traversée possible à la lecture.
"""
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 2000
_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 Mo (avant compression)
_UNSAFE_NAME = re.compile(r"[^\w.\- ]+")


def _safe_filename(filename: str) -> str:
    """Basename assaini terminé par `.zip`."""
    base = _UNSAFE_NAME.sub("_", Path(str(filename or "").strip()).name).strip(". ")
    return f"{Path(base).stem or 'archive'}.zip"


def _safe_entry_name(name: Any) -> str:
    """Chemin relatif sûr dans l'archive (anti zip-slip) : ni absolu, ni `..`.

    Conserve l'arborescence (`src/App.tsx`) mais supprime tout segment `.`/`..`,
    les racines absolues et les caractères exotiques.
    """
    parts: list[str] = []
    for seg in PurePosixPath(str(name or "").replace("\\", "/")).parts:
        if seg in ("", ".", "..", "/"):
            continue
        cleaned = _UNSAFE_NAME.sub("_", seg).strip(". ")
        parts.append(cleaned or "_")
    return "/".join(parts) or "fichier.txt"


def _unique(entry: str, used: set[str]) -> str:
    """Rend `entry` unique dans `used` en suffixant ` (n)` avant l'extension."""
    if entry not in used:
        used.add(entry)
        return entry
    stem, dot, ext = entry.rpartition(".")
    base, suffix = (stem, f".{ext}") if dot else (entry, "")
    n = 2
    while f"{base} ({n}){suffix}" in used:
        n += 1
    out = f"{base} ({n}){suffix}"
    used.add(out)
    return out


def bundle_zip(filename: str, files: Any = None) -> dict:
    """Construit une archive `.zip` à partir d'entrées `{name, content}`.

    Args:
        filename: nom souhaité (assaini, extension `.zip` forcée).
        files: liste `[{"name": "src/App.tsx", "content": "..."}, ...]`.

    Returns:
        `{"status": "ok", "filename", "path", "download_url", "entries", "size"}`
        ou `{"error": "..."}`.
    """
    if not isinstance(files, list) or not files:
        return {"error": "Aucun fichier : fournis une liste [{name, content}]."}

    safe_name = _safe_filename(filename)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = (DOWNLOADS_DIR / safe_name).resolve()
    if dest.parent != DOWNLOADS_DIR.resolve():
        return {"error": f"Nom de fichier invalide : {filename!r}"}

    used: set[str] = set()
    written: list[str] = []
    total = 0
    overflow = False

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, item in enumerate(files):
            if not isinstance(item, dict):
                continue
            if len(written) >= _MAX_ENTRIES:
                logger.warning("zip %s : > %d entrées, surplus ignoré", safe_name, _MAX_ENTRIES)
                break
            content = item.get("content", "")
            data = (content if isinstance(content, str) else str(content)).encode("utf-8")
            if total + len(data) > _MAX_TOTAL_BYTES:
                overflow = True
                break
            total += len(data)
            entry = _unique(_safe_entry_name(item.get("name") or f"fichier_{i + 1}.txt"), used)
            zf.writestr(entry, data)
            written.append(entry)

    if overflow:
        dest.unlink(missing_ok=True)
        return {"error": f"Archive trop volumineuse (> {_MAX_TOTAL_BYTES // (1024 * 1024)} Mo)."}
    if not written:
        dest.unlink(missing_ok=True)
        return {"error": "Aucune entrée valide à archiver."}

    size = dest.stat().st_size
    logger.info("Archive zip générée : %s (%d entrées, %d o)", safe_name, len(written), size)
    return {
        "status": "ok",
        "filename": safe_name,
        "path": str(dest),
        "download_url": f"/api/files/{safe_name}",
        "entries": written,
        "size": size,
    }
