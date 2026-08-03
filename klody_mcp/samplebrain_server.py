"""SampleBrain MCP server — recherche sémantique dans les bibliothèques de samples.

Bras MCP du domaine SAMPLES, sibling de klody_music_server.py. Il ne charge
AUCUN modèle : il consomme les serveurs locaux SampleBrain (`samplebrain-index
serve`, stdlib) qui tiennent l'index CLAP + le catalogue — même patron que
l'arm vocalbrain avec le daemon local-suno (:8766). Process séparé : isolation
crash/domaine, zéro dépendance lourde ici (httpx seulement).

**Plusieurs bibliothèques.** Un index par corpus, chacun servi par son propre
serveur : `principale` (:8788, disque interne) et `externe` (:8799, index dédié
au disque de samples externe — 69 384 sons, soit l'essentiel de la
bibliothèque). Elles sont déclarées par `SAMPLEBRAIN_URLS` et interrogées
ensemble par défaut.

⚠️ **Fusionner deux index n'est légitime que s'ils partagent le modèle.** La
distance CLAP est comparable d'un index à l'autre à condition que le backend
soit le même ; sinon on classerait ensemble deux échelles différentes — le
piège déjà documenté pour `score` dans `reaper_samples.py`. Les modèles sont
donc vérifiés, et un désaccord DÉGRADE en résultats séparés plutôt que de
produire un classement muet et faux.

Une bibliothèque injoignable n'interrompt pas la recherche : les autres
répondent, et `bibliotheques_muettes` dit laquelle s'est tue et pourquoi.

Position vis-à-vis de `reaper_samples.py` (qui interroge le MÊME index
principal depuis le 2026-08-02) : deux usages, deux coûts. reaper_samples =
PLACEMENT dans un projet REAPER, moteur in-process (lancedb+torch dans le
process, filtre KLODY_SAMPLES_DIR). Ici = RECHERCHE pure exposée comme domaine,
via HTTP — rien de lourd n'entre dans ce process, et l'index n'est chargé
qu'une fois, côté serveur web. ⚠️ `reaper_samples` ne voit QUE l'index
principal : les sons du disque externe ne sont pas plaçables par ce chemin-là.

Outils :
- chercher_samples(description, k, bibliotheque) — texte libre → samples
  classés par similarité CLAP (« warm rhodes chords », « punchy kick »).
- statut_index() — état de chaque bibliothèque déclarée.

Si un serveur ne répond pas, l'outil renvoie une erreur claire avec la commande
de démarrage — jamais de stacktrace.

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

TIMEOUT = float(os.getenv("SAMPLEBRAIN_MCP_TIMEOUT", "30"))
TOUTES = "toutes"


def _lire_bibliotheques() -> dict[str, str]:
    """{nom: url} depuis `SAMPLEBRAIN_URLS` (`nom=url,nom=url`).

    Repli sur `SAMPLEBRAIN_URL` seul, sous le nom `principale` : la variable
    historique reste donc valide telle quelle.
    """
    brut = os.getenv("SAMPLEBRAIN_URLS", "").strip()
    if not brut:
        seule = os.getenv("SAMPLEBRAIN_URL", "http://127.0.0.1:8788")
        return {"principale": seule.rstrip("/")}
    out: dict[str, str] = {}
    for morceau in brut.split(","):
        morceau = morceau.strip()
        if not morceau:
            continue
        nom, _, url = morceau.partition("=")
        nom, url = nom.strip(), url.strip().rstrip("/")
        if not nom or not url:
            logger.warning("SAMPLEBRAIN_URLS : entrée ignorée (%r)", morceau)
            continue
        out[nom] = url
    return out or {"principale": "http://127.0.0.1:8788"}


BIBLIOTHEQUES = _lire_bibliotheques()


def _demarrage(nom: str, url: str) -> str:
    return (
        f"La bibliothèque {nom!r} ne répond pas sur {url}. La démarrer : "
        "cd ~/Projets/SampleBrain && ~/.venvs/samplebrain/bin/python "
        "-m samplebrain.indexer.cli serve"
    )


mcp = FastMCP("samplebrain")


def _request(path: str, params: dict | None = None,
             base: str | None = None, nom: str = "principale") -> dict:
    """GET JSON vers un serveur SampleBrain. Erreur réseau → message actionnable."""
    url = base or next(iter(BIBLIOTHEQUES.values()))
    try:
        response = httpx.get(f"{url}{path}", params=params, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"SampleBrain ({nom}) a répondu {exc.response.status_code} sur {path}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(_demarrage(nom, url)) from exc


def _cibles(bibliotheque: str) -> list[tuple[str, str]]:
    """(nom, url) à interroger. Lève si le nom demandé n'existe pas — se taire
    rendrait une liste vide indiscernable d'un corpus sans résultat."""
    if bibliotheque in (TOUTES, "", None):
        return list(BIBLIOTHEQUES.items())
    if bibliotheque not in BIBLIOTHEQUES:
        connues = ", ".join(sorted(BIBLIOTHEQUES)) or "aucune"
        raise RuntimeError(
            f"bibliothèque inconnue : {bibliotheque!r} (connues : {connues}, "
            f"ou {TOUTES!r})"
        )
    return [(bibliotheque, BIBLIOTHEQUES[bibliotheque])]


