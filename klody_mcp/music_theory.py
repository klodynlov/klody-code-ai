"""Théorie musicale avancée — cœurs PURS pour la composition (P-musique).

Complète klody_music_server (tessiture, harmonisation, progressions, mélodie→MIDI)
avec les briques manquantes de COMPOSITION et de CHANT, toutes déterministes :

  - analyser_progression : analyse harmonique d'une suite d'accords (degrés romains,
    fonctions tonales T/S/D, cadence finale) — infère la tonalité si non fournie.
  - reharmoniser        : substitutions par accord (relatives diatoniques, substitut
    tritonique des dominantes, dominantes secondaires d'approche).
  - moduler             : accords PIVOTS communs à deux tonalités + approche dominante.
  - generer_basse       : ligne de basse (fondamentale/quinte/octave/walking) → events
    MIDI prêts pour REAPER, repliés dans le registre grave.
  - harmonies_vocales   : voix d'harmonie DIATONIQUE (tierce/sixte, haut/bas) + double
    à l'octave + repérage des notes en falsetto → events MIDI.

Aucune dépendance MCP/REAPER ici : music21 est importé en LAZY (le module se charge
même sans music21 ; chaque fonction renvoie {"error": ...} pointant l'install). Les
sorties MIDI ont la même forme que klody_music_server.melodie_vers_midi (pitch entier
0-127, start/length en secondes, velocity, note) → enchaînables tel quel dans REAPER
via insert_midi_note. Fidèle à la spec « ne jamais prétendre » : ce qui est incertain
(tonalité inférée, choix d'octave) est signalé, jamais présenté comme une vérité.
"""
from __future__ import annotations

import contextlib
import logging
import re

logger = logging.getLogger(__name__)

_MUSIC21_MANQUANT = (
    "music21 non installé dans le venv — installe-le : "
    "`pip install music21` (dans ~/Projets/klody-code-ai/.venv)."
)

# Registre de basse par défaut : E1 (MIDI 28) → E3 (MIDI 52). Couvre basse électrique/
# synthé sans descendre sous le seuil audible utile d'un système de diffusion courant.
_BASSE_LO = 28
_BASSE_HI = 52

# Passaggio approximatif (voix d'homme) : au-dessus, le registre bascule souvent en
# voix de tête/falsetto. HEURISTIQUE de repérage, pas une vérité par voix (à caler sur
# la tessiture réelle via evaluer_tessiture). ~E4 = MIDI 64.
_PASSAGGIO_DEFAUT = 64

# Pas diatoniques d'un intervalle d'harmonie (en degrés de gamme).
_INTERVALLE_PAS = {"tierce": 2, "sixte": 5, "quinte": 4, "octave": 7}


# ---------------------------------------------------------------------------- #
# Helpers (autonomes — mêmes conventions que klody_music_server)                #
# ---------------------------------------------------------------------------- #


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


def _parse_key(ton: str, m21):
    """Parse 'C', 'Am', 'F#', 'Bb', 'B♭ mineur', 'C sharp major'… -> key.Key."""
    s = _norm_in(str(ton).strip())
    s = re.sub(r"\s*(flat|bémol|bemol)\b", "-", s, flags=re.I)
    s = re.sub(r"\s*(sharp|dièse|diese)\b", "#", s, flags=re.I)
    m = re.match(r"^([A-Ga-g])\s*([#♯b♭-]?)(.*)$", s)
    if not m:
        raise ValueError(f"tonalité illisible : {ton!r} (ex: 'C', 'Am', 'F#', 'B♭ mineur').")
    acc = {"b": "-", "♭": "-", "♯": "#"}.get(m.group(2), m.group(2))
    tonic = m.group(1).upper() + acc
    rest = m.group(3).strip().lower()
    mineur = ("min" in rest) or (rest.startswith("m") and not rest.startswith("maj"))
    return m21.key.Key(tonic, "minor" if mineur else "major")


def _nom_ton(k) -> str:
    return f"{_pretty(k.tonic.name)} {'mineur' if k.mode == 'minor' else 'majeur'}"


def _armure(k) -> str:
    n = k.sharps
    if n == 0:
        return "aucune altération"
    return f"{abs(n)} {'♯' if n > 0 else '♭'}"


def _midi_to_name(midi: float, m21) -> str:
    p = m21.pitch.Pitch()
    p.midi = round(midi)
    return _pretty(p.nameWithOctave)


