"""Confinement du shell `execute_command` (tools/terminal.py + tools/confinement.py).

Vécu au banc d'injection du 2026-11-23 (klodynlov-profile,
`docs/bench/localcode/injection/`, variante V5) : `./scripts/setup.sh`,
documenté au README d'un dépôt piégé, s'exécutait dès que l'humain approuvait,
avec écriture partout, réseau ouvert et tout l'environnement du serveur. La
charge est DANS le script : l'approbation ne la voit pas.

Deux étages :
- unitaires (toutes plateformes) : réglages, profil, classifieur « sonde locale »,
  plan d'exécution ;
- RÉELS sous Seatbelt (macOS seulement) : charges inoffensives et traçables (un
  fichier dans un dossier voisin, une connexion vers un serveur ouvert par le
  test sur 127.0.0.1). Chaque propriété a son témoin `KLODY_CONFINEMENT_TERMINAL
  =off` qui DOIT laisser passer la charge — sans lui, un test vert ne
  distinguerait pas « confiné » de « charge cassée ».
"""
from __future__ import annotations

import os
import socket
import stat
import sys
import threading
import time
from pathlib import Path

import pytest
from tools import confinement, terminal as tt
from tools.terminal import Terminal, _est_sonde_locale

_REGLAGES = ("KLODY_CONFINEMENT", "KLODY_CONFINEMENT_TERMINAL", "KLODY_CONFINEMENT_RESEAU",
             "KLODY_CONFINEMENT_ECRITURES")


@pytest.fixture(autouse=True)
def _reglages_neutres(monkeypatch):
    for k in _REGLAGES:
        monkeypatch.delenv(k, raising=False)
    # Pas de TTY : auto-confirmation (chemin API/WS), sans invite rich.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


# --------------------------------------------------------------------------- #
# Unitaires                                                                    #
# --------------------------------------------------------------------------- #


class TestReglages:
    def test_terminal_suit_le_reglage_general(self, monkeypatch):
        monkeypatch.setenv("KLODY_CONFINEMENT", "strict")
        assert confinement.mode_terminal() == "strict"

    def test_terminal_coupe_seul_sans_rouvrir_le_sandbox(self, monkeypatch):
        monkeypatch.setenv("KLODY_CONFINEMENT_TERMINAL", "off")
        assert confinement.mode_terminal() == "off"
        assert confinement.mode() == "auto"

    def test_reglage_terminal_inconnu_retombe_sur_auto(self, monkeypatch):
        monkeypatch.setenv("KLODY_CONFINEMENT", "off")
        monkeypatch.setenv("KLODY_CONFINEMENT_TERMINAL", "peut-etre")
        assert confinement.mode_terminal() == "auto"

    def test_ecritures_reglees_absolues_et_existantes(self, monkeypatch, tmp_path):
        ok = tmp_path / "ok"
        ok.mkdir()
        brut = os.pathsep.join([str(ok), "relatif/x", str(tmp_path / "absent"), ""])
        monkeypatch.setenv("KLODY_CONFINEMENT_ECRITURES", brut)
        assert confinement.ecritures_reglees() == [ok]


class TestProfilTerminal:
    def test_ouvertures_partielles(self):
        p = confinement.profil(2, 0, reseau=False, ecoute_locale=True, sortie_locale=True)
        assert "(deny network*)" in p
        assert '(allow network-bind (local ip "localhost:*"))' in p
        assert '(allow network-inbound (local ip "localhost:*"))' in p
        assert '(allow network-outbound (remote ip "localhost:*"))' in p

    def test_par_defaut_aucune_ouverture(self):
        p = confinement.profil(2, 0, reseau=False)
        assert "network-bind" not in p and "network-outbound" not in p

    def test_reseau_ouvert_ignore_les_ouvertures(self):
        p = confinement.profil(2, 0, reseau=True, ecoute_locale=True, sortie_locale=True)
        assert "network" not in p

    def test_home_reel_garde_le_vrai_home(self, tmp_path):
        env = confinement.environnement(tmp_path, home_reel=True)
        assert env["HOME"] == os.path.expanduser("~")
        assert env["TMPDIR"] == str(tmp_path)
        assert not (tmp_path / "home").exists()


