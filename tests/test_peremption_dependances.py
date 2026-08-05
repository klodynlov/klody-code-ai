"""Le garde qui aurait nommé l'incident du 2026-08-05 en un coup d'œil.

Rappel de l'incident, parce que c'est lui qui dicte chaque assertion ici : la
PR #206 a bumpé `openai` 2.41.1 → 2.53.0 ; `com.klody.api` tournait depuis 28 h ;
pip a réécrit `site-packages/openai/` SOUS le process. `openai._utils` est resté
figé en 2.41.1 dans `sys.modules`, `openai/resources/*` a été lu neuf (import
paresseux via `_resources_proxy.py`), et l'app a rendu sur CHAQUE requête
« cannot import name 'path_template' from 'openai._utils' » — ~23 h après le
merge, avec un venv sain.

Ce que ces tests verrouillent, dans l'ordre d'importance :

1. le garde SAIT ROUGIR — une réécriture de `site-packages` sous le process
   produit `PERIMEES`, pas une note ;
2. il sait dire « je n'ai pas pu juger », distinct de « tout va bien » — le
   mode de défaillance dominant du dépôt est le garde-fou incapable d'échouer ;
3. il ne rougit PAS sur un venv stable, y compris quand un `__pycache__` de
   premier niveau apparaît après le démarrage (le faux positif que le choix de
   dater les `*.dist-info` plutôt que le dossier `site-packages` évite).
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from agent import peremption


def _site_packages(racine: Path, paquets: dict[str, str]) -> Path:
    """Un faux `site-packages` : un `*.dist-info` par paquet, comme pip."""
    sp = racine / "site-packages"
    sp.mkdir(exist_ok=True)
    for nom, version in paquets.items():
        (sp / f"{nom}-{version}.dist-info").mkdir(exist_ok=True)
    return sp


def _dater(chemin: Path, horodatage: float) -> None:
    import os
    os.utime(chemin, (horodatage, horodatage))


# ── Empreinte ─────────────────────────────────────────────────────────────────

class TestEmpreinte:
    def test_date_les_dist_info_et_les_compte(self, tmp_path):
        sp = _site_packages(tmp_path, {"openai": "2.41.1", "httpx": "0.28.1"})
        _dater(sp / "openai-2.41.1.dist-info", 1_000_000)
        _dater(sp / "httpx-0.28.1.dist-info", 2_000_000)

        e = peremption.empreinte_disque([sp])
        assert e.nb_paquets == 2
        assert e.horodatage == 2_000_000
        assert e.mesurable

    def test_un_dossier_sans_dist_info_n_est_pas_mesurable(self, tmp_path):
        """« Rien à dater » doit rester distinct de « daté, tout va bien »."""
        vide = tmp_path / "site-packages"
        vide.mkdir()
        e = peremption.empreinte_disque([vide])
        assert not e.mesurable
        assert e.horodatage is None
        assert e.nb_paquets == 0

    def test_le_pycache_de_premier_niveau_ne_bouge_pas_l_empreinte(self, tmp_path):
        """Le faux positif évité par construction.

        La première compilation d'un module de premier niveau crée
        `site-packages/__pycache__/`, ce qui rajeunit le DOSSIER sans qu'aucune
        dépendance n'ait bougé. Dater le dossier ferait rougir le garde sur un
        venv parfaitement stable, quelques secondes après le démarrage.
        """
        sp = _site_packages(tmp_path, {"openai": "2.41.1"})
        avant = peremption.empreinte_disque([sp])

        (sp / "__pycache__").mkdir()
        (sp / "__pycache__" / "six.cpython-311.pyc").write_bytes(b"\x00")

        apres = peremption.empreinte_disque([sp])
        assert (apres.horodatage, apres.nb_paquets) == (avant.horodatage, avant.nb_paquets)

    def test_resume_est_serialisable(self, tmp_path):
        sp = _site_packages(tmp_path, {"openai": "2.53.0"})
        r = peremption.empreinte_disque([sp]).resume()
        assert set(r) == {"horodatage", "iso", "nb_paquets", "racines"}
        assert r["nb_paquets"] == 1
        assert isinstance(r["iso"], str)

    def test_racines_reelles_du_venv_courant(self):
        """L'instrument doit fonctionner sur l'interpréteur qui exécute les
        tests, pas seulement sur des fixtures — sinon il ne mesure que
        lui-même."""
        racines = peremption.racines_site_packages()
        assert racines, "aucun site-packages résolu pour cet interpréteur"
        assert all(r.is_dir() for r in racines)


# ── Vu de l'intérieur ─────────────────────────────────────────────────────────

class TestEtatProcess:
    def test_venv_stable_est_a_jour(self, tmp_path):
        sp = _site_packages(tmp_path, {"openai": "2.41.1"})
        e = peremption.empreinte_disque([sp])
        etat = peremption.etat_process(demarrage=e, disque=e)
        assert etat["statut"] == peremption.A_JOUR
        assert etat["remede"] is None

    def test_le_bump_sous_le_process_rougit(self, tmp_path):
        """Le scénario exact du 2026-08-05, rejoué : pip remplace le dist-info
        d'openai pendant que le process tourne."""
        sp = _site_packages(tmp_path, {"openai": "2.41.1", "httpx": "0.28.1"})
        _dater(sp / "openai-2.41.1.dist-info", 1_000_000)
        _dater(sp / "httpx-0.28.1.dist-info", 1_000_000)
        au_demarrage = peremption.empreinte_disque([sp])

        (sp / "openai-2.41.1.dist-info").rmdir()
        (sp / "openai-2.53.0.dist-info").mkdir()
        _dater(sp / "openai-2.53.0.dist-info", 9_000_000)

        etat = peremption.etat_process(
            demarrage=au_demarrage,
            disque=peremption.empreinte_disque([sp]),
            label_service="com.klody.api",
        )
        assert etat["statut"] == peremption.PERIMEES
        assert "path_template" in str(etat["raison"]), "le remède doit citer l'incident"
        assert etat["remede"] == peremption.commande_kickstart("com.klody.api")

    def test_une_desinstallation_seche_rougit_aussi(self, tmp_path):
        """`pip uninstall` n'écrit rien : la mtime max ne bouge pas. Sans le
        second axe (le NOMBRE de paquets), ce cas passerait au vert."""
        sp = _site_packages(tmp_path, {"openai": "2.41.1", "httpx": "0.28.1"})
        _dater(sp / "openai-2.41.1.dist-info", 5_000_000)
        _dater(sp / "httpx-0.28.1.dist-info", 1_000_000)
        au_demarrage = peremption.empreinte_disque([sp])

        (sp / "httpx-0.28.1.dist-info").rmdir()
        apres = peremption.empreinte_disque([sp])
        assert apres.horodatage == au_demarrage.horodatage, "la mtime max n'a pas bougé"

        etat = peremption.etat_process(demarrage=au_demarrage, disque=apres)
        assert etat["statut"] == peremption.PERIMEES

    def test_un_downgrade_rougit(self, tmp_path):
        """Comparer des EMPREINTES et non des dates : un `pip install
        openai==2.41.1` repose une mtime plus ANCIENNE, qu'un simple
        « disque plus récent que le démarrage ? » laisserait passer."""
        sp = _site_packages(tmp_path, {"openai": "2.53.0"})
        _dater(sp / "openai-2.53.0.dist-info", 9_000_000)
        au_demarrage = peremption.empreinte_disque([sp])

        (sp / "openai-2.53.0.dist-info").rmdir()
        (sp / "openai-2.41.1.dist-info").mkdir()
        _dater(sp / "openai-2.41.1.dist-info", 1_000_000)

        etat = peremption.etat_process(
            demarrage=au_demarrage, disque=peremption.empreinte_disque([sp])
        )
        assert etat["statut"] == peremption.PERIMEES

    def test_sans_site_packages_le_verdict_est_non_juge(self, tmp_path):
        """⚠️ La propriété centrale du dépôt : un garde qui ne peut pas juger
        ne doit surtout pas rendre « à jour »."""
        vide = peremption.empreinte_disque([tmp_path / "absent"])
        etat = peremption.etat_process(demarrage=vide, disque=vide)
        assert etat["statut"] == peremption.NON_JUGE
        assert etat["statut"] != peremption.A_JOUR
        assert "NON JUGÉE" in str(etat["raison"])

    def test_defaut_utilise_le_venv_reel_sans_lever(self):
        """Appel nu, tel que `/health` le fait : il doit rendre un verdict sur
        l'interpréteur courant sans argument ni exception."""
        etat = peremption.etat_process(label_service="com.klody.api")
        assert etat["statut"] in {
            peremption.A_JOUR, peremption.PERIMEES, peremption.NON_JUGE,
        }


