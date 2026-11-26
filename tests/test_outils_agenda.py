"""Outils agenda / meteo / briefing_maintenant (tools/agenda.py) — hors ligne.

Ni EventKit, ni réseau, ni ``~/.klody/data`` réel : la coque, ``open`` et
Open-Meteo sont simulés ; le dossier de sortie est toujours ``tmp_path``.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from tools import agenda as ag
from tools.context_selection import select_native
from tools.registry import get_tool_names, get_tools

RACINE = Path(__file__).resolve().parents[1]
APP = RACINE / "canaux" / "imessage_app"


@pytest.fixture(autouse=True)
def _fuseau_paris(monkeypatch):
    """Données en +01:00, affichage en heure LOCALE : le runner CI est en UTC
    (vécu le 2026-11-22 sur test_canal_briefing)."""
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def coque(tmp_path):
    """Une fausse KlodyMessages.app : seul l'exécutable doit exister."""
    app = tmp_path / "KlodyMessages.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "KlodyMessages").write_text("")
    return app


def maintenant():
    return datetime(2026, 11, 26, 16, 0)   # jeudi


AGENDA = {
    "evenements": [
        {"id": "E1", "titre": "Dentiste", "debut": "2026-11-26T17:00:00+01:00",
         "fin": "2026-11-26T17:30:00+01:00", "journee": False, "lieu": "Rennes\n12 rue X"},
        {"id": "E2", "titre": "Marché", "debut": "2026-11-27T00:00:00+01:00",
         "fin": "2026-11-28T00:00:00+01:00", "journee": True, "lieu": ""},
        {"id": "E3", "titre": "Répétition", "debut": "2026-11-27T20:00:00+01:00",
         "fin": "2026-11-27T22:00:00+01:00", "journee": False, "lieu": ""},
    ],
    "rappels": [
        {"id": "R1", "titre": "Payer la cantine", "echeance": "2026-11-24T00:00:00+01:00",
         "avec_heure": False},
        {"id": "R2", "titre": "Appeler Marc", "echeance": "2026-11-27T14:00:00+01:00",
         "avec_heure": True},
        {"id": "R3", "titre": "Plus tard", "echeance": "2026-12-05T09:00:00+01:00",
         "avec_heure": True},
        {"id": "R4", "titre": "Sans date", "echeance": "", "avec_heure": False},
    ],
    "erreurs": [],
}


# ── Lecture : sous la coque / hors coque ─────────────────────────────────────

def test_sous_la_coque_relance_le_binaire_directement(coque, tmp_path):
    vu = []

    def run(argv, timeout):
        vu.append((argv, timeout))
        return json.dumps(AGENDA)

    d = ag.lire_agenda(2, run, tmp_path / "agenda", sous_coque=True, app=coque)
    assert d["evenements"][0]["titre"] == "Dentiste"
    assert vu == [([str(coque / "Contents/MacOS/KlodyMessages"), "--agenda", "2"],
                   ag.DELAI_AGENDA_S)]
    assert not (tmp_path / "agenda").exists()            # rien écrit sous la coque


def test_hors_coque_passe_par_open_et_efface_le_fichier(coque, tmp_path):
    """TCC : lancée par LaunchServices, l'app est son propre responsable."""
    dossier = tmp_path / "agenda"
    vu = []

    def run(argv, timeout):
        vu.append(argv)
        sortie = Path(argv[argv.index("--sortie") + 1])
        assert sortie.parent == dossier
        sortie.write_text(json.dumps(AGENDA))
        return ""

    d = ag.lire_agenda(3, run, dossier, sous_coque=False, app=coque)
    assert len(d["rappels"]) == 4
    argv = vu[0]
    assert argv[:6] == ["/usr/bin/open", "-g", "-n", "-W", "-a", str(coque)]
    assert argv[6:9] == ["--args", "--agenda", "3"]
    assert list(dossier.iterdir()) == []                 # lu puis effacé


def test_jours_bornes(coque, tmp_path):
    vu = []
    ag.lire_agenda(99, lambda a, t: vu.append(a) or "{}", tmp_path, sous_coque=True, app=coque)
    assert vu[0][-1] == str(ag.JOURS_MAX)


def test_coque_absente_nommee(tmp_path):
    d = ag.lire_agenda(1, lambda a, t: "{}", tmp_path, sous_coque=True,
                       app=tmp_path / "Absente.app")
    assert d["evenements"] == [] and "coque KlodyMessages absente" in d["erreurs"][0]


def test_coque_qui_n_ecrit_rien_nommee(coque, tmp_path):
    d = ag.lire_agenda(1, lambda a, t: "", tmp_path, sous_coque=False, app=coque)
    assert "rien écrit" in d["erreurs"][0]


