"""Canal iMessage local : parler à Klody depuis l'iPhone, sans cloud (façon « Dot »).

Boucle : ``chat.db`` (lecture seule) → filtre expéditeur + préfixe « Klody » →
orchestrateur in-process, **liste blanche d'outils en lecture** → réponse ``🤖 …``
par Messages.app (AppleScript, texte passé en argv).

Issu du spike du 2026-11-19 (``~/Archives/spikes/imessage-20261119/``), passé de
bout en bout le 2026-11-21 : « Klody, 12 fois 12 » depuis l'iPhone → « 🤖 144 »
reçu en 7,7 s.

Pourquoi une liste blanche et pas « tout sauf approval.py » : approval.py laisse
passer 54 outils natifs sur 69 (run_applescript, run_shortcut, git_control,
mqtt_publish…) — mesuré le 2026-11-19. Un canal joignable par simple message n'a
droit qu'à de la lecture. ``web`` exclu : un mail piégé lu → fetch d'une URL =
exfiltration.

Accès macOS (TCC) — vécu le 2026-11-21 : l'Accès complet au disque donné à
Terminal.app ne couvre PAS une commande venue de Claude.app (boîte ``!``,
panneau Terminal de Claude, ``osascript … do script`` injecté) : TCC l'attribue
au processus responsable. En production le canal tourne donc comme enfant de la
coque signée ``KlodyMessages.app`` (``canaux/imessage_app/``), qui porte seule
l'Accès complet au disque et l'Automatisation → Messages. Ne jamais donner ces
droits au Python du venv ni à Claude.app.

Usage (depuis la racine du dépôt) ::

    python -m canaux.imessage --probe      # lit chat.db, n'affiche aucun texte
    python -m canaux.imessage --dry-run    # répond dans le journal, n'envoie rien
    python -m canaux.imessage              # écoute + envoi réel

Réglages (``.env``) : ``KLODY_IMSG_ALLOWED`` (numéros / Apple ID séparés par des
virgules, OBLIGATOIRE — vide = le canal refuse de démarrer), ``KLODY_IMSG_PREFIX``
(défaut « klody »).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3

# osascript seulement, argv sans shell (cf. send_imessage)
import subprocess  # nosec B404
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from canaux.briefing import HEURE_DEFAUT, HEURES_DEFAUT, Dependances, source_briefing, synthese_llm
from canaux.proactif import (
    MAX_PAR_JOUR,
    OUTILS_CANAL,
    GmailMCP,
    Registre,
    client_llm,
    construire_moteur,
    contexte_date,
    outils_locaux,
    resumeur_llm,
)
from canaux.rendez_vous import AVANCE_MIN, source_rendez_vous

logger = logging.getLogger("klody.imessage")

CHAT_DB = Path.home() / "Library/Messages/chat.db"
BOT_MARK = "🤖 "  # toute réponse commence par ça → jamais retraitée (anti-boucle)
MAX_REPLY = 1500
POLL_S = 3.0
ATTENTE_ACCES_S = 60.0  # chat.db refusé (TCC) : on réessaie sans boucler launchd
FENETRE_DOUBLON_S = 60.0
# Date Apple = ns depuis 2001-01-01
APPLE_EPOCH = 978307200

# Sortie « rien à faire » : launchd (KeepAlive SuccessfulExit=false) ne relance pas.
EXIT_NON_CONFIGURE = 0


# ── Lecture chat.db ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Msg:
    rowid: int
    text: str
    handle: str
    is_from_me: bool
    chat_guid: str
    ts: float


def decode_attributed_body(blob: bytes | None) -> str:
    """Extrait le texte d'un attributedBody (NSAttributedString en typedstream).

    Depuis Ventura, ``message.text`` est souvent NULL : le texte n'est que dans ce
    blob. Format : … b"NSString" <5 octets> b"+" <longueur> <utf-8> …
    Longueur : 1 octet, ou 0x81 + 2 octets LE, ou 0x82 + 4 octets LE.
    """
    if not blob:
        return ""
    i = blob.find(b"NSString")
    if i < 0:
        return ""
    j = blob.find(b"+", i + len(b"NSString"))
    if j < 0 or j + 1 >= len(blob):
        return ""
    n = blob[j + 1]
    k = j + 2
    if n == 0x81:
        n = int.from_bytes(blob[k:k + 2], "little")
        k += 2
    elif n == 0x82:
        n = int.from_bytes(blob[k:k + 4], "little")
        k += 4
    return blob[k:k + n].decode("utf-8", errors="replace")


def open_db(path: Path = CHAT_DB) -> sqlite3.Connection:
    # mode=ro : jamais d'écriture dans la base de Messages
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)


_Q = """
SELECT m.ROWID, m.text, m.attributedBody, COALESCE(h.id, ''), m.is_from_me,
       COALESCE(c.guid, ''), m.date
