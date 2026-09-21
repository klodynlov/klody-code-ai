"""Tests pour klody_mcp/laser_server.py — pont vers l'organe KlodyLaser.

Le pont ne doit ni importer KlodyLaser ni lancer quoi que ce soit dans les
tests : `os.execv` est bouchonné, on vérifie seulement QUOI il exécuterait.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from klody_mcp import laser_server as ls


def _organe(tmp_path: Path, executable: bool = True) -> Path:
    racine = tmp_path / "KlodyLaser"
    script = racine / "scripts" / "start.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 0\n")
    if executable:
        script.chmod(0o755)
    return racine


class TestDossierOrgane:
    def test_defaut_sous_projets(self):
        assert ls.dossier_organe({}) == Path.home() / "Projets" / "KlodyLaser"

    def test_variable_env_prioritaire(self, tmp_path: Path):
        assert ls.dossier_organe({"KLODYLASER_DIR": str(tmp_path)}) == tmp_path

    def test_tilde_developpe(self):
        assert ls.dossier_organe({"KLODYLASER_DIR": "~/x"}) == Path.home() / "x"


class TestLanceur:
    def test_trouve_start_sh(self, tmp_path: Path):
        racine = _organe(tmp_path)
        assert ls.lanceur(racine) == racine / "scripts" / "start.sh"

    def test_absent_leve(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="KlodyLaser absent"):
            ls.lanceur(tmp_path / "nulle-part")

    def test_non_executable_leve(self, tmp_path: Path):
        racine = _organe(tmp_path, executable=False)
        with pytest.raises(FileNotFoundError, match="non exécutable"):
            ls.lanceur(racine)


class TestMain:
    def test_exec_le_lanceur_de_l_organe(self, tmp_path: Path, monkeypatch, capsys):
        racine = _organe(tmp_path)
        monkeypatch.setenv("KLODYLASER_DIR", str(racine))
        appels: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(os, "execv", lambda p, a: appels.append((p, a)))
        cwd = os.getcwd()
        try:
            ls.main(["--port", "8798"])
        finally:
            os.chdir(cwd)
        script = str(racine / "scripts" / "start.sh")
        assert appels == [(script, [script, "--port", "8798"])]
        out = capsys.readouterr().out
        assert "http://127.0.0.1:8798/mcp" in out
        assert str(racine) in out

    def test_organe_absent_sort_en_1_sans_exec(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setenv("KLODYLASER_DIR", str(tmp_path / "rien"))
        monkeypatch.setattr(os, "execv", lambda *_: pytest.fail("execv ne doit pas être appelé"))
        assert ls.main([]) == 1
        assert "KlodyLaser absent" in capsys.readouterr().err


class TestLanceurShell:
    """Le start-*-mcp.sh doit pointer sur CE module (point_d_entree())."""

    def test_le_script_execute_le_module(self):
        source = Path("scripts/start-laser-mcp.sh").read_text()
        assert "exec python -m klody_mcp.laser_server" in source
