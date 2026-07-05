"""Sound design — génération de presets de synthé + organisation de banques (P-musique).

Deux briques déterministes pour le SOUND DESIGN :

  - generer_preset_synth : construit un patch de synthé soustractif AGNOSTIQUE (osc /
    filtre / enveloppes / LFO / unison / effets) pour un rôle (basse, lead, pad,
    pluck…) et un caractère (chaud, brillant, agressif…). Pas lié à un synthé précis :
    ce sont des valeurs à recopier dans Serum/Vital/Massive/Alchemy/n'importe lequel.
  - organiser_banque : range une banque de samples locale par catégorie (kick, snare,
    hat, 808, synth, vocal, fx, loop…) déduite du nom de fichier, et propose une
    arborescence. Le cœur `categoriser_fichiers` est pur (liste de noms) → testable.

Aucune dépendance lourde. Les patchs sont des POINTS DE DÉPART cohérents, pas des
presets « finis » : le goût final se règle à l'oreille (spec « ne jamais prétendre »).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Génération de presets                                                        #
# --------------------------------------------------------------------------- #

# Patchs de BASE par rôle (synthé soustractif agnostique). Enveloppes en secondes,
# cutoff/resonance en 0..1 (normalisé), octave relative à C3.
_ROLES: dict[str, dict] = {
    "basse": {
        "oscillateurs": [{"forme": "saw", "octave": -1, "detune": 0.05, "niveau": 1.0},
                         {"forme": "sine", "octave": -2, "detune": 0.0, "niveau": 0.7}],
        "filtre": {"type": "passe-bas", "cutoff": 0.35, "resonance": 0.15, "env_amount": 0.4},
        "amp_env": {"a": 0.005, "d": 0.15, "s": 0.8, "r": 0.15},
        "filtre_env": {"a": 0.005, "d": 0.2, "s": 0.3, "r": 0.15},
        "lfo": None, "unison": 1, "glide_ms": 40, "polyphonie": "mono",
        "effets": ["saturation légère"],
    },
    "sub": {
        "oscillateurs": [{"forme": "sine", "octave": -2, "detune": 0.0, "niveau": 1.0}],
        "filtre": {"type": "passe-bas", "cutoff": 0.25, "resonance": 0.0, "env_amount": 0.0},
        "amp_env": {"a": 0.01, "d": 0.1, "s": 1.0, "r": 0.1},
        "filtre_env": {"a": 0.0, "d": 0.0, "s": 1.0, "r": 0.0},
        "lfo": None, "unison": 1, "glide_ms": 20, "polyphonie": "mono", "effets": [],
    },
    "lead": {
        "oscillateurs": [{"forme": "saw", "octave": 0, "detune": 0.08, "niveau": 1.0},
                         {"forme": "square", "octave": 0, "detune": -0.08, "niveau": 0.5}],
        "filtre": {"type": "passe-bas", "cutoff": 0.7, "resonance": 0.2, "env_amount": 0.3},
        "amp_env": {"a": 0.01, "d": 0.2, "s": 0.85, "r": 0.2},
        "filtre_env": {"a": 0.02, "d": 0.25, "s": 0.5, "r": 0.2},
        "lfo": {"forme": "sine", "cible": "hauteur", "rate_hz": 5.5, "depth": 0.03, "delay_ms": 400},
        "unison": 3, "glide_ms": 15, "polyphonie": "mono", "effets": ["delay", "reverb"],
    },
    "pad": {
        "oscillateurs": [{"forme": "saw", "octave": 0, "detune": 0.12, "niveau": 1.0},
                         {"forme": "saw", "octave": 0, "detune": -0.12, "niveau": 1.0},
                         {"forme": "triangle", "octave": 1, "detune": 0.0, "niveau": 0.5}],
        "filtre": {"type": "passe-bas", "cutoff": 0.5, "resonance": 0.1, "env_amount": 0.5},
        "amp_env": {"a": 0.8, "d": 0.5, "s": 0.9, "r": 1.2},
        "filtre_env": {"a": 1.0, "d": 0.8, "s": 0.6, "r": 1.0},
        "lfo": {"forme": "sine", "cible": "cutoff", "rate_hz": 0.3, "depth": 0.15, "delay_ms": 0},
        "unison": 5, "glide_ms": 0, "polyphonie": "poly", "effets": ["chorus", "reverb"],
    },
    "pluck": {
        "oscillateurs": [{"forme": "saw", "octave": 0, "detune": 0.04, "niveau": 1.0}],
        "filtre": {"type": "passe-bas", "cutoff": 0.6, "resonance": 0.25, "env_amount": 0.6},
        "amp_env": {"a": 0.002, "d": 0.25, "s": 0.0, "r": 0.2},
        "filtre_env": {"a": 0.002, "d": 0.18, "s": 0.0, "r": 0.15},
        "lfo": None, "unison": 1, "glide_ms": 0, "polyphonie": "poly", "effets": ["delay", "reverb"],
    },
    "keys": {
        "oscillateurs": [{"forme": "triangle", "octave": 0, "detune": 0.0, "niveau": 1.0},
                         {"forme": "saw", "octave": 0, "detune": 0.03, "niveau": 0.4}],
        "filtre": {"type": "passe-bas", "cutoff": 0.65, "resonance": 0.1, "env_amount": 0.2},
        "amp_env": {"a": 0.005, "d": 0.4, "s": 0.5, "r": 0.4},
        "filtre_env": {"a": 0.005, "d": 0.3, "s": 0.3, "r": 0.3},
        "lfo": None, "unison": 2, "glide_ms": 0, "polyphonie": "poly", "effets": ["chorus", "reverb"],
    },
    "brass": {
        "oscillateurs": [{"forme": "saw", "octave": 0, "detune": 0.06, "niveau": 1.0},
                         {"forme": "saw", "octave": 0, "detune": -0.06, "niveau": 0.8}],
        "filtre": {"type": "passe-bas", "cutoff": 0.55, "resonance": 0.15, "env_amount": 0.5},
        "amp_env": {"a": 0.06, "d": 0.2, "s": 0.9, "r": 0.25},
        "filtre_env": {"a": 0.05, "d": 0.3, "s": 0.6, "r": 0.2},
        "lfo": {"forme": "sine", "cible": "hauteur", "rate_hz": 5.0, "depth": 0.02, "delay_ms": 300},
        "unison": 3, "glide_ms": 10, "polyphonie": "poly", "effets": ["reverb"],
    },
    "arp": {
        "oscillateurs": [{"forme": "square", "octave": 0, "detune": 0.05, "niveau": 1.0}],
        "filtre": {"type": "passe-bas", "cutoff": 0.6, "resonance": 0.3, "env_amount": 0.5},
        "amp_env": {"a": 0.002, "d": 0.15, "s": 0.2, "r": 0.1},
        "filtre_env": {"a": 0.002, "d": 0.12, "s": 0.1, "r": 0.1},
        "lfo": None, "unison": 1, "glide_ms": 0, "polyphonie": "poly", "effets": ["delay", "reverb"],
    },
}
_ALIAS_ROLE = {"808": "basse", "bass": "basse", "sub-bass": "sub", "subbass": "sub",
               "nappe": "pad", "clavier": "keys", "cuivre": "brass", "cuivres": "brass",
               "arpege": "arp", "arpège": "arp", "solo": "lead"}

# Modificateurs de caractère : deltas appliqués (clampés). cutoff/resonance/drive 0..1.
_CARACTERES: dict[str, dict] = {
    "neutre": {},
    "chaud": {"cutoff": -0.12, "resonance": -0.03, "drive": 0.25, "detune": 0.02,
              "effet": "saturation lampe"},
    "brillant": {"cutoff": +0.18, "resonance": +0.03, "effet": "exciter/air"},
    "sombre": {"cutoff": -0.22, "resonance": -0.02},
    "agressif": {"cutoff": +0.1, "resonance": +0.2, "drive": 0.4, "effet": "distorsion"},
    "doux": {"cutoff": -0.08, "resonance": -0.08, "attaque_x": 1.5},
    "large": {"detune": 0.06, "unison_bonus": 2, "effet": "chorus + stéréo"},
    "vintage": {"cutoff": -0.1, "drive": 0.2, "detune": 0.03, "effet": "chorus + wow/flutter"},
}


def _clamp01(x: float) -> float:
    return round(max(0.0, min(1.0, x)), 3)


def generer_preset_synth(role: str, caractere: str = "neutre", ton: str = "") -> dict:
    """Génère un patch de synthé soustractif agnostique pour un rôle + un caractère.

    role : basse, sub, lead, pad, pluck, keys, brass, arp (alias : 808→basse, nappe→pad…).
    caractere : neutre, chaud, brillant, sombre, agressif, doux, large, vintage.
    ton : optionnel — note de référence pour l'oscillateur (ex. 'C', 'F#')."""
    import copy
    r = (role or "").strip().lower()
    r = _ALIAS_ROLE.get(r, r)
    if r not in _ROLES:
        return {"error": f"rôle inconnu : {role!r} ({', '.join(sorted(_ROLES))})."}
    c = (caractere or "neutre").strip().lower()
    if c not in _CARACTERES:
        return {"error": f"caractère inconnu : {caractere!r} ({', '.join(sorted(_CARACTERES))})."}

    patch = copy.deepcopy(_ROLES[r])
    mod = _CARACTERES[c]

    # Filtre : applique les deltas cutoff/resonance, clampés.
    patch["filtre"]["cutoff"] = _clamp01(patch["filtre"]["cutoff"] + mod.get("cutoff", 0.0))
    patch["filtre"]["resonance"] = _clamp01(patch["filtre"]["resonance"] + mod.get("resonance", 0.0))
    # Detune : élargit les oscillateurs.
    if mod.get("detune"):
        for osc in patch["oscillateurs"]:
            if osc["detune"] != 0.0:
                osc["detune"] = round(osc["detune"] + (mod["detune"] if osc["detune"] > 0 else -mod["detune"]), 3)
    # Unison / attaque / drive / effet.
    patch["unison"] = min(9, patch["unison"] + mod.get("unison_bonus", 0))
    if mod.get("attaque_x"):
        patch["amp_env"]["a"] = round(patch["amp_env"]["a"] * mod["attaque_x"], 4)
    drive = mod.get("drive", 0.0)
    if drive:
        patch["drive"] = _clamp01(drive)
    if mod.get("effet") and mod["effet"] not in patch["effets"]:
        patch["effets"] = [*patch["effets"], mod["effet"]]

    out = {
        "nom": f"{r}_{c}",
        "role": r, "caractere": c,
        "patch": patch,
        "note": "Patch de DÉPART agnostique (Serum/Vital/Massive/Alchemy…). Enveloppes "
        "en secondes, cutoff/resonance normalisés 0..1. Ajuste cutoff et enveloppes à "
        "l'oreille selon le mix.",
    }
    if ton:
        out["note_reference"] = ton.strip()
    return out


