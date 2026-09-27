"""Fixtures pytest pour les tests d'intégration replay.

Wire un Orchestrator dont le LLM/Router sont stubés. Test = scénario figé.
"""
from __future__ import annotations

import errno
import functools
import json
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

# Partagés avec le garde de SUITE (tests/conftest.py) : le périmètre nommé ici
# et le double de httpx servent aussi hors de ce dossier.
from tests.garde_reseau import HttpxSansReseau, services_de_la_machine

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_preview_bind(monkeypatch):
    """Empêche le lifespan FastAPI de binder le VRAI port 8899 pendant les tests
    d'intégration : TestClient(app) déclenche le pré-démarrage du serveur d'aperçu.
    On le neutralise (no-op) pour éviter un port réel ouvert / un flake / une
    attente de retry si 8899 est déjà pris."""
    monkeypatch.setattr("tools.preview._ensure_server", lambda: "http://localhost:8899")
    monkeypatch.setattr("tools.preview._stop_server", lambda: None)
    # Et ne jamais OUVRIR le navigateur de la machine : le scénario #18 rejoue
    # 5 preview_code d'affilée (5 onglets sinon).
    monkeypatch.setattr(
        "tools.preview.webbrowser",
        type("W", (), {"open": staticmethod(lambda url: None)}),
    )


@pytest.fixture(autouse=True)
def _no_live_retrieval(monkeypatch):
    """Coupe le retrieval proactif (recherche sémantique Ollama/bge-m3) pour TOUS
    les tests d'intégration. Sinon `_relevant_files_section` fait un appel réseau
    live vers Ollama :11434 : non-hermétique (le résultat dépend de l'humeur
    d'Ollama) et surtout BLOQUANT — sur un projet réel l'index s'embedde par lots
    de 16 avec un timeout de 60 s/lot, soit ~18 min si bge-m3 est froid/lent. Le
    retrieval est best-effort et n'est pas le sujet de ces tests ; ses tests dédiés
    (tests/test_retrieval_inject.py) stubent l'index. Défaut prod = activé."""
    monkeypatch.setattr("agent.orchestrator.RETRIEVAL_INJECT_ENABLED", False)


@pytest.fixture(autouse=True)
def _no_preview_feedback_wait(monkeypatch):
    """Ramène l'attente du retour d'erreurs de preview à son défaut hors live (0).

    `PREVIEW_FEEDBACK_TIMEOUT_S` vaut 0 dans `config.py`, mais le `.env` du
    checkout principal le pose à 6,0 — et un worktree en hérite, `load_dotenv()`
    remontant l'arborescence jusqu'à lui. Chaque `preview_code` attendait alors
    6 s un beacon que le navigateur, bouchonné par `_no_preview_bind`, ne peut
    PAS envoyer : mesuré le 2026-09-27, rejeu #18 (5 previews) à 30,6 s et #06
    (1 preview) à 6,1 s, dans les deux passes — sur la machine de dev seulement,
    la CI n'ayant pas de `.env` : deux chemins de code selon la machine.
    La boucle de feedback a ses tests dédiés (`tests/test_preview_feedback.py`,
    `test_preview_feedback_loop.py`), qui posent leur propre délai après celui-ci.
    """
    monkeypatch.setattr("agent.orchestrator.PREVIEW_FEEDBACK_TIMEOUT_S", 0.0)


class LibraryBrainBouchon:
    """Réponses déterministes des deux ponts LibraryBrain, et journal des appels.

    Le catalogue rend TOUJOURS un miss, formaté par le vrai `_catalog_miss` : c'est
    la condition du scénario #21 (le modèle interroge le catalogue, ne trouve rien,
    conclut « aucune source »). La recherche de contenu rend un passage sourcé,
    formaté par le vrai `_parse_result`. Le texte vient donc du code de production ;
    seules la base et le serveur sont remplacés.

    `via` distingue l'appel d'OUTIL (nom importé par l'orchestrateur) de l'appel
    INTERNE au module (`learn_from_books` appelle `search_books`) : seul le premier
    correspond à un tool_call du rejeu, le second ne doit pas le faire rougir.
    """

    TOTAL_CATALOGUE = 3

    def __init__(self) -> None:
        self.appels: list[tuple[str, str, str]] = []  # (via, outil, requête)

    def appels_d_outil(self) -> list[str]:
        return [nom for via, nom, _ in self.appels if via == "outil"]

    def catalogue(self, query: str, limit: int = 5, *, via: str = "outil") -> str:
        from tools.mcp_client import _catalog_miss

        self.appels.append((via, "library_catalog", query))
        return _catalog_miss(query, self.TOTAL_CATALOGUE)

    def recherche(self, query: str, limit: int = 3, *, via: str = "outil") -> str:
        from tools.mcp_client import _parse_result

        self.appels.append((via, "search_books", query))
        return _parse_result(
            {
                "found": True,
                "answer": f"(bouchon des rejeux) Passage trouvé pour « {query} ».",
                "sources": [{"title": "Livre factice", "author": "Auteur factice", "page": 1}],
            },
            limit,
        )


