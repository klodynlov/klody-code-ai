"""Tout script que launchd lance sous `/usr/bin/python3` doit TOURNER sous lui.

Vécu en septembre 2026 : `scripts/veille_nightly.py` importait `datetime.UTC`
(Python 3.11+) alors que son agent le lance sous `/usr/bin/python3` — 3.9, celui
des outils Xcode. ImportError au CHARGEMENT : la veille censée dénoncer un
nightly muet ne pouvait ni interroger, ni notifier. La suite la testait sous le
3.11 du venv, donc au vert. Et c'est le linter qui avait réclamé `datetime.UTC`
(pyupgrade, UP017), faute de savoir que ce fichier vise 3.9.

Trois verrous, du plus portable au plus fidèle :

1. syntaxe acceptée par l'analyseur en mode 3.9 (`ast.parse(feature_version)`) ;
2. aucun nom de bibliothèque standard apparu après 3.9 parmi ceux qui ont déjà
   servi ou qu'un linter réclame (liste fermée, pas exhaustive) ;
3. le module se CHARGE sous le vrai `/usr/bin/python3` quand il existe. Sur le
   Mac c'est LE juge (3.9) ; en CI Linux c'est un 3.12 et il ne juge que
   l'import — d'où les deux verrous précédents, qui tournent partout.

Plus : ruff doit connaître la vraie cible de chacun de ces scripts
(`per-file-target-version`), sinon il réclamera de nouveau du 3.11.
"""

from __future__ import annotations

import ast
import fnmatch
import plistlib
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
AGENTS = REPO / "launchagents"
PYTHON_SYSTEME = "/usr/bin/python3"
# Préfixe figé dans les plists versionnés (réécrit à l'installation).
PREFIXE_PLIST = "/Users/klodynlov/Projets/klody-code-ai/"

# (module, nom) apparus après 3.9. Liste FERMÉE : ceux qui ont mordu ou que
# pyupgrade propose en cible py311.
NOMS_POST_39 = {
    ("datetime", "UTC"),      # 3.11 — l'incident
    ("typing", "Self"),       # 3.11
    ("typing", "TypeAlias"),  # 3.10
    ("typing", "ParamSpec"),  # 3.10
    ("enum", "StrEnum"),      # 3.11
    ("itertools", "batched"),  # 3.12
    ("itertools", "pairwise"),  # 3.10
}
MODULES_POST_39 = {"tomllib"}  # 3.11


def _scripts_systeme() -> list[Path]:
    trouves = []
    for plist in sorted(AGENTS.glob("*.plist")):
        args = plistlib.loads(plist.read_bytes()).get("ProgramArguments", [])
        if len(args) >= 2 and args[0] == PYTHON_SYSTEME:
            assert args[1].startswith(PREFIXE_PLIST), f"{plist.name} : chemin inattendu {args[1]}"
            trouves.append(REPO / args[1].removeprefix(PREFIXE_PLIST))
    return trouves


SCRIPTS = _scripts_systeme()


@pytest.mark.parametrize("plist", sorted(AGENTS.glob("*.plist")), ids=lambda p: p.stem)
def test_chaque_plist_est_du_xml_strict(plist):
    """launchd et plutil tolèrent `--` dans un commentaire XML ; plistlib non.
    `com.klody.veille-qwen.plist` en contenait un — invisible jusqu'à ce qu'un
    test veuille lire ses `ProgramArguments`."""
    plistlib.loads(plist.read_bytes())


def test_la_liste_n_est_pas_vide():
    """Sans ce garde, un changement de format des plists viderait la liste et
    tous les tests ci-dessous passeraient à vide."""
    noms = {p.name for p in SCRIPTS}
    assert {"veille_nightly.py", "veille_qwen.py", "bench_dispatch.py"} <= noms


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
class TestCompatible39:
    def test_existe(self, script):
        assert script.is_file()

    def test_syntaxe_39(self, script):
        ast.parse(script.read_text(encoding="utf-8"), feature_version=(3, 9))

    def test_aucun_nom_post_39(self, script):
        arbre = ast.parse(script.read_text(encoding="utf-8"))
        fautes = []
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.ImportFrom) and noeud.module:
                if noeud.module in MODULES_POST_39:
                    fautes.append(f"from {noeud.module} import …")
                fautes += [
                    f"from {noeud.module} import {a.name}"
                    for a in noeud.names
                    if (noeud.module, a.name) in NOMS_POST_39
                ]
            elif isinstance(noeud, ast.Import):
                fautes += [f"import {a.name}" for a in noeud.names if a.name in MODULES_POST_39]
            elif (
                isinstance(noeud, ast.Attribute)
                and isinstance(noeud.value, ast.Name)
                and (noeud.value.id, noeud.attr) in NOMS_POST_39
            ):
                fautes.append(f"{noeud.value.id}.{noeud.attr}")
        assert not fautes, f"{script.name} utilise du post-3.9 : {fautes}"

    @pytest.mark.skipif(not Path(PYTHON_SYSTEME).exists(), reason="/usr/bin/python3 absent")
    def test_se_charge_sous_le_vrai_interpreteur(self, script):
        """Charge le module sans exécuter `main` (garde `__name__`)."""
        code = (
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('m', {str(script)!r})\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
        )
        r = subprocess.run(
            [PYTHON_SYSTEME, "-c", code],
            capture_output=True, text=True, timeout=30,
            env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
        )
        assert r.returncode == 0, r.stderr


def test_ruff_connait_la_vraie_cible():
    """Sans cible py39 par fichier, pyupgrade réclame `datetime.UTC` — c'est
    ainsi que l'incident est entré."""
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    cibles = config["tool"]["ruff"].get("per-file-target-version", {})
    for script in SCRIPTS:
        relatif = script.relative_to(REPO).as_posix()
        vues = [v for motif, v in cibles.items() if fnmatch.fnmatch(relatif, motif)]
        assert vues == ["py39"], f"{relatif} : per-file-target-version = {vues}, attendu py39"
