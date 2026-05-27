"""Test de contrat MCP — fige les signatures des 8 outils Klody.

Tout changement de nom, retrait de paramètre ou ajout d'un required casse le
contrat avec les clients MCP (Cursor, Claude Desktop, etc.). Le test compare
le state actuel à un snapshot versionné.

Si un changement EST intentionnel :
    python -m tests.contract.regenerate_mcp_snapshot
puis revue de la diff git avant commit.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).parent / "snapshots" / "mcp_klody.json"


def _build_current_snapshot() -> dict:
    """Introspecte le serveur MCP et retourne le snapshot des outils."""
    from klody_mcp.klody_server import mcp

    snap: dict[str, dict] = {}
    tools = asyncio.run(mcp.list_tools())
    for ft in tools:
        t = ft.to_mcp_tool()
        schema = t.inputSchema or {}
        props = schema.get("properties", {})
        snap[t.name] = {
            "description_first_line": (t.description or "").split("\n")[0].strip(),
            "required": sorted(schema.get("required", [])),
            "parameters": {
                name: {"type": props[name].get("type", "any")}
                for name in sorted(props.keys())
            },
        }
    return snap


def test_mcp_has_eight_tools():
    """La roadmap v2 #9 fige 8 outils MCP. Bloque ajout/retrait silencieux."""
    current = _build_current_snapshot()
    assert len(current) == 8, (
        f"Attendu 8 outils MCP, observé {len(current)}: {sorted(current.keys())}. "
        f"Si le changement est intentionnel, met à jour ce test ET le snapshot."
    )


def test_mcp_contract_matches_snapshot():
    """Compare le serveur MCP courant au snapshot versionné."""
    assert SNAPSHOT.exists(), (
        f"Snapshot manquant: {SNAPSHOT}. Génère-le avec "
        "`python -m tests.contract.regenerate_mcp_snapshot`."
    )
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    current = _build_current_snapshot()

    # Diff manuel pour message d'erreur lisible
    if current != expected:
        added = set(current) - set(expected)
        removed = set(expected) - set(current)
        diffs = []
        if added:
            diffs.append(f"Outils ajoutés: {sorted(added)}")
        if removed:
            diffs.append(f"Outils retirés: {sorted(removed)} (BREAKING)")
        for name in set(current) & set(expected):
            if current[name] != expected[name]:
                diffs.append(f"Signature changée pour '{name}'")
                exp_req = set(expected[name]["required"])
                cur_req = set(current[name]["required"])
                if cur_req - exp_req:
                    diffs.append(
                        f"  → nouveaux required {sorted(cur_req - exp_req)} (BREAKING pour clients existants)"
                    )
                if exp_req - cur_req:
                    diffs.append(
                        f"  → required retirés {sorted(exp_req - cur_req)}"
                    )
                cur_params = set(current[name]["parameters"])
                exp_params = set(expected[name]["parameters"])
                if exp_params - cur_params:
                    diffs.append(
                        f"  → paramètres retirés {sorted(exp_params - cur_params)} (BREAKING)"
                    )

        pytest.fail(
            "Contrat MCP modifié:\n  - "
            + "\n  - ".join(diffs)
            + "\n\nSi le changement est intentionnel:\n"
            "  python -m tests.contract.regenerate_mcp_snapshot\n"
            "puis revue de la diff git."
        )
