"""Tests pour tools/mac_control.py — pilotage macOS (garde plateforme + sûreté)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from tools import mac_control
from tools.mac_control import (
    MacControlError,
    _check_applescript_safety,
    list_shortcuts,
    reveal_in_finder,
    run_applescript,
    run_shortcut,
    spotlight_search,
)


@pytest.fixture
def on_mac(monkeypatch):
    """Simule macOS pour exercer la vraie logique (les binaires sont mockés)."""
    monkeypatch.setattr(mac_control, "is_macos", lambda: True)


# ── garde plateforme (hors macOS) ────────────────────────────────────────────

class TestPlatformGuard:
    def test_all_tools_guard_off_mac(self, monkeypatch):
        monkeypatch.setattr(mac_control, "is_macos", lambda: False)
        assert "macOS" in run_applescript('tell app "Music" to play')
        assert "macOS" in spotlight_search("x")
        assert "macOS" in list_shortcuts()
        assert "macOS" in run_shortcut("Bonne nuit")
        assert "macOS" in reveal_in_finder(".")


# ── blocklist AppleScript ────────────────────────────────────────────────────

class TestAppleScriptSafety:
    @pytest.mark.parametrize("script", [
        'tell application "Finder" to delete every file',
        "do shell script \"rm -rf /\"",
        "tell app \"System Events\" to keystroke \"x\"",
        "empty trash",
        "shut down",
    ])
    def test_blocked(self, script):
        with pytest.raises(MacControlError):
            _check_applescript_safety(script)

    def test_allowed(self):
        _check_applescript_safety('tell application "Music" to play')  # ne lève pas

    def test_run_blocked_returns_security_error(self, on_mac):
        assert "SÉCURITÉ" in run_applescript("empty trash")

    def test_empty_script(self, on_mac):
        assert "vide" in run_applescript("   ")


# ── exécution (binaires mockés) ──────────────────────────────────────────────

class TestExecution:
    @patch("subprocess.run")
    def test_applescript_calls_osascript(self, mock_run, on_mac):
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = run_applescript('tell application "Music" to play')
        assert "ok" in result
        argv = mock_run.call_args[0][0]
        assert argv[0] == "osascript" and argv[1] == "-e"

    @patch("subprocess.run")
    def test_spotlight_parses_results(self, mock_run, on_mac):
        mock_run.return_value = MagicMock(
            stdout="/Users/x/a.pdf\n/Users/x/b.pdf\n", stderr="", returncode=0)
        result = spotlight_search("*.pdf", limit=1)
        assert "2 résultat" in result
        assert "a.pdf" in result and "b.pdf" not in result   # limité à 1

    @patch("subprocess.run")
    def test_spotlight_no_result(self, mock_run, on_mac):
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        assert "Aucun résultat" in spotlight_search("zzz")

    @patch("subprocess.run")
    def test_run_shortcut_ok(self, mock_run, on_mac):
        mock_run.return_value = MagicMock(stdout="done", stderr="", returncode=0)
        result = run_shortcut("Bonne nuit")
        assert "exécuté" in result
        argv = mock_run.call_args[0][0]
        assert argv[:2] == ["shortcuts", "run"]

    @patch("subprocess.run")
    def test_run_shortcut_failure(self, mock_run, on_mac):
        mock_run.return_value = MagicMock(stdout="", stderr="not found", returncode=1)
        assert "ERREUR" in run_shortcut("Inexistant")

    @patch("subprocess.run")
    def test_list_shortcuts(self, mock_run, on_mac):
        mock_run.return_value = MagicMock(stdout="Scene A\nScene B\n", stderr="", returncode=0)
        result = list_shortcuts()
        assert "2 raccourci" in result

    def test_reveal_outside_sandbox(self, on_mac, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert "SÉCURITÉ" in reveal_in_finder("/etc/hosts")

    @patch("subprocess.run")
    def test_reveal_ok(self, mock_run, on_mac, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        f = tmp_path / "file.txt"
        f.write_text("x")
        result = reveal_in_finder(str(f))
        assert "Finder" in result
