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


# ── Moteur de recherche : API Brave officielle, DuckDuckGo en secours ──────────
# Vécu le 2026-11-20 : DuckDuckGo bloquait l'IP (202 « anomaly ») sur toutes ses
# entrées. Tout est bouchonné ici : aucun appel réseau, aucune vraie clé.

from klody_mcp import web_server as ws

_PAGE_ANOMALIE = "<html><body><div class='anomaly-modal__title'>bloqué</div></body></html>"
_PAGE_CHALLENGE = "<html><body><form id='challenge-form'></form></body></html>"
_PAGE_DDG = (
    '<div class="result"><a class="result__a" '
    'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fddg.example%2Fa">DDG A</a></div>'
)
_JSON_BRAVE = {
    "web": {
        "results": [
            {"title": "Vitesse <strong>au volant</strong>",
             "url": "https://www.service-public.gouv.fr/particuliers/vosdroits/F19460",
             "description": "Limitations de <strong>vitesse</strong>."},
            {"title": "Sans URL", "url": ""},
            {"title": "Légifrance", "url": "https://www.legifrance.gouv.fr/codes/x",
             "description": "Code de la route"},
        ]
    }
}


class _Rep:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("pas du JSON")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _Routeur:
    """Bouchon de requests.get : répond selon l'hôte, consigne chaque appel."""

    def __init__(self, brave=None, ddg=None):
        self.brave, self.ddg, self.appels = brave, ddg, []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.appels.append({"url": url, "params": params, "headers": headers})
        if url == ws._BRAVE_API_URL:
            if isinstance(self.brave, Exception):
                raise self.brave
            return self.brave
        return self.ddg


@pytest.fixture
def moteur(monkeypatch):
    def poser(brave=None, ddg=None, cle="cle-de-test", backend=None):
        routeur = _Routeur(brave, ddg)
        monkeypatch.setattr(ws.requests, "get", routeur)
        if cle is None:
            monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
        else:
            monkeypatch.setenv("BRAVE_SEARCH_API_KEY", cle)
        if backend is None:
            monkeypatch.delenv("WEB_SEARCH_BACKEND", raising=False)
        else:
            monkeypatch.setenv("WEB_SEARCH_BACKEND", backend)
        return routeur
    return poser


class TestParseBrave:
    def test_balises_retirees_et_url_vides_sautees(self):
        out = ws._parse_brave(_JSON_BRAVE, limit=10)
        assert [r["url"] for r in out] == [
            "https://www.service-public.gouv.fr/particuliers/vosdroits/F19460",
            "https://www.legifrance.gouv.fr/codes/x",
        ]
        assert out[0]["title"] == "Vitesse au volant"
        assert out[0]["snippet"] == "Limitations de vitesse ."
        assert out[1]["snippet"] == "Code de la route"

    def test_limite_et_reponse_sans_web(self):
        assert len(ws._parse_brave(_JSON_BRAVE, limit=1)) == 1
        assert ws._parse_brave({}, limit=5) == []
        assert ws._parse_brave({"web": None}, limit=5) == []


