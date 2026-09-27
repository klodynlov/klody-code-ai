#!/usr/bin/env python3
"""Sur de VRAIS messages consécutifs, le prompt système reste-t-il identique ?

Le cache de préfixe de mlx_lm ne réutilise qu'un préfixe EXACT
(`scripts/mesure_cache_prefixe.py`) : un prompt système qui diffère d'un octet
d'un message au suivant fait recalculer tous les schémas d'outils. Ce script
rejoue les messages utilisateur des dernières sessions (`~/.klody/data`) dans le
profileur et le sélecteur de skills RÉELS, et compte les paires consécutives dont
la partie variable (profil, profil + skills) est identique octet pour octet.

Rien n'est écrit : le profil est copié en dossier temporaire, `_save` neutralisé.
Le retrieval proactif n'est pas rejoué (il exige les embeddings) — le chiffre
« profil + skills » est donc un PLAFOND de la stabilité réelle.

Mesuré le 2026-09-27, 218 paires : profil identique 0 % avant la suppression des
compteurs (« Requêtes : N », « (N×) »), 84 % après ; profil + skills 9 %.

Usage : python scripts/mesure_stabilite_prompt.py [--sessions 400]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.profiler as prof
from tools.skills import format_skills_for_prompt, load_skills, select_skills


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=400)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp())
    if prof._PROFILE_FILE.exists():
        shutil.copy(prof._PROFILE_FILE, tmp / "user_profile.json")
    prof._PROFILE_FILE = tmp / "user_profile.json"
    prof.UserProfiler._save = lambda self: None  # type: ignore[method-assign]
    profileur = prof.UserProfiler()
    skills = load_skills()

    dossier = Path.home() / ".klody" / "data"
    fichiers = sorted(dossier.glob("memory_*.json"), key=lambda f: f.stat().st_mtime)
    paires = identiques = identiques_profil = 0
    for f in fichiers[-args.sessions:]:
        try:
            msgs = json.loads(f.read_text(encoding="utf-8")).get("messages", [])
        except (OSError, json.JSONDecodeError):
            continue
        prec: str | None = None
        prec_profil: str | None = None
        for m in msgs:
            if m.get("role") != "user" or not isinstance(m.get("content"), str):
                continue
            profileur.track_request(m["content"])
            profil = profileur.get_profile_for_prompt()
            choisis = select_skills(skills, m["content"])
            variable = (format_skills_for_prompt(choisis) if choisis else "") + profil
            if prec is not None:
                paires += 1
                identiques += variable == prec
                identiques_profil += profil == prec_profil
            prec, prec_profil = variable, profil

    if not paires:
        print("aucune paire de messages consécutifs trouvée", file=sys.stderr)
        return 1
    print(f"paires={paires}  profil identique={identiques_profil / paires:.0%}  "
          f"profil+skills identiques={identiques / paires:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
