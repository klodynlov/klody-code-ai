"""Parité de signature entre stream_api et LLMClient.stream_chat.

Le chemin API (WebSocket) fabrique un `stream_api` qui REMPLACE
`LLMClient.stream_chat` sur l'orchestrateur. Si un kwarg diverge
(ajouté d'un côté, oublié de l'autre), le LLM reçoit des paramètres
tronqués — le symptôme (thinking coupé, loop-guard absent…) ne pointe
JAMAIS vers une signature décalée. Ce test le rend visible.
"""
from __future__ import annotations

import inspect

from agent.llm import LLMClient
from api.streaming import make_stream_api


def test_parite_signature_stream_api_vs_stream_chat():
    """Les kwargs de stream_api (hors closure) = ceux de stream_chat (hors self)."""
    sig_llm = inspect.signature(LLMClient.stream_chat)
    params_llm = {
        name: p
        for name, p in sig_llm.parameters.items()
        if name != "self"
    }

    # stream_api est une closure retournée par make_stream_api — on l'obtient
    # en passant des stubs (jamais appelés, on veut juste la signature).
    class _Stub:
        llm = type("LLM", (), {"model": "test"})()
    stream_api = make_stream_api(_Stub(), lambda _: None, None)
    sig_stream = inspect.signature(stream_api)
    params_stream = dict(sig_stream.parameters)

    noms_llm = set(params_llm)
    noms_stream = set(params_stream)

    manquants_stream = noms_llm - noms_stream
    manquants_llm = noms_stream - noms_llm

    assert not manquants_stream, (
        f"kwargs dans stream_chat ABSENTS de stream_api : {manquants_stream}"
    )
    assert not manquants_llm, (
        f"kwargs dans stream_api ABSENTS de stream_chat : {manquants_llm}"
    )

    for name in noms_llm:
        p_llm = params_llm[name]
        p_stream = params_stream[name]
        if p_llm.default is not inspect.Parameter.empty:
            assert p_stream.default == p_llm.default, (
                f"default diverge pour '{name}' : "
                f"stream_chat={p_llm.default!r}, stream_api={p_stream.default!r}"
            )
