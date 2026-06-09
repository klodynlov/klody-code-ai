"""Round-trip chat WebSocket complet — couvre api/server.py (chemin chat).

Comble la lacune notée dans .coveragerc : le endpoint /api/ws + son
`_build_streaming_orchestrator` n'étaient pas testés car ils exigent un client
OpenAI bas niveau. On injecte ici un faux client qui émet exactement la forme
de chunks consommée par `stream_api` (delta.content / delta.tool_calls / usage),
et on pilote un vrai échange via le TestClient FastAPI.

Deux scénarios :
- réponse texte simple → events thinking / token / stream_end / message_stats / done ;
- round-trip avec tool_call (list_skills, lecture seule) → tool_call / tool_result.
"""
from __future__ import annotations

from typing import ClassVar

import pytest

# --------------------------------------------------------------------------- #
# Faux client OpenAI : reproduit la forme des chunks de streaming attendue.     #
# --------------------------------------------------------------------------- #

class _Fn:
    def __init__(self, name: str = "", arguments: str = "") -> None:
        self.name = name
        self.arguments = arguments


class _ToolCallChunk:
    def __init__(self, index: int, id: str, name: str, arguments: str) -> None:
        self.index = index
        self.id = id
        self.function = _Fn(name, arguments)


class _Delta:
    def __init__(self, content=None, tool_calls=None, reasoning=None) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning = reasoning  # CoT (mode thinking) — capté par _delta_reasoning


class _Choice:
    def __init__(self, delta: _Delta) -> None:
        self.delta = delta


class _Usage:
    completion_tokens = 7
    prompt_tokens = 11
    total_tokens = 18


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _CompletionChoice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Completion:
    """Réponse non-streaming minimale (extracteur mémoire en tâche de fond)."""

    def __init__(self, content: str = "") -> None:
        self.choices = [_CompletionChoice(content)]


class _Chunk:
    def __init__(self, content=None, tool_calls=None, usage=None, reasoning=None) -> None:
        if content is None and tool_calls is None and reasoning is None:
            self.choices = []
        else:
            self.choices = [_Choice(_Delta(content, tool_calls, reasoning))]
        self.usage = usage


def _text_turn(text: str) -> list[_Chunk]:
    # Tokenise grossièrement + chunk final porteur de l'usage (include_usage).
    chunks = [_Chunk(content=tok) for tok in text.split(" ")]
    chunks.append(_Chunk(usage=_Usage()))
    return chunks


def _thinking_turn(cot: str, text: str) -> list[_Chunk]:
    """Tour façon brain Qwen3 : CoT (delta.reasoning) AVANT la réponse (content)."""
    chunks = [_Chunk(reasoning=part + " ") for part in cot.split(" ")]
    chunks += [_Chunk(content=tok + " ") for tok in text.split(" ")]
    chunks.append(_Chunk(usage=_Usage()))
    return chunks


def _tool_turn(call_id: str, name: str, arguments: str = "{}") -> list[_Chunk]:
    tc = _ToolCallChunk(index=0, id=call_id, name=name, arguments=arguments)
    return [_Chunk(tool_calls=[tc]), _Chunk(usage=_Usage())]


class _Completions:
    def __init__(self, turns: list[list[_Chunk]]) -> None:
        self._turns = turns
        self._i = 0

    def create(self, **params):
        if not params.get("stream"):
            # Appel non-streaming (routeur désactivé ici, mais l'extracteur
            # mémoire de fond peut appeler) → complétion benigne, non bloquante.
            return _Completion("")
        # Capture les messages envoyés au modèle → permet de vérifier qu'une
        # réponse ask_user a bien été réinjectée dans le contexte (tool result).
        FakeOpenAI.captured.append(params.get("messages") or [])
        turn = self._turns[min(self._i, len(self._turns) - 1)]
        self._i += 1
        return iter(turn)


class _Chat:
    def __init__(self, turns: list[list[_Chunk]]) -> None:
        self.completions = _Completions(turns)


class FakeOpenAI:
    """Remplace agent.llm.OpenAI : ignore base_url/api_key, joue un script."""

    _turns: ClassVar[list] = []
    captured: ClassVar[list] = []  # messages passés au modèle à chaque tour streamé

    def __init__(self, *args, **kwargs) -> None:
        FakeOpenAI.captured = []
        self.chat = _Chat(list(FakeOpenAI._turns))


def _tool_messages(captured: list) -> list[str]:
    """Contenus des messages role=tool vus dans le dernier tour capturé."""
    if not captured:
        return []
    return [m.get("content", "") for m in captured[-1] if m.get("role") == "tool"]


# --------------------------------------------------------------------------- #
# Fixture : TestClient avec LLM faux + routeur/best-of-N désactivés.            #
# --------------------------------------------------------------------------- #

