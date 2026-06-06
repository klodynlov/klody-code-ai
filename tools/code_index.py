"""Index code-aware via tree-sitter (Roadmap v2 #6).

Indexe les symboles (fonctions, classes, méthodes) et leurs références
d'un projet pour permettre à Klody de répondre à :
- "où est définie X ?"     → find_symbol(name)
- "qui appelle X ?"        → find_references(name)

Architecture :
- 1 index par PROJECT_ROOT, construit paresseusement à la première requête
- Cache sur le mtime des fichiers : on ne re-parse que les fichiers modifiés
- Supports : .py, .js, .ts, .tsx, .jsx (extensible via _LANGUAGES)
- Skip : .venv, __pycache__, node_modules, .git, etc.

L'index reste en mémoire — pas de persistance pour l'instant (rapide à
rebuild, < 1s pour un repo de quelques centaines de fichiers).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tree_sitter_javascript
    import tree_sitter_python
    import tree_sitter_typescript
    from tree_sitter import Language, Parser
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

logger = logging.getLogger(__name__)

# Mapping extension → (Language, queries) — initialisé paresseusement.
_LANGUAGES: dict[str, Language] = {}
_PARSERS: dict[str, Parser] = {}

# Dossiers à ne pas parcourir
_SKIP_DIRS = frozenset({
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".git",
    "node_modules", "dist", "build", ".next", ".nuxt", ".cache",
    "htmlcov", ".mypy_cache", ".tox", "_preview", "preview", ".claude", "imports",
})

_EXT_TO_LANG = {
    ".py":  "python",
    ".js":  "javascript",
    ".jsx": "javascript",
    ".ts":  "typescript",
    ".tsx": "tsx",
}


def _init_languages() -> None:
    """Initialise les Language tree-sitter (idempotent)."""
    if _LANGUAGES or not _AVAILABLE:
        return
    _LANGUAGES["python"] = Language(tree_sitter_python.language())
    _LANGUAGES["javascript"] = Language(tree_sitter_javascript.language())
    _LANGUAGES["typescript"] = Language(tree_sitter_typescript.language_typescript())
    _LANGUAGES["tsx"] = Language(tree_sitter_typescript.language_tsx())
    for name, lang in _LANGUAGES.items():
        _PARSERS[name] = Parser(lang)


# ---------------------------------------------------------------------------- #
# Modèle                                                                       #
# ---------------------------------------------------------------------------- #


@dataclass
class Symbol:
    """Définition d'un symbole (fonction, classe, méthode)."""
    name: str
    kind: str   # 'function' | 'class' | 'method'
    file: str   # chemin relatif au project_root
    line: int   # 1-indexed
    parent: str = ""  # nom de la classe pour les méthodes


@dataclass
class Reference:
    """Utilisation d'un symbole."""
    name: str
    file: str
    line: int
    context: str = ""  # 1 ligne de code autour


@dataclass
class FileIndex:
    """Index d'un fichier (symboles + références)."""
    mtime: float
    symbols: list[Symbol] = field(default_factory=list)
    refs: list[Reference] = field(default_factory=list)


# ---------------------------------------------------------------------------- #
# Parsing langage                                                              #
# ---------------------------------------------------------------------------- #


def _extract_python(src: bytes, rel_path: str) -> tuple[list[Symbol], list[Reference]]:
    """Extrait symboles + références d'un fichier Python via tree-sitter."""
    parser = _PARSERS["python"]
    tree = parser.parse(src)
    syms: list[Symbol] = []
    refs: list[Reference] = []

    def walk(node: Any, parent_class: str = "") -> None:
        # Définitions
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                kind = "method" if parent_class else "function"
                syms.append(Symbol(
                    name=name_node.text.decode("utf-8", errors="replace"),
                    kind=kind,
                    file=rel_path,
                    line=name_node.start_point[0] + 1,
                    parent=parent_class,
                ))
        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                cls_name = name_node.text.decode("utf-8", errors="replace")
                syms.append(Symbol(
                    name=cls_name,
                    kind="class",
                    file=rel_path,
                    line=name_node.start_point[0] + 1,
                ))
                # Plonger dans la classe avec son nom comme parent
                for child in node.children:
                    walk(child, parent_class=cls_name)
                return  # déjà descendu

        # Références : identifiers utilisés en position d'appel ou d'attribut
        elif node.type == "identifier":
            # On considère ça comme une référence si le parent est un call_expression
            # ou un attribute access en lecture
            parent_type = node.parent.type if node.parent else ""
            if parent_type in ("call", "attribute", "argument_list"):
                name = node.text.decode("utf-8", errors="replace")
                # Extraire la ligne source
                line_start = src.rfind(b"\n", 0, node.start_byte) + 1
                line_end = src.find(b"\n", node.end_byte)
                if line_end == -1:
                    line_end = len(src)
                ctx = src[line_start:line_end].decode("utf-8", errors="replace").strip()
                refs.append(Reference(
                    name=name,
                    file=rel_path,
                    line=node.start_point[0] + 1,
                    context=ctx[:120],
                ))

        for child in node.children:
            walk(child, parent_class)

    walk(tree.root_node)
    return syms, refs