FROM message m
LEFT JOIN handle h ON h.ROWID = m.handle_id
LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
LEFT JOIN chat c ON c.ROWID = cmj.chat_id
WHERE m.ROWID > ? AND m.item_type = 0
ORDER BY m.ROWID
LIMIT 200
"""


def fetch_since(con: sqlite3.Connection, rowid: int) -> list[Msg]:
    out = []
    for r in con.execute(_Q, (rowid,)):
        text = r[1] or decode_attributed_body(r[2])
        ts = (r[6] or 0) / 1e9 + APPLE_EPOCH if r[6] else 0.0
        out.append(Msg(r[0], text.strip(), r[3], bool(r[4]), r[5], ts))
    return out


def max_rowid(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])


# ── Filtre : qui a le droit de parler à Klody ────────────────────────────────

def norm_handle(h: str) -> str:
    h = h.strip().lower()
    return h if "@" in h else re.sub(r"[^\d+]", "", h)


def parse_allowed(raw: str) -> set[str]:
    return {norm_handle(h) for h in raw.split(",") if h.strip()}


def prefix_re(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(prefix)}\b[\s,:!-]*", re.IGNORECASE)


def extract_command(m: Msg, allowed: set[str], prefix: re.Pattern[str]) -> str | None:
    """Renvoie la requête si le message s'adresse à Klody, sinon None."""
    if not m.text or m.text.startswith(BOT_MARK.strip()):
        return None  # vide ou réponse de Klody lui-même
    if norm_handle(m.handle) not in allowed:
        return None
    mt = prefix.match(m.text)
    if not mt:
        return None  # conversation normale avec soi-même : on ne touche pas
    q = m.text[mt.end():].strip()
    return q or None


class Dedoublonneur:
    """S'écrire à soi-même = 2 lignes dans chat.db (envoyé + reçu), même texte.

    Vécu au spike du 2026-11-21 : #6968 traité, #6969 (sa copie reçue) ignoré.
    """

    def __init__(self, fenetre_s: float = FENETRE_DOUBLON_S) -> None:
        self.fenetre_s = fenetre_s
        self._vus: dict[str, float] = {}

    def nouveau(self, q: str, maintenant: float) -> bool:
        self._vus = {k: v for k, v in self._vus.items() if maintenant - v < self.fenetre_s}
        if q in self._vus:
            return False
        self._vus[q] = maintenant
        return True


# ── Outils : liste blanche en lecture ────────────────────────────────────────

REFUS = ("Outil indisponible par iMessage : ce canal est en lecture seule. "
         "Dis à l'utilisateur de lancer l'action depuis klody-ui.")

ALLOWED_NATIVE = frozenset({
    "search_books", "library_catalog", "rappeler_memoire", "get_skills", "list_skills",
    "spotlight_search", "list_shortcuts", "list_files", "read_file", "search_in_files",
    "find_relevant_files",
})
ALLOWED_MCP_SERVERS = frozenset({"gmail"})  # + filtre approval.py (send/draft gardés)


def tool_allowed(name: str, requires_approval: Callable[..., bool]) -> bool:
    if name.startswith("mcp__"):
        server = name.split("__", 2)[1] if name.count("__") >= 2 else ""
        return server in ALLOWED_MCP_SERVERS and not requires_approval(name)
    return name in ALLOWED_NATIVE


