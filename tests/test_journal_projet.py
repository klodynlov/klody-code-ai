"""Chantier 4 (2026-11-27) : journal de projet, d'une session à l'autre.

Base SQLite RÉELLE dans un dossier jetable (`config.MEMORY_DIR` redirigé) : le
module la lit à l'appel, pas à l'import.
"""
from __future__ import annotations

from datetime import datetime

import config
import pytest
from tools import journal_projet as jp

JOUR = datetime(2026, 11, 27, 10, 0)


@pytest.fixture(autouse=True)
def _dossier(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path)


def _noter(projet="KaribTruck", type_="decision", texte="On garde SwiftData", **kw):
    return jp.noter(projet, type_, texte, maintenant=kw.pop("maintenant", JOUR), **kw)


class TestNoter:
    def test_note_et_numerote(self):
        assert _noter() == "Noté dans le journal de KaribTruck : décision #1."
        assert _noter(type_="etat", texte="TestFlight 0.1.0") == \
            "Noté dans le journal de KaribTruck : état #2."

    @pytest.mark.parametrize("alias,attendu", [("décision", "décision"), ("todo", "tâche"),
                                                ("bug", "incident"), ("État", "état")])
    def test_alias_de_type(self, alias, attendu):
        assert _noter(type_=alias).endswith(f"{attendu} #1.")

    def test_type_inconnu(self):
        assert _noter(type_="idée").startswith("Rien noté : `type`")
        assert not jp.chemin_base().exists()

    def test_projet_ou_texte_vide(self):
        assert "projet" in _noter(projet="  ")
        assert "texte" in _noter(texte="")
        assert not jp.chemin_base().exists()

    def test_texte_trop_long(self):
        assert "trop long" in _noter(texte="x" * (jp.TEXTE_MAX + 1))
        assert _noter(texte="x" * jp.TEXTE_MAX).startswith("Noté")

    def test_texte_aplati(self):
        _noter(texte="ligne 1\n\nligne 2")
        assert "ligne 1 ligne 2" in jp.lire("KaribTruck")

    def test_cle_normalisee_nom_le_plus_recent(self):
        _noter(projet="klody code")
        _noter(projet="Klody-Code", type_="etat", texte="PR #346")
        texte = jp.lire("KLODY CODE")
        assert texte.startswith("Journal de Klody-Code (2 entrée(s)) :")

    def test_orthographe_differente_deux_projets(self):
        _noter(projet="KaribTruck")
        _noter(projet="Karib Truck")
        assert jp.projets().count("\n- ") == 2

    def test_session_consignee(self):
        _noter(session="abc123")
        import sqlite3
        with sqlite3.connect(jp.chemin_base()) as cx:
            assert cx.execute("SELECT session FROM entrees").fetchone()[0] == "abc123"


class TestClore:
    def test_clore_une_tache(self):
        _noter(type_="tache", texte="Mesurer la marge")
        assert jp.clore(1, "4 suffit", maintenant=JOUR) == "Tâche #1 close (KaribTruck)."
        assert "✅ Mesurer la marge → 4 suffit" in jp.lire("KaribTruck", "tache")
        assert jp.clore("1") == "#1 était déjà close."

    def test_tache_close_quitte_les_ouvertes(self):
        _noter(type_="tache", texte="A")
        _noter(type_="tache", texte="B")
        jp.clore(1)
        synthese = jp.lire("KaribTruck")
        assert "☐ B" in synthese and "A" not in synthese.split("Tâches ouvertes")[1]

    def test_refus(self):
        assert jp.clore(1) == "Rien clos : le journal est vide."
        _noter()
        assert "pas une tâche" in jp.clore(1)
        assert jp.clore(9) == "Rien clos : pas d'entrée #9."
        assert "numéro" in jp.clore("#x")


