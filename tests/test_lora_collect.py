"""Tests pour scripts/lora/collect_sessions.py — extraction paires user→assistant."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Importer la fonction directement (le script n'est pas un module standard)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lora"))
from collect_sessions import _extract_pairs


class TestExtractPairs:
    def test_paire_simple(self):
        session = [
            {"role": "system", "content": "you are klody"},
            {"role": "user", "content": "renomme x en y"},
            {"role": "assistant", "content": "Fait."},
        ]
        pairs = _extract_pairs(session)
        assert len(pairs) == 1
        assert pairs[0]["messages"][0]["content"] == "renomme x en y"
        assert pairs[0]["messages"][1]["content"] == "Fait."

    def test_plusieurs_paires(self):
        session = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        pairs = _extract_pairs(session)
        assert len(pairs) == 2
        assert pairs[0]["messages"][0]["content"] == "Q1"
        assert pairs[1]["messages"][0]["content"] == "Q2"

    def test_user_vide_skippe(self):
        session = [
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": "A"},
        ]
        assert _extract_pairs(session) == []

    def test_assistant_vide_skippe(self):
        session = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": ""},
        ]
        assert _extract_pairs(session) == []

    def test_concatene_assistant_multiples(self):
        session = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "step1", "tool_calls": [{"a": 1}]},
            {"role": "tool", "content": "result1"},
            {"role": "assistant", "content": "step2"},
        ]
        pairs = _extract_pairs(session)
        assert len(pairs) == 1
        c = pairs[0]["messages"][1]["content"]
        assert "step1" in c and "step2" in c

    def test_min_tools_filtre(self):
        session_no_tools = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A"},
        ]
        session_with_tools = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A", "tool_calls": [{"x": 1}]},
        ]
        # min_tools=1 → filtre la première
        assert _extract_pairs(session_no_tools, min_tools=1) == []
        assert len(_extract_pairs(session_with_tools, min_tools=1)) == 1

    def test_meta_contient_tool_calls(self):
        session = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A", "tool_calls": [{"a": 1}, {"b": 2}]},
        ]
        pairs = _extract_pairs(session)
        assert pairs[0]["_meta"]["tool_calls"] == 2