class TestEmpreinteDemarrage:
    def test_prise_a_l_import(self):
        """Si cette constante cessait d'être calculée à l'import, la référence
        daterait du premier appel — et le garde ne verrait plus jamais rien."""
        assert isinstance(peremption.EMPREINTE_DEMARRAGE, peremption.Empreinte)
        assert peremption.EMPREINTE_DEMARRAGE.mesurable, (
            "le venv qui exécute les tests doit être datable"
        )


# ── Conversion de dates ───────────────────────────────────────────────────────

class TestLstart:
    def test_format_reel_de_ps(self):
        """Format relevé sur la machine : `ps -o lstart= -p 49575` rend
        « Wed Aug  5 04:59:35 2026 » (double espace avant un jour à un chiffre)."""
        e = peremption.epoch_depuis_lstart("Wed Aug  5 04:59:35 2026")
        assert e == datetime(2026, 8, 5, 4, 59, 35).timestamp()

    def test_jour_a_deux_chiffres(self):
        e = peremption.epoch_depuis_lstart("Mon Aug 24 01:36:02 2026\n")
        assert e == datetime(2026, 8, 24, 1, 36, 2).timestamp()

    @pytest.mark.parametrize("brut", ["", "\n", "ps: no such process", "Wed Zzz 5 04:59:35 2026"])
    def test_illisible_rend_none(self, brut):
        assert peremption.epoch_depuis_lstart(brut) is None

    def test_sur_le_process_courant(self):
        """Contrôle de bout en bout contre le vrai `ps` : une regex qui
        cesserait de coller au format de la machine rendrait « non jugé »
        partout, en silence."""
        import os
        brut = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(os.getpid())],
            capture_output=True, text=True, check=False,
        ).stdout
        if not brut.strip():
            pytest.skip("`ps -o lstart=` indisponible sur cette plateforme")
        assert peremption.epoch_depuis_lstart(brut) is not None, (
            f"format non reconnu : {brut!r}"
        )