def preparer_orchestrateur(orch: Any, requires_approval: Callable[..., bool],
                           bloques: list[str],
                           locaux: dict[str, Callable[[dict], str]] | None = None) -> None:
    """Met un Orchestrator au régime iMessage (même régime que ``/api/siri``).

    - modèle ÉPINGLÉ : pas de bascule brain→coder, le cache de préfixe survit
      (PR #324 — bascule = brain évincé + coder chargé à froid à chaque requête) ;
    - routage progressif coupé : sinon ``_active_tools`` réintroduit MCP et outils
      gardés par-dessus la liste blanche (PR #326) ;
    - schémas réduits à la liste blanche : 89 k tokens de prompt pour 405 outils
      = 165 s de prefill à froid (mesuré le 2026-11-19) ;
    - pas de pistes de fichiers du dépôt (1re recherche = ré-encodage complet) ;
    - exécution gardée : la liste blanche est REVÉRIFIÉE à l'appel, le modèle ne
      se contente pas de ne pas voir les autres outils.

    Résultat mesuré au spike le 2026-11-21 : 4-22 s par réponse (19-273 s avant).

    ``locaux`` : outils propres au canal (``programmer_rappel``, ``noter`` —
    ``canaux/proactif.py``), exécutés ici sans passer par l'orchestrateur. Ils
    n'écrivent que dans le registre du canal : aucun effet hors de lui.
    """
    orch._pinned_model = orch.llm.model
    orch._context_routing_enabled = False
    orch._active_tools = None
    if orch.mcp is not None:
        try:
            # Le routage progressif (défaut) n'a rien découvert à l'init.
            orch.tools = [*orch.tools, *orch.mcp.discover_selected(set(ALLOWED_MCP_SERVERS))]
        except Exception as exc:
            logger.warning("[imessage] découverte MCP %s échouée : %s",
                           sorted(ALLOWED_MCP_SERVERS), exc)
    vus: set[str] = set()
    garder = []
    for t in orch.tools:
        nom = t["function"]["name"]
        if nom not in vus and tool_allowed(nom, requires_approval):
            vus.add(nom)
            garder.append(t)
    locaux = locaux or {}
    garder += [s for s in OUTILS_CANAL if s["function"]["name"] in locaux]
    orch.tools = garder
    orch._relevant_files_section = lambda _q: ""
    executer = orch._execute_tool

    def garde(name: str, args: dict) -> str:
        if name in locaux:
            resultat = locaux[name](args if isinstance(args, dict) else {})
            logger.info("[imessage] outil local %s → %s", name, resultat[:120])
            return resultat
        if not tool_allowed(name, requires_approval):
            bloques.append(name)
            logger.warning("[imessage] outil refusé : %s", name)
            return REFUS
        return str(executer(name, args))

    orch._execute_and_display = garde


def make_agent_runner(memory_dir: Path,
                      locaux: dict[str, Callable[[dict], str]] | None = None,
                      horloge: Callable[[], float] = time.time) -> Callable[[str], str]:
    """Charge l'orchestrateur KlodyAI ; renvoie ``requête → réponse texte``.

    La date du jour précède la requête : sans elle, « demain 9 h » n'a pas de
    référence (le prompt système ne la porte pas) et ``programmer_rappel`` ne
    peut pas calculer une heure absolue.
    """
    from agent.approval import requires_approval
    from agent.memory import ConversationMemory
    from agent.orchestrator import Orchestrator
    from api.server import _strip_markdown

    mem_file = memory_dir / "memory_imessage.json"
    bloques: list[str] = []

    def run(query: str) -> str:
        memory = (ConversationMemory.load_from_file(mem_file) if mem_file.exists()
                  else ConversationMemory(session_id="imessage"))
        orch = Orchestrator(memory)
        preparer_orchestrateur(orch, requires_approval, bloques, locaux)
        try:
            orch.run(f"{contexte_date(horloge())}\n{query}")
        finally:
            orch.close()
        last = next((m["content"] for m in reversed(memory.messages)
                     if m["role"] == "assistant" and m.get("content")),
                    "Je n'ai pas pu répondre.")
        return str(_strip_markdown(last))

    return run


# ── Envoi via Messages.app ───────────────────────────────────────────────────

_AS = """on run argv
  set theText to item 1 of argv
  set theHandle to item 2 of argv
  tell application "Messages"
    set svc to 1st account whose service type = iMessage
    send theText to participant theHandle of svc
  end tell
end run"""


def send_imessage(handle: str, text: str) -> None:
    # texte passé en argv → aucune interpolation dans le script (pas d'injection AS)
    subprocess.run(  # nosec B603
        ["/usr/bin/osascript", "-e", _AS, text, handle],
        check=True, capture_output=True, timeout=30,
    )


