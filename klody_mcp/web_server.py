"""Web MCP server — accès web en LECTURE SEULE.

Serveur MCP autonome (même patron que gmail_server.py) exposant deux outils :
n'importe quel client MCP (Klody, Claude Desktop, Cline, Zed…) peut s'y brancher.

Garde-fous de sécurité (lecture seule, pensé pour un agent qui a aussi des
pouvoirs d'action — fichiers, terminal, email) :
- GET uniquement, schémas http/https uniquement (file://, ftp://, gopher://… refusés) ;
- protection SSRF : l'hôte est résolu et TOUTE IP privée / loopback / link-local /
  réservée / multicast est refusée — y compris à chaque saut de redirection ;
- taille de téléchargement plafonnée, texte tronqué, timeout strict ;
- aucune écriture, aucun POST de données utilisateur (sauf la requête vers le
  moteur de recherche).

Démarrage :
    python -m klody_mcp.web_server                          # stdio (défaut)
    WEB_MCP_TRANSPORT=http python -m klody_mcp.web_server     # HTTP sur :8085

Outils exposés :
- fetch_url(url, max_chars)        — récupère une page (HTML → texte) en lecture seule
- web_search(query, limit)         — recherche DuckDuckGo (sans clé API) → titres/URL/extraits
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

logger = logging.getLogger(__name__)

# Limites (surchargées par l'environnement / .env)
FETCH_TIMEOUT = float(os.getenv("WEB_FETCH_TIMEOUT", "15"))
MAX_BYTES = int(os.getenv("WEB_FETCH_MAX_BYTES", "2000000"))      # 2 Mo téléchargés max
MAX_CHARS = int(os.getenv("WEB_FETCH_MAX_CHARS", "8000"))         # texte renvoyé max
HARD_MAX_CHARS = int(os.getenv("WEB_FETCH_HARD_MAX_CHARS", "50000"))
MAX_REDIRECTS = int(os.getenv("WEB_MAX_REDIRECTS", "5"))
SEARCH_RESULTS = int(os.getenv("WEB_SEARCH_RESULTS", "8"))
USER_AGENT = os.getenv(
    "WEB_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

_ALLOWED_SCHEMES = {"http", "https"}
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"

mcp = FastMCP("Web")


class WebFetchError(Exception):
    """Erreur de récupération web lisible (refus de garde-fou ou réseau)."""


# ---------------------------------------------------------------------------- #
# Garde-fous (purs / réseau)                                                    #
# ---------------------------------------------------------------------------- #


def _ip_is_public(ip_str: str) -> bool:
    """True seulement si l'IP est routable publiquement (anti-SSRF)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # IPv6 mappant une IPv4 (::ffff:127.0.0.1) → on évalue l'IPv4 sous-jacente
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_url(url: str) -> str:
    """Valide schéma + résout l'hôte et refuse toute IP non publique (SSRF).

    Retourne l'URL telle quelle si elle est sûre, lève WebFetchError sinon.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise WebFetchError(
            f"Schéma non autorisé: {parsed.scheme or '(vide)'!r} — http/https uniquement."
        )
    host = parsed.hostname
    if not host:
        raise WebFetchError("URL sans hôte.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebFetchError(f"Résolution DNS échouée pour {host!r}: {exc}") from exc
    ips = {str(info[4][0]) for info in infos}
    if not ips:
        raise WebFetchError(f"Aucune IP résolue pour {host!r}.")
    for ip in ips:
        if not _ip_is_public(ip):
            raise WebFetchError(
                f"Accès refusé: {host!r} résout vers une IP non publique ({ip}) "
                "— protection SSRF."
            )
    return url


def _fetch(url: str) -> tuple[int, str, bytes, str, str]:
    """GET avec validation à chaque saut de redirection et plafond de taille.

    Retourne (status, content_type, contenu brut, encodage, url finale).
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _validate_url(current)
        resp = requests.get(
            current,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            timeout=FETCH_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                if not loc:
                    raise WebFetchError("Redirection sans en-tête Location.")
                current = urljoin(current, loc)
                continue
            total = 0
            chunks: list[bytes] = []
            for chunk in resp.iter_content(8192):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_BYTES:
                    break
            return (
                resp.status_code,
                resp.headers.get("Content-Type", ""),
                b"".join(chunks),
                resp.encoding or "utf-8",
                current,
            )
        finally:
            resp.close()
    raise WebFetchError(f"Trop de redirections (> {MAX_REDIRECTS}).")


def _html_to_text(html_str: str) -> tuple[str, str]:
    """Extrait (titre, texte) d'un document HTML, balises de bruit retirées."""
    soup = BeautifulSoup(html_str, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe"]):
        tag.decompose()
    raw = soup.get_text("\n")
    lines = [ln.strip() for ln in raw.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return title, text


def _ddg_decode_href(href: str) -> str:
    """Décode les liens de redirection DuckDuckGo (//duckduckgo.com/l/?uddg=…)."""
    if "uddg=" in href:
        q = parse_qs(urlparse(href).query)
        if "uddg" in q:
            return unquote(q["uddg"][0])
    if href.startswith("//"):
        return "https:" + href
    return href


def _parse_ddg(html_str: str, limit: int) -> list[dict]:
    """Parse la page de résultats HTML DuckDuckGo en [{title, url, snippet}]."""
    soup = BeautifulSoup(html_str, "html.parser")
    results: list[dict] = []
    for res in soup.select(".result"):
        a = res.select_one("a.result__a")
        if not a:
            continue
        snip = res.select_one(".result__snippet")
        results.append(
            {
                "title": a.get_text(" ", strip=True),
                "url": _ddg_decode_href(str(a.get("href", "") or "")),
                "snippet": snip.get_text(" ", strip=True) if snip else "",
            }
        )
        if len(results) >= limit:
            break
    return results


# ---------------------------------------------------------------------------- #
# Outils MCP                                                                    #
# ---------------------------------------------------------------------------- #


@mcp.tool()
def fetch_url(url: str, max_chars: int = MAX_CHARS) -> dict:
    """Récupère une page web en LECTURE SEULE (GET) et renvoie son contenu texte.

    Utile pour lire une doc, une page d'API, une issue GitHub, un message d'erreur.
    Aucune écriture, aucune donnée envoyée. Les IP privées/loopback sont refusées
    (protection SSRF), y compris via redirection ; le contenu binaire n'est pas extrait.

    Args:
        url: URL http(s) à lire.
        max_chars: Taille max du texte renvoyé (tronqué au-delà).

    Returns:
        {"url", "status", "content_type", "title", "truncated", "text"}
        ou {"error": "..."} en cas de refus / échec.
    """
    cap = max(500, min(int(max_chars), HARD_MAX_CHARS))
    try:
        status, ctype, raw, encoding, final_url = _fetch(url)
    except WebFetchError as exc:
        return {"error": str(exc)}
    except requests.RequestException as exc:
        return {"error": f"Échec réseau: {exc}"}
    except Exception as exc:
        logger.error("fetch_url: %s", exc, exc_info=True)
        return {"error": str(exc)}

    ctl = ctype.lower()
    is_html = "html" in ctl
    is_texty = (
        is_html
        or ctl.startswith("text/")
        or "json" in ctl
        or "xml" in ctl
        or ctype == ""
    )
    if not is_texty:
        return {
            "url": final_url,
            "status": status,
            "content_type": ctype,
            "title": "",
            "truncated": False,
            "text": "",
            "note": "Contenu non textuel (binaire) — non extrait.",
        }

    body = raw.decode(encoding or "utf-8", errors="replace")
    if is_html:
        title, text = _html_to_text(body)
    else:
        title, text = "", body
    return {
        "url": final_url,
        "status": status,
        "content_type": ctype,
        "title": title,
        "truncated": len(text) > cap,
        "text": text[:cap],
    }


@mcp.tool()
def web_search(query: str, limit: int = SEARCH_RESULTS) -> dict:
    """Recherche sur le web via DuckDuckGo (sans clé API) en LECTURE SEULE.

    Renvoie une liste de résultats (titre, URL, extrait). Utiliser fetch_url
    ensuite pour lire une page en détail.

    Args:
        query: Termes de recherche.
        limit: Nombre max de résultats.

    Returns:
        {"count", "results": [{"title", "url", "snippet"}, ...]} ou {"error": "..."}.
    """
    n = max(1, min(int(limit), 25))
    try:
        # GET + en-têtes navigateur : un UA contenant "bot" ou un POST brut
        # déclenche la page anti-bot de DuckDuckGo.
        resp = requests.get(
            _DDG_HTML_URL,
            params={"q": query},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        results = _parse_ddg(resp.text, n)
        if not results:
            if "challenge" in resp.text.lower():
                return {
                    "error": "DuckDuckGo a renvoyé une page anti-bot ; "
                    "réessayer plus tard ou espacer les requêtes."
                }
            return {"count": 0, "results": [], "note": "Aucun résultat."}
        return {"count": len(results), "results": results}
    except requests.RequestException as exc:
        return {"error": f"Échec réseau: {exc}"}
    except Exception as exc:
        logger.error("web_search: %s", exc, exc_info=True)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------- #
# Entrée principale                                                            #
# ---------------------------------------------------------------------------- #


def main() -> None:
    transport = os.getenv("WEB_MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("WEB_MCP_PORT", "8085"))
    host = os.getenv("WEB_MCP_HOST", "127.0.0.1")

    if transport == "http":
        logger.info("Web MCP HTTP : http://%s:%d", host, port)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("Web MCP stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