def _extract_javascript_like(src: bytes, rel_path: str, lang_key: str) -> tuple[list[Symbol], list[Reference]]:
    """Extrait symboles d'un fichier JS/TS/TSX (basique)."""
    parser = _PARSERS[lang_key]
    tree = parser.parse(src)
    syms: list[Symbol] = []
    refs: list[Reference] = []

    def walk(node: Any, parent_class: str = "") -> None:
        if node.type in ("function_declaration", "method_definition", "arrow_function"):
            name_node = node.child_by_field_name("name")
            if name_node:
                kind = "method" if (parent_class or node.type == "method_definition") else "function"
                syms.append(Symbol(
                    name=name_node.text.decode("utf-8", errors="replace"),
                    kind=kind,
                    file=rel_path,
                    line=name_node.start_point[0] + 1,
                    parent=parent_class,
                ))
        elif node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                cls_name = name_node.text.decode("utf-8", errors="replace")
                syms.append(Symbol(
                    name=cls_name, kind="class",
                    file=rel_path, line=name_node.start_point[0] + 1,
                ))
                for child in node.children:
                    walk(child, parent_class=cls_name)
                return
        elif node.type == "call_expression":
            fn_node = node.child_by_field_name("function")
            if fn_node and fn_node.type == "identifier":
                name = fn_node.text.decode("utf-8", errors="replace")
                line_start = src.rfind(b"\n", 0, fn_node.start_byte) + 1
                line_end = src.find(b"\n", fn_node.end_byte)
                if line_end == -1:
                    line_end = len(src)
                ctx = src[line_start:line_end].decode("utf-8", errors="replace").strip()
                refs.append(Reference(
                    name=name, file=rel_path,
                    line=fn_node.start_point[0] + 1,
                    context=ctx[:120],
                ))

        for child in node.children:
            walk(child, parent_class)

    walk(tree.root_node)
    return syms, refs


# ---------------------------------------------------------------------------- #
# CodeIndex                                                                    #
# ---------------------------------------------------------------------------- #


class CodeIndex:
    """Index incrémental des symboles + références d'un projet."""

    def __init__(self, project_root: Path):
        self.root: Path = Path(project_root).resolve()
        self._files: dict[str, FileIndex] = {}  # rel_path → FileIndex

    def is_available(self) -> bool:
        return _AVAILABLE

    # -- Indexation -------------------------------------------------------- #

    def _iter_source_files(self) -> Iterator[Path]:
        # os.walk + élagage EN PLACE de dirnames : on ne DESCEND jamais dans
        # _SKIP_DIRS (.venv, node_modules…). L'ancien `rglob("*")` parcourait et
        # stat-ait tout l'arbre — y compris des milliers de fichiers de .venv —
        # avant de les filtrer après coup. Ici ils sont coupés à la racine.
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in filenames:
                if Path(fname).suffix not in _EXT_TO_LANG:
                    continue
                yield Path(dirpath) / fname

    def refresh(self) -> int:
        """Re-indexe les fichiers ajoutés/modifiés. Retourne le nb d'updates."""
        if not _AVAILABLE:
            return 0
        _init_languages()

        seen: set[str] = set()
        updated = 0
        for path in self._iter_source_files():
            rel = str(path.relative_to(self.root))
            seen.add(rel)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            cached = self._files.get(rel)
            if cached and cached.mtime >= mtime:
                continue
            # (Re)parse
            try:
                src = path.read_bytes()
            except (OSError, MemoryError):
                continue
            lang_key = _EXT_TO_LANG[path.suffix]
            try:
                if lang_key == "python":
                    syms, refs = _extract_python(src, rel)
                else:
                    syms, refs = _extract_javascript_like(src, rel, lang_key)
            except Exception as exc:
                logger.debug("Parse failed %s: %s", rel, exc)
                continue
            self._files[rel] = FileIndex(mtime=mtime, symbols=syms, refs=refs)
            updated += 1

        # Purger les fichiers supprimés
        for rel in list(self._files.keys()):
            if rel not in seen:
                del self._files[rel]
                updated += 1

        return updated

    # -- API publique ------------------------------------------------------ #

    def find_symbol(self, name: str) -> list[Symbol]:
        """Cherche par nom exact (case-sensitive)."""
        self.refresh()
        out: list[Symbol] = []
        for idx in self._files.values():
            for s in idx.symbols:
                if s.name == name:
                    out.append(s)
        return out

    def find_references(self, name: str, max_results: int = 50) -> list[Reference]:
        """Liste les références à un nom dans tout le projet."""
        self.refresh()
        out: list[Reference] = []
        for idx in self._files.values():
            for r in idx.refs:
                if r.name == name:
                    out.append(r)
                    if len(out) >= max_results:
                        return out
        return out

    def stats(self) -> dict:
        """Compteurs (debug/intro)."""
        self.refresh()
        return {
            "files": len(self._files),
            "symbols": sum(len(idx.symbols) for idx in self._files.values()),
            "references": sum(len(idx.refs) for idx in self._files.values()),
        }


# ---------------------------------------------------------------------------- #
# Formatters pour la réinjection LLM                                           #
# ---------------------------------------------------------------------------- #


def format_symbols(syms: list[Symbol]) -> str:
    if not syms:
        return "Aucun symbole trouvé."
    lines = [f"{len(syms)} définition(s) trouvée(s) :"]
    for s in syms[:20]:
        suffix = f" (dans classe {s.parent})" if s.parent else ""
        lines.append(f"  • {s.kind} `{s.name}`{suffix} — {s.file}:{s.line}")
    if len(syms) > 20:
        lines.append(f"  ... +{len(syms) - 20} autres (max 20 affichés)")
    return "\n".join(lines)


def format_references(refs: list[Reference]) -> str:
    if not refs:
        return "Aucune référence trouvée."
    lines = [f"{len(refs)} référence(s) :"]
    for r in refs[:25]:
        lines.append(f"  • {r.file}:{r.line}  {r.context}")
    if len(refs) > 25:
        lines.append(f"  ... +{len(refs) - 25} autres (max 25 affichées)")
    return "\n".join(lines)
