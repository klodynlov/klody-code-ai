"""Tests pour klody_mcp/klod_server.py — pont vers l'organe KLOD Live Brain.

Le pont ne doit ni importer KLOD ni lancer quoi que ce soit dans les tests :
`os.execve` est bouchonné, on vérifie seulement QUOI il exécuterait, où, et avec
quel environnement.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from klody_mcp import klod_server as ks


def _organe(tmp_path: Path, executable: bool = True, module: bool = True) -> Path:
    racine = tmp_path / "klod-live-brain"
    python = racine / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n")
    if executable:
        python.chmod(0o755)
    if module:
        (racine / "klod_mcp").mkdir()
        (racine / "klod_mcp" / "klod_server.py").write_text("")
    return racine


class TestDossierOrgane:
    def test_defaut_sous_le_home(self):
        assert ks.dossier_organe({}) == Path.home() / "klod-live-brain"

    def test_variable_env_prioritaire(self, tmp_path: Path):
        assert ks.dossier_organe({"KLOD_LIVE_BRAIN_DIR": str(tmp_path)}) == tmp_path

    def test_tilde_developpe(self):
        assert ks.dossier_organe({"KLOD_LIVE_BRAIN_DIR": "~/x"}) == Path.home() / "x"


class TestInterpreteur:
    def test_trouve_le_python_du_venv(self, tmp_path: Path):
        racine = _organe(tmp_path)
        assert ks.interpreteur(racine) == racine / ".venv" / "bin" / "python"

    def test_venv_absent_leve(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="venv non créé"):
            ks.interpreteur(tmp_path / "nulle-part")

    def test_python_non_executable_leve(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="venv non créé"):
            ks.interpreteur(_organe(tmp_path, executable=False))

    def test_organe_sans_serveur_mcp_leve(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match=r"sans klod_mcp/klod_server\.py"):
            ks.interpreteur(_organe(tmp_path, module=False))


class TestEnvironnement:
    def test_force_http_local_sur_le_port(self):
        env = ks.environnement({"PATH": "/opt/homebrew/bin:/usr/bin", "KLOD_MCP_TRANSPORT": "stdio"})
        assert env["KLOD_MCP_TRANSPORT"] == "http"
        assert env["KLOD_MCP_HOST"] == "127.0.0.1"
        assert env["KLOD_MCP_PORT"] == str(ks.PORT)
        assert env["PATH"] == "/opt/homebrew/bin:/usr/bin"  # adb doit rester trouvable

    def test_ne_confond_pas_avec_le_port_de_klody(self):
        # KLODY_MCP_PORT (serveur MCP propre de Klody) traverse sans être touché.
        env = ks.environnement({"KLODY_MCP_PORT": "8087"})
        assert env["KLODY_MCP_PORT"] == "8087"
        assert env["KLOD_MCP_PORT"] != "8087"


class TestMain:
    def test_execve_l_interpreteur_de_l_organe(self, tmp_path: Path, monkeypatch, capsys):
        racine = _organe(tmp_path)
        monkeypatch.setenv("KLOD_LIVE_BRAIN_DIR", str(racine))
        appels: list[tuple[str, list[str], dict[str, str], str]] = []
        monkeypatch.setattr(os, "execve", lambda p, a, e: appels.append((p, a, e, os.getcwd())))
        cwd = os.getcwd()
        try:
            ks.main([])
        finally:
            os.chdir(cwd)
        python = str(racine / ".venv" / "bin" / "python")
        assert len(appels) == 1
        p, args, env, dossier = appels[0]
        assert (p, args) == (python, [python, "-m", "klod_mcp.klod_server"])
        assert Path(dossier).resolve() == racine.resolve()  # `-m klod_mcp…` se résout là
        assert env["KLOD_MCP_TRANSPORT"] == "http"
        out = capsys.readouterr().out
        assert f"http://127.0.0.1:{ks.PORT}/mcp" in out
        assert str(racine) in out

    def test_organe_absent_sort_en_1_sans_exec(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setenv("KLOD_LIVE_BRAIN_DIR", str(tmp_path / "rien"))
        monkeypatch.setattr(os, "execve", lambda *_: pytest.fail("execve ne doit pas être appelé"))
        assert ks.main([]) == 1
        assert "venv non créé" in capsys.readouterr().err


class TestLanceurShell:
    """Le start-*-mcp.sh doit pointer sur CE module (point_d_entree())."""

    def test_le_script_execute_le_module(self):
        source = Path("scripts/start-klod-mcp.sh").read_text()
        assert "exec python -m klody_mcp.klod_server" in source

    def test_le_port_par_defaut_est_8104(self, monkeypatch):
        # Hors du bloc 8080-8099 des workers klody-core. Verrouillé sur un LITTÉRAL,
        # variable retirée : un test qui relirait l'environnement suivrait le réglage
        # au lieu de le juger (CLAUDE.md, « mutation échappée »).
        monkeypatch.delenv("KLOD_MCP_PORT", raising=False)
        try:
            assert importlib.reload(ks).PORT == 8104
        finally:
            importlib.reload(ks)
