"""5 tâches hard — multi-étapes, dépendances, débogage. Stubs."""
from __future__ import annotations

from pathlib import Path

from bench.framework import Task, register


class _Stub(Task):
    def setup(self, workdir: Path) -> None:
        raise NotImplementedError(f"Task {self.id} pas encore implémentée")

    def validate(self, workdir: Path) -> tuple[bool, str]:
        return False, "stub"


@register
class FixAsyncBug(_Stub):
    id = "hard/fix_async_bug"
    category = "hard"
    prompt = "TODO: corriger une race condition subtile dans du code async."


@register
class OptimizeNSquared(_Stub):
    id = "hard/optimize_n_squared"
    category = "hard"
    prompt = "TODO: détecter algo O(n²) et proposer O(n log n)."


@register
class MigrateSyncToAsync(_Stub):
    id = "hard/migrate_sync_to_async"
    category = "hard"
    prompt = "TODO: convertir un module sync en async (httpx + asyncio)."


@register
class ApiEndpointFull(_Stub):
    id = "hard/api_endpoint_full"
    category = "hard"
    prompt = "TODO: ajouter un endpoint FastAPI complet (route + Pydantic + test)."


@register
class DebugTestSuite(_Stub):
    id = "hard/debug_test_suite"
    category = "hard"
    prompt = "TODO: 3 tests échouent pour 3 raisons différentes. Tout fixer."
