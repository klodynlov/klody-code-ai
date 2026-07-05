"""Coaching vocal & AutoTune — analyse de justesse + réglages recommandés (P-musique).

Deux briques déterministes pour le CHANT, complémentaires d'evaluer_tessiture :

  - analyser_justesse : mesure la JUSTESSE d'une prise voix vs une gamme — pour chaque
    frame voisée, écart en CENTS à la note de gamme la plus proche → note de justesse
    /100, % de frames dans la tolérance, tendance (chante ♯ ou ♭), stabilité, conseils.
  - recommander_autotune : réglages de DÉPART pour un correcteur de hauteur (gamme à
    charger, vitesse de retune selon le style, force selon la justesse mesurée).

Le cœur d'analyse (`evaluer_justesse_f0`) travaille sur une LISTE de f0 (Hz) : pur,
sans audio ni dépendance lourde → testable directement. Le wrapper `analyser_justesse`
extrait les f0 via librosa (pyin, lazy) puis appelle le cœur. Fidèle à la spec « ne
jamais prétendre » : la justesse est une MESURE relative à une gamme supposée, pas un
jugement de la performance (une note « fausse » peut être une intention expressive).
"""
from __future__ import annotations

import logging
import math
import re

logger = logging.getLogger(__name__)

_LIBROSA_MANQUANT = (
    "librosa non installé dans le venv — installe-le : "
    "`pip install librosa soundfile` (dans ~/Projets/klody-code-ai/.venv)."
)

# Intervalles de gamme (demi-tons depuis la tonique). Naturelle pour le mineur : c'est
# la référence de justesse la plus neutre (les 6/7 haussés sont des couleurs, pas des
# cibles de justesse imposées).
_ECHELLE = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
}
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_PC_NOMS = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]

# Tolérance de justesse « pro » : ±25 cents = quart de ton, seuil courant au-delà
# duquel une note commence à s'entendre fausse sur une tenue.
_TOL_CENTS = 25.0


def _tonique_pc_mode(ton: str) -> tuple[int, str]:
    """Parse 'C', 'Am', 'F#', 'Bb', 'B♭ mineur'… -> (pitch_class tonique, 'major'|'minor').

    Lève ValueError si illisible."""
    s = (ton or "").strip().replace("♯", "#").replace("♭", "b")
    s = re.sub(r"\s*(flat|bémol|bemol)\b", "b", s, flags=re.I)
    s = re.sub(r"\s*(sharp|dièse|diese)\b", "#", s, flags=re.I)
    m = re.match(r"^([A-Ga-g])\s*([#b]?)(.*)$", s)
    if not m:
        raise ValueError(f"tonalité illisible : {ton!r} (ex: 'C', 'Am', 'F#', 'B♭ mineur').")
    pc = _PC[m.group(1).upper()]
    if m.group(2) == "#":
        pc = (pc + 1) % 12
    elif m.group(2) == "b":
        pc = (pc - 1) % 12
    rest = m.group(3).strip().lower()
    mode = "minor" if (("min" in rest) or (rest.startswith("m") and not rest.startswith("maj"))) else "major"
    return pc, mode


def _scale_pcs(ton: str) -> tuple[set[int], int, str]:
    """(classes de hauteur de la gamme, pc tonique, mode) pour une tonalité."""
    pc, mode = _tonique_pc_mode(ton)
    return {(pc + i) % 12 for i in _ECHELLE[mode]}, pc, mode


def _hz_to_midi(f: float) -> float:
    """Fréquence (Hz) -> hauteur MIDI continue (69 = A4 = 440 Hz)."""
    return 69.0 + 12.0 * math.log2(f / 440.0)


def _plus_proche_note_gamme(midi: float, scale_pcs: set[int]) -> int:
    """Entier MIDI dans la gamme le plus proche d'une hauteur continue."""
    base = round(midi)
    return min(
        (m for m in range(base - 2, base + 3) if m % 12 in scale_pcs),
        key=lambda m: abs(m - midi),
    )


