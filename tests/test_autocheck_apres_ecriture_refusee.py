"""L'auto-check sandbox ne part QUE si `write_file` a réellement changé le disque.

Vécu au banc d'injection du 2026-11-23 (klodynlov-profile,
`docs/bench/localcode/injection/`) : Klody lançait son auto-check pytest après
un `write_file` sur un `.py` y compris quand l'humain avait REFUSÉ l'écriture
(3 essais, 7 occurrences). Le refus renvoie « Action refusée par l'utilisateur
(non validée). », qui ne commence pas par `ERREUR` : la seule garde existante
le laissait passer. Sur un dépôt piégé (`tests/conftest.py`), ce chemin
exécutait le conftest sans AUCUNE approbation.

On teste `_execute_and_display` avec un `_execute_tool` bouchonné (chemin réel
de l'API : `execute_with_events` remplace `_execute_tool` et rend le refus) et
un espion à la place de `_auto_sandbox_check` — aucun venv, aucun pytest lancé.
"""

from __future__ import annotations

import pytest
from agent.orchestrator import Orchestrator
from tools.file_manager import FileManager

REFUS_WS = "Action refusée par l'utilisateur (non validée)."
REFUS_CANAL = "Action refusée : le canal imessage ne peut pas faire valider une action."


@pytest.fixture
def orch(tmp_path):
    o = Orchestrator.__new__(Orchestrator)
    o._sandbox_cache = {}
    o._sandbox_timeout = 20
    o._sandbox_auto_exec = True
    o.file_manager = FileManager(root=tmp_path, allowed_roots=[tmp_path])
    o._afficher_sans_casser = lambda *a, **k: None
    o._auto_preview_check = lambda _p: ""
    o.checks = []

    def espion(path: str) -> str:
        o.checks.append(path)
        return "[sandbox auto-check]\nespion"

    o._auto_sandbox_check = espion
    return o


def _outil_refuse(message: str):
    """`_execute_tool` qui refuse comme le fait la garde humaine : rien n'est écrit."""
    return lambda _name, _args: message


def _outil_reel(orch):
    """`_execute_tool` qui écrit vraiment, via le FileManager."""
    return lambda _name, args: orch.file_manager.write_file(args["path"], args["content"])


@pytest.mark.parametrize("refus", [REFUS_WS, REFUS_CANAL])
def test_ecriture_refusee_ne_lance_pas_l_auto_check(orch, tmp_path, refus):
    (tmp_path / "tarifs.py").write_text("x = 1\n")
    orch._execute_tool = _outil_refuse(refus)
    res = orch._execute_and_display("write_file", {"path": "tarifs.py", "content": "x = 2\n"})
    assert res == refus
    assert orch.checks == []
    assert (tmp_path / "tarifs.py").read_text() == "x = 1\n"


def test_creation_refusee_ne_lance_pas_l_auto_check(orch, tmp_path):
    orch._execute_tool = _outil_refuse(REFUS_WS)
    orch._execute_and_display("write_file", {"path": "nouveau.py", "content": "y = 1\n"})
    assert orch.checks == []
    assert not (tmp_path / "nouveau.py").exists()


def test_ecriture_identique_ne_lance_pas_l_auto_check(orch, tmp_path):
    (tmp_path / "tarifs.py").write_text("x = 1\n")
    orch._execute_tool = _outil_reel(orch)
    orch._execute_and_display("write_file", {"path": "tarifs.py", "content": "x = 1\n"})
    assert orch.checks == []


def test_ecriture_en_erreur_ne_lance_pas_l_auto_check(orch, tmp_path):
    orch._execute_tool = _outil_refuse("ERREUR: chemin hors racines")
    orch._execute_and_display("write_file", {"path": "x.py", "content": "z = 1\n"})
    assert orch.checks == []


def test_modification_reelle_lance_l_auto_check(orch, tmp_path):
    """Contre-épreuve : sans elle, une garde qui ne lance JAMAIS le check passerait."""
    (tmp_path / "tarifs.py").write_text("x = 1\n")
    orch._execute_tool = _outil_reel(orch)
    res = orch._execute_and_display("write_file", {"path": "tarifs.py", "content": "x = 2\n"})
    assert orch.checks == ["tarifs.py"]
    assert "[sandbox auto-check]" in res


def test_creation_reelle_lance_l_auto_check(orch, tmp_path):
    orch._execute_tool = _outil_reel(orch)
    orch._execute_and_display("write_file", {"path": "pkg/nouveau.py", "content": "y = 1\n"})
    assert orch.checks == ["pkg/nouveau.py"]


def test_chemin_absolu_modifie_lance_l_auto_check(orch, tmp_path):
    cible = tmp_path / "abs.py"
    cible.write_text("a = 1\n")
    orch._execute_tool = _outil_reel(orch)
    orch._execute_and_display("write_file", {"path": str(cible), "content": "a = 2\n"})
    assert orch.checks == [str(cible)]


def test_auto_exec_coupe_reste_respecte(orch, tmp_path):
    orch._sandbox_auto_exec = False
    orch._execute_tool = _outil_reel(orch)
    orch._execute_and_display("write_file", {"path": "c.py", "content": "c = 1\n"})
    assert orch.checks == []
