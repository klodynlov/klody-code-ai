"""Tests du palier real_repo — fixture échoue, référence passe.

Chaque tâche a DEUX tests :
  - `test_la_fixture_echoue` : setup + validate SANS modification → DOIT échouer
  - `test_la_solution_de_reference_passe` : setup + solution + validate → DOIT passer

Et un bloc `TestEnregistrement` qui vérifie la découverte, la catégorie,
et la longueur du prompt.
"""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from bench.framework import discover_tasks
from bench.tasks.real_repo import (
    BatchAtomicDelete,
    CreateSubmoduleReadme,
    FixFromKnownIssue,
    NullByteSanitize,
    TestWithFixture,
)

# ── Solutions de référence ─────────────────────────────────────────────── #

def _solve_batch_atomic_delete(workdir: Path) -> None:
    """Ajoute delete_many atomique à RecordStore."""
    src = workdir / "store.py"
    code = src.read_text(encoding="utf-8")
    method = (
        "\n"
        "    def delete_many(self, ids):\n"
        '        """Supprime plusieurs enregistrements — atomique."""\n'
        "        for rid in ids:\n"
        "            if rid not in self._records:\n"
        "                raise KeyError(f'enregistrement {rid} inconnu')\n"
        "        for rid in ids:\n"
        "            del self._records[rid]\n"
    )
    src.write_text(code + method, encoding="utf-8")


def _solve_null_byte_sanitize(workdir: Path) -> None:
    """Ajoute normalize avec suppression des null bytes."""
    src = workdir / "text_utils.py"
    code = src.read_text(encoding="utf-8")
    func = (
        "\n\n"
        "def normalize(text):\n"
        '    """Normalise : supprime null bytes, minuscules, strip, collapse spaces."""\n'
        "    text = text.replace('\\x00', '')\n"
        "    return re.sub(r'\\s+', ' ', text.strip().lower())\n"
    )
    src.write_text(code + func, encoding="utf-8")


def _solve_test_with_fixture(workdir: Path) -> None:
    """Crée test_stats.py avec classe et fixture."""
    (workdir / "test_stats.py").write_text(
        "from stats import average_score, top_performers\n"
        "\n"
        "\n"
        "class TestAverageScore:\n"
        "    def test_avec_fixture(self, sample_records):\n"
        "        assert average_score(sample_records) == 85.0\n"
        "\n"
        "\n"
        "class TestTopPerformers:\n"
        "    def test_seuil_80(self, sample_records):\n"
        "        top = top_performers(sample_records, threshold=80)\n"
        "        assert len(top) == 2\n"
        "        assert top[0]['name'] == 'beta'\n",
        encoding="utf-8",
    )


def _solve_fix_from_known_issue(workdir: Path) -> None:
    """Remplace dict(base) par copy.deepcopy(base)."""
    src = workdir / "config_merger.py"
    code = src.read_text(encoding="utf-8")
    code = "import copy\n" + code.replace("dict(base)", "copy.deepcopy(base)")
    src.write_text(code, encoding="utf-8")


def _solve_create_submodule_readme(workdir: Path) -> None:
    """Crée utils/README.md documentant helpers et formatters."""
    (workdir / "utils" / "README.md").write_text(
        "# utils/\n\n"
        "Sous-module utilitaires.\n\n"
        "## helpers\n\n"
        "- `flatten(nested)` : aplatit une liste imbriquée\n"
        "- `chunk(items, size)` : découpe en sous-listes\n"
        "- `first(iterable, default)` : premier élément\n\n"
        "## formatters\n\n"
        "- `table(rows, headers)` : table ASCII alignée\n"
        "- `indent(text, prefix)` : indente chaque ligne\n",
        encoding="utf-8",
    )


SOLUTIONS: dict[type, callable] = {
    BatchAtomicDelete: _solve_batch_atomic_delete,
    NullByteSanitize: _solve_null_byte_sanitize,
    TestWithFixture: _solve_test_with_fixture,
    FixFromKnownIssue: _solve_fix_from_known_issue,
    CreateSubmoduleReadme: _solve_create_submodule_readme,
}

TASK_CLASSES = list(SOLUTIONS.keys())


# ── Fixtures échouent ──────────────────────────────────────────────────── #


@pytest.mark.parametrize("cls", TASK_CLASSES, ids=lambda c: c.id)
def test_la_fixture_echoue(cls):
    """Sans modification, validate DOIT rendre (False, …)."""
    task = cls()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp) / "project"
        workdir.mkdir()
        task.setup(workdir)

        ok, detail = task.validate(workdir)

    assert not ok, f"{cls.id} ne devrait pas passer sans solution — {detail}"


# ── Solutions de référence passent ─────────────────────────────────────── #


@pytest.mark.parametrize("cls", TASK_CLASSES, ids=lambda c: c.id)
def test_la_solution_de_reference_passe(cls):
    """setup + solution → validate DOIT rendre (True, …)."""
    task = cls()
    solver = SOLUTIONS[cls]
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp) / "project"
        workdir.mkdir()
        task.setup(workdir)
        solver(workdir)

        ok, detail = task.validate(workdir)

    assert ok, f"{cls.id} solution KO — {detail}"


# ── Enregistrement ─────────────────────────────────────────────────────── #


class TestEnregistrement:
    """Vérifie que les tâches sont découvertes et bien catégorisées."""

    def test_decouverte(self):
        registry = discover_tasks()
        ids_real = {tid for tid, cls in registry.items() if cls.category == "real_repo"}
        attendus = {cls.id for cls in TASK_CLASSES}
        assert attendus <= ids_real, f"manquent : {attendus - ids_real}"

    def test_categorie(self):
        for cls in TASK_CLASSES:
            assert cls.category == "real_repo", f"{cls.id} pas en real_repo"

    def test_prompt_substantiel(self):
        for cls in TASK_CLASSES:
            assert len(cls.prompt) > 100, f"{cls.id} prompt trop court"

    def test_cli_refuse_une_categorie_inconnue(self):
        proc = subprocess.run(
            [sys.executable, "-m", "bench.run", "--category", "inexistante"],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 2

    def test_dry_run_real_repo_ne_selectionne_que_real_repo(self):
        proc = subprocess.run(
            [sys.executable, "-m", "bench.run", "--category", "real_repo",
             "--dry-run"],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0
        for line in proc.stdout.strip().splitlines():
            if line.startswith("  "):
                assert "real_repo/" in line, f"tâche hors catégorie : {line}"