def evaluer_justesse_f0(f0_hz, ton: str, tolerance_cents: float = _TOL_CENTS) -> dict:
    """Cœur PUR : évalue la justesse d'une suite de f0 (Hz) vs la gamme `ton`.

    Pour chaque f0 finie/positive : écart en cents à la note de gamme la plus proche.
    Agrège en note /100 (part des frames dans la tolérance), écart moyen, tendance
    (♯/♭), stabilité, et le pire écart. Sans audio → testable tel quel."""
    try:
        scale_pcs, _pc, mode = _scale_pcs(ton)
    except ValueError as exc:
        return {"error": str(exc)}
    vals = [float(f) for f in (f0_hz or []) if isinstance(f, (int, float)) and math.isfinite(f) and f > 0]
    if len(vals) < 5:
        return {"error": "pas assez de hauteur exploitable (≥5 frames voisées requises)."}

    ecarts = []          # signés (cents) : + = trop haut (♯), - = trop bas (♭)
    pire = None
    for f in vals:
        midi = _hz_to_midi(f)
        cible = _plus_proche_note_gamme(midi, scale_pcs)
        cents = (midi - cible) * 100.0
        ecarts.append(cents)
        if pire is None or abs(cents) > abs(pire[0]):
            pire = (cents, _PC_NOMS[cible % 12])

    n = len(ecarts)
    abs_moy = sum(abs(c) for c in ecarts) / n
    signe_moy = sum(ecarts) / n
    dans_tol = sum(1 for c in ecarts if abs(c) <= tolerance_cents) / n
    # Note /100 = part des frames dans la tolérance (interprétable directement).
    note = round(100.0 * dans_tol)
    # Stabilité = écart-type des cents (petit = tenues stables ; grand = instable/vibrato).
    moy = signe_moy
    stab = math.sqrt(sum((c - moy) ** 2 for c in ecarts) / n)

    if signe_moy > 8:
        tendance = "tu chantes légèrement ♯ (au-dessus) — vise un poil plus bas"
    elif signe_moy < -8:
        tendance = "tu chantes légèrement ♭ (en-dessous) — soutiens plus le souffle pour remonter"
    else:
        tendance = "centrage correct (ni ♯ ni ♭ marqué)"

    conseils = []
    if note >= 90:
        verdict = "très juste"
    elif note >= 75:
        verdict = "juste dans l'ensemble"
    elif note >= 55:
        verdict = "justesse perfectible"
        conseils.append("travaille les tenues longues avec un bourdon (drone) sur la tonique.")
    else:
        verdict = "justesse à retravailler"
        conseils.append("ralentis, chante à côté d'un piano/drone, note par note.")
    if stab > 35:
        conseils.append("hauteur instable : attaque la note franchement plutôt qu'en glissando.")
    if abs(signe_moy) > 15:
        conseils.append("biais constant : vérifie ton point de référence (casque trop fort masque la justesse).")

    return {
        "ton": f"{_PC_NOMS[_pc]} {'mineur' if mode == 'minor' else 'majeur'}",
        "note_justesse": note,
        "verdict": verdict,
        "ecart_moyen_cents": round(abs_moy, 1),
        "tendance_cents": round(signe_moy, 1),
        "tendance": tendance,
        "stabilite_cents": round(stab, 1),
        "pct_dans_tolerance": round(dans_tol, 3),
        "tolerance_cents": tolerance_cents,
        "pire_ecart": {"cents": round(pire[0], 1), "vers": pire[1]} if pire else None,
        "frames": n,
        "conseils": conseils,
        "note_methode": "Justesse = écart en cents à la gamme supposée. Une note 'fausse' "
        "peut être une intention expressive (blue note, glissando) — juge à l'oreille.",
    }


