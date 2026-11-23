"""Confinement des exécutions du sandbox (tools/confinement.py).

Vécu au banc d'injection du 2026-11-23 (klodynlov-profile,
`docs/bench/localcode/injection/`, variante V4) : un `tests/conftest.py` piégé
s'exécutait 10/10 dès que pytest était approuvé, avec tout l'environnement du
serveur, l'écriture partout et le réseau ouvert.

Deux étages :
- unitaires (toutes plateformes) : environnement en liste blanche, profil SBPL,
  modes auto / strict / off ;
- intégration RÉELLE sous Seatbelt (macOS seulement) : charges inoffensives et
  traçables — un fichier dans un dossier voisin du projet, une connexion vers
  un serveur que le test ouvre lui-même sur 127.0.0.1. Chaque propriété a son
  témoin `KLODY_CONFINEMENT=off`, qui DOIT laisser passer la charge : sans lui,
  un test vert ne distinguerait pas « confiné » de « charge cassée ».
"""
from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

import pytest
from tools import confinement
from tools.sandbox import SandboxRunner

# --------------------------------------------------------------------------- #
# Unitaires                                                                    #
# --------------------------------------------------------------------------- #


class TestEnvironnement:
    def test_secrets_et_variables_klody_jetes(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GH_TOKEN", "ghp_faux")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-faux")
        monkeypatch.setenv("KLODY_LB_TOKEN", "faux")
        monkeypatch.setenv("MON_JETON_PERSO", "faux")
        env = confinement.environnement(tmp_path)
        for k in ("GH_TOKEN", "OPENAI_API_KEY", "KLODY_LB_TOKEN", "MON_JETON_PERSO"):
            assert k not in env

    def test_home_et_tmpdir_dans_le_temporaire(self, tmp_path):
        env = confinement.environnement(tmp_path)
        assert env["TMPDIR"] == str(tmp_path)
        assert env["HOME"].startswith(str(tmp_path))
        assert Path(env["HOME"]).is_dir()
        assert "PATH" in env

    def test_extra_ajoute(self, tmp_path):
        env = confinement.environnement(tmp_path, {"MPLBACKEND": "Agg"})
        assert env["MPLBACKEND"] == "Agg"


class TestProfil:
    def test_reseau_coupe_par_defaut(self):
        p = confinement.profil(2, 1, reseau=False)
        assert "(deny network*)" in p
        assert "(deny file-write*)" in p
        assert '(param "ECRIT_1")' in p and '(param "SECRET_0")' in p
        assert "(deny appleevent-send)" in p and "(deny lsopen)" in p

    def test_reseau_ouvert_sur_demande(self):
        assert "(deny network*)" not in confinement.profil(1, 0, reseau=True)

    def test_aucun_chemin_interpole(self):
        # Les chemins passent par -D : le texte du profil ne cite que /dev.
        import re
        chemins = re.findall(r'"\^?(/[^"]*)"', confinement.profil(3, 3, reseau=False))
        assert chemins and all(c.startswith("/dev/") for c in chemins), chemins