def _chord_symbol(sym: str, m21):
    """Parse un symbole d'accord ('C', 'Am7', 'G7', 'F#dim'…) -> ChordSymbol.

    Tolère les altérations Unicode. Lève ValueError si illisible (music21 lève des
    exceptions variées : on normalise vers ValueError pour un traitement homogène)."""
    raw = _norm_in(str(sym).strip())
    if not raw:
        raise ValueError("accord vide")
    try:
        return m21.harmony.ChordSymbol(raw)
    except Exception as exc:  # music21 lève ChordException/ValueError/… selon l'entrée
        raise ValueError(f"accord illisible : {sym!r}") from exc


def _suffixe_qualite(qualite: str) -> str:
    """Qualité music21 ('major'/'minor'/'diminished'/'augmented') -> suffixe d'accord."""
    return {"major": "", "minor": "m", "diminished": "°", "augmented": "+"}.get(qualite, "")


def _nom_accord(root_name: str, qualite: str) -> str:
    return f"{_pretty(root_name)}{_suffixe_qualite(qualite)}"


# ---------------------------------------------------------------------------- #
# Analyse harmonique                                                            #
# ---------------------------------------------------------------------------- #

# Fonction tonale par degré de gamme (1..7). Convention fonctionnelle standard :
# I/iii/vi = Tonique, ii/IV = Sous-dominante, V/viio = Dominante.
_FONCTION = {1: "Tonique", 2: "Sous-dominante", 3: "Tonique", 4: "Sous-dominante",
             5: "Dominante", 6: "Tonique", 7: "Dominante"}


def _inferer_tonalite(chords, m21):
    """Meilleure tonalité pour une suite d'accords : maximise les accords diatoniques.

    Balaie les 24 tonalités ; score = nombre d'accords dont TOUTES les classes de
    hauteur sont dans la gamme. Départage : tonique = fondamentale du 1er ou dernier
    accord (repère fort du centre tonal). Renvoie (key, score, total)."""
    total = len(chords)
    premier = chords[0].root().pitchClass
    dernier = chords[-1].root().pitchClass
    best = None
    for tonic_pc in range(12):
        p = m21.pitch.Pitch()
        p.midi = 60 + tonic_pc
        for mode in ("major", "minor"):
            k = m21.key.Key(p.name, mode)
            scale_pcs = {sp.pitchClass for sp in k.pitches}
            diat = sum(
                1 for c in chords
                if {pt.pitchClass for pt in c.pitches} <= scale_pcs
            )
            # Bonus de départage (< 1 pour ne jamais dépasser un accord diatonique) :
            # tonique alignée sur le 1er/dernier accord + préférence légère au majeur.
            bonus = 0.0
            if tonic_pc == dernier:
                bonus += 0.4
            if tonic_pc == premier:
                bonus += 0.3
            if mode == "major":
                bonus += 0.05
            score = diat + bonus
            if best is None or score > best[0]:
                best = (score, diat, k)
    return best[2], best[1], total


def _cadence(degres) -> str | None:
    """Nomme la cadence d'après les deux derniers degrés (chiffres 1..7)."""
    if len(degres) < 2:
        return None
    a, b = degres[-2], degres[-1]
    if a == 5 and b == 1:
        return "parfaite (V→I) — conclusion forte"
    if a == 4 and b == 1:
        return "plagale (IV→I) — « Amen », douce"
    if a == 5 and b == 6:
        return "rompue (V→vi) — surprise, relance"
    if b == 5:
        return "demi-cadence (→V) — suspension, à poursuivre"
    if a == 7 and b == 1:
        return "parfaite (viio→I) — tension résolue"
    return None


