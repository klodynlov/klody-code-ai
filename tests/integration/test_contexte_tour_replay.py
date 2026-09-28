"""Rejeu : deux messages d'une même session, préfixe observé appel par appel.

Ce que le cache de préfixe de mlx_lm exige (préfixe EXACT, cf.
tests/test_contexte_tour.py) vu depuis le LLM lui-même, sur le vrai run() :
- le système est identique d'un appel à l'autre, d'un message à l'autre, alors
  que les skills injectées changent avec la requête ;
- dans un même message, chaque appel prolonge le précédent ;
- au message suivant, l'ancien tour repart sans son contexte.
"""
from __future__ import annotations

import copy
from pathlib import Path

_SKILLS = [
    {"name": "Mixage et mastering", "slug": "mixage_mastering",
     "description": "comment mixer et masteriser un morceau", "content": "compresse le bus"},
    {"name": "Gammes et modes", "slug": "gammes_modes",
     "description": "choisir une gamme pour une mélodie", "content": "la mineur naturel"},
]

_Q1 = "résume notes.md : comment mixer et masteriser mon morceau ?"
_Q2 = "quelle gamme pour une mélodie triste ?"

_FIXTURE = {
    "name": "contexte_tour",
    "router_decision": {"difficulty": "easy", "task_type": "explain", "reasoning": "x"},
    "llm_responses": [
        {"content": "", "tool_calls": [{
            "id": "call_001", "type": "function",
            "function": {"name": "read_file", "arguments": "{\"path\": \"notes.md\"}"},
        }]},
        {"content": "Compresse le bus, puis limite à -1 dBTP.", "tool_calls": None},
        {"content": "La mineur naturel, tempo lent.", "tool_calls": None},
    ],
}


def test_prefixe_stable_sur_deux_messages(fake_orchestrator, project_root: Path, monkeypatch):
    from agent import orchestrator as orch_mod

    (project_root / "notes.md").write_text("bus : 2 dB de compression\n", encoding="utf-8")
    orch, llm = fake_orchestrator(_FIXTURE)
    monkeypatch.setattr(orch_mod, "load_skills", lambda: _SKILLS)
    monkeypatch.setattr(orch_mod, "SKILLS_ROUTER_ENABLED", False)

    appels: list[list[dict]] = []
    rejouer = llm.stream_chat

    def capturer(messages, *a, **kw):
        appels.append(copy.deepcopy(messages))
        return rejouer(messages, *a, **kw)

    llm.stream_chat = capturer

    orch.run(_Q1)
    n1 = len(appels)
    orch.run(_Q2)
    assert n1 == 2 and len(appels) == 3, [len(a) for a in appels]

    systemes = {a[0]["content"] for a in appels}
    assert len(systemes) == 1, "le système a changé entre deux appels"
    systeme = systemes.pop()
    assert "Mixage" not in systeme and "Gammes" not in systeme

    # Message 1 : l'appel 2 (après read_file) prolonge exactement l'appel 1.
    assert appels[1][:len(appels[0])] == appels[0]
    tour_1 = appels[0][-1]["content"]
    assert tour_1.startswith(_Q1) and "Mixage et mastering" in tour_1

    # Message 2 : le tour 1 repart NU, seul le tour 2 porte son contexte.
    users = [m["content"] for m in appels[2] if m["role"] == "user"]
    assert users[0] == _Q1
    assert users[-1].startswith(_Q2) and "Gammes et modes" in users[-1]
    assert "Mixage et mastering" not in users[-1]