def formater_reponse(reply: str) -> str:
    return BOT_MARK + reply.strip()[:MAX_REPLY]


# ── État + boucle ────────────────────────────────────────────────────────────

def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, st: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(path)


def probe(db: Path | None = None) -> int:
    """Vérifie l'accès à chat.db SANS afficher de contenu (textes, numéros)."""
    try:
        con = open_db(db or CHAT_DB)
        top = max_rowid(con)
    except sqlite3.OperationalError as e:
        print(f"chat.db illisible : {e}\n→ Accès complet au disque requis "
              "(pour KlodyMessages.app, jamais pour Python ni Claude.app).")
        return 2
    msgs = fetch_since(con, max(0, top - 20))
    vides = sum(1 for m in msgs if not m.text)
    print(f"chat.db OK — ROWID max {top} ; {len(msgs) - vides}/{len(msgs)} "
          "derniers messages décodés")
    return 0


def traiter(msgs: list[Msg], allowed: set[str], prefix: re.Pattern[str],
            dedup: Dedoublonneur, run: Callable[[str], str],
            envoyer: Callable[[str, str], None] | None,
            maintenant: Callable[[], float] = time.time) -> int:
    """Traite un lot ; renvoie le dernier ROWID vu. ``envoyer=None`` = à blanc."""
    last = 0
    for m in msgs:
        last = m.rowid
        q = extract_command(m, allowed, prefix)
        if q is None or not dedup.nouveau(q, maintenant()):
            continue
        logger.info("← #%d (%d car.)", m.rowid, len(q))
        t0 = maintenant()
        try:
            reply = run(q)
        except Exception as e:  # le canal ne meurt pas sur une requête
            logger.exception("[imessage] requête #%d en échec", m.rowid)
            reply = f"Erreur côté Klody : {type(e).__name__}"
        texte = formater_reponse(reply)
        logger.info("→ #%d en %.1f s (%d car.)", m.rowid, maintenant() - t0, len(texte))
        if envoyer is None:
            print(f"[à blanc] ← {q!r}\n[à blanc] → {texte!r}", flush=True)
            continue
        try:
            envoyer(m.handle, texte)
        except (subprocess.SubprocessError, OSError) as e:
            # Typiquement : Automatisation → Messages refusée. On continue.
            logger.error("[imessage] envoi #%d impossible : %s", m.rowid, e)
    return last


def ouvrir_avec_attente(db: Path, dormir: Callable[[float], None] = time.sleep,
                        essais: int | None = None) -> sqlite3.Connection:
    """Ouvre chat.db, en attendant l'Accès complet au disque s'il manque.

    Sans ça, launchd relancerait en boucle un processus qui meurt aussitôt.
    """
    n = 0
    while True:
        try:
            con = open_db(db)
            max_rowid(con)
            return con
        except sqlite3.OperationalError as e:
            if n == 0:
                logger.error("[imessage] chat.db illisible (%s) — accorder l'Accès complet "
                             "au disque à KlodyMessages.app ; nouvel essai toutes les %.0f s",
                             e, ATTENTE_ACCES_S)
            n += 1
            if essais is not None and n >= essais:
                raise
            dormir(ATTENTE_ACCES_S)


def premier_destinataire(raw: str) -> str:
    """Le destinataire des messages spontanés : ``KLODY_PROACTIF_DEST``, sinon le
    PREMIER expéditeur autorisé (l'ordre compte, d'où la lecture du brut)."""
    dest = os.environ.get("KLODY_PROACTIF_DEST", "").strip()
    return dest or next(h.strip() for h in raw.split(",") if h.strip())


