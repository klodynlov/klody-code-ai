"""Régénère le snapshot OpenAPI. À lancer manuellement quand un changement
est intentionnel, puis revue de la diff git avant commit.

    python -m tests.contract.regenerate_openapi_snapshot
"""
from __future__ import annotations

import json

from tests.contract.test_openapi_contract import _build_current_snapshot, SNAPSHOT


def main() -> None:
    snap = _build_current_snapshot()
    SNAPSHOT.write_text(
        json.dumps(snap, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    paths = len(snap)
    methods = sum(len(v) for v in snap.values())
    print(f"✓ Snapshot regénéré: {SNAPSHOT} ({paths} routes, {methods} méthodes)")


if __name__ == "__main__":
    main()