@pytest.fixture
def chat_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("services.ensure_librarybrain", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        "services.get_librarybrain_status",
        lambda: {"running": False, "books": 0, "url": ""},
    )
    # Déterminisme : pas de routeur LLM, pas de best-of-N (un seul appel chat).
    monkeypatch.setattr("agent.orchestrator.ROUTER_ENABLED", False)
    monkeypatch.setattr("agent.orchestrator.BEST_OF_N_ENABLED", False)
    # Le client OpenAI bas niveau devient notre faux scripté.
    monkeypatch.setattr("agent.llm.OpenAI", FakeOpenAI)

    from api.server import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def _drain_until(ws, wanted: str, max_msgs: int = 80) -> list[dict]:
    """Collecte les events jusqu'à `wanted` (inclus). Lève si absent."""
    events = []
    for _ in range(max_msgs):
        msg = ws.receive_json()
        events.append(msg)
        if msg["type"] == wanted:
            return events
    raise AssertionError(f"event '{wanted}' jamais reçu (vu: {[e['type'] for e in events]})")


def _connect_ready(ws) -> None:
    for _ in range(10):
        if ws.receive_json()["type"] == "session_init":
            return
    raise AssertionError("pas de session_init")


class TestChatRoundTrip:
    def test_reponse_texte_emet_token_et_done(self, chat_client):
        FakeOpenAI._turns = [_text_turn("Bonjour je suis Klody")]
        with chat_client.websocket_connect("/api/ws") as ws:
            _connect_ready(ws)
            ws.send_json({"type": "chat", "content": "dis bonjour"})
            events = _drain_until(ws, "done")
            types = [e["type"] for e in events]
            assert "thinking" in types
            assert "token" in types
            assert "message_stats" in types
            assert types[-1] == "done"
            # Tokens RÉELS propagés depuis le chunk usage (include_usage).
            stats = next(e for e in events if e["type"] == "message_stats")
            assert stats["tokens"] == 7
            assert stats["total_tokens"] == 18
            # Le texte streamé reconstitue la réponse.
            streamed = "".join(e["content"] for e in events if e["type"] == "token")
            assert "Klody" in streamed

    def test_tool_call_round_trip(self, chat_client):
        # Tour 1 : le LLM appelle list_skills (lecture seule, sans effet de bord) ;
        # tour 2 : il conclut en texte → la boucle s'arrête.
        FakeOpenAI._turns = [
            _tool_turn("call_1", "list_skills", "{}"),
            _text_turn("Voici tes skills disponibles"),
        ]
        with chat_client.websocket_connect("/api/ws") as ws:
            _connect_ready(ws)
            ws.send_json({"type": "chat", "content": "liste mes skills"})
            events = _drain_until(ws, "done")
            types = [e["type"] for e in events]
            # Le tool a bien été exécuté et son résultat renvoyé à l'UI.
            assert "tool_call" in types
            assert "tool_result" in types
            tc = next(e for e in events if e["type"] == "tool_call")
            assert tc.get("name") == "list_skills"
            assert types[-1] == "done"


class TestThinkingStream:
    """Mode raisonnement (Levier 2) sur le chemin WebSocket : quand
    `_should_think()` est vrai, `stream_api` doit DIFFUSER le CoT (delta.reasoning)
    à l'UI via des events `reasoning`, AVANT les tokens — sinon l'utilisateur fixe
    un placeholder figé ~33 s (A/B 08/06). Le CoT ne doit jamais polluer le content."""

    def test_reasoning_diffuse_avant_les_tokens(self, chat_client, monkeypatch):
        # Force le thinking pour CE tour (indépendant du routeur, désactivé ici).
        monkeypatch.setattr(
            "agent.orchestrator.Orchestrator._should_think", lambda self: True
        )
        FakeOpenAI._turns = [
            _thinking_turn("je pose le raisonnement", "Voici la reponse finale")
        ]
        with chat_client.websocket_connect("/api/ws") as ws:
            _connect_ready(ws)
            ws.send_json({"type": "chat", "content": "explique en raisonnant"})
            events = _drain_until(ws, "done")
            types = [e["type"] for e in events]
            assert "reasoning" in types, f"CoT non diffusé (vu: {types})"
            # Le CoT est reconstitué côté UI…
            cot = "".join(e["content"] for e in events if e["type"] == "reasoning")
            assert "raisonnement" in cot
            # …mais NE fuite PAS dans le content (token) — canaux séparés.
            streamed = "".join(e["content"] for e in events if e["type"] == "token")
            assert "raisonnement" not in streamed
            assert "reponse" in streamed
            # Ordre : le 1er `reasoning` précède le 1er `token` (attente rendue visible).
            first_reasoning = next(i for i, e in enumerate(events) if e["type"] == "reasoning")
            first_token = next(i for i, e in enumerate(events) if e["type"] == "token")
            assert first_reasoning < first_token


