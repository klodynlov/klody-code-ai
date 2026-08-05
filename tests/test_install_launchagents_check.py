"""`install-launchagents.sh --check` : ce qu'il juge, et ce qu'il se contente
de signaler.

Le contrôle historique compare les plists versionnés à ceux installés. Un
plist conforme ne dit pourtant RIEN de ce que le service exécute : les agents
lancent des scripts de ce dépôt, avec `WorkingDirectory` sur sa racine, donc le
code en production est celui de l'ARBRE DE TRAVAIL au démarrage. D'où la
section ajoutée le 2026-08-03 — et d'où ces tests, parce qu'elle porte deux
propriétés faciles à casser sans le voir :

* elle **ne doit pas** influencer le code de sortie — diverger d'origin/main
  est l'état normal d'un poste de développement, et faire rougir `--check`
  là-dessus le rendrait inutilisable ;
* elle **doit se taire** quand aucun service ne tourne, sinon elle bruite tous
  les runners de CI.

S'y ajoute depuis le 2026-08-05 le contrôle des DÉPENDANCES (`mtime_dependances`),
angle mort de tout ce qui précède : pip peut réécrire `site-packages` sous un
service vivant sans qu'aucun fichier du dépôt ne bouge. Même contrat — il
informe et nomme `scripts/diagnostic_peremption.py`, qui lui juge.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "install-launchagents.sh"
AGENTS = REPO / "launchagents"

ORIG_REPO = "/Users/klodynlov/Projets/klody-code-ai"
ORIG_HOME = "/Users/klodynlov"


@pytest.fixture
def home_synchronise(tmp_path):
    """Un `$HOME` où les agents installés sont exactement ceux du dépôt.

    Reproduit le rendu du script (mêmes substitutions) plutôt que de l'appeler
    sans `--check` : l'appeler installerait ET bootstrapperait de vrais
    services sur la machine qui exécute les tests.
    """
    dest = tmp_path / "Library" / "LaunchAgents"
    dest.mkdir(parents=True)
    for src in AGENTS.glob("*.plist"):
        rendu = src.read_text().replace(ORIG_REPO, str(REPO)).replace(ORIG_HOME, str(tmp_path))
        (dest / src.name).write_text(rendu)
    return tmp_path


def _lancer(home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), "--check"],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=180,
    )


class TestCodeDeSortie:
    def test_aucun_ecart_de_plist_rend_zero(self, home_synchronise):
        """Même si la branche diverge d'origin/main et que des services
        tournent : la nouvelle section informe, elle ne juge pas."""
        p = _lancer(home_synchronise)
        assert p.returncode == 0, p.stdout + p.stderr

    def test_un_plist_manquant_rend_un(self, home_synchronise):
        """Le critère historique doit continuer de mordre."""
        agents = home_synchronise / "Library" / "LaunchAgents"
        victime = sorted(agents.glob("*.plist"))[0]
        victime.unlink()
        p = _lancer(home_synchronise)
        assert p.returncode == 1
        assert "ABSENT" in p.stdout

    def test_un_plist_divergent_rend_un(self, home_synchronise):
        agents = home_synchronise / "Library" / "LaunchAgents"
        victime = sorted(agents.glob("*.plist"))[0]
        avant = victime.read_text()
        # ⚠️ Une substitution ciblée (`<integer>30</integer>`) ne s'appliquait
        # pas au premier plist par ordre alphabétique : le test passait sur une
        # mutation qui n'avait rien muté, et rendait vert un script qu'il
        # croyait avoir mis en défaut. On mute donc à coup sûr, et on le
        # vérifie.
        victime.write_text(avant.replace("</dict>\n</plist>",
                                         "\t<key>Zzz</key><string>x</string>\n</dict>\n</plist>"))
        assert victime.read_text() != avant, "la mutation n'a rien changé"
        p = _lancer(home_synchronise)
        assert p.returncode == 1, p.stdout
        assert "ÉCART" in p.stdout


class TestSectionCodeServi:
    def test_muette_sans_service_en_cours(self, home_synchronise, monkeypatch, tmp_path):
        """Sur un runner de CI, aucun agent n'est chargé : la section ne doit
        rien écrire du tout. On le vérifie en neutralisant `launchctl`, ce qui
        rend l'absence de service certaine plutôt que supposée."""
        faux_bin = tmp_path / "bin"
        faux_bin.mkdir()
        (faux_bin / "launchctl").write_text("#!/bin/sh\nexit 1\n")
        (faux_bin / "launchctl").chmod(0o755)
        env_path = f"{faux_bin}:{os.environ['PATH']}"
        p = subprocess.run(
            ["/bin/sh", str(SCRIPT), "--check"],
            capture_output=True, text=True, timeout=180, cwd=str(REPO),
            env=dict(os.environ, HOME=str(home_synchronise), PATH=env_path),
        )
        assert p.returncode == 0
        assert "service(s) en cours" not in p.stdout
        assert "ATTENTION" not in p.stdout
        assert "PÉRIMÉ" not in p.stdout

    def test_compte_les_services_quand_il_y_en_a(self, home_synchronise):
        """Sur une machine où des agents de ce dépôt sont chargés, le résumé
        doit être là et cohérent.

        ⚠️ La condition de saut est mesurée INDÉPENDAMMENT, en interrogeant
        launchd. La version précédente se sautait quand sa propre regex ne
        trouvait rien, en annonçant « aucun agent chargé » : après un
        changement de libellé du résumé, elle a donc cessé de tester tout en
        affichant une raison fausse — dix agents étaient chargés.
        """
        charges = [
            plist.stem for plist in sorted(AGENTS.glob("*.plist"))
            if subprocess.run(
                ["/bin/sh", "-c",
                 f'launchctl print "gui/$(id -u)/{plist.stem}" >/dev/null 2>&1'],
                check=False).returncode == 0
        ]
        if not charges:
            pytest.skip("aucun agent de ce dépôt chargé (CI)")

        p = _lancer(home_synchronise)
        m = re.search(r"(\d+) agent\(s\) chargé\(s\) : (\d+) résident\(s\) dont "
                      r"(\d+) au point d'entrée périmé, (\d+) périodique\(s\)", p.stdout)
        assert m, f"résumé absent alors que {len(charges)} agent(s) sont chargés :\n{p.stdout}"
        nb, residents, perimes, periodiques = (int(g) for g in m.groups())
        assert nb == len(charges), f"{nb} annoncés, {len(charges)} mesurés : {charges}"
        assert residents + periodiques == nb
        assert perimes <= residents


