"""Garde anti-boucle câblée dans le VRAI chemin WS (`stream_api`).

Prouve que `_build_streaming_orchestrator` produit un `stream_chat` qui coupe une
répétition dégénérée et émet `stream_trim` avec le contenu effondré — sans LLM
réel : `client.chat.completions.create` est remplacé par un faux stream.
"""
import asyncio
from types import SimpleNamespace

import api.server as server
from agent.memory import ConversationMemory


def _chunk(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))],
        usage=None,
    )


def _rchunk(text: str):
    """Chunk de RAISONNEMENT (CoT) : le brain Qwen3 le pose dans `delta.reasoning`."""
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(
            reasoning=text, content=None, tool_calls=None))],
        usage=None,
    )


class _FakeStream:
    """Itérable de chunks façon SDK OpenAI, avec `.close()` observable."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    def __iter__(self):
        for c in self._chunks:
            if self.closed:
                return
            yield c

    def close(self):
        self.closed = True


async def test_stream_api_coupe_repetition_degeneree():
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    memory = ConversationMemory(session_id="test-loopguard")
    orch = server._build_streaming_orchestrator(memory, "brain", queue, loop)

    unit = "Je répète exactement la même phrase. "  # > LLM_LOOP_MIN_UNIT
    fake = _FakeStream([_chunk("Réponse : ")] + [_chunk(unit) for _ in range(16)])
    orch.llm.client.chat.completions.create = lambda **kw: fake

    content, tool_calls = await asyncio.to_thread(
        orch.llm.stream_chat, [{"role": "user", "content": "x"}]
    )

    # Contenu effondré sur UNE copie (rstrip côté coupe) + stream coupé en amont.
    expected = ("Réponse : " + unit).rstrip()
    assert content == expected
    assert tool_calls is None
    assert fake.closed is True

    # `stream_trim` émis vers l'UI avec le contenu propre.
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    trims = [e for e in events if e.get("type") == "stream_trim"]
    assert trims, f"aucun stream_trim émis ; events={[e.get('type') for e in events]}"
    assert trims[-1]["content"] == expected


async def test_stream_api_coupe_boucle_raisonnement():
    """Boucle dégénérée dans le CoT (0 content, 0 tool call) : le garde reasoning
    coupe le stream et relance UNE passe sans thinking → réponse directe."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    memory = ConversationMemory(session_id="test-reasoning-loop")
    orch = server._build_streaming_orchestrator(memory, "brain", queue, loop)

    unit = "Je re-dérive exactement la même étape de raisonnement.\n"  # > MIN_UNIT
    loop_stream = _FakeStream([_rchunk(unit) for _ in range(12)] + [_chunk("jamais atteint")])
    recovery_stream = _FakeStream([_chunk("Réponse directe sans thinking.")])
    streams = iter([loop_stream, recovery_stream])
    orch.llm.client.chat.completions.create = lambda **kw: next(streams)

    content, tool_calls = await asyncio.to_thread(
        lambda: orch.llm.stream_chat(
            [{"role": "user", "content": "x"}], enable_thinking=True
        )
    )

    # CoT en boucle coupé → passe de récupération SANS thinking → vraie réponse.
    assert content == "Réponse directe sans thinking."
    assert tool_calls is None
    assert loop_stream.closed is True          # 1er stream (CoT) coupé en amont
    assert recovery_stream.closed is False     # 2e stream consommé normalement

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    reasoning_ev = [e for e in events if e.get("type") == "reasoning"]
    assert any("coupée" in e.get("content", "") for e in reasoning_ev), (
        f"pas de marqueur de coupe CoT ; events={[e.get('type') for e in events]}"
    )
