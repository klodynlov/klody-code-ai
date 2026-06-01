"""Sandbox multi-racines : auto-check et run_in_sandbox conscients de la racine.

Vérifie que l'orchestrator exécute l'auto-check sandbox dans la BONNE racine
autorisée (pas seulement PROJECT_ROOT) et refuse les workdir hors racines.
Aucune exécution réelle de venv : on teste la résolution (_resolve_sandbox_target,
_sandbox_for, dispatch run_in_sandbox).
"""

from pathlib import Path

import pytest
from agent.orchestrator import Orchestrator
from tools.file_manager import FileManager


@pytest.fixture
def orch_two_roots(tmp_path):
    """Orchestrator minimal avec 2 racines autorisées (A primaire, B secondaire)."""
    a = tmp_path / "projA"
    b = tmp_path / "projB"
    a.mkdir()
    b.mkdir()
    o = Orchestrator.__new__(Orchestrator)
    o._sandbox_cache = {}
    o._sandbox_timeout = 20
    o._sandbox_auto_exec = True
    o.file_manager = FileManager(root=a, allowed_roots=[a, b])
    return o, a, b


class TestResolveSandboxTarget:
    def test_fichier_dans_racine_secondaire(self, orch_two_roots):
        o, a, b = orch_two_roots
        (b / "mod.py").write_text("x = 1\n")
        target = o._resolve_sandbox_target(str(b / "mod.py"))
        assert target is not None
        _sandbox, _rel_cmd, root = target
        assert root == b.resolve()

    def test_chemin_relatif_resolu_sur_projet(self, orch_two_roots):
        o, a, b = orch_two_roots
        (a / "foo.py").write_text("y = 2\n")
        target = o._resolve_sandbox_target("foo.py")
        assert target is not None
        _sandbox, _rel_cmd, root = target
        assert root == a.resolve()

    def test_fichier_hors_racines_retourne_none(self, orch_two_roots, tmp_path):
        o, a, b = orch_two_roots
        dehors = tmp_path / "dehors"
        dehors.mkdir()
        (dehors / "evil.py").write_text("z = 3\n")
        assert o._resolve_sandbox_target(str(dehors / "evil.py")) is None

    def test_fichier_non_executable_retourne_none(self, orch_two_roots):
        o, a, b = orch_two_roots
        # auto_command_for ne renvoie rien pour un .txt → pas de check
        (a / "notes.txt").write_text("hello")
        assert o._resolve_sandbox_target("notes.txt") is None

    def test_chemin_vide_retourne_none(self, orch_two_roots):
        o, a, b = orch_two_roots
        assert o._resolve_sandbox_target("") is None


class TestSandboxCache:
    def test_racines_distinctes_runners_distincts(self, orch_two_roots):
        o, a, b = orch_two_roots
        assert o._sandbox_for(a) is not o._sandbox_for(b)

    def test_meme_racine_meme_runner(self, orch_two_roots):
        o, a, b = orch_two_roots
        assert o._sandbox_for(a) is o._sandbox_for(a)

    def test_sandbox_property_pointe_sur_projet(self, orch_two_roots):
        o, a, b = orch_two_roots
        assert o.sandbox is o._sandbox_for(a)


class TestRunInSandboxWorkdir:
    def test_workdir_hors_racines_refuse(self, orch_two_roots, tmp_path):
        o, a, b = orch_two_roots
        dehors = tmp_path / "ailleurs"
        dehors.mkdir()
        out = o._execute_tool(
            "run_in_sandbox", {"command": "echo hi", "workdir": str(dehors)}
        )
        assert out.startswith("ERREUR SÉCURITÉ")
        assert "hors des racines" in out