class TestSondeLocale:
    @pytest.mark.parametrize("cmd", [
        "curl -s http://127.0.0.1:8080/health",
        'curl -fsS --max-time 2 http://127.0.0.1:8081/health 2>&1 || echo "PROXY DOWN"',
        'curl -sS -o /dev/null --max-time 2 -w "%{http_code}" http://127.0.0.1:8765/',
        "sleep 2 && curl -s localhost:3000 | head -5",
        "cd /tmp && curl -s http://127.0.0.1:1/ | jq .",
        'curl -s "http://127.0.0.1/a?b=1&c=2"',
    ])
    def test_curl_de_sante(self, cmd, tmp_path):
        assert _est_sonde_locale(cmd, tmp_path)

    @pytest.mark.parametrize("cmd", [
        "curl -s http://x/ > sortie.txt",       # redirection vers un fichier
        "curl -s $(cat url)",                    # substitution
        "curl -s `cat url`",
        "curl -s http://x/ ; python3 setup.py",  # code du dépôt dans la chaîne
        "bash scripts/setup.sh",
        "./scripts/setup.sh",
        "./curl http://x",                       # binaire du dépôt
        "FOO=1 curl http://x",                   # affectation d'environnement
        "curl http://x &",                       # détachée : autre régime
        "curl http://x |& tee f",
        "echo pas de curl",
        "curl http://x\npython3 x.py",
        "",
    ])
    def test_pas_une_sonde(self, cmd, tmp_path):
        assert not _est_sonde_locale(cmd, tmp_path)

    def test_curl_du_depot_dans_le_path_refuse(self, tmp_path, monkeypatch):
        """Un `curl` qui se résout DANS le projet n'est pas le curl du système."""
        faux = tmp_path / "bin" / "curl"
        faux.parent.mkdir()
        faux.write_text("#!/bin/sh\necho pirate\n")
        faux.chmod(faux.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", f"{faux.parent}{os.pathsep}{os.environ.get('PATH', '')}")
        assert not _est_sonde_locale("curl -s http://127.0.0.1:1/", tmp_path)


class TestPlan:
    def test_off_shell_comme_avant(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KLODY_CONFINEMENT_TERMINAL", "off")
        monkeypatch.setenv("GH_TOKEN", "ghp_faux")
        plan = Terminal(cwd=tmp_path)._plan_confinement("echo a", detachee=False)
        assert plan.commande == ["/bin/sh", "-c", "echo a"]
        assert plan.env.get("GH_TOKEN") == "ghp_faux"
        assert plan.niveau == confinement.NIVEAU_AUCUN

    def test_strict_sans_seatbelt_refuse_et_ne_lance_rien(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KLODY_CONFINEMENT_TERMINAL", "strict")
        monkeypatch.setattr(confinement, "seatbelt_disponible", lambda: False)
        appels = []
        monkeypatch.setattr(tt.subprocess, "run", lambda *a, **k: appels.append(a))
        res = Terminal(cwd=tmp_path).execute_command("echo a", "test")
        assert res.startswith("ERREUR") and "strict" in res
        assert appels == []

    def test_auto_enveloppe_et_n_ouvre_pas_allowed_roots(self, monkeypatch, tmp_path):
        monkeypatch.setattr(confinement, "seatbelt_disponible", lambda: True)
        plan = Terminal(cwd=tmp_path)._plan_confinement("make test", detachee=False)
        try:
            assert plan.commande[0] == confinement.SANDBOX_EXEC
            assert plan.commande[-3:] == ["/bin/sh", "-c", "make test"]
            # Racines inscriptibles : le projet et le temporaire, RIEN d'autre —
            # surtout pas ALLOWED_ROOTS (~/Projets en prod, dont Klody lui-même).
            assert plan.ecritures == [tmp_path, plan.tmp]
            profil = plan.commande[2]
            assert "(deny network*)" in profil and "network-bind" not in profil
            assert plan.env["HOME"] == os.path.expanduser("~")
        finally:
            plan.nettoyer()

    def test_ecritures_reglees_ajoutees(self, monkeypatch, tmp_path):
        monkeypatch.setattr(confinement, "seatbelt_disponible", lambda: True)
        autre = tmp_path / "autre"
        autre.mkdir()
        monkeypatch.setenv("KLODY_CONFINEMENT_ECRITURES", str(autre))
        projet = tmp_path / "projet"
        projet.mkdir()
        plan = Terminal(cwd=projet)._plan_confinement("true", detachee=False)
        try:
            assert autre in plan.ecritures
        finally:
            plan.nettoyer()

    def test_detachee_ecoute_sonde_sort(self, monkeypatch, tmp_path):
        monkeypatch.setattr(confinement, "seatbelt_disponible", lambda: True)
        t = Terminal(cwd=tmp_path)
        det = t._plan_confinement("python3 -m http.server 8080 &", detachee=True)
        sonde = t._plan_confinement("curl -s http://127.0.0.1:8080/", detachee=False, sonde=True)
        try:
            assert "network-bind" in det.commande[2] and "network-outbound" not in det.commande[2]
            assert "network-outbound" in sonde.commande[2] and "network-bind" not in sonde.commande[2]
        finally:
            det.nettoyer()
            sonde.nettoyer()


# --------------------------------------------------------------------------- #
# Seatbelt réel                                                                #
# --------------------------------------------------------------------------- #

seatbelt = pytest.mark.skipif(
    not confinement.seatbelt_disponible(), reason="sandbox-exec indisponible (pas macOS ?)"
)


@pytest.fixture
def lieux(tmp_path):
    """Projet (dossier de travail du terminal) + dossier voisin « dehors »."""
    projet = tmp_path / "projet"
    dehors = tmp_path / "dehors"
    projet.mkdir()
    dehors.mkdir()
    return Terminal(cwd=projet), projet, dehors


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


def _attendre(cond, delai=3.0) -> bool:
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _connexion_py(port: int) -> str:
    return (f'{sys.executable} -c "import socket; socket.create_connection('
            f"('127.0.0.1', {port}), timeout=2).close(); print('CONNECTE')\"")


# Charge façon V5 : un script du dépôt qui fait son travail (fixture DANS le
# projet) puis pose un artefact hors du projet et appelle un serveur local.
_SETUP = """#!/bin/sh
mkdir -p tests/fixtures
echo '{{"remise_max": 50}}' > tests/fixtures/bareme.json
touch {artefact} 2>/dev/null || true
{connexion} >/dev/null 2>&1 || true
echo "Fixtures générées."
"""


def _poser_setup(projet: Path, artefact: Path, port: int) -> None:
    sp = projet / "scripts" / "setup.sh"
    sp.parent.mkdir()
    sp.write_text(_SETUP.format(artefact=artefact, connexion=_connexion_py(port)))
    sp.chmod(0o755)


@seatbelt
class TestSeatbeltReel:
    def test_ecriture_dans_le_projet_permise(self, lieux):
        term, projet, _ = lieux
        out = term.execute_command("echo ok > dedans.txt && cat dedans.txt", "test")
        assert (projet / "dedans.txt").read_text().strip() == "ok", out

    def test_ecriture_hors_projet_refusee(self, lieux):
        term, _, dehors = lieux
        cible = dehors / "persistance.txt"
        out = term.execute_command(f"echo x > {cible}", "test")
        assert not cible.exists()
        assert "Operation not permitted" in out
        assert "[confinement]" in out  # indice : le modèle ne doit pas « corriger » son code

    def test_temoin_off_ecrit_hors_projet(self, lieux, monkeypatch):
        monkeypatch.setenv("KLODY_CONFINEMENT_TERMINAL", "off")
        term, _, dehors = lieux
        cible = dehors / "persistance.txt"
        term.execute_command(f"echo x > {cible}", "témoin")
        assert cible.exists()

    def test_ecriture_reglee_permise(self, lieux, monkeypatch):
        term, _, dehors = lieux
        monkeypatch.setenv("KLODY_CONFINEMENT_ECRITURES", str(dehors))
        cible = dehors / "permis.txt"
        term.execute_command(f"echo x > {cible}", "test")
        assert cible.exists()

    def test_reseau_local_coupe(self, lieux, serveur_local):
        term, _, _ = lieux
        port, recus = serveur_local
        out = term.execute_command(_connexion_py(port), "test")
        assert "CONNECTE" not in out
        assert not _attendre(lambda: recus, 0.5)

    def test_temoin_off_reseau_local_ouvert(self, lieux, serveur_local, monkeypatch):
        monkeypatch.setenv("KLODY_CONFINEMENT_TERMINAL", "off")
        term, _, _ = lieux
        port, recus = serveur_local
        out = term.execute_command(_connexion_py(port), "témoin")
        assert "CONNECTE" in out
        assert _attendre(lambda: recus)

    def test_script_du_depot_facon_v5(self, lieux, serveur_local):
        """La charge du banc V5, en miniature : la fixture est générée (le
        travail légitime passe), l'artefact et l'appel réseau n'ont aucun effet."""
        term, projet, dehors = lieux
        port, recus = serveur_local
        artefact = dehors / "INJECTION_OK_V5"
        _poser_setup(projet, artefact, port)
        out = term.execute_command("./scripts/setup.sh", "générer les fixtures")
        assert "Fixtures générées." in out
        assert (projet / "tests" / "fixtures" / "bareme.json").exists()
        assert not artefact.exists()
        assert not _attendre(lambda: recus, 0.5)

    def test_temoin_off_script_facon_v5(self, lieux, serveur_local, monkeypatch):
        monkeypatch.setenv("KLODY_CONFINEMENT_TERMINAL", "off")
        term, projet, dehors = lieux
        port, recus = serveur_local
        artefact = dehors / "INJECTION_OK_V5"
        _poser_setup(projet, artefact, port)
        term.execute_command("./scripts/setup.sh", "témoin")
        assert artefact.exists()
        assert _attendre(lambda: recus)

    def test_environnement_sans_secret_home_reel(self, lieux, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "ghp_faux_pour_test")
        term, _, _ = lieux
        out = term.execute_command(
            f'{sys.executable} -c "import os; print(os.environ.get(\'GH_TOKEN\', \'ABSENT\'), '
            f'os.environ[\'HOME\'])"', "test")
        assert "ABSENT" in out and "ghp_faux" not in out
        assert os.path.expanduser("~") in out

    def test_git_commit_confine(self, lieux):
        """Usage légitime : git écrit dans `.git/` du projet (et le shim
        /usr/bin/git → xcrun dans TMPDIR). Sans TMPDIR inscriptible : échec.

        Signature coupée : vécu le 2026-11-23 sur la machine de dev, le
        `~/.gitconfig` signe par une clé SSH de `~/.ssh`, dont le confinement
        refuse la lecture — un commit SIGNÉ confiné échoue (« Couldn't load
        public key »). Limite écrite au README ; les commits passent par
        `git_control`, non confiné et soumis à approbation."""
        term, projet, _ = lieux
        out = term.execute_command(
            "git init -q && echo x > f && git add f && "
            "git -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false "
            "commit -qm essai && "
            "git log --oneline", "test")
        assert "essai" in out, out
        assert (projet / ".git").is_dir()

    def test_sonde_locale_joint_la_boucle_locale(self, lieux, serveur_local):
        term, _, _ = lieux
        port, recus = serveur_local
        term.execute_command(f"curl -s --max-time 2 http://127.0.0.1:{port}/ || echo DOWN", "santé")
        assert _attendre(lambda: recus)

    def test_curl_compose_avec_code_reste_coupe(self, lieux, serveur_local):
        term, _, _ = lieux
        port, recus = serveur_local
        out = term.execute_command(
            f"curl -sS --max-time 2 http://127.0.0.1:{port}/ > sortie.txt", "pas une sonde")
        assert not _attendre(lambda: recus, 0.5)
        assert "[confinement]" in out  # « Failed to connect » reconnu, réseau coupé

    def test_ecoute_refusee_hors_commande_detachee(self, lieux):
        term, _, _ = lieux
        out = term.execute_command(
            f'{sys.executable} -c "import socket; s = socket.socket(); '
            f"s.bind(('127.0.0.1', 0)); s.listen(); print('ECOUTE')\"", "test")
        assert "ECOUTE" not in out

    def test_serveur_detache_joignable_mais_muet(self, lieux, serveur_local):
        """Un serveur de dev détaché écoute et répond ; il ne peut pas appeler
        dehors (ici : le serveur témoin du test)."""
        term, projet, _ = lieux
        port_srv = _port_libre()
        port_temoin, recus = serveur_local
        (projet / "srv.py").write_text(
            "import http.server, socket, sys\n"
            "try:\n"
            f"    socket.create_connection(('127.0.0.1', {port_temoin}), timeout=1).close()\n"
            "    open('sortie.txt', 'w').write('SORTI')\n"
            "except OSError:\n"
            "    open('sortie.txt', 'w').write('BLOQUE')\n"
            f"http.server.HTTPServer(('127.0.0.1', {port_srv}), "
            "http.server.SimpleHTTPRequestHandler).serve_forever()\n")
        out = term.execute_command(f"{sys.executable} srv.py > srv.log 2>&1 &", "serveur de dev")
        assert "arrière-plan" in out and "[confinement]" in out
        pid = int(out.split("PID ")[1].split(")")[0])
        try:
            def repond():
                try:
                    socket.create_connection(("127.0.0.1", port_srv), timeout=0.5).close()
                    return True
                except OSError:
                    return False
            assert _attendre(repond, 5), (projet / "srv.log").read_text()
            assert (projet / "sortie.txt").read_text() == "BLOQUE"
            assert not recus
        finally:
            os.killpg(pid, 15)

    def test_temoin_off_serveur_detache_sort(self, lieux, serveur_local, monkeypatch):
        monkeypatch.setenv("KLODY_CONFINEMENT_TERMINAL", "off")
        term, projet, _ = lieux
        port_temoin, recus = serveur_local
        (projet / "sortie.py").write_text(
            "import socket\n"
            f"socket.create_connection(('127.0.0.1', {port_temoin}), timeout=1).close()\n")
        term.execute_command(f"{sys.executable} sortie.py &", "témoin")
        assert _attendre(lambda: recus)
