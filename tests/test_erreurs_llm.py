"""agent/erreurs_llm.py — l'utilisateur lit une cause et un remède, jamais le
dict brut du SDK OpenAI.

Vécu le 2026-09-20 dans KlodyAI : bulle rouge
`Error code: 503 - {'error': "RAM insuffisante pour brain (~44 Go) : libre 80/80
Go virtuel, RAM réelle 46 Go (plancher 12), rien d'évinçable de plus"}`.
"""
from types import SimpleNamespace

import httpx
import pytest
from agent.erreurs_llm import detail_http, est_503, expliquer_erreur_llm, resume_exception
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
)

_RAM = (
    "RAM insuffisante pour brain (~44 Go) : libre 80/80 Go virtuel, "
    "RAM réelle 46 Go (plancher 12), rien d'évinçable de plus"
)


def _requete() -> httpx.Request:
    return httpx.Request("POST", "http://localhost:8090/v1/chat/completions")


def erreur_http(statut: int, body, classe=None):
    """Fabrique l'exception que le SDK lèverait pour une réponse `statut`/`body`."""
    classe = classe or {503: InternalServerError, 404: NotFoundError, 403: PermissionDeniedError}[statut]
    reponse = httpx.Response(statut, request=_requete(), json=body if isinstance(body, dict) else None)
    return classe(f"Error code: {statut} - {body!r}", response=reponse, body=body)


def _llm(backend="mlx", model="brain", base_url="http://localhost:8090/v1"):
    return SimpleNamespace(model=model, _backend=backend, _base_url=base_url)


class TestDetailHttp:
    def test_extrait_error_du_corps(self):
        assert detail_http(erreur_http(503, {"error": _RAM})) == _RAM

    def test_extrait_message_imbrique(self):
        exc = erreur_http(404, {"error": {"message": "modèle inconnu : brain"}})
        assert detail_http(exc) == "modèle inconnu : brain"

    def test_corps_texte(self):
        exc = erreur_http(503, "service en cours de démarrage")
        assert detail_http(exc) == "service en cours de démarrage"

    def test_sans_corps_retombe_sur_str(self):
        assert detail_http(ValueError("boum")) == "boum"


class TestResume:
    @pytest.mark.parametrize("exc, attendu", [
        (erreur_http(503, {"error": _RAM}), "HTTP 503"),
        (erreur_http(404, {"error": "inconnu"}), "HTTP 404"),
        (APIConnectionError(request=_requete()), "connexion refusée"),
        (APITimeoutError(request=_requete()), "timeout"),
        (ValueError("x"), "ValueError"),
    ])
    def test_resume_court(self, exc, attendu):
        assert resume_exception(exc) == attendu

    def test_est_503(self):
        assert est_503(erreur_http(503, {"error": _RAM}))
        assert not est_503(erreur_http(404, {"error": "x"}))
        assert not est_503(APIConnectionError(request=_requete()))


class Test503Ram:
    """Le cas vécu : le message nomme la RAM RÉELLE et donne un remède."""

    def test_pas_de_dict_brut(self):
        msg = expliquer_erreur_llm(erreur_http(503, {"error": _RAM}), _llm())
        assert "Error code" not in msg
        assert "{'error'" not in msg

    def test_nomme_modele_cause_et_remede(self):
        msg = expliquer_erreur_llm(erreur_http(503, {"error": _RAM}), _llm())
        assert "« brain »" in msg
        assert "RAM réelle 46 Go" in msg          # le détail du gateway est conservé
        assert "RAM réelle de la machine" in msg  # …et son sens expliqué (budget VIRTUEL ≠ RAM)
        assert "ferme des applications" in msg
        assert "http://localhost:8090/admin/status" in msg

    def test_503_non_ram_reste_transitoire(self):
        msg = expliquer_erreur_llm(erreur_http(503, {"error": "worker en cours de démarrage"}), _llm())
        assert "503" in msg
        assert "worker en cours de démarrage" in msg
        assert "quelques secondes" in msg
        assert "ferme des applications" not in msg


class TestAutresCas:
    def test_404_en_mode_gateway_rappelle_la_regle_des_alias(self):
        exc = erreur_http(404, {"error": "modèle inconnu : unsloth/Qwen3.6"})
        msg = expliquer_erreur_llm(exc, _llm(model="unsloth/Qwen3.6"))
        assert "404" in msg
        assert "ALIAS" in msg
        assert "2026-07-03" in msg

    def test_404_en_mode_ollama_sans_le_conseil_gateway(self):
        exc = erreur_http(404, {"error": "model not found"})
        msg = expliquer_erreur_llm(exc, _llm(backend="ollama", model="mistral:latest"))
        assert "Ollama" in msg
        assert "ALIAS" not in msg

    def test_connexion_nomme_le_backend_vise(self):
        # Incident du 2026-07-30 : « Impossible de joindre Ollama » en mode mlx
        # envoyait l'enquête au mauvais endroit.
        msg = expliquer_erreur_llm(APIConnectionError(request=_requete()), _llm())
        assert "gateway Klody Core" in msg
        assert "Ollama" not in msg
        assert "/admin/status" in msg

    def test_connexion_en_mode_ollama(self):
        msg = expliquer_erreur_llm(APIConnectionError(request=_requete()), _llm(backend="ollama"))
        assert "Ollama" in msg
        assert "ollama serve" in msg

    def test_timeout_avant_connexion(self):
        # APITimeoutError HÉRITE d'APIConnectionError : l'ordre des branches compte.
        msg = expliquer_erreur_llm(APITimeoutError(request=_requete()), _llm())
        assert "délai" in msg
        assert "Impossible de joindre" not in msg

    def test_autre_statut_http(self):
        msg = expliquer_erreur_llm(erreur_http(403, {"error": "interdit"}), _llm())
        assert "403" in msg and "interdit" in msg

    def test_exception_quelconque_garde_son_texte(self):
        assert expliquer_erreur_llm(RuntimeError("cassé"), _llm()) == "cassé"

    def test_exception_sans_texte_donne_son_type(self):
        assert expliquer_erreur_llm(RuntimeError(), _llm()) == "RuntimeError"

    def test_sans_client_lit_la_config(self):
        # `llm=None` (orchestrateur sans LLM construit) : jamais d'exception.
        assert expliquer_erreur_llm(erreur_http(503, {"error": _RAM}), None)