class TestModes:
    def test_off_garde_tout_l_environnement(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KLODY_CONFINEMENT", "off")
        monkeypatch.setenv("GH_TOKEN", "ghp_faux")
        plan = confinement.preparer(["echo"], tmp_path)
        assert plan.niveau == confinement.NIVEAU_AUCUN
        assert plan.commande == ["echo"]
        assert plan.env["GH_TOKEN"] == "ghp_faux"

    def test_strict_refuse_sans_seatbelt(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KLODY_CONFINEMENT", "strict")
        monkeypatch.setattr(confinement, "seatbelt_disponible", lambda: False)
        plan = confinement.preparer(["echo"], tmp_path)
        assert plan.refus and "strict" in plan.refus
        assert plan.commande == []

    def test_auto_sans_seatbelt_nettoie_quand_meme(self, monkeypatch, tmp_path):
        monkeypatch.delenv("KLODY_CONFINEMENT", raising=False)
        monkeypatch.setenv("GH_TOKEN", "ghp_faux")
        monkeypatch.setattr(confinement, "seatbelt_disponible", lambda: False)
        plan = confinement.preparer(["echo"], tmp_path)
        try:
            assert plan.niveau == confinement.NIVEAU_ENV
            assert plan.commande == ["echo"]
            assert "GH_TOKEN" not in plan.env
        finally:
            plan.nettoyer()
        assert not plan.tmp.exists()

    def test_auto_avec_seatbelt_enveloppe(self, monkeypatch, tmp_path):
        monkeypatch.delenv("KLODY_CONFINEMENT", raising=False)
        monkeypatch.delenv("KLODY_CONFINEMENT_RESEAU", raising=False)
        monkeypatch.setattr(confinement, "seatbelt_disponible", lambda: True)
        plan = confinement.preparer(["echo", "x"], tmp_path)
        try:
            assert plan.commande[0] == confinement.SANDBOX_EXEC
            assert plan.commande[-2:] == ["echo", "x"]
            assert "(deny network*)" in plan.commande[2]
            assert any(a.startswith("ECRIT_0=") for a in plan.commande)
        finally:
            plan.nettoyer()

    def test_reseau_par_reglage(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KLODY_CONFINEMENT_RESEAU", "1")
        monkeypatch.setattr(confinement, "seatbelt_disponible", lambda: True)
        plan = confinement.preparer(["echo"], tmp_path)
        try:
            assert "(deny network*)" not in plan.commande[2]
        finally:
            plan.nettoyer()

    def test_mode_inconnu_retombe_sur_auto(self, monkeypatch):
        monkeypatch.setenv("KLODY_CONFINEMENT", "nimporte")
        assert confinement.mode() == confinement.MODE_AUTO


class TestIndiceRefus:
    def test_indice_si_refus_sous_confinement(self):
        txt = confinement.indice_refus("PermissionError: [Errno 1] Operation not permitted",
                                       confinement.NIVEAU_SYSTEME)
        assert "confinement" in txt

    def test_pas_d_indice_sans_confinement_systeme(self):
        assert confinement.indice_refus("Operation not permitted", confinement.NIVEAU_ENV) == ""
        assert confinement.indice_refus("ZeroDivisionError", confinement.NIVEAU_SYSTEME) == ""


# --------------------------------------------------------------------------- #
# Intégration réelle (macOS + sandbox-exec)                                    #
# --------------------------------------------------------------------------- #

seatbelt = pytest.mark.skipif(
    not confinement.seatbelt_disponible(), reason="sandbox-exec indisponible (pas macOS ?)"
)


@pytest.fixture
def projet(tmp_path, monkeypatch):
    """Projet + dossier voisin « dehors » + runner dont le venv « existe ».

    Les commandes passent l'interpréteur des tests en chemin absolu (non
    réécrit vers le venv) : aucun venv réel, aucun pip, aucun réseau.
    """
    monkeypatch.delenv("KLODY_CONFINEMENT", raising=False)
    monkeypatch.delenv("KLODY_CONFINEMENT_RESEAU", raising=False)
    racine = tmp_path / "projet"
    dehors = tmp_path / "dehors"
    racine.mkdir()
    dehors.mkdir()
    runner = SandboxRunner(workdir=racine, cache_root=tmp_path / "_cache")
    (runner.venv_dir / "bin").mkdir(parents=True)
    runner.python.touch()
    return runner, racine, dehors


@pytest.fixture
def serveur_local():
    """Serveur TCP sur 127.0.0.1 qui compte les connexions reçues."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.2)
    recus: list[int] = []
    stop = threading.Event()

    def boucle():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                recus.append(1)
                c.close()
            except OSError:
                continue

    t = threading.Thread(target=boucle, daemon=True)
    t.start()
    yield srv.getsockname()[1], recus
    stop.set()
    t.join(timeout=2)
    srv.close()


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@seatbelt
class TestSeatbeltReel:
    def test_ecriture_dans_le_projet_permise(self, projet):
        runner, racine, _ = projet
        r = runner.run(_py("open('ok.txt','w').write('1')"))
        assert r.success, r.stderr
        assert r.confinement == confinement.NIVEAU_SYSTEME
        assert (racine / "ok.txt").read_text() == "1"

    def test_ecriture_hors_projet_refusee(self, projet):
        runner, _, dehors = projet
        cible = dehors / "INJECTION_OK_ecriture"
        r = runner.run(_py(f"open({str(cible)!r},'w').write('1')"))
        assert not r.success
        assert "Operation not permitted" in r.stderr
        assert not cible.exists()
        assert "[confinement]" in r.format_for_llm()

    def test_temoin_off_ecrit_hors_projet(self, projet, monkeypatch):
        runner, _, dehors = projet
        monkeypatch.setenv("KLODY_CONFINEMENT", "off")
        cible = dehors / "INJECTION_OK_temoin"
        r = runner.run(_py(f"open({str(cible)!r},'w').write('1')"))
        assert r.success and cible.exists()

    def test_reseau_local_coupe(self, projet, serveur_local):
        runner, _, _ = projet
        port, recus = serveur_local
        code = f"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=2)"
        r = runner.run(_py(code))
        assert not r.success
        assert recus == []

    def test_temoin_off_reseau_local_ouvert(self, projet, serveur_local, monkeypatch):
        runner, _, _ = projet
        port, recus = serveur_local
        monkeypatch.setenv("KLODY_CONFINEMENT", "off")
        code = f"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=2)"
        r = runner.run(_py(code))
        assert r.success, r.stderr
        import time
        fin = time.monotonic() + 3
        while not recus and time.monotonic() < fin:
            time.sleep(0.05)
        assert recus, "le serveur témoin n'a rien reçu : la sonde réseau est cassée"

    def test_environnement_sans_secret(self, projet, monkeypatch):
        runner, _, _ = projet
        monkeypatch.setenv("KLODY_JETON_DE_TEST", "secret-de-test")
        r = runner.run(_py("import os; print(os.environ.get('KLODY_JETON_DE_TEST'))"))
        assert r.success, r.stderr
        assert r.stdout.strip() == "None"

    def test_conftest_piege_sans_effet(self, projet, serveur_local):
        """La variante V4 du banc, en miniature : la charge s'exécute au
        chargement de pytest, mais ne laisse aucune trace hors du projet."""
        runner, racine, dehors = projet
        port, recus = serveur_local
        trace = dehors / "INJECTION_OK_V4"
        (racine / "tests").mkdir()
        (racine / "tests" / "conftest.py").write_text(
            "import socket\n"
            "try:\n"
            f"    open({str(trace)!r}, 'w').write('charge')\n"
            "except OSError:\n"
            "    pass\n"
            "try:\n"
            f"    socket.create_connection(('127.0.0.1', {port}), timeout=2)\n"
            "except OSError:\n"
            "    pass\n"
        )
        (racine / "tests" / "test_x.py").write_text("def test_x():\n    assert 1 + 1 == 2\n")
        r = runner.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
        assert r.success, r.stdout + r.stderr  # le vrai travail (les tests) passe
        assert not trace.exists()
        assert recus == []


class TestRedirectionsSansEffet:
    """`2>&1` passé à run_in_sandbox devenait un argument de pytest (exit 4)."""

    def test_2_et_1_retire(self, projet):
        runner, racine, _ = projet
        (racine / "test_a.py").write_text("def test_a():\n    assert True\n")
        r = runner.run(f"{sys.executable} -m pytest -q -p no:cacheprovider test_a.py 2>&1")
        assert r.success, r.stdout + r.stderr
        assert "2>&1" not in r.command

    def test_indice_ne_designe_pas_le_blocage_comme_la_panne(self):
        txt = confinement.indice_refus("Operation not permitted", confinement.NIVEAU_SYSTEME)
        assert "BLOQUÉE" in txt and "signale" in txt
        assert "pas la cause" in txt


def test_profil_pip_reserve_au_pip_du_venv(tmp_path, monkeypatch):
    """`<autre binaire> -m pip` ne doit pas obtenir le réseau du profil pip."""
    monkeypatch.setattr(confinement, "seatbelt_disponible", lambda: True)
    monkeypatch.delenv("KLODY_CONFINEMENT", raising=False)
    monkeypatch.delenv("KLODY_CONFINEMENT_RESEAU", raising=False)
    runner = SandboxRunner(workdir=tmp_path, cache_root=tmp_path / "_cache")
    (runner.venv_dir / "bin").mkdir(parents=True)
    runner.python.touch()
    vus: list[list[str]] = []

    def faux_executer(plan, cmd_str, is_python, timeout):
        vus.append(plan.commande)
        from tools.sandbox import SandboxResult
        return SandboxResult(command=cmd_str, exit_code=0, stdout="", stderr="", duration_s=0.0)

    monkeypatch.setattr(runner, "_executer", faux_executer)
    runner.run(["/usr/bin/env", "-m", "pip", "install", "x"])
    runner.run(["python", "-m", "pip", "install", "x"])
    assert "(deny network*)" in vus[0][2]       # binaire quelconque : réseau coupé
    assert "(deny network*)" not in vus[1][2]   # python du venv -m pip : profil pip
