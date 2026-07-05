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


_GRAPHQL_TEMPLATE = '''"""Schéma GraphQL %RES% — généré par Klody (scaffold_api, Strawberry). \
Store en mémoire ; brancher une vraie persistance en production."""
from __future__ import annotations
%IMPORTS%
import strawberry


@strawberry.type
class %RES%:
    id: int
%INPUT_FIELDS%


@strawberry.input
class %RES%Input:
%INPUT_FIELDS%


_store: dict[int, %RES%] = {}
_next_id = 1


@strawberry.type
class Query:
    @strawberry.field
    def %res%s(self) -> list[%RES%]:
        return list(_store.values())

    @strawberry.field
    def %res%(self, id: int) -> %RES% | None:
        return _store.get(id)


@strawberry.type
class Mutation:
    @strawberry.mutation
    def create_%res%(self, data: %RES%Input) -> %RES%:
        global _next_id
        obj = %RES%(id=_next_id, %CTOR%)
        _store[_next_id] = obj
        _next_id += 1
        return obj

    @strawberry.mutation
    def update_%res%(self, id: int, data: %RES%Input) -> %RES% | None:
        if id not in _store:
            return None
        obj = %RES%(id=id, %CTOR%)
        _store[id] = obj
        return obj

    @strawberry.mutation
    def delete_%res%(self, id: int) -> bool:
        return _store.pop(id, None) is not None


schema = strawberry.Schema(query=Query, mutation=Mutation)
'''


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_")) or "Resource"


class _ScaffoldInvalid(Exception):
    """Entrée invalide pour un générateur (message destiné à l'utilisateur)."""


def _validate_resource_fields(
    resource: str, fields: list | None
) -> tuple[str, list[tuple[str, str]], bool]:
    """Valide/normalise resource + fields (partagé API & SDK).

    Retourne (resource_normalisée, specs[(nom, annotation)], needs_datetime).
    Lève _ScaffoldInvalid avec un message clair sinon."""
    resource = (resource or "").strip().lower()
    if not _IDENT_RE.match(resource):
        raise _ScaffoldInvalid("Nom de ressource invalide (minuscules, commence par une lettre, ex: 'user').")
    if resource == "id":
        raise _ScaffoldInvalid("'id' est réservé (ajouté automatiquement).")

    fields = fields or []
    if not isinstance(fields, (list, tuple)):
        raise _ScaffoldInvalid("'fields' doit être une liste de {name, type}.")
    if len(fields) > _MAX_FIELDS:
        raise _ScaffoldInvalid(f"Trop de champs (> {_MAX_FIELDS}).")

    specs: list[tuple[str, str]] = []
    needs_datetime = False
    seen: set[str] = set()
    for f in fields:
        if not isinstance(f, dict):
            raise _ScaffoldInvalid("Chaque champ doit être un objet {name, type}.")
        fname = str(f.get("name", "")).strip().lower()
        ftype = str(f.get("type", "")).strip().lower()
        if not _IDENT_RE.match(fname):
            raise _ScaffoldInvalid(f"Nom de champ invalide : '{fname}'.")
        if fname == "id" or fname in seen:
            raise _ScaffoldInvalid(f"Champ '{fname}' réservé ou dupliqué.")
        if ftype not in _TYPES:
            raise _ScaffoldInvalid(f"Type '{ftype}' non supporté. Types : {', '.join(sorted(_TYPES))}.")
        seen.add(fname)
        annotation, dt = _TYPES[ftype]
        needs_datetime = needs_datetime or dt
        specs.append((fname, annotation))

    if not specs:
        specs.append(("name", "str"))  # défaut sensé si aucun champ fourni
    return resource, specs, needs_datetime


_FRAMEWORKS = frozenset({"fastapi", "graphql"})


def scaffold_api(resource: str, fields: list | None = None,
                 framework: str = "fastapi") -> dict:
    """Génère un module CRUD. Retourne {ok, code, filename, framework} ou {ok:False,error}."""
    framework = (framework or "fastapi").strip().lower()
    if framework not in _FRAMEWORKS:
        return {"ok": False, "error": (
            f"Framework '{framework}' non supporté. Choix : {', '.join(sorted(_FRAMEWORKS))}."
        )}

    try:
        resource, specs, needs_datetime = _validate_resource_fields(resource, fields)
    except _ScaffoldInvalid as exc:
        return {"ok": False, "error": str(exc)}

    res = _pascal(resource)
    dt_import = "from datetime import datetime\n" if needs_datetime else ""
    if framework == "graphql":
        code = _render_graphql(res, resource, specs, dt_import)
        filename = f"{resource}_schema.py"
    else:
        code = _render_fastapi(res, resource + "s", resource, specs, dt_import)
        filename = f"{resource}_api.py"

    return {"ok": True, "code": code, "filename": filename, "framework": framework}


