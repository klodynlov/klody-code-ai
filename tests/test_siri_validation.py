"""Le chemin Siri (/api/siri) refuse les outils gardés — il n'a aucune surface de validation.

Vécu le 2026-11-19 (spike iMessage) : `_run_siri_query` exécutait tout outil sans
passer par approval.py, la garde n'existant que dans le WebSocket.
"""
import inspect

import api.server as serveur


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
