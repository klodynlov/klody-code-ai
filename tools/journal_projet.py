"""Journal de projet : décisions, état, tâches et incidents, d'une session à l'autre.

Ce que les autres mémoires ne font pas :
- la mémoire long terme (`remember_fact`) garde des FAITS, réécrits sous une clé :
  « le projet X utilise SwiftData ». Elle ne dit ni quand ni pourquoi ;
- le plan de tâche (`plan_tache`) vit dans UNE session et découpe UNE demande.

Le journal est daté et cumulatif, rangé par projet : « 27/11 décision : on garde
Qwen, parce que… », « état : PR #346 ouverte », « tâche : mesurer la marge de la
garde ». Reprendre un projet trois semaines plus tard = lire son journal.

Stockage : SQLite `journal_projet.db` dans `config.MEMORY_DIR`, lu à l'APPEL (pas
à l'import) pour que les tests et un second profil (`KLODY_DATA_DIR`) le
redirigent. Une lecture ne crée jamais la base : la section du tour est calculée
à chaque message, elle ne doit pas écrire sur le disque.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import config

TYPES = ("decision", "etat", "tache", "incident", "note")
LIBELLES = {"decision": "Décision", "etat": "État", "tache": "Tâche",
            "incident": "Incident", "note": "Note"}
_ALIAS_TYPE = {"décision": "decision", "état": "etat", "tâche": "tache", "todo": "tache",
               "bug": "incident", "probleme": "incident", "problème": "incident"}
TEXTE_MAX = 1000
NOM_MAX = 60
LECTURE_MAX = 50
SECTION_MAX = 1200
PROJETS_DANS_LE_TOUR = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entrees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    projet TEXT NOT NULL,
    nom TEXT NOT NULL,
    type TEXT NOT NULL,
    texte TEXT NOT NULL,
    statut TEXT,
    resultat TEXT,
    session TEXT,
    cree_le TEXT NOT NULL,
    clos_le TEXT
);
CREATE INDEX IF NOT EXISTS entrees_projet ON entrees(projet, id);
"""


def chemin_base() -> Path:
    return Path(config.MEMORY_DIR) / "journal_projet.db"


def _connexion(creer: bool) -> sqlite3.Connection | None:
    chemin = chemin_base()
    if not creer and not chemin.exists():
        return None
    if creer:
        chemin.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(chemin, timeout=5)
    cx.row_factory = sqlite3.Row
    if creer:
        cx.executescript(_SCHEMA)
    return cx


def _plat(texte: str) -> str:
    return unicodedata.normalize("NFKD", texte.lower()).encode("ascii", "ignore").decode()


def cle_projet(nom: str) -> str:
    """Casse, accents et séparateurs normalisés : « Klody Code » et
    « klody-code » sont un même projet. L'orthographe, non : « KaribTruck »
    (`karibtruck`) et « Karib Truck » (`karib-truck`) restent deux projets —
    les fusionner en devinant serait mentir."""
    return re.sub(r"[^a-z0-9]+", "-", _plat(nom)).strip("-")[:NOM_MAX]


def _ligne(texte: Any, maxi: int) -> str:
    return " ".join(str(texte or "").split())[:maxi]


def _type(v: Any) -> str | None:
    t = _ligne(v, 20).lower()
    t = _ALIAS_TYPE.get(t, t)
    return t if t in TYPES else None


def _date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y")
    except ValueError:
        return iso[:10]


def _format(e: sqlite3.Row) -> str:
    marque = ""
    if e["type"] == "tache":
        marque = "✅ " if e["statut"] == "faite" else "☐ "
    fin = f" → {e['resultat']}" if e["resultat"] else ""
    return f"#{e['id']} {_date(e['cree_le'])} {marque}{e['texte']}{fin}"


# ── Actions ─────────────────────────────────────────────────────────────────

