#!/usr/bin/env python3
"""Deux messages consécutifs d'une même session : le 2ᵉ réutilise-t-il le préfixe ?

Rejoue le chemin de l'API (un Orchestrator par message, `stream_chat` remplacé
par `api.streaming.make_stream_api`) sur le VRAI gateway, et relève la ligne
`[cache]` de chaque appel LLM. Les deux questions sont choisies pour sélectionner
des skills how-to DIFFÉRENTES : c'est le cas qui, tant que les skills vivaient
dans le prompt système, faisait refaire le prefill des schémas d'outils depuis le
token 0 au premier appel de chaque message (`scripts/mesure_cache_prefixe.py`).

Lecture : `cached` du 1ᵉʳ appel du message 2. Le juge est ce compte, rendu par
mlx_lm ; la durée n'est que sa conséquence, et ne se compare qu'à l'intérieur
d'un même run (CLAUDE.md). Le script dit aussi si le système a changé entre les
deux messages (profil, mémoire…) : un `cached=0` avec un système différent ne
dit rien du contexte de tour.

Aucun état utilisateur n'est écrit : sessions en dossier temporaire, profil non
sauvegardé, extraction mémoire mi-session coupée.

Codes de sortie : 0 = mesure faite, 1 = mesure impossible (aucune ligne
`[cache]` : gateway injoignable, ou `usage` absent).

Mesuré le 2026-09-27 (brain, 92 k tokens de prompt dont les schémas d'outils
des serveurs MCP) — même run, même session :

    main (skills dans le système)   msg 1 cached=0       235,0 s
                                    msg 2 cached=0       154,9 s   système DIFFÉRENT
    contexte dans le tour           msg 1 cached=0       249,7 s
                                    msg 2 cached=85 975  32,7 s    système identique

Rejoué le 2026-09-28 sur main 83d8c8a (rebase), même protocole :

    main                            msg 1 cached=0       219,0 s
                                    msg 2 cached=0       134,3 s   système DIFFÉRENT
    contexte dans le tour           msg 1 cached=0       213,3 s
                                    msg 2 cached=85 990  35,9 s    système identique

Usage : python scripts/mesure_cache_deux_messages.py
"""
from __future__ import annotations

import hashlib
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

QUESTIONS = [
    ("Sans utiliser d'outil, explique en trois phrases ce qu'est une progression "
     "d'accords II-V-I en jazz."),
    ("Sans utiliser d'outil, explique en trois phrases comment fine-tuner un petit "
     "modèle avec LoRA sur Mac."),
]


class _Releve(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lignes: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        texte = record.getMessage()
        if texte.startswith("[cache]"):
            self.lignes.append(texte)


def main() -> int:
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    import agent.profiler as prof
    from agent.memory import ConversationMemory
    from agent.orchestrator import Orchestrator
    from api.streaming import make_stream_api

    prof.UserProfiler._save = lambda self: None  # type: ignore[method-assign]
    Orchestrator._mid_session_extract = lambda self: None  # type: ignore[method-assign]

    releve = _Releve()
    # Les deux emplacements du journal : avant (#270, api.streaming) et après
    # (agent.cache_prefixe) — le même script mesure les deux versions.
    for nom in ("api.streaming", "agent.cache_prefixe"):
        journal = logging.getLogger(nom)
        journal.setLevel(logging.INFO)
        journal.addHandler(releve)

    memoire = ConversationMemory()
    systemes: list[str] = []
    premiers: list[str] = []
    for i, question in enumerate(QUESTIONS, 1):
        orch = Orchestrator(memoire)  # comme l'API : un orchestrateur par message
        orch.llm.stream_chat = make_stream_api(orch, lambda _e: None, None)
        debut = len(releve.lignes)
        try:
            orch.run(question)
        finally:
            orch.close()
        systeme = memoire.messages[0]["content"]
        systemes.append(hashlib.sha256(systeme.encode()).hexdigest()[:12])
        lignes = releve.lignes[debut:]
        howto = [s for s in getattr(orch, "_injected_skill_slugs", [])
                 if not s.startswith(("utilisateur_", "conventions_"))]
        print(f"message {i} — système {systemes[-1]} ({len(systeme)} car.), "
              f"skills how-to : {howto}")
        for ligne in lignes:
            print(f"   {ligne}")
        if lignes:
            premiers.append(lignes[0])

    if len(premiers) < len(QUESTIONS):
        print("mesure impossible : un message n'a produit aucune ligne [cache]",
              file=sys.stderr)
        return 1
    identique = "identique" if systemes[0] == systemes[1] else "DIFFÉRENT"
    print(f"système entre les deux messages : {identique}")
    print(f"1ᵉʳ appel du message 2 : {premiers[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
