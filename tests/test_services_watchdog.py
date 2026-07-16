"""Tests de la sonde et du watchdog LibraryBrain (services.py).

Deux régressions couvertes ici :

1. Spam de logs : une fois le budget de redémarrages épuisé, la branche
   « abandon » se ré-exécutait à chaque cycle de 15s et noyait agent.log
   (~300 lignes par session morte). L'abandon doit être signalé UNE seule fois,
   puis se réarmer si le service redevient joignable.

2. Point vert menteur : la sonde renvoyait `r.status_code < 500` sur
   `/api/stats`, une route AUTHENTIFIÉE. Avec `api_token` posé dans le
   config.yaml de LibraryBrain, elle répond 401 → `401 < 500` → « vivant »,
   alors que 100 % des appels /api/ de Klody échouaient. Le statut doit
   distinguer trois états (up / unauthorized / down).
"""
from __future__ import annotations

import logging
import subprocess

import pytest
import services


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _fake_httpx_get(status_code: int):
    return lambda *_a, **_k: _FakeResponse(status_code)


@pytest.fixture(autouse=True)
def _reset_services_globals():
    """`services` est un module singleton : ses globals fuient d'un test à
    l'autre (un FakeProc laissé par un test fait échouer le suivant sur `.pid`).
    On repart d'un état neutre à chaque test."""
    services._librarybrain_proc = None
    services._externally_managed = False
    services._librarybrain_status.update(
        {"state": services.PROBE_DOWN, "pid": None, "restarts": 0}
    )
    yield
    services._librarybrain_proc = None