# --------------------------------------------------------------------------- #
# Organisation de banques de samples                                          #
# --------------------------------------------------------------------------- #

# Catégorie -> mots-clés (dans le nom de fichier, minuscule). Ordre = priorité :
# les percussions spécifiques avant les termes génériques (perc, loop).
_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("kick", ("kick", "bd", "grosse caisse", "bassdrum")),
    ("snare", ("snare", "sd", "caisse claire", "rimshot", "rim")),
    ("clap", ("clap", "handclap")),
    ("hat", ("hat", "hh", "hihat", "charley", "charleston")),
    ("cymbale", ("cymbal", "crash", "ride", "cymbale")),
    ("tom", ("tom", "floor")),
    ("808", ("808",)),
    ("basse", ("bass", "basse", "sub")),
    ("perc", ("perc", "conga", "bongo", "shaker", "tamb", "cowbell", "clave", "djembe")),
    ("lead", ("lead", "synth", "arp", "pluck")),
    ("pad", ("pad", "nappe", "atmos", "drone", "texture")),
    ("keys", ("key", "piano", "rhodes", "organ", "epiano")),
    ("guitare", ("guitar", "guitare", "gtr")),
    ("vocal", ("vocal", "vox", "voix", "acap", "adlib", "chant")),
    ("fx", ("fx", "riser", "downlifter", "impact", "sweep", "whoosh", "foley", "transition")),
    ("loop", ("loop", "groove", "beat")),
]
# Extensions reconnues (pour ne classer que des samples/presets, pas des .txt).
_EXT_AUDIO = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".m4a"}
_EXT_MIDI = {".mid", ".midi"}
_EXT_PRESET = {".fxp", ".vital", ".serumpreset", ".adg", ".nmsv", ".h2p", ".fst"}


