"""Klody's web discovery -> LibraryBrain acquisition bridge.

Only URLs reach the importer: generated prose and search snippets cannot be
passed as source content. LibraryBrain downloads and versions the full document.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit

import httpx
from config import LIBRARYBRAIN_URL, librarybrain_auth_hint, librarybrain_headers

_BASE = LIBRARYBRAIN_URL.rsplit("/api/", 1)[0]
_ROAD_DOMAINS = ("legifrance.gouv.fr", "service-public.gouv.fr", "service-public.fr", "securite-routiere.gouv.fr")


def _service_public_search(query: str, limit: int) -> dict:
    """Fallback to the official site's own public search form, without API keys."""
    import requests
    from bs4 import BeautifulSoup

    # Depuis l'automne 2026, /particuliers/recherche ne liste plus que les démarches
    # en ligne ; les fiches « vosdroits » sont sur la recherche générale.
    url = "https://www.service-public.gouv.fr/particuliers/recherche-generale"
    query = re.sub(r"\bservice[ -]public\b|site:\S+", "", query, flags=re.I).strip()
    try:
        with requests.get(url, params={"keyword": query},
                          timeout=15, stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                return {"error": f"Recherche Service Public indisponible (HTTP {response.status_code})."}
            parts, total = [], 0
            for part in response.iter_content(65536):
                total += len(part)
                if total > 2_000_000:
                    return {"error": "Réponse du moteur officiel trop volumineuse."}
                parts.append(part)
            soup = BeautifulSoup(b"".join(parts), "html.parser")
        results, seen = [], set()
        for anchor in soup.select('main a[href*="/particuliers/vosdroits/"], main a[href*="/particuliers/actualites/"]'):
            target = urljoin(url, str(anchor.get("href") or ""))
            if urlsplit(target).hostname != "www.service-public.gouv.fr" or target in seen:
                continue
            seen.add(target)
            results.append({"title": anchor.get_text(" ", strip=True), "url": target,
                            "snippet": "", "official_domain": True})
            if len(results) >= limit:
                break
        return {"results": results}
    except requests.RequestException:
        return {"error": "Le moteur Service Public n'est pas accessible."}


def _request(method, path, **kwargs):
    try:
        with httpx.Client(timeout=150, headers=librarybrain_headers(), follow_redirects=False) as client:
            response = client.request(method, _BASE + path, **kwargs)
            if response.status_code == 401:
                return {"error": librarybrain_auth_hint()}
            if response.status_code == 404:
                return {"error": "API des sources web indisponible : mettre à jour/redémarrer LibraryBrain."}
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc.response.status_code))
        except ValueError:
            detail = str(exc.response.status_code)
        return {"error": detail}
    except httpx.TimeoutException:
        return {"error": "Délai dépassé ; vérifier library_source_status avant de relancer l'import."}
    except (httpx.RequestError, ValueError):
        return {"error": "LibraryBrain inaccessible ou réponse invalide."}


def library_web_search(query: str, limit: int = 5, official_only: bool = False) -> str:
    """Discover URLs; road-law queries automatically filter official domains."""
    from klody_mcp.web_server import web_search

    if not query.strip():
        return json.dumps({"error": "Requête vide."}, ensure_ascii=False)
    official_only = official_only or bool(re.search(r"code\s+de\s+la\s+route|droit\s+routier|légifrance|legifrance", query, re.I))
    n = max(1, min(int(limit), 10))
    effective = query
    if official_only:
        effective += " (" + " OR ".join("site:" + d for d in _ROAD_DOMAINS) + ")"
    data = web_search(effective, limit=25)
    results, seen = [], set()
    for result in data.get("results", []):
        url = result.get("url", "")
        p = urlsplit(url)
        host = (p.hostname or "").lower().rstrip(".")
        if p.scheme not in ("http", "https") or not host or p.username or p.password:
            continue
        # DDG may include sponsored results before organic hits, even with site:.
        if host in ("duckduckgo.com", "www.duckduckgo.com", "bing.com", "www.bing.com"):
            continue
        official = any(host == d or host.endswith("." + d) for d in _ROAD_DOMAINS)
        if (official_only and not official) or url in seen:
            continue
        seen.add(url)
        results.append({**result, "official_domain": official})
        if len(results) >= n:
            break
    provider = data.get("provider", "duckduckgo")
    error = data.get("error")
    warning = error or data.get("warning")
    if not results and official_only:
        fallback = _service_public_search(query, n)
        if fallback.get("error"):
            return json.dumps({"error": fallback["error"], "initial_error": warning}, ensure_ascii=False)
        results = fallback["results"]
        provider = "service-public"
        warning = "Recherche de secours limitée au site Service Public ; Légifrance et Sécurité routière ne sont pas couverts par ce résultat."
    elif not results and error:
        return json.dumps({"error": error}, ensure_ascii=False)
    return json.dumps({"query": query, "official_only": official_only, "count": len(results),
                       "provider": provider, "warning": warning,
                       "results": results, "notice": "Résultats de découverte, pas des sources indexées. Importer l'URL choisie avec library_import_source ; ne jamais présenter l'extrait comme le document complet."}, ensure_ascii=False)


def library_import_source(url: str, official_only: bool = False) -> str:
    return json.dumps(_request("POST", "/api/web-sources/import", json={"url": url, "official_only": official_only}), ensure_ascii=False)


def library_refresh_source(book_id: int) -> str:
    return json.dumps(_request("POST", f"/api/web-sources/{int(book_id)}/refresh"), ensure_ascii=False)


def library_source_status(book_id: int | None = None) -> str:
    return json.dumps(_request("GET", "/api/web-sources", params={"book_id": book_id} if book_id else {}), ensure_ascii=False)


def _tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}}


LIBRARY_SOURCE_TOOLS = [
    _tool("library_web_search", "Cherche sur le web des sources à ajouter/actualiser dans LibraryBrain. Le Code de la route filtre les domaines officiels. Retourne des URL et extraits de découverte, sans import.",
          {"query": {"type": "string"}, "limit": {"type": "integer", "default": 5},
           "official_only": {"type": "boolean", "default": False}}, ["query"]),
    _tool("library_import_source", "Importe une URL HTML, texte ou PDF dans LibraryBrain à la demande de l'utilisateur. Téléchargement intégral, provenance, date, version et index lexical. Même URL inchangée = pas de doublon. En droit routier utiliser official_only=true. Une fiche ne remplace pas un code complet.",
          {"url": {"type": "string"}, "official_only": {"type": "boolean", "default": False}}, ["url"]),
    _tool("library_refresh_source", "Vérifie l'URL d'une source web déjà suivie et publie une nouvelle version seulement si le contenu change. Anciennes copies conservées. Utiliser le book_id retourné par import/status.",
          {"book_id": {"type": "integer"}}, ["book_id"]),
    _tool("library_source_status", "Liste les sources web suivies ; avec book_id donne URL, dates, historique, erreurs, passages et état réel d'indexation. Une date de récupération ne certifie pas le droit en vigueur.",
          {"book_id": {"type": "integer"}}, []),
]
