"""Exécution SQL locale sandboxée — outil runtime (Roadmap v2 #10).

Exécute une requête SQL sur un fichier **SQLite local** de l'utilisateur, via le
module stdlib `sqlite3` (aucune dépendance nouvelle, aucun réseau, aucun daemon).

Sécurité — le sandbox fichiers de Klody ne doit PAS être contournable via SQL. Les
contrôles ci-dessous ont été dérivés d'un threat-model adversarial (vecteurs :
ATTACH, VACUUM INTO, load_extension, complétude read-only, injection d'URI, DoS) :

1. **Confinement chemin** : la base doit tomber sous une racine autorisée
   (`match_allowed_root`), extensions/noms sensibles bloqués, création interdite
   (le fichier doit exister). Toute valeur `database` de forme URI (`file:`, `://`,
   `?`, `#`, `%`) est refusée → pas d'injection de paramètres d'URI (mode=rwc, vfs…).
2. **URI construite sûrement** : le chemin résolu est percent-encodé (`urllib.quote`)
   avant `file:<path>?mode=ro|rw` — jamais d'f-string brute.
3. **Authorizer DEFAULT-DENY** (whitelist) : seules les actions explicitement
   autorisées passent. ATTACH/DETACH → toujours DENY (bloque aussi VACUUM INTO, qui
   déclenche SQLITE_ATTACH). Fonctions dangereuses (load_extension, readfile…) DENY.
4. **Verrou dur ATTACH** : `setlimit(SQLITE_LIMIT_ATTACHED, 0)` — indépendant de
   l'authorizer.
5. **load_extension** : `enable_load_extension` laissé à False (défaut) et jamais
   appelé ; l'authorizer refuse la fonction en plus.
6. **Read-only complet** : ouverture `mode=ro` + `PRAGMA temp_store=MEMORY` +
   authorizer qui refuse toute écriture/DDL/temp/vtable.
7. **Anti-DoS** : `SQLITE_LIMIT_LENGTH` (bombe mémoire `randomblob(1e9)`),
   `SQLITE_LIMIT_VDBE_OP`, budget d'octets sur le résultat, cap de lignes, et un
   `set_progress_handler` à échéance **wall-clock** (le seul moyen d'interrompre un
   appel C SQLite bloquant).
8. **Une seule instruction** par appel (`conn.execute` lève sur multi-statements).
9. **write désactivé par défaut** (`config.SQL_WRITE_ENABLED`, sûr par défaut).
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from config import (
    PROJECT_ROOT,
    SQL_WRITE_ENABLED,
    build_allowed_roots,
    match_allowed_root,
)

from tools.file_manager import BLOCKED_EXTENSIONS, BLOCKED_FILENAMES

logger = logging.getLogger(__name__)

# Racines autorisées (PROJECT_ROOT + ALLOWED_ROOTS). Overridable en test.
_SQL_ROOTS: list[Path] = build_allowed_roots(PROJECT_ROOT, None)

# --- Bornes anti-DoS -------------------------------------------------------- #
_DEFAULT_MAX_ROWS = 100
_HARD_MAX_ROWS = 1000
_QUERY_TIMEOUT_S = 5.0
_RESULT_BYTE_BUDGET = 5_000_000   # ~5 Mo de résultat max
_CELL_MAX = 100_000               # troncature d'affichage d'une cellule géante
_CONNECT_TIMEOUT_S = 2.0

# --- Codes d'action sqlite3 (résolus défensivement selon la version) -------- #


def _code(name: str, default: int) -> int:
    return int(getattr(sqlite3, name, default))


_SQLITE_OK = _code("SQLITE_OK", 0)
_SQLITE_DENY = _code("SQLITE_DENY", 1)
_SQLITE_FUNCTION = _code("SQLITE_FUNCTION", 31)

# Whitelist LECTURE (default-deny : tout le reste est refusé).
_READ_ACTIONS: frozenset[int] = frozenset({
    _code("SQLITE_SELECT", 21),
    _code("SQLITE_READ", 20),
    _SQLITE_FUNCTION,
    _code("SQLITE_RECURSIVE", 33),
})

# Whitelist ÉCRITURE = lecture + DML/DDL usuels (jamais ATTACH/DETACH/vtable/temp).
_WRITE_ACTIONS: frozenset[int] = _READ_ACTIONS | frozenset({
    _code("SQLITE_INSERT", 18),
    _code("SQLITE_UPDATE", 23),
    _code("SQLITE_DELETE", 9),
    _code("SQLITE_TRANSACTION", 22),
    _code("SQLITE_SAVEPOINT", 32),
    _code("SQLITE_CREATE_TABLE", 2),
    _code("SQLITE_CREATE_INDEX", 1),
    _code("SQLITE_CREATE_VIEW", 8),
    _code("SQLITE_CREATE_TRIGGER", 7),
    _code("SQLITE_DROP_TABLE", 11),
    _code("SQLITE_DROP_INDEX", 10),
    _code("SQLITE_DROP_VIEW", 17),
    _code("SQLITE_DROP_TRIGGER", 16),
    _code("SQLITE_ALTER_TABLE", 26),
    _code("SQLITE_REINDEX", 27),
    _code("SQLITE_ANALYZE", 28),
})

# Fonctions SQL toujours refusées (défense en profondeur ; load_extension exige de
# toute façon enable_load_extension(True), jamais activé ici).
_DENIED_FUNCTIONS: frozenset[str] = frozenset({
    "load_extension", "readfile", "writefile", "fts3_tokenizer",
    "zipfile", "edit",
})

# Limites dures posées sur chaque connexion (nom sqlite3 → valeur).
_LIMITS: tuple[tuple[str, int], ...] = (
    ("SQLITE_LIMIT_ATTACHED", 0),              # verrou ATTACH (anti VACUUM INTO / évasion)
    ("SQLITE_LIMIT_LENGTH", 1_000_000),        # bombe mémoire (randomblob/zeroblob/hex)
    ("SQLITE_LIMIT_SQL_LENGTH", 100_000),
    ("SQLITE_LIMIT_EXPR_DEPTH", 200),
    ("SQLITE_LIMIT_COMPOUND_SELECT", 50),
    ("SQLITE_LIMIT_VDBE_OP", 500_000),
    ("SQLITE_LIMIT_LIKE_PATTERN_LENGTH", 5_000),
)


class SqlSandboxViolation(Exception):
    """Chemin de base non autorisé ou entrée malveillante."""


def _make_authorizer(write: bool):
    """Authorizer DEFAULT-DENY : n'autorise que la whitelist du mode courant."""
    allowed = _WRITE_ACTIONS if write else _READ_ACTIONS

    def _auth(action: int, arg1: Any, arg2: Any, dbname: Any, source: Any) -> int:
        if action == _SQLITE_FUNCTION:
            # arg2 = nom de la fonction ; refuse les fonctions à effet de bord fichier.
            name = (arg2 or "").lower()
            return _SQLITE_DENY if name in _DENIED_FUNCTIONS else _SQLITE_OK
        return _SQLITE_OK if action in allowed else _SQLITE_DENY

    return _auth


