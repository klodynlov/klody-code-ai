#!/usr/bin/env python3
"""La section « Erreurs récurrentes » change-t-elle le prompt système entre deux messages ?

Le cache de préfixe de mlx_lm ne réutilise qu'un préfixe EXACT (#270,
`scripts/mesure_cache_prefixe.py`) : un octet changé dans le système refait le
prefill de tous les schémas d'outils. `ErrorMemory.format_for_prompt` est
injecté dans le système à chaque message (`Orchestrator._inject_system_prompt`),
et il rendait `(N× vus récemment)` : chaque récidive d'une erreur déjà
récurrente changeait donc le système — le motif de compteur que #270 a retiré
du profil.

Rejeu, sans rien écrire ni appeler : les sessions RÉELLES de `~/.klody/data`
(celles que `scripts/etat_pollue.py` garde), et les `errors.json` RÉELS. Pour
chaque vrai message utilisateur (`timestamp` non nul — les relances de
l'orchestrateur sont posées avec `timestamp: None`), la section est recalculée
par le code du dépôt à l'instant du message : entrées enregistrées AVANT lui,
fenêtre de 24 h comptée depuis lui. Deux messages consécutifs d'une même
session forment une paire ; on compte celles dont la section diffère, et on
classe la cause :

- `compteur` : mêmes signatures, même ordre — seul un `N×` a bougé ;
- `ordre` : mêmes signatures, ordre différent ;
- `ensemble` : une signature est apparue ou sortie (seuil franchi, fenêtre).

L'API lit `<PROJECT_ROOT>/.klody/errors.json`, et `PROJECT_ROOT` a changé
(`~/Projets` en dur, puis le cwd du service — cf. `.env`). Chaque source est
donc rejouée séparément sur TOUTES les paires : un chiffre par source, pas de
fusion qui inventerait un fichier que personne n'a lu.

Le JSON produit (`--json`) ne porte que des comptes, jamais une signature.

Mesuré le 2026-09-27, 116 sessions, 305 paires (2026-06-09 → 2026-09-22) :
section changée par le COMPTEUR sur **0/305** paires, quelle que soit la source ;
1/305 par une APPARITION (seuil franchi), que retirer le compteur ne corrigerait
pas. Les deux `errors.json` n'ont reçu que 23 échecs en quatre mois, dont 2
seulement étaient des récidives d'une erreur déjà récurrente. Témoin
synthétique (une récidive posée entre deux vrais messages) : `compteur: 1` —
l'instrument sait rougir. Relevé :
`bench/results/reference_2026-09-27_stabilite_erreurs.md`.

Codes de sortie : 0 = mesure faite ; 1 = rien à juger (aucune paire, ou aucune
source lisible).

Usage : python scripts/mesure_stabilite_erreurs.py [--source F ...] [--json F]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from unittest import mock

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

from agent import error_memory as em  # noqa: E402

import etat_pollue  # noqa: E402

SOURCES = (
    Path.home() / "Projets" / ".klody" / "errors.json",
    Path.home() / "Projets" / "klody-code-ai" / ".klody" / "errors.json",
)


def _libelle(source: Path) -> str:
    """Chemin sans le dossier personnel : le JSON versionné ne porte pas la machine."""
    try:
        return "~/" + str(source.expanduser().resolve().relative_to(Path.home()))
    except ValueError:
        return str(source)


def _instants(session: dict) -> list[float]:
    """Instants (epoch) des VRAIS messages utilisateur, dans l'ordre."""
    res: list[float] = []
    for m in session.get("messages") or []:
        if not (isinstance(m, dict) and m.get("role") == "user"):
            continue
        ts = m.get("timestamp")
        if not isinstance(ts, str):
            continue
        try:
            res.append(datetime.fromisoformat(ts).timestamp())
        except ValueError:
            continue
    return res


def section_a(entrees: list[em.ErrorEntry], instant: float) -> str:
    """Ce que `format_for_prompt()` rendait à `instant` (code réel du dépôt)."""
    memoire = em.ErrorMemory.__new__(em.ErrorMemory)
    memoire.entries = [e for e in entrees if e.timestamp < instant]
    with mock.patch.object(em.time, "time", return_value=instant):
        return memoire.format_for_prompt()


