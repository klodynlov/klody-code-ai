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
class DebugTestSuite(Task):
    """Hard task : 3 tests échouent pour 3 raisons distinctes.

    Démontre l'impact du Best-of-N (Roadmap v2 #7) — sur une tâche hard,
    la 1ère décision (par où commencer ? quelle stratégie ?) est critique.

    Bugs cachés dans `calculator.py` :
      1. `divide(a, b)` — pas de vérif b == 0 → ZeroDivisionError
      2. `square(x)` — retourne x + 2 au lieu de x * x
      3. `is_positive(x)` — retourne True pour x == 0 (devrait être strict)
    """
    id = "hard/debug_test_suite"
    category = "hard"
    prompt = (
        "3 tests dans test_calculator.py échouent pour 3 raisons différentes. "
        "Lis le code, identifie chaque bug, corrige calculator.py SANS toucher "
        "aux tests (qui constituent la spec). Lance pytest à la fin pour confirmer "
        "que les 3 tests passent."
    )

    def setup(self, workdir):
        (workdir / "calculator.py").write_text(
            "def divide(a, b):\n"
            "    # BUG 1 : aucune vérif sur b == 0\n"
            "    return a / b\n"
            "\n"
            "def square(x):\n"
            "    # BUG 2 : opérateur incorrect\n"
            "    return x + 2\n"
            "\n"
            "def is_positive(x):\n"
            "    # BUG 3 : compare à 0 inclus au lieu de strict\n"
            "    return x >= 0\n",
            encoding="utf-8",
        )
        (workdir / "test_calculator.py").write_text(
            "import pytest\n"
            "from calculator import divide, square, is_positive\n"
            "\n"
            "def test_divide_normal():\n"
            "    assert divide(10, 2) == 5\n"
            "\n"
            "def test_divide_by_zero_raises():\n"
            "    with pytest.raises((ZeroDivisionError, ValueError)):\n"
            "        divide(5, 0)\n"
            "\n"
            "def test_square_positives():\n"
            "    assert square(4) == 16\n"
            "    assert square(7) == 49\n"
            "\n"
            "def test_is_positive_zero_is_false():\n"
            "    assert is_positive(0) is False\n"
            "    assert is_positive(-1) is False\n"
            "    assert is_positive(3) is True\n",
            encoding="utf-8",
        )

    def validate(self, workdir):
        import subprocess
        import sys

        # Tests interdits de modif (test_calculator.py garde sa spec)
        test_src = (workdir / "test_calculator.py").read_text(encoding="utf-8")
        if "square(4) == 16" not in test_src or "is_positive(0) is False" not in test_src:
            return False, "test_calculator.py a été modifié (interdit)"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(workdir / "test_calculator.py"),
             "-q", "--no-header"],
            capture_output=True, text=True, timeout=30, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:80] if tail else 'no output'}"
        if "4 passed" not in proc.stdout:
            return False, f"pytest output inattendu: {proc.stdout.strip()[:80]}"
        return True, "4/4 tests passent (3 bugs fixés)"
