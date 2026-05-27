"""Tests unitaires pour agent.profiler — UserProfiler.

Couvre :
- Détection technos / catégories
- track_request : compteurs cumulatifs + persistance JSON
- track_tool_usage : compteur outils
- get_suggestions : skills, LibraryBrain hint, pattern récurrent, preview hint
- get_profile_for_prompt : formatage prompt injection
- get_display_summary : résumé CLI
- _detect_recurring_pattern : paires consécutives
- Persistance _save/_load : roundtrip
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_profiler(tmp_path, monkeypatch):
    """Profiler dont le fichier _PROFILE_FILE pointe vers tmp_path."""
    fake_file = tmp_path / "user_profile.json"
    # Patch le path AVANT l'import de la classe pour que __init__ utilise le bon
    import agent.profiler as prof_mod
    monkeypatch.setattr(prof_mod, "_PROFILE_FILE", fake_file)
    # Reset le singleton pour récupérer une instance neuve
    monkeypatch.setattr(prof_mod, "_instance", None)
    return prof_mod.UserProfiler(), fake_file


class TestTrackRequest:
    def test_detects_python_tech(self, isolated_profiler):
        prof, _ = isolated_profiler
        out = prof.track_request("Fix the pytest config and pyproject.toml")
        assert "Python" in out["techs"]
        assert prof.tech_usage["Python"] == 1

    def test_detects_multiple_categories(self, isolated_profiler):
        prof, _ = isolated_profiler
        out = prof.track_request("Crée une API REST avec auth JWT et migration SQL")
        assert "api" in out["categories"]
        assert "data" in out["categories"]

    def test_general_when_no_match(self, isolated_profiler):
        prof, _ = isolated_profiler
        out = prof.track_request("zzz xyz qqq")
        assert out["categories"] == ["general"]

    def test_total_requests_incremented(self, isolated_profiler):
        prof, _ = isolated_profiler
        prof.track_request("foo bar python")
        prof.track_request("baz qux django")
        assert prof.total_requests == 2

    def test_recent_categories_capped_at_20(self, isolated_profiler):
        prof, _ = isolated_profiler
        for _ in range(30):
            prof.track_request("html css js")
        assert len(prof.recent_categories) == 20

    def test_persists_to_disk(self, isolated_profiler):
        prof, fake_file = isolated_profiler
        prof.track_request("api fastapi route")
        assert fake_file.exists()
        data = json.loads(fake_file.read_text())
        assert data["total_requests"] == 1
        assert "api" in data["request_categories"]


class TestTrackToolUsage:
    def test_increments(self, isolated_profiler):
        prof, _ = isolated_profiler
        prof.track_tool_usage("write_file")
        prof.track_tool_usage("write_file")
        prof.track_tool_usage("read_file")
        assert prof.top_skills_used["write_file"] == 2
        assert prof.top_skills_used["read_file"] == 1


class TestIncrementSession:
    def test_persists(self, isolated_profiler):
        prof, fake_file = isolated_profiler
        prof.increment_session()
        prof.increment_session()
        assert prof.session_count == 2
        data = json.loads(fake_file.read_text())
        assert data["session_count"] == 2


class TestGetSuggestions:
    def test_no_skills_returns_max_3(self, isolated_profiler):
        prof, _ = isolated_profiler
        # Sans skills et sans techs, suggestions est probablement vide
        out = prof.get_suggestions("hello", [])
        assert isinstance(out, list)
        assert len(out) <= 3

    def test_skill_match_by_name(self, isolated_profiler):
        prof, _ = isolated_profiler
        skills = [
            {
                "name": "deploy-fastapi",
                "description": "Deploy a FastAPI app to production with gunicorn",
                "content": "use docker compose with fastapi backend",
            },
        ]
        sugg = prof.get_suggestions("Comment je deploy mon fastapi en prod ?", skills)
        # Au moins une suggestion mentionne la skill
        assert any("deploy-fastapi" in s for s in sugg)

    def test_librarybrain_hint_on_first_tech(self, isolated_profiler):
        prof, _ = isolated_profiler
        sugg = prof.get_suggestions("Je découvre tauri", [])
        assert any("LibraryBrain" in s and "Tauri" in s for s in sugg)

    def test_preview_hint_when_web_pattern_repeated(self, isolated_profiler):
        prof, _ = isolated_profiler
        # Saturer le compteur "web" pour déclencher le hint
        for _ in range(4):
            prof.track_request("crée une page html avec un formulaire")
        sugg = prof.get_suggestions("encore une page html responsive", [])
        assert any("aperçu" in s or "preview" in s.lower() for s in sugg)


class TestRecurringPattern:
    def test_no_pattern_below_threshold(self, isolated_profiler):
        prof, _ = isolated_profiler
        prof.recent_categories = ["web", "api", "web"]
        assert prof._detect_recurring_pattern() is None

    def test_pattern_detected_when_pair_repeats(self, isolated_profiler):
        prof, _ = isolated_profiler
        # Pair "web → debug" répété 3× → doit déclencher
        prof.recent_categories = [
            "web", "debug", "web", "debug", "web", "debug",
            "api", "data", "web", "debug",
        ]
        pattern = prof._detect_recurring_pattern()
        assert pattern is not None
        assert "web" in pattern and "debug" in pattern

    def test_general_pairs_excluded(self, isolated_profiler):
        prof, _ = isolated_profiler
        prof.recent_categories = ["general", "general", "general", "general", "general", "general"]
        assert prof._detect_recurring_pattern() is None


class TestProfileForPrompt:
    def test_empty_when_too_few_requests(self, isolated_profiler):
        prof, _ = isolated_profiler
        prof.track_request("python")
        # 1 request < 3 → vide
        assert prof.get_profile_for_prompt() == ""

    def test_format_after_3_requests(self, isolated_profiler):
        prof, _ = isolated_profiler
        prof.track_request("python fastapi")
        prof.track_request("python pytest")
        prof.track_request("python sql migration")
        text = prof.get_profile_for_prompt()
        assert "Profil utilisateur" in text
        assert "Python" in text
        assert "Sessions" in text and "Requêtes" in text


class TestDisplaySummary:
    def test_structure(self, isolated_profiler):
        prof, _ = isolated_profiler
        prof.track_request("docker compose ci/cd")
        prof.track_tool_usage("execute_command")
        summary = prof.get_display_summary()
        assert "sessions" in summary
        assert "requêtes" in summary
        assert "top_techs" in summary
        assert "top_categories" in summary
        assert "top_tools" in summary


class TestPersistRoundtrip:
    def test_save_then_load_preserves_state(self, tmp_path, monkeypatch):
        import agent.profiler as prof_mod
        fake_file = tmp_path / "p.json"
        monkeypatch.setattr(prof_mod, "_PROFILE_FILE", fake_file)
        monkeypatch.setattr(prof_mod, "_instance", None)

        p1 = prof_mod.UserProfiler()
        p1.track_request("fastapi rest api")
        p1.track_tool_usage("write_file")
        p1.increment_session()

        # Re-instancie → doit lire le fichier
        p2 = prof_mod.UserProfiler()
        assert p2.total_requests == 1
        assert p2.session_count == 1
        assert p2.top_skills_used.get("write_file") == 1
        assert "FastAPI" in p2.tech_usage

    def test_corrupted_file_handled_gracefully(self, tmp_path, monkeypatch, caplog):
        import agent.profiler as prof_mod
        fake_file = tmp_path / "p.json"
        fake_file.write_text("not valid json {{{")
        monkeypatch.setattr(prof_mod, "_PROFILE_FILE", fake_file)
        monkeypatch.setattr(prof_mod, "_instance", None)

        # Ne doit pas crasher
        p = prof_mod.UserProfiler()
        assert p.total_requests == 0


class TestSingleton:
    def test_get_profiler_returns_same_instance(self, tmp_path, monkeypatch):
        import agent.profiler as prof_mod
        monkeypatch.setattr(prof_mod, "_PROFILE_FILE", tmp_path / "p.json")
        monkeypatch.setattr(prof_mod, "_instance", None)
        a = prof_mod.get_profiler()
        b = prof_mod.get_profiler()
        assert a is b