def _signatures(entrees: list[em.ErrorEntry], instant: float) -> list[str]:
    memoire = em.ErrorMemory.__new__(em.ErrorMemory)
    memoire.entries = [e for e in entrees if e.timestamp < instant]
    with mock.patch.object(em.time, "time", return_value=instant):
        return [sig for sig, _ in memoire.recurrent()[:5]]


def cause(entrees: list[em.ErrorEntry], t1: float, t2: float) -> str:
    a, b = _signatures(entrees, t1), _signatures(entrees, t2)
    if a == b:
        return "compteur"
    if sorted(a) == sorted(b):
        return "ordre"
    return "ensemble"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dossier", type=Path, default=Path.home() / ".klody" / "data")
    ap.add_argument("--source", type=Path, action="append", default=None,
                    help="errors.json à rejouer (répétable)")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    litteraux = etat_pollue.litteraux_des_tests(RACINE / "tests")
    gardees = etat_pollue.inventaire(args.dossier.expanduser(), litteraux)["garde"]
    sessions: list[list[float]] = []
    for f in gardees:
        try:
            instants = _instants(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if len(instants) >= 2:
            sessions.append(instants)
    paires = [(a, b) for s in sessions for a, b in pairwise(s)]
    if not paires:
        print("✗ aucune paire de vrais messages consécutifs", file=sys.stderr)
        return 1
    debut = datetime.fromtimestamp(min(s[0] for s in sessions)).date()
    fin = datetime.fromtimestamp(max(s[-1] for s in sessions)).date()
    print(f"{len(gardees)} sessions gardées, {len(sessions)} à ≥ 2 vrais messages, "
          f"{len(paires)} paires ({debut} → {fin})")

    rapport: dict = {"sessions": len(sessions), "paires": len(paires),
                     "periode": [str(debut), str(fin)], "sources": {}}
    lues = 0
    for source in args.source or SOURCES:
        try:
            brut = json.loads(source.expanduser().read_text(encoding="utf-8"))
            entrees = [em.ErrorEntry(**d) for d in brut]
        except (OSError, ValueError, TypeError) as exc:
            print(f"  {source} : illisible ({type(exc).__name__}) — non jugé")
            continue
        lues += 1
        non_vides = changes = 0
        causes = {"compteur": 0, "ordre": 0, "ensemble": 0}
        for t1, t2 in paires:
            s1, s2 = section_a(entrees, t1), section_a(entrees, t2)
            non_vides += bool(s1 or s2)
            if s1 != s2:
                changes += 1
                causes[cause(entrees, t1, t2)] += 1
        # Contrefactuel indépendant des sessions : à chaque NOUVELLE entrée, la
        # section juste après diffère-t-elle de celle juste avant ? C'est ce que
        # verrait un message posé entre deux échecs sandbox consécutifs.
        transitions = {"compteur": 0, "ordre": 0, "ensemble": 0, "inchangee": 0}
        for e in sorted(entrees, key=lambda x: x.timestamp):
            avant, apres = section_a(entrees, e.timestamp), section_a(entrees, e.timestamp + 1e-3)
            transitions["inchangee" if avant == apres
                        else cause(entrees, e.timestamp, e.timestamp + 1e-3)] += 1
        dates = [e.timestamp for e in entrees]
        print(f"\n  {source} — {len(entrees)} entrée(s)"
              + (f", {datetime.fromtimestamp(min(dates)).date()} → "
                 f"{datetime.fromtimestamp(max(dates)).date()}" if dates else ""))
        print(f"    paires où la section est non vide : {non_vides}/{len(paires)}")
        print(f"    paires où elle CHANGE             : {changes}/{len(paires)}  {causes}")
        print(f"    à chaque échec enregistré         : {transitions}")
        rapport["sources"][_libelle(source)] = {
            "entrees": len(entrees), "paires_non_vides": non_vides,
            "paires_changees": changes, "causes": causes,
            "transitions_par_echec": transitions,
        }
    if not lues:
        print("✗ aucune source lisible", file=sys.stderr)
        return 1
    if args.json:
        args.json.write_text(json.dumps(rapport, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