def _drive_watchdog(monkeypatch, *, probe_seq, cycles):
    """Pilote `_watchdog` sur un nombre fixe de cycles.

    probe_seq : callable(i) -> PROBE_UP / PROBE_UNAUTHORIZED / PROBE_DOWN, le
    verdict de la sonde au cycle i.
    """
    state = {"i": -1}

    def fake_probe(*_a, **_k):
        return probe_seq(state["i"])

    class FakeProc:
        returncode = 1

        def poll(self):
            return 1  # process mort → déclenche un redémarrage

    monkeypatch.setattr(services, "_probe", fake_probe)
    monkeypatch.setattr(services, "_start_process", lambda *_a, **_k: FakeProc())
    monkeypatch.setattr(services.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(services, "_externally_managed", False)  # cas « Klody gère »

    services._librarybrain_dir = "/tmp"
    services._librarybrain_base_url = "http://127.0.0.1:8765"
    services._librarybrain_status.update({"state": services.PROBE_DOWN, "pid": None, "restarts": 0})

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
    _drive_watchdog(monkeypatch, probe_seq=lambda i: services.PROBE_DOWN, cycles=20)

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
    _drive_watchdog(
        monkeypatch,
        probe_seq=lambda i: services.PROBE_UP if i >= 10 else services.PROBE_DOWN,
        cycles=20,
    )

    with caplog.at_level(logging.INFO, logger="services"):
        services._watchdog()

    msgs = [r.getMessage() for r in caplog.records]
    assert any("abandon" in m.lower() for m in msgs)
    assert any("réarmée" in m for m in msgs), "doit signaler le réarmement"
    # Réarmé → budget remis à zéro.
    assert services._librarybrain_status["restarts"] == 0


def test_externe_jamais_de_spawn(monkeypatch, caplog):
    """LibraryBrain géré en externe (launchd) + injoignable : le watchdog ne
    spawne JAMAIS de doublon. Régression des 84 fausses morts code=1 : Klody
    lançait un uvicorn concurrent sur :8765 déjà pris → `[Errno 48]` en boucle."""
    state = {"i": -1}
    spawn_calls: list = []

    monkeypatch.setattr(services, "_probe", lambda *_a, **_k: services.PROBE_DOWN)
    monkeypatch.setattr(services, "_start_process",
                        lambda *_a, **_k: spawn_calls.append(1))  # espion
    monkeypatch.setattr(services.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(services, "_externally_managed", True)

    services._librarybrain_proc = None
    services._librarybrain_dir = "/tmp"
    services._librarybrain_base_url = "http://127.0.0.1:8765"
    services._librarybrain_status.update({"state": services.PROBE_DOWN, "pid": None, "restarts": 0})

    stop = services._watchdog_stop
    stop.clear()

    def fake_wait(_timeout):
        state["i"] += 1
        if state["i"] >= 10:
            stop.set()
        return stop.is_set()

    monkeypatch.setattr(stop, "wait", fake_wait)

    with caplog.at_level(logging.WARNING, logger="services"):
        services._watchdog()

    assert spawn_calls == [], "ne doit JAMAIS spawner quand LibraryBrain est géré en externe"
    assert services._librarybrain_status["restarts"] == 0
    externe = [r for r in caplog.records if "externe" in r.getMessage().lower()]
    assert len(externe) == 1, "la gestion externe doit être signalée une seule fois (pas de spam)"


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


# ── Sonde : « joignable » n'est pas « exploitable » ──────────────────────────


def test_sonde_401_nest_pas_up(monkeypatch):
    """RÉGRESSION CAPITALE : un 401 ne doit JAMAIS ressortir en « up ».

    `/api/stats` est authentifiée côté LibraryBrain (api/auth.py). Dès que
    `api_token` est posé dans son config.yaml — p.ex. à une exposition Tailscale
    — elle renvoie 401, et l'ancienne sonde (`return r.status_code < 500`)
    comptait ce 401 comme VIVANT : point vert dans l'UI, « ✓ en ligne » dans
    /status, alors qu'aucun appel de Klody n'aboutissait. La commande de
    diagnostic confirmait le faux positif au lieu de le lever.
    """
    monkeypatch.setattr(services.httpx, "get", _fake_httpx_get(401))

    services._librarybrain_status["state"] = services._probe("http://127.0.0.1:8765")
    status = services.get_librarybrain_status()

    assert status["up"] is False, "un 401 ne doit jamais être publié comme « up »"
    assert status["state"] == services.PROBE_UNAUTHORIZED, "ni « up », ni « down »"
    assert "api_token" in status["detail"], (
        "une panne d'auth doit se lire comme une panne d'auth : le détail doit "
        "nommer api_token"
    )


@pytest.mark.parametrize(
    ("code", "attendu"),
    [
        (200, services.PROBE_UP),
        (401, services.PROBE_UNAUTHORIZED),  # auth_middleware, api_token défini
        (403, services.PROBE_UNAUTHORIZED),
        (404, services.PROBE_DOWN),  # route disparue → inexploitable, pas « vivant »
        (500, services.PROBE_DOWN),
        (503, services.PROBE_DOWN),
    ],
)
def test_sonde_verdict_par_code(monkeypatch, code, attendu):
    """Le 200 est STRICT. L'ancien `status_code < 500` acceptait aussi les 4xx —
    il aurait donc aussi avalé un 404 sur une route supprimée."""
    monkeypatch.setattr(services.httpx, "get", _fake_httpx_get(code))
    assert services._probe("http://127.0.0.1:8765") == attendu


def test_sonde_erreur_reseau_est_down(monkeypatch):
    """Port fermé / timeout → down (le seul cas où un redémarrage a du sens)."""
    def _boom(*_a, **_k):
        raise services.httpx.ConnectError("connection refused")

    monkeypatch.setattr(services.httpx, "get", _boom)
    assert services._probe("http://127.0.0.1:8765") == services.PROBE_DOWN


def test_watchdog_401_ne_redemarre_pas(monkeypatch, caplog):
    """401 = panne de CONFIG, pas de process : LibraryBrain tourne et tient
    :8765. Le redémarrer ne réparerait rien, et spawner un doublon sur un port
    déjà pris rejouerait les 84 fausses morts `[Errno 48]`. On signale une fois
    (pas de spam), on ne relance jamais, et le budget reste intact."""
    spawn_calls: list = []
    _drive_watchdog(
        monkeypatch, probe_seq=lambda i: services.PROBE_UNAUTHORIZED, cycles=20
    )
    monkeypatch.setattr(services, "_start_process", lambda *_a, **_k: spawn_calls.append(1))

    with caplog.at_level(logging.ERROR, logger="services"):
        services._watchdog()

    assert spawn_calls == [], "un 401 ne doit JAMAIS déclencher de redémarrage"
    assert services._librarybrain_status["restarts"] == 0, "budget non entamé"
    assert services.get_librarybrain_status()["up"] is False
    auth = [r for r in caplog.records if "api_token" in r.getMessage()]
    assert len(auth) == 1, "le refus d'auth doit être signalé une seule fois"


def test_watchdog_rearme_quand_lauth_est_reparee(monkeypatch, caplog):
    """401 puis 200 (token vidé, ou X-API-Token câblé) : le statut doit repasser
    à « up » — sinon Klody resterait faussement en panne après réparation."""
    _drive_watchdog(
        monkeypatch,
        probe_seq=lambda i: services.PROBE_UP if i >= 5 else services.PROBE_UNAUTHORIZED,
        cycles=10,
    )

    with caplog.at_level(logging.INFO, logger="services"):
        services._watchdog()

    assert services.get_librarybrain_status()["up"] is True
    assert any("réarmée" in r.getMessage() for r in caplog.records)


def test_ensure_401_nannonce_pas_actif_et_ne_spawne_pas(monkeypatch):
    """Au boot avec un 401 : le port a bien un propriétaire externe (donc jamais
    de doublon), mais le service n'est pas exploitable → ensure renvoie False au
    lieu du « ✓ LibraryBrain déjà actif » qui ouvrait toute la cascade de faux
    positifs (status up → watchdog muet → /status vert)."""
    spawn_calls: list = []
    monkeypatch.setattr(services.httpx, "get", _fake_httpx_get(401))
    monkeypatch.setattr(services, "_start_process", lambda *_a, **_k: spawn_calls.append(1))
    monkeypatch.setattr(services, "_launch_watchdog", lambda: None)
    monkeypatch.setattr(services.time, "sleep", lambda *_a, **_k: None)

    ok = services.ensure_librarybrain("/tmp", "http://127.0.0.1:8765/api/ask")

    assert ok is False, "401 = LibraryBrain refuse Klody : ne pas annoncer « actif »"
    assert spawn_calls == [], "le port :8765 a un propriétaire → jamais de doublon"
    assert services._externally_managed is True
    status = services.get_librarybrain_status()
    assert status["up"] is False
    assert status["state"] == services.PROBE_UNAUTHORIZED