def _validate_db_path(database: str) -> Path:
    """Résout + confine le chemin de la base. Lève SqlSandboxViolation sinon."""
    if not database or not database.strip():
        raise SqlSandboxViolation("Chemin de base vide.")
    raw = database.strip()
    if raw.lower().startswith("file:") or "://" in raw:
        raise SqlSandboxViolation("URI non autorisée : passe un chemin de fichier, pas une URI.")
    if any(c in raw for c in ("?", "#", "\x00", "\n", "\r")):
        raise SqlSandboxViolation("Caractères non autorisés dans le chemin de base.")

    p = Path(raw).expanduser()
    resolved = p.resolve() if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    if match_allowed_root(resolved, _SQL_ROOTS) is None:
        raise SqlSandboxViolation(f"Base hors des racines autorisées : '{database}'")
    if resolved.suffix.lower() in BLOCKED_EXTENSIONS or resolved.name in BLOCKED_FILENAMES:
        raise SqlSandboxViolation(f"Fichier bloqué : '{resolved.name}'")
    # Le chemin résolu ne doit contenir aucun méta-caractère d'URI (défense injection).
    if any(c in str(resolved) for c in ("?", "#", "%")):
        raise SqlSandboxViolation("Chemin résolu contenant un méta-caractère d'URI.")
    if not resolved.exists() or not resolved.is_file():
        raise SqlSandboxViolation(
            f"Base introuvable : '{database}' (la création de base n'est pas supportée)."
        )
    return resolved


def _apply_limits(conn: sqlite3.Connection) -> None:
    for name, val in _LIMITS:
        code = getattr(sqlite3, name, None)
        if code is None:
            continue
        with contextlib.suppress(sqlite3.Error, AttributeError, OverflowError):
            conn.setlimit(int(code), val)