def _ext(nom: str) -> str:
    i = nom.rfind(".")
    return nom[i:].lower() if i >= 0 else ""


def categoriser_fichiers(noms) -> dict:
    """Cœur PUR : classe une liste de noms de fichiers par catégorie (mots-clés + ext).

    Renvoie {categories: {cat: [noms]}, counts, non_categorises, midi, presets, ignores}.
    'ignores' = fichiers dont l'extension n'est ni audio/midi/preset. Sans FS → testable."""
    cats: dict[str, list[str]] = {}
    midi: list[str] = []
    presets: list[str] = []
    non_cat: list[str] = []
    ignores: list[str] = []
    for nom in (noms or []):
        base = str(nom)
        low = base.lower()
        ext = _ext(base)
        if ext in _EXT_MIDI:
            midi.append(base)
            continue
        if ext in _EXT_PRESET:
            presets.append(base)
            continue
        if ext not in _EXT_AUDIO:
            ignores.append(base)
            continue
        trouve = None
        for cat, mots in _CATEGORIES:
            if any(m in low for m in mots):
                trouve = cat
                break
        if trouve:
            cats.setdefault(trouve, []).append(base)
        else:
            non_cat.append(base)
    counts = {cat: len(v) for cat, v in cats.items()}
    if midi:
        counts["midi"] = len(midi)
    if presets:
        counts["presets"] = len(presets)
    return {
        "categories": cats,
        "counts": counts,
        "midi": midi,
        "presets": presets,
        "non_categorises": non_cat,
        "ignores": ignores,
        "arborescence_suggeree": [f"{c}/" for c in sorted(cats)]
        + (["MIDI/"] if midi else []) + (["Presets/"] if presets else []),
    }


