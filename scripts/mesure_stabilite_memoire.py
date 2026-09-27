#!/usr/bin/env python3
"""Une extraction de faits entre deux messages change-t-elle le prompt système ?

Le cache de préfixe de mlx_lm ne réutilise qu'un préfixe EXACT (#270,
`scripts/mesure_cache_prefixe.py`) : un octet changé dans le système refait le
prefill des schémas d'outils. L'extraction automatique (`api/server.py::
_extract_memory_bg`) écrit dans `LongTermMemory` après CHAQUE message WebSocket,
et `format_for_prompt()` est injecté dans le système. Ce script mesure à quelle
fréquence la section « mémoire longue terme » change d'un message au suivant.

Rejeu : les sessions RÉELLES de `~/.klody/data` (celles que
`scripts/etat_pollue.py` garde), dans l'ordre chronologique. Après chaque tour
utilisateur, la VRAIE extraction (`extract_and_save`, même prompt, même
troncature) tourne contre le gateway — la cible de la PR qui la répare :
`config.LLM_BASE_URL` / `config.LLM_MODEL`, en-têtes `X-Klody-App` et
`X-Klody-Source: system` (sans ce dernier, chaque appel serait compté comme un
tour UTILISATEUR par le journal d'usage). Le profil est rejoué comme dans
`scripts/mesure_stabilite_prompt.py`.

Trois bras, sur les mêmes paires de messages consécutifs d'une même session :

- `sans extraction` : la production jusqu'ici (extraction morte depuis le
  2026-07-18) — seule la section profil peut bouger ;
- `extraction` : extraction après chaque message, section relue à chaque message ;
- `extraction + figée` : même extraction, section rendue par
  `agent.long_term_memory.section_de_session` — le code réel du remède.

Rien n'est écrit dans l'état réel : `KLODY_DATA_DIR` est redirigé AVANT
`import config`, `long_term.json` et `user_profile.json` y sont COPIÉS, le miroir
sémantique est coupé. Les contenus des faits ne sortent jamais du processus :
le JSON produit (`--json`) ne porte que des comptes.

Codes de sortie : 0 = mesure faite ; 1 = mesure impossible (aucune paire, ou
aucun appel d'extraction n'a abouti — gateway injoignable) ; 2 = usage.

Mesuré le 2026-09-27, 45 sessions, 93 paires, 53 extractions réelles sur `brain`
(relevé : `bench/results/reference_2026-09-27_stabilite_memoire.md`) — système
(profil + mémoire) identique : 95 % sans extraction, 83 % avec, 95 % avec la
section figée. 11/53 extractions écrivent, et chacune change la section.

Usage : python scripts/mesure_stabilite_memoire.py [--sessions 87] [--json F]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent.parent
REEL = Path.home() / ".klody" / "data"


def _tours(session: dict) -> list[int]:
    """Index des VRAIS messages utilisateur. Les relances de l'orchestrateur
    (« Ton budget d'itérations… », « STOP — ne conclus pas… ») sont aussi en rôle
    `user`, mais posées avec `timestamp: None` — le seul critère qui les sépare."""
    return [
        i for i, m in enumerate(session.get("messages") or [])
        if isinstance(m, dict) and m.get("role") == "user"
        and isinstance(m.get("content"), str) and m.get("timestamp") is not None
    ]


class _Compteur:
    """Enveloppe du client OpenAI : chronomètre chaque appel d'extraction et
    compte les échecs (`extract_and_save` les avale en rendant `[]`, ce qui
    ferait lire « aucun fait » là où il n'y a eu aucun appel)."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.durees: list[float] = []
        self.prompt_tokens: list[int] = []
        self.echecs: list[str] = []

    @property
    def chat(self) -> _Compteur:
        return self

    @property
    def completions(self) -> _Compteur:
        return self

    def create(self, **kwargs: Any) -> Any:
        debut = time.perf_counter()
        try:
            reponse = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            self.echecs.append(type(exc).__name__)
            raise
        self.durees.append(time.perf_counter() - debut)
        usage = getattr(reponse, "usage", None)
        if isinstance(getattr(usage, "prompt_tokens", None), int):
            self.prompt_tokens.append(usage.prompt_tokens)
        return reponse


def _blocs_changes(avant: str, apres: str) -> set[str]:
    """Blocs de catégorie (« **Contexte général** : » …) qui diffèrent entre deux
    rendus de `format_for_prompt()` — dit QUEL mécanisme casse le système : une
    ligne ajoutée dans une petite catégorie, ou la fenêtre des 15 `context`."""
    def blocs(texte: str) -> dict[str, str]:
        res: dict[str, str] = {}
        courant = "(en-tête)"
        for ligne in texte.splitlines():
            if ligne.startswith("**") and ligne.endswith(" :"):
                courant = ligne.strip("* :")
            res[courant] = res.get(courant, "") + ligne + "\n"
        return res
    a, b = blocs(avant), blocs(apres)
    return {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}


def _mediane(valeurs: list[float]) -> float | None:
    if not valeurs:
        return None
    v = sorted(valeurs)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dossier", type=Path, default=REEL,
                    help="état réel à rejouer (lu, jamais écrit)")
    ap.add_argument("--sessions", type=int, default=0,
                    help="ne rejouer que les N dernières sessions éligibles (0 = toutes)")
    ap.add_argument("--json", type=Path, default=None, help="comptes agrégés en JSON")
    args = ap.parse_args(argv)

    source = args.dossier.expanduser()
    if not source.is_dir():
        print(f"✗ {source} introuvable", file=sys.stderr)
        return 2

    # --- Isolation AVANT tout import du dépôt ------------------------------ #
    # Le dossier temporaire porte une COPIE de l'état de l'utilisateur : il ne
    # doit pas survivre au script, y compris interrompu (SIGTERM ⇒ SystemExit,
    # sinon le `finally` ne s'exécute pas — vécu à la mise au point).
    tmp = Path(tempfile.mkdtemp(prefix="stabilite_memoire_"))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        return _rejouer(args, source, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _rejouer(args: argparse.Namespace, source: Path, tmp: Path) -> int:
    for nom in ("long_term.json", "user_profile.json"):
        if (source / nom).exists():
            shutil.copy(source / nom, tmp / nom)
    os.environ["KLODY_DATA_DIR"] = str(tmp)
    os.environ["SEMANTIC_MEMORY_ENABLED"] = "false"
    os.environ["KLODY_JOURNAL"] = "0"
    sys.path.insert(0, str(RACINE))
    sys.path.insert(0, str(RACINE / "scripts"))

    import agent.long_term_memory as ltm
    import agent.memory_extractor as extracteur
    import agent.profiler as prof
    import config
    from agent.journal_client import APP
    from openai import OpenAI

    import etat_pollue

    if tmp not in ltm._STORAGE.parents or tmp not in prof._PROFILE_FILE.parents:
        print(f"✗ refus : l'état écrit ne serait pas sous {tmp}", file=sys.stderr)
        return 1
    prof.UserProfiler._save = lambda self: None  # type: ignore[method-assign]

    client = _Compteur(OpenAI(
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        timeout=config.LLM_HTTP_TIMEOUT,
        max_retries=0,
        default_headers={"X-Klody-App": APP, "X-Klody-Source": "system"},
    ))
    extracteur._client_llm = lambda: client  # type: ignore[assignment]

    # --- Sessions réelles, chronologiques ---------------------------------- #
    litteraux = etat_pollue.litteraux_des_tests(RACINE / "tests")
    gardees = etat_pollue.inventaire(source, litteraux)["garde"]
    sessions: list[tuple[str, dict]] = []
    for f in gardees:
        s = json.loads(f.read_text(encoding="utf-8"))
        if len(_tours(s)) >= 2:
            sessions.append((str(s.get("created_at", "")), s))
    sessions.sort(key=lambda t: t[0])
    if args.sessions:
        sessions = sessions[-args.sessions:]

    lt = ltm.LongTermMemory()
    profileur = prof.UserProfiler()

    paires = 0
    paires_extraction = 0  # paires (k, k+1) entre lesquelles une extraction a tourné
    stable = {"sans extraction": 0, "extraction": 0, "extraction + figée": 0}
    lt_change = lt_change_figee = profil_change = 0
    faits = {"appels": 0, "avec_faits": 0, "nouvelles_cles": 0,
             "contenu_modifie": 0, "retouche_identique": 0}
    par_categorie: dict[str, int] = {}
    blocs_changes: dict[str, int] = {}  # bloc de catégorie → nb de paires où il a changé

    for _, session in sessions:
        messages = session["messages"]
        tours = _tours(session)
        session_figee = type("Session", (), {})()  # porte l'attribut du remède
        prec: tuple[str, str, str] | None = None  # (profil, lt, lt figée)
        extraction_faite = False  # une extraction a-t-elle tourné depuis `prec` ?
        for rang, i in enumerate(tours, 1):
            profileur.track_request(messages[i]["content"])
            profil = profileur.get_profile_for_prompt()
            courant = (profil, lt.format_for_prompt(), ltm.section_de_session(session_figee, lt))
            if prec is not None and courant[1] != prec[1]:
                for bloc in sorted(_blocs_changes(prec[1], courant[1])):
                    blocs_changes[bloc] = blocs_changes.get(bloc, 0) + 1
            if prec is not None:
                paires += 1
                paires_extraction += extraction_faite
                p_idem = courant[0] == prec[0]
                l_idem = courant[1] == prec[1]
                f_idem = courant[2] == prec[2]
                profil_change += not p_idem
                lt_change += not l_idem
                lt_change_figee += not f_idem
                stable["sans extraction"] += p_idem
                stable["extraction"] += p_idem and l_idem
                stable["extraction + figée"] += p_idem and f_idem
            prec = courant

            # Fin du tour `rang` : l'API lance l'extraction sur tout l'historique.
            # Pas après le DERNIER tour : elle ne toucherait que la session
            # suivante, jamais une paire intra-session — et le gateway en charge
            # rend ~15 s par appel (2026-09-27), soit 135 appels économisés.
            if rang == len(tours):
                break
            fin = tours[rang]
            # Le seuil `_MIN_USER_MESSAGES` compte AUSSI les relances de
            # l'orchestrateur (rôle `user`) : comme en production, une extraction
            # peut donc tourner dès le 1ᵉʳ tour. On compte les appels, pas les rangs.
            avant = {e["key"]: e["content"] for e in lt.entries}
            n_appels = len(client.durees) + len(client.echecs)
            sauves = extracteur.extract_and_save(messages[:fin], lt, model=config.LLM_MODEL)
            extraction_faite = len(client.durees) + len(client.echecs) > n_appels
            faits["appels"] += extraction_faite
            if sauves:
                faits["avec_faits"] += 1
            for fait in sauves:
                cle = str(fait["key"]).strip().lower().replace(" ", "_")
                par_categorie[fait["category"]] = par_categorie.get(fait["category"], 0) + 1
                if cle not in avant:
                    faits["nouvelles_cles"] += 1
                elif avant[cle] != str(fait["content"]).strip():
                    faits["contenu_modifie"] += 1
                else:
                    faits["retouche_identique"] += 1
        print(f"· session {session.get('session_id', '?')} : {len(tours)} tour(s), "
              f"{faits['appels']} appel(s) d'extraction cumulés", flush=True)

    if not paires:
        print("mesure impossible : aucune paire de messages consécutifs", file=sys.stderr)
        return 1
    if not client.durees:
        print(f"mesure impossible : aucun appel d'extraction n'a abouti "
              f"({len(client.echecs)} échec(s) : {sorted(set(client.echecs))})",
              file=sys.stderr)
        return 1

    def pc(n: int, d: int) -> str:
        return f"{n}/{d} ({n / d:.0%})" if d else "—"

    print()
    print(f"sessions={len(sessions)}  paires={paires}  dont après extraction={paires_extraction}")
    print(f"appels d'extraction aboutis={len(client.durees)}  échecs={len(client.echecs)}  "
          f"médiane={_mediane(client.durees):.2f}s  "
          f"prompt médian={_mediane([float(t) for t in client.prompt_tokens])} tokens")
    print(f"extractions rendant ≥1 fait : {pc(faits['avec_faits'], len(client.durees))}  "
          f"— clés neuves {faits['nouvelles_cles']}, contenu modifié "
          f"{faits['contenu_modifie']}, retouche identique {faits['retouche_identique']}  "
          f"— catégories {par_categorie}")
    print(f"section mémoire changée entre deux messages : {pc(lt_change, paires)} "
          f"(figée : {pc(lt_change_figee, paires)})")
    print(f"  blocs en cause (paires) : {blocs_changes}")
    print(f"section profil changée : {pc(profil_change, paires)}")
    for bras, n in stable.items():
        print(f"système (profil + mémoire) identique — {bras:<20} {pc(n, paires)}")

    if args.json:
        args.json.write_text(json.dumps({
            "sessions": len(sessions),
            "paires": paires,
            "paires_apres_extraction": paires_extraction,
            "appels_aboutis": len(client.durees),
            "echecs": len(client.echecs),
            "duree_mediane_s": _mediane(client.durees),
            "prompt_tokens_median": _mediane([float(t) for t in client.prompt_tokens]),
            "modele": config.LLM_MODEL,
            "faits": faits,
            "faits_par_categorie": par_categorie,
            "section_memoire_changee": lt_change,
            "blocs_changes": blocs_changes,
            "section_memoire_figee_changee": lt_change_figee,
            "section_profil_changee": profil_change,
            "systeme_identique": stable,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