@pytest.fixture(autouse=True)
def librarybrain_bouchon(monkeypatch) -> LibraryBrainBouchon:
    """Coupe les deux ponts LibraryBrain de TOUS les tests d'intégration.

    Rien ne les bouchonnait. Mesuré le 2026-09-27 : le rejeu #21 a pris 120,06 s
    dans une passe (timeout HTTP de `search_books`) et 33,2 s dans la suivante —
    `search_books` faisait un vrai POST vers le RAG génératif (`:8765/api/ask`), et
    `library_catalog` lisait la vraie `library_brain.db` (25 823 livres). Le
    verdict dépendait donc de la machine : en CI, `ConnectError` immédiat et base
    absente, un autre chemin ; en local, le contenu du catalogue — le jour où un
    titre contient « puériculture », le hit exact désarme le garde et #21 rougit
    sans que le code ait bougé.

    Les deux niveaux sont remplacés : les noms importés par l'orchestrateur, et ceux
    du module (`learn_from_books` et la sonde catalogue de `search_books` passent
    par eux). Le journal `appels` permet au rejeu de vérifier que chaque appel
    d'outil LibraryBrain a bien été servi ICI — sans quoi un nouvel import qui
    contournerait le bouchon referait des appels réels sans que rien ne rougisse.
    """
    bouchon = LibraryBrainBouchon()
    monkeypatch.setattr("agent.orchestrator.mcp_catalog", bouchon.catalogue)
    monkeypatch.setattr("agent.orchestrator.mcp_search_books", bouchon.recherche)
    monkeypatch.setattr(
        "tools.mcp_client.catalog_lookup", functools.partial(bouchon.catalogue, via="module")
    )
    monkeypatch.setattr(
        "tools.mcp_client.search_books", functools.partial(bouchon.recherche, via="module")
    )
    return bouchon


@pytest.fixture(autouse=True)
def _librarybrain_jamais_demarre(monkeypatch):
    """Le lifespan de l'API ne sonde ni ne lance LibraryBrain pendant les tests.

    `TestClient(app)` déclenche le lifespan, qui démarre le thread `lb-init` :
    jusqu'à 8 sondes de :8765, puis, port libre et `LIBRARYBRAIN_DIR` posé (il
    l'est dans le `.env` principal), le SPAWN d'un vrai LibraryBrain. Les fixtures
    `client` le « désactivaient » en patchant `services.ensure_librarybrain`,
    mais `api/server.py` l'importait par nom : mesuré le 2026-09-27, 63 connexions
    réelles vers :8765 pendant tests/integration. Le lifespan passe désormais par
    le module ; ce patch le garantit pour TOUT test du dossier, fixture ou pas.
    """
    monkeypatch.setattr("services.ensure_librarybrain", lambda *_a, **_kw: True)


def _extraction_vide(**_kwargs):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))])