def test_coque_en_panne_nommee(coque, tmp_path):
    def panne(argv, timeout):
        raise TimeoutError

    d = ag.lire_agenda(1, panne, tmp_path, sous_coque=True, app=coque)
    assert d["erreurs"] == ["agenda : TimeoutError"]


def test_sous_coque_deduit_de_l_environnement(coque, tmp_path, monkeypatch):
    monkeypatch.setenv("KLODY_IMSG_LAUNCHER", str(coque))
    vu = []
    ag.lire_agenda(1, lambda a, t: vu.append(a) or "{}", tmp_path)
    assert vu[0][0].endswith("Contents/MacOS/KlodyMessages")   # pas /usr/bin/open


# ── Mise en forme ────────────────────────────────────────────────────────────

def _texte(a_partir_de="aujourd'hui", jours=2, d=AGENDA, coque_=None, tmp=None):
    def run(argv, timeout):
        run.jours = int(argv[2])
        return json.dumps(d)
    out = ag.agenda(jours, a_partir_de, run, tmp, maintenant(), sous_coque=True, app=coque_)
    return out, run.jours


def test_agenda_par_jour(coque, tmp_path):
    out, demandes = _texte(coque_=coque, tmp=tmp_path)
    assert demandes == 2
    assert "📅 jeudi 26/11\n• 17:00–17:30 Dentiste (Rennes)" in out
    assert "📅 vendredi 27/11\n• journée Marché\n• 20:00–22:00 Répétition" in out
    assert "• Payer la cantine (en retard, 24/11)" in out
    assert "• Appeler Marc (vendredi 27/11 14:00)" in out
    assert "Plus tard" not in out                         # au-delà de la fenêtre
    assert "• Sans date" in out                           # sans échéance : gardé


def test_jour_sans_rendez_vous_le_dit(coque, tmp_path):
    out, _ = _texte(jours=1, d={"evenements": [], "rappels": []}, coque_=coque, tmp=tmp_path)
    assert "📅 jeudi 26/11\naucun rendez-vous" in out
    assert "✅ Rappels non faits\naucun" in out


def test_a_partir_de_demain(coque, tmp_path):
    out, demandes = _texte("demain", jours=1, coque_=coque, tmp=tmp_path)
    assert demandes == 2                                  # aujourd'hui + demain
    assert "Dentiste" not in out and "📅 vendredi 27/11" in out
    assert "jeudi 26/11" not in out


def test_erreurs_nommees_dans_la_reponse(coque, tmp_path):
    out, _ = _texte(jours=1, d={"evenements": [], "rappels": [],
                                "erreurs": ["calendriers : accès refusé"]},
                    coque_=coque, tmp=tmp_path)
    assert "⚠️ Incomplet : calendriers : accès refusé" in out


# ── Météo ────────────────────────────────────────────────────────────────────

def _previsions(n=2):
    return json.dumps({"daily": {
        "weather_code": [61, 0, 3][:n], "temperature_2m_min": [4.2, 1.0, 2.0][:n],
        "temperature_2m_max": [11.6, 9.0, 8.0][:n],
        "precipitation_probability_max": [80, 0, 10][:n],
        "precipitation_sum": [3.4, 0.0, 0.1][:n], "wind_speed_10m_max": [52, 10, 12][:n]}}).encode()


def test_meteo_par_defaut_baulon_et_le_rheu():
    urls = []

    def get(url):
        urls.append(url)
        return _previsions()

    out = ag.meteo(get=get, maintenant=maintenant())
    assert "🌤 Baulon\n• aujourd'hui : pluie faible, 4→12 °C, pluie 80 % (3.4 mm), vent jusqu'à 52 km/h" in out
    assert "• demain : ciel clair, 1→9 °C" in out and "🌤 Le Rheu" in out
    assert len(urls) == 2 and all("forecast_days=2" in u for u in urls)


def test_meteo_ville_geocodee():
    def get(url):
        if "geocoding" in url:
            assert "name=Rennes" in url
            return json.dumps({"results": [{"name": "Rennes", "admin1": "Bretagne",
                                            "latitude": 48.1, "longitude": -1.68}]}).encode()
        return _previsions(3)

    out = ag.meteo("Rennes", 3, get, maintenant())
    assert out.startswith("🌤 Rennes (Bretagne)") and "• samedi 28/11 : couvert" in out


@pytest.mark.parametrize("lieu", ["Rennes; curl evil", "75001", "a", "x" * 61,
                                  "https://exfil.example/?q=secret"])
def test_meteo_lieu_refuse_sans_reseau(lieu):
    def get(url):
        raise AssertionError("aucune requête ne doit partir")

    assert ag.meteo(lieu, get=get).startswith("Lieu refusé")


