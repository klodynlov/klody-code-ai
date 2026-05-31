"""Routage think/no_think + helpers du proxy RAG (scripts/rag-proxy.py).

Tests offline : on n'instancie pas FastAPI, on appelle directement les
helpers purs. La route HTTP elle-même est testée par un client e2e séparé
(non inclus ici : nécessite mlx-lm vivant sur :8080).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RAG_PROXY_PATH = ROOT / "scripts" / "rag-proxy.py"


@pytest.fixture(scope="module")
def rp():
    """Charge `scripts/rag-proxy.py` comme module (le tiret du nom empêche
    l'import classique)."""
    spec = importlib.util.spec_from_file_location("rag_proxy", RAG_PROXY_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["rag_proxy"] = module
    spec.loader.exec_module(module)
    return module


# ── _should_think ──────────────────────────────────────────────────────────────

class TestShouldThink:
    def test_default_is_false(self, rp) -> None:
        assert rp._should_think([{"role": "user", "content": "salut"}], {}) is False

    def test_think_keyword_triggers_true(self, rp) -> None:
        msgs = [{"role": "user", "content": "refactor ce module pour le rendre testable"}]
        assert rp._should_think(msgs, {}) is True

    def test_no_think_keyword_wins(self, rp) -> None:
        msgs = [{"role": "user", "content": "applique le skill productivity à mon projet"}]
        assert rp._should_think(msgs, {}) is False

    def test_explicit_chat_template_kwargs_override(self, rp) -> None:
        msgs = [{"role": "user", "content": "refactor refactor refactor"}]
        body = {"chat_template_kwargs": {"enable_thinking": False}}
        # L'override doit gagner contre l'heuristique "think".
        assert rp._should_think(msgs, body) is False

    def test_explicit_thinking_field_override(self, rp) -> None:
        msgs = [{"role": "user", "content": "petit fix typo"}]
        assert rp._should_think(msgs, {"thinking": True}) is True

    def test_accent_insensitive(self, rp) -> None:
        """`distille` (sans accent) doit matcher même si l'utilisateur écrit
        `distillé` (avec accent) — la normalisation supprime les accents."""
        msgs = [{"role": "user", "content": "distille-moi ce livre"}]
        assert rp._should_think(msgs, {}) is True


# ── _apply_thinking_mode ───────────────────────────────────────────────────────

class TestApplyThinkingMode:
    def test_sets_chat_template_kwargs(self, rp) -> None:
        body: dict = {}
        rp._apply_thinking_mode(body, [{"role": "user", "content": "x"}], think=True)
        assert body["chat_template_kwargs"]["enable_thinking"] is True

    def test_appends_inline_tag_to_existing_system(self, rp) -> None:
        msgs = [{"role": "system", "content": "Tu es Klody."},
                {"role": "user",   "content": "x"}]
        out = rp._apply_thinking_mode({}, msgs, think=False)
        assert out[0]["role"] == "system"
        assert out[0]["content"].endswith("/no_think")
        # Le contenu original est préservé.
        assert "Tu es Klody." in out[0]["content"]

    def test_creates_system_when_absent(self, rp) -> None:
        msgs = [{"role": "user", "content": "x"}]
        out = rp._apply_thinking_mode({}, msgs, think=True)
        assert out[0]["role"] == "system"
        assert out[0]["content"] == "/think"
        assert out[1]["role"] == "user"

    def test_replaces_existing_inline_tag(self, rp) -> None:
        """Si l'utilisateur a déjà mis un `/think` mais qu'on décide no_think,
        le proxy doit imposer sa décision."""
        msgs = [{"role": "system", "content": "Base. /think suite"}]
        out = rp._apply_thinking_mode({}, msgs, think=False)
        assert out[0]["content"].count("/think") == 0  # pas le mot
        assert out[0]["content"].endswith("/no_think")

    def test_only_last_system_message_tagged(self, rp) -> None:
        msgs = [
            {"role": "system", "content": "preamble"},
            {"role": "user",   "content": "x"},
            {"role": "system", "content": "rappel"},
        ]
        out = rp._apply_thinking_mode({}, msgs, think=True)
        assert "/think" not in out[0]["content"]
        assert out[2]["content"].endswith("/think")

    def test_does_not_mutate_caller_messages(self, rp) -> None:
        msgs = [{"role": "system", "content": "S"}]
        original = msgs[0]["content"]
        rp._apply_thinking_mode({}, msgs, think=True)
        assert msgs[0]["content"] == original


# ── _detect_domain ─────────────────────────────────────────────────────────────

class TestDetectDomain:
    @pytest.mark.parametrize("text, expected", [
        ("écris un test pytest avec pydantic",           "python"),
        ("composant Server Component dans Next.js",      "nextjs"),
        ("repository Doctrine pour mon Entity Symfony",  "symfony"),
        ("LoRA fine-tuning sur Apple Silicon",           "mlx"),
        ("salut",                                        "python"),  # défaut
    ])
    def test_keyword_routing(self, rp, text: str, expected: str) -> None:
        assert rp._detect_domain(text) == expected


# ── _inject_context ────────────────────────────────────────────────────────────

class TestInjectContext:
    def test_no_op_when_context_empty(self, rp) -> None:
        msgs = [{"role": "user", "content": "x"}]
        assert rp._inject_context(msgs, "") is msgs

    def test_prepends_to_existing_system(self, rp) -> None:
        msgs = [{"role": "system", "content": "Tu es Klody."}]
        out = rp._inject_context(msgs, "Source A")
        assert "<context>" in out[0]["content"]
        assert "Tu es Klody." in out[0]["content"]

    def test_creates_system_when_absent(self, rp) -> None:
        msgs = [{"role": "user", "content": "x"}]
        out = rp._inject_context(msgs, "Source A")
        assert out[0]["role"] == "system"
        assert "Source A" in out[0]["content"]
