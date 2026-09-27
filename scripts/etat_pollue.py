#!/usr/bin/env python3
"""Quelles sessions de `~/.klody/data` viennent des tests ou du banc, et pas de l'utilisateur ?

Écrit le 2026-09-27. Le dossier d'état persistant comptait ~6 000 sessions
`memory_*.json` quand la mémoire projet en annonçait 1 624 : la suite de tests
(`tests/integration/test_websocket_chat.py`, les scénarios de rejeu…) et le banc
(`bench/run.py`) y écrivaient depuis des semaines. L'historique KlodyAI de
l'utilisateur en était noyé, et `klody --continue` pouvait rouvrir une session
de test. Les deux fuites sont fermées (tests/garde_etat.py, `_isoler_etat` du
banc) ; ce script traite le stock.

On identifie par CONTENU, jamais par date ni par nom de fichier :

- **banc** — le premier message est le préambule que `bench/run.py` fabrique
  (« [Répertoire de travail : …/kb-xxxx] Les fichiers sont dans ce répertoire. »,
  ou `klody-bench-` avant le 2026-07-29). Un utilisateur ne tape pas ça.
- **test** — TOUTES ces conditions :
    1. le premier message utilisateur est une chaîne écrite telle quelle dans
       `tests/` (code ou fixture JSON) ;
    2. chaque réponse texte de l'assistant est, elle aussi, une chaîne des tests
       (au blanc près : le faux client streame des tokens sans espaces) — ou un
       message que l'orchestrateur fabrique lui-même (repli d'outil, budget
       épuisé). Aucune réponse n'est donc sortie d'un vrai modèle ;
    3. au moins un tour de l'assistant existe (texte ou appel d'outil) ;
    4. la session a duré moins d'une minute.

La condition 2 est celle qui protège : une session réelle ouverte sur « salut »
ou « explique en raisonnant » — deux chaînes présentes dans les tests — a été
trouvée dans le vrai dossier, avec une vraie réponse médicale du modèle. La
règle 1 seule l'aurait emportée ; la règle 2 la garde. Dans le doute, on garde :
une session de test oubliée coûte une ligne dans une liste, une session réelle
déplacée coûte un souvenir.

Rien n'est jamais supprimé. `--quarantaine` DÉPLACE les fichiers et écrit un
manifeste ; l'opération se défait par `mv`. Elle refuse de tourner tant que
l'API écoute sur :8000 — l'écrivain doit être arrêté avant toute bascule
(`launchctl bootout gui/$(id -u)/com.klody.api`, cf. mémoire projet).

Usage :
    python scripts/etat_pollue.py                         # inventaire, n'écrit rien
    python scripts/etat_pollue.py --quarantaine DOSSIER   # déplace + manifeste

Codes de sortie :
    0 = inventaire fait (ou quarantaine faite)
    1 = refus : l'API écoute, ou la quarantaine est dans le dossier inspecté
    2 = rien n'a pu être jugé (dossier absent, aucun littéral de test trouvé)
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import socket
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Préambule du banc (`bench/run.py::_run_klody`). `klody-bench-` : préfixe du
# tmpdir avant le passage à /tmp/kb- (2026-07-29).
_PREAMBULE_BANC = re.compile(
    r"^\[Répertoire de travail : [^\]]*/(?:kb|klody-bench)-[^\]/]+\]\n"
    r"Les fichiers sont dans ce répertoire\."
)
# Textes que l'ORCHESTRATEUR écrit lui-même dans le fil (agent/orchestrator.py) :
# ils apparaissent dans les sessions de test sans être des chaînes des tests.
_MESSAGES_SYSTEME = (
    "[Système] Tool fallback exécuté",
    "J'ai épuisé mon budget d'outils",
)
DUREE_MAX_S = 60.0


def _compacte(texte: str) -> str:
    return re.sub(r"\s+", "", texte)


def litteraux_des_tests(racine: Path) -> frozenset[str]:
    """Toutes les chaînes écrites telles quelles dans `tests/` (code et JSON)."""
    trouves: set[str] = set()

    def _json(obj: object) -> None:
        if isinstance(obj, str):
            trouves.add(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                _json(v)
        elif isinstance(obj, list):
            for v in obj:
                _json(v)

    for f in racine.rglob("*.py"):
        try:
            arbre = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        trouves.update(
            n.value for n in ast.walk(arbre)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
    for f in racine.rglob("*.json"):
        try:
            _json(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
    return frozenset(s for s in trouves if s.strip())


def _duree_s(session: dict) -> float | None:
    try:
        debut = datetime.fromisoformat(session["created_at"])
        fin = datetime.fromisoformat(session["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return (fin - debut).total_seconds()


def classer(
    session: dict, litteraux: frozenset[str], compacts: frozenset[str]
) -> str | None:
    """« banc », « test » ou None (session à GARDER)."""
    messages = [m for m in session.get("messages") or [] if isinstance(m, dict)]
    utilisateur = [m.get("content") for m in messages if m.get("role") == "user"]
    premier = utilisateur[0] if utilisateur else None
    if not isinstance(premier, str):
        return None
    if _PREAMBULE_BANC.match(premier):
        return "banc"
    if premier not in litteraux:
        return None
    assistant = [m for m in messages if m.get("role") == "assistant"]
    if not assistant:
        return None
    for m in assistant:
        texte = m.get("content")
        if not isinstance(texte, str) or not texte.strip():
            continue
        if _compacte(texte) in compacts or texte.startswith(_MESSAGES_SYSTEME):
            continue
        return None  # une vraie réponse de modèle : c'est une session réelle
    duree = _duree_s(session)
    if duree is None or duree >= DUREE_MAX_S:
        return None
    return "test"


def inventaire(dossier: Path, litteraux: frozenset[str]) -> dict[str, list[Path]]:
    compacts = frozenset(_compacte(s) for s in litteraux)
    classes: dict[str, list[Path]] = {"banc": [], "test": [], "garde": [], "illisible": []}
    for f in sorted(dossier.glob("memory_*.json")):
        try:
            session = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            classes["illisible"].append(f)
            continue
        if not isinstance(session, dict):
            classes["illisible"].append(f)
            continue
        classes[classer(session, litteraux, compacts) or "garde"].append(f)
    return classes


def premier_message(f: Path) -> str:
    """Première ligne du premier message — l'énoncé de la tâche pour le banc."""
    try:
        session = json.loads(f.read_text(encoding="utf-8"))
        for m in session.get("messages") or []:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                texte = m["content"]
                if _PREAMBULE_BANC.match(texte) and "\n\n" in texte:
                    texte = texte.split("\n\n", 1)[1]
                return texte.split("\n", 1)[0][:60]
    except (ValueError, OSError, AttributeError):
        pass
    return "?"