def analyser_progression(accords, ton: str = "") -> dict:
    """Analyse harmonique d'une suite d'accords : degrés romains + fonctions + cadence.

    Si `ton` est vide, la tonalité est INFÉRÉE (et signalée comme hypothèse)."""
    try:
        m21 = _import_music21()
    except ImportError as exc:
        return {"error": str(exc)}
    if not accords or not isinstance(accords, (list, tuple)):
        return {"error": "accords doit être une liste non vide (ex: ['C','G','Am','F'])."}
    try:
        chords = [_chord_symbol(a, m21) for a in accords]
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        if ton:
            k = _parse_key(ton, m21)
            infere = False
        else:
            k, diat, total = _inferer_tonalite(chords, m21)
            infere = True

        analyse = []
        degres = []
        for sym, c in zip(accords, chords, strict=True):
            rn = m21.roman.romanNumeralFromChord(c, k)
            deg = int(rn.scaleDegree)
            degres.append(deg)
            diatonique = {pt.pitchClass for pt in c.pitches} <= {sp.pitchClass for sp in k.pitches}
            analyse.append({
                "accord": _pretty(str(sym)),
                "degre": rn.figure,
                "fonction": _FONCTION.get(deg, "—"),
                "diatonique": bool(diatonique),
            })
        out = {
            "ton": _nom_ton(k),
            "ton_infere": infere,
            "accords": analyse,
            "cadence_finale": _cadence(degres),
            "note": "Fonctions : Tonique (repos), Sous-dominante (élan), Dominante "
            "(tension → veut résoudre vers la Tonique).",
        }
        if infere:
            # diat/total ne sont liés que dans la branche `else` ci-dessus — lus
            # UNIQUEMENT ici, sous la même condition (infere) : toujours définis.
            out["confiance_tonalite"] = round(diat / total, 2) if total else None
            out["note_tonalite"] = (
                "Tonalité INFÉRÉE (hypothèse) : passe `ton` explicitement pour l'imposer."
            )
        return out
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("analyser_progression: %s", exc, exc_info=True)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------- #
# Reharmonisation                                                              #
# ---------------------------------------------------------------------------- #

# Substituts diatoniques par degré (partagent 2 notes → interchangeables sans casser
# la ligne). Chiffres romains de la tonalité courante.
_SUBSTITUT_RELATIF = {
    1: [("vi", "relative mineure — même couleur, plus douce"),
        ("iii", "médiante — tonique colorée")],
    4: [("ii", "relatif sous-dominante — plus mobile")],
    5: [("viio", "sensible — dominante sans fondamentale")],
    6: [("I", "relatif majeur — ouvre la couleur")],
    2: [("IV", "sous-dominante — plus assise")],
    3: [("I", "tonique colorée")],
}


def reharmoniser(accords, ton: str = "") -> dict:
    """Propose, par accord, des substitutions harmoniques (couleur/tension).

    Trois familles : substitut diatonique (relatif partageant des notes), substitut
    TRITONIQUE des dominantes (7e), et dominante SECONDAIRE d'approche (V7/x) devant
    l'accord SUIVANT. Toutes restent des propositions à essayer, pas des règles."""
    try:
        m21 = _import_music21()
    except ImportError as exc:
        return {"error": str(exc)}
    if not accords or not isinstance(accords, (list, tuple)):
        return {"error": "accords doit être une liste non vide (ex: ['C','G','Am','F'])."}
    try:
        chords = [_chord_symbol(a, m21) for a in accords]
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        if ton:
            k = _parse_key(ton, m21)
            infere = False
        else:
            k, _diat, _total = _inferer_tonalite(chords, m21)
            infere = True

        degres = [int(m21.roman.romanNumeralFromChord(c, k).scaleDegree) for c in chords]
        propositions = []
        for i, (sym, c) in enumerate(zip(accords, chords, strict=True)):
            subs = []
            # 1) Substituts diatoniques relatifs.
            for fig, why in _SUBSTITUT_RELATIF.get(degres[i], []):
                rn = m21.roman.RomanNumeral(fig, k)
                subs.append({
                    "accord": _nom_accord(rn.root().name, rn.quality),
                    "type": "relatif diatonique", "explication": why,
                })
            # 2) Substitut tritonique (dominantes 7e) : dominante à un triton, glisse
            # la basse d'un demi-ton vers la cible.
            if c.isDominantSeventh():
                sub_root = c.root().transpose(6)
                # Convention jazz : le substitut tritonique se note en BÉMOL
                # (G7 → D♭7, pas C♯7). On prend l'enharmonie bémol si besoin.
                if "#" in sub_root.name:
                    sub_root = sub_root.getEnharmonic()
                subs.append({
                    "accord": f"{_pretty(sub_root.name)}7",
                    "type": "substitut tritonique",
                    "explication": "dominante à un triton — basse chromatique descendante vers la cible",
                })
            # 3) Dominante secondaire d'approche vers l'accord SUIVANT (si diatonique
            # non-tonique : ii/iii/IV/V/vi). contextlib.suppress plutôt qu'un except-vide
            # (que CodeQL py/empty-except signale) si la figure secondaire n'est pas résoluble.
            if i + 1 < len(chords) and degres[i + 1] in (2, 3, 4, 5, 6):
                with contextlib.suppress(Exception):
                    rn = m21.roman.RomanNumeral(f"V7/{_fig_simple(degres[i + 1])}", k)
                    subs.append({
                        "accord": f"{_pretty(rn.root().name)}7",
                        "type": "dominante secondaire (approche)",
                        "explication": f"V7 de l'accord suivant ({_pretty(str(accords[i + 1]))}) "
                        "— insère-la AVANT lui pour le renforcer",
                    })
            propositions.append({"accord": _pretty(str(sym)), "substituts": subs})
        out = {"ton": _nom_ton(k), "ton_infere": infere, "propositions": propositions,
               "note": "Substituts à ESSAYER : garde ceux qui servent la mélodie. "
               "Le substitut tritonique et la dominante secondaire ajoutent de la tension jazz."}
        return out
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("reharmoniser: %s", exc, exc_info=True)
        return {"error": str(exc)}