class TestInteractiveQuestionRoundTrip:
    """ask_user : le tour se met en pause (question_request), l'UI renvoie le
    choix (question_response), l'agent reprend avec la réponse en tool_result.
    C'est la mécanique « questions une-à-une » des skills QCM."""

    def test_question_reponse_round_trip(self, chat_client):
        import json

        # Tour 1 : le LLM pose UNE question via ask_user ; tour 2 : il conclut.
        FakeOpenAI._turns = [
            _tool_turn(
                "call_q",
                "ask_user",
                json.dumps({
                    "question": "Quel type de jeu ?",
                    "options": ["Plateforme", "Tir"],
                }),
            ),
            _text_turn("Parfait, on part sur un platformer"),
        ]
        with chat_client.websocket_connect("/api/ws") as ws:
            _connect_ready(ws)
            ws.send_json({"type": "chat", "content": "aide-moi à concevoir mon jeu"})

            # L'agent bloque sur la question : on draine jusqu'à question_request.
            events = _drain_until(ws, "question_request")
            q = events[-1]
            assert q["question"] == "Quel type de jeu ?"
            assert q["options"] == ["Plateforme", "Tir"]
            assert q.get("allow_free_text") is True

            # ask_user n'émet PAS de tool_call/tool_result UI (canal dédié).
            types = [e["type"] for e in events]
            assert "tool_call" not in types and "tool_result" not in types

            # L'UI répond → débloque le thread orchestrator.
            ws.send_json({"type": "question_response", "id": q["id"], "answer": "Plateforme"})

            tail = _drain_until(ws, "done")
            assert tail[-1]["type"] == "done"
            # La réponse revient au modèle via un tool result réinjecté en contexte.
            assert any("Plateforme" in c for c in _tool_messages(FakeOpenAI.captured))

    def test_questions_posees_une_a_une(self, chat_client):
        """Cœur du feature : 3 ask_user successifs sont posés UN À UN (la 2e
        question n'est émise qu'après la réponse à la 1re), puis synthèse. Vérifie
        que la boucle ReAct enchaîne Q1→réponse→Q2→réponse→Q3→réponse→conclusion."""
        import json

        FakeOpenAI._turns = [
            _tool_turn("q1", "ask_user", json.dumps({"question": "Nature ?", "options": ["Trier", "Chercher"]})),
            _tool_turn("q2", "ask_user", json.dumps({"question": "Entrées ?", "options": ["Liste", "Graphe"]})),
            _tool_turn("q3", "ask_user", json.dumps({"question": "Volume ?", "options": ["Petit", "Grand"]})),
            _text_turn("Fiche de besoin synthétisée"),
        ]
        with chat_client.websocket_connect("/api/ws") as ws:
            _connect_ready(ws)
            ws.send_json({"type": "chat", "content": "conçois mon algo pas à pas"})

            seen = []
            for expected, answer in [("Nature ?", "Trier"), ("Entrées ?", "Liste"), ("Volume ?", "Petit")]:
                ev = _drain_until(ws, "question_request")[-1]
                assert ev["question"] == expected, f"questions hors séquence : {ev['question']}"
                seen.append(ev["question"])
                ws.send_json({"type": "question_response", "id": ev["id"], "answer": answer})

            tail = _drain_until(ws, "done")
            assert len(seen) == 3  # les trois questions ont bien été posées séparément
            assert tail[-1]["type"] == "done"
            # Les 3 réponses ont été réinjectées au modèle (contexte du tour final).
            tools = _tool_messages(FakeOpenAI.captured)
            assert any("Trier" in c for c in tools)
            assert any("Liste" in c for c in tools)
            assert any("Petit" in c for c in tools)

    def test_double_reponse_ignoree(self, chat_client):
        """Idempotence : une 2e question_response pour le même id (double-clic /
        message tardif) ne corrompt pas la réponse déjà livrée."""
        import json

        FakeOpenAI._turns = [
            _tool_turn("q1", "ask_user", json.dumps({"question": "Choix ?", "options": ["A", "B"]})),
            _text_turn("ok"),
        ]
        with chat_client.websocket_connect("/api/ws") as ws:
            _connect_ready(ws)
            ws.send_json({"type": "chat", "content": "demande"})
            ev = _drain_until(ws, "question_request")[-1]
            # Première réponse (retenue) puis doublon (ignoré).
            ws.send_json({"type": "question_response", "id": ev["id"], "answer": "A"})
            ws.send_json({"type": "question_response", "id": ev["id"], "answer": "B"})
            tail = _drain_until(ws, "done")
            assert tail[-1]["type"] == "done"
            # Seule la 1re réponse ("A") est retenue ; le doublon "B" est ignoré.
            tools = _tool_messages(FakeOpenAI.captured)
            assert any("Réponse de l'utilisateur : A" in c for c in tools)
            assert not any("Réponse de l'utilisateur : B" in c for c in tools)
