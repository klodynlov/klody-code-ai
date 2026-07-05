"""Génération de diagrammes UML (mermaid) depuis le code — Roadmap v2 #10.

Produit un diagramme de CLASSES au format Mermaid (`classDiagram`) à partir de la
structure réelle du code, via l'index tree-sitter (`tools.code_index`) — donc fidèle
au code, pas à une intention supposée. Sortie 100 % texte (aucune exécution, aucun
réseau) : le mermaid est rendu par l'UI, GitHub/GitLab ou n'importe quel outil.

Confiné aux racines autorisées ; bornes sur le nombre de classes/méthodes.
"""
from __future__ import annotations

import logging
from pathlib import Path

from config import PROJECT_ROOT, build_allowed_roots, match_allowed_root

logger = logging.getLogger(__name__)

_MAX_CLASSES = 40
_MAX_METHODS = 20

# Racines autorisées. Overridable en test.
_DIAGRAM_ROOTS: list[Path] = build_allowed_roots(PROJECT_ROOT, None)


class DiagramError(Exception):
    """Chemin invalide."""


def _resolve(path: str) -> Path:
    raw = (path or "").strip()
    base = PROJECT_ROOT.resolve() if not raw else (
        Path(raw).expanduser().resolve()
        if Path(raw).expanduser().is_absolute()
        else (PROJECT_ROOT / raw).resolve()
    )
    if match_allowed_root(base, _DIAGRAM_ROOTS) is None:
        raise DiagramError(f"Chemin hors des racines autorisées : '{path}'")
    if not base.is_dir():
        raise DiagramError(f"Dossier introuvable : '{path}'")
    return base


def _sanitize(name: str) -> str:
    """Nom sûr pour un identifiant mermaid (garde alphanum + _ ; sinon remplace)."""
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in name) or "_"


def generate_class_diagram(path: str = "", max_classes: int = _MAX_CLASSES) -> dict:
    """Construit un diagramme de classes Mermaid depuis le code sous `path`."""
    from tools.code_index import CodeIndex

    try:
        base = _resolve(path)
    except DiagramError as exc:
        return {"ok": False, "error": f"ERREUR SÉCURITÉ: {exc}"}

    index = CodeIndex(base)
    if not index.is_available():
        return {"ok": False, "error": (
            "Indexation tree-sitter indisponible (paquets tree-sitter absents)."
        )}

    try:
        max_classes = max(1, min(int(max_classes), _MAX_CLASSES))
    except (TypeError, ValueError):
        max_classes = _MAX_CLASSES

    symbols = index.iter_symbols()
    # Méthodes groupées par classe parente ; classes triées par nom (déterministe).
    methods: dict[str, list[str]] = {}
    class_names: list[str] = []
    for s in symbols:
        if s.kind == "class":
            class_names.append(s.name)
        elif s.kind == "method" and s.parent:
            methods.setdefault(s.parent, []).append(s.name)

    class_names = sorted(dict.fromkeys(class_names))
    if not class_names:
        return {"ok": False, "error": (
            "Aucune classe trouvée sous ce chemin (langages indexés : py/js/ts). "
            "Le diagramme de classes nécessite au moins une classe."
        )}

    truncated = len(class_names) > max_classes
    class_names = class_names[:max_classes]

    lines = ["classDiagram"]
    method_count = 0
    for cname in class_names:
        safe = _sanitize(cname)
        ms = sorted(dict.fromkeys(methods.get(cname, [])))[:_MAX_METHODS]
        if ms:
            lines.append(f"    class {safe} {{")
            for m in ms:
                lines.append(f"        +{_sanitize(m)}()")
                method_count += 1
            lines.append("    }")
        else:
            lines.append(f"    class {safe}")

    mermaid = "\n".join(lines)
    return {
        "ok": True, "mermaid": mermaid, "class_count": len(class_names),
        "method_count": method_count, "truncated": truncated,
    }


def format_diagram_result(res: dict) -> str:
    """Rend le diagramme (bloc mermaid) lisible/rendu-able pour le LLM et l'UI."""
    if not res.get("ok"):
        return res.get("error", "Erreur de génération de diagramme.")
    note = " (tronqué)" if res.get("truncated") else ""
    return (
        f"Diagramme de classes — {res['class_count']} classe(s), "
        f"{res['method_count']} méthode(s){note} :\n\n"
        f"```mermaid\n{res['mermaid']}\n```"
    )
