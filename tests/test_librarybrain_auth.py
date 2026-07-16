"""Auth LibraryBrain : l'en-tête `X-API-Token` sur TOUS les appels HTTP.

LibraryBrain (dépôt séparé) protège tout `/api/` par un middleware (api/auth.py)
dès qu'`api_token` est posé dans son config.yaml — p.ex. avant une exposition
Tailscale. Klody n'envoyait AUCUN token : 100 % de ses appels /api/ seraient
partis en 401, derrière un point vert menteur (cf. test_services_watchdog).

CES TESTS SONT LE SEUL FILET. Aucun test existant ne peut attraper un en-tête
manquant : tous les mocks LibraryBrain du repo sont des MagicMock ou des
`*_a, **_k` qui avalent les kwargs — un site d'appel oublié resterait vert. On
épingle donc chaque site un par un, en assertant sur les headers.

ISOLATION : `config.LIBRARYBRAIN_TOKEN` est lu à l'import, via load_dotenv(). Un
vrai token dans le `.env` du dev basculerait ces tests sur la branche « token
présent » alors que la CI (sans .env) joue « pas de token » — le test dirait la
vérité sur une machine et mentirait sur l'autre. On PIN donc la valeur à chaque
test plutôt que de lire l'environnement (même leçon que 6a4dc93 « pin MLX_MODEL,
drop .env dependency in backend switch »).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import config
import pytest
import services

TOKEN = "tok-de-test-42"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _pin_token(monkeypatch):
    """Neutralise le .env du dev : sans ce pin, la suite passerait ou échouerait
    selon la machine. Défaut = pas de token (le cas local, et celui de la CI)."""
    monkeypatch.setattr(config, "LIBRARYBRAIN_TOKEN", "")


def _set_token(monkeypatch, value: str = TOKEN) -> None:
    monkeypatch.setattr(config, "LIBRARYBRAIN_TOKEN", value)


# ── Le helper central ────────────────────────────────────────────────────────


def test_sans_token_aucun_en_tete():
    """Usage local (api_token vide côté serveur) : ne rien envoyer. Le
    comportement historique doit rester strictement inchangé."""
    assert config.librarybrain_headers() == {}


def test_token_pose_len_tete_x_api_token(monkeypatch):
    """Nom d'en-tête EXACT : api/auth.py lit `request.headers.get("X-API-Token")`."""
    _set_token(monkeypatch)
    assert config.librarybrain_headers() == {"X-API-Token": TOKEN}


def test_token_est_strippe(monkeypatch):
    """Le serveur compare avec `secrets.compare_digest` — exact, aucune
    tolérance. Un `\\n` de fin traîné depuis .env donnerait un 401 très pénible à
    diagnostiquer (le token « est pourtant le bon »), et httpx lèverait sur une
    valeur d'en-tête contenant un saut de ligne."""
    _set_token(monkeypatch, f"  {TOKEN}\n")
    assert config.librarybrain_headers() == {"X-API-Token": TOKEN}


def test_token_blanc_compte_comme_absent(monkeypatch):
    """`LIBRARYBRAIN_TOKEN="   "` = pas de token. Envoyer `X-API-Token: ""`
    compterait comme une tentative RATÉE au lieu d'une absence — or les deux
    pannes ont des remèdes opposés (cf. _unauthorized_detail)."""
    _set_token(monkeypatch, "   ")
    assert config.librarybrain_headers() == {}


def test_le_token_ne_fuit_pas_dans_lurl():
    """Le token doit vivre dans un en-tête, JAMAIS dans LIBRARYBRAIN_URL :
    /health échoie les URLs des serveurs, et tools/project_creator.py montre le
    précédent (`https://{GITHUB_TOKEN}@github.com/...`) à ne pas copier ici."""
    assert "@" not in config.LIBRARYBRAIN_URL.split("//", 1)[-1].split("/", 1)[0]
    assert "token" not in config.LIBRARYBRAIN_URL.lower()


# ── La sonde de vie (services._probe) ────────────────────────────────────────


def _capture_probe_headers(monkeypatch) -> dict:
    captured: dict = {}

    class _Resp:
        status_code = 200

    def _get(*_a, **kwargs):
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr(services.httpx, "get", _get)
    return captured


