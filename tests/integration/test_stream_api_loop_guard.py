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
