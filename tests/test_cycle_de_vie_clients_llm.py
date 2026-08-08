"""Cycle de vie des clients OpenAI — non-régression de la fuite mémoire de l'API.

Fuite constatée (audit 2026-08-09) : api/server.py crée un Orchestrator NEUF par
message WebSocket, et chaque Orchestrator instancie des clients OpenAI (pools de
connexions httpx) jamais fermés → ~5-6 Gio/jour de phys_footprint sur
com.klody.api. Les sites : LLMClient (__init__, set_session, switch_to — le
remplacé devenait orphelin), Router, memory_extractor (un client par appel),
greeting.

Le contrat verrouillé ici :
- tout client REMPLACÉ est fermé (set_session, switch_to) ;
- LLMClient.close() / Router.close() ferment le client courant, sans jamais
  lever, même sur un objet partiel ou un faux client sans close() ;
- Orchestrator.close() propage à llm + router et vide le cache sandbox ;
- memory_extractor RÉUTILISE un client partagé au lieu d'en créer un par appel
  — et le reconstruit si sa classe `OpenAI` a été re-patchée (isolation des
  tests existants qui posent une classe fraîche par test via @patch).

Le câblage serveur (try/finally de run_agent) est verrouillé par
tests/integration/test_websocket_chat.py::TestCycleDeVieOrchestrateur.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import agent.memory_extractor as memory_extractor
from agent.llm import LLMClient
from agent.orchestrator import Orchestrator
from agent.router import Router

# --------------------------------------------------------------------------- #
# Faux clients                                                                 #
# --------------------------------------------------------------------------- #


def _faux_openai():
    """Fabrique une CLASSE de faux client OpenAI, fraîche par test.

    Fraîche pour que le registre `crees` ne fuie pas d'un test à l'autre —
    exactement le défaut que cette suite verrouille côté production.
    """

    class Faux:
        crees: ClassVar[list] = []

        def __init__(self, *args, **kwargs):
            self.ferme = False
            self.kwargs = kwargs
            # Forme minimale pour l'extracteur mémoire (réponse non-streaming).
            reponse = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))]
            )
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_p: reponse)
            )
            Faux.crees.append(self)

        def close(self):
            self.ferme = True

    return Faux


class _SansClose:
    """Faux client SANS close() — la forme du FakeOpenAI des tests WS existants.

    Le cycle de vie doit le tolérer : un getattr défensif, jamais d'exception.
    """

    def __init__(self, *args, **kwargs):
        pass


# --------------------------------------------------------------------------- #
# LLMClient                                                                    #
# --------------------------------------------------------------------------- #


class TestLLMClientFermeture:
    def test_set_session_ferme_le_client_remplace(self, monkeypatch):
        Faux = _faux_openai()
        monkeypatch.setattr("agent.llm.OpenAI", Faux)
        llm = LLMClient()
        llm.set_session("session-1")
        assert len(Faux.crees) == 2
        assert Faux.crees[0].ferme, "le client remplacé doit être fermé"
        assert not Faux.crees[1].ferme, "le client courant doit rester ouvert"
        assert llm.client is Faux.crees[1]

    def test_set_session_meme_id_ne_recree_rien(self, monkeypatch):
        Faux = _faux_openai()
        monkeypatch.setattr("agent.llm.OpenAI", Faux)
        llm = LLMClient()
        llm.set_session("s")
        llm.set_session("s")  # no-op
        assert len(Faux.crees) == 2

    def test_switch_to_ferme_le_client_remplace(self, monkeypatch):
        Faux = _faux_openai()
        monkeypatch.setattr("agent.llm.OpenAI", Faux)
        llm = LLMClient()
        llm.switch_to("autre-modele", "http://localhost:9/v1", "cle")
        assert Faux.crees[0].ferme, "switch_to laissait l'ancien client orphelin"
        assert llm.client is Faux.crees[-1]
        assert not llm.client.ferme

    def test_switch_to_meme_modele_est_un_noop(self, monkeypatch):
        Faux = _faux_openai()
        monkeypatch.setattr("agent.llm.OpenAI", Faux)
        llm = LLMClient()
        llm.switch_to(llm.model, "http://localhost:9/v1", "cle")
        assert len(Faux.crees) == 1
        assert not Faux.crees[0].ferme

    def test_close_ferme_le_courant_et_reste_idempotent(self, monkeypatch):
        Faux = _faux_openai()
        monkeypatch.setattr("agent.llm.OpenAI", Faux)
        llm = LLMClient()
        llm.close()
        assert llm.client.ferme
        llm.close()  # une seconde fermeture ne doit pas lever

    def test_close_sur_client_partiel_via_new_ne_leve_pas(self):
        # Contrat existant : les tests construisent des LLMClient partiels via
        # __new__ (cf. commentaire de _make_client) — close() doit le tolérer.
        llm = LLMClient.__new__(LLMClient)
        llm.close()

    def test_un_client_sans_close_est_tolere(self, monkeypatch):
        # Le FakeOpenAI du harnais WS n'a pas de close() : set_session/close
        # doivent rester des no-ops silencieux, pas des AttributeError.
        monkeypatch.setattr("agent.llm.OpenAI", _SansClose)
        llm = LLMClient()
        llm.set_session("s")
        llm.switch_to("autre", "http://localhost:9/v1", "cle")
        llm.close()


# --------------------------------------------------------------------------- #
# Router                                                                       #
# --------------------------------------------------------------------------- #


class TestRouterFermeture:
    def test_close_ferme_le_client(self, monkeypatch):
        Faux = _faux_openai()
        monkeypatch.setattr("agent.router.OpenAI", Faux)
        routeur = Router()
        routeur.close()
        assert Faux.crees[0].ferme

    def test_close_sans_close_sur_le_client_ne_leve_pas(self, monkeypatch):
        monkeypatch.setattr("agent.router.OpenAI", _SansClose)
        Router().close()


# --------------------------------------------------------------------------- #
# Orchestrator.close — construit via __new__ (l'__init__ complet découvre les   #
# serveurs MCP et charge les outils, hors de propos ici)                        #
# --------------------------------------------------------------------------- #


class _Fermable:
    def __init__(self):
        self.ferme = False

    def close(self):
        self.ferme = True


class TestOrchestratorClose:
    def _orchestrateur_partiel(self):
        orch = Orchestrator.__new__(Orchestrator)
        orch.llm = _Fermable()
        orch._router = _Fermable()
        orch._sandbox_cache = {"racine": object()}
        return orch

    def test_propage_a_llm_router_et_vide_le_cache_sandbox(self):
        orch = self._orchestrateur_partiel()
        orch.close()
        assert orch.llm.ferme
        assert orch._router.ferme
        assert orch._sandbox_cache == {}

    def test_router_jamais_initialise_reste_un_noop(self):
        # Le Router est lazy : close() ne doit ni le créer ni lever sur None.
        orch = Orchestrator.__new__(Orchestrator)
        orch.llm = _Fermable()
        orch._router = None
        orch._sandbox_cache = {}
        orch.close()
        assert orch.llm.ferme
        assert orch._router is None

    def test_orchestrateur_nu_ne_leve_pas(self):
        # Aucun attribut posé : close() est un nettoyage, jamais une panne.
        Orchestrator.__new__(Orchestrator).close()

    def test_double_close_idempotent(self):
        orch = self._orchestrateur_partiel()
        orch.close()
        orch.close()
        assert orch.llm.ferme


# --------------------------------------------------------------------------- #
# memory_extractor — client partagé                                            #
# --------------------------------------------------------------------------- #


def _messages(n_user: int = 3) -> list[dict]:
    msgs: list[dict] = []
    for i in range(n_user):
        msgs.append({"role": "user", "content": f"Question {i}"})
        msgs.append({"role": "assistant", "content": f"Réponse {i}"})
    return msgs


class TestExtracteurClientPartage:
    def _isoler(self, monkeypatch):
        """Repart d'un cache module vierge (restauré en sortie par monkeypatch)."""
        monkeypatch.setattr(memory_extractor, "_client_partage", None)
        monkeypatch.setattr(memory_extractor, "_classe_du_client", None)

    def test_deux_appels_reutilisent_le_meme_client(self, monkeypatch):
        self._isoler(monkeypatch)
        Faux = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", Faux)
        lt = SimpleNamespace(remember=lambda *a, **k: "ok")

        memory_extractor.extract_and_save(_messages(3), lt)
        memory_extractor.extract_and_save(_messages(3), lt)

        assert len(Faux.crees) == 1, (
            "un client par appel = la fuite d'origine (audit 2026-08-09)"
        )
        assert not Faux.crees[0].ferme, "le client partagé vit avec le process"

    def test_classe_repatchee_reconstruit_le_client(self, monkeypatch):
        """Verrouille l'isolation des tests existants : @patch pose une classe
        fraîche par test — le cache ne doit pas servir le client du test
        précédent (il pointerait sur les mocks d'un autre test, voire sur le
        vrai réseau)."""
        self._isoler(monkeypatch)
        lt = SimpleNamespace(remember=lambda *a, **k: "ok")

        FauxA = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", FauxA)
        memory_extractor.extract_and_save(_messages(3), lt)

        FauxB = _faux_openai()
        monkeypatch.setattr(memory_extractor, "OpenAI", FauxB)
        memory_extractor.extract_and_save(_messages(3), lt)

        assert len(FauxA.crees) == 1
        assert len(FauxB.crees) == 1, "la classe a changé → client reconstruit"