def _formater(hit: dict, nom: str) -> dict:
    """Un hit du serveur web → la forme rendue par l'outil.

    `paths` groupe déjà les chemins d'un MÊME contenu (même SHA-256) ; le
    serveur regroupe en plus les quasi-doublons (même son, encodages
    différents) et les expose dans `variants`. Les deux sont aplatis ici en
    `autres_chemins` : pour un agent qui cherche un fichier à placer, la
    distinction interne ne change rien — il lui faut un chemin qui marche.
    """
    chemins = list(hit.get("paths") or [])
    for variante in hit.get("variants") or []:
        chemins.extend(variante.get("paths") or [])
    return {
        "fichier": chemins[0].rsplit("/", 1)[-1],
        "chemin": chemins[0],
        "autres_chemins": chemins[1:],
        "bibliotheque": nom,
        "distance": round(float(hit.get("distance", 0.0)), 4),
    }


@mcp.tool()
def chercher_samples(description: str, k: int = 10,
                     bibliotheque: str = TOUTES) -> dict:
    """Cherche des samples audio par description libre (similarité CLAP).

    Args:
        description: ce que doit évoquer le son — instrument, ambiance, style
            (« warm rhodes chords », « punchy kick drum », « dark trap piano »).
            Essayer les DEUX langues : l'anglais gagne souvent, mais pas
            toujours (« percussions congas latines » bat « latin conga
            percussion » sur la bibliothèque externe).
        k: nombre de résultats (1-50, défaut 10).
        bibliotheque: « toutes » (défaut), ou le nom d'une seule — voir
            `statut_index()` pour la liste. La bibliothèque externe porte
            l'essentiel du corpus.

    Returns:
        requete, resultats[] (fichier, chemin, autres_chemins, bibliotheque,
        distance — plus la distance est BASSE, plus le son colle), et
        `bibliotheques_muettes` quand l'une d'elles n'a pas répondu.
        `classement_fusionne` dit si les bibliothèques ont pu être classées
        ensemble ; à False, `resultats` reste groupé par bibliothèque.
    """
    description = (description or "").strip()
    if not description:
        return {"erreur": "description vide"}
    k = max(1, min(50, int(k)))

    cibles = _cibles(bibliotheque)
    par_biblio: dict[str, list[dict]] = {}
    modeles: dict[str, str] = {}
    muettes: list[dict] = []

    for nom, url in cibles:
        try:
            payload = _request("/api/search", {"q": description, "k": k},
                               base=url, nom=nom)
        except RuntimeError as exc:
            # Une bibliothèque éteinte ne doit pas emporter les autres : le
            # disque externe n'est pas toujours branché, et son serveur peut
            # être arrêté sans que la bibliothèque interne cesse de répondre.
            muettes.append({"bibliotheque": nom, "raison": str(exc)})
            continue
        modeles[nom] = str(payload.get("model", "")) or "?"
        par_biblio[nom] = [
            _formater(hit, nom)
            for hit in payload.get("hits", [])
            if hit.get("paths")
        ]

    # ⚠️ Classer ensemble deux index suppose la MÊME échelle, donc le même
    # backend d'embedding. Sinon on rendrait un classement d'apparence normale
    # et sans signification — on préfère annoncer qu'on n'a pas fusionné.
    distincts = set(modeles.values())
    fusionnable = len(distincts) <= 1

    if fusionnable:
        resultats = sorted(
            (r for liste in par_biblio.values() for r in liste),
            key=lambda r: r["distance"],
        )[:k]
    else:
        resultats = [r for nom in sorted(par_biblio) for r in par_biblio[nom]]

    out: dict = {
        "requete": description,
        "resultats": resultats,
        "classement_fusionne": fusionnable,
        "bibliotheques_interrogees": [nom for nom, _ in cibles],
    }
    if not fusionnable:
        out["avertissement"] = (
            "modèles d'embedding différents entre bibliothèques "
            f"({sorted(distincts)}) : distances NON comparables, résultats "
            "laissés groupés par bibliothèque"
        )
    if muettes:
        out["bibliotheques_muettes"] = muettes
    return out


@mcp.tool()
def statut_index() -> dict:
    """État de chaque bibliothèque : vecteurs, modèle, fraîcheur.

    Une bibliothèque injoignable est signalée sans faire échouer les autres —
    savoir laquelle est tombée fait partie du statut.
    """
    out: dict = {"bibliotheques": {}}
    for nom, url in BIBLIOTHEQUES.items():
        try:
            etat = _request("/api/status", base=url, nom=nom)
            etat["url"] = url
            out["bibliotheques"][nom] = etat
        except RuntimeError as exc:
            out["bibliotheques"][nom] = {"url": url, "erreur": str(exc)}
    return out


if __name__ == "__main__":
    transport = os.getenv("SAMPLEBRAIN_MCP_TRANSPORT", "stdio")
    if transport == "http":
        # 8094 : premier port libre du bloc MCP (8082 LB, 8084 gmail, 8085 web,
        # 8087 klody, 8088 musique, 8089 REAPER, 8093 gadget).
        mcp.run(transport="http", host="127.0.0.1",
                port=int(os.getenv("SAMPLEBRAIN_MCP_PORT", "8094")))
    else:
        mcp.run()
