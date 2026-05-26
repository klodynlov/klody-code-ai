"""5 tâches easy — édits localisés, < 30s attendus."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bench.framework import Task, register


@register
class RenameVar(Task):
    id = "easy/rename_var"
    category = "easy"
    prompt = (
        "Dans le fichier app.py, renomme la variable `usr` en `user` partout "
        "(déclaration et utilisations). Aucune autre modification."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "app.py").write_text(
            "usr = 'alice'\n"
            "print(f'Hello {usr}')\n"
            "def greet(usr):\n"
            "    return f'Hi {usr}'\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        src = (workdir / "app.py").read_text(encoding="utf-8")
        if "usr" in src:
            return False, "occurrence de `usr` restante"
        if src.count("user") < 4:
            return False, f"attendu ≥4 occurrences de `user`, vu {src.count('user')}"
        return True, "rename complet"


@register
class AddDocstring(Task):
    id = "easy/add_docstring"
    category = "easy"
    prompt = (
        "Dans utils.py, ajoute une docstring Google-style à la fonction `compute_area` "
        "qui décrit ses paramètres et la valeur de retour. Ne change pas le code."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "utils.py").write_text(
            "def compute_area(width, height):\n"
            "    return width * height\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        src = (workdir / "utils.py").read_text(encoding="utf-8")
        if '"""' not in src and "'''" not in src:
            return False, "pas de docstring"
        if "width" not in src.lower() or "height" not in src.lower():
            return False, "params non documentés"
        # Vérifier que le code source est intact
        if "return width * height" not in src:
            return False, "code modifié"
        return True, "docstring ajoutée"


@register
class FixTypo(Task):
    id = "easy/fix_typo"
    category = "easy"
    prompt = (
        "Dans config.py, corrige la typo dans le commentaire : "
        "« Configuraton » → « Configuration »."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "config.py").write_text(
            "# Configuraton de l'application\n"
            "DEBUG = False\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        src = (workdir / "config.py").read_text(encoding="utf-8")
        if "Configuraton" in src:
            return False, "typo non corrigée"
        if "Configuration" not in src:
            return False, "remplacement absent"
        if "DEBUG = False" not in src:
            return False, "code modifié"
        return True, "typo corrigée"


@register
class AddImport(Task):
    id = "easy/add_import"
    category = "easy"
    prompt = (
        "Dans `script.py`, le code utilise `os.getenv` mais l'import `os` est manquant. "
        "Ajoute l'import nécessaire en haut du fichier."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "script.py").write_text(
            "PORT = int(os.getenv('PORT', '8000'))\n"
            "print(PORT)\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        src = (workdir / "script.py").read_text(encoding="utf-8")
        if "import os" not in src:
            return False, "import os manquant"
        # Vérifier que le fichier s'exécute sans NameError
        proc = subprocess.run(
            [sys.executable, str(workdir / "script.py")],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return False, f"exécution KO: {proc.stderr.strip()[:80]}"
        return True, "import ajouté + exécution OK"


@register
class AddSimpleTest(Task):
    id = "easy/add_simple_test"
    category = "easy"
    prompt = (
        "Dans le fichier test_math.py (à créer), écris un test pytest qui vérifie "
        "que la fonction `add(a, b)` du module math_utils.py renvoie bien la somme. "
        "Couvre au moins 2 cas : entiers positifs et un cas avec zéro."
    )

    def setup(self, workdir: Path) -> None:
        (workdir / "math_utils.py").write_text(
            "def add(a, b):\n"
            "    return a + b\n",
            encoding="utf-8",
        )

    def validate(self, workdir: Path) -> tuple[bool, str]:
        test_file = workdir / "test_math.py"
        if not test_file.exists():
            return False, "test_math.py non créé"
        src = test_file.read_text(encoding="utf-8")
        if "def test_" not in src:
            return False, "aucune fonction test_*"
        # Lance pytest réellement
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-q", "--no-header"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workdir,
        )
        if proc.returncode != 0:
            return False, f"pytest KO: {proc.stdout.strip().splitlines()[-1][:80]}"
        # Compter les tests collectés
        if " passed" not in proc.stdout:
            return False, "aucun test passé"
        return True, proc.stdout.strip().splitlines()[-1][:60]
