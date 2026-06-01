"""FakeLLMClient — rejoue des réponses LLM depuis une fixture JSON.

Objectif : non-régression comportementale. La régression du 27/05 (max_tokens
non défini → HTML tronqué) aurait été bloquée par un scénario figé qui
simule la réponse tronquée et vérifie que l'orchestrator détecte/recover.

Signature : compatible avec agent.llm.LLMClient.stream_chat() pour drop-in
remplacement via monkeypatch.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path


class FixtureExhausted(RuntimeError):
    """Le scénario a appelé le LLM plus de fois que la fixture n'a de réponses."""


class FakeLLMClient:
    """Stub LLM. Rejoue les réponses du fichier fixture dans l'ordre."""

    def __init__(self, fixture: dict | str | Path, model: str = "fake-model"):
        if isinstance(fixture, (str, Path)):
            fixture = json.loads(Path(fixture).read_text(encoding="utf-8"))
        self.fixture = fixture
        self.model = model
        self.responses: list[dict] = list(fixture.get("llm_responses", []))
        self._cursor = 0
        self.total_tokens = 0
        # Historique d'appels reçus (pour assertions de test)
        self.call_log: list[dict] = []

    def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        token_callback: Callable[[str], None] | None = None,
        temperature: float = 0.1,
        silent: bool = False,
        tool_choice: str = "auto",
        max_tokens: int = 8192,
    ) -> tuple[str, list[dict] | None]:
        """Compatible avec LLMClient.stream_chat — retourne (content, tool_calls)."""
        if self._cursor >= len(self.responses):
            raise FixtureExhausted(
                f"Fixture '{self.fixture.get('name', '?')}' épuisée après "
                f"{self._cursor} appels (orchestrator continue à appeler le LLM)"
            )

        # Log l'appel pour assertions
        self.call_log.append({
            "messages_count": len(messages),
            "last_role": messages[-1]["role"] if messages else None,
            "tools_count": len(tools) if tools else 0,
            "tool_choice": tool_choice,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })

        resp = self.responses[self._cursor]
        self._cursor += 1

        content = resp.get("content", "") or ""
        tool_calls = resp.get("tool_calls")

        # Streame le contenu via callback (mimique le streaming réel)
        if content and token_callback:
            # Découpe en petits chunks pour ressembler à du streaming
            chunk_size = max(1, len(content) // 20)
            for i in range(0, len(content), chunk_size):
                token_callback(content[i:i + chunk_size])

        # Estimation tokens
        self.total_tokens += len(content) // 4

        return content, tool_calls if tool_calls else None

    @property
    def remaining(self) -> int:
        return len(self.responses) - self._cursor

    @property
    def consumed(self) -> int:
        return self._cursor


class FakeRouter:
    """Router stub — retourne une décision prédéfinie sans appeler le LLM."""

    def __init__(self, decision: dict):
        self._decision_dict = decision

    def classify(self, user_prompt: str):
        from agent.router import RoutingDecision, _decide_strategy

        diff = self._decision_dict.get("difficulty", "medium")
        ttype = self._decision_dict.get("task_type", "explain")
        strategy = _decide_strategy(diff, ttype)
        return RoutingDecision(
            difficulty=diff,
            task_type=ttype,
            reasoning=self._decision_dict.get("reasoning", "fake-router"),
            raw_response="",
            **strategy,
        )
