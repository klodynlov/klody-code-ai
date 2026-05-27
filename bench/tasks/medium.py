"""10 tâches medium — multi-fichier ou refactor léger, < 2min attendus.

Stubs déclarés (id + prompt). À implémenter au fur et à mesure :
chaque stub lève NotImplementedError dans setup/validate, donc tant que
la tâche n'est pas finalisée, --category medium ne l'inclura pas par défaut
(le runner skip via try/except dans _run_one → erreur de setup).
"""
from __future__ import annotations

from pathlib import Path

from bench.framework import Task, register


class _Stub(Task):
    """Base pour stubs : signalent clairement qu'ils ne sont pas prêts."""

    def setup(self, workdir: Path) -> None:
        raise NotImplementedError(f"Task {self.id} pas encore implémentée")

    def validate(self, workdir: Path) -> tuple[bool, str]:
        return False, "stub"


@register
class ExtractFunction(_Stub):
    id = "medium/extract_function"
    category = "medium"
    prompt = "TODO: extraire un bloc dupliqué en fonction réutilisable."


@register
class LoopToComprehension(_Stub):
    id = "medium/convert_loop_to_comprehension"
    category = "medium"
    prompt = "TODO: convertir une boucle for/append en list-comprehension."


@register
class AddTypeHints(_Stub):
    id = "medium/add_type_hints"
    category = "medium"
    prompt = "TODO: ajouter des annotations de type à un module."


@register
class FixFailingTest(Task):
    """Fix bug : un test pytest échoue, l'agent doit corriger le CODE (pas le test).

    Démontre l'impact du sandbox loop (Roadmap v2 #3) : avec sandbox auto-exec,
    l'agent voit l'AssertionError dans stderr et corrige immédiatement.
    """
    id = "medium/fix_failing_test"
    category = "medium"
    prompt = (
        "Le test test_calc.py échoue. Lis le test et le module calc.py, identifie "
        "le bug dans calc.py et corrige-le. Ne modifie PAS le test — c'est la "
        "spécification. Lance pytest pour vérifier que tout passe."
    )

    def setup(self, workdir):
        # calc.py : la fonction `multiply` retourne `a + b` au lieu de `a * b`
        (workdir / "calc.py").write_text(
            "def multiply(a, b):\n"
            "    # BUG: opérateur incorrect\n"
            "    return a + b\n",
            encoding="utf-8",
        )
        (workdir / "test_calc.py").write_text(
            "from calc import multiply\n"
            "\n"
            "def test_multiply_positives():\n"
            "    assert multiply(3, 4) == 12\n"
            "\n"
            "def test_multiply_with_one():\n"
            "    assert multiply(7, 1) == 7\n"
            "\n"
            "def test_multiply_with_zero():\n"
            "    assert multiply(5, 0) == 0\n",
            encoding="utf-8",
        )

    def validate(self, workdir):
        import subprocess
        import sys

        # Le code corrigé doit faire passer les 3 tests sans modifier test_calc.py
        test_src = (workdir / "test_calc.py").read_text(encoding="utf-8")
        if "multiply(3, 4) == 12" not in test_src:
            return False, "test_calc.py a été modifié (interdit)"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(workdir / "test_calc.py"), "-q", "--no-header"],
            capture_output=True, text=True, timeout=30, cwd=workdir,
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()
            return False, f"pytest KO: {tail[-1][:80] if tail else 'no output'}"
        if "3 passed" not in proc.stdout:
            return False, f"pytest output inattendu: {proc.stdout.strip()[:80]}"
        return True, "3/3 tests passent, code corrigé"


@register
class AddCliArg(_Stub):
    id = "medium/add_cli_arg"
    category = "medium"
    prompt = "TODO: ajouter une option --verbose à un script argparse."


@register
class JsonToDataclass(_Stub):
    id = "medium/json_to_dataclass"
    category = "medium"
    prompt = "TODO: convertir un dict en dataclass typée."


@register
class SplitModule(_Stub):
    id = "medium/split_module"
    category = "medium"
    prompt = "TODO: séparer un gros fichier en 2 modules cohérents."


@register
class AddLogging(_Stub):
    id = "medium/add_logging"
    category = "medium"
    prompt = "TODO: ajouter logging structuré à un script."


@register
class MigratePrintToLogger(Task):
    """Multi-fichier : remplacer tous les print() par logger.info() dans le projet.

    Démontre l'impact du retrieval (Roadmap v2 #6) : sans find_relevant_files
    ou find_references, l'agent ne sait pas tous les endroits où print() est utilisé.
    """
    id = "medium/migrate_print_to_logger"
    category = "medium"
    prompt = (
        "Dans ce projet, remplace TOUS les appels `print(...)` par `logger.info(...)`. "
        "Ajoute `import logging` et `logger = logging.getLogger(__name__)` en tête des "
        "fichiers concernés s'ils manquent. N'ajoute pas de print, n'en supprime aucun "
        "par erreur. Couvre tous les fichiers .py du projet."
    )

    def setup(self, workdir):
        # 3 fichiers Python avec des print éparpillés
        (workdir / "app.py").write_text(
            "def greet(name):\n"
            "    print(f'Hello, {name}')\n"
            "    return name\n",
            encoding="utf-8",
        )
        (workdir / "utils.py").write_text(
            "def divide(a, b):\n"
            "    if b == 0:\n"
            "        print('Erreur: division par zéro')\n"
            "        return None\n"
            "    print(f'{a} / {b}')\n"
            "    return a / b\n",
            encoding="utf-8",
        )
        (workdir / "main.py").write_text(
            "from app import greet\n"
            "from utils import divide\n"
            "\n"
            "print('Démarrage')\n"
            "greet('Klody')\n"
            "divide(10, 2)\n"
            "print('Fin')\n",
            encoding="utf-8",
        )

    def validate(self, workdir):
        import re

        files = ["app.py", "utils.py", "main.py"]
        for name in files:
            src = (workdir / name).read_text(encoding="utf-8")
            # Plus de print(
            if re.search(r"\bprint\s*\(", src):
                return False, f"{name}: print() encore présent"
            # logger.info présent dans les fichiers qui avaient des print
            if "logger.info" not in src:
                return False, f"{name}: pas de logger.info"
            # imports logging présents
            if "import logging" not in src:
                return False, f"{name}: import logging manquant"
            if "getLogger" not in src:
                return False, f"{name}: logger = logging.getLogger(...) manquant"
        return True, "3/3 fichiers migrés correctement"


@register
class AddErrorHandling(_Stub):
    id = "medium/add_error_handling"
    category = "medium"
    prompt = "TODO: ajouter try/except contextualisés à un script."
