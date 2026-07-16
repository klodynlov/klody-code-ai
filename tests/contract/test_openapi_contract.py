"""Test de contrat OpenAPI — fige les routes REST exposées par FastAPI.

Tout retrait/ajout/renommage de route est visible dans la diff git du snapshot.
Le WebSocket `/api/ws` n'apparaît PAS dans OpenAPI (par design — FastAPI ne
le sérialise pas), il est couvert par tests/integration/test_websocket.py.

Si un changement est intentionnel :
    python -m tests.contract.regenerate_openapi_snapshot
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).parent / "snapshots" / "openapi_routes.json"


def _build_current_snapshot() -> dict:
    """Extrait la signature des routes depuis l'OpenAPI FastAPI."""
    # Évite spawn LibraryBrain à l'import. Le stub est RESTAURÉ en sortie : cette
    # assignation était permanente et `services` est un module singleton, donc
    # elle fuitait dans tout le reste de la session pytest — n'importe quel test
    # ultérieur appelant le vrai ensure_librarybrain recevait « True » sans le
    # savoir (le stub n'est pas restaurable par monkeypatch : regenerate_openapi
    # _snapshot.py appelle cette fonction hors pytest, sans fixture).
    import services as _svc

    _real_ensure = _svc.ensure_librarybrain
    _svc.ensure_librarybrain = lambda *_a, **_kw: True
    try:
        from api.server import app

        schema = app.openapi()
    finally:
        _svc.ensure_librarybrain = _real_ensure

    routes: dict = {}
    for path, methods in schema.get("paths", {}).items():
        routes[path] = {}
        for method, op in methods.items():
            if method == "parameters":
                continue
            params = op.get("parameters", [])
            routes[path][method.upper()] = {
                "params": sorted(p["name"] for p in params),
                "has_body": "requestBody" in op,
                "body_required": bool(
                    op.get("requestBody", {}).get("required", False)
                ),
            }
    return routes


def test_openapi_routes_match_snapshot():
    """Le snapshot fige les chemins, méthodes et signatures de paramètres."""
    assert SNAPSHOT.exists(), (
        f"Snapshot manquant: {SNAPSHOT}. Génère-le avec "
        "`python -m tests.contract.regenerate_openapi_snapshot`."
    )
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    current = _build_current_snapshot()

    if current != expected:
        added_paths = set(current) - set(expected)
        removed_paths = set(expected) - set(current)
        diffs = []
        if added_paths:
            diffs.append(f"Routes ajoutées: {sorted(added_paths)}")
        if removed_paths:
            diffs.append(
                f"Routes retirées: {sorted(removed_paths)} (BREAKING pour clients)"
            )
        for path in set(current) & set(expected):
            cur_methods = set(current[path])
            exp_methods = set(expected[path])
            if cur_methods - exp_methods:
                diffs.append(
                    f"{path}: méthodes ajoutées {sorted(cur_methods - exp_methods)}"
                )
            if exp_methods - cur_methods:
                diffs.append(
                    f"{path}: méthodes retirées {sorted(exp_methods - cur_methods)} (BREAKING)"
                )
            for m in cur_methods & exp_methods:
                if current[path][m] != expected[path][m]:
                    diffs.append(f"{path} {m}: signature changée")
                    cur_p = set(current[path][m]["params"])
                    exp_p = set(expected[path][m]["params"])
                    if exp_p - cur_p:
                        diffs.append(f"  → params retirés {sorted(exp_p - cur_p)} (BREAKING)")
                    if cur_p - exp_p:
                        diffs.append(f"  → params ajoutés {sorted(cur_p - exp_p)}")

        pytest.fail(
            "Contrat OpenAPI modifié:\n  - "
            + "\n  - ".join(diffs)
            + "\n\nSi le changement est intentionnel:\n"
            "  python -m tests.contract.regenerate_openapi_snapshot\n"
            "puis revue de la diff git."
        )