@pytest.fixture(autouse=True, scope="package")
def _extracteur_memoire_muet():
    """L'extraction de faits de fin de message n'interroge aucun modèle réel.

    Après chaque message WebSocket, l'API lance le thread `mem-extractor`
    (`api/server.py::_extract_memory_bg`). Il interroge le backend de
    `agent/memory_extractor.py`, que le faux `agent.llm.OpenAI` des tests
    WebSocket ne remplace pas : Ollama (`OLLAMA_BASE_URL`) au 2026-09-27, le
    gateway en mode mlx depuis la PR #289, donc un vrai `brain` de 44 Go.
    Mesuré le 2026-09-27 : 4 connexions vers :11434 depuis
    test_websocket_chat.py. Backend absent (la CI) : `APIConnectionError`
    avalée. Backend présent : une vraie extraction, dont les faits iraient en
    mémoire long terme. Le chemin dépendait de la machine.

    Le client rend une extraction vide : le thread tourne, rien ne sort. Portée
    `package` et pas test : `mem-extractor` est un thread démon lancé APRÈS
    l'événement `done`, il peut donc atteindre `_client_llm()` une fois le test
    démonté. Portée `session`, en revanche, déborderait sur
    tests/test_memory_extractor.py, qui a besoin du vrai `_client_llm`.
    L'extraction est testée là-bas.
    """
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_extraction_vide))
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("agent.memory_extractor._client_llm", lambda: client)
        yield


@pytest.fixture(autouse=True)
def api_sans_services_machine(monkeypatch) -> HttpxSansReseau:
    """`/api/status`, `/health` et `/api/proposals` ne joignent aucun service réel.

    Ces routes ouvrent leur propre `httpx.AsyncClient` vers Ollama, le gateway
    et le MCP Klody. Mesuré le 2026-09-27 : `test_status_endpoint` joignait
    :11434, :8090 et :8087 (5 connexions), et son verdict (`ollama`,
    `backend_active`, `mcp_server_active`) dépendait de ce qui tournait sur la
    machine. Pire : un test de `POST /api/proposals/{id}/status` aurait modifié
    l'état du vrai gateway. Tous les services sont absents, comme en CI.
    """
    faux = HttpxSansReseau()
    monkeypatch.setattr("api.server.httpx", faux)
    return faux


@pytest.fixture(autouse=True)
def _services_machine_hors_reseau(monkeypatch):
    """Garde : un test d'intégration qui joint un service de la machine ROUGIT.

    Les bouchons ci-dessus préviennent ; ce garde constate. Toute connexion
    vers un service de `services_de_la_machine()` (LibraryBrain, gateway et
    journal d'usage, Ollama, MCP Klody), et toute tentative de lancer
    LibraryBrain (`services._start_process`), est REFUSÉE — aucune requête
    n'atteint le vrai serveur, même par un chemin que personne n'a bouchonné —
    puis journalisée, et le test échoue au démontage. Journaliser plutôt que
    lever sur place : l'appel fautif part presque toujours d'un thread démon
    (`lb-init`, `klody-journal-client`, `mem-extractor`), dont l'exception
    n'atteindrait jamais pytest. Rend la liste des tentatives, pour les tests
    du garde lui-même.

    Limites connues : un thread démon lancé par le test N qui se connecte
    pendant le test N+1 est imputé à N+1 (le nom du thread figure dans le
    message), et une connexion partie après le démontage du DERNIER test
    n'est vue par personne.

    Derrière lui, le garde de SUITE (`tests/garde_reseau.py`) refuse TOUT le
    loopback sous le premier port éphémère, services nommés ou non. Celui-ci
    reste devant : il couvre aussi les hôtes NON loopback de la config, il
    garde à `connect_ex` sa sémantique `ECONNREFUSED`, et il surveille le
    lancement de LibraryBrain, qui n'est pas une connexion.
    """
    services = services_de_la_machine()
    tentatives: list[str] = []

    def _service_vise(adresse) -> str | None:
        if not (isinstance(adresse, tuple) and len(adresse) >= 2):
            return None
        nom, hotes = services.get(adresse[1], (None, frozenset()))
        return nom if adresse[0] in hotes else None

    def _noter(quoi: str) -> None:
        tentatives.append(f"{quoi} (thread {threading.current_thread().name})")

    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _connect(self, adresse):
        nom = _service_vise(adresse)
        if nom:
            _noter(f"connexion vers {adresse[0]}:{adresse[1]} [{nom}]")
            raise ConnectionRefusedError(
                errno.ECONNREFUSED, f"{nom} interdit pendant les tests d'intégration"
            )
        return connect(self, adresse)

    def _connect_ex(self, adresse):
        nom = _service_vise(adresse)
        if nom:
            _noter(f"connexion vers {adresse[0]}:{adresse[1]} [{nom}]")
            return errno.ECONNREFUSED
        return connect_ex(self, adresse)

    def _lancement(lb_path):
        _noter(f"lancement de LibraryBrain depuis {lb_path}")
        return None

    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr("services._start_process", _lancement)
    yield tentatives
    assert not tentatives, (
        "Un test d'intégration a tenté de joindre ou de lancer un service RÉEL de "
        f"la machine : {tentatives}"
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """PROJECT_ROOT isolé par test."""
    return tmp_path


@pytest.fixture
def fixture_loader():
    """Helper pour charger une fixture par nom (sans .json)."""
    def _load(name: str) -> dict:
        path = FIXTURES_DIR / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Fixture inconnue: {name}. Fixtures disponibles: "
                f"{[p.stem for p in FIXTURES_DIR.glob('*.json')]}"
            )
        return json.loads(path.read_text(encoding="utf-8"))
    return _load