class TestWebSearchBrave:
    def test_cle_posee_brave_en_premier(self, moteur):
        r = moteur(brave=_Rep(json_data=_JSON_BRAVE))
        out = ws.web_search("vitesse", limit=3)
        assert out["provider"] == "brave" and out["count"] == 2
        assert len(r.appels) == 1
        appel = r.appels[0]
        assert appel["headers"]["X-Subscription-Token"] == "cle-de-test"
        assert appel["params"]["q"] == "vitesse"
        assert appel["params"]["count"] == "3"

    def test_count_plafonne_a_20(self, moteur):
        r = moteur(brave=_Rep(json_data={"web": {"results": []}}))
        ws.web_search("x", limit=25)
        assert r.appels[0]["params"]["count"] == "20"

    def test_sans_cle_duckduckgo_seul(self, moteur):
        r = moteur(ddg=_Rep(text=_PAGE_DDG), cle=None)
        out = ws.web_search("x")
        assert out["provider"] == "duckduckgo"
        assert [a["url"] for a in r.appels] == [ws._DDG_HTML_URL]

    @pytest.mark.parametrize("statut,mot", [(401, "clé refusée"), (403, "clé refusée"),
                                            (429, "quota"), (500, "HTTP 500")])
    def test_echec_brave_secours_duckduckgo_avec_avertissement(self, moteur, statut, mot):
        moteur(brave=_Rep(status=statut), ddg=_Rep(text=_PAGE_DDG))
        out = ws.web_search("x")
        assert out["provider"] == "duckduckgo"
        assert out["results"][0]["url"] == "https://ddg.example/a"
        assert mot in out["warning"]

    def test_les_deux_echouent_erreur_nomme_les_deux(self, moteur):
        moteur(brave=_Rep(status=429), ddg=_Rep(status=202, text=_PAGE_ANOMALIE))
        out = ws.web_search("x")
        assert "results" not in out
        assert "429" in out["error"] and "anti-bot" in out["error"]

    def test_reseau_brave_ne_leve_pas(self, moteur):
        import requests
        moteur(brave=requests.ConnectionError("boom"), ddg=_Rep(text=_PAGE_DDG))
        out = ws.web_search("x")
        assert out["provider"] == "duckduckgo" and "échec réseau" in out["warning"]

    def test_reponse_non_json(self, moteur):
        moteur(brave=_Rep(json_data=None), backend="brave")
        assert ws.web_search("x") == {"error": "Brave : réponse non JSON."}

    def test_mode_brave_strict_pas_de_secours(self, moteur):
        r = moteur(brave=_Rep(status=401), ddg=_Rep(text=_PAGE_DDG), backend="brave")
        out = ws.web_search("x")
        assert "clé refusée" in out["error"]
        assert len(r.appels) == 1

    def test_mode_brave_sans_cle_refuse(self, moteur):
        r = moteur(cle=None, backend="brave")
        assert "BRAVE_SEARCH_API_KEY est vide" in ws.web_search("x")["error"]
        assert r.appels == []

    def test_mode_duckduckgo_ignore_la_cle(self, moteur):
        r = moteur(ddg=_Rep(text=_PAGE_DDG), backend="duckduckgo")
        ws.web_search("x")
        assert [a["url"] for a in r.appels] == [ws._DDG_HTML_URL]

    def test_backend_inconnu_vaut_auto(self, moteur):
        moteur(brave=_Rep(json_data=_JSON_BRAVE), backend="bing")
        assert ws.web_search("x")["provider"] == "brave"

    def test_la_cle_ne_fuit_dans_aucune_sortie(self, moteur):
        import json
        cle = "SECRET-NE-PAS-FUIR"
        for statut in (401, 403, 429, 500):
            moteur(brave=_Rep(status=statut), ddg=_Rep(status=202, text=_PAGE_ANOMALIE), cle=cle)
            assert cle not in json.dumps(ws.web_search("x"), ensure_ascii=False)


class TestDdgAntiBot:
    @pytest.mark.parametrize("page", [_PAGE_ANOMALIE, _PAGE_CHALLENGE])
    def test_page_anti_bot_en_202_est_denoncee(self, moteur, page):
        # Le blocage du 2026-11-20 répondait 202 : le 2xx seul ne dit rien.
        moteur(ddg=_Rep(status=202, text=page), cle=None)
        out = ws.web_search("x")
        assert "anti-bot" in out["error"] and "BRAVE_SEARCH_API_KEY" in out["error"]

    def test_vraie_absence_de_resultats(self, moteur):
        moteur(ddg=_Rep(text="<html><body>rien</body></html>"), cle=None)
        assert ws.web_search("x") == {"count": 0, "results": [], "provider": "duckduckgo",
                                      "note": "Aucun résultat."}
