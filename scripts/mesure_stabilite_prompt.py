#!/usr/bin/env python3
"""Sur de VRAIS messages consécutifs, le prompt système reste-t-il identique ?

Le cache de préfixe de mlx_lm ne réutilise qu'un préfixe EXACT
(`scripts/mesure_cache_prefixe.py`) : un prompt système qui diffère d'un octet
d'un message au suivant fait recalculer tous les schémas d'outils. Ce script
rejoue les messages utilisateur des dernières sessions (`~/.klody/data`) dans le
profileur et le sélecteur de skills RÉELS, et compte les paires consécutives dont
le prompt système est identique octet pour octet.

Le système « après » est construit par le VRAI `Orchestrator._inject_system_prompt`
(orchestrateur partiel, mémoire jetable) : le script mesure ce que le code
envoie, pas une copie de sa logique qui pourrait diverger. Le système « avant »
(retrieval et skills dans le système) en est déduit : identique si et seulement
si le système après ET les skills le sont.

Rien n'est écrit : le profil est copié en dossier temporaire, `_save` neutralisé.
Mémoire long terme, conventions et erreurs sont lues une fois et figées — elles
changent rarement dans une session, mais ce chiffre en est un PLAFOND. Le
retrieval n'est pas rejoué (il exige les embeddings) : l'« avant » est donc lui
aussi un plafond, la stabilité réelle d'avant était plus basse encore.

`task_type` choisit le prompt de tâche (explain.md, review.md…) qui reste dans le
système. Par défaut il est FIXE (`explain`, le seul type non-code du brain en
routage auto le plus courant) ; `--routeur` rejoue le vrai routeur via le
gateway (un appel `brain` par message) et ne compare alors que les messages
servis par le brain, le coder ayant son propre cache et un prompt slim fixe.

Les sessions de la suite de tests et du banc sont ÉCARTÉES, par le classifieur
de `scripts/etat_pollue.py` : le 2026-09-27, `~/.klody/data` en était aux trois
quarts (CLAUDE.md), et des paires de messages scriptés — identiques d'une
session à l'autre — gonflaient les deux taux. Le nombre écarté est affiché.

Mesuré le 2026-09-27, 218 paires : profil identique 0 % avant la suppression des
compteurs (« Requêtes : N », « (N×) »), 84 % après ; profil + skills 9 %.
Mesuré le 2026-09-28 sur le dossier assaini (251 sessions réelles, 386 paires),
retrieval et skills how-to sortis du système : système identique 22 % → 95 %.

Usage : python scripts/mesure_stabilite_prompt.py [--sessions 400] [--routeur]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.profiler as prof
import config
from tools.skills import format_skills_for_prompt, load_skills, select_skills

from etat_pollue import REPO, _compacte, classer, litteraux_des_tests


def _orchestrateur(profileur, dossier_tmp: Path):
    """Orchestrateur partiel : seul `_inject_system_prompt` tourne, sur les
    vraies sources des sections stables (lues une fois, figées)."""
    import agent.orchestrator as orch_mod
    from agent.long_term_memory import get_long_term_memory
    from agent.memory import ConversationMemory

    config.MEMORY_DIR = dossier_tmp
    o = MagicMock()
    o.memory = ConversationMemory(session_id="mesure")
    o.memory.save = lambda: None  # type: ignore[method-assign]
    o._code_model_active = False
    o._on_skills_selected = None
    o._relevant_files_section.return_value = ""
    o.profiler = profileur
    o.lt_memory = get_long_term_memory()
    try:
        from agent.conventions import ConventionDetector
        conventions = ConventionDetector(config.PROJECT_ROOT).detect().format_for_prompt()
    except Exception:
        conventions = ""
    o.conventions.detect.return_value.format_for_prompt.return_value = conventions
    try:
        from agent.error_memory import ErrorMemory
        erreurs = ErrorMemory(workdir=config.PROJECT_ROOT).format_for_prompt()
    except Exception:
        erreurs = ""
    o.error_memory.format_for_prompt.return_value = erreurs
    return o, orch_mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=400)
    ap.add_argument("--routeur", action="store_true",
                    help="rejoue le vrai routeur (gateway) pour le task_type")
    args = ap.parse_args()

    dossier = Path(config.MEMORY_DIR)  # le vrai, avant la redirection ci-dessous
    litteraux = litteraux_des_tests(REPO / "tests")
    compacts = frozenset(_compacte(t) for t in litteraux)
    tmp = Path(tempfile.mkdtemp())
    if prof._PROFILE_FILE.exists():
        shutil.copy(prof._PROFILE_FILE, tmp / "user_profile.json")
    prof._PROFILE_FILE = tmp / "user_profile.json"
    prof.UserProfiler._save = lambda self: None  # type: ignore[method-assign]
    profileur = prof.UserProfiler()
    skills = load_skills()
    o, orch_mod = _orchestrateur(profileur, tmp)
    routeur = None
    if args.routeur:
        from agent.orchestrateur.outils import _is_continuation
        from agent.router import Router
        routeur = Router()

    fichiers = sorted(dossier.glob("memory_*.json"), key=lambda f: f.stat().st_mtime)
    paires = avant = apres = profil_ok = ecartees = 0
    types: dict[str, int] = {}
    for f in fichiers[-args.sessions:]:
        try:
            session = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(session, dict) or classer(session, litteraux, compacts):
            ecartees += 1
            continue
        msgs = session.get("messages", [])
        prec: tuple[str, str, str] | None = None
        decision = None
        for m in msgs:
            if m.get("role") != "user" or not isinstance(m.get("content"), str):
                continue
            texte = m["content"]
            profileur.track_request(texte)
            task_type = "explain"
            if routeur is not None:
                if decision is None or not _is_continuation(texte):
                    decision = routeur.classify(texte)
                task_type = decision.task_type
                types[task_type] = types.get(task_type, 0) + 1
                if task_type in orch_mod._CODE_TASK_TYPES:
                    continue  # servi par le coder : autre cache, prompt slim fixe
            o.memory.messages = [{"role": "user", "content": texte}]
            orch_mod.Orchestrator._inject_system_prompt(o, task_type=task_type, query=texte)
            systeme = o.memory.messages[0]["content"]
            choisis = select_skills(skills, texte)
            skills_txt = format_skills_for_prompt(choisis) if choisis else ""
            profil = profileur.get_profile_for_prompt()
            if prec is not None:
                paires += 1
                apres += systeme == prec[0]
                avant += (systeme, skills_txt) == prec[:2]
                profil_ok += profil == prec[2]
            prec = (systeme, skills_txt, profil)

    if not paires:
        print("aucune paire de messages consécutifs trouvée", file=sys.stderr)
        return 1
    mode = "routeur rejoué" if args.routeur else "task_type=explain fixe"
    print(f"sessions écartées (tests, banc, illisibles) : {ecartees}")
    print(f"paires={paires} ({mode})  profil identique={profil_ok / paires:.0%}  "
          f"système AVANT (+skills) identique={avant / paires:.0%}  "
          f"système APRÈS identique={apres / paires:.0%}")
    if types:
        print("task_type rejoués : " + ", ".join(
            f"{t}={n}" for t, n in sorted(types.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
