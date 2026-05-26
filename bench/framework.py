"""Framework de bench : Task de base + registry + Result."""
from __future__ import annotations

import dataclasses
import importlib
import pkgutil
import time
from pathlib import Path
from typing import ClassVar, Iterable


_REGISTRY: dict[str, type["Task"]] = {}


def register(cls: type["Task"]) -> type["Task"]:
    """Décorateur d'auto-enregistrement des tâches."""
    if not cls.id:
        raise ValueError(f"Task {cls.__name__} sans id")
    if cls.id in _REGISTRY:
        raise ValueError(f"Task id dupliqué: {cls.id}")
    _REGISTRY[cls.id] = cls
    return cls


class Task:
    """Tâche de bench. Sous-classer + décorer avec @register."""

    id: ClassVar[str] = ""
    category: ClassVar[str] = ""  # easy | medium | hard
    prompt: ClassVar[str] = ""

    def setup(self, workdir: Path) -> None:
        """Crée les fixtures dans workdir avant de lancer Klody."""
        raise NotImplementedError

    def validate(self, workdir: Path) -> tuple[bool, str]:
        """Retourne (succès, message court explicatif)."""
        raise NotImplementedError


@dataclasses.dataclass
class Result:
    task_id: str
    category: str
    success: bool
    detail: str
    latency_s: float
    tokens_generated: int
    tokens_per_sec: float
    tool_calls_total: int
    tool_calls_broken: int
    iterations: int
    error: str | None = None


def discover_tasks() -> dict[str, type[Task]]:
    """Importe tous les modules sous bench.tasks.* → remplit _REGISTRY."""
    import bench.tasks as pkg

    for mod in pkgutil.walk_packages(pkg.__path__, prefix="bench.tasks."):
        importlib.import_module(mod.name)
    return dict(_REGISTRY)


def filter_tasks(
    tasks: dict[str, type[Task]],
    category: str | None = None,
    task_id: str | None = None,
) -> Iterable[type[Task]]:
    for tid, cls in tasks.items():
        if task_id and tid != task_id:
            continue
        if category and cls.category != category:
            continue
        yield cls


def stopwatch() -> tuple[float, callable]:
    """Retourne (start, lambda → elapsed_s)."""
    start = time.perf_counter()
    return start, lambda: time.perf_counter() - start