def _faux_launchctl(dossier: Path, absent: str = "", pid: str = "1") -> str:
    """Un `launchctl` bouchonné, pour tester des états qu'on ne va pas
    provoquer sur la machine : arrêter un vrai service pour voir si le contrôle
    le remarque coûterait une coupure de l'API.

    `pid = 1` (launchd) est choisi parce qu'il est TOUJOURS vivant : `ps` peut
    donc en tirer une vraie date de démarrage, et le critère de péremption
    s'évalue pour de bon au lieu d'être sauté faute de processus.
    """
    dossier.mkdir(parents=True, exist_ok=True)
    script = dossier / "launchctl"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in print) ;; *) exit 0 ;; esac\n'
        f'case "$2" in *{absent or "AUCUN_LABEL_ABSENT"}) exit 1 ;; esac\n'
        'case "$2" in */com.klody.*) ;; *) exit 0 ;; esac\n'
        f'printf "\\tstate = running\\n\\tpid = {pid}\\n"\n'
    )
    script.chmod(0o755)
    return f"{dossier}:{os.environ['PATH']}"


def _lancer_avec_path(home: Path, path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), "--check"],
        capture_output=True, text=True, timeout=180, cwd=str(REPO),
        env=dict(os.environ, HOME=str(home), PATH=path),
    )


class TestChargeContrePid:
    """« Pas de PID » ne veut pas dire « pas chargé ».

    Un job `StartInterval` n'a de processus que pendant son tick. La première
    version confondait les deux et déclarait `com.klody.api-watchdog` absent de
    launchd — alors qu'il avait 669 exécutions au compteur et un journal
    d'incidents réels. Ces tests verrouillent la distinction.
    """

    def test_un_service_absent_de_launchd_est_signale(self, home_synchronise, tmp_path):
        path = _faux_launchctl(tmp_path / "bin", absent="com.klody.web-mcp")
        p = _lancer_avec_path(home_synchronise, path)
        assert "NON CHARGÉ  com.klody.web-mcp" in p.stdout, p.stdout
        assert "launchctl bootstrap" in p.stdout

    def test_les_periodiques_sont_comptes_a_part(self, home_synchronise, tmp_path):
        """`api-watchdog` porte un `StartInterval` : il doit apparaître comme
        chargé ET périodique, jamais comme périmé."""
        path = _faux_launchctl(tmp_path / "bin")
        p = _lancer_avec_path(home_synchronise, path)
        m = re.search(r"(\d+) agent\(s\) chargé\(s\) : (\d+) résident\(s\) dont "
                      r"(\d+) au point d'entrée périmé, (\d+) périodique\(s\)", p.stdout)
        assert m, p.stdout
        charges, residents, _perimes, periodiques = (int(g) for g in m.groups())
        nb_plists = len(list(AGENTS.glob("*.plist")))
        assert charges == nb_plists, "tous les agents bouchonnés sont chargés"
        assert periodiques >= 1, "com.klody.api-watchdog a un StartInterval"
        assert residents + periodiques == charges

    def test_un_periodique_n_est_jamais_declare_perime(self, home_synchronise, tmp_path):
        """Avec `pid = 1` (démarré au boot, donc antérieur à toute source), un
        résident serait périmé — le périodique, lui, ne doit jamais l'être : il
        ré-exécute son programme à chaque tick."""
        path = _faux_launchctl(tmp_path / "bin")
        p = _lancer_avec_path(home_synchronise, path)
        assert "PÉRIMÉ  com.klody.api-watchdog" not in p.stdout, p.stdout


