"""Route de l'app Apple Watch (`api/montre.py`) : jeton, contrat, lecture seule.

L'app Watch/iPhone de KlodyPocket est écrite CONTRE ce contrat, en parallèle :
chaque route est jouée sur l'app RÉELLE (garde d'origine compris), agenda,
météo, mails et orchestrateur bouchonnés. Aucune écriture hors du dossier
d'état jetable du test (`tests/garde_etat.py` le fait rougir sinon), aucun
appel réseau, aucune lecture d'agenda (TCC l'attribuerait à Claude.app).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar

import config
import pytest
from api import montre
from fastapi.testclient import TestClient

from tests import garde_etat

LOCAL = "http://127.0.0.1:8000"
JETON = "test-montre-" + "x" * 40  # valeur de test, jamais le vrai .env
AUTH = {"Authorization": f"Bearer {JETON}"}
# Jeudi 26/11/2026, 10:00 heure locale
MAINTENANT = datetime(2026, 11, 26, 10, 0).astimezone()

ROUTES = [
    ("GET", "/api/montre/etat", None),
    ("GET", "/api/montre/agenda", None),
    ("GET", "/api/montre/meteo", None),
    ("GET", "/api/montre/briefing", None),
    ("POST", "/api/montre/briefing", {"moment": "matin"}),
    ("POST", "/api/montre/demande", {"texte": "bonjour"}),
    ("POST", "/api/montre/note", {"texte": "acheter du pain"}),
    ("POST", "/api/montre/rappel", {"texte": "garage", "quand": "2026-11-27T09:00"}),
]


def _iso(j: int, h: int, mn: int = 0) -> str:
    return (MAINTENANT.replace(hour=h, minute=mn) + timedelta(days=j)).isoformat()


AGENDA = {
    "evenements": [
        {"titre": "Petit-déj", "debut": _iso(0, 8), "fin": _iso(0, 9)},  # passé
        {"titre": "Férié", "debut": _iso(0, 0), "fin": _iso(1, 0), "journee": True},
        {"titre": "Dentiste", "debut": _iso(0, 14, 30), "fin": _iso(0, 15),
         "lieu": "12 rue de Rennes\n35000 Rennes"},
        {"titre": "Garage", "debut": _iso(1, 9), "fin": _iso(1, 10)},
        {"titre": "Concert", "debut": _iso(3, 20), "fin": _iso(3, 23)},
    ],
    "rappels": [
        {"titre": "Payer la cantine", "echeance": _iso(-1, 0)},
        {"titre": "Rendre les livres"},
        {"titre": "Vacances", "echeance": _iso(20, 0)},
    ],
    "erreurs": [],
}


def _faux_get(url: str) -> bytes:
    if "geocoding-api" in url:
        return json.dumps({"results": [{"name": "Rennes", "admin1": "Bretagne",
                                        "latitude": 48.1, "longitude": -1.68}]}).encode()
    assert "api.open-meteo.com/v1/forecast" in url, url
    n = 7
    return json.dumps({"daily": {
        "weather_code": [3] * n, "temperature_2m_min": [4.0] * n,
        "temperature_2m_max": [11.0] * n, "precipitation_probability_max": [60] * n,
        "precipitation_sum": [2.5] * n, "wind_speed_10m_max": [20] * n}}).encode()


@pytest.fixture
def lectures(monkeypatch):
    appels: list[int] = []

    def lire(jours: int) -> dict:
        appels.append(jours)
        return AGENDA

    montre.vider_cache()
    monkeypatch.setattr(montre, "LIRE_AGENDA", lire)
    monkeypatch.setattr(montre, "HTTP_GET", _faux_get)
    monkeypatch.setattr(montre, "maintenant", lambda: MAINTENANT)
    monkeypatch.setattr(montre, "chercheur_mails",
                        lambda: (lambda q, n: [{"uid": "1"}, {"uid": "2"}]))
    yield appels
    montre.vider_cache()


@pytest.fixture
def client(monkeypatch, tmp_path, lectures):
    import agent.long_term_memory as ltm

    monkeypatch.setattr(ltm, "_STORAGE", tmp_path / "long_term.json")
    monkeypatch.setattr(ltm, "_instance", None)
    monkeypatch.setattr("services.ensure_librarybrain", lambda *_a, **_kw: True)
    monkeypatch.setenv(montre.ENV_JETON, JETON)
    import api.server as srv

    c = TestClient(srv.app, base_url=LOCAL)
    c.lectures = lectures  # type: ignore[attr-defined]
    return c


def _appel(c, methode, chemin, corps=None, headers=AUTH):
    if methode == "GET":
        return c.get(chemin, headers=headers)
    return c.post(chemin, json=corps, headers=headers)


# ── Jeton ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("methode", "chemin", "corps"), ROUTES)
def test_jeton_absent_du_env_toutes_les_routes_503(client, monkeypatch, methode, chemin, corps):
    monkeypatch.delenv(montre.ENV_JETON, raising=False)
    r = _appel(client, methode, chemin, corps)
    assert r.status_code == 503
    assert "non configurée" in r.json()["erreur"]
    assert montre.ENV_JETON in r.json()["erreur"]


def test_jeton_trop_court_refuse_de_servir(client, monkeypatch):
    monkeypatch.setenv(montre.ENV_JETON, "court")
    r = client.get("/api/montre/etat", headers={"Authorization": "Bearer court"})
    assert r.status_code == 503
    assert "trop court" in r.json()["erreur"]


@pytest.mark.parametrize(("methode", "chemin", "corps"), ROUTES)
@pytest.mark.parametrize("entete", [
    None, "Bearer faux", f"Basic {JETON}", f"Bearer {JETON}x", "Bearer ", b"Bearer \xe9\xe9"])
def test_jeton_faux_ou_absent_401(client, methode, chemin, corps, entete):
    headers = {} if entete is None else {"Authorization": entete}
    r = _appel(client, methode, chemin, corps, headers=headers)
    assert r.status_code == 401
    assert r.json() == {"erreur": "jeton absent ou invalide"}


def test_jeton_verifie_avant_le_corps(client):
    """Un inconnu reçoit 401, jamais un 400/422 qui décrirait la route."""
    r = client.post("/api/montre/demande", content=b"{pas du json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 401


def test_bon_jeton_insensible_a_la_casse_du_schema(client):
    r = client.get("/api/montre/etat", headers={"Authorization": f"bearer {JETON}"})
    assert r.status_code == 200


def test_verifier_jeton_pur():
    montre.verifier_jeton(f"Bearer {JETON}", JETON)
    for mauvais in (None, "", "Bearer", "Bearer ñ", JETON):
        with pytest.raises(montre.ErreurMontre) as e:
            montre.verifier_jeton(mauvais, JETON)
        assert e.value.statut == 401
    with pytest.raises(montre.ErreurMontre) as e:
        montre.verifier_jeton(f"Bearer {JETON}", "  ")
    assert e.value.statut == 503


# ── Garde d'origine : le proxy Tailscale garde son Host ──────────────────────

def test_hote_tailnet_admis_sur_la_route_montre_seulement(client):
    hote = {"Host": "macbook-pro-de-claude-2.tailfc1294.ts.net"}
    assert client.get("/api/montre/etat", headers={**AUTH, **hote}).status_code == 200
    # Le reste de l'API reste fermé à tout hôte non local.
    assert client.get("/api/status", headers=hote).status_code == 403
    assert client.post("/api/siri", json={"query": "x"}, headers=hote).status_code == 403
    # Un nom qui ne finit pas par .ts.net : refusé, même sur la route montre.
    r = client.get("/api/montre/etat", headers={**AUTH, "Host": "evil.example"})
    assert r.status_code == 403
    r = client.get("/api/montre/etat", headers={**AUTH, "Host": "ts.net.evil.example"})
    assert r.status_code == 403


def test_origine_tierce_refusee_meme_avec_jeton(client):
    r = client.get("/api/montre/etat", headers={**AUTH, "Origin": "https://evil.example"})
    assert r.status_code == 403


# ── /etat ────────────────────────────────────────────────────────────────────

def test_etat_prochain_rdv_horaire_a_venir(client):
    r = client.get("/api/montre/etat", headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["version"] == 1
    assert d["prochain_rdv"] == {
        "titre": "Dentiste", "debut": _iso(0, 14, 30)[:19] + d["prochain_rdv"]["debut"][19:],
        "fin": d["prochain_rdv"]["fin"], "lieu": "12 rue de Rennes"}
    assert datetime.fromisoformat(d["prochain_rdv"]["debut"]) == datetime.fromisoformat(
        _iso(0, 14, 30))
    assert datetime.fromisoformat(d["prochain_rdv"]["fin"]) == datetime.fromisoformat(_iso(0, 15))
    assert datetime.fromisoformat(d["maj"]) == MAINTENANT
    assert d["erreurs"] == []
    assert client.lectures == [2]  # aujourd'hui + demain


def test_etat_demain_si_plus_rien_aujourdhui(client, monkeypatch):
    monkeypatch.setattr(montre, "maintenant", lambda: MAINTENANT.replace(hour=18))
    d = client.get("/api/montre/etat", headers=AUTH).json()
    assert d["prochain_rdv"]["titre"] == "Garage"
    assert d["prochain_rdv"]["lieu"] is None


def test_etat_rien_au_dela_de_demain_et_erreurs_nommees(client, monkeypatch):
    monkeypatch.setattr(montre, "LIRE_AGENDA", lambda j: {
        "evenements": [AGENDA["evenements"][-1]], "rappels": [],
        "erreurs": ["la coque n'a rien écrit (accès Calendriers/Rappels refusé ?)"]})
    d = client.get("/api/montre/etat", headers=AUTH).json()
    assert d["ok"] is True
    assert d["prochain_rdv"] is None
    assert "accès Calendriers" in d["erreurs"][0]


def test_agenda_garde_un_instantane_partage(client):
    for _ in range(3):
        assert client.get("/api/montre/etat", headers=AUTH).status_code == 200
    assert client.lectures == [2]  # une seule coque lancée pour trois sondages


def test_instantane_perime_relu(client, monkeypatch):
    client.get("/api/montre/etat", headers=AUTH)
    monkeypatch.setattr(montre, "CACHE_AGENDA_S", 0.0)
    client.get("/api/montre/etat", headers=AUTH)
    assert client.lectures == [2, 2]


# ── /agenda ──────────────────────────────────────────────────────────────────

def test_agenda_aujourdhui_par_defaut(client):
    d = client.get("/api/montre/agenda", headers=AUTH).json()
    assert set(d) == {"texte", "evenements", "rappels"}
    assert "📅 jeudi 26/11" in d["texte"]
    assert "14:30–15:00 Dentiste" in d["texte"]
    assert [e["titre"] for e in d["evenements"]] == ["Petit-déj", "Férié", "Dentiste"]
    # rappels non faits : en retard + sans date ; pas celui dans 20 jours
    assert [r["titre"] for r in d["rappels"]] == ["Payer la cantine", "Rendre les livres"]
    assert client.lectures == [1]


def test_agenda_a_partir_de_demain(client):
    d = client.get("/api/montre/agenda?jours=3&a_partir_de=demain", headers=AUTH).json()
    assert [e["titre"] for e in d["evenements"]] == ["Garage", "Concert"]
    assert "📅 vendredi 27/11" in d["texte"] and "📅 jeudi 26/11" not in d["texte"]
    assert client.lectures == [4]  # décalage + jours


@pytest.mark.parametrize("requete", [
    "jours=0", "jours=15", "jours=abc", "a_partir_de=hier", "jours=1.5"])
def test_agenda_parametres_refuses_nommes(client, requete):
    r = client.get(f"/api/montre/agenda?{requete}", headers=AUTH)
    assert r.status_code == 400
    assert "erreur" in r.json() and "detail" not in r.json()


def test_agenda_apostrophe_typographique(client):
    r = client.get("/api/montre/agenda?a_partir_de=aujourd’hui", headers=AUTH)
    assert r.status_code == 200


def test_panne_interne_nommee_jamais_un_500_nu(client, monkeypatch):
    def panne(_j):
        raise RuntimeError("coque en vrac")

    monkeypatch.setattr(montre, "LIRE_AGENDA", panne)
    r = client.get("/api/montre/agenda", headers=AUTH)
    assert r.status_code == 500
    assert r.json() == {"erreur": "erreur interne (RuntimeError)"}


# ── /meteo ───────────────────────────────────────────────────────────────────

def test_meteo_par_defaut_baulon_le_rheu(client):
    d = client.get("/api/montre/meteo", headers=AUTH).json()
    assert "Baulon" in d["texte"] and "Le Rheu" in d["texte"]
    assert "demain" in d["texte"]  # jours=2 par défaut


def test_meteo_ville_nommee(client):
    d = client.get("/api/montre/meteo?lieu=Rennes&jours=1", headers=AUTH).json()
    assert "Rennes (Bretagne)" in d["texte"] and "demain" not in d["texte"]


@pytest.mark.parametrize("requete", [
    "lieu=https://evil.example/x", "lieu=Rennes%3B%20cat%20~%2F.ssh", "lieu=" + "a" * 61,
    "jours=8", "jours=0"])
def test_meteo_refus_nommes(client, requete):
    r = client.get(f"/api/montre/meteo?{requete}", headers=AUTH)
    assert r.status_code == 400
    assert "erreur" in r.json()


# ── /briefing ────────────────────────────────────────────────────────────────

def test_briefing_express_trois_messages_sans_modele(client):
    d = client.get("/api/montre/briefing?moment=matin", headers=AUTH).json()
    m = d["messages"]
    assert len(m) == 3
    assert m[0].startswith("☀️ Bonjour — jeudi 26/11") and "Baulon" in m[0]
    assert "Dentiste" in m[1] and "Rappels non faits" in m[1]
    assert m[2] == "📬 2 mail(s) important(s) non lu(s)"


def test_briefing_du_soir_regarde_demain(client):
    m = client.get("/api/montre/briefing?moment=soir", headers=AUTH).json()["messages"]
    assert m[0].startswith("🌙 Bonsoir — demain, vendredi 27/11")
    assert "Garage" in m[1] and "Dentiste" not in m[1]


def test_briefing_moment_par_defaut_selon_l_heure(client):
    m = client.get("/api/montre/briefing", headers=AUTH).json()["messages"]
    assert m[0].startswith("☀️ Bonjour")  # 10:00


def test_briefing_rubriques_en_panne_nommees(client, monkeypatch):
    def panne_reseau(_url):
        raise OSError("réseau coupé")

    def panne_gmail():
        def chercher(q, n):
            raise RuntimeError("gmail injoignable")
        return chercher

    monkeypatch.setattr(montre, "HTTP_GET", panne_reseau)
    monkeypatch.setattr(montre, "chercheur_mails", panne_gmail)
    r = client.get("/api/montre/briefing?moment=midi", headers=AUTH)
    assert r.status_code == 200
    m = r.json()["messages"]
    assert "indisponible (OSError)" in m[0]
    assert "Dentiste" in m[1]  # l'agenda, lui, répond
    assert m[2] == "📬 Mails : indisponible (RuntimeError)"


def test_briefing_sans_gmail_configure(client, monkeypatch):
    monkeypatch.setattr(montre, "chercheur_mails", lambda: None)
    m = client.get("/api/montre/briefing?moment=matin", headers=AUTH).json()["messages"]
    assert m[2] == "📬 Mails : Gmail non configuré"


def test_briefing_moment_inconnu(client):
    r = client.get("/api/montre/briefing?moment=nuit", headers=AUTH)
    assert r.status_code == 400 and "matin, midi ou soir" in r.json()["erreur"]


def test_briefing_complet_depose_la_demande_pour_le_canal(client):
    r = client.post("/api/montre/briefing", json={"moment": "soir"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["texte"] == "demandé"
    f = config.MEMORY_DIR / "briefing.maintenant"
    assert f.read_text() == "soir"
    # Déjà demandé : toujours « demandé », le détail le dit.
    r = client.post("/api/montre/briefing", json={}, headers=AUTH)
    assert r.json()["texte"] == "demandé" and "déjà demandé" in r.json()["detail"]


@pytest.mark.parametrize("corps", [{"moment": "nuit"}, [1, 2]])
def test_briefing_complet_refus(client, corps):
    r = client.post("/api/montre/briefing", json=corps, headers=AUTH)
    assert r.status_code == 400
    assert not (config.MEMORY_DIR / "briefing.maintenant").exists()


# ── /note et /rappel : proactif.db du dossier d'état ─────────────────────────

def _registre():
    from canaux.proactif import Registre
    return Registre(config.MEMORY_DIR / "proactif.db")


def test_note_dans_le_registre_du_canal(client):
    r = client.post("/api/montre/note", json={"texte": "  acheter du pain  "}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"texte": "Note n°1 enregistrée.", "id": 1}
    assert _registre().notes_recentes() == ["acheter du pain"]
    # Garde d'état : on a bien écrit dans le dossier JETABLE, pas le vrai.
    assert not garde_etat.est_protege(config.MEMORY_DIR)


@pytest.mark.parametrize(("corps", "statut"), [
    ({}, 400), ({"texte": "   "}, 400), ({"texte": 42}, 400),
    ({"texte": "a" * (montre.TEXTE_MAX + 1)}, 413)])
def test_note_refus(client, corps, statut):
    r = client.post("/api/montre/note", json=corps, headers=AUTH)
    assert r.status_code == statut and "erreur" in r.json()


def test_note_corps_illisible(client):
    r = client.post("/api/montre/note", content=b"{oups", headers={
        **AUTH, "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json() == {"erreur": "corps JSON illisible"}


def test_rappel_programme_pour_le_canal(client):
    r = client.post("/api/montre/rappel", headers=AUTH,
                    json={"texte": "appeler le garage", "quand": "2026-11-27T09:00"})
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == 1 and "vendredi 27/11 à 09:00" in d["texte"]
    echeance = datetime(2026, 11, 27, 9, 0).timestamp()
    reg = _registre()
    assert reg.rappels_dus(echeance - 1) == []
    assert reg.rappels_dus(echeance) == [(1, "appeler le garage")]


def test_rappel_avec_fuseau_converti_en_local(client):
    quand = (MAINTENANT + timedelta(hours=2)).astimezone().isoformat()
    r = client.post("/api/montre/rappel", headers=AUTH, json={"texte": "x", "quand": quand})
    assert r.status_code == 200
    assert datetime.fromisoformat(r.json()["quand"]) == MAINTENANT + timedelta(hours=2)


def test_rappel_date_passee_refusee_en_citant_maintenant(client):
    r = client.post("/api/montre/rappel", headers=AUTH,
                    json={"texte": "x", "quand": "2026-11-26T09:59"})
    assert r.status_code == 400
    assert r.json()["erreur"] == ("2026-11-26T09:59 est déjà passé "
                                  "(maintenant : 2026-11-26T10:00)")
    assert _registre().rappels_dus(time.time() + 10 * 365 * 86400) == []


@pytest.mark.parametrize("quand", ["demain 9h", "", None, "2028-01-01T09:00"])
def test_rappel_refus_nommes(client, quand):
    r = client.post("/api/montre/rappel", headers=AUTH, json={"texte": "x", "quand": quand})
    assert r.status_code == 400 and "erreur" in r.json()


# ── /demande ─────────────────────────────────────────────────────────────────

def test_demande_repond_avec_la_duree(client, monkeypatch):
    vus = []

    def fabrique(session, bloques):
        vus.append(session)
        return lambda q: f"vu:{q}"

    monkeypatch.setattr(montre, "fabrique_runner", fabrique)
    r = client.post("/api/montre/demande", json={"texte": "quelle heure ?"}, headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["reponse"] == "vu:quelle heure ?"
    assert isinstance(d["duree_s"], float) and d["duree_s"] >= 0
    assert vus == ["montre"]
    assert not montre._verrou_demande.locked()


def test_demande_trop_longue(client, monkeypatch):
    monkeypatch.setattr(montre, "fabrique_runner", lambda s, b: pytest.fail("appelé"))
    r = client.post("/api/montre/demande", json={"texte": "a" * 1001}, headers=AUTH)
    assert r.status_code == 413


def test_demande_delai_depasse_puis_occupe_puis_libre(client, monkeypatch):
    libere = threading.Event()

    def lente(q):
        libere.wait(5)
        return "enfin"

    monkeypatch.setattr(montre, "fabrique_runner", lambda s, b: lente)
    monkeypatch.setattr(montre, "DELAI_DEMANDE_S", 0.2)
    r = client.post("/api/montre/demande", json={"texte": "long"}, headers=AUTH)
    assert r.status_code == 504 and "pas de réponse" in r.json()["erreur"]
    # Le fil tourne encore : pas de file d'attente, un refus nommé.
    r = client.post("/api/montre/demande", json={"texte": "et là ?"}, headers=AUTH)
    assert r.status_code == 429
    libere.set()
    for _ in range(100):
        if not montre._verrou_demande.locked():
            break
        time.sleep(0.05)
    r = client.post("/api/montre/demande", json={"texte": "et maintenant ?"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["reponse"] == "enfin"


def test_demande_panne_du_runner_nommee_et_verrou_rendu(client, monkeypatch):
    def casse(q):
        raise ConnectionError("gateway muet")

    monkeypatch.setattr(montre, "fabrique_runner", lambda s, b: casse)
    r = client.post("/api/montre/demande", json={"texte": "x"}, headers=AUTH)
    assert r.status_code == 500 and r.json() == {"erreur": "erreur interne (ConnectionError)"}
    assert not montre._verrou_demande.locked()


def _outil(nom):
    return {"type": "function", "function": {"name": nom}}


class _FauxOrch:
    """Orchestrateur bouchonné : le VRAI `make_agent_runner` + la VRAIE garde
    `preparer_orchestrateur` tournent autour de lui."""

    derniers: ClassVar[list[_FauxOrch]] = []

    def __init__(self, memory):
        self.memory = memory
        self.llm = SimpleNamespace(model="brain")
        self.mcp = None
        self.tools = [_outil(n) for n in ("read_file", "run_applescript", "web_search")]
        self._pinned_model = None
        self._context_routing_enabled = True
        self._active_tools = ["x"]
        self.executes: list[str] = []
        self.resultats: dict[str, str] = {}
        self.ferme = False
        _FauxOrch.derniers.append(self)

    def _execute_tool(self, name, args):
        self.executes.append(name)
        return f"ok:{name}"

    def _execute_and_display(self, name, args):
        raise AssertionError("garde non posée")

    def _relevant_files_section(self, q):
        raise AssertionError("retrieval non coupé")

    def run(self, requete):
        self.requete = requete
        for nom, args in (("run_applescript", {"script": "do shell script \"rm -rf ~\""}),
                          ("web_search", {"query": "x"}),
                          ("read_file", {"path": "README.md"}),
                          ("noter", {"texte": "note dictée par le modèle"})):
            self.resultats[nom] = self._execute_and_display(nom, args)
        self.memory.messages.append({"role": "assistant", "content": "**C'est fait.**"})

    def close(self):
        self.ferme = True


def test_demande_au_regime_imessage_outil_hors_liste_blanche_refuse(client, monkeypatch):
    from canaux import imessage as im

    _FauxOrch.derniers.clear()
    monkeypatch.setattr("agent.orchestrator.Orchestrator", _FauxOrch)
    monkeypatch.setattr(montre, "bloques", [])
    r = client.post("/api/montre/demande", json={"texte": "lis le README"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["reponse"] == "C'est fait."  # Markdown retiré (lecture vocale)

    orch = _FauxOrch.derniers[-1]
    # Régime du canal : modèle épinglé, routage coupé, schémas = liste blanche
    assert orch._pinned_model == "brain"
    assert orch._context_routing_enabled is False and orch._active_tools is None
    assert [t["function"]["name"] for t in orch.tools] == ["read_file", "programmer_rappel",
                                                           "noter"]
    # …et la liste blanche REVÉRIFIÉE à l'exécution, même pour un outil caché.
    assert orch.resultats["run_applescript"] == im.REFUS
    assert orch.resultats["web_search"] == im.REFUS
    assert orch.executes == ["read_file"]
    assert montre.bloques == ["run_applescript", "web_search"]
    # Outil local du canal : écrit dans le registre du dossier d'état jetable.
    assert _registre().notes_recentes() == ["note dictée par le modèle"]
    # Date en contexte (avec l'année), conversation à part du canal iMessage.
    assert orch.requete.startswith("[Contexte : nous sommes ") and "lis le README" in orch.requete
    assert orch.memory.session_id == "montre"
    assert orch.ferme


# ── Contrat d'ensemble ───────────────────────────────────────────────────────

def test_toutes_les_routes_du_contrat_existent():
    chemins = {(m, r.path) for r in montre.router.routes for m in r.methods}  # type: ignore[attr-defined]
    assert {(m, c) for m, c, _ in ROUTES} == chemins
