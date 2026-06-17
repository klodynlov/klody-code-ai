"""KlodyMusic MCP server — analyse musicale de la voix (tessiture, théorie, idées).

Bras moteur MCP dédié au domaine MUSIQUE (analyse IN + théorie + idéation), sibling
de vocalbrain_server.py. Process séparé : isolation crash/domaine, RAM schedulable
par la gateway. Les libs lourdes (librosa, plus tard music21 / mlx-whisper) sont
importées en LAZY → le serveur démarre même si une dépendance manque, et l'outil
renvoie une erreur claire pointant vers le `pip install`.

Premier outil (brique de base) :
- evaluer_tessiture(chemin_audio)  — extrait f0 (pyin), renvoie notes grave/aigüe,
  étendue en demi-tons et un type de voix approximatif (Basse..Soprano).

Suivront (mêmes patrons) : suggerer_tonalites, suggerer_accords, harmoniser,
idees_chanson, composer_demo.

Démarrage :
    python -m klody_mcp.klody_music_server                          # stdio (défaut)
    KLODYMUSIC_MCP_TRANSPORT=http python -m klody_mcp.klody_music_server   # :8088
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

logger = logging.getLogger(__name__)

# Plage de recherche f0 par défaut : C2 (~65 Hz) .. D6 (~1175 Hz) couvre toutes
# les voix humaines chantées (basse profonde -> soprano aigu) sans gaspiller de
# calcul sur des fréquences hors voix.
F0_NOTE_MIN = os.getenv("KLODYMUSIC_F0_NOTE_MIN", "C2")
F0_NOTE_MAX = os.getenv("KLODYMUSIC_F0_NOTE_MAX", "D6")
ANALYSE_SR = int(os.getenv("KLODYMUSIC_SR", "22050"))

# Brain créatif (gateway Klody Core :8090, OpenAI-compat) pour idees_chanson.
# Mêmes variables que l'agent (MLX_*) — l'arm lit le même .env SANS importer
# config (isolation de domaine préservée).
BRAIN_URL = os.getenv("MLX_BASE_URL", "http://localhost:8090/v1")
BRAIN_MODEL = os.getenv("MLX_MODEL", "")
BRAIN_KEY = os.getenv("MLX_API_KEY", "klody")
BRAIN_TIMEOUT = float(os.getenv("KLODYMUSIC_BRAIN_TIMEOUT", "180"))
# Anti-boucle génération (param mlx_lm, cf. config.LLM_REPETITION_PENALTY) — sans
# lui le modèle boucle sur un champ et casse le JSON. Défaut firme pour le créatif.
BRAIN_REP_PENALTY = float(os.getenv("KLODYMUSIC_REP_PENALTY", "1.2"))
# Transcription paroles (best-effort, lazy). mlx-whisper absent -> idées sans paroles.
WHISPER_MODEL = os.getenv("KLODYMUSIC_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
# Rendu démo chantée via le daemon local-suno (:8766), même backend que l'arm
# vocalbrain. composer_demo POSTe /generate ; statut_demo/resultat_demo y pollent.
LOCALSUNO_URL = os.getenv("LOCALSUNO_URL", "http://127.0.0.1:8766").rstrip("/")
LOCALSUNO_DIR = Path(os.getenv("LOCALSUNO_DIR", str(Path.home() / "local-suno")))
LOCALSUNO_TIMEOUT = float(os.getenv("KLODYMUSIC_SUNO_TIMEOUT", "30"))

mcp = FastMCP("KlodyMusic")

_LIBROSA_MANQUANT = (
    "librosa non installé dans le venv — installe-le : "
    "`pip install librosa soundfile` (dans ~/Projets/klody-code-ai/.venv)."
)

# Plages vocales (Hz) standard, étendue confortable note grave -> note aigüe.
# Sert à classer une tessiture observée vers le type de voix le plus proche.
_PLAGES_VOIX: list[tuple[str, float, float]] = [
    ("Basse", 82.4, 329.6),        # E2 - E4
    ("Baryton", 98.0, 392.0),      # G2 - G4
    ("Ténor", 130.8, 523.3),       # C3 - C5
    ("Contralto", 174.6, 698.5),   # F3 - F5
    ("Mezzo-soprano", 220.0, 880.0),  # A3 - A5
    ("Soprano", 261.6, 1046.5),    # C4 - C6
]


# ---------------------------------------------------------------------------- #
# Cœur (pur, testable sans MCP)                                                  #
# ---------------------------------------------------------------------------- #


def _classifier_voix(f_grave: float, f_aigu: float):
    """Classe une tessiture [f_grave, f_aigu] vers le type de voix le plus proche.

    Compare le centre géométrique de la tessiture observée au centre géométrique
    de chaque plage standard et retourne la plus proche. Le centre géométrique
    (et non arithmétique) car la perception de hauteur est logarithmique.

    Returns:
        (type_voix, plage_reference) — ex: ("Baryton", "G2–G4").
    """
    import math

    centre = math.sqrt(f_grave * f_aigu)
    meilleur = min(
        _PLAGES_VOIX,
        key=lambda p: abs(math.log2(math.sqrt(p[1] * p[2])) - math.log2(centre)),
    )
    nom, lo, hi = meilleur
    return nom, f"{lo:.0f}–{hi:.0f} Hz"


def _analyser_tessiture(
    chemin_audio: str,
    note_min: str = F0_NOTE_MIN,
    note_max: str = F0_NOTE_MAX,
    sr: int = ANALYSE_SR,
) -> dict:
    """Extrait la tessiture d'un extrait vocal monophonique.

    Pipeline : load -> pyin (f0 par frame) -> garde les frames voisées finies ->
    rogne 2 %/98 % (tue les erreurs d'octave du tracker) -> min/max -> notes +
    étendue + type de voix.

    Returns:
        dict résultat (voir evaluer_tessiture) ou {"error": "..."}.
    """
    p = Path(chemin_audio).expanduser()
    if not p.is_file():
        return {"error": f"fichier introuvable : {p}"}

    try:
        import librosa
    except ImportError:
        return {"error": _LIBROSA_MANQUANT}

    try:
        import numpy as np

        y, sr_eff = librosa.load(str(p), sr=sr, mono=True)
        duree = float(len(y) / sr_eff) if sr_eff else 0.0
        if duree < 0.3:
            return {"error": f"extrait trop court ({duree:.2f}s) — ≥0.3s requis."}

        fmin = float(librosa.note_to_hz(note_min))
        fmax = float(librosa.note_to_hz(note_max))
        f0, voiced_flag, _ = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=sr_eff)

        f0 = f0[voiced_flag]
        f0 = f0[np.isfinite(f0)]
        if f0.size < 5:
            return {
                "error": "pas assez de hauteur détectée (voix faible, bruit ou "
                "audio polyphonique ?) — fournis un extrait vocal seul, plus net."
            }

        # Rogne les 2 % extrêmes : les trackers de pitch glissent parfois d'une
        # octave sur quelques frames, ce qui fausserait grave/aigu.
        lo, hi = np.percentile(f0, [2, 98])
        f0 = f0[(f0 >= lo) & (f0 <= hi)]
        if f0.size == 0:
            return {"error": "tessiture indéterminable après filtrage."}

        f_grave = float(f0.min())
        f_aigu = float(f0.max())
        f_centre = float(np.median(f0))
        etendue_demitons = float(12.0 * np.log2(f_aigu / f_grave))
        type_voix, plage_ref = _classifier_voix(f_grave, f_aigu)

        return {
            "note_grave": librosa.hz_to_note(f_grave),
            "note_aigue": librosa.hz_to_note(f_aigu),
            "note_centrale": librosa.hz_to_note(f_centre),
            "hz_grave": round(f_grave, 1),
            "hz_aigu": round(f_aigu, 1),
            "etendue_demitons": round(etendue_demitons, 1),
            "etendue_octaves": round(etendue_demitons / 12.0, 2),
            "type_voix": type_voix,
            "plage_reference": plage_ref,
            "frames_voisees": int(f0.size),
            "duree_analysee_sec": round(duree, 1),
            "note": "Tessiture observée sur CET extrait — borne basse de ta vraie "
            "étendue (tu peux aller plus loin en t'échauffant). Type de voix "
            "approximatif, calé sur le centre de la tessiture.",
        }
    except Exception as exc:
        logger.error("_analyser_tessiture: %s", exc, exc_info=True)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------- #
# Outils MCP                                                                    #
# ---------------------------------------------------------------------------- #


@mcp.tool()
def evaluer_tessiture(chemin_audio: str) -> dict:
    """Évalue la tessiture (étendue vocale) d'un extrait audio chanté/parlé.

    Analyse un fichier audio monophonique (voix seule, idéalement a cappella) et
    en déduit la note la plus grave, la plus aigüe, l'étendue et un type de voix
    approximatif. Donne le meilleur résultat sur une voix nue ; un mix complet
    (avec instruments) fausse la détection.

    Args:
        chemin_audio: Chemin du fichier audio (wav/mp3/flac…), absolu ou ~.

    Returns:
        {"note_grave", "note_aigue", "note_centrale", "hz_grave", "hz_aigu",
         "etendue_demitons", "etendue_octaves", "type_voix", "plage_reference",
         "frames_voisees", "duree_analysee_sec", "note"} ou {"error": "..."}.
    """
    return _analyser_tessiture(chemin_audio)


# ---------------------------------------------------------------------------- #
# Théorie musicale (music21) — cœur pur                                         #
# ---------------------------------------------------------------------------- #

_MUSIC21_MANQUANT = (
    "music21 non installé dans le venv — installe-le : "
    "`pip install music21` (dans ~/Projets/klody-code-ai/.venv)."
)

# Degrés diatoniques (chiffrage romain) par mode.
_DEG_MAJEUR = ["I", "ii", "iii", "IV", "V", "vi", "viio"]
_DEG_MINEUR = ["i", "iio", "III", "iv", "v", "VI", "VII"]

# Progressions d'accords courantes (chiffrage romain + tag de style).
_PROG_MAJEUR = [
    ("Pop (I–V–vi–IV)", ["I", "V", "vi", "IV"], "pop"),
    ("Pop variante (vi–IV–I–V)", ["vi", "IV", "I", "V"], "pop"),
    ("Doo-wop 50s (I–vi–IV–V)", ["I", "vi", "IV", "V"], "pop"),
    ("Folk (I–IV–V)", ["I", "IV", "V"], "folk"),
    ("Jazz (ii–V–I)", ["ii", "V", "I"], "jazz"),
    ("Pachelbel (I–V–vi–iii–IV–I–IV–V)", ["I", "V", "vi", "iii", "IV", "I", "IV", "V"], "classique"),
    ("Blues 12 mesures", ["I", "I", "I", "I", "IV", "IV", "I", "I", "V", "IV", "I", "V"], "blues"),
]
_PROG_MINEUR = [
    ("Pop mineur (i–VI–III–VII)", ["i", "VI", "III", "VII"], "pop"),
    ("Andalouse (i–VII–VI–V)", ["i", "VII", "VI", "V"], "pop"),
    ("Ballade mineure (i–iv–v–i)", ["i", "iv", "v", "i"], "folk"),
    ("Jazz mineur (ii°–V–i)", ["iio", "V", "i"], "jazz"),
]


def _import_music21():
    """Importe music21 (lazy). Lève ImportError au message clair si absent."""
    try:
        import music21
        return music21
    except ImportError as exc:
        raise ImportError(_MUSIC21_MANQUANT) from exc


def _norm_in(s: str) -> str:
    """Altérations Unicode -> ASCII music21 (♯->#, ♭->-)."""
    return (s or "").replace("♯", "#").replace("♭", "-")


def _pretty(name: str) -> str:
    """ASCII music21 -> affichage (#->♯, ->♭)."""
    return name.replace("-", "♭").replace("#", "♯")


def _note_to_midi(note_str: str, m21) -> int:
    return m21.pitch.Pitch(_norm_in(note_str)).midi


def _midi_to_name(midi: float, m21) -> str:
    p = m21.pitch.Pitch()
    p.midi = int(round(midi))
    return _pretty(p.nameWithOctave)


def _parse_key(ton: str, m21):
    """Parse 'C', 'Am', 'F#', 'Bb', 'B♭ mineur', 'C sharp major'… -> key.Key.

    Tolère le bémol ASCII 'b' (Bb = B♭) et les accidents épelés (flat/bémol,
    sharp/dièse). Lève ValueError si illisible.
    """
    import re

    s = _norm_in(str(ton).strip())
    # Accidents épelés -> symbole, AVANT le parse lettre unique.
    s = re.sub(r"\s*(flat|bémol|bemol)\b", "-", s, flags=re.I)
    s = re.sub(r"\s*(sharp|dièse|diese)\b", "#", s, flags=re.I)
    m = re.match(r"^([A-Ga-g])\s*([#♯b♭-]?)(.*)$", s)
    if not m:
        raise ValueError(f"tonalité illisible : {ton!r} (ex: 'C', 'Am', 'F#', 'B♭ mineur').")
    # 'b' (ASCII) et '♭' = bémol music21 '-' ; '♯' = '#'.
    acc = {"b": "-", "♭": "-", "♯": "#"}.get(m.group(2), m.group(2))
    tonic = m.group(1).upper() + acc
    rest = m.group(3).strip().lower()
    mineur = ("min" in rest) or (rest.startswith("m") and not rest.startswith("maj"))
    return m21.key.Key(tonic, "minor" if mineur else "major")


def _armure(k) -> str:
    n = k.sharps
    if n == 0:
        return "aucune altération"
    return f"{abs(n)} {'♯' if n > 0 else '♭'}"


def _nom_ton(k) -> str:
    return f"{_pretty(k.tonic.name)} {'mineur' if k.mode == 'minor' else 'majeur'}"


def _roman_symbol(fig: str, k, m21):
    """Chiffrage romain -> (symbole d'accord, [notes]) dans la tonalité k."""
    rn = m21.roman.RomanNumeral(fig, k)
    root = _pretty(rn.root().name)
    suf = {"major": "", "minor": "m", "diminished": "°", "augmented": "+"}.get(rn.quality, "")
    return f"{root}{suf}", [_pretty(p.name) for p in rn.pitches]


def _suggerer_tonalites(note_grave: str, note_aigue: str, n: int = 5) -> dict:
    """Tonalités où une mélodie tombe dans la zone de confort de la voix.

    Heuristique : le centre d'une chanson (tonique/centre tonal) doit tomber près
    du centre de la tessiture. On classe les 12 toniques par proximité de leur
    placement le plus proche du centre vocal, et on rend les n meilleures (majeur
    + relative mineure, armure, écart au centre).
    """
    try:
        m21 = _import_music21()
    except ImportError as exc:
        return {"error": str(exc)}
    try:
        mg = _note_to_midi(note_grave, m21)
        ma = _note_to_midi(note_aigue, m21)
        if ma <= mg:
            return {"error": f"note_aigue ({note_aigue}) doit être > note_grave ({note_grave})."}
        # MIDI est déjà logarithmique : la moyenne arithmétique = centre perçu.
        centre = (mg + ma) / 2.0

        cands = []
        base = int(round(centre))
        for pc in range(12):
            best = min(
                (m for m in range(base - 7, base + 8) if m % 12 == pc),
                key=lambda m: abs(m - centre),
            )
            cands.append((abs(best - centre), best))
        cands.sort(key=lambda x: x[0])

        tonalites = []
        for dist, bmidi in cands[:n]:
            p = m21.pitch.Pitch()
            p.midi = bmidi
            kmaj = m21.key.Key(p.name, "major")
            # Préfère l'enharmonie à l'armure la plus simple (ex: D♭ 5♭ plutôt que C♯ 7♯).
            if abs(kmaj.sharps) > 6:
                pe = p.getEnharmonic()
                kalt = m21.key.Key(pe.name, "major")
                if abs(kalt.sharps) < abs(kmaj.sharps):
                    kmaj = kalt
            rel = kmaj.relative
            tonalites.append({
                "ton": _nom_ton(kmaj),
                "relative_mineure": _nom_ton(rel),
                "tonique_confort": _midi_to_name(bmidi, m21),
                "armure": _armure(kmaj),
                "ecart_centre_demitons": round(dist, 1),
            })
        return {
            "tessiture": f"{note_grave}–{note_aigue}",
            "centre_vocal": _midi_to_name(centre, m21),
            "tonalites": tonalites,
            "note": "Tons classés du plus confortable au moins : ils centrent la "
            "chanson sur ta zone médiane. Majeur ou sa relative mineure = même armure.",
        }
    except ValueError as exc:  # entrée illisible : erreur attendue, pas un bug
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("_suggerer_tonalites: %s", exc, exc_info=True)
        return {"error": str(exc)}


def _harmoniser(ton: str) -> dict:
    """Accords diatoniques d'une tonalité (gamme harmonisée)."""
    try:
        m21 = _import_music21()
    except ImportError as exc:
        return {"error": str(exc)}
    try:
        k = _parse_key(ton, m21)
        figs = _DEG_MAJEUR if k.mode == "major" else _DEG_MINEUR
        accords = []
        for f in figs:
            sym, notes = _roman_symbol(f, k, m21)
            accords.append({"degre": f, "accord": sym, "notes": notes})
        out = {
            "ton": _nom_ton(k),
            "armure": _armure(k),
            "relative": _nom_ton(k.relative),
            "accords": accords,
        }
        if k.mode == "minor":
            sym, notes = _roman_symbol("V", k, m21)
            out["dominante_majeure"] = {
                "degre": "V",
                "accord": sym,
                "notes": notes,
                "note": "V majeur (gamme mineure harmonique) — résolution plus forte que le v mineur.",
            }
        return out
    except ValueError as exc:  # tonalité illisible : erreur attendue, pas un bug
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("_harmoniser: %s", exc, exc_info=True)
        return {"error": str(exc)}


def _suggerer_accords(ton: str, style: str = "tous") -> dict:
    """Progressions d'accords courantes dans une tonalité, filtrées par style."""
    try:
        m21 = _import_music21()
    except ImportError as exc:
        return {"error": str(exc)}
    try:
        k = _parse_key(ton, m21)
        table = _PROG_MAJEUR if k.mode == "major" else _PROG_MINEUR
        st = (style or "tous").strip().lower()
        progs = []
        for nom, figs, tag in table:
            if st not in ("tous", "all", "") and tag != st:
                continue
            accords = [_roman_symbol(f, k, m21)[0] for f in figs]
            progs.append({
                "nom": nom,
                "style": tag,
                "chiffrage": "–".join(figs),
                "accords": accords,
            })
        if not progs:
            return {
                "error": f"aucune progression pour style={style!r} en {k.mode}. "
                "Essaie : pop, jazz, folk, blues, classique — ou 'tous'."
            }
        return {
            "ton": _nom_ton(k),
            "progressions": progs,
            "note": "Accords triadiques. En blues/jazz, joue-les souvent en 7e (C7, Gm7…).",
        }
    except ValueError as exc:  # tonalité illisible : erreur attendue, pas un bug
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("_suggerer_accords: %s", exc, exc_info=True)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------- #
# Outils MCP — théorie                                                          #
# ---------------------------------------------------------------------------- #


@mcp.tool()
def suggerer_tonalites(note_grave: str, note_aigue: str) -> dict:
    """Propose les tonalités où la voix chante en zone de confort.

    À partir des bornes de la tessiture (typiquement la sortie d'evaluer_tessiture),
    classe les tonalités qui centrent une chanson sur la zone médiane de la voix —
    là où elle sonne juste sans forcer.

    Args:
        note_grave: Note la plus grave (ex: "C#2", accepte ♯/♭).
        note_aigue: Note la plus aigüe (ex: "F#4").

    Returns:
        {"tessiture", "centre_vocal", "tonalites": [{"ton", "relative_mineure",
         "tonique_confort", "armure", "ecart_centre_demitons"}], "note"}
        ou {"error": "..."}.
    """
    return _suggerer_tonalites(note_grave, note_aigue)


@mcp.tool()
def harmoniser(ton: str) -> dict:
    """Donne les accords diatoniques (gamme harmonisée) d'une tonalité.

    Args:
        ton: Tonalité — "C", "Am", "F#", "B♭ mineur"… (majeur par défaut).

    Returns:
        {"ton", "armure", "relative", "accords": [{"degre", "accord", "notes"}],
         "dominante_majeure"?} ou {"error": "..."}.
    """
    return _harmoniser(ton)


@mcp.tool()
def suggerer_accords(ton: str, style: str = "tous") -> dict:
    """Propose des suites d'accords courantes dans une tonalité.

    Args:
        ton: Tonalité — "C", "Am", "F#", "D mineur"…
        style: Filtre — "pop", "jazz", "folk", "blues", "classique" ou "tous".

    Returns:
        {"ton", "progressions": [{"nom", "style", "chiffrage", "accords"}], "note"}
        ou {"error": "..."}.
    """
    return _suggerer_accords(ton, style)


# ---------------------------------------------------------------------------- #
# Idéation de chanson (features audio + paroles + brain créatif)                #
# ---------------------------------------------------------------------------- #

# Profils Krumhansl-Schmuckler — estimation de tonalité depuis le chroma.
_KS_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_KS_MIN = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
_PC_NOMS = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]


def _estimer_tonalite(y, sr, librosa, np) -> str:
    """Tonalité dominante de l'audio (corrélation chroma vs profils KS, 24 clés)."""
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    if float(np.ptp(chroma)) == 0.0:  # chroma constant (silence/bruit plat) -> corr NaN
        return "indéterminable"
    maj, minp = np.array(_KS_MAJ), np.array(_KS_MIN)
    best = None
    for i in range(12):
        for prof, mode in ((maj, "majeur"), (minp, "mineur")):
            r = float(np.corrcoef(chroma, np.roll(prof, i))[0, 1])
            if not np.isfinite(r):
                continue
            if best is None or r > best[0]:
                best = (r, f"{_PC_NOMS[i]} {mode}")
    return best[1] if best is not None else "indéterminable"


def _label(valeur: float, paliers) -> str:
    """Premier nom dont le seuil n'est pas dépassé (dernier palier = fourre-tout)."""
    for seuil, nom in paliers:
        if valeur < seuil:
            return nom
    return paliers[-1][1]


def _transcrire(chemin_audio: str):
    """Transcrit les paroles (best-effort). None si mlx-whisper absent ou échec."""
    try:
        import mlx_whisper
    except ImportError:
        return None
    try:
        r = mlx_whisper.transcribe(
            str(Path(chemin_audio).expanduser()), path_or_hf_repo=WHISPER_MODEL
        )
        texte = (r.get("text") or "").strip()
        if not texte:
            return None
        return {"texte": texte, "langue": r.get("language")}
    except Exception as exc:
        logger.warning("transcription échouée: %s", exc)
        return None


def _extract_features(chemin_audio: str, transcrire: bool = True) -> dict:
    """Bundle de caractéristiques d'un extrait vocal pour l'idéation."""
    tess = _analyser_tessiture(chemin_audio)
    feats: dict = {"tessiture": None if "error" in tess else tess}
    if "error" in tess:
        feats["tessiture_erreur"] = tess["error"]
    try:
        import librosa
        import numpy as np
    except ImportError:
        return {"error": _LIBROSA_MANQUANT}
    try:
        y, sr = librosa.load(str(Path(chemin_audio).expanduser()), sr=ANALYSE_SR, mono=True)
        tempo = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0])
        cent = float(librosa.feature.spectral_centroid(y=y, sr=sr).mean())
        rms = float(librosa.feature.rms(y=y).mean())
        feats.update({
            "tempo_bpm": round(tempo),
            "tonalite_estimee": _estimer_tonalite(y, sr, librosa, np),
            "timbre": _label(cent, [(1500, "sombre"), (3000, "équilibré"), (float("inf"), "brillant")])
            + f" ({cent:.0f} Hz)",
            "energie": _label(rms, [(0.03, "calme"), (0.1, "modérée"), (float("inf"), "intense")])
            + f" (rms {rms:.3f})",
        })
    except Exception as exc:
        logger.warning("_extract_features (audio): %s", exc)
    if transcrire:
        feats["paroles"] = _transcrire(chemin_audio)
    return feats


