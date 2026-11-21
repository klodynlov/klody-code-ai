"""Canal iMessage (canaux/imessage.py) — hors ligne : aucun vrai chat.db, aucun envoi."""

import inspect
import sqlite3
import subprocess

import pytest
from canaux import imessage as im

PREFIX = im.prefix_re("klody")
MOI = "+596696000000"
ALLOWED = im.parse_allowed(MOI)


def _blob(text: str) -> bytes:
    raw = text.encode()
    n = len(raw)
    if n < 0x80:
        ln = bytes([n])
    elif n < 0x10000:
        ln = b"\x81" + n.to_bytes(2, "little")
    else:
        ln = b"\x82" + n.to_bytes(4, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString"
            b"\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
            + ln + raw + b"\x86\x84\x02iI")


def M(text, handle="+596 696 00 00 00", me=False, rowid=1):
    return im.Msg(rowid, text, handle, me, "", 0.0)


# ── Décodage attributedBody ──────────────────────────────────────────────────

def test_decode_court():
    assert im.decode_attributed_body(_blob("Klody, quelle heure ?")) == "Klody, quelle heure ?"


@pytest.mark.parametrize("n", [300, 70000])
def test_decode_longueurs_sur_2_et_4_octets(n):
    t = "é" * (n // 2)
    assert im.decode_attributed_body(_blob(t)) == t


def test_decode_vide_ou_illisible():
    assert im.decode_attributed_body(None) == ""
    assert im.decode_attributed_body(b"rien") == ""
    assert im.decode_attributed_body(b"NSString sans plus") == ""


# ── Filtre ───────────────────────────────────────────────────────────────────

def test_prefixe_et_expediteur():
    assert im.extract_command(M("Klody, résume mes mails"), ALLOWED, PREFIX) == "résume mes mails"
    assert im.extract_command(M("klody: météo"), ALLOWED, PREFIX) == "météo"
    assert im.extract_command(M("KLODY quoi de neuf", me=True), ALLOWED, PREFIX) == "quoi de neuf"


@pytest.mark.parametrize("msg", [
    M("salut"),                          # pas de préfixe : conversation normale
    M("Klodyx test"),                    # autre mot
    M("Klody, test", "+33600000000"),    # expéditeur inconnu
    M("🤖 Klody, boucle"),               # sa propre réponse
    M("Klody,"),                         # requête vide
    M(""),
])
def test_refus(msg):
    assert im.extract_command(msg, ALLOWED, PREFIX) is None


def test_apple_id_et_prefixe_configurable():
    allowed = im.parse_allowed(" Moi@Exemple.fr , ")
    assert allowed == {"moi@exemple.fr"}
    assert im.extract_command(M("jarvis go", "moi@exemple.fr"), allowed, im.prefix_re("jarvis")) == "go"


def test_dedoublonneur_fenetre():
    d = im.Dedoublonneur(fenetre_s=60)
    assert d.nouveau("q", 0.0)
    assert not d.nouveau("q", 10.0)       # copie reçue du même message
    assert d.nouveau("q", 100.0)          # redemandé plus tard : nouvelle requête


# ── Liste blanche ────────────────────────────────────────────────────────────

def test_liste_blanche_sur_la_vraie_garde():
    from agent.approval import requires_approval as ra

    def ok(n):
        return im.tool_allowed(n, ra)

    assert ok("mcp__gmail__search_emails") and ok("mcp__gmail__read_email")
    assert ok("search_books") and ok("rappeler_memoire")
    for n in ("mcp__gmail__send_email", "mcp__gmail__create_draft", "run_applescript",
              "run_shortcut", "git_control", "mqtt_publish", "execute_command",
              "write_file", "ask_user", "mcp__web__fetch", "mcp__laser__laser_status",
              "remember_fact", "forget_fact", "sync_directories", "mcp__", "mcp__gmail"):
        assert not ok(n), n


def test_liste_blanche_native_sans_outil_garde():
    """Un outil natif de la liste blanche ne doit jamais être un outil gardé."""
    from agent.approval import requires_approval as ra
    gardes = [n for n in im.ALLOWED_NATIVE if ra(n, {})]
    assert gardes == []


class _FauxLLM:
    model = "brain"


class _FauxMCP:
    def __init__(self, outils):
        self.outils = outils
        self.demandes = []

    def discover_selected(self, noms):
        self.demandes.append(set(noms))
        return self.outils


def _outil(nom):
    return {"type": "function", "function": {"name": nom}}


class _FauxOrch:
    def __init__(self, mcp=None):
        self.llm = _FauxLLM()
        self.mcp = mcp
        self.tools = [_outil(n) for n in ("search_books", "run_shortcut", "write_file", "read_file")]
        self._pinned_model = None
        self._context_routing_enabled = True
        self._active_tools = ["x"]
        self.executes = []

    def _execute_tool(self, name, args):
        self.executes.append(name)
        return f"ok:{name}"

    def _execute_and_display(self, name, args):  # remplacé par la garde
        raise AssertionError("non gardé")

    def _relevant_files_section(self, q):
        raise AssertionError("retrieval non coupé")


def test_preparer_orchestrateur_regime_siri_et_garde():
    from agent.approval import requires_approval as ra
    mcp = _FauxMCP([_outil("mcp__gmail__read_email"), _outil("mcp__gmail__send_email"),
                    _outil("search_books")])
    orch = _FauxOrch(mcp)
    bloques = []
    im.preparer_orchestrateur(orch, ra, bloques)

    assert orch._pinned_model == "brain"
    assert orch._context_routing_enabled is False
    assert orch._active_tools is None
    assert orch._relevant_files_section("q") == ""
    assert mcp.demandes == [{"gmail"}]
    noms = [t["function"]["name"] for t in orch.tools]
    assert noms == ["search_books", "read_file", "mcp__gmail__read_email"]  # dédoublonné

    assert orch._execute_and_display("read_file", {}) == "ok:read_file"
    assert orch._execute_and_display("run_shortcut", {}) == im.REFUS
    assert orch._execute_and_display("mcp__gmail__send_email", {}) == im.REFUS
    assert orch.executes == ["read_file"]
    assert bloques == ["run_shortcut", "mcp__gmail__send_email"]


def test_preparer_orchestrateur_mcp_en_panne_ne_casse_pas():
    from agent.approval import requires_approval as ra

    class Panne(_FauxMCP):
        def discover_selected(self, noms):
            raise RuntimeError("serveur gmail absent")

    orch = _FauxOrch(Panne([]))
    im.preparer_orchestrateur(orch, ra, [])
    assert [t["function"]["name"] for t in orch.tools] == ["search_books", "read_file"]


def test_contrat_orchestrator_attributs_utilises():
    """Le canal patche des attributs privés de l'orchestrateur : un renommage doit rougir ici."""
    from agent.orchestrator import Orchestrator
    for meth in ("_execute_and_display", "_execute_tool", "_relevant_files_section", "run", "close"):
        assert callable(getattr(Orchestrator, meth, None)), meth
    init = inspect.getsource(Orchestrator.__init__)
    for attr in ("self._context_routing_enabled", "self._active_tools", "self._pinned_model",
                 "self.mcp", "self.tools", "self.llm"):
        assert attr in init, attr
    from tools.mcp_bridge import MCPManager
    assert callable(MCPManager.discover_selected)


# ── chat.db synthétique ──────────────────────────────────────────────────────

def _chat_db(path, rows):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB,
                              handle_id INTEGER, is_from_me INTEGER, date INTEGER,
                              item_type INTEGER DEFAULT 0);
        INSERT INTO handle VALUES (1, '+596696000000');
        INSERT INTO chat VALUES (1, 'iMessage;-;+596696000000');
    """)
    for rowid, text, body, me, item_type in rows:
        con.execute("INSERT INTO message VALUES (?,?,?,?,?,?,?)",
                    (rowid, text, body, 1, me, 785000000000000000, item_type))
        con.execute("INSERT INTO chat_message_join VALUES (1, ?)", (rowid,))
    con.commit()
    con.close()


def test_fetch_since_decode_et_filtre_item_type(tmp_path):
    db = tmp_path / "chat.db"
    _chat_db(db, [(1, "vieux", None, 0, 0),
                  (2, None, _blob("Klody, 12 fois 12"), 1, 0),
                  (3, "renommage de groupe", None, 0, 2)])
    con = im.open_db(db)
    assert im.max_rowid(con) == 3
    msgs = im.fetch_since(con, 1)
    assert [(m.rowid, m.text, m.handle, m.is_from_me) for m in msgs] == \
        [(2, "Klody, 12 fois 12", "+596696000000", True)]
    assert msgs[0].chat_guid.startswith("iMessage;")
    assert msgs[0].ts > im.APPLE_EPOCH


def test_open_db_lecture_seule(tmp_path):
    db = tmp_path / "chat.db"
    _chat_db(db, [])
    with pytest.raises(sqlite3.OperationalError):
        im.open_db(db).execute("INSERT INTO handle VALUES (9, 'x')")


def test_probe_n_affiche_aucun_texte(tmp_path, capsys):
    db = tmp_path / "chat.db"
    _chat_db(db, [(1, "secret très privé", None, 0, 0), (2, None, None, 0, 0)])
    assert im.probe(db) == 0
    out = capsys.readouterr().out
    assert "secret" not in out and "+596" not in out
    assert "ROWID max 2" in out and "1/2" in out


def test_probe_acces_refuse(tmp_path, capsys):
    assert im.probe(tmp_path / "absent" / "chat.db") == 2
    assert "Accès complet au disque" in capsys.readouterr().out


def test_ouvrir_avec_attente_reessaie_sans_boucler(tmp_path):
    dodos = []
    with pytest.raises(sqlite3.OperationalError):
        im.ouvrir_avec_attente(tmp_path / "absent" / "chat.db", dormir=dodos.append, essais=3)
    assert dodos == [im.ATTENTE_ACCES_S] * 2


def test_ouvrir_avec_attente_finit_par_ouvrir(tmp_path):
    db = tmp_path / "chat.db"
    dodos = []

    def dormir(s):
        dodos.append(s)
        _chat_db(db, [])  # l'accès arrive pendant l'attente

    con = im.ouvrir_avec_attente(db, dormir=dormir, essais=5)
    assert im.max_rowid(con) == 0 and len(dodos) == 1


# ── Traitement d'un lot ──────────────────────────────────────────────────────

def _horloge():
    t = [1000.0]

    def maintenant():
        t[0] += 1.0
        return t[0]
    return maintenant


def test_traiter_envoie_une_fois_malgre_la_copie_recue():
    envois, requetes = [], []
    msgs = [M("Klody, 12 fois 12", MOI, me=True, rowid=10),
            M("Klody, 12 fois 12", MOI, me=False, rowid=11),   # copie reçue
            M("🤖 144", MOI, me=True, rowid=12),                # sa réponse
            M("coucou", MOI, rowid=13)]

    def run(q):
        requetes.append(q)
        return "144"

    last = im.traiter(msgs, ALLOWED, PREFIX, im.Dedoublonneur(), run,
                      lambda h, t: envois.append((h, t)), _horloge())
    assert last == 13
    assert requetes == ["12 fois 12"]
    assert envois == [(MOI, "🤖 144")]


def test_traiter_a_blanc_n_envoie_rien(capsys):
    last = im.traiter([M("Klody, ping", MOI, rowid=5)], ALLOWED, PREFIX, im.Dedoublonneur(),
                      lambda q: "pong", None, _horloge())
    assert last == 5
    assert "[à blanc] → '🤖 pong'" in capsys.readouterr().out


def test_traiter_survit_a_une_requete_et_a_un_envoi_en_echec():
    envois = []

    def run(q):
        raise ValueError("boum")

    def envoyer(h, t):
        envois.append(t)
        raise subprocess.CalledProcessError(1, "osascript")

    msgs = [M("Klody, a", MOI, rowid=1), M("Klody, b", MOI, rowid=2)]
    assert im.traiter(msgs, ALLOWED, PREFIX, im.Dedoublonneur(), run, envoyer, _horloge()) == 2
    assert envois == ["🤖 Erreur côté Klody : ValueError"] * 2


def test_formater_reponse_borne():
    assert im.formater_reponse("  x  ") == "🤖 x"
    assert len(im.formater_reponse("a" * 5000)) == len(im.BOT_MARK) + im.MAX_REPLY


def test_send_imessage_texte_en_argv(monkeypatch):
    vu = {}

    def faux_run(argv, **kw):
        vu["argv"], vu["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(im.subprocess, "run", faux_run)
    piege = '" & do shell script "rm -rf ~" & "'
    im.send_imessage(MOI, piege)
    assert vu["argv"][:2] == ["/usr/bin/osascript", "-e"]
    assert piege not in vu["argv"][2]            # jamais interpolé dans le script
    assert vu["argv"][3:] == [piege, MOI]
    assert vu["kw"]["check"] is True and "shell" not in vu["kw"]


# ── État + main ──────────────────────────────────────────────────────────────

def test_etat_aller_retour_et_corrompu(tmp_path):
    p = tmp_path / "s.json"
    assert im.load_state(p) == {}
    im.save_state(p, {"last_rowid": 42})
    assert im.load_state(p) == {"last_rowid": 42}
    p.write_text("[1, 2]")
    assert im.load_state(p) == {}
    p.write_text("{pas du json")
    assert im.load_state(p) == {}


def test_main_refuse_sans_expediteur_autorise(monkeypatch):
    monkeypatch.setenv("KLODY_IMSG_ALLOWED", " , ")

    def interdit(*a, **k):
        raise AssertionError("chat.db ne doit pas être ouvert")

    monkeypatch.setattr(im, "ouvrir_avec_attente", interdit)
    assert im.main([]) == im.EXIT_NON_CONFIGURE == 0


def test_main_un_passage_a_blanc(monkeypatch, tmp_path, capsys):
    import config
    db = tmp_path / "chat.db"
    _chat_db(db, [(1, "historique", None, 0, 0)])
    monkeypatch.setenv("KLODY_IMSG_ALLOWED", MOI)
    monkeypatch.setattr(im, "CHAT_DB", db)
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(im, "make_agent_runner", lambda d: (lambda q: f"vu:{q}"))
    etat = tmp_path / "imessage_state.json"
    im.save_state(etat, {"last_rowid": 0})
    assert im.main(["--dry-run", "--once"]) == 0
    assert im.load_state(etat) == {"last_rowid": 1}   # historique lu mais pas adressé à Klody

    con = sqlite3.connect(db)
    con.execute("INSERT INTO message VALUES (2, 'Klody, ping', NULL, 1, 1, 0, 0)")
    con.commit()
    con.close()
    assert im.main(["--dry-run", "--once"]) == 0
    assert "🤖 vu:ping" in capsys.readouterr().out
    assert im.load_state(etat) == {"last_rowid": 2}


def test_main_premier_lancement_ignore_l_historique(monkeypatch, tmp_path):
    import config
    db = tmp_path / "chat.db"
    _chat_db(db, [(7, "Klody, vieux message", None, 1, 0)])
    monkeypatch.setenv("KLODY_IMSG_ALLOWED", MOI)
    monkeypatch.setattr(im, "CHAT_DB", db)
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path)

    def run(q):
        raise AssertionError("l'historique ne doit pas être traité")

    monkeypatch.setattr(im, "make_agent_runner", lambda d: run)
    assert im.main(["--once"]) == 0


def test_main_probe(monkeypatch, tmp_path):
    db = tmp_path / "chat.db"
    _chat_db(db, [])
    monkeypatch.setattr(im, "CHAT_DB", db)
    monkeypatch.setattr(im, "probe", lambda: 0)
    assert im.main(["--probe"]) == 0


# ── Coque KlodyMessages.app + installeur ─────────────────────────────────────

import plistlib
import shutil
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
APP = RACINE / "canaux" / "imessage_app"
INSTALLEUR = RACINE / "scripts" / "install-klody-messages.sh"


def test_info_plist_coque():
    p = plistlib.loads((APP / "Info.plist").read_bytes())
    assert p["CFBundleIdentifier"] == "com.klody.imessage"
    assert p["CFBundleExecutable"] == "KlodyMessages"
    assert p["KlodyRepo"] == "__REPO__"            # remplacé à la construction
    assert p["LSBackgroundOnly"] is True
    assert "Messages" in p["NSAppleEventsUsageDescription"]


def test_droit_apple_events_sinon_envoi_refuse_en_silence():
    e = plistlib.loads((APP / "KlodyMessages.entitlements").read_bytes())
    assert e == {"com.apple.security.automation.apple-events": True}


def test_coque_lance_le_bon_module():
    src = (APP / "main.swift").read_text()
    assert '["-m", "canaux.imessage"]' in src
    assert "canaux/imessage.py" in src


def test_installeur_syntaxe_et_usage():
    assert subprocess.run(["bash", "-n", str(INSTALLEUR)]).returncode == 0
    r = subprocess.run(["bash", str(INSTALLEUR)], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr


def test_installeur_hors_launchagents_car_opt_in():
    """install-launchagents.sh installe tout launchagents/ : le canal n'y est pas."""
    assert not (RACINE / "launchagents" / "com.klody.imessage.plist").exists()
    src = INSTALLEUR.read_text()
    assert '"SuccessfulExit": False' in src        # sortie 0 « non configuré » ≠ boucle
    assert "rev-parse --git-common-dir" in src      # refuse un worktree
    assert "KLODY_IMSG_ALLOWED" in src


@pytest.mark.skipif(shutil.which("swiftc") is None or shutil.which("codesign") is None,
                    reason="macOS + outils Xcode requis")
def test_construction_ad_hoc_et_coque_relaie_le_code(tmp_path):
    env = {**__import__("os").environ, "SIGN_ID": "-"}
    r = subprocess.run(["bash", str(INSTALLEUR), "build", str(tmp_path)],
                       capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr
    binaire = tmp_path / "KlodyMessages.app" / "Contents" / "MacOS" / "KlodyMessages"
    r = subprocess.run([str(binaire)], capture_output=True, text=True,
                       env={**env, "KLODY_IMSG_REPO": str(tmp_path / "absent")}, timeout=30)
    assert r.returncode == 78 and "dépôt introuvable" in r.stderr