def analyser_justesse(
    chemin_audio: str, ton: str, sr: int = 22050,
    note_min: str = "C2", note_max: str = "D6", tolerance_cents: float = _TOL_CENTS,
) -> dict:
    """Analyse la justesse d'une prise voix (fichier) vs la gamme `ton`.

    Extrait la hauteur image par image (pyin) puis évalue via evaluer_justesse_f0.
    Voix nue / a cappella recommandée (le pitch-tracking est monophonique)."""
    from klody_mcp._pathguard import PathGuardViolation, safe_path
    try:
        p = safe_path(chemin_audio)  # ASI02
    except (PathGuardViolation, FileNotFoundError) as exc:
        return {"error": str(exc)}
    if not p.is_file():
        return {"error": f"fichier introuvable : {p}"}
    try:
        import librosa
        import numpy as np
    except ImportError:
        return {"error": _LIBROSA_MANQUANT}
    try:
        y, sr_eff = librosa.load(str(p), sr=sr, mono=True)
        if len(y) / float(sr_eff or 1) < 0.3:
            return {"error": "extrait trop court (≥0.3s requis)."}
        fmin = float(librosa.note_to_hz(note_min))
        fmax = float(librosa.note_to_hz(note_max))
        f0, voiced, _ = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=sr_eff)
        f0 = f0[voiced]
        f0 = f0[np.isfinite(f0)]
        out = evaluer_justesse_f0(f0.tolist(), ton, tolerance_cents=tolerance_cents)
        if "error" not in out:
            out["duree_analysee_sec"] = round(len(y) / float(sr_eff), 1)
        return out
    except Exception as exc:
        logger.error("analyser_justesse: %s", exc, exc_info=True)
        return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Recommandation AutoTune                                                      #
# --------------------------------------------------------------------------- #

# Vitesse de retune par style (ms) + caractère. Points de DÉPART, à affiner.
_RETUNE_STYLE = {
    "trap": (0, "effet robotique assumé (retune quasi-instantané, T-Pain/hyperpop)"),
    "drill": (0, "retune très rapide, grain moderne"),
    "pop": (25, "correction rapide mais discrète"),
    "afro": (40, "corrige sans figer le groove"),
    "zouk": (60, "naturel, garde les inflexions"),
    "rnb": (70, "transparent, respecte les mélismes"),
    "soul": (90, "léger, préserve le vibrato et les runs"),
    "ballade": (110, "quasi-inaudible, seulement rattraper les dérives"),
    "jazz": (130, "minimal — la justesse expressive prime"),
}
_ALIAS_STYLE = {
    "trap soul": "soul", "r&b": "rnb", "dancehall": "afro", "afrobeat": "afro",
    "afrobeats": "afro", "": "pop", "neutre": "pop", "default": "pop",
}


def recommander_autotune(ton: str, style: str = "pop", justesse_cents: float | None = None) -> dict:
    """Réglages de DÉPART pour un correcteur de hauteur (AutoTune/Melodyne/pitch).

    Donne la gamme à charger (depuis `ton`), la vitesse de retune selon le style, et
    la force selon la justesse MESURÉE (`justesse_cents` = écart moyen d'analyser_justesse,
    optionnel). Tout est un point de départ à régler à l'oreille."""
    try:
        _scale, pc, mode = _scale_pcs(ton)
    except ValueError as exc:
        return {"error": str(exc)}
    s = (style or "").strip().lower()
    s = _ALIAS_STYLE.get(s, s)
    if s not in _RETUNE_STYLE:
        s = "pop"
    retune_ms, retune_note = _RETUNE_STYLE[s]

    notes_gamme = [_PC_NOMS[(pc + i) % 12] for i in _ECHELLE[mode]]

    # Force : si on connaît l'écart moyen mesuré, on module. Sinon défaut prudent.
    if justesse_cents is None:
        force_pct, force_note = 100, "force pleine par défaut (baisse-la si l'effet s'entend trop)"
    elif justesse_cents <= 15:
        force_pct, force_note = 70, "voix déjà juste : correction subtile, préserve le naturel"
    elif justesse_cents <= 35:
        force_pct, force_note = 90, "quelques dérives : correction ferme mais pas totale"
    else:
        force_pct, force_note = 100, "dérives marquées : correction pleine, puis retravaille la prise"

    return {
        "ton": f"{_PC_NOMS[pc]} {'mineur' if mode == 'minor' else 'majeur'}",
        "style": s,
        "gamme_a_charger": notes_gamme,
        "note_reference": _PC_NOMS[pc],
        "retune_speed_ms": retune_ms,
        "retune_note": retune_note,
        "force_pct": force_pct,
        "force_note": force_note,
        "reglages_complementaires": {
            "humanize": "monte si les tenues sonnent figées",
            "flex_tune": "laisse passer les petites variations expressives",
            "transition": "adoucit les sauts entre notes (formant/natural vibrato ON)",
        },
        "note": "Charge d'abord la gamme, cale la vitesse au style, PUIS la force selon "
        "la prise. Sur les notes en falsetto/tête, allège la correction (elles bougent plus).",
    }
