"""SampleBrain MCP server — recherche sémantique dans la bibliothèque de samples.

Bras MCP du domaine SAMPLES, sibling de klody_music_server.py. Il ne charge
AUCUN modèle : il consomme le serveur local SampleBrain (`samplebrain-index
serve`, :8788, stdlib) qui tient l'index CLAP + le catalogue — même patron que
l'arm vocalbrain avec le daemon local-suno (:8766). Process séparé : isolation
crash/domaine, zéro dépendance lourde ici (httpx seulement).

Position vis-à-vis de `reaper_samples.py` (qui interroge le MÊME index depuis
le 2026-08-02) : deux usages, deux coûts. reaper_samples = PLACEMENT dans un
projet REAPER, moteur in-process (lancedb+torch dans le process, filtre
KLODY_SAMPLES_DIR). Ici = RECHERCHE pure exposée comme domaine, via HTTP —
rien de lourd n'entre dans ce process, et l'index n'est chargé qu'une fois,
côté serveur web. Si les deux répondent, c'est le même index ; les distances
sont comparables entre eux, pas avec le `score` tokens de reaper_samples.

Outils :
- chercher_samples(description, k) — texte libre → samples classés par
  similarité CLAP (« warm rhodes chords », « punchy kick », « dark trap piano »).
- statut_index() — vecteurs, modèle, fraîcheur du serveur.

Si le serveur SampleBrain ne répond pas, l'outil renvoie une erreur claire
avec la commande de démarrage — jamais de stacktrace.

Démarrage :
    python -m klody_mcp.samplebrain_server                             # stdio (défaut)
    SAMPLEBRAIN_MCP_TRANSPORT=http python -m klody_mcp.samplebrain_server  # :8094
"""
from __future__ import annotations

import logging
import os

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

logger = logging.getLogger(__name__)

# Le serveur web SampleBrain (samplebrain-index serve) — la seule dépendance.
SAMPLEBRAIN_URL = os.getenv("SAMPLEBRAIN_URL", "http://127.0.0.1:8788").rstrip("/")
TIMEOUT = float(os.getenv("SAMPLEBRAIN_MCP_TIMEOUT", "30"))

DEMARRAGE = (
    "Le serveur SampleBrain ne répond pas sur "
    f"{SAMPLEBRAIN_URL}. Le démarrer : cd ~/Projets/SampleBrain && "
    "~/.venvs/samplebrain/bin/python -m samplebrain.indexer.cli serve"
)

mcp = FastMCP("samplebrain")


def _request(path: str, params: dict | None = None) -> dict:
    """GET JSON vers le serveur SampleBrain. Erreur réseau → message actionnable."""
    try:
        response = httpx.get(f"{SAMPLEBRAIN_URL}{path}", params=params, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"SampleBrain a répondu {exc.response.status_code} sur {path}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(DEMARRAGE) from exc


@mcp.tool()
def chercher_samples(description: str, k: int = 10) -> dict:
    """Cherche des samples audio par description libre (similarité CLAP).

    Args:
        description: ce que doit évoquer le son — instrument, ambiance, style
            (« warm rhodes chords », « punchy kick drum », « nappe sombre »).
            L'anglais matche souvent mieux (CLAP est entraîné en anglais).
        k: nombre de résultats (1-50, défaut 10).

    Returns:
        requete, resultats[] (fichier, chemin, autres_chemins, distance —
        plus la distance est BASSE, plus le son colle à la description).
    """
    description = (description or "").strip()
    if not description:
        return {"erreur": "description vide"}
    k = max(1, min(50, int(k)))
    payload = _request("/api/search", {"q": description, "k": k})
    resultats = []
    for hit in payload.get("hits", []):
        paths = hit.get("paths") or []
        if not paths:
            continue
        resultats.append({
            "fichier": paths[0].rsplit("/", 1)[-1],
            "chemin": paths[0],
            "autres_chemins": paths[1:],
            "distance": round(float(hit.get("distance", 0.0)), 4),
        })
    return {"requete": description, "resultats": resultats}


@mcp.tool()
def statut_index() -> dict:
    """État de l'index SampleBrain : nombre de vecteurs, modèle, dossier d'état."""
    return _request("/api/status")


if __name__ == "__main__":
    transport = os.getenv("SAMPLEBRAIN_MCP_TRANSPORT", "stdio")
    if transport == "http":
        # 8094 : premier port libre du bloc MCP (8082 LB, 8084 gmail, 8085 web,
        # 8087 klody, 8088 musique, 8089 REAPER, 8093 gadget).
        mcp.run(transport="http", host="127.0.0.1",
                port=int(os.getenv("SAMPLEBRAIN_MCP_PORT", "8094")))
    else:
        mcp.run()