# ── Vu de l'extérieur ─────────────────────────────────────────────────────────

def _plist(dossier: Path, label: str, periodique: bool = False) -> Path:
    dossier.mkdir(parents=True, exist_ok=True)
    corps = "<key>StartInterval</key><integer>60</integer>" if periodique else ""
    chemin = dossier / f"{label}.plist"
    chemin.write_text(f"<plist><dict><key>Label</key><string>{label}</string>{corps}"
                      "</dict></plist>")
    return chemin


def _sondes(*, charges: set[str], pids: dict[str, int], lstart: dict[int, str]):
    """Un `launchctl` / `ps` bouchonnés — on ne va pas arrêter de vrais
    services pour éprouver le contrôle : couper l'API coûterait la session."""
    def _lancer(argv):
        if argv[0] == "launchctl":
            label = argv[2].rsplit("/", 1)[-1]
            if label not in charges:
                return 1, ""
            pid = pids.get(label)
            if pid is None:
                return 0, "\tstate = waiting\n"
            return 0, f"\tstate = running\n\tpid = {pid}\n"
        if argv[0] == "ps":
            return 0, lstart.get(int(argv[-1]), "")
        raise AssertionError(f"sonde inattendue : {argv}")  # pragma: no cover
    return _lancer


class TestExaminerServices:
    def _empreinte(self, tmp_path, horodatage: float):
        sp = _site_packages(tmp_path, {"openai": "2.53.0"})
        _dater(sp / "openai-2.53.0.dist-info", horodatage)
        return peremption.empreinte_disque([sp])

    def test_demarre_avant_le_pip_install_est_perime(self, tmp_path):
        """L'incident, vu du dehors : l'API démarrée le 2026-08-03 01:36, pip
        passé le 2026-08-04 05:27."""
        plists = tmp_path / "agents"
        service = _plist(plists, "com.klody.api")
        empreinte = self._empreinte(tmp_path, datetime(2026, 8, 4, 5, 27, 36).timestamp())

        # ⚠️ Date écrite en dur, jamais via `strftime` : ses noms de mois
        # suivent la locale, et sous `LC_TIME=fr_FR` la fixture cesserait de
        # ressembler à ce que `ps` produit — le test verdirait sur autre chose.
        examens = peremption.examiner_services(
            [("com.klody.api", service)],
            empreinte=empreinte,
            lancer=_sondes(charges={"com.klody.api"}, pids={"com.klody.api": 49575},
                           lstart={49575: "Mon Aug  3 01:36:02 2026"}),
            uid=501,
        )
        assert [e.statut for e in examens] == [peremption.PERIMEES]
        assert examens[0].pid == 49575
        assert examens[0].remede == "launchctl kickstart -k gui/501/com.klody.api"

    def test_demarre_apres_le_pip_install_est_a_jour(self, tmp_path):
        plists = tmp_path / "agents"
        service = _plist(plists, "com.klody.klody-mcp")
        empreinte = self._empreinte(tmp_path, datetime(2026, 8, 4, 5, 27, 36).timestamp())

        examens = peremption.examiner_services(
            [("com.klody.klody-mcp", service)],
            empreinte=empreinte,
            lancer=_sondes(charges={"com.klody.klody-mcp"},
                           pids={"com.klody.klody-mcp": 13499},
                           lstart={13499: "Tue Aug  4 05:41:10 2026"}),
            uid=501,
        )
        assert [e.statut for e in examens] == [peremption.A_JOUR]
        assert examens[0].remede is None

    def test_un_periodique_n_est_jamais_perime(self, tmp_path):
        """`com.klody.api-watchdog` a un `StartInterval` : il ré-exécute son
        programme à chaque tick et repart donc toujours du disque. Le déclarer
        périmé serait faux, et un contrôle toujours rouge cesse d'être lu."""
        plists = tmp_path / "agents"
        service = _plist(plists, "com.klody.api-watchdog", periodique=True)
        empreinte = self._empreinte(tmp_path, datetime(2026, 8, 4, 5, 27, 36).timestamp())

        examens = peremption.examiner_services(
            [("com.klody.api-watchdog", service)],
            empreinte=empreinte,
            lancer=_sondes(charges={"com.klody.api-watchdog"},
                           pids={"com.klody.api-watchdog": 1},
                           lstart={1: "Mon Jan  1 00:00:00 2020"}),
            uid=501,
        )
        assert [e.statut for e in examens] == [peremption.A_JOUR]

    def test_service_absent_de_launchd_est_non_juge(self, tmp_path):
        service = _plist(tmp_path / "agents", "com.klody.gadget-mcp")
        examens = peremption.examiner_services(
            [("com.klody.gadget-mcp", service)],
            empreinte=self._empreinte(tmp_path, 9_000_000),
            lancer=_sondes(charges=set(), pids={}, lstart={}), uid=501,
        )
        assert [e.statut for e in examens] == [peremption.NON_JUGE]
        assert examens[0].pid is None

    def test_charge_sans_processus_est_non_juge(self, tmp_path):
        """« Chargé » ≠ « a un processus » — la confusion avait déjà fait
        déclarer `com.klody.api-watchdog` absent alors qu'il tournait."""
        service = _plist(tmp_path / "agents", "com.klody.vlc-mcp")
        examens = peremption.examiner_services(
            [("com.klody.vlc-mcp", service)],
            empreinte=self._empreinte(tmp_path, 9_000_000),
            lancer=_sondes(charges={"com.klody.vlc-mcp"}, pids={}, lstart={}), uid=501,
        )
        assert [e.statut for e in examens] == [peremption.NON_JUGE]

    def test_date_de_demarrage_illisible_est_non_juge(self, tmp_path):
        service = _plist(tmp_path / "agents", "com.klody.web-mcp")
        examens = peremption.examiner_services(
            [("com.klody.web-mcp", service)],
            empreinte=self._empreinte(tmp_path, 9_000_000),
            lancer=_sondes(charges={"com.klody.web-mcp"}, pids={"com.klody.web-mcp": 42},
                           lstart={}), uid=501,
        )
        assert [e.statut for e in examens] == [peremption.NON_JUGE]
        assert examens[0].pid == 42

    def test_sans_site_packages_datable_rien_n_est_juge(self, tmp_path):
        service = _plist(tmp_path / "agents", "com.klody.api")
        examens = peremption.examiner_services(
            [("com.klody.api", service)],
            empreinte=peremption.empreinte_disque([tmp_path / "nulle-part"]),
            lancer=_sondes(charges={"com.klody.api"}, pids={"com.klody.api": 7},
                           lstart={7: "Wed Aug  5 04:59:35 2026"}), uid=501,
        )
        assert [e.statut for e in examens] == [peremption.NON_JUGE]

    def test_les_agents_versionnes_sont_tous_examinables(self):
        """Le garde doit couvrir TOUS les `com.klody.*`, pas seulement l'API :
        les serveurs MCP n'ont aucun `/health`, c'est leur seul angle."""
        services = peremption.services_versionnes()
        labels = {label for label, _ in services}
        assert "com.klody.api" in labels
        assert len([lbl for lbl in labels if lbl.endswith("-mcp")]) >= 5


