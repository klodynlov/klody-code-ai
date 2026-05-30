"""Client MCP (tools/mcp_bridge.py) : découverte, dispatch, résilience.

Utilise le serveur Gmail FastMCP en in-process (FastMCPTransport) — aucun
réseau, aucune credential requise (les outils renvoient une erreur lisible
en texte, ce qui valide aussi la propagation d'erreur sans crash).
"""

import json
import pytest

from tools.mcp_bridge import MCPManager, _result_to_text, _tool_to_openai_schema


@pytest.fixture
def gmail_server():
    from klody_mcp.gmail_server import mcp
    return mcp


@pytest.fixture
def manager(gmail_server):
    mgr = MCPManager({"gmail": gmail_server})
    mgr.discover()
    return mgr


# ── Découverte ────────────────────────────────────────────────────────────────

class TestDiscovery:
    def test_decouvre_les_outils(self, manager):
        names = manager.tool_names()
        assert len(names) == 8

    def test_noms_namespaces(self, manager):
        for n in manager.tool_names():
            assert n.startswith("mcp__gmail__")

    def test_schema_openai_valide(self, manager):
        for schema in manager.tools:
            assert schema["type"] == "function"
            fn = schema["function"]
            assert set(fn.keys()) == {"name", "description", "parameters"}
            assert fn["parameters"]["type"] == "object"
            assert "[MCP:gmail]" in fn["description"]

    def test_discover_idempotent(self, manager):
        first = manager.tool_names()
        again = manager.discover()  # ne re-scanne pas
        assert manager.tool_names() == first
        assert len(again) == len(first)


# ── owns / call ────────────────────────────────────────────────────────────────

class TestOwnsAndCall:
    def test_owns_outil_connu(self, manager):
        assert manager.owns("mcp__gmail__send_email")

    def test_owns_outil_inconnu(self, manager):
        assert not manager.owns("mcp__gmail__nope")
        assert not manager.owns("read_file")

    def test_call_propage_resultat_texte(self, manager, monkeypatch):
        # On force l'absence de credentials (indépendant du .env de la machine,
        # et sans accès réseau) : list_labels renvoie alors une erreur en TEXTE,
        # pas en exception — c'est exactement la propagation qu'on veut.
        import klody_mcp.gmail_server as gs
        monkeypatch.setattr(gs, "GMAIL_ADDRESS", "")
        monkeypatch.setattr(gs, "GMAIL_APP_PASSWORD", "")
        out = manager.call("mcp__gmail__list_labels", {})
        assert isinstance(out, str)
        assert "GMAIL_ADDRESS" in out or "error" in out.lower()

    def test_call_outil_inconnu_renvoie_erreur(self, manager):
        out = manager.call("mcp__gmail__nope", {})
        assert out.startswith("ERREUR")
        assert "inconnu" in out


# ── Résilience ──────────────────────────────────────────────────────────────────

class TestResilience:
    def test_serveur_injoignable_ignore(self, gmail_server):
        # Un serveur mort (port 1) est ignoré ; le serveur valide survit.
        mgr = MCPManager({
            "gmail": gmail_server,
            "mort": "http://127.0.0.1:1/mcp",
        })
        tools = mgr.discover()
        assert len(tools) == 8
        assert all("mcp__gmail__" in t["function"]["name"] for t in tools)

    def test_aucun_serveur(self):
        mgr = MCPManager({})
        assert mgr.discover() == []
        assert mgr.tool_names() == []
        assert not mgr.owns("quoi_que_ce_soit")


class TestCache:
    def test_second_manager_reutilise_le_cache(self, gmail_server):
        from tools import mcp_bridge
        mcp_bridge.clear_discovery_cache()
        m1 = MCPManager({"gmail": gmail_server})
        m1.discover()
        assert mcp_bridge._DISCOVERY_CACHE, "le cache devrait être peuplé"
        # Un second manager avec la même config réutilise le cache (pas de re-scan)
        m2 = MCPManager({"gmail": gmail_server})
        assert m2.discover() == m1.tools
        assert m2.tool_names() == m1.tool_names()

    def test_clear_cache(self, gmail_server):
        from tools import mcp_bridge
        MCPManager({"gmail": gmail_server}).discover()
        mcp_bridge.clear_discovery_cache()
        assert mcp_bridge._DISCOVERY_CACHE == {}


# ── Helpers purs ────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_tool_to_openai_schema(self):
        class FakeTool:
            name = "send_email"
            description = "Envoie un email"
            inputSchema = {"type": "object", "properties": {"to": {"type": "string"}}}
        schema = _tool_to_openai_schema("gmail", FakeTool())
        assert schema["function"]["name"] == "mcp__gmail__send_email"
        assert schema["function"]["parameters"]["properties"]["to"]["type"] == "string"

    def test_tool_to_openai_schema_sans_input(self):
        class FakeTool:
            name = "ping"
            description = ""
            inputSchema = None
        schema = _tool_to_openai_schema("srv", FakeTool())
        assert schema["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_result_to_text_content(self):
        class Item:
            text = "bonjour"
        class Result:
            content = [Item()]
            data = None
        assert _result_to_text(Result()) == "bonjour"

    def test_result_to_text_data_fallback(self):
        class Result:
            content = []
            data = {"ok": True}
        assert "ok" in _result_to_text(Result())


# ── Parsing config ───────────────────────────────────────────────────────────────

class TestConfigParsing:
    def test_json_valide(self):
        from config import _parse_mcp_servers
        assert _parse_mcp_servers('{"gmail":"http://x/mcp"}') == {"gmail": "http://x/mcp"}

    def test_vide(self):
        from config import _parse_mcp_servers
        assert _parse_mcp_servers("") == {}
        assert _parse_mcp_servers("   ") == {}

    def test_json_invalide(self):
        from config import _parse_mcp_servers
        assert _parse_mcp_servers("pas du json") == {}

    def test_pas_un_dict(self):
        from config import _parse_mcp_servers
        assert _parse_mcp_servers("[1, 2, 3]") == {}
