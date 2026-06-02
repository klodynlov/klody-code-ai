"""Intégration : la boucle de feedback preview LIVRE, bout-en-bout.

Ces tests prouvent le chemin de succès que la démo live ne pouvait pas montrer
(le modèle réel n'arrivait pas à réparer le bug, donc on ne voyait que la boucle
*tenter*) :

  preview qui plante au runtime → erreur captée → nudge correctif injecté →
  le modèle régénère → preview propre → la boucle conclut.

Et le garde-fou : si le modèle ne répare jamais, la boucle plafonne à
`_MAX_PREVIEW_FIX` puis s'arrête — pas de corrections futiles à l'infini.

Le navigateur est simulé : le stub `pv_preview_code` sème `preview_errors`
selon le numéro d'appel (1er rendu = plante, 2e = charge proprement), exactement
ce que ferait le beacon de l'overlay (`navigator.sendBeacon` → /api/preview_error).
On exerce le chemin tool_call natif de `run()` (le hook ligne ~1388).
"""
from __future__ import annotations

import json

import pytest
from agent import orchestrator as orch_mod, preview_errors

_URL = "http://127.0.0.1:8899/arbres.html"


def _tc(html: str, title: str = "Arbres") -> dict:
    """Un tool_call natif preview_code (arguments = chaîne JSON, comme le vrai LLM)."""
    return {
        "id": "call_preview",
        "type": "function",
        "function": {
            "name": "preview_code",
            "arguments": json.dumps({"html": html, "title": title}),
        },
    }


def _preview_nudges(orch) -> list[str]:
    """Nudges correctifs injectés par la boucle (messages user, marqueur dédié)."""
    return [
        m["content"]
        for m in orch.memory.messages
        if m.get("role") == "user"
        and isinstance(m.get("content"), str)
        and "erreur(s) JS À L'EXÉCUTION" in m["content"]
    ]


def _final_assistant(orch) -> str:
    for m in reversed(orch.memory.messages):
        if m.get("role") == "assistant" and m.get("content"):
            return m["content"]
    return ""


@pytest.fixture(autouse=True)
def _clean_store():
    """Le tampon d'erreurs est un état module global — on l'isole par test."""
    preview_errors.clear()
    yield
    preview_errors.clear()


def test_preview_feedback_loop_converges(fake_orchestrator, monkeypatch):
    """Preview cassée → 1 correction → preview propre → la boucle s'arrête.

    Preuve du chemin de succès : avec un « modèle » capable de corriger (ici
    scripté), la boucle transforme une preview qui *jette* en une preview propre
    et conclut, SANS atteindre le plafond.
    """
    monkeypatch.setattr(orch_mod, "PREVIEW_FEEDBACK_TIMEOUT_S", 2.0)

    calls = {"n": 0}
    received_html: list[str] = []

    def stub_preview(html, css="", js="", title="Preview", scripts=None, styles=None):
        calls["n"] += 1
        received_html.append(html)
        preview_errors.clear(_URL)
        if calls["n"] == 1:
            # 1er rendu : le navigateur détecte une erreur runtime (le beacon).
            preview_errors.record(_URL, [{
                "label": "Error",
                "msg": "Cannot read properties of undefined (reading 'toString')",
                "src": f"{_URL}:604:37",
            }])
        else:
            # 2e rendu (code corrigé) : chargement propre (ping « ok »).
            preview_errors.mark_loaded(_URL)
        return f"✅ Preview prête\nURL : {_URL}"

    monkeypatch.setattr(orch_mod, "pv_preview_code", stub_preview)

    fixture = {
        "name": "preview_feedback_converge",
        "user_prompt": "Construis un visualiseur d'arbres équilibrés (AVL, rouge-noir, B-arbre).",
        "router_decision": {"difficulty": "medium", "task_type": "feature"},
        "llm_responses": [
            {"content": "", "tool_calls": [_tc("<canvas></canvas><script>/* drawNode binaire buggé */</script>")]},
            {"content": "", "tool_calls": [_tc("<canvas></canvas><script>/* renderer corrigé */</script>")]},
            {"content": "Le visualiseur est prêt : les trois arbres se rendent sans erreur."},
        ],
    }

    orch, fake_llm = fake_orchestrator(fixture)
    events: list[dict] = []
    orch._emit = events.append

    orch.run(fixture["user_prompt"])

    # La boucle a fait EXACTEMENT une passe de correction, puis a convergé.
    assert orch._preview_fix_attempts == 1
    nudges = _preview_nudges(orch)
    assert len(nudges) == 1
    assert "tentative 1/2" in nudges[0]
    assert "toString" in nudges[0]  # l'erreur runtime réelle est transmise au modèle

    # Le modèle a régénéré EN RÉPONSE au nudge (2e preview = code corrigé), puis
    # a conclu (3e réponse, sans tool) → la boucle s'est arrêtée d'elle-même.
    assert calls["n"] == 2
    assert "corrigé" in received_html[1]
    assert fake_llm.consumed == 3
    assert "sans erreur" in _final_assistant(orch)

    # L'événement UI (PreviewFeedbackChip) a été émis pour la passe de correction.
    feedback = [e for e in events if e.get("type") == "preview_feedback"]
    assert len(feedback) == 1
    assert feedback[0]["attempt"] == 1
    assert feedback[0]["max"] == 2
    assert feedback[0]["count"] == 1


def test_preview_feedback_loop_caps_when_model_never_fixes(fake_orchestrator, monkeypatch):
    """Le modèle ne répare jamais → la boucle plafonne à _MAX_PREVIEW_FIX, puis stop.

    Garde-fou anti-emballement : comportement observé en démo live (le coder
    régénérait la même classe d'erreur) — 2 nudges puis arrêt, pas une boucle
    infinie de corrections futiles.
    """
    monkeypatch.setattr(orch_mod, "PREVIEW_FEEDBACK_TIMEOUT_S", 2.0)

    calls = {"n": 0}

    def stub_preview(html, css="", js="", title="Preview", scripts=None, styles=None):
        calls["n"] += 1
        preview_errors.clear(_URL)
        preview_errors.record(_URL, [{
            "label": "Error",
            "msg": "toujours la même erreur runtime",
            "src": f"{_URL}:1:1",
        }])
        return f"URL : {_URL}"

    monkeypatch.setattr(orch_mod, "pv_preview_code", stub_preview)

    fixture = {
        "name": "preview_feedback_cap",
        "user_prompt": "Construis un visualiseur d'arbres équilibrés.",
        "router_decision": {"difficulty": "medium", "task_type": "feature"},
        "llm_responses": [
            {"content": "", "tool_calls": [_tc("<script>/* bug v1 */</script>")]},
            {"content": "", "tool_calls": [_tc("<script>/* bug v2 */</script>")]},
            {"content": "", "tool_calls": [_tc("<script>/* bug v3 */</script>")]},
            {"content": "Je n'arrive pas à corriger cette erreur runtime."},
        ],
    }

    orch, fake_llm = fake_orchestrator(fixture)
    orch.run(fixture["user_prompt"])

    # Plafonné : 2 corrections max — pas 3 — malgré 3 previews qui plantent.
    assert orch._preview_fix_attempts == 2
    assert len(_preview_nudges(orch)) == 2
    # La 3e preview plante AUSSI mais n'a PAS déclenché de nudge (plafond atteint).
    assert calls["n"] == 3
    assert fake_llm.consumed == 4
