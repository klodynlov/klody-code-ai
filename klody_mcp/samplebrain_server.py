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

Position vis-à-vis de `reaper_samples.py` : deux usages, UN chemin. reaper_samples
= PLACEMENT dans un projet REAPER (filtre KLODY_SAMPLES_DIR, repli filesystem) ;
ici = RECHERCHE pure exposée comme domaine. Depuis MISSION-D 6.2 les deux
interrogent les mêmes serveurs HTTP (`SAMPLEBRAIN_URLS`, lus par
`_samplebrain_urls.py`) — plus de moteur in-process, plus d'index « principal
seulement » côté REAPER.

Outils :
- chercher_samples(description, k, bibliotheque) — texte libre → samples
  classés par similarité CLAP (« warm rhodes chords », « punchy kick »).
- chercher_samples_par_audio(chemin, k, classe, duree_max_sec, bibliotheque) —
  un FICHIER audio (one-shot, tranche de stem) → ses voisins par le son
  (MISSION-D 6.2), filtrables par famille de batterie et durée max.
- chercher_kicks_pour_morceau(job_id, k) — les 3 tranches de kick découpées
  par l'analyse `atelier` → fusion RRF de 3 recherches audio → k kicks
  one-shots (< 1 s) qui sonnent comme ceux du morceau.
- statut_index() — état de chaque bibliothèque déclarée.

Si un serveur ne répond pas, l'outil renvoie une erreur claire avec la commande
de démarrage — jamais de stacktrace.

Démarrage :
    python -m klody_mcp.samplebrain_server                             # stdio (défaut)
    SAMPLEBRAIN_MCP_TRANSPORT=http python -m klody_mcp.samplebrain_server  # :8094
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import sys as _sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

# Lancé en stdio par le pont MCP, `sys.path[0]` = `klody_mcp/` : la racine du
# dépôt doit être visible pour `import klody_mcp` (même piège que atelier_server).
_RACINE = str(Path(__file__).resolve().parent.parent)
if _RACINE not in _sys.path:
    _sys.path.insert(0, _RACINE)

from klody_mcp._pathguard import PathGuardViolation, safe_path  # noqa: E402
from klody_mcp._samplebrain_urls import lire_bibliotheques  # noqa: E402

load_dotenv()

logger = logging.getLogger(__name__)

TIMEOUT = float(os.getenv("SAMPLEBRAIN_MCP_TIMEOUT", "30"))
TOUTES = "toutes"
MAX_AUDIO_OCTETS = 20 * 1024 * 1024
CLASSES = ("kick", "snare", "clap", "hihat", "tom", "cymbal", "perc")  # miroir samplebrain/classes.py
ATELIER_CACHE_DIR = Path(os.getenv("ATELIER_CACHE_DIR", "~/.klody/atelier")).expanduser()
_JOB_ID = re.compile(r"^[0-9a-f]{16}-[a-z0-9_]+-(core|a2a)-hub-v\d+$")
RRF_K = 60  # constante classique de la fusion par rangs réciproques


_lire_bibliotheques = lire_bibliotheques  # compat : tests + appelants historiques


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


