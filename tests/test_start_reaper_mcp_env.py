"""Le lanceur du serveur MCP REAPER pose bien les racines de samples.

Aucun agent launchd ne lance ce serveur : `scripts/start-reaper-mcp.sh` est le
seul point qui couvre tous les modes de démarrage, donc le seul endroit où
poser `KLODY_SAMPLES_DIR`. Un réglage que rien ne vérifie finit par mentir —
ces tests EXÉCUTENT le bloc d'affectation extrait du script, ils ne lisent pas
sa prose.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "start-reaper-mcp.sh"


def _bloc_affectation() -> str:
    """Extrait du script les deux lignes qui posent la variable.

    On ne peut pas sourcer le script entier : il finit par un `exec` du serveur.
    On extrait donc exactement l'affectation et son export, et on les exécute —
    si quelqu'un les renomme ou les supprime, l'extraction rend vide et les
    tests rougissent au lieu de passer sur du vide.
    """
    texte = SCRIPT.read_text()
    lignes = [
        ligne for ligne in texte.splitlines()
        if re.match(r'^\s*(KLODY_SAMPLES_DIR=|export KLODY_SAMPLES_DIR)', ligne)
    ]
    assert lignes, "le script ne pose plus KLODY_SAMPLES_DIR"
    return "\n".join(lignes)


def _resoudre(valeur_preexistante: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "KLODY_SAMPLES_DIR"}
    if valeur_preexistante is not None:
        env["KLODY_SAMPLES_DIR"] = valeur_preexistante
    p = subprocess.run(
        ["/bin/bash", "-c", _bloc_affectation() + '\necho "$KLODY_SAMPLES_DIR"'],
        capture_output=True, text=True, env=env, check=True,
    )
    return p.stdout.strip()


class TestRacinesDeSamples:
    def test_le_defaut_contient_les_deux_racines(self):
        valeur = _resoudre(None)
        racines = valeur.split(os.pathsep)
        assert len(racines) == 2, valeur
        assert racines[0].endswith("/Desktop/SAMPLES")
        assert racines[1].endswith("/local-suno/samples")

    def test_le_defaut_est_relatif_a_HOME(self):
        """Pas de chemin en dur : le script doit rester lisible par quelqu'un
        d'autre que son auteur."""
        assert "/Users/" not in _bloc_affectation()
        assert "$HOME" in _bloc_affectation()

    @pytest.mark.parametrize("explicite", ["/tmp/mes-samples", "/a:/b:/c"])
    def test_une_valeur_explicite_gagne(self, explicite):
        assert _resoudre(explicite) == explicite

    def test_la_variable_est_exportee(self):
        """Sans `export`, le serveur lancé par `exec` ne la verrait pas."""
        p = subprocess.run(
            ["/bin/bash", "-c",
             _bloc_affectation() + '\nbash -c \'echo "$KLODY_SAMPLES_DIR"\''],
            capture_output=True, text=True,
            env={k: v for k, v in os.environ.items() if k != "KLODY_SAMPLES_DIR"},
            check=True,
        )
        assert p.stdout.strip(), "la variable n'est pas exportée au sous-processus"
