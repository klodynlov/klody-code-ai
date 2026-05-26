"""Capture des métriques LLM (tokens, tool calls) via monkey-patch léger
du LLMClient existant. Pas d'intrusion permanente dans le code Klody."""
from __future__ import annotations

import dataclasses
import json
from contextlib import contextmanager


@dataclasses.dataclass
class RunMetrics:
    tokens_generated: int = 0
    tool_calls_total: int = 0
    tool_calls_broken: int = 0
    iterations: int = 0

    def reset(self) -> None:
        self.tokens_generated = 0
        self.tool_calls_total = 0
        self.tool_calls_broken = 0
        self.iterations = 0


_CURRENT: RunMetrics | None = None


def current() -> RunMetrics:
    if _CURRENT is None:
        raise RuntimeError("metrics.current() appelé hors d'un bloc capture()")
    return _CURRENT


@contextmanager
def capture():
    """Active un RunMetrics partagé pendant le bloc."""
    global _CURRENT
    m = RunMetrics()
    _CURRENT = m
    try:
        yield m
    finally:
        _CURRENT = None


def install_patches():
    """Monkey-patch agent.llm.LLMClient.stream_chat pour capter les métriques.

    Best-effort : si la signature interne change, on logge mais on ne crashe pas.
    Le bench reste fonctionnel pour latency/success même sans ces métriques.
    """
    try:
        from agent import llm as klody_llm
    except Exception:
        return False

    original = klody_llm.LLMClient.stream_chat

    def patched(self, messages, tools=None, *args, **kwargs):
        content, tool_calls = original(self, messages, tools=tools, *args, **kwargs)
        m = _CURRENT
        if m is not None:
            m.iterations += 1
            if content:
                # estimation 1 token ≈ 4 chars pour les modèles Qwen
                m.tokens_generated += max(1, len(content) // 4)
            if tool_calls:
                m.tool_calls_total += len(tool_calls)
                for tc in tool_calls:
                    args_str = tc.get("function", {}).get("arguments", "")
                    try:
                        json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        m.tool_calls_broken += 1
        return content, tool_calls

    klody_llm.LLMClient.stream_chat = patched
    return True