class TestSondesReelles:
    """Les sondes elles-mêmes, contre le vrai système.

    Bouchonner `lancer` dans tous les tests laisserait `_lancer` — le seul
    chemin utilisé en production — jamais exécuté. Un garde dont la sonde n'est
    testée qu'en double est un garde dont on ne sait pas s'il sonde.
    """

    def test_lancer_rend_le_code_et_la_sortie(self):
        """Contrat de `_lancer` : un code et une sortie. Le FORMAT de cette
        sortie est le contrat d'`epoch_depuis_lstart`, éprouvé séparément
        (`TestLstart`) — les mélanger ferait rougir la sonde pour un défaut
        de conversion, et l'inverse."""
        import os
        rc, sortie = peremption._lancer(["ps", "-o", "lstart=", "-p", str(os.getpid())])
        assert rc == 0
        assert sortie.strip()

    def test_un_binaire_absent_ne_leve_pas(self):
        """`launchctl` n'existe pas hors macOS : la sonde doit rendre « rien »,
        pas faire tomber le diagnostic entier."""
        rc, sortie = peremption._lancer(["klody-binaire-qui-n-existe-pas"])
        assert rc == 127
        assert sortie == ""

    def test_plist_illisible_n_est_pas_periodique(self, tmp_path):
        """Faute de pouvoir lire, on ne DÉCLARE pas périodique : ce serait
        écarter le service du contrôle sur une lecture ratée."""
        assert peremption.est_periodique(tmp_path / "absent.plist") is False

    def test_plist_avec_startinterval(self, tmp_path):
        p = tmp_path / "x.plist"
        p.write_text("<plist><dict><key>StartInterval</key><integer>60</integer>"
                     "</dict></plist>")
        assert peremption.est_periodique(p) is True


class TestVerdict:
    def _ex(self, statut):
        return peremption.ServiceExamine("x", statut, "")

    def test_un_seul_perime_suffit(self):
        assert peremption.verdict([
            self._ex(peremption.A_JOUR),
            self._ex(peremption.PERIMEES),
            self._ex(peremption.NON_JUGE),
        ]) == peremption.PERIMEES

    def test_tout_non_juge_ne_devient_pas_a_jour(self):
        """⚠️ Le piège que ce dépôt paie cher : agréger « je n'ai rien pu
        juger » en « tout va bien »."""
        assert peremption.verdict(
            [self._ex(peremption.NON_JUGE)] * 3
        ) == peremption.NON_JUGE

    def test_liste_vide_est_non_jugee(self):
        assert peremption.verdict([]) == peremption.NON_JUGE

    def test_a_jour_survit_a_un_non_juge(self):
        assert peremption.verdict([
            self._ex(peremption.A_JOUR), self._ex(peremption.NON_JUGE),
        ]) == peremption.A_JOUR