def _extract_json(txt: str):
    """Extrait les objets JSON d'un texte (strip <think>, tolère troncature/boucle).

    Tente le tableau entier ; sinon récupère un par un les objets {...} complets via
    raw_decode — sauve les concepts terminés même si le dernier dérive ou est coupé.
    """
    import json
    import re

    if not txt:
        return None
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
    m = re.search(r"\[.*\]", txt, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            pass
    dec = json.JSONDecoder()
    objs = []
    i = txt.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(txt, i)
            if isinstance(obj, dict):
                objs.append(obj)
            i = txt.find("{", max(end, i + 1))
        except ValueError:
            i = txt.find("{", i + 1)
    return objs or None


def _idees_chanson(chemin_audio: str, style: str = "", n: int = 3, transcrire: bool = True) -> dict:
    """Propose n idées de chanson taillées pour la voix de l'extrait (via brain)."""
    import json

    feats = _extract_features(chemin_audio, transcrire)
    if "error" in feats:
        return feats
    tess = feats.get("tessiture") or {}
    type_voix = tess.get("type_voix", "inconnu")

    bundle = json.dumps(feats, ensure_ascii=False)
    systeme = (
        "Tu es directeur artistique et compositeur. À partir des caractéristiques "
        "vocales d'un chanteur, tu proposes des idées de chanson taillées pour SA "
        "voix. Tu réponds UNIQUEMENT par un tableau JSON valide, sans texte autour, "
        "sans bloc de code. Sois CONCIS : aucune valeur ne dépasse une phrase courte, "
        "ne répète jamais un mot ou un symbole plus de deux fois de suite."
    )
    user = (
        f"Caractéristiques de la voix (extraites d'un enregistrement) :\n{bundle}\n\n"
        f"Style souhaité : {style or 'libre'}.\n\n"
        f"Propose {n} idées de chanson DIFFÉRENTES, chacune calée sur cette voix "
        f"(type {type_voix}, sa tessiture, sa tonalité, son tempo, son énergie).\n"
        "Chaque idée = un objet JSON avec EXACTEMENT ces clés :\n"
        '- "titre" : string court\n'
        '- "theme" : string, 1 phrase\n'
        '- "genre" : string court\n'
        '- "ambiance" : string, 1 phrase\n'
        '- "structure" : tableau de 3 à 6 sections courtes, ex ["Intro","Couplet","Refrain","Pont","Outro"]\n'
        '- "tonalite_suggeree" : string, ex "Mi bémol majeur"\n'
        '- "progression_suggeree" : tableau de 3 à 6 accords, ex ["Cm","A♭","E♭","B♭"]\n'
        '- "instrumentation" : tableau de 3 à 6 instruments\n'
        '- "refs_artistes" : tableau de 1 à 3 artistes au timbre proche\n'
        '- "amorce_paroles" : tableau de 2 à 4 vers courts\n'
        "Réponds par un tableau JSON de ces objets, rien d'autre. Sois compact."
    )
    try:
        from openai import APIConnectionError, APITimeoutError, OpenAI

        # max_retries=0 : règle maison (config.LLM_MAX_RETRIES) — un retry SDK
        # silencieux re-génère tout le tour (coûteux, caché, hang ~9 min).
        client = OpenAI(base_url=BRAIN_URL, api_key=BRAIN_KEY, timeout=BRAIN_TIMEOUT, max_retries=0)
        # repetition_penalty = param mlx_lm (hors spec OpenAI) → extra_body, sinon
        # le modèle boucle ("A A A A…") et casse le JSON (vécu). enable_thinking
        # False → JSON direct, plus rapide, moins de dérive.
        extra_body: dict = {"chat_template_kwargs": {"enable_thinking": False}}
        if BRAIN_REP_PENALTY > 1.0:
            extra_body["repetition_penalty"] = BRAIN_REP_PENALTY
            # Fenêtre élargie : la pénalité voit toute la zone de boucle, pas 20 tokens.
            extra_body["repetition_context_size"] = 100
        resp = client.chat.completions.create(
            model=BRAIN_MODEL,
            messages=[{"role": "system", "content": systeme}, {"role": "user", "content": user}],
            temperature=0.85,
            max_tokens=2000,
            extra_body=extra_body,
        )
        txt = resp.choices[0].message.content or ""
    except (APIConnectionError, APITimeoutError) as exc:  # brain down : erreur ATTENDUE
        logger.warning("_idees_chanson (brain injoignable): %s", exc)
        return {
            "error": f"brain injoignable ({BRAIN_URL}) — démarre la gateway Klody Core :8090.",
            "features": feats,
        }
    except Exception as exc:
        # Pas l'exception brute dans le retour (peut porter URL/clé/corps worker).
        logger.error("_idees_chanson (brain): %s", exc, exc_info=True)
        return {"error": f"erreur brain ({BRAIN_URL}) — voir les logs.", "features": feats}

    idees = _extract_json(txt)
    out = {"features": feats, "idees": idees}
    if idees is None:
        out["idees_brut"] = txt.strip()
        out["note"] = "Le brain n'a pas renvoyé de JSON exploitable — texte brut fourni."
    return out


@mcp.tool()
def idees_chanson(chemin_audio: str, style: str = "", transcrire: bool = True) -> dict:
    """Propose des idées de chanson à partir d'un extrait vocal.

    Analyse la voix (tessiture, tempo, tonalité, timbre, énergie, + paroles si
    chantées avec des mots) puis demande au brain local des concepts de chanson
    taillés pour cette voix. Génératif : compter 30-90 s.

    Args:
        chemin_audio: Chemin du fichier audio (voix), absolu ou ~.
        style: Style/genre souhaité (ex: "ballade pop", "rap", "soul"). Vide = libre.
        transcrire: Transcrire les paroles via whisper si la voix a des mots.

    Returns:
        {"features": {...}, "idees": [{titre, theme, genre, ambiance, structure,
         tonalite_suggeree, progression_suggeree, instrumentation, refs_artistes,
         amorce_paroles}, ...]} ou {"error": "...", "features"?}.
    """
    return _idees_chanson(chemin_audio, style, transcrire=transcrire)


# ---------------------------------------------------------------------------- #
# Rendu démo (local-suno :8766) — idée -> audio chanté                          #
# ---------------------------------------------------------------------------- #

_UNREACHABLE_SUNO = (
    f"daemon local-suno injoignable ({LOCALSUNO_URL}) — démarre-le "
    "(service com.klody.localsuno-daemon)."
)


def _abs_url(rel: str) -> str:
    if not rel:
        return ""
    return rel if rel.startswith("http") else f"{LOCALSUNO_URL}{rel}"


def _session_output_dir(session_id: str) -> Path:
    """Dossier de sortie local (le daemon nomme session_{8 premiers caractères})."""
    return LOCALSUNO_DIR / "output" / f"session_{session_id[:8]}"


async def _ls_get(path: str, **kw) -> httpx.Response:
    async with httpx.AsyncClient(timeout=LOCALSUNO_TIMEOUT) as client:
        return await client.get(f"{LOCALSUNO_URL}{path}", **kw)


async def _ls_post(path: str, json_body: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=LOCALSUNO_TIMEOUT) as client:
        return await client.post(f"{LOCALSUNO_URL}{path}", json=json_body)


def _as_list(v) -> list:
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def _idee_to_body(idee: dict, duree_sec: int, modele_voix: str, transpose: int, bpm) -> dict:
    """Mappe une idée de chanson -> corps /generate du daemon local-suno."""
    # Tolère qu'on passe la sortie complète d'idees_chanson : prend la 1ʳᵉ idée.
    if isinstance(idee.get("idees"), list) and idee["idees"]:
        idee = idee["idees"][0]
    genre = str(idee.get("genre", "")).strip()
    ambiance = str(idee.get("ambiance", "")).strip()
    instr = ", ".join(_as_list(idee.get("instrumentation")))
    ton = str(idee.get("tonalite_suggeree", "")).strip()
    style = ", ".join(t for t in (genre, ambiance, instr, ton) if t) or "emotional song, warm vocals"
    paroles = "\n".join(_as_list(idee.get("amorce_paroles"))).strip()
    body: dict = {
        "prompt": style,
        "duration_sec": max(10, min(int(duree_sec), 120)),  # bornes daemon (ge=10 le=120)
        "rvc_transpose": int(transpose),
        "rvc_model": modele_voix or "klody",
        "style_prompt": style,
    }
    if paroles:
        body["custom_lyrics"] = paroles
    if bpm:
        body["bpm"] = max(60, min(int(bpm), 180))  # bornes daemon (ge=60 le=180)
    return body


@mcp.tool()
async def composer_demo(
    idee: dict,
    duree_sec: int = 30,
    modele_voix: str = "klody",
    transpose: int = 0,
    bpm: int | None = None,
) -> dict:
    """Génère une démo audio chantée à partir d'une idée de chanson — NON bloquant.

    Prend une idée (objet renvoyé par idees_chanson : genre, ambiance, tonalité,
    instrumentation, amorce de paroles), la mappe en requête de génération et lance
    le pipeline local-suno (ACE-Step -> RVC voix clonée -> mix). Suis l'avancement
    avec statut_demo(session_id), puis resultat_demo(session_id) quand status=done.

    Args:
        idee: Idée de chanson (clés genre/ambiance/tonalite_suggeree/instrumentation/
            amorce_paroles). Accepte aussi la sortie complète d'idees_chanson.
        duree_sec: Durée cible (s).
        modele_voix: Voix clonée RVC (voir l'arm vocalbrain : lister_voix).
        transpose: Transposition en demi-tons (cale la démo sur ta tessiture).
        bpm: BPM cible (optionnel).

    Returns:
        {"session_id", "status", "demo": {style, paroles}, "note"} ou {"error": "..."}.
    """
    if not isinstance(idee, dict) or not idee:
        return {"error": "idee requise (un objet renvoyé par idees_chanson)."}
    body = _idee_to_body(idee, duree_sec, modele_voix, transpose, bpm)
    try:
        resp = await _ls_post("/generate", body)
        if resp.status_code == 429:
            return {"error": "file d'attente pleine — réessaie dans un moment."}
        resp.raise_for_status()
        d = resp.json()
        return {
            "session_id": d.get("session_id"),
            "status": d.get("status", "queued"),
            "demo": {
                "style": body["prompt"],
                "paroles": body.get("custom_lyrics", "(instrumental)"),
            },
            "note": "Démo en génération. Suis avec statut_demo(session_id), "
            "puis resultat_demo(session_id) quand status=done.",
        }
    except httpx.ConnectError:
        return {"error": _UNREACHABLE_SUNO}
    except Exception as exc:
        logger.error("composer_demo: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
async def statut_demo(session_id: str) -> dict:
    """Avancement d'une démo lancée avec composer_demo.

    Args:
        session_id: id renvoyé par composer_demo.

    Returns:
        {"status", "progress", "step", "error_message"} ou {"error": "..."}.
        status ∈ queued | generating | mixing | done | error.
    """
    try:
        resp = await _ls_get(f"/sessions/{session_id}/status")
        if resp.status_code == 404:
            return {"error": f"session inconnue : {session_id}"}
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        return {"error": _UNREACHABLE_SUNO}
    except Exception as exc:
        logger.error("statut_demo: %s", exc, exc_info=True)
        return {"error": str(exc)}


@mcp.tool()
async def resultat_demo(session_id: str) -> dict:
    """Récupère la démo finale (status=done) : chemin du mix + stems.

    Args:
        session_id: id de la démo (de composer_demo).

    Returns:
        {"final_mix_path", "final_mix_url", "stems", "title", "detected_language",
         "lyrics", "generation_time_sec"} ; sinon {"status"} (pas prête) ou {"error": "..."}.
    """
    try:
        resp = await _ls_get(f"/sessions/{session_id}/result")
        if resp.status_code == 404:
            return {"error": f"session inconnue : {session_id}"}
        if resp.status_code == 202:
            return {"status": "en cours", "note": "démo pas encore terminée — réessaie."}
        resp.raise_for_status()
        d = resp.json()
        out_dir = _session_output_dir(session_id)
        mix = out_dir / "final_mix.wav"
        stems: dict[str, dict] = {}
        for name, url in (d.get("stems") or {}).items():
            p = out_dir / "stems" / f"{name}.wav"
            stems[name] = {"url": _abs_url(url), "path": str(p) if p.exists() else None}
        return {
            "session_id": session_id,
            "final_mix_url": _abs_url(d.get("final_mix_url", "")),
            "final_mix_path": str(mix) if mix.exists() else None,
            "stems": stems,
            "title": d.get("title"),
            "bpm": d.get("bpm"),
            "detected_language": d.get("detected_language"),
            "lyrics": d.get("lyrics"),
            "generation_time_sec": d.get("generation_time_sec"),
        }
    except httpx.ConnectError:
        return {"error": _UNREACHABLE_SUNO}
    except Exception as exc:
        logger.error("resultat_demo: %s", exc, exc_info=True)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------- #
# Entrée principale                                                            #
# ---------------------------------------------------------------------------- #


def main() -> None:
    transport = os.getenv("KLODYMUSIC_MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("KLODYMUSIC_MCP_PORT", "8088"))
    host = os.getenv("KLODYMUSIC_MCP_HOST", "127.0.0.1")

    if transport == "http":
        logger.info("KlodyMusic MCP HTTP : http://%s:%d", host, port)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("KlodyMusic MCP stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
