"""`scripts/diagnostic_peremption.py` : ses trois codes de sortie, et le
comportement de `--corriger`.

Un code de sortie documenté dans une docstring EST un réglage — et ce dépôt a
déjà payé le cas où un chiffre écrit de tête ne correspondait à rien (le tableau
de sensibilité de `bench/gate.py`, faux d'une unité sur toute une colonne
pendant une journée). D'où ces tests, case par case.

Trois codes et non deux, parce que « je n'ai pas pu juger » n'est pas « tout va
bien » : c'est le mode de défaillance dominant du dépôt.

⚠️ Le moteur (`agent.peremption.examiner_services`) est bouchonné ici : il a ses
propres tests dans `test_peremption_dependances.py`, contre de vraies sondes
`launchctl`/`ps` bouchonnées. Ce qui est jugé ICI, c'est la traduction
verdict → code de sortie → texte affiché.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from agent import peremption
from scripts import diagnostic_peremption as diag

REPO = Path(__file__).resolve().parent.parent


def _examens(*statuts: str) -> list[peremption.ServiceExamine]:
    return [
        peremption.ServiceExamine(
            f"com.klody.faux{i}", statut, "raison de test",
            pid=100 + i,
            remede=(peremption.commande_kickstart(f"com.klody.faux{i}", 501)
                    if statut == peremption.PERIMEES else None),
        )
        for i, statut in enumerate(statuts)
    ]


@pytest.fixture
def moteur(monkeypatch):
    """Remplace l'examen des services par une liste posée à la main."""
    def _poser(*statuts):
        monkeypatch.setattr(
            peremption, "examiner_services",
            lambda services, **kw: _examens(*statuts),
        )
    return _poser


class TestCodesDeSortie:
    def test_tout_a_jour_rend_zero(self, moteur, capsys):
        moteur(peremption.A_JOUR, peremption.A_JOUR)
        assert diag.main([]) == 0
        assert "servent le code de dépendances du disque" in capsys.readouterr().out

    def test_un_service_perime_rend_un(self, moteur, capsys):
        moteur(peremption.A_JOUR, peremption.PERIMEES)
        assert diag.main([]) == 1
        sortie = capsys.readouterr().out
        assert "1 service(s) périmé(s)" in sortie
        assert "launchctl kickstart -k gui/501/com.klody.faux1" in sortie, (
            "le remède doit être copiable tel quel"
        )

    def test_rien_de_jugeable_rend_deux(self, moteur, capsys):
        """Le code qui n'existait pas dans la première version de ce garde, et
        sans lequel « aucun agent chargé » se lirait « tout va bien »."""
        moteur(peremption.NON_JUGE, peremption.NON_JUGE)
        assert diag.main([]) == 2
        assert "PAS un feu vert" in capsys.readouterr().out

    def test_un_non_juge_n_efface_pas_un_perime(self, moteur):
        moteur(peremption.NON_JUGE, peremption.PERIMEES, peremption.A_JOUR)
        assert diag.main([]) == 1

    def test_sans_launchagent_versionne_rend_deux(self, monkeypatch, capsys):
        monkeypatch.setattr(peremption, "services_versionnes", lambda *a, **k: [])
        assert diag.main([]) == 2
        assert "rien à juger" in capsys.readouterr().out


class TestCorriger:
    def test_ne_relance_que_les_perimes(self, moteur, monkeypatch, capsys):
        """⚠️ `kickstart -k` TUE le process : une relance de trop coupe une
        session de travail. Le garde ne doit toucher que ce qu'il a jugé
        périmé."""
        appels: list[list[str]] = []

        class _Ok:
            returncode = 0
            stderr = ""

        monkeypatch.setattr(diag.subprocess, "run",
                            lambda argv, **kw: (appels.append(argv), _Ok())[1])
        moteur(peremption.A_JOUR, peremption.PERIMEES, peremption.NON_JUGE)

        assert diag.main(["--corriger"]) == 0
        assert appels == [
            ["launchctl", "kickstart", "-k", "gui/501/com.klody.faux1"]
        ], appels
        assert "Tous relancés" in capsys.readouterr().out

    def test_ne_touche_a_rien_sans_le_drapeau(self, moteur, monkeypatch):
        """Le défaut est INOFFENSIF : sans `--corriger`, aucun `launchctl`."""
        appels: list[list[str]] = []
        monkeypatch.setattr(diag.subprocess, "run",
                            lambda argv, **kw: appels.append(argv))
        moteur(peremption.PERIMEES)
        assert diag.main([]) == 1
        assert appels == []

    def test_une_relance_en_echec_reste_rouge(self, moteur, monkeypatch, capsys):
        """Un `kickstart` qui échoue ne doit pas rendre 0 : le service sert
        toujours du code périmé."""
        class _Ko:
            returncode = 3
            stderr = "Could not find service"

        monkeypatch.setattr(diag.subprocess, "run", lambda argv, **kw: _Ko())
        moteur(peremption.PERIMEES)
        assert diag.main(["--corriger"]) == 1
        assert "ÉCHEC (3)" in capsys.readouterr().out


class TestBoutEnBout:
    def test_s_execute_reellement_sur_cette_machine(self):
        """Aucune assertion sur le verdict — il dépend de l'état réel de la
        machine. Ce qui est vérifié, c'est que le script s'exécute de bout en
        bout avec de VRAIES sondes : une régression d'import, de chemin ou de
        signature ne serait pas rattrapée par les tests bouchonnés."""
        import subprocess as sp
        p = sp.run(
            [__import__("sys").executable, str(REPO / "scripts" / "diagnostic_peremption.py")],
            capture_output=True, text=True, timeout=120, cwd=str(REPO),
        )
        assert p.returncode in (0, 1, 2), f"code inattendu {p.returncode} : {p.stderr}"
        assert "Péremption des dépendances" in p.stdout
        assert "site-packages" in p.stdout