def noter(projet: str, type_: str, texte: str, session: str | None = None,
          maintenant: datetime | None = None) -> str:
    nom = _ligne(projet, NOM_MAX)
    cle = cle_projet(nom)
    if not cle:
        return "Rien noté : `projet` est vide (nomme le projet, ex. « KaribTruck »)."
    t = _type(type_)
    if t is None:
        return f"Rien noté : `type` doit être {', '.join(TYPES)}."
    contenu = _ligne(texte, TEXTE_MAX + 1)
    if not contenu:
        return "Rien noté : `texte` est vide."
    if len(contenu) > TEXTE_MAX:
        return (f"Rien noté : texte trop long (max {TEXTE_MAX} car.). Une entrée = un "
                "fait daté ; découpe en plusieurs entrées.")
    quand = (maintenant or datetime.now()).isoformat(timespec="seconds")
    cx = _connexion(creer=True)
    if cx is None:  # pragma: no cover — creer=True ouvre toujours
        return "Rien noté : journal inaccessible."
    with closing(cx), cx:
        cur = cx.execute(
            "INSERT INTO entrees (projet, nom, type, texte, statut, session, cree_le) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cle, nom, t, contenu, "ouverte" if t == "tache" else None, session, quand))
        numero = cur.lastrowid
    return f"Noté dans le journal de {nom} : {LIBELLES[t].lower()} #{numero}."


def clore(numero: Any, resultat: str = "", maintenant: datetime | None = None) -> str:
    try:
        n = int(numero)
    except (TypeError, ValueError):
        return "Rien clos : `numero` doit être le numéro d'une tâche (#12 → 12)."
    cx = _connexion(creer=False)
    if cx is None:
        return "Rien clos : le journal est vide."
    with closing(cx), cx:
        e = cx.execute("SELECT * FROM entrees WHERE id = ?", (n,)).fetchone()
        if e is None:
            return f"Rien clos : pas d'entrée #{n}."
        if e["type"] != "tache":
            return f"Rien clos : #{n} est une {LIBELLES[e['type']].lower()}, pas une tâche."
        if e["statut"] == "faite":
            return f"#{n} était déjà close."
        quand = (maintenant or datetime.now()).isoformat(timespec="seconds")
        cx.execute("UPDATE entrees SET statut = 'faite', resultat = ?, clos_le = ? WHERE id = ?",
                   (_ligne(resultat, TEXTE_MAX) or None, quand, n))
    return f"Tâche #{n} close ({e['nom']})."


def _nom(cx: sqlite3.Connection, cle: str) -> str | None:
    """Nom d'affichage = celui de l'entrée la plus RÉCENTE : « klody code » puis
    « Klody-Code » s'affiche « Klody-Code », sans réécrire les anciennes entrées."""
    r = cx.execute("SELECT nom FROM entrees WHERE projet = ? ORDER BY id DESC LIMIT 1",
                   (cle,)).fetchone()
    return r["nom"] if r else None


_DERNIERES = "SELECT * FROM entrees WHERE projet = ? AND type = ? ORDER BY id DESC LIMIT ?"
_OUVERTES = ("SELECT * FROM entrees WHERE projet = ? AND type = ? AND statut = 'ouverte' "
             "ORDER BY id DESC LIMIT ?")


def _synthese(cx: sqlite3.Connection, cle: str, compacte: bool = False) -> list[str]:
    """Ce qu'il faut pour reprendre : dernier état, tâches ouvertes, décisions,
    incidents, notes — du plus récent au plus ancien."""
    def dernieres(t: str, n: int, ouvertes: bool = False) -> list[sqlite3.Row]:
        requete = _OUVERTES if ouvertes else _DERNIERES
        return cx.execute(requete, (cle, t, n)).fetchall()

    lignes: list[str] = []
    blocs = [("État actuel", dernieres("etat", 1)),
             ("Tâches ouvertes", dernieres("tache", 5 if compacte else 15, ouvertes=True)),
             ("Décisions récentes", dernieres("decision", 3 if compacte else 8)),
             ("Incidents récents", dernieres("incident", 2 if compacte else 5))]
    if not compacte:
        blocs.append(("Notes récentes", dernieres("note", 5)))
    for titre, rangs in blocs:
        if rangs:
            lignes.append(f"{titre} :")
            lignes += [f"- {_format(e)}" for e in rangs]
    return lignes


def lire(projet: str, type_: str = "", nombre: Any = 10) -> str:
    cle = cle_projet(_ligne(projet, NOM_MAX))
    if not cle:
        return "Lecture impossible : `projet` est vide. `action=projets` les liste."
    cx = _connexion(creer=False)
    if cx is None:
        return "Le journal est vide : aucun projet noté pour l'instant."
    with closing(cx):
        nom = _nom(cx, cle)
        if nom is None:
            return (f"Aucune entrée pour « {projet} ». Projets connus : "
                    + (", ".join(_noms(cx)) or "aucun") + ".")
        if type_:
            t = _type(type_)
            if t is None:
                return f"Lecture impossible : `type` doit être {', '.join(TYPES)}."
            try:
                n = max(1, min(int(nombre), LECTURE_MAX))
            except (TypeError, ValueError):
                n = 10
            rangs = cx.execute("SELECT * FROM entrees WHERE projet = ? AND type = ? "
                               "ORDER BY id DESC LIMIT ?", (cle, t, n)).fetchall()
            if not rangs:
                return f"Journal de {nom} : aucune entrée de type {t}."
            return "\n".join([f"Journal de {nom} — {LIBELLES[t].lower()}s :",
                              *[f"- {_format(e)}" for e in rangs]])
        corps = _synthese(cx, cle)
        total = cx.execute("SELECT COUNT(*) FROM entrees WHERE projet = ?", (cle,)).fetchone()[0]
    return "\n".join([f"Journal de {nom} ({total} entrée(s)) :", *corps])


def _noms(cx: sqlite3.Connection) -> list[str]:
    rangs = cx.execute("SELECT nom FROM entrees e WHERE id = (SELECT MAX(id) FROM entrees "
                       "WHERE projet = e.projet) ORDER BY id DESC").fetchall()
    return [r["nom"] for r in rangs]


def projets() -> str:
    cx = _connexion(creer=False)
    if cx is None:
        return "Le journal est vide : aucun projet noté pour l'instant."
    with closing(cx):
        rangs = cx.execute(
            "SELECT projet, MAX(id) AS dernier, COUNT(*) AS n, MAX(cree_le) AS quand, "
            "SUM(type = 'tache' AND statut = 'ouverte') AS ouvertes "
            "FROM entrees GROUP BY projet ORDER BY dernier DESC").fetchall()
        lignes = ["Projets du journal :"]
        for r in rangs:
            nom = _nom(cx, r["projet"])
            lignes.append(f"- {nom} : {r['n']} entrée(s), dernière le {_date(r['quand'])}, "
                          f"{r['ouvertes'] or 0} tâche(s) ouverte(s)")
    return "\n".join(lignes)


def executer(args: dict[str, Any], session: str | None = None) -> str:
    """Point d'entrée de l'outil `journal_projet`."""
    action = _ligne(args.get("action") or "lire", 20).lower()
    if action == "noter":
        return noter(args.get("projet", ""), args.get("type", ""), args.get("texte", ""),
                     session=session)
    if action == "lire":
        return lire(args.get("projet", ""), args.get("type", "") or "",
                    args.get("nombre", 10) or 10)
    if action == "clore":
        return clore(args.get("numero"), args.get("resultat", "") or "")
    if action == "projets":
        return projets()
    return f"Action « {action} » inconnue (noter, lire, clore, projets)."


# ── Rappel dans le tour ─────────────────────────────────────────────────────

def section_tour(requete: str) -> str:
    """Journal compact des projets NOMMÉS dans la demande (au plus 2).

    « on reprend KaribTruck » doit trouver son journal sans que le modèle pense à
    le lire : c'est exactement la situation où il ne sait pas qu'il existe. Rien
    si la base n'existe pas ou si aucun projet n'est nommé — jamais d'écriture.
    """
    texte = " " + re.sub(r"[^a-z0-9]+", " ", _plat(requete or "")) + " "
    if not texte.strip():
        return ""
    try:
        cx = _connexion(creer=False)
    except sqlite3.Error:
        return ""
    if cx is None:
        return ""
    sections: list[str] = []
    try:
        with closing(cx):
            for cle in [r["projet"] for r in cx.execute(
                    "SELECT projet, MAX(id) AS d FROM entrees GROUP BY projet ORDER BY d DESC")]:
                if f" {cle.replace('-', ' ')} " not in texte:
                    continue
                nom = _nom(cx, cle)
                lignes = _synthese(cx, cle, compacte=True)
                if lignes:
                    sections.append("\n".join([f"### {nom}", *lignes]))
                if len(sections) >= PROJETS_DANS_LE_TOUR:
                    break
    except sqlite3.Error:
        return ""
    if not sections:
        return ""
    corps = "\n\n".join(sections)
    if len(corps) > SECTION_MAX:
        corps = corps[: SECTION_MAX - 1] + "…"
    return ("\n\n## Journal de projet (outil journal_projet)\n" + corps +
            "\nCe sont des notes datées : vérifie avant de t'appuyer sur un état ancien.")