class TestPointDEntree:
    """L'attribution doit rester NOMINATIVE : un service n'est déclaré périmé
    que si SON lanceur ou SON module a bougé. La première version comparait à
    tout `klody_mcp/` et accusait 7 services sur 9 — un contrôle toujours rouge
    n'est plus lu."""

    def test_chaque_agent_mcp_a_un_point_d_entree_resoluble(self):
        script = SCRIPT.read_text()
        assert "point_d_entree()" in script, "la fonction a été renommée"
        for plist in sorted(AGENTS.glob("com.klody.*-mcp.plist")):
            sortie = subprocess.run(
                ["/bin/sh", "-c",
                 f'REPO_ROOT={REPO}; ORIG_REPO={ORIG_REPO}; ORIG_HOME={ORIG_HOME}; '
                 + _extraire_fonction(script, "point_d_entree")
                 + f' point_d_entree "{plist}"'],
                capture_output=True, text=True, timeout=60,
            ).stdout.split()
            assert len(sortie) == 2, f"{plist.name}: attendu lanceur + module, eu {sortie}"
            assert sortie[0].endswith(".sh")
            assert sortie[1].endswith(".py")


def _extraire_fonction(script: str, nom: str) -> str:
    """Isole une fonction shell du script pour la tester seule."""
    m = re.search(rf"^{nom}\(\) \{{.*?^\}}", script, re.S | re.M)
    assert m, f"fonction {nom} introuvable"
    return m.group(0) + "\n"