def _post_audio(octets: bytes, params: dict, base: str, nom: str,
                content_type: str = "audio/wav") -> dict:
    """POST des octets audio vers `/api/search_audio` d'UNE bibliothèque (local)."""
    try:
        response = httpx.post(f"{base}/api/search_audio", params=params, content=octets,
                              headers={"Content-Type": content_type}, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        detail = ""
        with contextlib.suppress(ValueError):
            detail = str(exc.response.json().get("error") or "")
        raise RuntimeError(
            f"SampleBrain ({nom}) a répondu {exc.response.status_code} sur /api/search_audio"
            + (f" : {detail}" if detail else "")
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(_demarrage(nom, base)) from exc


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
    out = {
        "fichier": chemins[0].rsplit("/", 1)[-1],
        "chemin": chemins[0],
        "autres_chemins": chemins[1:],
        "bibliotheque": nom,
        "distance": round(float(hit.get("distance", 0.0)), 4),
    }
    # Posés par /api/search_audio quand ses filtres ont tourné (6.2).
    for cle in ("classe", "secondes"):
        if hit.get(cle) is not None:
            out[cle] = hit[cle]
    return out


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


def _fusionner(par_biblio: dict[str, list[dict]], modeles: dict[str, str], k: int,
               cle: str = "distance") -> tuple[list[dict], bool]:
    """Classement commun si même modèle partout, sinon groupé par bibliothèque."""
    fusionnable = len(set(modeles.values())) <= 1
    if fusionnable:
        return sorted((r for liste in par_biblio.values() for r in liste),
                      key=lambda r: r[cle])[:k], True
    return [r for nom in sorted(par_biblio) for r in par_biblio[nom]], False


def _lire_audio(chemin: str) -> bytes:
    """Octets d'un fichier audio SOUS les racines autorisées, borné en taille."""
    p = safe_path(chemin)  # PathGuardViolation / FileNotFoundError remontent
    taille = p.stat().st_size
    if taille == 0 or taille > MAX_AUDIO_OCTETS:
        raise RuntimeError(f"fichier vide ou > {MAX_AUDIO_OCTETS // (1024 * 1024)} Mo : {p.name}")
    return p.read_bytes()


def _chercher_par_octets(octets: bytes, k: int, classe: str, duree_max_sec: float,
                         cibles: list[tuple[str, str]]) -> dict:
    params: dict = {"k": k}
    if classe:
        params["classe"] = classe
    if duree_max_sec > 0:
        params["duree_max"] = duree_max_sec
    par_biblio: dict[str, list[dict]] = {}
    modeles: dict[str, str] = {}
    muettes: list[dict] = []
    for nom, url in cibles:
        try:
            payload = _post_audio(octets, params, url, nom)
        except RuntimeError as exc:
            muettes.append({"bibliotheque": nom, "raison": str(exc)})
            continue
        modeles[nom] = str(payload.get("model", "")) or "?"
        par_biblio[nom] = [_formater(h, nom) for h in payload.get("hits", []) if h.get("paths")]
    resultats, fusionnable = _fusionner(par_biblio, modeles, k)
    out: dict = {"resultats": resultats, "classement_fusionne": fusionnable,
                 "bibliotheques_interrogees": [n for n, _ in cibles]}
    if muettes:
        out["bibliotheques_muettes"] = muettes
    return out


@mcp.tool()
def chercher_samples_par_audio(chemin: str, k: int = 10, classe: str = "",
                               duree_max_sec: float = 0.0,
                               bibliotheque: str = TOUTES) -> dict:
    """Cherche des samples qui SONNENT comme un fichier audio donné (CLAP audio↔audio).

    Args:
        chemin: fichier audio local (one-shot, tranche de stem, sample de référence),
            sous les racines autorisées (~/Music, ~/Documents, ~/Projets, ~/.klody…).
            ≤ 20 Mo ; seules les 10 premières secondes comptent.
        k: nombre de résultats (1-50, défaut 10).
        classe: famille de batterie exigée, lue dans le chemin du sample :
            kick | snare | clap | hihat | tom | cymbal | perc (vide = sans filtre).
        duree_max_sec: durée maximale du sample rendu (ex. 1.0 = one-shots) ;
            0 = sans filtre. Un fichier dont l'en-tête est illisible est écarté.
        bibliotheque: « toutes » (défaut) ou un nom — voir `statut_index()`.

    Returns:
        resultats[] (fichier, chemin, autres_chemins, bibliotheque, distance —
        plus BASSE = plus proche ; classe, secondes quand filtrés),
        classement_fusionne, bibliotheques_interrogees, bibliotheques_muettes.
    """
    k = max(1, min(50, int(k)))
    classe = (classe or "").strip().lower()
    if classe and classe not in CLASSES:
        return {"erreur": f"classe inconnue : {classe!r} (connues : {', '.join(CLASSES)})"}
    try:
        octets = _lire_audio(chemin)
    except (PathGuardViolation, FileNotFoundError, RuntimeError, OSError) as exc:
        return {"erreur": f"{type(exc).__name__}: {exc}"}
    cibles = _cibles(bibliotheque)
    out = _chercher_par_octets(octets, k, classe, float(duree_max_sec or 0), cibles)
    out["requete"] = {"chemin": chemin, "classe": classe or None,
                      "duree_max_sec": duree_max_sec or None}
    return out


@mcp.tool()
def chercher_kicks_pour_morceau(job_id: str, k: int = 3, bibliotheque: str = TOUTES) -> dict:
    """Kicks one-shots de la bibliothèque qui sonnent comme ceux d'un morceau analysé.

    Enchaîne : tranches `kicks/kick_0N.wav` découpées par `atelier.analyser_morceau`
    (O&F, kicks isolés) → une recherche audio par tranche (classe=kick,
    durée < 1 s) → fusion par rangs réciproques (RRF) → `k` kicks. Déterministe.

    Args:
        job_id: id rendu par `analyser_morceau` / `lister_analyses`.
        k: nombre de kicks (1-10, défaut 3).
        bibliotheque: « toutes » (défaut) ou un nom.

    Returns:
        kicks[] (fichier, chemin, bibliotheque, secondes, score_rrf, distance_min,
        tranches_votantes), tranches[] (t_sec, conf), bibliotheques_muettes.
        Si le morceau n'a pas de kick transcrit : {"erreur"} — on n'invente pas.
    """
    k = max(1, min(10, int(k)))
    if not _JOB_ID.match(job_id or ""):
        return {"erreur": f"job_id invalide : {job_id!r}"}
    meta_path = ATELIER_CACHE_DIR / job_id / "kicks" / "kicks.json"
    if not meta_path.is_file():
        return {"erreur": "aucune tranche de kick pour cette analyse (0 kick transcrit, "
                          "module `kicks` rouge, ou analyse antérieure à hub-v2 → relancer "
                          "`analyser_morceau`)", "job_id": job_id}
    import json
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {"erreur": f"kicks.json illisible : {exc}"}
    tranches = meta.get("tranches") or []
    if not tranches:
        return {"erreur": "kicks.json sans tranche", "job_id": job_id}

    cibles = _cibles(bibliotheque)
    scores: dict[str, dict] = {}
    muettes: list[dict] = []
    for i, tr in enumerate(tranches, 1):
        try:
            octets = _lire_audio(str(tr.get("path") or ""))
        except (PathGuardViolation, FileNotFoundError, RuntimeError, OSError) as exc:
            muettes.append({"tranche": i, "raison": f"{type(exc).__name__}: {exc}"})
            continue
        rep = _chercher_par_octets(octets, 10, "kick", 1.0, cibles)
        muettes.extend(rep.get("bibliotheques_muettes", []))
        vus_ici: set[str] = set()
        for rang, r in enumerate(rep["resultats"], 1):
            if r["chemin"] in vus_ici:      # même son rendu deux fois pour cette tranche
                continue
            vus_ici.add(r["chemin"])
            e = scores.setdefault(r["chemin"], {**r, "score_rrf": 0.0, "distance_min": r["distance"],
                                                 "tranches_votantes": []})
            e["score_rrf"] += 1.0 / (RRF_K + rang)
            e["distance_min"] = min(e["distance_min"], r["distance"])
            e["tranches_votantes"].append(i)
    kicks = sorted(scores.values(), key=lambda e: (-e["score_rrf"], e["distance_min"]))[:k]
    for e in kicks:
        e["score_rrf"] = round(e["score_rrf"], 5)
        e.pop("distance", None)
    out: dict = {"job_id": job_id, "kicks": kicks,
                 "tranches": [{"t_sec": t.get("t_sec"), "conf": t.get("conf"),
                               "path": t.get("path")} for t in tranches],
                 "n_kicks_dans_le_morceau": meta.get("n_kicks_total"),
                 "bibliotheques_interrogees": [n for n, _ in cibles]}
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
