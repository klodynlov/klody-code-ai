"""Pont mince vers Libretto / Forge (`~/Projets/Libretto`, dépôt voisin).

Libretto note la **structure** d'un MIDI : 29 axes pondérés (forme, harmonie,
mélodie, rythme, texture, cohérence) → un score SMS [0,1] + une fiabilité.
Forge (`examples/forge.py`) branche un générateur dessus : N ébauches, chacune
notée, on garde la meilleure — fiabilité d'abord, score ensuite.

Rôle de ce module dans la chaîne Klody :

    Forge (génère N)  →  Libretto (juge + GATE)  →  [ici]  →  Gadget/REAPER
                                     ↑
                    rien ne part vers le DAW sans avoir passé le gate

Deux mécanismes d'accès, chacun choisi pour ce qu'il est :
- **Forge = sous-processus** : c'est une CLI qui écrit des fichiers ; on
  l'isole (argv liste, `shell=False`, cwd du dépôt — convention
  `tools/git_tools.py`).
- **Libretto = import paresseux** : `libretto.midi`/`axes`/`builder` sont des
  modules bibliothèque 100 % stdlib. Import DANS les fonctions, jamais au
  chargement : Libretto est une dépendance OPTIONNELLE hors requirements (même
  patron que les extras de `audio_analysis`) — absent, la CI et les autres
  outils Gadget continuent de tourner, seul l'outil concerné renvoie une
  erreur exploitable.

La demande peut être COMMANDÉE plutôt que subie : `build_constraints` traduit
« 16 mesures en fa mineur, style R&B » en contraintes dures que Forge impose
au générateur (cf. `STYLE_PRESETS`). Sans elles, tout est tiré au sort et la
graine est la seule poignée — elle ne dit rien de ce qui sortira.

Racine : `LIBRETTO_ROOT` (env) sinon `~/Projets/Libretto`.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404 — argv liste, shell=False (cf. tools/git_tools)
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = Path.home() / "Projets" / "Libretto"

# Le pont REAPER garde chaque ligne JSON sous 64 KiB → on découpe les notes.
# Même valeur que `libretto/reaper.py::CHUNK` (contrat commun au même pont).
NOTE_CHUNK = 120

# Canal MIDI 10 (index 9) = percussions General MIDI. Signal explicite du
# générateur : plus fiable que n'importe quelle heuristique de tessiture.
GM_DRUM_CHANNEL = 9


class LibrettoUnavailable(RuntimeError):
    """Libretto introuvable ou inutilisable — message actionnable pour le LLM."""


def libretto_root() -> Path:
    return Path(os.getenv("LIBRETTO_ROOT", str(_DEFAULT_ROOT))).expanduser()


def _require_root() -> Path:
    root = libretto_root()
    if not (root / "libretto" / "axes.py").is_file():
        raise LibrettoUnavailable(
            f"Libretto introuvable sous {root} — cloner le dépôt ou définir "
            "LIBRETTO_ROOT vers sa racine."
        )
    return root


def _import_libretto() -> Any:
    """Import paresseux du paquet Libretto (stdlib pur, hors requirements)."""
    root = _require_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import libretto
    except ImportError as exc:  # dépôt présent mais cassé/incomplet
        raise LibrettoUnavailable(f"import de Libretto impossible depuis {root} : {exc}") from exc
    return libretto


def status() -> dict:
    """État du pont Libretto — diagnostic, ne lève jamais."""
    root = libretto_root()
    try:
        _require_root()
    except LibrettoUnavailable as exc:
        return {"available": False, "root": str(root), "detail": str(exc)}
    return {
        "available": True,
        "root": str(root),
        "forge": (root / "examples" / "forge.py").is_file(),
    }


# --------------------------------------------------------------------------- #
# Contraintes — traduire une demande humaine en commande pour le générateur   #
# --------------------------------------------------------------------------- #

# Champs que Forge sait imposer. La liste sert de garde : un LLM invente vite
# une clé (« genre », « ambiance ») qui serait silencieusement ignorée.
CONSTRAINT_FIELDS = ("tonic", "mode", "bpm", "meter", "bars", "swing",
                     "syncopation", "drums")
_BOOL_CONSTRAINTS = ("swing", "drums")

# Ce que ces presets règlent : la CARRURE (tempo, métrique, swing, syncope,
# mode). Pas la couleur harmonique — le générateur de Forge ne construit que
# des TRIADES, sans septièmes ni neuvièmes. « rnb » produit donc un mineur
# lent, swingué et syncopé : la charpente du genre, pas son harmonie. Le dire
# ici plutôt que laisser croire à une imitation.
STYLE_PRESETS: dict[str, dict] = {
    "rnb":     {"mode": "min", "bpm": 72, "meter": "4/4", "swing": True,
                "syncopation": 0.45, "drums": True},
    "lofi":    {"mode": "min", "bpm": 78, "meter": "4/4", "swing": True,
                "syncopation": 0.45, "drums": True},
    "house":   {"mode": "min", "bpm": 124, "meter": "4/4", "swing": False,
                "syncopation": 0.25, "drums": True},
    "ballade": {"mode": "min", "bpm": 64, "meter": "4/4", "swing": False,
                "syncopation": 0.0, "drums": False},
    "valse":   {"mode": "maj", "bpm": 96, "meter": "3/4", "swing": False,
                "syncopation": 0.0, "drums": False},
}

_STYLE_ALIASES = {
    "rb": "rnb", "rnb": "rnb", "rhythmandblues": "rnb", "soul": "rnb",
    "lofi": "lofi", "lowfi": "lofi", "chill": "lofi", "hiphop": "lofi",
    "house": "house", "dance": "house", "techno": "house", "edm": "house",
    "ballade": "ballade", "ballad": "ballade", "slow": "ballade",
    "valse": "valse", "waltz": "valse",
}


def resolve_style(style: str) -> dict:
    """Preset de carrure depuis « R&B », « rnb », « lo-fi »… Lève ValueError."""
    key = "".join(ch for ch in str(style).lower() if ch.isalnum())
    name = _STYLE_ALIASES.get(key)
    if name is None:
        raise ValueError(
            f"style inconnu : {style!r} — connus : {', '.join(sorted(STYLE_PRESETS))}")
    return dict(STYLE_PRESETS[name])


def build_constraints(key: str | None = None, mode: str | None = None,
                      bars: int | None = None, bpm: float | None = None,
                      meter: str | None = None,
                      style: str | None = None) -> dict:
    """Assemble les contraintes Forge. Preset d'abord, explicite ensuite.

    L'ordre compte : un `style` pose une carrure par défaut, chaque valeur
    donnée nommément l'écrase. « R&B en fa mineur à 90 bpm » doit sortir à 90,
    pas au tempo du preset.

    Les noms de notes et de modes ne sont PAS interprétés ici : Forge a déjà
    ses parseurs (`parse_tonic`/`parse_mode`), qui restent la seule autorité.
    Un second vocabulaire divergerait tôt ou tard.
    """
    cons: dict = {}
    if style is not None:
        cons.update(resolve_style(style))
    for name, value in (("tonic", key), ("mode", mode), ("bars", bars),
                        ("bpm", bpm), ("meter", meter)):
        if value is not None:
            cons[name] = value
    return cons


def _constraint_argv(cons: dict) -> list[str]:
    """Contraintes → options de ligne de commande, dans un ordre stable."""
    unknown = sorted(set(cons) - set(CONSTRAINT_FIELDS))
    if unknown:
        raise ValueError(
            f"contrainte(s) inconnue(s) : {', '.join(unknown)} — "
            f"attendu parmi {', '.join(CONSTRAINT_FIELDS)}")
    argv: list[str] = []
    for name in CONSTRAINT_FIELDS:
        value = cons.get(name)
        if value is None:
            continue
        if name in _BOOL_CONSTRAINTS:
            argv.append(f"--{name}" if value else f"--no-{name}")
        else:
            argv += [f"--{name}", str(value)]
    return argv


# --------------------------------------------------------------------------- #
# Forge — génération + sélection (sous-processus)                             #
# --------------------------------------------------------------------------- #


def run_forge(out_dir: Path, n: int = 12, seed: int = 1,
              min_confidence: float = 0.55, min_score: float = 0.0,
              timeout: float = 600.0, constraints: dict | None = None) -> dict:
    """Lance Forge : n ébauches notées, la meilleure gagne.

    `constraints` impose tonalité, mode, tempo, métrique ou longueur (voir
    `build_constraints`). Sans elles, Forge tire tout au sort et la graine est
    la seule poignée.

    Renvoie le rapport JSON de Forge enrichi de `winner_path` — qui vaut None
    quand le gate n'a laissé passer personne. Lève LibrettoUnavailable si le
    dépôt manque, RuntimeError si Forge échoue vraiment.
    `sys.executable` (et pas « python3 ») : Libretto est stdlib pur, donc
    l'interpréteur du venv convient et est garanti présent.
    """
    root = _require_root()
    forge = root / "examples" / "forge.py"
    if not forge.is_file():
        raise LibrettoUnavailable(f"forge.py absent de {root / 'examples'}")
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / "forge_report.json"
    # Un rapport laissé par un appel précédent se lirait comme le résultat de
    # celui-ci : la présence du fichier sert à distinguer gate et échec.
    report_path.unlink(missing_ok=True)

    argv = [
        sys.executable, str(forge), str(out_dir), str(int(n)), str(int(seed)),
        "--min-confidence", str(float(min_confidence)),
        "--min-score", str(float(min_score)),
    ]
    argv += _constraint_argv(constraints or {})
    try:
        proc = subprocess.run(  # nosec B603 — argv liste, shell=False, chemins validés
            argv, cwd=str(root), capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Forge n'a pas fini en {timeout}s (n={n}) — baisser n.") from exc

    # Forge sort en 2 quand AUCUN candidat ne passe le gate — c'est un
    # résultat, pas une panne. Ce qui départage : le rapport a été écrit.
    # Rien d'écrit = échec réel (contrainte refusée, dépôt cassé, crash).
    if not report_path.is_file():
        err = (proc.stderr or proc.stdout or "").strip()
        tail = " / ".join(err.splitlines()[-5:])
        if constraints and "unrecognized arguments" in err:
            raise RuntimeError(
                f"cette version de Libretto ne connaît pas les contraintes "
                f"({tail}) — mettre à jour {root}.")
        raise RuntimeError(
            f"Forge a échoué (code {proc.returncode}) sans écrire "
            f"{report_path.name} : {tail}")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    winner_file = report.get("winner_file")
    report["winner_path"] = str(out_dir / winner_file) if winner_file else None
    return report


# --------------------------------------------------------------------------- #
# Libretto — jugement + lecture MIDI (import paresseux)                       #
# --------------------------------------------------------------------------- #


def analyze_midi(midi_path: Path) -> dict:
    """Score SMS d'un MIDI : score global, fiabilité, groupes d'axes."""
    _import_libretto()
    from libretto.axes import SenseOfMusicalStructure
    from libretto.builder import build_score
    from libretto.midi import parse_midi

    score_obj = build_score(parse_midi(str(midi_path)))
    if not score_obj.sections:
        return {"error": "aucune section analysable dans ce MIDI"}
    sms = SenseOfMusicalStructure(score_obj)
    sms.calculate()
    return {
        "score": round(sms.get_score(), 4),
        "confidence": round(sms.confidence(), 4),
        "level": sms.confidence_level(),
        "interpretable": sms.is_interpretable(),
        "groups": {g: round(v, 3) for g, v in sms.group_scores().items()},
        "sections": len(score_obj.sections),
    }


def midi_to_tracks(midi_path: Path) -> dict:
    """Décompose un MIDI en pistes prêtes pour le pont REAPER.

    Notes converties en SECONDES via la tempo map (réutilise
    `libretto.reaper.tick_to_seconds` — le même convertisseur que Libretto
    pousse déjà dans ce pont, donc pas de seconde vérité). Chaque piste
    reçoit un `role` déduit (voir `_assign_roles`).
    """
    _import_libretto()
    from libretto.midi import parse_midi
    from libretto.reaper import tick_to_seconds

    md = parse_midi(str(midi_path))
    to_sec = tick_to_seconds(md.tempos, md.ppq)

    by_track: dict[int, list] = {}
    for note in md.notes:
        by_track.setdefault(note.track, []).append(note)
    if not by_track:
        return {"error": f"aucune note dans {midi_path.name}"}

    tracks = []
    for src in sorted(by_track):
        notes = sorted(by_track[src], key=lambda n: n.start)
        pitches = [n.pitch for n in notes]
        tracks.append({
            "source_track": src,
            "channel": notes[0].channel,
            "note_count": len(notes),
            "mean_pitch": round(sum(pitches) / len(pitches), 1),
            "pitch_min": min(pitches),
            "pitch_max": max(pitches),
            "notes": [{
                "pitch": n.pitch,
                "start": round(to_sec(n.start), 4),
                # Plancher 0.03 s : une note de longueur nulle est invisible
                # dans REAPER (même garde que libretto/reaper.py).
                "length": round(max(0.03, to_sec(n.end) - to_sec(n.start)), 4),
                "velocity": n.velocity,
                "channel": n.channel,
            } for n in notes],
        })

    _assign_roles(tracks)
    first_bpm = (sorted(md.tempos) or [(0, 120.0)])[0][1]
    return {
        "tempo": round(first_bpm, 3),
        "tracks": tracks,
        "markers": [{"position": round(to_sec(t), 4), "name": txt} for t, txt in md.markers],
        "total_notes": sum(t["note_count"] for t in tracks),
    }


def _assign_roles(tracks: list[dict]) -> None:
    """Attribue un rôle à chaque piste, en place. Déterministe.

    1. canal 9 = percussions GM → `drums` (signal explicite, pas d'heuristique).
    2. Le reste est classé par tessiture moyenne : la plus grave = `bass`, la
       plus aiguë = `lead`, les autres = `chords`. Avec une seule piste
       mélodique, elle est `lead` (une pièce mono-piste n'a pas de « basse »).
    """
    melodic = [t for t in tracks if t["channel"] != GM_DRUM_CHANNEL]
    for t in tracks:
        if t["channel"] == GM_DRUM_CHANNEL:
            t["role"] = "drums"
    if not melodic:
        return
    ordered = sorted(melodic, key=lambda t: t["mean_pitch"])
    for t in ordered:
        t["role"] = "chords"
    ordered[-1]["role"] = "lead"
    if len(ordered) > 1:
        ordered[0]["role"] = "bass"