def _render_fastapi(res: str, plural: str, resource: str,
                    specs: list[tuple[str, str]], dt_import: str) -> str:
    fields = "\n".join(f"    {n}: {a}" for n, a in specs)
    code = (
        _TEMPLATE
        .replace("%IMPORTS%", dt_import.rstrip("\n"))
        .replace("%FIELDS%", fields)
        .replace("%PLURAL%", plural)
        .replace("%RES%", res)
        .replace("%res%", resource)
    )
    return code if dt_import else code.replace("\n\nfrom fastapi", "\nfrom fastapi")


def _render_graphql(res: str, resource: str,
                    specs: list[tuple[str, str]], dt_import: str) -> str:
    input_fields = "\n".join(f"    {n}: {a}" for n, a in specs)
    ctor = ", ".join(f"{n}=data.{n}" for n, _ in specs)
    code = (
        _GRAPHQL_TEMPLATE
        .replace("%IMPORTS%", dt_import.rstrip("\n"))
        .replace("%INPUT_FIELDS%", input_fields)
        .replace("%CTOR%", ctor)
        .replace("%RES%", res)
        .replace("%res%", resource)
    )
    return code if dt_import else code.replace("\n\nimport strawberry", "\nimport strawberry")


_SDK_TEMPLATE = '''"""SDK %RES% — client HTTP typé généré par Klody (scaffold_sdk). \
Nécessite httpx. Pointe `base_url` sur l'API %PLURAL%."""
from __future__ import annotations
%IMPORTS%
from dataclasses import dataclass

import httpx


@dataclass
class %RES%:
    id: int
%FIELDS%


class %RES%Client:
    """Client typé pour l'API %PLURAL% (CRUD)."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def list(self) -> list[%RES%]:
        resp = self._client.get("/%PLURAL%")
        resp.raise_for_status()
        return [%RES%(**item) for item in resp.json()]

    def get(self, item_id: int) -> %RES%:
        resp = self._client.get(f"/%PLURAL%/{item_id}")
        resp.raise_for_status()
        return %RES%(**resp.json())

    def create(self, **fields: object) -> %RES%:
        resp = self._client.post("/%PLURAL%", json=fields)
        resp.raise_for_status()
        return %RES%(**resp.json())

    def update(self, item_id: int, **fields: object) -> %RES%:
        resp = self._client.put(f"/%PLURAL%/{item_id}", json=fields)
        resp.raise_for_status()
        return %RES%(**resp.json())

    def delete(self, item_id: int) -> None:
        resp = self._client.delete(f"/%PLURAL%/{item_id}")
        resp.raise_for_status()

    def close(self) -> None:
        self._client.close()
'''


def _render_python_sdk(res: str, resource: str, plural: str,
                       specs: list[tuple[str, str]], dt_import: str) -> str:
    fields = "\n".join(f"    {n}: {a}" for n, a in specs)
    code = (
        _SDK_TEMPLATE
        .replace("%IMPORTS%", dt_import.rstrip("\n"))
        .replace("%FIELDS%", fields)
        .replace("%PLURAL%", plural)
        .replace("%RES%", res)
    )
    return code if dt_import else code.replace("\n\nfrom dataclasses", "\nfrom dataclasses")


def scaffold_sdk(resource: str, fields: list | None = None,
                 language: str = "python") -> dict:
    """Génère un client SDK typé pour une ressource REST. Retourne {ok, code, filename}."""
    language = (language or "python").strip().lower()
    if language != "python":
        return {"ok": False, "error": f"Langage '{language}' non supporté (python uniquement pour l'instant)."}
    try:
        resource, specs, needs_datetime = _validate_resource_fields(resource, fields)
    except _ScaffoldInvalid as exc:
        return {"ok": False, "error": str(exc)}
    dt_import = "from datetime import datetime\n" if needs_datetime else ""
    code = _render_python_sdk(_pascal(resource), resource, resource + "s", specs, dt_import)
    return {"ok": True, "code": code, "filename": f"{resource}_client.py", "language": "python"}


def format_scaffold_result(res: dict) -> str:
    if not res.get("ok"):
        return res.get("error", "Erreur de génération.")
    if "language" in res:
        kind = "SDK client Python"
    elif res.get("framework") == "graphql":
        kind = "schéma GraphQL (Strawberry)"
    else:
        kind = "module FastAPI CRUD"
    return (
        f"{kind} généré ({res['filename']}). "
        f"Enregistre-le avec write_file, ou empaquette-le avec bundle_zip :\n\n"
        f"```python\n{res['code']}\n```"
    )