def organiser_banque(root: str | None = None, limit: int = 5000) -> dict:
    """Range une banque de samples LOCALE par catégorie (déduite des noms de fichiers).

    Parcourt `root` (récursif, borné à `limit` fichiers), classe via categoriser_fichiers,
    et propose une arborescence. Lecture seule : ne DÉPLACE rien (l'agent enchaîne des
    déplacements si tu valides). `root` défaut = env KLODY_SAMPLES_DIR."""
    import os

    from klody_mcp._pathguard import PathGuardViolation, safe_path
    base = root or os.getenv("KLODY_SAMPLES_DIR", "")
    if not (base or "").strip():
        return {"error": "aucune racine de banque : passe `root` ou définis KLODY_SAMPLES_DIR."}
    try:
        p = safe_path(base)  # ASI02
    except (PathGuardViolation, FileNotFoundError) as exc:
        return {"error": str(exc)}
    if not p.is_dir():
        return {"error": f"dossier introuvable : {p}"}
    try:
        noms: list[str] = []
        tronque = False
        for dirpath, _dirs, files in os.walk(str(p)):
            for f in files:
                # Nom relatif pour un affichage lisible tout en gardant l'info de dossier.
                rel = os.path.relpath(os.path.join(dirpath, f), str(p))
                noms.append(rel)
                if len(noms) >= limit:
                    tronque = True
                    break
            if tronque:
                break
        out = categoriser_fichiers(noms)
        out["root"] = str(p)
        out["total_fichiers"] = len(noms)
        if tronque:
            out["tronque"] = f"limité aux {limit} premiers fichiers"
        return out
    except OSError as exc:
        return {"error": f"lecture de la banque impossible : {exc}"}
    except Exception as exc:
        logger.error("organiser_banque: %s", exc, exc_info=True)
        return {"error": str(exc)}
