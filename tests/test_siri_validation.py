"""Le chemin Siri (/api/siri) refuse les outils gardés — il n'a aucune surface de validation.

Vécu le 2026-11-19 (spike iMessage) : `_run_siri_query` exécutait tout outil sans
passer par approval.py, la garde n'existant que dans le WebSocket.
"""
import inspect

import api.server as serveur
import httpx
import pytest
from openai import InternalServerError, NotFoundError


def _executeur():
    appels: list[str] = []

    def execute(nom: str, args: dict) -> str:
        appels.append(nom)
        return f"ok:{nom}"

    return serveur._executeur_sans_validation(execute, "Siri"), appels


def test_outil_garde_refuse_et_non_execute():
    executer, appels = _executeur()
    for nom, args in [("write_file", {"path": "x"}), ("run_applescript", {"script": "x"}),
                      ("mcp__gmail__send_email", {}), ("git_control", {"action": "commit"}),
                      ("ask_user", {"question": "?"})]:
        assert executer(nom, args).startswith("Action refusée"), nom
    assert appels == []


def test_lecture_passe():
    executer, appels = _executeur()
    assert executer("read_file", {"path": "x"}) == "ok:read_file"
    assert executer("git_control", {"action": "status"}) == "ok:git_control"
    assert executer("mcp__gmail__read_email", {}) == "ok:mcp__gmail__read_email"
    assert appels == ["read_file", "git_control", "mcp__gmail__read_email"]


def test_le_chemin_siri_utilise_l_enveloppe():
    source = inspect.getsource(serveur._run_siri_query)
    assert "_executeur_sans_validation(orch._execute_tool" in source


# ── Réessai 503 (2026-11-19) ─────────────────────────────────────────────────
# Le chemin Siri remplace `stream_chat` par un appel synchrone : il perdait le
# réessai 503 partagé par la CLI et le WebSocket. Vécu au test live du fix
# ci-dessus : « RAM insuffisante pour coder » rendu au premier refus.

def _http(code: int, exc_cls):
    req = httpx.Request("POST", "http://localhost:8090/v1/chat/completions")
    return exc_cls(f"Error code: {code}", response=httpx.Response(code, request=req),
                   body={"error": "RAM insuffisante pour coder (~40 Go)"})


def _scenario(*etapes):
    appels: list[dict] = []
    reste = list(etapes)

    def creer(**params):
        appels.append(params)
        etape = reste.pop(0)
        if isinstance(etape, BaseException):
            raise etape
        return etape

    return creer, appels


def test_503_rejoue_puis_reussit(monkeypatch):
    monkeypatch.setattr(serveur.config, "LLM_503_ESSAIS", 2)
    monkeypatch.setattr(serveur.config, "LLM_503_ATTENTE_S", 5)
    creer, appels = _scenario(_http(503, InternalServerError), _http(503, InternalServerError), "ok")
    attentes: list[float] = []
    assert serveur._creer_avec_reessai_503(creer, {"model": "m"}, dormir=attentes.append) == "ok"
    assert attentes == [5, 10]
    assert appels == [{"model": "m"}] * 3


def test_503_borne_puis_remonte(monkeypatch):
    monkeypatch.setattr(serveur.config, "LLM_503_ESSAIS", 2)
    monkeypatch.setattr(serveur.config, "LLM_503_ATTENTE_S", 0)
    creer, appels = _scenario(*[_http(503, InternalServerError)] * 3)
    with pytest.raises(InternalServerError):
        serveur._creer_avec_reessai_503(creer, {}, dormir=lambda _s: None)
    assert len(appels) == 3


def test_autre_erreur_pas_rejouee():
    creer, appels = _scenario(_http(404, NotFoundError))
    with pytest.raises(NotFoundError):
        serveur._creer_avec_reessai_503(creer, {}, dormir=lambda _s: pytest.fail("pas d'attente"))
    assert len(appels) == 1


def test_le_chemin_siri_utilise_le_reessai():
    source = inspect.getsource(serveur._run_siri_query)
    assert "_creer_avec_reessai_503(orch.llm.client.chat.completions.create" in source
    assert "orch.llm.client.chat.completions.create(**params)" not in source