def test_sonde_envoie_le_token(monkeypatch):
    """Sans ça, la sonde verrait 401 alors que les vrais appels passent — un faux
    négatif exactement symétrique du faux positif qu'on vient de corriger."""
    _set_token(monkeypatch)
    captured = _capture_probe_headers(monkeypatch)

    assert services._probe("http://127.0.0.1:8765") == services.PROBE_UP
    assert captured["headers"] == {"X-API-Token": TOKEN}


def test_sonde_sans_token_nenvoie_pas_den_tete(monkeypatch):
    captured = _capture_probe_headers(monkeypatch)

    services._probe("http://127.0.0.1:8765")
    assert captured["headers"] == {}


# ── Le message de diagnostic ─────────────────────────────────────────────────


def test_detail_401_distingue_token_absent_et_token_refuse(monkeypatch):
    """LibraryBrain renvoie le MÊME `401 {"error": "Non autorisé"}` qu'on ait
    envoyé un mauvais token ou aucun : le serveur ne nous départage pas. Klody
    sait ce qu'il a envoyé — et les deux pannes n'ont pas le même remède."""
    services._librarybrain_status["state"] = services.PROBE_UNAUTHORIZED

    absent = services.get_librarybrain_status()["detail"]
    assert "LIBRARYBRAIN_TOKEN est vide" in absent
    assert "api_token" in absent, "doit nommer la clé serveur à renseigner"

    _set_token(monkeypatch)
    refuse = services.get_librarybrain_status()["detail"]
    assert "ne correspond pas" in refuse
    assert absent != refuse, "un token refusé ne se soigne pas comme un token absent"

    services._librarybrain_status["state"] = services.PROBE_DOWN


# ── Les sites d'appel, un par un ─────────────────────────────────────────────


