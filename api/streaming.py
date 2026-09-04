"""Streaming LLM pour l'API WebSocket — extrait de `api/server.py` (lot 4.2).

Contient `stream_api`, le chemin de prod de la génération (pas de TTY, pas de
Rich). Sa signature DOIT rester alignée avec `LLMClient.stream_chat` — un test
de parité (`tests/test_stream_signature.py`) rougit dès qu'un kwarg manque.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Any

import config
from agent.orchestrator import Orchestrator
from agent.stream_guard import LoopGuard

logger = logging.getLogger(__name__)


class StopGeneration(Exception):
    pass


def make_stream_api(
    orch: Orchestrator,
    _put: Callable[[dict], None],
    stop_flag: list[bool] | None,
) -> Callable[..., tuple[str, Any]]:
    """Fabrique la closure `stream_api` branchée sur un orchestrateur donné.

    Retourne une fonction dont la signature est identique à
    `LLMClient.stream_chat` (hors `self`).
    """

    def stream_api(
        messages: list[dict],
        tools=None,
        token_callback=None,
        temperature: float = 0.1,
        silent: bool = False,
        tool_choice: str = "auto",
        max_tokens: int = 8192,
        enable_thinking: bool = False,
        thinking_budget: int | None = None,
        _recovering: bool = False,
    ) -> tuple[str, Any]:
        """Streaming direct sans Rich — pour l'API server (pas de TTY).

        Signature alignée avec LLMClient.stream_chat.
        max_tokens=8192 par défaut : permet de générer des gros fichiers
        (Three.js avec scène complète peut faire 3-5KB, donc ~1500-2000 tokens
        rien que pour le content de write_file). Avant : MLX coupait à ~500 tokens.
        """
        import time as _t
        t0 = _t.perf_counter()
        if not silent:
            _put({"type": "thinking"})

        if enable_thinking:
            max_tokens = max(max_tokens, config.THINKING_MAX_TOKENS)
        params: dict = {
            "model": orch.llm.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        extra_body: dict = {}
        if config.LLM_REPETITION_PENALTY > 1.0:
            extra_body["repetition_penalty"] = config.LLM_REPETITION_PENALTY
        if enable_thinking:
            ctk: dict = {"enable_thinking": True}
            if thinking_budget is not None and config.THINKING_BUDGET_FORWARD:
                ctk["thinking_budget"] = thinking_budget
            extra_body["chat_template_kwargs"] = ctk
        if extra_body:
            params["extra_body"] = extra_body
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        full_content = ""
        reasoning_buf = ""
        raw_tool_calls: dict = {}
        usage = None

        loop_guard = (
            LoopGuard(
                reps=config.LLM_LOOP_REPS,
                min_unit=config.LLM_LOOP_MIN_UNIT,
                window=config.LLM_LOOP_WINDOW,
            )
            if config.LLM_LOOP_GUARD and not silent
            else None
        )
        reasoning_guard = (
            LoopGuard(
                reps=config.LLM_REASONING_LOOP_REPS,
                min_unit=config.LLM_LOOP_MIN_UNIT,
                window=config.LLM_LOOP_WINDOW,
            )
            if config.LLM_LOOP_GUARD and enable_thinking and not silent and not _recovering
            else None
        )

        if stop_flag and stop_flag[0]:
            raise StopGeneration()

        try:
            stream = orch.llm.client.chat.completions.create(**params)
            for chunk in stream:
                if stop_flag and stop_flag[0]:
                    if not silent:
                        _put({"type": "stream_end"})
                    raise StopGeneration()
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if enable_thinking and not silent:
                    reasoning_delta = orch.llm._delta_reasoning(delta)
                    if reasoning_delta:
                        _put({"type": "reasoning", "content": reasoning_delta})
                        if reasoning_guard is not None:
                            reasoning_buf += reasoning_delta
                            if reasoning_guard.cut(reasoning_buf) is not None:
                                logger.warning(
                                    "[loop-guard] boucle dégénérée dans le RAISONNEMENT "
                                    "(CoT) coupée après %d chars → réponse directe sans "
                                    "thinking", len(reasoning_buf),
                                )
                                with contextlib.suppress(Exception):
                                    stream.close()
                                _put({"type": "reasoning", "content":
                                      "\n⚠ boucle de raisonnement coupée — réponse directe."})
                                return stream_api(
                                    messages, tools=tools,
                                    token_callback=token_callback,
                                    temperature=temperature, silent=silent,
                                    tool_choice=tool_choice, max_tokens=max_tokens,
                                    enable_thinking=False, thinking_budget=None,
                                    _recovering=True,
                                )
                if delta.content:
                    full_content += delta.content
                    if not silent:
                        _put({"type": "token", "content": delta.content})
                    if token_callback:
                        token_callback(delta.content)
                    if loop_guard is not None:
                        cut = loop_guard.cut(full_content)
                        if cut is not None and cut < len(full_content):
                            trimmed = full_content[:cut].rstrip()
                            logger.warning(
                                "[loop-guard] répétition dégénérée coupée : %d → %d chars",
                                len(full_content), len(trimmed),
                            )
                            with contextlib.suppress(Exception):
                                stream.close()
                            full_content = trimmed
                            if not silent:
                                _put({"type": "stream_trim", "content": trimmed})
                            return full_content, (
                                list(raw_tool_calls.values()) if raw_tool_calls else None
                            )
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        orch.llm._accumulate_tool_call(raw_tool_calls, tc)
        except StopGeneration:
            raise
        except Exception as e:
            if not silent:
                _put({"type": "error", "content": str(e)})
            raise

        tool_calls = list(raw_tool_calls.values()) if raw_tool_calls else None

        if not tool_calls and full_content and tools:
            valid_names = {t["function"]["name"] for t in tools}
            text_part, parsed = orch.llm.extract_mixed_tool_call(full_content, valid_names)
            if parsed:
                tool_calls = parsed
                if not silent:
                    if text_part:
                        _put({"type": "stream_trim", "content": text_part})
                    else:
                        _put({"type": "discard_stream"})
                full_content = text_part
                return full_content, tool_calls

        if full_content and not silent:
            _put({"type": "stream_end"})
            elapsed = round(_t.perf_counter() - t0, 2)
            if usage is not None:
                completion_toks = getattr(usage, "completion_tokens", 0) or 0
                prompt_toks = getattr(usage, "prompt_tokens", 0) or 0
                total_toks = getattr(usage, "total_tokens", 0) or (prompt_toks + completion_toks)
            else:
                completion_toks = max(1, len(full_content) // 4)
                prompt_toks = 0
                total_toks = completion_toks
            _put({"type": "message_stats", "latency_s": elapsed,
                  "tokens": completion_toks, "prompt_tokens": prompt_toks,
                  "total_tokens": total_toks, "context_window": config.CONTEXT_WINDOW,
                  "model": orch.llm.model})

        orch.llm.total_tokens += (
            (getattr(usage, "completion_tokens", 0) or 0) if usage is not None
            else len(full_content) // 4
        )

        return full_content, tool_calls

    return stream_api
