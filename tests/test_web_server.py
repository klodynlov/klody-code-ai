"""Serveur MCP web (klody_mcp/web_server.py) : garde-fous SSRF, parsing, découverte.

Tout est hors-ligne : on teste la validation d'URL (résolution locale d'IP
littérales, aucun accès réseau), l'extraction HTML, le parsing DuckDuckGo, et
la découverte des outils via MCPManager en in-process (FastMCPTransport).
"""

import pytest

from klody_mcp.web_server import (
    WebFetchError,
    _ddg_decode_href,
    _html_to_text,
    _ip_is_public,
    _parse_ddg,
    _validate_url,
)


# ── Anti-SSRF : _ip_is_public ───────────────────────────────────────────────────

class TestIpIsPublic:
    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:2800:220:1::"])
    def test_publiques(self, ip):
        assert _ip_is_public(ip) is True

    @pytest.mark.parametrize("ip", [
        "127.0.0.1",        # loopback
        "10.0.0.1",         # privé
        "192.168.1.1",      # privé
        "172.16.0.1",       # privé
        "169.254.169.254",  # link-local (métadonnées cloud — cible SSRF classique)
        "0.0.0.0",          # unspecified
        "::1",              # loopback IPv6
        "fe80::1",          # link-local IPv6
        "::ffff:127.0.0.1", # IPv4-mapped loopback
        "pas-une-ip",       # invalide → non public
    ])
    def test_non_publiques(self, ip):
        assert _ip_is_public(ip) is False


# ── Anti-SSRF : _validate_url ───────────────────────────────────────────────────

class TestValidateUrl:
    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "javascript:alert(1)",
        "http://",                 # sans hôte
    ])
    def test_schema_ou_hote_refuse(self, url):
        with pytest.raises(WebFetchError):
            _validate_url(url)

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/admin",
        "http://localhost:8000/",
        "http://10.1.2.3/",
        "http://192.168.0.10/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ])
    def test_ip_non_publique_refusee(self, url):
        with pytest.raises(WebFetchError):
            _validate_url(url)

    @pytest.mark.parametrize("url", [
        "http://8.8.8.8/",
        "https://1.1.1.1/dns-query",
    ])
    def test_ip_publique_acceptee(self, url):
        # IP littérale publique → getaddrinfo la renvoie sans accès réseau
        assert _validate_url(url) == url


# ── Extraction HTML ──────────────────────────────────────────────────────────────

class TestHtmlToText:
    def test_titre_et_texte(self):
        html = "<html><head><title>  Ma Page </title></head><body><h1>Bonjour</h1><p>Du texte.</p></body></html>"
        title, text = _html_to_text(html)
        assert title == "Ma Page"
        assert "Bonjour" in text
        assert "Du texte." in text

    def test_script_et_style_retires(self):
        html = "<body><script>evil()</script><style>.x{}</style><p>Visible</p></body>"
        _, text = _html_to_text(html)
        assert "Visible" in text
        assert "evil" not in text
        assert ".x{}" not in text

    def test_lignes_vides_compactees(self):
        html = "<body><p>A</p><p></p><p>B</p></body>"
        _, text = _html_to_text(html)
        assert text == "A\nB"


# ── Parsing DuckDuckGo ─────────────────────────────────────────────────────────

class TestDdg:
    def test_decode_href_uddg(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc&rut=abc"
        assert _ddg_decode_href(href) == "https://example.com/doc"

    def test_decode_href_protocole_relatif(self):
        assert _ddg_decode_href("//example.com/x") == "https://example.com/x"

    def test_decode_href_direct(self):
        assert _ddg_decode_href("https://example.com") == "https://example.com"

    def test_parse_resultats(self):
        html = """
        <div class="result">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpy.org%2Fa">Titre A</a>
          <a class="result__snippet">Extrait A</a>
        </div>
        <div class="result">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpy.org%2Fb">Titre B</a>
          <a class="result__snippet">Extrait B</a>
        </div>
        """
        out = _parse_ddg(html, limit=10)
        assert len(out) == 2
        assert out[0] == {"title": "Titre A", "url": "https://py.org/a", "snippet": "Extrait A"}
        assert out[1]["url"] == "https://py.org/b"

    def test_parse_respecte_limit(self):
        html = '<div class="result"><a class="result__a" href="//x/?uddg=https%3A%2F%2Fa.com">T</a></div>' * 5
        assert len(_parse_ddg(html, limit=2)) == 2


# ── Découverte via le client MCP (in-process) ───────────────────────────────────

class TestDiscovery:
    @pytest.fixture
    def manager(self):
        from klody_mcp.web_server import mcp
        from tools.mcp_bridge import MCPManager
        mgr = MCPManager({"web": mcp})
        mgr.discover()
        return mgr

    def test_deux_outils(self, manager):
        assert len(manager.tool_names()) == 2

    def test_noms_namespaces(self, manager):
        names = set(manager.tool_names())
        assert names == {"mcp__web__fetch_url", "mcp__web__web_search"}

    def test_schema_valide(self, manager):
        for schema in manager.tools:
            assert schema["type"] == "function"
            assert "[MCP:web]" in schema["function"]["description"]
            assert schema["function"]["parameters"]["type"] == "object"
