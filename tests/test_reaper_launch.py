"""Tests de `launch_reaper` — démarrage/réveil de REAPER par l'agent.

Répond au point 2 du rapport de bug in-vivo : « Klody ne peut pas lancer REAPER,
il me demande de le faire ». Le piège corrigé ici : un `ping` muet ne prouve PAS
que REAPER est éteint. En arrière-plan macOS throttle les timers de l'app, la
boucle defer du pont ralentit et le ping timeoute alors que REAPER TOURNE
(observé en vivo 23/07 : l'outil rapportait `launched: true` sur une app déjà
lancée). D'où la séparation « process existe » (pgrep) vs « pont répond » (ping).

Aucun REAPER requis : le pont et `subprocess.run` sont monkeypatchés.
"""
from __future__ import annotations

import pytest
from klody_mcp import reaper_server as rs


class _FakeProc:
    def __init__(self, returncode: int = 0, stderr: bytes = b""):
        self.returncode = returncode
        self.stderr = stderr


@pytest.fixture
def wiring(monkeypatch):
    """Câblage : ping scripté, `open -a` enregistré, sleep neutralisé."""
    state = {"pings": [], "open_calls": 0, "running": False, "open_rc": 0}

    async def fake_bridge(cmd, args=None, timeout=None):
        # `pings` = file de booléens « le pont répond-il ? », dernier réutilisé.
        ok = state["pings"].pop(0) if len(state["pings"]) > 1 else state["pings"][0]
        return {"pong": True} if ok else {"error": "pont REAPER injoignable"}

    def fake_run(cmd, **kw):
        if cmd[0] == "pgrep":
            return _FakeProc(returncode=0 if state["running"] else 1)
        state["open_calls"] += 1
        return _FakeProc(returncode=state["open_rc"], stderr=b"boom")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(rs, "_bridge_call", fake_bridge)
    monkeypatch.setattr(rs.subprocess, "run", fake_run)
    monkeypatch.setattr(rs.asyncio, "sleep", no_sleep)
    return state


async def test_bridge_repond_deja_ne_relance_rien(wiring):
    wiring["pings"] = [True]
    wiring["running"] = True
    out = await rs.launch_reaper()
    assert out == {"already_running": True, "launched": False, "bridge_ready": True}
    assert wiring["open_calls"] == 0  # idempotent : aucun `open -a`


async def test_reaper_eteint_est_lance(wiring):
    # ping muet d'abord, puis le pont répond après le lancement.
    wiring["pings"] = [False, True]
    wiring["running"] = False
    out = await rs.launch_reaper()
    assert out["launched"] is True
    assert out["already_running"] is False
    assert out["bridge_ready"] is True
    assert wiring["open_calls"] == 1


async def test_reaper_en_arriere_plan_est_reveille_pas_lance(wiring):
    """Le cas qui mentait : app VIVANTE mais pont muet (defer throttlé)."""
    wiring["pings"] = [False, True]
    wiring["running"] = True  # pgrep le voit
    out = await rs.launch_reaper()
    assert out["already_running"] is True, "REAPER tournait : on ne l'a pas lancé"
    assert out["launched"] is False
    assert out["bridge_ready"] is True
    assert wiring["open_calls"] == 1  # `open -a` sert à le RÉVEILLER


async def test_pont_jamais_reveille_rend_hint_adapte(wiring):
    wiring["pings"] = [False]  # muet pour toujours
    wiring["running"] = True
    out = await rs.launch_reaper(wait=2)
    assert out["bridge_ready"] is False
    assert out["already_running"] is True and out["launched"] is False
    assert "relance l'action du pont" in out["hint"]


async def test_open_en_echec_remonte_une_erreur(wiring):
    wiring["pings"] = [False]
    wiring["running"] = False
    wiring["open_rc"] = 1
    out = await rs.launch_reaper()
    assert "error" in out and "boom" in out["error"]


class TestProcessProbe:
    def test_pgrep_absent_ne_leve_pas(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError
        monkeypatch.setattr(rs.subprocess, "run", boom)
        assert rs._reaper_process_running() is False
