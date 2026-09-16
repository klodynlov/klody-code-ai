"""Connecteur sample LOCAL (spec DAW agentique 8.8 : search -> rank -> import ->
place -> provenance).

Ce module ne fait que la partie search+rank (testable hors REAPER) ;
l'import/placement passe par le pont (`insert_media`) et la provenance est la
source rendue à l'appelant.

Deux moteurs de recherche, et le résultat dit TOUJOURS lequel a répondu
(champ `via`) :

* `samplebrain` — recherche SÉMANTIQUE. Interroge l'index CLAP construit par
  SampleBrain (dépôt séparé `~/Projets/SampleBrain`, index par défaut dans
  `~/.samplebrain`). C'est ce qui permet de chercher « nappe sombre
  cinématique » sans que ces mots figurent dans un nom de fichier.
* `filesystem` — recherche par TOKENS du nom de fichier. Balayage pur disque,
  aucune dépendance. C'est le repli, et il reste le seul moteur tant que
  SampleBrain n'est pas installé ou indexé.

⚠️ **SampleBrain reste OPTIONNEL, et n'entre plus dans ce process** (MISSION-D
6.2). Le module interroge en HTTP les serveurs `samplebrain-index serve`
déclarés par `SAMPLEBRAIN_URLS` (interne :8788 + externe :8799 en prod) — le
même chemin que `klody_mcp/samplebrain_server.py`. Plus de lancedb/torch ici,
plus d'index « principal seulement » : les sons du disque externe deviennent
plaçables. Serveur éteint → repli filesystem silencieux côté comportement,
LISIBLE via `semantic_status()`.

`KLODY_SAMPLEBRAIN=0` coupe le moteur sémantique sans rien désinstaller.

⚠️ **La tour texte de CLAP est ANGLOPHONE — une requête en français répond,
mais moins bien.** Mesuré sur l'index réel (654 fichiers), meilleur score et
meilleur résultat par paire :

    « nappe sombre cinématique »   0,393 → 05_Chant_80bpm.wav
    « dark cinematic pad »         0,447 → Deep Synth.wav
    « grosse caisse qui claque »   0,458 → 05_FX1_80bpm.wav
    « punchy kick drum »           0,509 → kickdrum2.wav
    « guitare acoustique arpégée » 0,387 → chords.wav
    « acoustic guitar arpeggio »   0,498 → keyfx.wav

L'anglais gagne sur les trois paires, et pas seulement au score : le résultat
français de « grosse caisse » est un FX, l'anglais est bien une grosse caisse.
Ce module ne traduit rien — traduire silencieusement une requête utilisateur
serait une transformation invisible de son intention. C'est à l'appelant de
savoir que l'anglais porte mieux. Le dépôt est en français, ce piège n'est donc
pas théorique.

⚠️ Les deux moteurs rendent un champ `score` qui n'a PAS la même échelle :
entier de tokens pour `filesystem`, similarité cosinus dans [0, 1] pour
`samplebrain`. « Plus grand = meilleur » dans les deux cas, mais comparer les
scores de deux `via` différents ne veut rien dire. Un même appel ne mélange
jamais les deux moteurs, précisément pour que ça n'arrive pas.

Racines de recherche : env KLODY_SAMPLES_DIR (chemins séparés par os.pathsep) ou,
à défaut, des dossiers usuels. On ne descend QUE dans des dossiers existants et on
borne le balayage (anti-hang sur une arbo géante). Les résultats sémantiques sont
filtrés sur ces mêmes racines : l'index peut couvrir plus large que ce que
l'appelant a demandé.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Extensions audio prises en compte (samples / one-shots / boucles).
_AUDIO_EXT = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".m4a", ".caf", ".wv"}

# Garde-fou : nombre max de fichiers balayés (une arbo de samples peut être énorme).
_MAX_SCAN = 50_000


def _default_roots() -> list[Path]:
    return [
        Path.home() / "Music" / "Samples",
        Path.home() / "Samples",
        Path.home() / "Music" / "Audio",
        Path.home() / "Documents" / "Samples",
    ]


def _roots(root: str | None = None) -> list[Path]:
    """Racines de recherche : `root` explicite, sinon env KLODY_SAMPLES_DIR, sinon
    défauts. Ne garde que les dossiers existants (dédupliqués)."""
    if root:
        raw = [root]
    else:
        env = os.getenv("KLODY_SAMPLES_DIR", "")
        raw = env.split(os.pathsep) if env.strip() else [str(p) for p in _default_roots()]
    out: list[Path] = []
    seen: set[str] = set()
    for r in raw:
        r = (r or "").strip()
        if not r:
            continue
        p = Path(r).expanduser()
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir():
            out.append(p)
    return out


# ---------------------------------------------------------------------------- #
# Pont SampleBrain — recherche sémantique, strictement optionnelle, via HTTP    #
# ---------------------------------------------------------------------------- #

# Modes acceptés par `search_samples`. `auto` = sémantique si disponible.
MODES = ("auto", "samplebrain", "filesystem")

# MISSION-D 6.2 : UN SEUL chemin d'accès à l'index. Avant, ce module ouvrait
# LanceDB + CLAP dans le process du serveur REAPER (600 Mo de poids, ~2,6 s,
# et il ne voyait que l'index interne). Il interroge désormais les mêmes
# serveurs `samplebrain-index serve` que `samplebrain_server.py`, déclarés
# par `SAMPLEBRAIN_URLS` — index interne ET externe, modèle chargé une fois.
_TIMEOUT_STATUT = float(os.getenv("SAMPLEBRAIN_STATUS_TIMEOUT", "3"))
_TIMEOUT_RECHERCHE = float(os.getenv("SAMPLEBRAIN_MCP_TIMEOUT", "30"))
_sb_derniere_erreur: str = ""


def _bibliotheques() -> dict[str, str]:
    from klody_mcp._samplebrain_urls import lire_bibliotheques
    return lire_bibliotheques()


def semantic_enabled() -> bool:
    """Interrupteur `KLODY_SAMPLEBRAIN` (0/false/off/no désactive).

    Deux usages, tous deux réels : couper le moteur sémantique en production
    sans rien arrêter, et rendre les tests HERMÉTIQUES. Sans lui,
    `test_reaper_samples.py` réussirait ou échouerait selon qu'un serveur
    SampleBrain tourne sur la machine.
    """
    return os.getenv("KLODY_SAMPLEBRAIN", "1").strip().lower() not in (
        "0", "false", "off", "no",
    )


def _get_json(url: str, params: dict | None, timeout: float) -> dict:
    """GET JSON — isolé pour être bouchonné par les tests. Lève sur tout échec."""
    import httpx

    response = httpx.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def semantic_status() -> dict:
    """Pourquoi la recherche sémantique répond — ou ne répond pas.

    Bon marché : un `/api/status` par bibliothèque, délai court. Sert à ce
    qu'une indisponibilité soit DIAGNOSTICABLE plutôt que silencieuse — un
    repli muet est indiscernable d'un moteur qui marche mal.
    """
    biblios = _bibliotheques()
    infos: dict[str, Any] = {"bibliotheques": biblios, "via": "http"}
    if not semantic_enabled():
        return {**infos, "available": False, "reason": "désactivé par KLODY_SAMPLEBRAIN"}
    vivantes, raisons = [], []
    for nom, base in biblios.items():
        try:
            etat = _get_json(f"{base}/api/status", None, _TIMEOUT_STATUT)
            vivantes.append(nom)
            infos.setdefault("modeles", {})[nom] = str(etat.get("model", "?"))
        except Exception as exc:  # réseau, HTTP, JSON : même diagnostic
            raisons.append(f"{nom} ({base}) : {type(exc).__name__}: {str(exc)[:120] or 'injoignable'}")
    infos["vivantes"] = vivantes
    if _sb_derniere_erreur:
        infos["derniere_erreur_recherche"] = _sb_derniere_erreur
    if not vivantes:
        return {**infos, "available": False,
                "reason": "aucun serveur SampleBrain ne répond — " + " ; ".join(raisons)
                + " ; démarrer : cd ~/Projets/SampleBrain && "
                  "~/.venvs/samplebrain/bin/samplebrain-index serve"}
    return {**infos, "available": True,
            "reason": "" if not raisons else "partiel : " + " ; ".join(raisons)}


def _similarite(distance: float) -> float:
    """Distance LanceDB -> similarité cosinus dans [0, 1].

    Vérifié sur l'index réel plutôt que supposé : les vecteurs CLAP sont
    L2-normalisés et `_distance` vaut exactement `2 - 2*cos` (concordance à
    1e-6 sur trois résultats). D'où `cos = 1 - d/2`. On borne à [0, 1] pour que
    « plus grand = meilleur » tienne sans exception.
    """
    return round(max(0.0, min(1.0, 1.0 - float(distance) / 2.0)), 4)


def _sous_racine(chemin: str, racines: list[Path]) -> Path | None:
    for base in racines:
        try:
            Path(chemin).relative_to(base)
        except ValueError:
            continue
        return base
    return None


def _hits_http(query: str, k: int) -> list[dict]:
    """Interroge chaque bibliothèque en `local=1` (pas d'essaimage serveur : on
    fusionne ici, et seulement si les modèles concordent — sinon on garde
    l'ordre par bibliothèque, distances non comparables). [] si tout se tait."""
    global _sb_derniere_erreur
    par_biblio: dict[str, list[dict]] = {}
    modeles: set[str] = set()
    erreurs = []
    for nom, base in _bibliotheques().items():
        try:
            payload = _get_json(f"{base}/api/search", {"q": query, "k": k, "local": "1"},
                                _TIMEOUT_RECHERCHE)
        except Exception as exc:
            erreurs.append(f"{nom}: {type(exc).__name__}")
            continue
        modeles.add(str(payload.get("model", "?")))
        par_biblio[nom] = [h for h in payload.get("hits", []) if h.get("paths")]
    _sb_derniere_erreur = " ; ".join(erreurs)
    if not par_biblio:
        return []
    if len(modeles) <= 1:
        return sorted((h for liste in par_biblio.values() for h in liste),
                      key=lambda h: float(h.get("distance") or 0.0))
    return [h for nom in sorted(par_biblio) for h in par_biblio[nom]]


def _search_samplebrain(query: str, racines: list[Path], limit: int) -> list[dict]:
    """Recherche sémantique. Rend [] si le moteur n'a rien à dire.

    Deux filtres après coup, et ils ne sont pas cosmétiques :

    * **par racine** — l'index couvre ce que SampleBrain a indexé, qui peut
      déborder de `KLODY_SAMPLES_DIR` ou du `root` demandé ;
    * **par existence** — un index est une vue dérivée, il peut avoir une
      longueur de retard sur le disque. Rendre un chemin mort ferait échouer
      `import_sample` plus loin, avec une erreur qui n'aurait plus rien à voir
      avec la recherche.
    """
    if not semantic_enabled():
        return []
    # On demande large : un contenu peut porter plusieurs chemins, et les deux
    # filtres ci-dessous en retirent encore.
    hits = _hits_http(query, k=max(4 * int(limit), 20))
    out: list[dict] = []
    vus: set[str] = set()
    for h in hits:
        score = _similarite(h.get("distance") or 0.0)
        for chemin in h.get("paths", []):
            if chemin in vus:
                continue
            base = _sous_racine(chemin, racines)
            if base is None or not os.path.exists(chemin):
                continue
            vus.add(chemin)
            out.append({
                "path": chemin,
                "name": os.path.basename(chemin),
                "rel": os.path.relpath(chemin, base),
                "root": str(base),
                "score": score,
                "via": "samplebrain",
            })
    return out[: max(1, int(limit))]


def _tokens(s: str) -> list[str]:
    """Découpe en tokens minuscules alphanumériques (sépare sur tout le reste)."""
    cur: list[str] = []
    out: list[str] = []
    for ch in s.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _score(query_tokens: list[str], stem: str, rel: str) -> int:
    """Score d'un fichier vs la requête : +3 si un token est dans le NOM de fichier,
    +1 si dans le CHEMIN relatif (dossier). 0 si aucun token ne matche."""
    stem_l = stem.lower()
    rel_l = rel.lower()
    sc = 0
    for t in query_tokens:
        if t in stem_l:
            sc += 3
        elif t in rel_l:
            sc += 1
    return sc


def search_samples(
    query: str, root: str | None = None, limit: int = 20, mode: str = "auto",
) -> list[dict]:
    """Cherche des samples audio sous les racines, classés par pertinence vs `query`.

    Renvoie [{"path","name","rel","root","score","via"}, ...] trié score
    décroissant (à score égal : nom le plus court d'abord). Provenance = `path`
    absolu (à passer à import_sample).

    `mode` :
      * `auto` (défaut) — sémantique si SampleBrain répond, sinon filesystem ;
      * `samplebrain` — sémantique exigée, [] si le moteur est indisponible ;
      * `filesystem` — tokens du nom de fichier, jamais d'index.

    ⚠️ Une requête VIDE part toujours au filesystem : « rien » n'a pas de
    voisin sémantique, alors que lister les premiers fichiers trouvés est un
    comportement utile que des appelants utilisent déjà.

    ⚠️ Le repli du mode `auto` est SILENCIEUX par conception (un connecteur
    REAPER ne doit pas casser parce qu'un index manque), mais jamais opaque :
    le champ `via` de chaque résultat dit quel moteur a répondu, et
    `semantic_status()` dit pourquoi l'autre s'est tu.
    """
    if mode not in MODES:
        raise ValueError(f"mode inconnu : {mode!r} (attendu : {', '.join(MODES)})")
    roots = _roots(root)
    qtokens = _tokens(query or "")

    if qtokens and mode in ("auto", "samplebrain"):
        semantiques = _search_samplebrain(query, roots, limit)
        # `auto` se rabat quand l'index est absent OU quand il ne connaît aucun
        # fichier sous les racines demandées ; `samplebrain` assume son vide.
        if semantiques or mode == "samplebrain":
            return semantiques

    scanned = 0
    hits: list[dict] = []
    for base in roots:
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in _AUDIO_EXT:
                    continue
                scanned += 1
                if scanned > _MAX_SCAN:
                    break
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, base)
                stem = os.path.splitext(fn)[0]
                sc = _score(qtokens, stem, rel) if qtokens else 0
                if qtokens and sc == 0:
                    continue  # avec une requête, on ne garde que ce qui matche
                hits.append({"path": full, "name": fn, "rel": rel, "root": str(base),
                             "score": sc, "via": "filesystem"})
            if scanned > _MAX_SCAN:
                break
        if scanned > _MAX_SCAN:
            break
    hits.sort(key=lambda d: (-d["score"], len(d["name"]), d["name"].lower()))
    return hits[: max(1, int(limit))]
