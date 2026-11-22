"""Rappels avant rendez-vous et à l'échéance des Rappels (canaux/rendez_vous.py)."""

import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from canaux import proactif as pr, rendez_vous as rv


@pytest.fixture(autouse=True)
def _fuseau_paris(monkeypatch):
    """Données en +01:00, affichage en heure LOCALE : le runner CI est en UTC."""
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def maintenant():
    return datetime(2026, 11, 23, 8, 35)


def ev(titre="Dentiste", debut="2026-11-23T09:00:00+01:00", fin="2026-11-23T09:30:00+01:00",
       journee=False, lieu="Rennes\n12 rue X", id_="E1"):
    return {"id": id_, "titre": titre, "debut": debut, "fin": fin, "journee": journee, "lieu": lieu}


def rap(titre="Appeler Marc", echeance="2026-11-23T08:33:00+01:00", avec_heure=True, id_="R1"):
    return {"id": id_, "titre": titre, "echeance": echeance, "avec_heure": avec_heure}


def test_rendez_vous_dans_la_fenetre():
    a = rv.alertes_agenda({"evenements": [ev()]}, maintenant())
    assert [(x.cle, x.texte, x.hors_quota) for x in a] == [(
        "rdv:E1:2026-11-23T09:00", "📅 Dans 25 min : Dentiste\n09:00–09:30 · Rennes", True)]


@pytest.mark.parametrize("e", [
    ev(debut="2026-11-23T09:10:00+01:00"),                 # dans 35 min : trop tôt
    ev(debut="2026-11-23T08:30:00+01:00"),                 # déjà commencé
    ev(debut="2026-11-23T00:00:00+01:00", journee=True),   # journée : au briefing
    ev(debut="pas une date"),
])
def test_rendez_vous_hors_fenetre(e):
    assert rv.alertes_agenda({"evenements": [e]}, maintenant()) == []


def test_rendez_vous_deplace_rappele_a_sa_nouvelle_heure():
    avant = rv.alertes_agenda({"evenements": [ev()]}, maintenant())[0].cle
    apres = rv.alertes_agenda({"evenements": [ev(debut="2026-11-23T10:00:00+01:00")]},
                              datetime(2026, 11, 23, 9, 45))[0].cle
    assert avant != apres


def test_avance_reglable_et_sans_lieu_ni_fin():
    a = rv.alertes_agenda({"evenements": [ev(debut="2026-11-23T09:50:00+01:00", fin="", lieu="")]},
                          maintenant(), avance_min=90)
    assert a[0].texte == "📅 Dans 1 h 15 : Dentiste\n09:50"


def test_rappel_app_a_echeance():
    a = rv.alertes_agenda({"rappels": [rap()]}, maintenant())
    assert [(x.cle, x.texte, x.hors_quota) for x in a] == [
        ("rappel_app:R1:2026-11-23T08:33", "✅ Rappel : Appeler Marc (08:33)", True)]


@pytest.mark.parametrize("r", [
    rap(echeance="2026-11-23T08:40:00+01:00"),             # pas encore échu
    rap(echeance="2026-11-23T07:50:00+01:00"),             # échu depuis 45 min : au briefing
    rap(avec_heure=False),                                 # sans heure : au briefing
    rap(echeance=""),
])
def test_rappel_app_hors_fenetre(r):
    assert rv.alertes_agenda({"rappels": [r]}, maintenant()) == []


@pytest.mark.parametrize("minutes, texte", [(0.2, "1 min"), (25, "25 min"), (60, "1 h"),
                                            (75, "1 h 15"), (125, "2 h 05")])
def test_dans(minutes, texte):
    assert rv._dans(minutes) == texte


def test_source_interroge_la_coque_sur_deux_jours_avec_delai_court():
    vu = []

    def run(argv, timeout):
        vu.append((argv, timeout))
        return json.dumps({"evenements": [ev()], "rappels": [], "erreurs": []})

    src = rv.source_rendez_vous(Path("/K"), run)
    alertes = src(maintenant().timestamp())
    assert vu == [(["/K", "--agenda", "2"], rv.DELAI_AGENDA_S)]
    assert [a.cle for a in alertes] == ["rdv:E1:2026-11-23T09:00"]


def test_source_sans_coque_ou_en_panne(caplog):
    assert rv.source_rendez_vous(None)(0) == []

    def panne(argv, timeout):
        raise TimeoutError

    assert rv.source_rendez_vous(Path("/K"), panne)(maintenant().timestamp()) == []
    assert "agenda partiel" in caplog.text


def test_moteur_rappelle_une_seule_fois_meme_au_plafond(tmp_path):
    reg = pr.Registre(tmp_path / "p.db")
    envois = []
    src = rv.source_rendez_vous(Path("/K"), lambda a, t: json.dumps(
        {"evenements": [ev()], "rappels": [rap()]}))
    m = pr.Moteur(reg, envois.append, [pr.Source("rendez_vous", 0, src)], max_par_jour=0)
    t = maintenant().timestamp()
    assert m.tick(t) == 2
    assert m.tick(t + 120) == 0
    assert envois[0].startswith("📅 Dans 25 min") and envois[1].startswith("✅ Rappel")


def test_construire_moteur_ajoute_la_source(tmp_path):
    reg = pr.Registre(tmp_path / "p.db")
    m = pr.construire_moteur(reg, print, tmp_path, None, None, rendez_vous=lambda now: [])
    assert [s.nom for s in m.sources] == ["rappels", "taches", "rendez_vous"]
    assert m.sources[-1].periode_s == pr.PERIODE_RDV_S == 120.0