def _fig_simple(degre: int) -> str:
    """Degré 1..7 -> chiffre romain diatonique 'simple' pour une cible de V7/x."""
    return {1: "I", 2: "ii", 3: "iii", 4: "IV", 5: "V", 6: "vi", 7: "vii"}[degre]


# ---------------------------------------------------------------------------- #
# Modulation                                                                   #
# ---------------------------------------------------------------------------- #


def moduler(ton_depart: str, ton_arrivee: str) -> dict:
    """Chemin de modulation entre deux tonalités : accords PIVOTS + approche dominante.

    Un pivot est un accord diatonique DANS LES DEUX tonalités : il appartient déjà à
    l'arrivée, donc on peut y basculer sans rupture. On donne son degré dans chaque
    tonalité. À défaut/en complément : approche par la dominante de l'arrivée."""
    try:
        m21 = _import_music21()
    except ImportError as exc:
        return {"error": str(exc)}
    try:
        k1 = _parse_key(ton_depart, m21)
        k2 = _parse_key(ton_arrivee, m21)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        figs1 = ["I", "ii", "iii", "IV", "V", "vi", "viio"] if k1.mode == "major" \
            else ["i", "iio", "III", "iv", "v", "VI", "VII"]
        figs2 = ["I", "ii", "iii", "IV", "V", "vi", "viio"] if k2.mode == "major" \
            else ["i", "iio", "III", "iv", "v", "VI", "VII"]
        # Index des accords de k2 par ensemble de classes de hauteur.
        k2_index = {}
        for f in figs2:
            rn = m21.roman.RomanNumeral(f, k2)
            k2_index[frozenset(p.pitchClass for p in rn.pitches)] = (f, rn)
        pivots = []
        for f in figs1:
            rn1 = m21.roman.RomanNumeral(f, k1)
            key_pcs = frozenset(p.pitchClass for p in rn1.pitches)
            if key_pcs in k2_index:
                f2, _rn2 = k2_index[key_pcs]
                pivots.append({
                    "accord": _nom_accord(rn1.root().name, rn1.quality),
                    "degre_depart": f, "degre_arrivee": f2,
                })
        v_arr = m21.roman.RomanNumeral("V7", k2)
        approche = {
            "dominante_arrivee": f"{_pretty(v_arr.root().name)}7",
            "explication": f"V7 de {_nom_ton(k2)} — pose-la juste avant la nouvelle tonique "
            "pour verrouiller l'arrivée (marche même sans pivot).",
        }
        return {
            "depart": _nom_ton(k1), "arrivee": _nom_ton(k2),
            "distance_quintes": _distance_quintes(k1, k2),
            "pivots": pivots,
            "approche_dominante": approche,
            "note": "Recette : joue un accord PIVOT (commun aux deux), puis la dominante "
            "de l'arrivée, puis sa tonique. Plus il y a de pivots, plus la modulation est douce."
            if pivots else "Pas de pivot diatonique commun (tonalités éloignées) : "
            "passe par l'approche dominante, ou module en deux temps via une tonalité proche.",
        }
    except Exception as exc:
        logger.error("moduler: %s", exc, exc_info=True)
        return {"error": str(exc)}


