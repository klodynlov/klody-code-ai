"""Tests du watchdog LibraryBrain (services.py).

Régression : avant le correctif, une fois le budget de redémarrages épuisé, la
branche « abandon » se ré-exécutait à chaque cycle de 15s et noyait agent.log
(~300 lignes par session morte). Le watchdog doit désormais signaler l'abandon
UNE seule fois, puis se réarmer si le service redevient joignable.
"""
from __future__ import annotations

import logging
import subprocess

import services


def _drive_watchdog(monkeypatch, *, reachable_seq, cycles):
    """Pilote `_watchdog` sur un nombre fixe de cycles.

    reachable_seq : callable(i) -> bool, l'état « joignable » au cycle i.
    Renvoie la liste des messages loggés au niveau >= WARNING par `services`.
    """
    state = {"i": -1}

    def fake_is_up(*_a, **_k):
        return reachable_seq(state["i"])

    class FakeProc:
        returncode = 1

        def poll(self):
            return 1  # process mort → déclenche un redémarrage

    monkeypatch.setattr(services, "_is_up", fake_is_up)
    monkeypatch.setattr(services, "_start_process", lambda *_a, **_k: FakeProc())
    monkeypatch.setattr(services.time, "sleep", lambda *_a, **_k: None)

    services._librarybrain_dir = "/tmp"
    services._librarybrain_base_url = "http://127.0.0.1:8765"
    services._librarybrain_status.update({"up": False, "pid": None, "restarts": 0})

    stop = services._watchdog_stop
    stop.clear()

    def fake_wait(_timeout):
        state["i"] += 1
        if state["i"] >= cycles:
            stop.set()
        return stop.is_set()

    monkeypatch.setattr(stop, "wait", fake_wait)
    return state


def test_abandon_logge_une_seule_fois(monkeypatch, caplog):
    """Service toujours injoignable : après _MAX_RESTARTS tentatives, l'abandon
    n'est loggé qu'une fois même sur de nombreux cycles."""
    _drive_watchdog(monkeypatch, reachable_seq=lambda i: False, cycles=20)

    with caplog.at_level(logging.ERROR, logger="services"):
        services._watchdog()

    abandons = [r for r in caplog.records if "abandon" in r.getMessage().lower()]
    assert len(abandons) == 1, (
        f"l'abandon doit être loggé exactement une fois (vu {len(abandons)} "
        "sur 20 cycles — régression du spam)"
    )
    # Budget épuisé après les tentatives autorisées.
    assert services._librarybrain_status["restarts"] == services._MAX_RESTARTS


def test_rearmement_quand_le_service_revient(monkeypatch, caplog):
    """Injoignable au début (→ abandon), puis joignable : le watchdog réarme
    le budget et peut de nouveau abandonner si ça retombe."""
    # Injoignable cycles 0..9 (abandon), joignable 10..19 (réarmement).
    _drive_watchdog(monkeypatch, reachable_seq=lambda i: i >= 10, cycles=20)

    with caplog.at_level(logging.INFO, logger="services"):
        services._watchdog()

    msgs = [r.getMessage() for r in caplog.records]
    assert any("abandon" in m.lower() for m in msgs)
    assert any("réarmée" in m for m in msgs), "doit signaler le réarmement"
    # Réarmé → budget remis à zéro.
    assert services._librarybrain_status["restarts"] == 0


def test_start_process_capture_stderr_pas_devnull(monkeypatch, tmp_path):
    """Régression : LibraryBrain doit écrire stdout/stderr dans un fichier, pas
    dans DEVNULL — sinon les morts code=1 (déclenchées par requête) restent
    indiagnostiquables. On vérifie qu'aucun flux ne pointe vers DEVNULL et que
    le marqueur de démarrage atterrit bien dans le fichier."""
    log_file = tmp_path / "librarybrain.log"
    monkeypatch.setattr(services, "_LB_LOG", log_file)

    captured: dict = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(services.subprocess, "Popen", fake_popen)

    proc = services._start_process(tmp_path)

    assert proc is not None
    assert captured["stderr"] is not subprocess.DEVNULL, "stderr ne doit plus être /dev/null"
    assert captured["stdout"] is not subprocess.DEVNULL, "stdout ne doit plus être /dev/null"
    assert log_file.exists()
    assert "démarrage" in log_file.read_text(), "le marqueur de démarrage doit être écrit"
