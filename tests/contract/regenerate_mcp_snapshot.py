"""Régénère le snapshot MCP. À lancer manuellement quand un changement est
intentionnel, puis revue de la diff git avant commit.

    python -m tests.contract.regenerate_mcp_snapshot
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.contract.test_mcp_contract import _build_current_snapshot, SNAPSHOT


def main() -> None:
    snap = _build_current_snapshot()
    SNAPSHOT.write_text(
        json.dumps(snap, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"✓ Snapshot regénéré: {SNAPSHOT} ({len(snap)} outils)")


if __name__ == "__main__":
    main()
