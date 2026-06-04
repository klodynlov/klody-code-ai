"""Génération de classeurs Excel (.xlsx) téléchargeables.

Le fichier est écrit dans `config.DOWNLOADS_DIR` (servi par l'API sur
`/api/files/<nom>`) — JAMAIS sous le projet ni une racine arbitraire : le nom
est réduit à un basename assaini et l'extension forcée à `.xlsx`, donc aucune
traversée de chemin n'est possible.

`openpyxl` est une dépendance optionnelle au sens du code (même pattern que
`tools/audio.py` avec librosa) : son absence renvoie une erreur lisible plutôt
que de planter l'agent. Elle est néanmoins déclarée dans requirements.txt car
la génération Excel est une fonctionnalité de premier plan.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from config import DOWNLOADS_DIR

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:  # pragma: no cover - dépend de l'environnement
    HAS_OPENPYXL = False

logger = logging.getLogger(__name__)

# Garde-fous (mémoire / temps de génération).
_MAX_ROWS = 100_000
_MAX_COLS = 1_000
_MAX_COL_WIDTH = 60

# Caractères interdits dans un nom de fichier → on n'en garde qu'un sous-ensemble
# sûr. `\w` (mode Unicode par défaut) garde les lettres accentuées : appli FR, on
# ne veut pas mutiler « résumé.xlsx » en « r_sum_.xlsx ». La protection
# anti-traversée ne repose PAS sur cette regex (cosmétique) mais sur
# `Path(...).name` + `resolve()` + vérif du dossier parent dans `generate_excel`.
_UNSAFE_NAME = re.compile(r"[^\w.\- ]+")
# Caractères interdits par Excel dans un titre d'onglet.
_UNSAFE_SHEET = re.compile(r"[\[\]:*?/\\]")


def _safe_filename(filename: str) -> str:
    """Réduit `filename` à un basename sûr terminé par `.xlsx`.

    Vire tout composant de chemin (anti-traversée), nettoie les caractères
    exotiques et force l'extension `.xlsx`.
    """
    base = Path(str(filename or "").strip()).name
    base = _UNSAFE_NAME.sub("_", base).strip(". ")
    stem = Path(base).stem or "export"
    return f"{stem}.xlsx"


def _safe_sheet_title(name: Any, used: set[str]) -> str:
    """Titre d'onglet valide Excel (≤ 31 car., sans `[]:*?/\\`), unique dans `used`."""
    title = _UNSAFE_SHEET.sub(" ", str(name or "Feuille")).strip()[:31] or "Feuille"
    base, n = title, 2
    while title.lower() in used:
        suffix = f" ({n})"
        title = f"{base[:31 - len(suffix)]}{suffix}"
        n += 1
    used.add(title.lower())
    return title


def _coerce(value: Any) -> Any:
    """Valeur écrivable telle quelle par openpyxl ; le reste est stringifié."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalize_sheets(sheets: Any) -> list[dict]:
    """Normalise l'entrée en `[{name, columns, rows}]`.

    Tolère : une feuille unique (dict), une liste de feuilles, et des lignes
    fournies en liste de dicts (les clés deviennent les en-têtes).
    """
    if sheets is None:
        return []
    if isinstance(sheets, dict):
        sheets = [sheets]
    if not isinstance(sheets, list):
        return []

    out: list[dict] = []
    for i, sh in enumerate(sheets):
        if not isinstance(sh, dict):
            continue
        name = sh.get("name") or f"Feuille{i + 1}"
        columns = sh.get("columns")
        rows = sh.get("rows") or []
        if not isinstance(rows, list):
            rows = []
        # Lignes en liste de dicts → en-têtes dérivés de l'union ordonnée des clés.
        if rows and isinstance(rows[0], dict):
            if not columns:
                seen: dict[str, None] = {}
                for r in rows:
                    if isinstance(r, dict):
                        for k in r:
                            seen.setdefault(str(k), None)
                columns = list(seen)
            rows = [[r.get(c) for c in columns] for r in rows if isinstance(r, dict)]
        out.append({"name": name, "columns": columns, "rows": rows})
    return out


def _autosize(ws: Any, columns: Any, rows: list) -> None:
    """Largeur de colonne ≈ longueur du contenu le plus large (bornée)."""
    widths: dict[int, int] = {}

    def consider(idx: int, value: Any) -> None:
        length = len(str(value)) if value is not None else 0
        if length > widths.get(idx, 0):
            widths[idx] = length

    if columns:
        for i, c in enumerate(columns):
            consider(i, c)
    for r in rows:
        cells = r if isinstance(r, (list, tuple)) else [r]
        for i, c in enumerate(cells):
            consider(i, c)
    for i, w in widths.items():
        ws.column_dimensions[get_column_letter(i + 1)].width = min(max(w + 2, 8), _MAX_COL_WIDTH)


def generate_excel(filename: str, sheets: Any = None) -> dict:
    """Construit un classeur `.xlsx` et l'écrit dans `DOWNLOADS_DIR`.

    Args:
        filename: nom souhaité (assaini, extension forcée `.xlsx`).
        sheets: une feuille (dict) ou une liste de feuilles `{name, columns, rows}`.
            `rows` accepte une liste de listes OU une liste de dicts.

    Returns:
        `{"status": "ok", "filename", "path", "download_url", "sheets", "rows",
        "size"}` en cas de succès, sinon `{"error": "..."}`.
    """
    if not HAS_OPENPYXL:
        return {"error": "openpyxl non installé — `pip install openpyxl`."}

    norm = _normalize_sheets(sheets)
    if not norm:
        return {"error": "Aucune donnée : fournis au moins une feuille avec des lignes."}

    safe_name = _safe_filename(filename)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = (DOWNLOADS_DIR / safe_name).resolve()
    if dest.parent != DOWNLOADS_DIR.resolve():
        return {"error": f"Nom de fichier invalide : {filename!r}"}

    wb = Workbook()
    wb.remove(wb.active)  # on crée nos propres feuilles nommées

    used_titles: set[str] = set()
    sheet_names: list[str] = []
    total_rows = 0

    for sh in norm:
        title = _safe_sheet_title(sh["name"], used_titles)
        ws = wb.create_sheet(title=title)
        sheet_names.append(title)

        columns = sh.get("columns")
        rows = sh.get("rows") or []
        if len(rows) > _MAX_ROWS:
            logger.warning("Excel %s/%s : %d lignes tronquées à %d",
                           safe_name, title, len(rows), _MAX_ROWS)
            rows = rows[:_MAX_ROWS]

        if columns:
            ws.append([_coerce(c) for c in list(columns)[:_MAX_COLS]])
            for cell in ws[1]:
                cell.font = Font(bold=True)
            ws.freeze_panes = "A2"

        for r in rows:
            cells = r if isinstance(r, (list, tuple)) else [r]
            ws.append([_coerce(c) for c in list(cells)[:_MAX_COLS]])
            total_rows += 1

        _autosize(ws, columns, rows)

    if not wb.sheetnames:  # garde-fou : un classeur sans feuille ne peut être sauvé
        wb.create_sheet(title="Feuille1")
        sheet_names.append("Feuille1")

    wb.save(dest)
    size = dest.stat().st_size
    logger.info("Excel généré : %s (%d feuille(s), %d lignes, %d o)",
                safe_name, len(sheet_names), total_rows, size)

    return {
        "status": "ok",
        "filename": safe_name,
        "path": str(dest),
        "download_url": f"/api/files/{safe_name}",
        "sheets": sheet_names,
        "rows": total_rows,
        "size": size,
    }