def test_search_books_envoie_le_token(monkeypatch):
    """tools/mcp_client.py — l'outil `search_books` du LLM."""
    from tools import mcp_client

    _set_token(monkeypatch)
    captured: dict = {}

    class _Client:
        def __init__(self, *_a, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def post(self, *_a, **_k):
            raise RuntimeError("stop — on ne teste que l'en-tête")

    monkeypatch.setattr(mcp_client.httpx, "Client", _Client)
    mcp_client.search_books("peu importe")

    assert captured["headers"] == {"X-API-Token": TOKEN}


def test_catalog_lookup_ne_passe_pas_par_le_reseau(monkeypatch):
    """GARDE-FOU : `catalog_lookup` lit la DB SQLite en direct — il ne traverse
    NI le serveur :8765 NI son auth. Il doit donc continuer à répondre quand
    LibraryBrain 401. mcp_client est un module HYBRIDE (search_books = HTTP,
    catalog_lookup = sqlite) : classer ce fichier par fichier donne une réponse
    fausse pour l'une des deux fonctions."""
    from tools import mcp_client

    def _boom(*_a, **_k):
        raise AssertionError("catalog_lookup ne doit faire AUCUN appel HTTP")

    monkeypatch.setattr(mcp_client.httpx, "Client", _boom)
    monkeypatch.setattr(mcp_client, "LIBRARY_DB_PATH", Path("/inexistant.db"))

    assert "introuvable" in mcp_client.catalog_lookup("test").lower()


def test_github_reader_list_envoie_le_token(monkeypatch):
    """tools/github_reader.py:123 — GET /api/books. Site que tout balayage naïf
    rate : le fichier grepe « GitHub », pas « LibraryBrain »."""
    from tools import github_reader

    _set_token(monkeypatch)
    with patch.object(github_reader, "_LB_BASE", "http://127.0.0.1:8765"), \
         patch.object(github_reader.httpx, "Client") as cls:
        cls.return_value.__enter__.return_value.get.return_value = MagicMock(
            json=MagicMock(return_value=[]), raise_for_status=MagicMock()
        )
        github_reader.list_indexed_repos()

    assert cls.call_args.kwargs["headers"] == {"X-API-Token": TOKEN}


def test_github_reader_index_envoie_le_token(monkeypatch):
    """tools/github_reader.py:165 — POST /api/github/index. Chemin d'ÉCRITURE :
    c'est précisément ce que l'auth est censée protéger."""
    from tools import github_reader

    _set_token(monkeypatch)
    with patch.object(github_reader, "_LB_BASE", "http://127.0.0.1:8765"), \
         patch.object(github_reader.httpx, "Client") as cls:
        cls.return_value.__enter__.return_value.post.return_value = MagicMock(
            json=MagicMock(return_value={"added": 0, "updated": 0, "files": []}),
            raise_for_status=MagicMock(),
        )
        github_reader.index_github_repo("owner/repo")

    assert cls.call_args.kwargs["headers"] == {"X-API-Token": TOKEN}


def test_distill_book_envoie_le_token(monkeypatch):
    """scripts/distill_book.py:270 — l'ancrage auteur anti-hallucination.

    Site le plus traître : le script n'importe PAS config.py (il redéclare les
    constantes), donc un fix routé par config le raterait. Et son
    `except Exception` avale un 401 en « on garde la valeur du modèle » → sous
    api_token, l'anti-hallucination se désactiverait à 100 % EN SILENCE, avec
    des digests réécrits sur des auteurs inventés et un pipeline vert.
    """
    from scripts import distill_book

    monkeypatch.setattr(distill_book, "LIBRARYBRAIN_HEADERS", {"X-API-Token": TOKEN})
    captured: dict = {}

    class _Client:
        def __init__(self, *_a, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def post(self, *_a, **_k):
            raise RuntimeError("stop — on ne teste que l'en-tête")

    monkeypatch.setattr(distill_book.httpx, "Client", _Client)
    distill_book._resolve_source("un titre")

    assert captured["headers"] == {"X-API-Token": TOKEN}


def test_distill_book_lit_la_meme_variable_denv():
    """Le script duplique la lecture du token (il est autonome par choix). La
    duplication n'est acceptable que si la VARIABLE est la même que celle de
    config.py — sinon les deux dérivent et le site redevient un angle mort."""
    source = (ROOT / "scripts" / "distill_book.py").read_text(encoding="utf-8")
    assert 'os.getenv("LIBRARYBRAIN_TOKEN", "")' in source


@pytest.mark.asyncio
async def test_klody_mcp_server_envoie_le_token(monkeypatch):
    """klody_mcp/server.py:49 — POST /api/ask. Process mort aujourd'hui (:8082
    n'écoute pas), mais le code est vivant : le laisser sans token en ferait une
    bombe à retardement au réveil."""
    from klody_mcp import server as mcp_server

    _set_token(monkeypatch)
    captured: dict = {}

    class _AsyncClient:
        def __init__(self, *_a, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *_a, **_k):
            raise RuntimeError("stop — on ne teste que l'en-tête")

    monkeypatch.setattr(mcp_server.httpx, "AsyncClient", _AsyncClient)
    # `@mcp.tool()` rend ici la fonction elle-même (pas un wrapper `.fn`).
    await mcp_server.search_books("peu importe")

    assert captured["headers"] == {"X-API-Token": TOKEN}


@pytest.mark.asyncio
async def test_rag_proxy_envoie_le_token(monkeypatch):
    """scripts/rag-proxy.py:166 — POST /api/ask. Proxy mort (:8081), même
    raisonnement que ci-dessus. Le tiret du nom empêche l'import classique."""
    spec = importlib.util.spec_from_file_location(
        "rag_proxy_auth", ROOT / "scripts" / "rag-proxy.py"
    )
    assert spec and spec.loader
    rp = importlib.util.module_from_spec(spec)
    sys.modules["rag_proxy_auth"] = rp
    spec.loader.exec_module(rp)

    _set_token(monkeypatch)
    captured: dict = {}

    class _AsyncClient:
        def __init__(self, *_a, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *_a, **_k):
            raise RuntimeError("stop — on ne teste que l'en-tête")

    monkeypatch.setattr(rp.httpx, "AsyncClient", _AsyncClient)
    await rp._search_books("peu importe")

    assert captured["headers"] == {"X-API-Token": TOKEN}
