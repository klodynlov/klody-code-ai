"""Garde anti-boucle câblée dans le chemin CLI (`LLMClient.stream_chat`).

Dernier angle mort fermé : avant, ni le `content` ni le RAISONNEMENT (CoT) du
chemin terminal n'étaient gardés (seul le chemin WS `stream_api` l'était). On
prouve, sans LLM réel (fake `create`), que :
  - une boucle dégénérée dans le CoT coupe le stream et relance UNE passe SANS
    thinking → vraie réponse ;
  - une répétition dégénérée dans le content est effondrée sur une copie ;
  - le mode silencieux (BoN/router) reste NON gardé (comportement historique).
"""
from types import SimpleNamespace

from agent.llm import LLMClient


class _Delta(SimpleNamespace):
    def __init__(self, content=None, reasoning=None, tool_calls=None):
        super().__init__(
            content=content, reasoning=reasoning,
            tool_calls=tool_calls, model_extra={},
        )


def _chunk(delta):
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class _ClosableIter:
    """Itérable de chunks avec `.close()` observable (comme le stream SDK)."""

    def __init__(self, chunks):
        self._it = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        return next(self._it)

    def close(self):
        self.closed = True


class _MultiStreamCompletions:
    """`create()` renvoie un stream différent par appel → teste la récupération."""

    def __init__(self, streams):
        self._streams = [list(s) for s in streams]
        self.captured: list[dict] = []
        self.streams: list[_ClosableIter] = []

    def create(self, **params):
        self.captured.append(params)
        it = _ClosableIter(self._streams.pop(0))
        self.streams.append(it)
        return it


def _make_client(streams):
    c = LLMClient.__new__(LLMClient)
    c.model = "brain"
    c.total_tokens = 0
    c._backend = "mlx"
    c.client = SimpleNamespace(
        chat=SimpleNamespace(completions=_MultiStreamCompletions(streams))
    )
    return c


def _force_guard(monkeypatch):
    monkeypatch.setattr("agent.llm.LLM_LOOP_GUARD", True)
    monkeypatch.setattr("agent.llm.LLM_LOOP_REPS", 4)
    monkeypatch.setattr("agent.llm.LLM_LOOP_MIN_UNIT", 16)
    monkeypatch.setattr("agent.llm.LLM_REASONING_LOOP_REPS", 6)


class TestReasoningLoop:
    def test_boucle_cot_coupe_et_recupere_sans_thinking(self, monkeypatch):
        _force_guard(monkeypatch)
        unit = "Je re-dérive exactement la même étape de raisonnement.\n"
        loop_stream = [_chunk(_Delta(reasoning=unit)) for _ in range(12)]
        recovery_stream = [_chunk(_Delta(content="Réponse directe sans thinking."))]
        client = _make_client([loop_stream, recovery_stream])
        comps = client.client.chat.completions

        content, tool_calls = client.stream_chat(
            [{"role": "user", "content": "x"}], enable_thinking=True,
        )

        assert content == "Réponse directe sans thinking."
        assert tool_calls is None
        # 2 appels create : boucle CoT coupée + passe de récupération.
        assert len(comps.captured) == 2
        assert comps.streams[0].closed is True   # 1er stream fermé en amont
        # La récupération est SANS thinking (pas de chat_template_kwargs).
        assert "chat_template_kwargs" not in comps.captured[-1].get("extra_body", {})

    def test_silent_non_garde(self, monkeypatch):
        # BoN/router : jamais gardé → pas de récupération, le CoT est juste consommé.
        _force_guard(monkeypatch)
        unit = "Je re-dérive exactement la même étape de raisonnement.\n"
        loop_stream = [_chunk(_Delta(reasoning=unit)) for _ in range(12)]
        client = _make_client([loop_stream])
        comps = client.client.chat.completions

        content, tool_calls = client.stream_chat(
            [{"role": "user", "content": "x"}], enable_thinking=True, silent=True,
        )

        assert content == ""          # 0 content, mais aucun crash
        assert tool_calls is None
        assert len(comps.captured) == 1   # aucune récupération déclenchée


class TestContentLoop:
    def test_repetition_content_effondree_sur_une_copie(self, monkeypatch):
        _force_guard(monkeypatch)
        unit = "Je répète toujours la même phrase ici. "  # > MIN_UNIT
        chunks = [_chunk(_Delta(content="Début : "))] + [
            _chunk(_Delta(content=unit)) for _ in range(16)
        ]
        client = _make_client([chunks])
        comps = client.client.chat.completions

        content, tool_calls = client.stream_chat([{"role": "user", "content": "x"}])

        assert tool_calls is None
        assert content.startswith("Début :")
        assert content.count(unit.strip()) == 1   # effondré sur 1 occurrence
        assert comps.streams[0].closed is True     # stream coupé