@pytest.fixture
def fake_orchestrator(project_root: Path, monkeypatch: pytest.MonkeyPatch):
    """Factory qui retourne (orchestrator, fake_llm) câblés à partir d'une fixture.

    Usage:
        orch, llm = fake_orchestrator(fixture_dict)
        orch.run(fixture_dict["user_prompt"])
        assert llm.consumed >= 2
    """
    from tests.integration.replay_llm import FakeLLMClient, FakeRouter

    # Isole le projet
    monkeypatch.setenv("PROJECT_ROOT", str(project_root))

    def _make(fixture: dict, *, max_iterations: int = 6):
        # Import retardé pour que monkeypatch.setenv soit pris en compte
        from agent import orchestrator as orch_mod
        from agent.memory import ConversationMemory

        # Désactive Best-of-N (coûteux et pas le sujet du replay)
        monkeypatch.setattr(orch_mod, "BEST_OF_N_ENABLED", False)
        # Cap les itérations au cas où la fixture diverge
        monkeypatch.setattr(orch_mod, "MAX_ITERATIONS", max_iterations)
        # Désactive auto-exec sandbox sur .py (pollue les tests)
        monkeypatch.setattr(orch_mod, "SANDBOX_AUTO_EXEC", False)

        # Patch LLMClient : retournera notre FakeLLMClient
        fake_llm = FakeLLMClient(fixture)
        monkeypatch.setattr(orch_mod, "LLMClient", lambda *_a, **_kw: fake_llm)

        # Patch Router si la fixture en spécifie un
        router_decision = fixture.get("router_decision")
        if router_decision:
            fake_router = FakeRouter(router_decision)
            # Le Router est instancié lazy par property — on remplace la classe importée
            from agent import router as router_mod
            monkeypatch.setattr(
                router_mod, "Router", lambda *_a, **_kw: fake_router
            )

        # Désactive le profiler (suggestions + threading)
        noop_profiler = SimpleNamespace(
            track_request=lambda *_a, **_kw: None,
            track_tool_usage=lambda *_a, **_kw: None,
            get_suggestions=lambda *_a, **_kw: [],
            get_profile_for_prompt=lambda *_a, **_kw: "",
            stats=lambda: {},
        )
        monkeypatch.setattr(orch_mod, "get_profiler", lambda: noop_profiler)

        # Désactive l'extraction mid-session (thread daemon qui pollue les logs)
        monkeypatch.setattr(
            orch_mod.Orchestrator, "_mid_session_extract", lambda self: None
        )

        # Désactive load_skills (lit un fichier global)
        monkeypatch.setattr(orch_mod, "load_skills", lambda: [])

        memory = ConversationMemory()
        orch = orch_mod.Orchestrator(memory)
        # Force le file_manager sur tmp_path (PROJECT_ROOT n'est résolu qu'au boot config)
        from tools.file_manager import FileManager
        orch.file_manager = FileManager(root=project_root)
        # Même raison pour la recherche texte : sans ça, `search_in_files('.')`
        # fouillait le VRAI dépôt. Vu le 2026-09-27 en écrivant le scénario 22 :
        # un grep qui ramène un document du dépôt désarme le garde doc, le
        # scénario aurait mesuré le dépôt de travail au lieu de son projet.
        from tools.search import Search
        orch.search = Search(root=project_root)
        return orch, fake_llm

    return _make
