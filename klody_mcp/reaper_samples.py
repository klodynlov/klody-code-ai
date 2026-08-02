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

⚠️ **SampleBrain est une dépendance strictement OPTIONNELLE et non déclarée.**
Elle tire `lancedb` (et, pour embedder la requête, `torch`/`transformers`) ;
l'imposer au dépôt ferait payer ce poids à tout le monde pour un connecteur
REAPER. L'import est donc tenté à l'exécution, isolément — jamais dans un `try`
groupé avec autre chose, sinon une absence en emporterait une autre (piège
vécu avec `numpy` importé dans le `try` de `librosa`). Toute indisponibilité
est silencieuse côté comportement mais LISIBLE via `semantic_status()`.

Pour l'activer dans le venv qui fait tourner le serveur MCP :

    pip install -e ~/Projets/SampleBrain          # lancedb + pyarrow
    samplebrain-index --roots "$KLODY_SAMPLES_DIR" index

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
# Pont SampleBrain — recherche sémantique, strictement optionnelle              #
# ---------------------------------------------------------------------------- #

# Modes acceptés par `search_samples`. `auto` = sémantique si disponible.
MODES = ("auto", "samplebrain", "filesystem")

# L'index et le modèle CLAP sont gardés chauds entre deux appels : mesuré dans
# le venv de ce dépôt (transformers 5.9.0), le premier appel paie ~2,6 s de
# chargement de poids, les suivants ~0,03 s. Rouvrir à chaque recherche
# rendrait l'outil MCP inutilisable.
_sb_indexer: Any = None
_sb_derniere_erreur: str = ""


def _samplebrain_home() -> Path:
    """Dossier d'état de SampleBrain (catalogue + index vectoriel)."""
    return Path(os.getenv("SAMPLEBRAIN_HOME") or (Path.home() / ".samplebrain")).expanduser()


def semantic_enabled() -> bool:
    """Interrupteur `KLODY_SAMPLEBRAIN` (0/false/off/no désactive).

    Deux usages, tous deux réels : couper le moteur sémantique en production
    sans désinstaller quoi que ce soit, et rendre les tests HERMÉTIQUES. Sans
    lui, `test_reaper_samples.py` réussirait ou échouerait selon qu'un index
    traîne dans le `$HOME` de la machine qui l'exécute — vert en CI, lent et
    imprévisible en local.
    """
    return (os.getenv("KLODY_SAMPLEBRAIN", "1") or "").strip().lower() not in (
        "0", "false", "off", "no",
    )


def semantic_status() -> dict:
    """Pourquoi la recherche sémantique répond — ou ne répond pas.

    Volontairement bon marché : n'importe que le paquet `samplebrain` (dont
    l'`__init__` ne tire ni torch ni lancedb) et vérifie l'existence du
    catalogue. Sert à ce qu'une indisponibilité soit DIAGNOSTICABLE plutôt que
    silencieuse — un repli muet est indiscernable d'un moteur qui marche mal.
    """
    home = _samplebrain_home()
    infos: dict[str, Any] = {"home": str(home), "charge": _sb_indexer is not None}
    if not semantic_enabled():
        return {**infos, "available": False,
                "reason": "désactivé par KLODY_SAMPLEBRAIN"}
    try:
        import samplebrain  # noqa: F401  (import isolé : voir le module docstring)
    except Exception as exc:  # ImportError, mais aussi toute erreur d'init
        return {**infos, "available": False,
                "reason": f"paquet samplebrain absent ou cassé : {type(exc).__name__}: {exc}"}
    if not (home / "catalog.sqlite3").exists():
        return {**infos, "available": False,
                "reason": f"aucun index dans {home} — lancer `samplebrain-index index`"}
    if _sb_derniere_erreur:
        return {**infos, "available": False, "reason": _sb_derniere_erreur}
    return {**infos, "available": True, "reason": ""}


def _samplebrain_indexer() -> Any:
    """Ouvre l'index une fois pour toutes. Rend None si indisponible.

    L'échec n'est pas mis en cache : un index construit après le démarrage du
    serveur MCP doit pouvoir être pris en compte sans redémarrage. Le coût d'un
    nouvel essai est un `import` déjà résolu et deux `stat`.
    """
    global _sb_indexer, _sb_derniere_erreur
    if _sb_indexer is not None:
        return _sb_indexer
    if not semantic_status()["available"]:
        return None
    try:
        from samplebrain.indexer.config import load_config
        from samplebrain.indexer.service import Indexer

        _sb_indexer = Indexer.open(load_config(home=_samplebrain_home()))
        _sb_derniere_erreur = ""
    except Exception as exc:
        _sb_derniere_erreur = f"ouverture de l'index impossible : {type(exc).__name__}: {exc}"
        _sb_indexer = None
    return _sb_indexer


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
    ix = _samplebrain_indexer()
    if ix is None:
        return []
    # On demande large : un contenu peut porter plusieurs chemins, et les deux
    # filtres ci-dessous en retirent encore.
    hits = ix.search_text(query, k=max(4 * int(limit), 20))
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
