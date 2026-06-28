"""Connecteur sample LOCAL (spec DAW agentique 8.8 : search -> rank -> import ->
place -> provenance).

SampleBrain n'existe PAS dans le repo (ni daemon externe, contrairement à
VocalBrain->local-suno) : on remplit donc l'intention de la spec avec une
bibliothèque de samples sur le DISQUE. Ce module ne fait que la partie
search+rank (pur filesystem, testable hors REAPER) ; l'import/placement passe par
le pont (`insert_media`) et la provenance est la source rendue à l'appelant.

Racines de recherche : env KLODY_SAMPLES_DIR (chemins séparés par os.pathsep) ou,
à défaut, des dossiers usuels. On ne descend QUE dans des dossiers existants et on
borne le balayage (anti-hang sur une arbo géante).
"""
from __future__ import annotations

import os
from pathlib import Path

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


def search_samples(query: str, root: str | None = None, limit: int = 20) -> list[dict]:
    """Cherche des samples audio sous les racines, classés par pertinence vs `query`.

    Renvoie [{"path","name","rel","root","score"}, ...] trié score décroissant (à
    score égal : nom le plus court d'abord). `query` vide -> on liste (score 0) les
    premiers fichiers trouvés. Provenance = `path` absolu (à passer à import_sample).
    """
    roots = _roots(root)
    qtokens = _tokens(query or "")
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
                hits.append({"path": full, "name": fn, "rel": rel, "root": str(base), "score": sc})
            if scanned > _MAX_SCAN:
                break
        if scanned > _MAX_SCAN:
            break
    hits.sort(key=lambda d: (-d["score"], len(d["name"]), d["name"].lower()))
    return hits[: max(1, int(limit))]