# --------------------------------------------------------------------------- #
# mcp_bridge — cache de découverte borné                                       #
# --------------------------------------------------------------------------- #


class TestCacheDecouverteBorne:
    def test_le_cache_evince_le_plus_ancien(self, monkeypatch):
        import tools.mcp_bridge as mb

        # Pas de réseau : la découverte « réussit » avec zéro outil, ce qui
        # suffit à alimenter le cache. On ferme la coroutine ignorée pour ne
        # pas déclencher « coroutine was never awaited ».
        def _sans_reseau(coro):
            coro.close()
            return []

        monkeypatch.setattr(mb, "_run_async", _sans_reseau)
        mb.clear_discovery_cache()
        try:
            total = mb._DISCOVERY_CACHE_MAX + 5
            for i in range(total):
                mb.MCPManager({f"serveur{i}": f"http://127.0.0.1:1/{i}"}).discover()

            assert len(mb._DISCOVERY_CACHE) == mb._DISCOVERY_CACHE_MAX
            premier = mb._signature({"serveur0": "http://127.0.0.1:1/0"})
            dernier = mb._signature(
                {f"serveur{total - 1}": f"http://127.0.0.1:1/{total - 1}"}
            )
            assert premier not in mb._DISCOVERY_CACHE, "le plus ancien est évincé"
            assert dernier in mb._DISCOVERY_CACHE, "le plus récent est conservé"
        finally:
            mb.clear_discovery_cache()
