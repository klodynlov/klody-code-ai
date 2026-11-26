"""Actions confirmées par « oui » (canaux/confirmation.py, tools/agenda.py création).

Hors ligne : ni EventKit, ni Gmail, ni Raccourcis, ni envoi iMessage. La coque
est simulée ; le seul binaire réel compilé (si swiftc est là) ne sert qu'aux
refus qui sortent AVANT toute demande d'accès Calendriers/Rappels.
"""

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from canaux import confirmation as cf, imessage as im
from tools import agenda as ag

RACINE = Path(__file__).resolve().parents[1]
APP = RACINE / "canaux" / "imessage_app"
PREFIX = im.prefix_re("klody")
MOI = "+596696000000"
ALLOWED = im.parse_allowed(MOI)


@pytest.fixture(autouse=True)
def _fuseau_paris(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def coque(tmp_path):
    app = tmp_path / "KlodyMessages.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "KlodyMessages").write_text("")
    return app


N = datetime(2026, 11, 26, 20, 0)  # jeudi soir


def M(text, handle=MOI, me=True, rowid=1):
    return im.Msg(rowid, text, handle, me, "", 0.0)


class Horloge:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ── Préparation : ce qui sera écrit, validé, sans rien toucher ───────────────

def test_rappel_avec_heure_part_en_iso_local_avec_fuseau():
    p = ag.preparer_rappel("Acheter du pain", "2026-11-27T09:00", maintenant=N)
    assert p.args_coque == ("--creer-rappel", "--titre", "Acheter du pain",
                            "--echeance", "2026-11-27T09:00:00+01:00")
    assert p.resume == "✅ Créer le rappel « Acheter du pain » — vendredi 27/11 09:00 (liste par défaut)"


def test_rappel_date_seule_liste_et_notes():
    p = ag.preparer_rappel("Pain", "2026-11-27", "Courses", "complet\nbio", maintenant=N)
    assert p.args_coque == ("--creer-rappel", "--titre", "Pain", "--echeance", "2026-11-27",
                            "--liste", "Courses", "--notes", "complet\nbio")
    assert "vendredi 27/11 (liste Courses)" in p.resume and "Notes : complet\nbio" in p.resume


@pytest.mark.parametrize("echeance, motif", [
    ("2025-11-27T09:00", "déjà passée"),     # l'année fausse du 2026-11-22
    ("2031-01-01", "trop lointaine"),
    ("demain 9h", "illisible"),
])
def test_rappel_date_refusee(echeance, motif):
    r = ag.preparer_rappel("x", echeance, maintenant=N)
    assert isinstance(r, str) and motif in r


def test_titre_vide_ou_caracteres_de_controle():
    assert ag.preparer_rappel("  \x00 ", maintenant=N) == "titre du rappel vide"
    p = ag.preparer_rappel("a\x07b\nc", maintenant=N)
    assert p.args_coque[2] == "a b c"


def test_evenement_duree_par_defaut_et_lieu():
    p = ag.preparer_evenement("Dentiste", "2026-11-28T14:30", lieu="Rennes", maintenant=N)
    assert p.args_coque[:7] == ("--creer-evenement", "--titre", "Dentiste",
                                "--debut", "2026-11-28T14:30:00+01:00",
                                "--fin", "2026-11-28T15:30:00+01:00")
    assert "--journee" not in p.args_coque
    assert p.resume == ("📅 Créer l'événement « Dentiste » — samedi 28/11 14:30–15:30 (Rennes), "
                        "calendrier par défaut")


def test_evenement_journee_sur_plusieurs_jours():
    p = ag.preparer_evenement("Vacances", "2026-12-20", fin="2026-12-27", maintenant=N)
    assert "--journee" in p.args_coque
    assert p.args_coque[4] == "2026-12-20T00:00:00+01:00"
    assert p.args_coque[6] == "2026-12-27T23:59:00+01:00"
    assert "dimanche 20/12 → dimanche 27/12, journée" in p.resume


@pytest.mark.parametrize("kw, motif", [
    ({"debut": "2026-11-28T14:00", "fin": "2026-11-28T13:00"}, "précède"),
    ({"debut": "2026-11-28T14:00", "duree_min": 2}, "hors bornes"),
    ({"debut": "2026-11-28T14:00", "duree_min": "longtemps"}, "illisible"),
    ({"debut": ""}, "début"),
])
def test_evenement_refuse(kw, motif):
    r = ag.preparer_evenement("x", maintenant=N, **kw)
    assert isinstance(r, str) and motif in r


# ── Exécution par la coque ───────────────────────────────────────────────────

def test_execution_sous_coque_relance_le_binaire_avec_les_args_valides(coque):
    vus = []

    def run(argv, delai):
        vus.append(argv)
        return json.dumps({"ok": True, "titre": "Pain", "liste": "Courses"})

    p = ag.preparer_rappel("Pain", liste="Courses", maintenant=N)
    assert ag.executer(p, run, sous_coque=True, app=coque) == "✅ Rappel créé : « Pain » (Courses)."
    assert vus == [[str(coque / "Contents/MacOS/KlodyMessages"), *p.args_coque]]


def test_execution_hors_coque_par_open_et_fichier_efface(coque, tmp_path):
    dossier = tmp_path / "agenda"

    def run(argv, delai):
        assert argv[:7] == ["/usr/bin/open", "-g", "-n", "-W", "-a", str(coque), "--args"]
        assert argv[-2] == "--sortie" and Path(argv[-1]).parent == dossier
        Path(argv[-1]).write_text(json.dumps({"ok": False, "erreur": "liste « X » introuvable"}))
        return ""

    p = ag.preparer_rappel("Pain", liste="X", maintenant=N)
    r = ag.executer(p, run, dossier, sous_coque=False, app=coque)
    assert r == "❌ Rappel non créé : liste « X » introuvable"
    assert list(dossier.iterdir()) == []


def test_execution_pannes_nommees(coque, tmp_path):
    p = ag.preparer_evenement("x", "2026-11-28T14:00", maintenant=N)
    rien = ag.executer(p, lambda a, d: "", tmp_path, sous_coque=False, app=coque)
    assert "la coque n'a rien écrit" in rien

    def boum(a, d):
        raise subprocess.CalledProcessError(1, a)

    assert ag.executer(p, boum, sous_coque=True, app=coque).endswith("CalledProcessError")
    assert "coque KlodyMessages absente" in ag.executer(p, boum, app=tmp_path / "absente.app")


def test_outils_refusent_sans_lancer_la_coque():
    def jamais(a, d):
        raise AssertionError("coque lancée")

    assert ag.creer_rappel("", run=jamais, maintenant=N) == "Rappel refusé : titre du rappel vide."
    assert ag.creer_evenement("x", "hier", run=jamais, maintenant=N).startswith("Événement refusé")


# ── « oui » / « non » ────────────────────────────────────────────────────────

@pytest.mark.parametrize("texte, mot", [
    ("oui", "oui"), ("Oui !", "oui"), ("Klody, oui", "oui"), ("OK.", "oui"), ("vas-y", "oui"),
    ("c’est bon", "oui"), ("non", "non"), ("Annule", "non"), ("klody: non", "non"),
    ("oui mais demain", None), ("non, à 10 h", None), ("Klody, qui suis-je", None), ("", None),
])
def test_mot_confirmation(texte, mot):
    assert cf.mot_confirmation(texte, PREFIX) == mot


def _action(journal, nom="creer_rappel", resultat="✅ fait"):
    def preparer(args):
        if args.get("refus"):
            return "titre vide"

        def executer():
            journal.append(args)
            return resultat

        return cf.Action(nom, f"✅ Créer « {args.get('titre')} »", executer)

    return preparer


def test_proposer_n_execute_rien_et_le_recap_part_une_fois():
    journal = []
    c = cf.Confirmations({"creer_rappel": _action(journal)}, Horloge())
    texte = c.proposer("creer_rappel", {"titre": "Pain"})
    assert texte.startswith("EN ATTENTE DE CONFIRMATION") and "jamais que c'est fait" in texte
    assert journal == [] and c.etat() == "attente"
    recap = c.recapitulatif_nouveau()
    assert "✅ Créer « Pain »" in recap and "« oui »" in recap and "10 min" in recap
    assert c.recapitulatif_nouveau() is None
    assert c.repondre("oui") == ("✅ fait", "creer_rappel ok")
    assert journal == [{"titre": "Pain"}]
    assert c.etat() is None and c.repondre("oui") == ("Rien à confirmer.", "vide")
    assert journal == [{"titre": "Pain"}]  # jamais deux fois


def test_non_annule():
    journal = []
    c = cf.Confirmations({"creer_rappel": _action(journal)}, Horloge())
    c.proposer("creer_rappel", {"titre": "Pain"})
    assert c.repondre("non")[0] == "Annulé : rien n'a été fait."
    assert journal == [] and c.etat() is None


def test_arguments_invalides_refuses_avant_toute_attente():
    c = cf.Confirmations({"creer_rappel": _action([])}, Horloge())
    r = c.proposer("creer_rappel", {"refus": True})
    assert r.startswith("Action refusée AVANT confirmation : titre vide")
    assert c.etat() is None and c.recapitulatif_nouveau() is None


def test_une_proposition_par_tour_et_la_suivante_remplace():
    journal = []
    h = Horloge()
    c = cf.Confirmations({"creer_rappel": _action(journal)}, h)
    c.debut_tour()
    c.proposer("creer_rappel", {"titre": "A"})
    assert c.proposer("creer_rappel", {"titre": "B"}).startswith("Refusé : une seule action")
    c.debut_tour()
    assert "remplace la proposition précédente" in c.proposer("creer_rappel", {"titre": "C"})
    c.repondre("oui")
    assert journal == [{"titre": "C"}]


def test_expiration_puis_oui_tardif_explique():
    journal = []
    h = Horloge()
    c = cf.Confirmations({"creer_rappel": _action(journal)}, h)
    c.proposer("creer_rappel", {"titre": "Pain"})
    h.t += cf.DUREE_S + 1
    assert c.etat() == "expiree"
    texte, audit = c.repondre("oui")
    assert texte.startswith("⌛ La proposition a expiré") and audit == "expiree"
    assert journal == [] and c.etat() is None
    c.proposer("creer_rappel", {"titre": "Pain"})
    h.t += cf.DUREE_S + cf.OUBLI_EXPIREE_S + 2
    assert c.etat() is None  # un « oui » des heures après n'est plus une réponse


def test_execution_qui_leve_est_nommee():
    def preparer(args):
        def executer():
            raise RuntimeError("gmail send_email : pas de réponse en 60 s")
        return cf.Action("mcp__gmail__send_email", "📧", executer)

    c = cf.Confirmations({"mcp__gmail__send_email": preparer}, Horloge())
    c.proposer("mcp__gmail__send_email", {})
    texte, audit = c.repondre("oui")
    assert texte.startswith("❌ Échec : RuntimeError — gmail send_email : pas de réponse")
    assert audit == "mcp__gmail__send_email échec"


# ── Mail : le récapitulatif montre TOUT ce qui part ──────────────────────────

def test_mail_recap_complet_et_arguments_nettoyes():
    appels = []
    prep = cf.preparer_mail(cf.GMAIL_ENVOI, lambda outil, a: appels.append((outil, a)) or {})
    a = prep({"to": " marc@exemple.fr ", "cc": "ana@exemple.fr", "bcc": "",
              "subject": "Re:  devis\n", "body": "Bonjour Marc,\nOK pour jeudi.\nC."})
    assert a.resume == ("📧 ENVOYER un mail à marc@exemple.fr\nCc : ana@exemple.fr\n"
                        "Objet : Re: devis\n———\nBonjour Marc,\nOK pour jeudi.\nC.\n———")
    assert a.executer() == "✅ Mail envoyé à marc@exemple.fr."
    assert appels == [("send_email", {"to": "marc@exemple.fr", "subject": "Re: devis",
                                      "body": "Bonjour Marc,\nOK pour jeudi.\nC.",
                                      "cc": "ana@exemple.fr", "bcc": ""})]


def test_envoi_en_echec_dit_incertain_pas_echoue():
    def panne(outil, a):
        raise RuntimeError("gmail send_email : pas de réponse en 60 s")

    a = cf.preparer_mail(cf.GMAIL_ENVOI, panne)({"to": "a@b.fr", "subject": "x", "body": "y"})
    r = a.executer()
    assert r.startswith("❌ Envoi incertain (gmail send_email : pas de réponse en 60 s)")
    assert "dossier Envoyés" in r
    b = cf.preparer_mail(cf.GMAIL_BROUILLON, panne)({"to": "a@b.fr", "subject": "x", "body": "y"})
    with pytest.raises(RuntimeError):
        b.executer()


def test_brouillon_dit_que_rien_ne_part():
    prep = cf.preparer_mail(cf.GMAIL_BROUILLON, lambda o, a: {})
    a = prep({"to": "a@b.fr", "subject": "x", "body": "y"})
    assert a.resume.startswith("📝 Créer un BROUILLON Gmail (rien ne part) à a@b.fr")
    assert "rien n'est parti" in a.executer()


@pytest.mark.parametrize("args, motif", [
    ({"to": "", "body": "x"}, "destinataire manquant"),
    ({"to": "pas-une-adresse", "body": "x"}, "adresse invalide"),
    ({"to": "a@b.fr", "bcc": "x@y.fr, <evil>", "body": "x"}, "adresse invalide"),
    ({"to": ",".join(f"a{i}@b.fr" for i in range(6)), "body": "x"}, "plus de 5"),
    ({"to": "a@b.fr", "body": "  "}, "corps du mail vide"),
    ({"to": "a@b.fr", "body": "x" * (cf.CORPS_MAX + 1)}, "ne tient pas en entier"),
])
def test_mail_refuse(args, motif):
    r = cf.preparer_mail(cf.GMAIL_ENVOI, lambda o, a: {})(args)
    assert isinstance(r, str) and motif in r


# ── Raccourcis : seul un nom EXISTANT est proposable ─────────────────────────

def test_raccourci_inconnu_refuse_connu_lance_tel_quel():
    lances = []
    prep = cf.preparer_raccourci(lambda: ["Lumières salon", "Bonne nuit"],
                                 lambda n, e: lances.append((n, e)) or "(aucune sortie)")
    assert "introuvable" in prep({"name": "Ouvre la porte"})
    a = prep({"name": "Bonne nuit", "input_text": "doux"})
    assert a.resume == "⚡ Lancer le raccourci « Bonne nuit »\nEntrée : doux"
    assert lances == []
    assert a.executer().startswith("✅ Raccourci « Bonne nuit » lancé.")
    assert lances == [("Bonne nuit", "doux")]


def test_raccourci_liste_illisible_et_erreur_relayee():
    def panne():
        raise OSError("shortcuts absent")

    assert "illisible (OSError)" in cf.preparer_raccourci(panne, lambda n, e: "")({"name": "x"})
    a = cf.preparer_raccourci(lambda: ["x"], lambda n, e: "ERREUR : refusé")({"name": "x"})
    assert a.executer() == "ERREUR : refusé"


# ── Agenda via Confirmations ─────────────────────────────────────────────────

def test_preparateur_agenda_branche_sur_tools_agenda(monkeypatch):
    executes = []
    monkeypatch.setattr(ag, "executer", lambda p: executes.append(p) or "✅ Rappel créé")
    monkeypatch.setattr(ag, "preparer_rappel",
                        lambda *a: ag.Preparation("rappel", ("--creer-rappel",), f"recap {a[0]}"))
    a = cf.preparer_agenda("creer_rappel")({"titre": "Pain"})
    assert a.resume == "recap Pain" and executes == []
    assert a.executer() == "✅ Rappel créé" and len(executes) == 1
    assert cf.preparer_agenda("creer_evenement")({"titre": "x", "debut": "n'importe"}).startswith("début")


def test_confirmations_par_defaut_gmail_seulement_si_configure():
    sans = cf.confirmations_par_defaut(None).noms
    assert sans == {"creer_rappel", "creer_evenement", "run_shortcut"}
    avec = cf.confirmations_par_defaut("http://127.0.0.1:8084/mcp").noms
    assert avec == sans | {cf.GMAIL_ENVOI, cf.GMAIL_BROUILLON}


# ── Canal : garde et boucle ──────────────────────────────────────────────────

class _FauxLLM:
    model = "brain"


class _FauxOrch:
    def __init__(self):
        self.llm = _FauxLLM()
        self.mcp = None
        self.tools = [{"type": "function", "function": {"name": n}}
                      for n in ("read_file", "run_shortcut", "creer_rappel", "write_file")]
        self._pinned_model = None
        self._context_routing_enabled = True
        self._active_tools = None
        self.executes = []

    def _execute_tool(self, name, args):
        self.executes.append(name)
        return "exécuté"

    def _execute_and_display(self, name, args):
        raise AssertionError("non gardé")

    def _relevant_files_section(self, q):
        return ""


def test_garde_met_en_attente_au_lieu_d_executer():
    from agent.approval import requires_approval as ra
    journal = []
    c = cf.Confirmations({"creer_rappel": _action(journal), "run_shortcut": _action(journal)},
                         Horloge())
    orch = _FauxOrch()
    bloques = []
    im.preparer_orchestrateur(orch, ra, bloques, confirmations=c)
    assert [t["function"]["name"] for t in orch.tools] == ["read_file", "run_shortcut", "creer_rappel"]
    assert orch._execute_and_display("creer_rappel", {"titre": "Pain"}).startswith("EN ATTENTE")
    assert orch._execute_and_display("write_file", {}) == im.REFUS
    assert orch.executes == [] and journal == [] and bloques == ["write_file"]


def test_sans_confirmations_le_canal_reste_en_lecture():
    from agent.approval import requires_approval as ra
    orch = _FauxOrch()
    im.preparer_orchestrateur(orch, ra, [])
    assert [t["function"]["name"] for t in orch.tools] == ["read_file"]
    assert orch._execute_and_display("creer_rappel", {}) == im.REFUS


def _canal(journal, h):
    c = cf.Confirmations({"creer_rappel": _action(journal, resultat="✅ Rappel créé")}, h)

    def run(q):
        c.debut_tour()
        if "rappel" in q:
            c.proposer("creer_rappel", {"titre": "Pain"})
            return "Je crée le rappel dès que tu confirmes."
        return "?"

    return c, run


def test_boucle_proposition_recap_puis_oui_execute_une_seule_fois():
    journal, envois, consignes = [], [], []
    h = Horloge()
    c, run = _canal(journal, h)
    dedup = im.Dedoublonneur()

    def traiter(msgs):
        return im.traiter(msgs, ALLOWED, PREFIX, dedup, run, lambda hd, t: envois.append(t), h,
                          confirmations=c, consigner=lambda q, r: consignes.append((q, r)))

    traiter([M("Klody, ajoute un rappel pain", rowid=1),
             M("Klody, ajoute un rappel pain", me=False, rowid=2)])
    assert envois == ["🤖 Je crée le rappel dès que tu confirmes.",
                      "🤖 ⏳ À confirmer :\n✅ Créer « Pain »\n\n"
                      "Réponds « oui » pour le faire, « non » pour annuler (10 min)."]
    assert journal == []

    h.t += 30
    traiter([M("oui", rowid=3), M("oui", me=False, rowid=4)])  # + sa copie reçue
    assert journal == [{"titre": "Pain"}]
    assert envois[2:] == ["🤖 ✅ Rappel créé"]
    assert consignes == [("oui", "✅ Rappel créé")]

    # 2ᵉ proposition dans la minute : le 2ᵉ « oui » n'est pas pris pour une copie.
    h.t += 10
    traiter([M("Klody, encore un rappel", rowid=5)])
    h.t += 5
    traiter([M("oui", rowid=6)])
    assert journal == [{"titre": "Pain"}, {"titre": "Pain"}]


def test_oui_sans_attente_ou_d_un_inconnu_n_execute_rien():
    journal, envois, requetes = [], [], []
    h = Horloge()
    c, run = _canal(journal, h)

    def run_trace(q):
        requetes.append(q)
        return run(q)

    dedup = im.Dedoublonneur()
    im.traiter([M("oui", rowid=1)], ALLOWED, PREFIX, dedup, run_trace,
               lambda hd, t: envois.append(t), h, confirmations=c)
    assert envois == [] and requetes == []  # « oui » nu sans attente : conversation normale

    c.proposer("creer_rappel", {"titre": "Pain"})
    c.recapitulatif_nouveau()
    im.traiter([M("oui", handle="+33600000000", me=False, rowid=2)], ALLOWED, PREFIX, dedup,
               run_trace, lambda hd, t: envois.append(t), h, confirmations=c)
    assert journal == [] and envois == [] and c.etat() == "attente"


def test_non_puis_oui_tardif():
    journal, envois = [], []
    h = Horloge()
    c, run = _canal(journal, h)
    dedup = im.Dedoublonneur()
    args = (ALLOWED, PREFIX, dedup, run, lambda hd, t: envois.append(t), h)
    im.traiter([M("Klody, un rappel", rowid=1)], *args, confirmations=c)
    h.t += 61
    im.traiter([M("non", rowid=2)], *args, confirmations=c)
    assert envois[-1] == "🤖 Annulé : rien n'a été fait."
    im.traiter([M("Klody, un rappel", rowid=3)], *args, confirmations=c)
    h.t += cf.DUREE_S + 1
    im.traiter([M("oui", rowid=4)], *args, confirmations=c)
    assert envois[-1].startswith("🤖 ⌛ La proposition a expiré")
    assert journal == []


def test_consigner_memoire_ecrit_la_conversation_du_canal(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path)
    im.consigner_memoire(tmp_path)("oui", "✅ Rappel créé")
    d = json.loads((tmp_path / "memory_imessage.json").read_text())
    assert [(m["role"], m["content"]) for m in d["messages"]] == [
        ("user", "oui"), ("assistant", "✅ Rappel créé")]


# ── Partout : app (validation), routage, schémas ─────────────────────────────

def test_ecritures_agenda_gardees_dans_l_app():
    from agent.approval import requires_approval
    assert requires_approval("creer_rappel") and requires_approval("creer_evenement")


def test_schemas_et_routage():
    from tools.context_selection import select_native
    from tools.registry import get_tool_names, get_tools
    noms = set(get_tool_names())
    assert {"creer_rappel", "creer_evenement"} <= noms
    choisis = select_native("ajoute un rendez-vous chez le dentiste dans mon agenda", get_tools())
    assert {"creer_rappel", "creer_evenement"} <= set(choisis)


def test_dispatch_orchestrateur(monkeypatch):
    import agent.orchestrator as om
    vus = {}
    monkeypatch.setattr(om, "ag_creer_rappel", lambda *a: vus.setdefault("r", a) and "r")
    monkeypatch.setattr(om, "ag_creer_evenement", lambda *a: vus.setdefault("e", a) and "e")
    o = om.Orchestrator.__new__(om.Orchestrator)

    class _Stub:
        def __getattr__(self, _):
            return lambda *a, **kw: None
    o.profiler = _Stub()
    o._execute_tool("creer_rappel", {"titre": "Pain", "echeance": "2026-11-27"})
    o._execute_tool("creer_evenement", {"titre": "x", "debut": "2026-11-28T14:00", "journee": True})
    assert vus["r"] == ("Pain", "2026-11-27", "", "")
    assert vus["e"] == ("x", "2026-11-28T14:00", "", 60, "", "", True, "")


# ── Coque Swift ──────────────────────────────────────────────────────────────

def test_coque_dispatch_et_refus_avant_eventkit():
    main = (APP / "main.swift").read_text()
    assert '["--creer-rappel", "--creer-evenement"]' in main and "lancerCreation(" in main
    src = (APP / "Creation.swift").read_text()
    i_sortie = src.index("sortieAutorisee(c)")
    i_titre = src.index('echec("titre vide"')
    i_acces = src.index("requestFullAccessTo")
    assert i_sortie < i_acces and i_titre < i_acces
    assert "allowsContentModifications" in src  # jamais un calendrier en lecture seule
    assert "EKAlarm(absoluteDate:" in src  # un rappel à heure sonne


# Vécu en CI le 2026-11-26 : le runner Linux a swiftc mais pas EventKit.
@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("swiftc") is None,
                    reason="macOS + swiftc requis (EventKit)")
def test_coque_compilee_refuse_avant_tout_acces(tmp_path):
    binaire = tmp_path / "km"
    r = subprocess.run(["swiftc", "-swift-version", "5", "-o", str(binaire),
                        *map(str, sorted(APP.glob("*.swift")))],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    # --sortie hors du dossier autorisé : 64, rien d'écrit
    r = subprocess.run([str(binaire), "--creer-rappel", "--titre", "x",
                        "--sortie", str(tmp_path / "x.json")], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 64 and not (tmp_path / "x.json").exists()
    # titre vide : erreur NOMMÉE en JSON, sans demander l'accès Rappels
    r = subprocess.run([str(binaire), "--creer-rappel", "--titre", " "],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and json.loads(r.stdout) == {"ok": False, "erreur": "titre vide"}
    # lecture SÉQUENTIELLE : la valeur « --sortie » des notes n'est pas le drapeau
    # (un firstIndex aurait lu « --x » comme chemin → 64)
    r = subprocess.run([str(binaire), "--creer-rappel", "--titre", "", "--notes", "--sortie",
                        "--x", "/tmp/evil.json"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and json.loads(r.stdout)["erreur"] == "titre vide"