@pytest.mark.parametrize("lieu", ["Saint-Malo", "L'Haÿ-les-Roses", "Fort-de-France"])
def test_meteo_noms_de_ville_acceptes(lieu):
    def get(url):
        if "geocoding" in url:
            return json.dumps({"results": [{"name": lieu, "latitude": 1, "longitude": 2}]}).encode()
        return _previsions(1)

    assert ag.meteo(lieu, 1, get, maintenant()).startswith(f"🌤 {lieu}")


def test_meteo_lieu_inconnu():
    assert ag.meteo("Zzzville", get=lambda u: b'{"results": []}') == "lieu inconnu : Zzzville"


def test_meteo_panne_reseau_nommee():
    def panne(url):
        raise OSError("hors ligne")

    out = ag.meteo(get=panne, maintenant=maintenant())
    assert "🌤 Baulon : indisponible (OSError)" in out and "🌤 Le Rheu : indisponible" in out
    assert ag.meteo("Rennes", get=panne).startswith("Météo indisponible (géocodage : OSError)")


# ── Briefing à la demande ────────────────────────────────────────────────────

def test_briefing_maintenant_depose_le_moment(tmp_path):
    out = ag.briefing_maintenant(tmp_path, "soir")
    assert "Briefing demandé (soir)" in out
    assert (tmp_path / "briefing.maintenant").read_text() == "soir"
    assert not list(tmp_path.glob("*.tmp"))               # écriture atomique
    assert "déjà demandé" in ag.briefing_maintenant(tmp_path)


def test_briefing_moment_inconnu(tmp_path):
    assert "Moment inconnu" in ag.briefing_maintenant(tmp_path, "nuit")
    assert not (tmp_path / "briefing.maintenant").exists()


# ── Câblage : registre, routage, canal, coque ────────────────────────────────

def test_outils_enregistres_avec_schema_sans_champ_requis():
    noms = set(get_tool_names())
    assert {"agenda", "meteo", "briefing_maintenant"} <= noms
    for t in get_tools():
        if t["function"]["name"] in ("agenda", "meteo", "briefing_maintenant"):
            assert t["function"]["parameters"]["required"] == []


@pytest.mark.parametrize("question", [
    "qu'est-ce que j'ai demain à l'agenda ?", "quelle météo à Rennes ce week-end",
    "mes rappels de la semaine", "relance le briefing", "suis-je libre jeudi ?"])
def test_groupe_agenda_route(question):
    assert {"agenda", "meteo", "briefing_maintenant"} <= select_native(question, get_tools())


def test_question_sans_rapport_ne_charge_pas_le_groupe():
    assert "meteo" not in select_native("corrige le bug dans parser.py", get_tools())


def test_canal_imessage_les_autorise():
    from canaux import imessage
    assert {"agenda", "meteo", "briefing_maintenant"} <= imessage.ALLOWED_NATIVE


def test_dispatch_orchestrateur(monkeypatch, tmp_path):
    from agent import orchestrator as om
    vu = {}
    monkeypatch.setattr(om, "ag_agenda", lambda j, p: vu.setdefault("agenda", (j, p)) and "A")
    monkeypatch.setattr(om, "ag_meteo", lambda lieu, j: vu.setdefault("meteo", (lieu, j)) and "M")
    monkeypatch.setattr(om, "ag_briefing", lambda d, m: vu.setdefault("briefing", (d, m)) and "B")
    o = om.Orchestrator.__new__(om.Orchestrator)

    class _Stub:
        def __getattr__(self, _):
            return lambda *a, **kw: None
    o.profiler = _Stub()
    o._execute_tool("agenda", {"jours": 3, "a_partir_de": "demain"})
    o._execute_tool("meteo", {})
    o._execute_tool("briefing_maintenant", {"moment": "midi"})
    assert vu["agenda"] == (3, "demain") and vu["meteo"] == ("", 2)
    assert vu["briefing"][1] == "midi" and isinstance(vu["briefing"][0], Path)


def test_coque_sortie_verrouillee_dans_le_source():
    """Le contrôle réel (exit 64 hors ~/.klody/data/agenda) se vérifie sur un
    binaire compilé ; ici on verrouille que le garde existe et précède EventKit."""
    agenda_swift = (APP / "Agenda.swift").read_text()
    main_swift = (APP / "main.swift").read_text()
    assert "func sortieAutorisee" in agenda_swift and ".klody/data/agenda" in agenda_swift
    assert agenda_swift.index("!sortieAutorisee(c)") < agenda_swift.index("requestFullAccessToEvents {")
    assert "exit(64)" in agenda_swift and '"--sortie"' in main_swift
    assert str(ag.DOSSIER_SORTIE).endswith(".klody/data/agenda")