class TestLire:
    def test_journal_vide_sans_creer_la_base(self):
        assert "vide" in jp.lire("X")
        assert "vide" in jp.projets()
        assert not jp.chemin_base().exists()

    def test_projet_inconnu_liste_les_connus(self):
        _noter(projet="KaribTruck")
        assert jp.lire("Libretto") == "Aucune entrée pour « Libretto ». Projets connus : KaribTruck."

    def test_synthese_ordonnee(self):
        _noter(type_="etat", texte="ancien état")
        _noter(type_="decision", texte="D1")
        _noter(type_="tache", texte="T1")
        _noter(type_="incident", texte="I1")
        _noter(type_="note", texte="N1")
        _noter(type_="etat", texte="état actuel", maintenant=datetime(2026, 11, 28))
        texte = jp.lire("karibtruck")
        assert texte.splitlines()[0] == "Journal de KaribTruck (6 entrée(s)) :"
        assert "État actuel :\n- #6 28/11/2026 état actuel" in texte
        assert "ancien état" not in texte
        blocs = [ligne for ligne in texte.splitlines() if ligne.endswith(" :")]
        assert blocs[1:] == ["État actuel :", "Tâches ouvertes :", "Décisions récentes :",
                             "Incidents récents :", "Notes récentes :"]

    def test_lire_par_type_et_nombre(self):
        for i in range(5):
            _noter(texte=f"D{i}")
        texte = jp.lire("KaribTruck", "decision", 2)
        assert texte.splitlines() == ["Journal de KaribTruck — décisions :",
                                      "- #5 27/11/2026 D4", "- #4 27/11/2026 D3"]
        assert "aucune entrée de type incident" in jp.lire("KaribTruck", "incident")
        assert "`type`" in jp.lire("KaribTruck", "idée")
        assert jp.lire("KaribTruck", "decision", "beaucoup").count("\n- ") == 5

    def test_projets(self):
        _noter(projet="A", type_="tache", texte="t")
        _noter(projet="B", maintenant=datetime(2026, 11, 28))
        lignes = jp.projets().splitlines()
        assert lignes[1] == "- B : 1 entrée(s), dernière le 28/11/2026, 0 tâche(s) ouverte(s)"
        assert lignes[2] == "- A : 1 entrée(s), dernière le 27/11/2026, 1 tâche(s) ouverte(s)"


class TestExecuter:
    def test_dispatch(self):
        assert jp.executer({"action": "noter", "projet": "P", "type": "etat",
                            "texte": "ok"}, session="s").startswith("Noté")
        assert jp.executer({"projet": "P"}).startswith("Journal de P")  # défaut : lire
        assert jp.executer({"action": "projets"}).startswith("Projets du journal")
        assert "Rien clos" in jp.executer({"action": "clore", "numero": 1})
        assert "inconnue" in jp.executer({"action": "effacer"})


class TestSectionTour:
    def test_rien_sans_base_ni_creation(self):
        assert jp.section_tour("on reprend KaribTruck") == ""
        assert not jp.chemin_base().exists()

    def test_projet_nomme_rappele(self):
        _noter(type_="etat", texte="TestFlight 0.1.0 (40)")
        _noter(type_="tache", texte="PR #14 à finir")
        section = jp.section_tour("On reprend karibtruck ?")
        assert section.startswith("\n\n## Journal de projet")
        assert "### KaribTruck" in section and "TestFlight" in section and "PR #14" in section
        assert "vérifie avant" in section

    def test_nom_compose_et_accents(self):
        _noter(projet="Suite Éveil", type_="etat", texte="PR #17 mergée")
        assert "PR #17" in jp.section_tour("où en est la suite eveil")
        assert jp.section_tour("la suite") == ""

    def test_mot_entier_seulement(self):
        _noter(projet="Klody", type_="etat", texte="x")
        assert jp.section_tour("klodynlov") == ""
        assert jp.section_tour("Klody, salut") != ""

    def test_au_plus_deux_projets_les_plus_recents(self):
        for nom in ("Alpha", "Beta", "Gamma"):
            _noter(projet=nom, type_="etat", texte=f"état {nom}")
        section = jp.section_tour("alpha beta gamma")
        assert section.count("### ") == 2 and "Alpha" not in section

    def test_bornee(self):
        for i in range(5):
            _noter(type_="tache", texte="t" * 900 + str(i))
        section = jp.section_tour("karibtruck")
        corps = section.split("\n", 3)[3]
        assert len(corps) < jp.SECTION_MAX + 120

    def test_base_illisible_ne_leve_pas(self):
        jp.chemin_base().write_bytes(b"pas une base sqlite" * 10)
        assert jp.section_tour("karibtruck") == ""


