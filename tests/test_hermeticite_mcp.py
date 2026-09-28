"""Un test ne découvre AUCUN serveur MCP vivant — en CI comme sur le Mac.

Ce fichier existe pour que `conftest._pas_de_decouverte_mcp` puisse ROUGIR
PARTOUT. Le garde neutralise une valeur venue du `.env` du développeur
(`KLODY_MCP_SERVERS`, quinze serveurs le 2026-09-27, hérités même depuis un
worktree puisque `load_dotenv()` remonte l'arborescence). En CI Linux, aucun
`.env` ne déclare de serveur : un test en process y serait vert garde RETIRÉ,
exactement le « garde-fou incapable d'échouer » que CLAUDE.md décrit comme le
mode de défaillance dominant du dépôt.

D'où le sous-processus : il FABRIQUE la condition (la variable posée avant
l'import de `config`) au lieu de l'attendre de la machine. Mesuré le 2026-09-27,
garde retiré : sur le Mac, les deux tests rougissent ; avec
`KLODY_MCP_SERVERS=` (la CI simulée), seul le sous-processus rougit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import config

_SONDE = "KLODY_TEST_SONDE_MCP"
_SERVEURS_DECLARES = {"vivant": "http://127.0.0.1:9/mcp"}  # port discard : jamais appelé
# Lu à l'IMPORT, donc avant toute fixture : ce que `config` a tiré de
# l'environnement, garde non encore posé.
_MCP_AVANT_GARDE = dict(config.MCP_SERVERS)


def test_aucun_serveur_mcp_pendant_un_test():
    if os.environ.get(_SONDE):
        # Prémisse du test ci-dessous : l'environnement a bien atteint `config`.
        # Sans elle, le verdict serait vert pour une raison étrangère au garde.
        assert _MCP_AVANT_GARDE == _SERVEURS_DECLARES
    assert config.MCP_SERVERS == {}


def test_le_garde_tient_quand_l_environnement_declare_des_serveurs():
    """Rejoue le test précédent dans un process où `KLODY_MCP_SERVERS` est posé.

    On exige « 1 passed » : un test sauté ou non collecté ne prouverait rien.
    Coût mesuré : ~0,5 s (démarrage de pytest et import du conftest).
    """
    racine = Path(__file__).resolve().parent.parent
    noeud = f"{Path(__file__).resolve().relative_to(racine)}::test_aucun_serveur_mcp_pendant_un_test"
    # Le sous-pytest doit démarrer comme une session NEUVE, pas hériter de l'état
    # de celle-ci — même filtre que test_hermeticite_etat_persistant.py. Vu le
    # 2026-09-27, CI de main rouge : le `KLODY_DATA_DIR` hérité est le dossier
    # jetable posé par la fixture du test courant ; `tests/garde_etat.py` le lit à
    # l'import comme un VRAI dossier de développeur, `rediriger()` sort tôt sur le
    # `_KLODY_TESTS_DATA_DIR` hérité, et conftest refuse de démarrer.
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("KLODY_DATA_DIR", "SEMANTIC_MEMORY_DB", "_KLODY_TESTS_DATA_DIR")
    }
    env |= {"KLODY_MCP_SERVERS": json.dumps(_SERVEURS_DECLARES), _SONDE: "1"}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", noeud, "-q", "-p", "no:cacheprovider"],
        cwd=racine, env=env, capture_output=True, text=True, timeout=120,
    )
    sortie = r.stdout + r.stderr
    assert r.returncode == 0, f"le garde MCP ne tient pas :\n{sortie[-3000:]}"
    assert "1 passed" in sortie, f"le test rejoué n'a pas été jugé :\n{sortie[-3000:]}"