def api_ecoute(port: int = 8000) -> bool:
    """L'écrivain (com.klody.api) est-il vivant ? Une connexion suffit à le dire."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def mettre_en_quarantaine(classes: dict[str, list[Path]], cible: Path) -> Path:
    """Déplace banc + test sous `cible`, écrit `manifeste.tsv`. Ne supprime rien."""
    cible.mkdir(parents=True, exist_ok=True)
    manifeste = cible / "manifeste.tsv"
    with manifeste.open("a", encoding="utf-8") as m:
        for categorie in ("banc", "test"):
            (cible / categorie).mkdir(exist_ok=True)
            for f in classes[categorie]:
                destination = cible / categorie / f.name
                m.write(f"{categorie}\t{f}\t{destination}\t{premier_message(f)}\n")
                shutil.move(str(f), str(destination))
    return manifeste


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--dossier",
        type=Path,
        default=Path(os.environ.get("KLODY_DATA_DIR") or Path.home() / ".klody" / "data"),
    )
    p.add_argument("--quarantaine", type=Path, default=None, metavar="DOSSIER")
    p.add_argument("--tests", type=Path, default=REPO / "tests", help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    dossier = args.dossier.expanduser()
    if not dossier.is_dir():
        print(f"✗ {dossier} n'existe pas — rien à juger.")
        return 2
    litteraux = litteraux_des_tests(args.tests)
    if not litteraux:
        # Sans littéraux, la règle « test » ne reconnaîtrait rien : un « 0
        # session de test » serait un faux vert.
        print(f"✗ aucune chaîne trouvée sous {args.tests} — impossible de juger.")
        return 2

    classes = inventaire(dossier, litteraux)
    total = sum(len(v) for v in classes.values())
    print(f"{dossier} — {total} session(s)")
    for categorie, libelle in (
        ("banc", "banc (préambule de bench/run.py)"),
        ("test", "tests (messages ET réponses scriptés)"),
        ("garde", "gardées"),
        ("illisible", "illisibles (gardées)"),
    ):
        print(f"  {len(classes[categorie]):5}  {libelle}")
    for categorie in ("banc", "test"):
        if classes[categorie]:
            print(f"\n{categorie} — premiers messages les plus fréquents :")
            for texte, n in Counter(premier_message(f) for f in classes[categorie]).most_common(12):
                print(f"  {n:5}  {texte!r}")

    if args.quarantaine is None:
        print("\n(inventaire seul : rien n'a été déplacé)")
        return 0

    cible = args.quarantaine.expanduser().absolute()
    if cible == dossier.absolute() or dossier.absolute() in cible.parents:
        print(f"✗ la quarantaine {cible} est DANS {dossier} : les sessions y resteraient visibles.")
        return 1
    if api_ecoute():
        print(
            "✗ l'API écoute sur :8000 — arrêter l'écrivain AVANT de déplacer :\n"
            "    launchctl bootout gui/$(id -u)/com.klody.api\n"
            "  puis relancer ce script, puis :\n"
            "    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.klody.api.plist"
        )
        return 1
    manifeste = mettre_en_quarantaine(classes, cible)
    n = len(classes["banc"]) + len(classes["test"])
    print(f"\n→ {n} session(s) déplacée(s) sous {cible}\n→ manifeste : {manifeste}")
    print("  Pour tout remettre : lire le manifeste (colonnes 3 → 2) et `mv` en sens inverse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
