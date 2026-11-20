import json
from unittest.mock import MagicMock

from tools import library_sources as sources


def test_road_search_filters_ads_and_false_domains(monkeypatch):
    from klody_mcp import web_server
    results = [
        {"title": "Ad", "url": "https://duckduckgo.com/y.js?ad_domain=example.org"},
        {"title": "False domain", "url": "https://service-public.gouv.fr.evil.com"},
        {"title": "Official", "url": "https://www.service-public.gouv.fr/particuliers/vosdroits/F20443"},
    ]
    search = MagicMock(return_value={"results": results})
    monkeypatch.setattr(web_server, "web_search", search)
    output = json.loads(sources.library_web_search("code de la route"))
    assert output["official_only"] and output["count"] == 1
    assert output["results"][0]["title"] == "Official"


def test_import_auth_url_only_and_status(monkeypatch):
    monkeypatch.setattr(sources, "librarybrain_headers", lambda: {"X-API-Token": "unit-test"})
    client = MagicMock()
    client.return_value.__enter__.return_value.request.return_value.json.return_value = {"status": "imported", "book_id": 9}
    monkeypatch.setattr(sources.httpx, "Client", client)
    result = json.loads(sources.library_import_source("https://example.org", True))
    assert result["book_id"] == 9
    assert client.call_args.kwargs["headers"] == {"X-API-Token": "unit-test"}
    request = client.return_value.__enter__.return_value.request
    assert request.call_args.kwargs["json"] == {"url": "https://example.org", "official_only": True}
    sources.library_source_status(9)
    assert request.call_args.kwargs["params"] == {"book_id": 9}


def test_discovery_selects_source_tools():
    from tools.context_selection import select_native
    from tools.registry import get_tool_names, get_tools
    names = {t["function"]["name"] for t in sources.LIBRARY_SOURCE_TOOLS}
    assert names <= set(get_tool_names())
    assert names <= select_native("Actualise le Code de la route dans LibraryBrain", get_tools())


def test_timeout_does_not_claim_failure_or_success(monkeypatch):
    client = MagicMock()
    client.return_value.__enter__.return_value.request.side_effect = sources.httpx.ReadTimeout("timeout")
    monkeypatch.setattr(sources.httpx, "Client", client)
    result = json.loads(sources.library_import_source("https://example.org"))
    assert "library_source_status" in result["error"]


def test_official_search_fallback_is_explicit(monkeypatch):
    from klody_mcp import web_server
    monkeypatch.setattr(web_server, "web_search", lambda *a, **kw: {"error": "anti-bot"})
    monkeypatch.setattr(sources, "_service_public_search", lambda *a: {"results": [
        {"url": "https://www.service-public.gouv.fr/particuliers/vosdroits/F20443"}]})
    result = json.loads(sources.library_web_search("Code de la route"))
    assert result["provider"] == "service-public" and result["count"] == 1
    assert "limitée" in result["warning"]
    assert "error" in json.loads(sources.library_web_search("documentation python"))


def test_import_and_refresh_require_approval_search_and_status_do_not():
    """Import/actualisation téléchargent une URL et publient une version indexée."""
    from agent.approval import requires_approval
    assert requires_approval("library_import_source", {"url": "https://example.org"})
    assert requires_approval("library_refresh_source", {"book_id": 1})
    assert not requires_approval("library_web_search", {"query": "x"})
    assert not requires_approval("library_source_status", {})


def test_orchestrator_dispatches_source_tools(monkeypatch):
    from agent import orchestrator as orch_mod
    appels = []
    monkeypatch.setattr(orch_mod, "library_source_status", lambda book_id=None: appels.append(book_id) or "{}")
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch._dispatch["library_source_status"]({"book_id": 7})
    assert appels == [7]


def test_service_public_fallback_parses_general_search(monkeypatch):
    """Seuls les liens de résultats (dans <main>) comptent, pas le menu."""
    import requests
    page = (b'<nav><a href="https://www.service-public.gouv.fr/particuliers/vosdroits/F1">Menu</a></nav>'
            b'<main><a href="/particuliers/vosdroits/F19460">Vitesse au volant</a>'
            b'<a href="https://evil.example/particuliers/vosdroits/F2">Faux</a></main>')
    vus = {}

    class Reponse:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_content(self, _n): yield page

    def faux_get(url, params=None, **kw):
        vus.update(url=url, params=params)
        return Reponse()

    monkeypatch.setattr(requests, "get", faux_get)
    out = sources._service_public_search("Code de la route vitesse service-public", 5)
    assert vus["url"].endswith("/particuliers/recherche-generale")
    assert vus["params"] == {"keyword": "Code de la route vitesse"}
    assert [r["url"] for r in out["results"]] == [
        "https://www.service-public.gouv.fr/particuliers/vosdroits/F19460"]