def demarrer_proactif(registre: Registre, dest: str,
                      envoyer: Callable[[str, str], None] | None,
                      data_dir: Path, mcp_servers: dict) -> threading.Thread:
    """Lance le moteur proactif en fil de fond (même processus = mêmes droits TCC)."""
    def envoi(texte: str) -> None:
        if envoyer is None:
            print(f"[à blanc · proactif] → {formater_reponse(texte)!r}", flush=True)
        else:
            envoyer(dest, formater_reponse(texte))

    gmail = GmailMCP(mcp_servers["gmail"]) if "gmail" in mcp_servers else None
    client, modele = client_llm()
    resumer = resumeur_llm(client, modele) if gmail is not None else None
    # La coque pose KLODY_IMSG_LAUNCHER (chemin du bundle) : relancée en
    # `--agenda`, elle lit EventKit sous SES droits TCC.
    bundle = os.environ.get("KLODY_IMSG_LAUNCHER", "")
    lanceur = Path(bundle) / "Contents" / "MacOS" / "KlodyMessages" if bundle else None
    briefing = None
    if os.environ.get("KLODY_BRIEFING", "1") != "0":
        dep = Dependances(lanceur=lanceur, resumer=synthese_llm(client, modele),
                          chercher_mails=gmail.chercher if gmail is not None else None)
        # Midi et soir demandés le 2026-11-26 ; « 0 » coupe un moment.
        briefing = source_briefing(registre, dep, data_dir,
                                   os.environ.get("KLODY_BRIEFING_HEURE", HEURE_DEFAUT), {
                                       "midi": os.environ.get("KLODY_BRIEFING_MIDI", HEURES_DEFAUT["midi"]),
                                       "soir": os.environ.get("KLODY_BRIEFING_SOIR", HEURES_DEFAUT["soir"]),
                                   })
    rdv = None
    if lanceur is not None and os.environ.get("KLODY_RDV", "1") != "0":
        rdv = source_rendez_vous(lanceur, avance_min=int(
            os.environ.get("KLODY_RDV_AVANCE_MIN", AVANCE_MIN)))
    moteur = construire_moteur(registre, envoi, data_dir / "notifications", gmail, resumer,
                               int(os.environ.get("KLODY_PROACTIF_MAX_JOUR", MAX_PAR_JOUR)),
                               briefing, rdv)
    fil = threading.Thread(target=moteur.boucle, args=(threading.Event(),),
                           name="klody-proactif", daemon=True)
    fil.start()
    return fil


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m canaux.imessage")
    ap.add_argument("--probe", action="store_true", help="vérifie chat.db, n'affiche aucun texte")
    ap.add_argument("--dry-run", action="store_true", help="n'envoie rien, affiche")
    ap.add_argument("--once", action="store_true", help="un seul passage")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if a.probe:
        return probe()

    import config  # charge .env

    allowed = parse_allowed(os.environ.get("KLODY_IMSG_ALLOWED", ""))
    if not allowed:
        logger.error("[imessage] KLODY_IMSG_ALLOWED vide : canal arrêté "
                     "(aucun expéditeur autorisé). À poser dans .env.")
        return EXIT_NON_CONFIGURE
    prefix = prefix_re(os.environ.get("KLODY_IMSG_PREFIX", "klody"))
    state_path = config.MEMORY_DIR / "imessage_state.json"

    con = ouvrir_avec_attente(CHAT_DB)
    st = load_state(state_path)
    # 1er lancement (aucun état) : on part du ROWID max, l'historique n'est jamais rejoué.
    # Test `in`, pas `or` : un état à 0 est un état, pas une absence d'état.
    last = int(st["last_rowid"]) if "last_rowid" in st else max_rowid(con)
    registre = Registre(config.MEMORY_DIR / "proactif.db")
    run = make_agent_runner(config.MEMORY_DIR, outils_locaux(registre))
    logger.info("[imessage] écoute depuis ROWID %d, %d expéditeur(s)%s",
                last, len(allowed), " — À BLANC" if a.dry_run else "")
    dedup = Dedoublonneur()
    envoyer = None if a.dry_run else send_imessage
    if not a.once and os.environ.get("KLODY_PROACTIF", "1") != "0":
        demarrer_proactif(registre, premier_destinataire(os.environ["KLODY_IMSG_ALLOWED"]),
                          envoyer, config.MEMORY_DIR, config.MCP_SERVERS)
    while True:
        try:
            msgs = fetch_since(con, last)
        except sqlite3.OperationalError as e:
            logger.warning("[imessage] lecture chat.db : %s — réouverture", e)
            con.close()
            con = ouvrir_avec_attente(CHAT_DB)
            continue
        if msgs:
            last = max(last, traiter(msgs, allowed, prefix, dedup, run, envoyer))
            save_state(state_path, {"last_rowid": last})
        if a.once:
            return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
