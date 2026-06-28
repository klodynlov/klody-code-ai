"""Registre des plugins REAPER installés (spec DAW agentique 7.6 : « détecter le
réel, ne JAMAIS supposer un plugin présent »).

Pas de dépendance REAPER ni d'appel au pont : on lit directement les fichiers de
cache plugins que REAPER maintient dans son dossier ressource —
  - reaper-vstplugins_<arch>.ini  (section [vstcache], 1 ligne/VST)
  - reaper-jsfx.ini               (lignes `NAME <path> "JS: <nom>"`)
C'est robuste (aucune dépendance au binding swig EnumInstalledFX, qui n'expose pas
les noms via ce build) et testable hors REAPER (on parse un fichier).

Sert à deux choses :
  1. lister ce qui est installé (list_installed_fx) — l'agent voit le réel.
  2. résoudre un RÔLE musical ("eq","comp","reverb"…) vers le MEILLEUR plugin
     installé (resolve_plugin) — en préférant les plugins de l'utilisateur
     (KaribVoice / KlodVoice : sa chaîne voix caribéenne) au stock Rea*.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Dossier ressource REAPER (macOS par défaut). Override possible pour les tests.
_DEFAULT_RESOURCE = Path.home() / "Library" / "Application Support" / "REAPER"

# Familles de fichiers cache VST selon l'arch (REAPER en nomme une par build).
_VST_INIS = (
    "reaper-vstplugins_arm64.ini", "reaper-vstplugins64.ini",
    "reaper-vstplugins.ini", "reaper-vstplugins_x64.ini",
)
_JSFX_INI = "reaper-jsfx.ini"


def _vst_display_name(rest: str) -> str:
    """Nom AFFICHÉ d'une entrée vstcache. La valeur (après `=`) est
    `<hash>,<id>{<guid>,<NOM>` ou `<hash>,<id>,<NOM>`. On coupe APRÈS la virgule
    STRUCTURELLE (pas la dernière) pour préserver une virgule éventuelle DANS le nom
    (ex. « Comp, Vintage (Vendor) »)."""
    if "{" in rest:
        after = rest.split("{", 1)[1]  # "<guid>,<NOM...>"
        return after.split(",", 1)[1].strip() if "," in after else after.strip()
    parts = rest.split(",", 2)  # ["<hash>", "<id>", "<NOM...>"]
    return parts[2].strip() if len(parts) >= 3 else parts[-1].strip()

# Rôles musicaux -> mots-clés (minuscule, sous-chaîne). Ordre = priorité de match.
_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "eq": ("eq", "equal"),
    "comp": ("comp",),
    "deesser": ("deess", "de-ess", "de ess", "esser"),
    "reverb": ("reverb", "verb", "verbate"),
    "delay": ("delay", "echo"),
    "gate": ("gate",),
    "limiter": ("limit",),
    "saturation": ("satur", "drive", "tape", "warm"),
    "harmonics": ("harmonic", "exciter", "aural"),
}


def _resource_dir() -> Path:
    """Dossier ressource REAPER. Env KLODY_REAPER_RESOURCE prioritaire (tests/Linux)."""
    env = os.getenv("KLODY_REAPER_RESOURCE")
    return Path(env) if env else _DEFAULT_RESOURCE


def _parse_vst_ini(path: Path) -> list[dict]:
    """Parse un reaper-vstplugins_*.ini : section [vstcache], le nom AFFICHÉ est le
    texte après la DERNIÈRE virgule de chaque ligne
    (`fichier=hash,id{guid,Nom (Vendor)` ou `fichier=hash,id,Nom (Vendor)`)."""
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    in_cache = False
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("["):
            in_cache = line.strip().lower() == "[vstcache]"
            continue
        if not in_cache or "=" not in line:
            continue
        file_key, _, rest = line.partition("=")
        name = _vst_display_name(rest)
        if name:
            out.append({"name": name, "kind": "vst", "file": file_key.strip()})
    return out


_JSFX_RE = re.compile(r'^NAME\s+(\S+)\s+"(.*)"\s*$')


def _parse_jsfx_ini(path: Path) -> list[dict]:
    """Parse reaper-jsfx.ini : lignes `NAME <chemin> "JS: <nom>"`."""
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for raw in lines:
        m = _JSFX_RE.match(raw.strip())
        if m:
            out.append({"name": m.group(2).strip(), "kind": "jsfx", "file": m.group(1)})
    return out


def list_installed_fx(filter: str = "", kinds: tuple[str, ...] = ("vst", "jsfx")) -> list[dict]:
    """Liste les effets INSTALLÉS (lecture des caches REAPER). `filter` = sous-chaîne
    insensible à la casse sur le nom. `kinds` restreint vst/jsfx. Dédupliqué par nom,
    trié. Renvoie [{"name","kind","file"}, ...]."""
    res = _resource_dir()
    items: list[dict] = []
    if "vst" in kinds:
        for fn in _VST_INIS:
            p = res / fn
            if p.exists():
                items.extend(_parse_vst_ini(p))
    if "jsfx" in kinds:
        p = res / _JSFX_INI
        if p.exists():
            items.extend(_parse_jsfx_ini(p))
    needle = (filter or "").strip().lower()
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        nm = it["name"]
        if needle and needle not in nm.lower():
            continue
        if nm in seen:
            continue
        seen.add(nm)
        out.append(it)
    out.sort(key=lambda d: d["name"].lower())
    return out


def _user_plugin_rank(name: str) -> int:
    """Priorité d'un plugin pour resolve_plugin : plus BAS = préféré.
    0 = plugins de l'utilisateur (KaribVoice / KlodVoice = sa chaîne voix caribéenne),
    1 = stock REAPER (Cockos : ReaEQ/ReaComp…), 2 = autres tiers."""
    low = name.lower()
    if "karib" in low or "klod" in low:
        return 0
    if "(cockos)" in low or low.startswith("rea") or "js:" in low:
        return 1
    return 2


def resolve_plugin(role: str, installed: list[dict] | None = None) -> dict | None:
    """Résout un RÔLE musical ("eq","comp","deesser","reverb","delay","gate",
    "limiter","saturation","harmonics") vers le MEILLEUR plugin installé.

    Classement : plugins de l'utilisateur (KaribVoice/KlodVoice) avant le stock Rea*
    avant les tiers ; à rang égal, le nom le plus court (souvent le plus « pur »).
    Renvoie {"name","kind","file","role"} ou None si rien d'installé pour ce rôle.
    """
    keys = _ROLE_KEYWORDS.get((role or "").strip().lower())
    if not keys:
        return None
    pool = installed if installed is not None else list_installed_fx()
    cands = [it for it in pool if any(k in it["name"].lower() for k in keys)]
    if not cands:
        return None
    cands.sort(key=lambda d: (_user_plugin_rank(d["name"]), len(d["name"]), d["name"].lower()))
    best = dict(cands[0])
    best["role"] = role
    return best