class TestMiroirFait:
    """Pont remember_fact (catégorie project) → journal : mesure du 27/11, Qwen
    note une décision avec remember_fact 3/3 et journal_projet 0/3."""

    FAIT = "Projet Atelier : on garde SQLite, pas Postgres, car tout tourne sur un seul Mac."

    def test_projet_nomme_devient_decision_datee(self):
        r = jp.miroir_fait("atelier_base_donnees", self.FAIT, session="s1", maintenant=JOUR)
        assert r == "Aussi daté dans le journal de Atelier : décision #1."
        assert "#1 27/11/2026 " + self.FAIT in jp.lire("Atelier")

    def test_sans_marque_de_decision_c_est_une_note(self):
        r = jp.miroir_fait("atelier_stack", "Le projet Atelier tourne sur macOS 27.",
                           maintenant=JOUR)
        assert r.endswith("note #1.")

    @pytest.mark.parametrize("texte,attendu", [
        ("Projet Atelier : base SQLite (pas Postgres), un seul Mac.", "décision"),
        ("Projet Atelier : SQLite, pas Postgres.", "décision"),
        ("Projet Atelier : SQLite et non Postgres.", "décision"),
        ("Projet Atelier : l'export MIDI ne marche pas encore.", "note"),
    ])
    def test_tournures_de_decision(self, texte, attendu):
        assert jp.miroir_fait("k", texte).endswith(f"{attendu} #1.")

    def test_nom_en_deux_mots_et_accent(self):
        assert "Klody Code" in jp.miroir_fait("k", "Le projet Klody Code utilise Qwen.")
        assert "Éveil" in jp.miroir_fait("e", "Projet Éveil : voix Supertonic retenue.")

    def test_variante_un_mot_deja_connue_gagne(self):
        _noter(projet="Atelier", texte="Démucs par défaut")
        r = jp.miroir_fait("x", "Projet Atelier SQLite retenu.")
        assert "de Atelier :" in r
        assert "Atelier SQLite" not in jp.projets()

    def test_projet_connu_retrouve_par_la_cle(self):
        _noter(projet="Atelier", texte="Démucs par défaut")
        r = jp.miroir_fait("atelier_base_donnees", "On garde SQLite, pas Postgres.")
        assert r == "Aussi daté dans le journal de Atelier : décision #2."

    def test_deux_projets_connus_ambigu_rien(self):
        _noter(projet="Atelier", texte="a")
        _noter(projet="KaribTruck", texte="b")
        assert jp.miroir_fait("atelier_karibtruck", "On garde SQLite partout.") == ""
        assert jp.lire("Atelier").count("\n- ") == 1

    def test_aucun_projet_nomme_rien_et_pas_de_base(self):
        assert jp.miroir_fait("projet_principal", "Le projet principal avance bien.") == ""
        assert not jp.chemin_base().exists()

    def test_meme_texte_deja_au_journal_rien(self):
        assert jp.miroir_fait("k", self.FAIT)
        assert jp.miroir_fait("k", self.FAIT) == ""
        assert "(1 entrée(s))" in jp.lire("Atelier")

    def test_decision_qui_change_laisse_deux_entrees(self):
        jp.miroir_fait("k", self.FAIT, maintenant=JOUR)
        jp.miroir_fait("k", "Projet Atelier : on passe à Postgres, plutôt que SQLite.",
                       maintenant=datetime(2026, 12, 3, 9, 0))
        texte = jp.lire("Atelier")
        assert "#1 27/11/2026" in texte and "#2 03/12/2026" in texte

    def test_texte_trop_long_tronque(self):
        r = jp.miroir_fait("k", "Projet Atelier : " + "x" * (jp.TEXTE_MAX + 50))
        assert r.endswith("note #1.")
        assert "…" in jp.lire("Atelier")

    def test_contenu_vide_rien(self):
        assert jp.miroir_fait("k", "   ") == ""

    def test_base_illisible_rien(self, monkeypatch):
        def panne(creer):
            raise jp.sqlite3.OperationalError("disk I/O error")
        monkeypatch.setattr(jp, "_connexion", panne)
        assert jp.miroir_fait("k", self.FAIT) == ""