def _distance_quintes(k1, k2) -> int:
    """Écart sur le cycle des quintes (0 = même armure, 6 = opposé)."""
    d = abs(k1.sharps - k2.sharps) % 12
    return min(d, 12 - d)


# ---------------------------------------------------------------------------- #
# Génération de basse                                                          #
# ---------------------------------------------------------------------------- #


def _replier(pitch: int, lo: int, hi: int) -> int:
    """Replie `pitch` dans [lo, hi] par octaves, puis clampe."""
    while pitch < lo:
        pitch += 12
    while pitch > hi:
        pitch -= 12
    return max(lo, min(hi, pitch))


def generer_basse(
    accords, motif: str = "fondamentale", duree_accord: float = 2.0,
    debut: float = 0.0, velocity: int = 100, octave_min: int = _BASSE_LO,
    octave_max: int = _BASSE_HI,
) -> dict:
    """Génère une ligne de basse à partir d'une suite d'accords → events MIDI.

    Motifs : 'fondamentale' (une ronde par accord), 'quinte' (fondamentale+quinte),
    'octave' (fondamentale + octave), 'walking' (fondamentale-tierce-quinte-approche,
    croches de marche jazz). Les hauteurs sont repliées dans [octave_min, octave_max].
    Sortie = même forme que melodie_vers_midi (enchaînable dans REAPER)."""
    try:
        m21 = _import_music21()
    except ImportError as exc:
        return {"error": str(exc)}
    if not accords or not isinstance(accords, (list, tuple)):
        return {"error": "accords doit être une liste non vide (ex: ['C','G','Am','F'])."}
    mtf = (motif or "fondamentale").strip().lower()
    if mtf not in ("fondamentale", "quinte", "octave", "walking"):
        return {"error": f"motif inconnu : {motif!r} (fondamentale, quinte, octave, walking)."}
    if duree_accord <= 0:
        return {"error": "duree_accord doit être > 0."}
    if octave_max <= octave_min:
        return {"error": "octave_max doit être > octave_min."}
    try:
        chords = [_chord_symbol(a, m21) for a in accords]
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        vel = max(1, min(127, int(velocity)))
        events = []
        t = float(debut)
        for i, c in enumerate(chords):
            root = c.root().midi
            third = c.third.midi if c.third is not None else root + 4
            fifth = c.fifth.midi if c.fifth is not None else root + 7
            # Note d'approche du walking : demi-ton sous la fondamentale suivante.
            nxt = chords[(i + 1) % len(chords)].root().midi
            approche = nxt - 1
            if mtf == "fondamentale":
                steps = [(root, duree_accord)]
            elif mtf == "quinte":
                steps = [(root, duree_accord / 2), (fifth, duree_accord / 2)]
            elif mtf == "octave":
                steps = [(root, duree_accord / 2), (root + 12, duree_accord / 2)]
            else:  # walking : 4 temps
                q = duree_accord / 4
                steps = [(root, q), (third, q), (fifth, q), (approche, q)]
            for pitch, length in steps:
                p = _replier(int(pitch), octave_min, octave_max)
                events.append({
                    "pitch": p, "start": round(t, 4), "length": round(length, 4),
                    "velocity": vel, "note": _midi_to_name(p, m21),
                })
                t += length
        pitches = [e["pitch"] for e in events]
        return {
            "motif": mtf, "events": events, "count": len(events),
            "duree_totale_sec": round(t - float(debut), 4),
            "ambitus_midi": [min(pitches), max(pitches)],
            "note": "Prêt pour REAPER : pour chaque event, insert_midi_note(track_index=<basse>, "
            "pitch=event['pitch'], start=event['start'], length=event['length'], "
            "velocity=event['velocity']).",
        }
    except (ValueError, m21.exceptions21.Music21Exception) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("generer_basse: %s", exc, exc_info=True)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------- #
# Harmonies vocales                                                            #
# ---------------------------------------------------------------------------- #


def _scale_midis(k, m21, lo: int = 24, hi: int = 96) -> list[int]:
    """Toutes les hauteurs MIDI de la gamme de k dans [lo, hi], triées."""
    pcs = {sp.pitchClass for sp in k.pitches}
    return [m for m in range(lo, hi + 1) if m % 12 in pcs]


