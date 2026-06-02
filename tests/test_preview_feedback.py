"""Boucle de feedback preview — tranche 2 : correction auto orchestrée.

Cible les helpers (extraction d'URL, message correctif) et la logique
d'attente/relance de l'orchestrateur, sans LLM réel (méthodes via __new__).
Le délai est monkeypatché sur le binding `agent.orchestrator.PREVIEW_FEEDBACK_TIMEOUT_S`.
"""
from types import SimpleNamespace

import agent.orchestrator as orch
from agent import preview_errors
from agent.orchestrator import Orchestrator, _extract_preview_url, _preview_fix_nudge

PREVIEW_RESULT = (
    "Aperçu créé avec succès !\n"
    "  Fichier : /x/_preview/demo.html\n"
    "  URL     : http://localhost:8899/demo.html\n"
    "Le navigateur s'ouvre automatiquement."
)


class TestExtractUrl:
    def test_extrait_url(self):
        assert _extract_preview_url(PREVIEW_RESULT) == "http://localhost:8899/demo.html"

    def test_aucune_url(self):
        assert _extract_preview_url("pas d'url ici") is None
        assert _extract_preview_url("") is None


class TestNudge:
    def test_message_correctif(self):
        errs = [SimpleNamespace(label="Error", msg="Cannot read 'toString'", src="demo.html:572:37")]
        msg = _preview_fix_nudge("http://localhost:8899/demo.html", errs, 1)
        assert "demo.html" in msg
        assert "tentative 1" in msg
        assert "Cannot read 'toString'" in msg
        assert "demo.html:572:37" in msg
        assert "preview_code" in msg


def _bare_orch() -> Orchestrator:
    o = Orchestrator.__new__(Orchestrator)
    o.memory = SimpleNamespace(messages=[])
    return o


URL = "http://localhost:8899/demo.html"


class TestAwaitErrors:
    def setup_method(self):
        preview_errors.clear()

    def test_retourne_les_erreurs(self, monkeypatch):
        monkeypatch.setattr(orch, "PREVIEW_FEEDBACK_TIMEOUT_S", 1.0)
        preview_errors.record(URL, [{"label": "Error", "msg": "boom", "src": "demo.html:1:1"}], now=100.0)
        errs = _bare_orch()._await_preview_errors(URL, since=0.0)
        assert len(errs) == 1
        assert errs[0].msg == "boom"

    def test_chargement_propre_retourne_vide(self, monkeypatch):
        monkeypatch.setattr(orch, "PREVIEW_FEEDBACK_TIMEOUT_S", 5.0)
        preview_errors.mark_loaded(URL, now=100.0)
        # Doit conclure tôt grâce au ping « ok » (pas d'attente des 5 s).
        assert _bare_orch()._await_preview_errors(URL, since=0.0) == []

    def test_timeout_retourne_vide(self, monkeypatch):
        monkeypatch.setattr(orch, "PREVIEW_FEEDBACK_TIMEOUT_S", 0.3)
        assert _bare_orch()._await_preview_errors(URL, since=0.0) == []

    def test_desactive_si_timeout_zero(self, monkeypatch):
        monkeypatch.setattr(orch, "PREVIEW_FEEDBACK_TIMEOUT_S", 0.0)
        preview_errors.record(URL, [{"msg": "boom"}], now=100.0)
        assert _bare_orch()._await_preview_errors(URL, since=0.0) == []

    def test_filtre_par_since(self, monkeypatch):
        monkeypatch.setattr(orch, "PREVIEW_FEEDBACK_TIMEOUT_S", 0.3)
        preview_errors.record(URL, [{"msg": "vieux"}], now=10.0)
        # since postérieur → la vieille erreur est ignorée.
        assert _bare_orch()._await_preview_errors(URL, since=50.0) == []


class TestCheckFeedback:
    def setup_method(self):
        preview_errors.clear()

    def test_injecte_nudge_si_erreurs(self, monkeypatch):
        monkeypatch.setattr(orch, "PREVIEW_FEEDBACK_TIMEOUT_S", 1.0)
        preview_errors.record(URL, [{"label": "Error", "msg": "boom", "src": "demo.html:1:1"}], now=100.0)
        o = _bare_orch()
        o._preview_fix_attempts = 0
        o._check_preview_feedback(URL, since=0.0)
        assert o._preview_fix_attempts == 1
        assert len(o.memory.messages) == 1
        assert o.memory.messages[0]["role"] == "user"
        assert "erreur" in o.memory.messages[0]["content"].lower()

    def test_pas_de_nudge_si_aucune_erreur(self, monkeypatch):
        monkeypatch.setattr(orch, "PREVIEW_FEEDBACK_TIMEOUT_S", 0.3)
        o = _bare_orch()
        o._preview_fix_attempts = 0
        o._check_preview_feedback(URL, since=0.0)
        assert o._preview_fix_attempts == 0
        assert o.memory.messages == []

    def test_plafond_atteint_pas_de_nudge(self, monkeypatch):
        monkeypatch.setattr(orch, "PREVIEW_FEEDBACK_TIMEOUT_S", 1.0)
        preview_errors.record(URL, [{"msg": "boom"}], now=100.0)
        o = _bare_orch()
        o._preview_fix_attempts = 2  # _MAX_PREVIEW_FIX atteint
        o._check_preview_feedback(URL, since=0.0)
        assert o._preview_fix_attempts == 2
        assert o.memory.messages == []

    def test_url_none_no_op(self, monkeypatch):
        monkeypatch.setattr(orch, "PREVIEW_FEEDBACK_TIMEOUT_S", 1.0)
        o = _bare_orch()
        o._preview_fix_attempts = 0
        o._check_preview_feedback(None, since=0.0)
        assert o._preview_fix_attempts == 0
        assert o.memory.messages == []