def _open(resolved: Path, write: bool) -> sqlite3.Connection:
    mode = "rw" if write else "ro"
    uri = "file:" + quote(str(resolved), safe="/") + f"?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, timeout=_CONNECT_TIMEOUT_S)
    # Durcissement AVANT l'installation de l'authorizer (sinon ces pragmas seraient refusés).
    for pragma in ("PRAGMA temp_store=MEMORY", "PRAGMA trusted_schema=OFF"):
        with contextlib.suppress(sqlite3.Error):
            conn.execute(pragma)
    _apply_limits(conn)
    conn.set_authorizer(_make_authorizer(write))
    return conn


def _install_deadline(conn: sqlite3.Connection, timeout_s: float) -> None:
    """Interruption wall-clock : seul moyen d'abattre un appel C SQLite bloquant."""
    start = time.monotonic()

    def _handler() -> int:
        return 1 if (time.monotonic() - start) > timeout_s else 0

    conn.set_progress_handler(_handler, 1000)


def _coerce_cell(v: Any) -> Any:
    if isinstance(v, bytes):
        return f"<blob {len(v)} octets>"
    if isinstance(v, str) and len(v) > _CELL_MAX:
        return v[:_CELL_MAX] + " […]"
    return v


def run_sql(
    query: str,
    database: str,
    mode: str = "read",
    params: list | tuple | None = None,
    max_rows: int = _DEFAULT_MAX_ROWS,
) -> dict:
    """Exécute UNE instruction SQL sur une base SQLite locale confinée.

    Retourne un dict : {ok, mode, database, columns, rows, rowcount, truncated} en
    cas de succès, ou {ok: False, error} sinon.
    """
    if mode not in ("read", "write"):
        return {"ok": False, "error": f"mode invalide : '{mode}' (attendu 'read' ou 'write')."}
    write = mode == "write"
    if write and not SQL_WRITE_ENABLED:
        return {"ok": False, "error": (
            "Mode 'write' désactivé (SQL_WRITE_ENABLED=false). Utilise mode='read', "
            "ou active le flag pour autoriser l'écriture."
        )}
    if not query or not query.strip():
        return {"ok": False, "error": "Requête vide."}

    try:
        resolved = _validate_db_path(database)
    except SqlSandboxViolation as exc:
        return {"ok": False, "error": f"ERREUR SÉCURITÉ: {exc}"}

    try:
        max_rows = max(1, min(int(max_rows), _HARD_MAX_ROWS))
    except (TypeError, ValueError):
        max_rows = _DEFAULT_MAX_ROWS
    bind = tuple(params) if isinstance(params, (list, tuple)) else ()

    conn: sqlite3.Connection | None = None
    try:
        conn = _open(resolved, write)
        _install_deadline(conn, _QUERY_TIMEOUT_S)
        cur = conn.execute(query, bind)  # lève sur multi-statements

        if cur.description is None:
            # Instruction sans résultat (write : INSERT/UPDATE/DELETE/DDL).
            if write:
                conn.commit()
            return {
                "ok": True, "mode": mode, "database": str(resolved),
                "columns": [], "rows": [], "rowcount": cur.rowcount, "truncated": False,
            }

        columns = [d[0] for d in cur.description]
        rows: list[list[Any]] = []
        truncated = False
        budget = _RESULT_BYTE_BUDGET
        for raw_row in cur:
            row = [_coerce_cell(v) for v in raw_row]
            rows.append(row)
            budget -= sum(len(str(v)) for v in row)
            if len(rows) >= max_rows or budget <= 0:
                truncated = True
                break
        if write:
            conn.commit()
        return {
            "ok": True, "mode": mode, "database": str(resolved),
            "columns": columns, "rows": rows, "rowcount": len(rows), "truncated": truncated,
        }
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"SQLite: {exc}"}
    finally:
        if conn is not None:
            conn.close()


def format_sql_result(res: dict) -> str:
    """Rend le résultat de run_sql lisible pour le LLM."""
    if not res.get("ok"):
        return res.get("error", "Erreur SQL inconnue.")
    cols = res.get("columns") or []
    if not cols:
        return (
            f"OK (mode {res['mode']}). Lignes affectées : {res['rowcount']}. "
            f"Base : {res['database']}"
        )
    header = " | ".join(cols)
    lines = [
        f"{res['rowcount']} ligne(s)" + (" (tronqué)" if res.get("truncated") else "") + " :",
        header,
        "-" * min(len(header), 100),
    ]
    for row in res["rows"]:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)