def harmonies_vocales(
    notes, ton: str, intervalle: str = "tierce", direction: str = "haut",
    duree_note: float = 0.5, durees=None, debut: float = 0.0, velocity: int = 88,
    double_octave: bool = False, passaggio: int = _PASSAGGIO_DEFAUT,
) -> dict:
    """Génère une voix d'HARMONIE diatonique pour une mélodie → events MIDI.

    Pour chaque note, prend la note de la gamme `ton` à `intervalle` degrés au-dessus
    (direction='haut') ou en dessous ('bas') : tierce/sixte/quinte diatoniques (donc
    majeures OU mineures selon le degré — l'harmonie reste DANS la tonalité). Options :
    `double_octave` ajoute une piste de DOUBLE à l'octave inférieure (épaissit sans
    changer l'accord) ; les notes de la mélodie au-dessus de `passaggio` (MIDI) sont
    signalées comme candidates FALSETTO (voix de tête). Sortie enchaînable dans REAPER."""
    try:
        m21 = _import_music21()
    except ImportError as exc:
        return {"error": str(exc)}
    if not notes or not isinstance(notes, (list, tuple)):
        return {"error": "notes doit être une liste non vide (ex: ['E3','G3','B3'])."}
    itv = (intervalle or "tierce").strip().lower()
    if itv not in _INTERVALLE_PAS:
        return {"error": f"intervalle inconnu : {intervalle!r} (tierce, sixte, quinte, octave)."}
    dir_ = (direction or "haut").strip().lower()
    if dir_ not in ("haut", "bas"):
        return {"error": f"direction inconnue : {direction!r} (haut ou bas)."}
    try:
        k = _parse_key(ton, m21)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        if durees is not None:
            if len(durees) != len(notes):
                return {"error": f"durees ({len(durees)}) doit avoir autant d'éléments que notes ({len(notes)})."}
            longueurs = [max(0.01, float(d)) for d in durees]
        else:
            longueurs = [max(0.01, float(duree_note))] * len(notes)

        scale = _scale_midis(k, m21)
        pas = _INTERVALLE_PAS[itv] * (1 if dir_ == "haut" else -1)
        vel = max(1, min(127, int(velocity)))

        events = []          # voix d'harmonie
        double = []          # double à l'octave (optionnel)
        falsetto = []        # index des notes mélodie candidates falsetto
        t = float(debut)
        for i, (nom, length) in enumerate(zip(notes, longueurs, strict=True)):
            try:
                mel = m21.pitch.Pitch(_norm_in(nom)).midi
            except Exception as exc:
                raise ValueError(f"note illisible : {nom!r}") from exc
            # Indexe la note dans la gamme (plus proche degré), puis décale de `pas`.
            idx = min(range(len(scale)), key=lambda j: abs(scale[j] - mel))
            j = max(0, min(len(scale) - 1, idx + pas))
            h = scale[j]
            events.append({
                "pitch": h, "start": round(t, 4), "length": round(length, 4),
                "velocity": vel, "note": _midi_to_name(h, m21),
                "melodie": _midi_to_name(mel, m21),
            })
            if double_octave:
                d = max(0, mel - 12)
                double.append({
                    "pitch": d, "start": round(t, 4), "length": round(length, 4),
                    "velocity": vel, "note": _midi_to_name(d, m21),
                })
            if mel >= passaggio:
                falsetto.append({"index": i, "note": _midi_to_name(mel, m21)})
            t += length
        out = {
            "ton": _nom_ton(k), "intervalle": itv, "direction": dir_,
            "events": events, "count": len(events),
            "duree_totale_sec": round(t - float(debut), 4),
            "falsetto": falsetto,
            "note": "Voix d'harmonie DIATONIQUE (reste dans la tonalité). Insère-la sur "
            "une piste séparée (insert_midi_note) ; pour un rendu chanté, passe par vocalbrain.",
        }
        if double_octave:
            out["double_octave"] = double
        if falsetto:
            out["note_falsetto"] = (
                f"{len(falsetto)} note(s) ≥ passaggio (MIDI {passaggio}) — probablement "
                "en voix de tête : chaîne vocale plus légère (moins de compression, "
                "de-esser doux, réverbe plus longue)."
            )
        return out
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("harmonies_vocales: %s", exc, exc_info=True)
        return {"error": str(exc)}
