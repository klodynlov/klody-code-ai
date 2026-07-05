"""Scaffolding d'API REST — Roadmap v2 #10.

Génère un module FastAPI CRUD complet et idiomatique (Pydantic v2 + APIRouter,
store en mémoire, endpoints list/get/create/update/delete) à partir d'un nom de
ressource et de champs typés. Sortie 100 % texte, déterministe et **garantie
compilable** ; le LLM peut ensuite l'écrire (write_file) ou l'empaqueter (bundle_zip).

Aucune exécution, aucun réseau. Les entrées sont validées (identifiants, types dans
une allowlist) — pas d'injection dans le code produit.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_MAX_FIELDS = 30

# Types autorisés → (annotation Python, requiert import datetime).
_TYPES: dict[str, tuple[str, bool]] = {
    "str": ("str", False),
    "int": ("int", False),
    "float": ("float", False),
    "bool": ("bool", False),
    "datetime": ("datetime", True),
}

_TEMPLATE = '''"""API REST %RES% — généré par Klody (scaffold_api). Store en mémoire ; \
brancher une vraie persistance en production."""
from __future__ import annotations
%IMPORTS%
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/%PLURAL%", tags=["%RES%"])


class %RES%Base(BaseModel):
%FIELDS%


class %RES%Create(%RES%Base):
    pass


class %RES%(%RES%Base):
    id: int


_store: dict[int, %RES%] = {}
_next_id = 1


@router.get("", response_model=list[%RES%])
def list_%PLURAL%() -> list[%RES%]:
    return list(_store.values())


@router.get("/{item_id}", response_model=%RES%)
def get_%res%(item_id: int) -> %RES%:
    if item_id not in _store:
        raise HTTPException(status_code=404, detail="%res% introuvable")
    return _store[item_id]


@router.post("", response_model=%RES%, status_code=201)
def create_%res%(payload: %RES%Create) -> %RES%:
    global _next_id
    obj = %RES%(id=_next_id, **payload.model_dump())
    _store[_next_id] = obj
    _next_id += 1
    return obj


@router.put("/{item_id}", response_model=%RES%)
def update_%res%(item_id: int, payload: %RES%Create) -> %RES%:
    if item_id not in _store:
        raise HTTPException(status_code=404, detail="%res% introuvable")
    obj = %RES%(id=item_id, **payload.model_dump())
    _store[item_id] = obj
    return obj


@router.delete("/{item_id}", status_code=204)
def delete_%res%(item_id: int) -> None:
    if item_id not in _store:
        raise HTTPException(status_code=404, detail="%res% introuvable")
    del _store[item_id]
'''


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_")) or "Resource"


def scaffold_api(resource: str, fields: list | None = None,
                 framework: str = "fastapi") -> dict:
    """Génère un module CRUD. Retourne {ok, code, filename, framework} ou {ok:False,error}."""
    framework = (framework or "fastapi").strip().lower()
    if framework != "fastapi":
        return {"ok": False, "error": f"Framework '{framework}' non supporté (fastapi uniquement pour l'instant)."}

    resource = (resource or "").strip().lower()
    if not _IDENT_RE.match(resource):
        return {"ok": False, "error": "Nom de ressource invalide (minuscules, commence par une lettre, ex: 'user')."}
    if resource == "id":
        return {"ok": False, "error": "'id' est réservé (ajouté automatiquement)."}

    fields = fields or []
    if not isinstance(fields, (list, tuple)):
        return {"ok": False, "error": "'fields' doit être une liste de {name, type}."}
    if len(fields) > _MAX_FIELDS:
        return {"ok": False, "error": f"Trop de champs (> {_MAX_FIELDS})."}

    field_lines: list[str] = []
    needs_datetime = False
    seen: set[str] = set()
    for f in fields:
        if not isinstance(f, dict):
            return {"ok": False, "error": "Chaque champ doit être un objet {name, type}."}
        fname = str(f.get("name", "")).strip().lower()
        ftype = str(f.get("type", "")).strip().lower()
        if not _IDENT_RE.match(fname):
            return {"ok": False, "error": f"Nom de champ invalide : '{fname}'."}
        if fname in ("id",) or fname in seen:
            return {"ok": False, "error": f"Champ '{fname}' réservé ou dupliqué."}
        if ftype not in _TYPES:
            return {"ok": False, "error": (
                f"Type '{ftype}' non supporté. Types : {', '.join(sorted(_TYPES))}."
            )}
        seen.add(fname)
        annotation, dt = _TYPES[ftype]
        needs_datetime = needs_datetime or dt
        field_lines.append(f"    {fname}: {annotation}")

    if not field_lines:
        field_lines.append("    name: str")  # défaut sensé si aucun champ fourni

    res = _pascal(resource)
    plural = resource + "s"
    imports = "from datetime import datetime\n" if needs_datetime else ""
    code = (
        _TEMPLATE
        .replace("%IMPORTS%", imports.rstrip("\n"))
        .replace("%FIELDS%", "\n".join(field_lines))
        .replace("%PLURAL%", plural)
        .replace("%RES%", res)
        .replace("%res%", resource)
    )
    # Nettoie une éventuelle ligne d'import vide.
    code = code.replace("\n\nfrom fastapi", "\nfrom fastapi") if not imports else code

    return {"ok": True, "code": code, "filename": f"{resource}_api.py", "framework": "fastapi"}


def format_scaffold_result(res: dict) -> str:
    if not res.get("ok"):
        return res.get("error", "Erreur de génération d'API.")
    return (
        f"Module FastAPI CRUD généré ({res['filename']}). "
        f"Enregistre-le avec write_file, ou empaquette-le avec bundle_zip :\n\n"
        f"```python\n{res['code']}\n```"
    )