def _mtime_dependances(repo_root: Path) -> str:
    """Exécute `mtime_dependances` seule, sur un `REPO_ROOT` de test."""
    script = SCRIPT.read_text()
    return subprocess.run(
        ["/bin/sh", "-c",
         f'REPO_ROOT="{repo_root}"\n'
         + _extraire_fonction(script, "mtime_max")
         + _extraire_fonction(script, "mtime_dependances")
         + "mtime_dependances\n"],
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()


def _venv_factice(racine: Path, mtime: int, paquets=("openai-2.53.0",)) -> Path:
    """Un `.venv` avec des `*.dist-info` datés, comme pip en laisse."""
    sp = racine / ".venv" / "lib" / "python3.11" / "site-packages"
    sp.mkdir(parents=True, exist_ok=True)
    for nom in paquets:
        d = sp / f"{nom}.dist-info"
        d.mkdir(exist_ok=True)
        os.utime(d, (mtime, mtime))
    return sp


# ⚠️ Raison MESURÉE sur le runner (job 92199425263, 2026-08-05), pas déduite :
# `stat -f` ne veut pas dire la même chose des deux côtés. En BSD c'est un format
# d'affichage (`%m` = mtime) ; en GNU c'est « display filesystem status », d'où
#     AssertionError: assert 'Inodes: Total: 19529728   Free: 18430229' == '1700000000'
# Et `demarrage_epoch` s'appuie sur `date -j -f`, qui n'existe pas non plus en GNU.
# Le script pilote launchd : il n'a de sens que sur macOS. La moitié PORTABLE du
# garde est `agent/peremption.py`, couverte à 100 % et bien exercée en CI — ce
# saut ne laisse donc pas le mécanisme sans juge.
@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="`stat -f` (BSD) et `date -j` n'existent pas en GNU — script macOS-only",
)
class TestPeremptionDesDependances:
    """L'angle mort de tout ce qui précède : le code des DÉPENDANCES.

    Vécu le 2026-08-05. La PR #206 bumpe `openai` 2.41.1 → 2.53.0, pip réécrit
    `site-packages/openai/` sous `com.klody.api` qui tourne depuis 28 h, et
    l'app rend « cannot import name 'path_template' from 'openai._utils' » sur
    CHAQUE requête ~23 h plus tard. Aucun fichier du dépôt n'avait bougé : la
    section « code servi » était verte, et elle avait raison de l'être.
    """

    def test_date_les_dist_info(self, tmp_path):
        _venv_factice(tmp_path, 1_700_000_000)
        assert _mtime_dependances(tmp_path) == "1700000000"

    def test_prend_la_plus_recente(self, tmp_path):
        sp = _venv_factice(tmp_path, 1_000_000_000, paquets=("httpx-0.28.1",))
        d = sp / "openai-2.53.0.dist-info"
        d.mkdir()
        os.utime(d, (1_700_000_000, 1_700_000_000))
        assert _mtime_dependances(tmp_path) == "1700000000"

    def test_ignore_le_pycache_de_premier_niveau(self, tmp_path):
        """Le faux positif que le choix « dater les `*.dist-info`, pas le
        dossier » évite : la première compilation d'un module de premier niveau
        crée `site-packages/__pycache__/` et rajeunit le DOSSIER, sans qu'aucune
        dépendance n'ait bougé."""
        sp = _venv_factice(tmp_path, 1_700_000_000)
        (sp / "__pycache__").mkdir()
        os.utime(sp, (1_900_000_000, 1_900_000_000))
        assert _mtime_dependances(tmp_path) == "1700000000"

    def test_muet_sans_venv(self, tmp_path):
        """Sur un runner de CI il n'y a pas de `.venv` : la fonction doit
        rendre du vide, pas un `0` qui daterait tout de 1970 et déclarerait
        l'univers entier périmé."""
        assert _mtime_dependances(tmp_path) == ""

    def test_le_bloc_apparait_quand_un_demon_precede_le_pip_install(self, tmp_path):
        """Bout en bout : un dépôt de test complet, des services bouchonnés
        démarrés au boot (pid 1), et un `site-packages` écrit à l'instant."""
        depot = tmp_path / "depot"
        (depot / "scripts").mkdir(parents=True)
        (depot / "launchagents").mkdir()
        (depot / "scripts" / SCRIPT.name).write_text(SCRIPT.read_text())
        for src in AGENTS.glob("*.plist"):
            (depot / "launchagents" / src.name).write_text(src.read_text())
        _venv_factice(depot, int(time.time()))

        home = tmp_path / "home"
        path = _faux_launchctl(tmp_path / "bin")
        p = subprocess.run(
            ["/bin/sh", str(depot / "scripts" / SCRIPT.name), "--check"],
            capture_output=True, text=True, timeout=180, cwd=str(depot),
            env=dict(os.environ, HOME=str(home), PATH=path),
        )
        assert "DÉPENDANCES" in p.stdout, p.stdout
        assert "diagnostic_peremption.py" in p.stdout, (
            "le contrôle doit nommer l'outil qui, lui, désigne les services"
        )

    def test_le_bloc_se_tait_quand_le_venv_precede_les_demons(self, tmp_path):
        """La moitié qui prouve que le contrôle peut être VERT : mêmes services,
        mêmes plists, `site-packages` daté d'avant le démarrage. Sans ce test,
        un bloc affiché inconditionnellement passerait le précédent."""
        depot = tmp_path / "depot"
        (depot / "scripts").mkdir(parents=True)
        (depot / "launchagents").mkdir()
        (depot / "scripts" / SCRIPT.name).write_text(SCRIPT.read_text())
        for src in AGENTS.glob("*.plist"):
            (depot / "launchagents" / src.name).write_text(src.read_text())
        _venv_factice(depot, 1_000_000)  # 1970 + 11 jours : avant tout démarrage

        home = tmp_path / "home"
        path = _faux_launchctl(tmp_path / "bin")
        p = subprocess.run(
            ["/bin/sh", str(depot / "scripts" / SCRIPT.name), "--check"],
            capture_output=True, text=True, timeout=180, cwd=str(depot),
            env=dict(os.environ, HOME=str(home), PATH=path),
        )
        assert "DÉPENDANCES" not in p.stdout, p.stdout

    def test_n_influence_pas_le_code_de_sortie(self, home_synchronise):
        """Même contrat que le reste de la section : elle informe, elle ne
        juge pas. Le juge est `scripts/diagnostic_peremption.py`, qui nomme
        les services et rend trois codes de sortie distincts."""
        p = _lancer(home_synchronise)
        assert p.returncode == 0, p.stdout + p.stderr
