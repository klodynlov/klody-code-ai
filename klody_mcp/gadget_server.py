"""KORG Gadget MCP server — pilote Gadget de manière INDIRECTE.

L'application KORG Gadget (macOS) n'est pas scriptable : aucun dictionnaire
AppleScript (pas de .sdef, NSAppleScriptEnabled absent), aucune API réseau.
Ce serveur la pilote donc par ses deux portes dérobées légitimes :

1. **Ses instruments sont installés en VST** (`/Library/Audio/Plug-Ins/VST/KORG/`,
   ~40 gadgets « villes » : Chicago, London, Phoenix…). On les héberge dans
   REAPER via le pont socket existant (`reaper_bridge/klody_reaper_bridge.py`,
   :9000) — même chaîne validée que reaper_server (43 outils).

2. **Son format projet `.gdproj2` est lisible** (rétro-ingénierie 22/07/26) :
   `<Nom>.gdproj2/<Nom>.gddat` = NSKeyedArchiver (plist binaire) contenant la
   tonalité (`project_scale_data` : Key/Scale) et un ZIP embarqué (`project_data`,
   magic PK) : `root/seqs/seq.dat` (tempo float32 LE à l'offset 8, version à 0),
   `root/tracks/<n>/plugins/<m>/plugin_info` (plist XML `{"Name": "<gadget>"}`),
   `root/buses/`, `root/master/`. Lecture 100 % stdlib (plistlib + zipfile).

Surface LLM : verbes métier, pas de mapping 1:1 (principe reaper_server).
Les projets Gadget ne sont JAMAIS écrits — lecture seule stricte ; les
mutations vont dans REAPER (encadrées par ses blocs Undo côté pont).

Démarrage :
    python -m klody_mcp.gadget_server                              # stdio (défaut)
    GADGET_MCP_TRANSPORT=http python -m klody_mcp.gadget_server    # :8093

Prérequis pilotage : REAPER lancé + pont chargé (auto via __startup.lua).
La lecture de projets, elle, ne requiert rien.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import plistlib
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP

from klody_mcp import libretto_forge
from klody_mcp._pathguard import PathGuardViolation, safe_path
from klody_mcp.reaper_server import _bridge_call

load_dotenv()

logger = logging.getLogger(__name__)

mcp = FastMCP("Gadget")

# Le pont REAPER par défaut lâche à 5 s : trop court pour un lot de notes ou un
# render, que REAPER termine pourtant (faux « pas de réponse »). Cf. reaper_server.
_INSERT_TIMEOUT = float(os.getenv("GADGET_INSERT_TIMEOUT", "30"))
_RENDER_TIMEOUT = float(os.getenv("GADGET_RENDER_TIMEOUT", "300"))

# --------------------------------------------------------------------------- #
# Catalogue des instruments (scan disque, jamais de liste codée en dur)       #
# --------------------------------------------------------------------------- #

# Dossiers d'installation des gadgets en plugin. Le VST est la référence (REAPER
# le scanne déjà : « Chicago (KORG)!!!VSTi » dans reaper-vstplugins_arm64.ini).
_VST_KORG_DIR = Path("/Library/Audio/Plug-Ins/VST/KORG")
_GADGET_APP = Path("/Applications/KORG Gadget.app")

# Plugins internes du format projet qui ne sont PAS des instruments : les
# recréer dans REAPER n'aurait pas de sens (mixage/limiteur propres à Gadget).
_INTERNAL_PLUGINS = {"ChannelStrip", "GenericMixer", "MasterLimiter", "IFX"}

_PROJECT_SUFFIXES = (".gdproj2", ".gdproj")


def _installed_gadgets() -> list[str]:
    """Noms des gadgets installés en VST (source de vérité pour add_fx)."""
    if not _VST_KORG_DIR.is_dir():
        return []
    return sorted(p.stem for p in _VST_KORG_DIR.iterdir() if p.suffix == ".vst")


def _gadget_categories() -> dict[str, str]:
    """{gadget: catégorie} lue dans le `CFBundleName` de chaque VST.

    KORG y écrit le caractère de l'instrument : « London (Drum) »,
    « Madrid (Bass) », « Marseille (Keys) »… C'est la seule source factuelle
    du timbre : on ne devine JAMAIS quel gadget joue quel rôle.
    """
    cats: dict[str, str] = {}
    if not _VST_KORG_DIR.is_dir():
        return cats
    for vst in sorted(_VST_KORG_DIR.iterdir()):
        if vst.suffix != ".vst":
            continue
        try:
            info = plistlib.loads((vst / "Contents" / "Info.plist").read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        name = str(info.get("CFBundleName", ""))
        cats[vst.stem] = name.split("(", 1)[1].rstrip(")").strip() if "(" in name else ""
    return cats


# Préférences de catégorie par rôle musical, du plus typé au plus passe-partout.
# Le résolveur prend la première catégorie effectivement installée : rien n'est
# codé en dur sur un gadget précis (l'utilisateur peut n'avoir qu'un sous-ensemble).
_ROLE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "drums": ("Drum", "E.Perc", "Dr. Octorex", "Slicer"),
    "bass": ("Bass", "Acid", "Wobble", "MS-20", "Mono/Poly"),
    "lead": ("Lead", "Analog", "Odyssey", "MS-20", "FM", "Digital"),
    "chords": ("Keys", "E.Piano", "Piano", "Organ", "Polysix", "Clav", "Pad"),
}


def _gadget_for_role(role: str, categories: dict[str, str]) -> str | None:
    """Gadget installé le plus typé pour un rôle, sinon n'importe lequel."""
    for wanted in _ROLE_CATEGORIES.get(role, ()):
        for gadget, cat in categories.items():
            if cat == wanted:
                return gadget
    return next(iter(categories), None)  # dernier recours : un instrument, au moins


def _project_roots() -> list[Path]:
    """Racines où chercher/lire des projets Gadget.

    `GADGET_PROJECT_ROOTS` (séparateur os.pathsep) sinon défauts : les racines
    audio du pathguard + iCloud Drive + Desktop (Gadget sauve volontiers sur le
    bureau, souvent synchronisé iCloud → `~/Library/Mobile Documents`).
    """
    raw = os.getenv("GADGET_PROJECT_ROOTS", "")
    if raw:
        candidates = [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]
    else:
        from klody_mcp._pathguard import AUDIO_ROOTS
        home = Path.home()
        candidates = [
            *AUDIO_ROOTS,
            home / "Desktop",
            home / "Library/Mobile Documents/com~apple~CloudDocs",
        ]
    roots: list[Path] = []
    for c in candidates:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r.is_dir() and r not in roots:
            roots.append(r)
    return roots


# --------------------------------------------------------------------------- #
# Parseur .gdproj2 (lecture seule, stdlib pure)                               #
# --------------------------------------------------------------------------- #


def _safe_project_path(path: str) -> Path:
    """Valide un chemin de projet : sous une racine autorisée ET suffixe Gadget.

    Le suffixe imposé borne la lecture aux projets Gadget — un arg LLM ne peut
    pas exfiltrer un fichier arbitraire même sous une racine (ASI02).
    """
    p = safe_path(path, roots=_project_roots())
    if p.suffix not in _PROJECT_SUFFIXES and p.suffix != ".gddat":
        raise PathGuardViolation(
            f"pas un projet Gadget ({'/'.join(_PROJECT_SUFFIXES)} attendu) : {path}"
        )
    return p


def _locate_gddat(p: Path) -> Path:
    """`.gdproj2` = package : le vrai contenu est `<Nom>.gddat` dedans."""
    if p.is_dir():
        gddat = p / f"{p.stem}.gddat"
        if not gddat.is_file():
            # Nom interne divergent (renommage Finder) : premier .gddat trouvé.
            found = sorted(p.glob("*.gddat"))
            if not found:
                raise FileNotFoundError(f"aucun .gddat dans le package : {p}")
            gddat = found[0]
        return gddat
    return p  # .gdproj v1 ou .gddat nu : fichier plat


def _parse_gddat(gddat: Path) -> dict:
    """Décode le NSKeyedArchiver + le ZIP embarqué. Lecture seule."""
    with gddat.open("rb") as f:
        archive = plistlib.load(f)
    objects = archive.get("$objects")
    if not isinstance(objects, list):
        raise ValueError("pas un NSKeyedArchiver (clé $objects absente)")

    # Tonalité : paires consécutives 'Key'/'Scale' → valeurs juste après.
    key = scale = None
    for i, obj in enumerate(objects):
        if obj == "Key" and key is None:
            key = _string_after(objects, i)
        elif obj == "Scale" and scale is None:
            scale = _string_after(objects, i)

    # Projet : premier blob ZIP (magic PK\x03\x04).
    blob = next(
        (o for o in objects if isinstance(o, bytes) and o[:4] == b"PK\x03\x04"),
        None,
    )
    if blob is None:
        raise ValueError("blob project_data (ZIP) introuvable dans le .gddat")

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = set(z.namelist())
        tempo, version = _read_seq_header(z, names)
        tracks = _read_tracks(z, names)
        buses = _read_chain_dir(z, names, "root/buses")
        master = _read_chain_dir(z, names, "root/master")

    return {
        "key": key,
        "scale": scale,
        "tempo": tempo,
        "format_version": version,
        "tracks": tracks,
        "buses": buses,
        "master_chain": master,
    }


def _string_after(objects: list, i: int) -> str | None:
    """Valeur string suivant un libellé dans le tableau NSKeyedArchiver.

    Layout observé (Gadget 2.9.5) : ['Key', 'Scale', 'C', 'Dorian'] — les
    valeurs suivent les DEUX libellés. On prend la 1re string à i+2 sinon i+1.
    """
    for j in (i + 2, i + 1):
        if j < len(objects) and isinstance(objects[j], str) and objects[j] not in ("Key", "Scale"):
            return objects[j]
    return None


def _read_seq_header(z: zipfile.ZipFile, names: set[str]) -> tuple[float | None, int | None]:
    """Tempo + version depuis root/seqs/seq.dat.

    Offsets vérifiés sur 8 projets (versions 1 et 2) : @0 int32 LE = version,
    @8 float32 LE = tempo BPM (96–197 observés ; @44 = 120.0 constant, à ignorer).
    """
    if "root/seqs/seq.dat" not in names:
        return None, None
    seq = z.read("root/seqs/seq.dat")
    if len(seq) < 12:
        return None, None
    version = struct.unpack_from("<i", seq, 0)[0]
    tempo = struct.unpack_from("<f", seq, 8)[0]
    if not (1.0 <= tempo <= 960.0):  # garde anti-décalage de format futur
        return None, version
    return round(tempo, 2), version


def _read_tracks(z: zipfile.ZipFile, names: set[str]) -> list[dict]:
    """Pistes : root/tracks/<n>/plugins/<m>/plugin_info (slot 0 = l'instrument)."""
    midi_channels: dict[str, int | None] = {}
    if "root/midiOutInfo.plist" in names:
        try:
            info = plistlib.loads(z.read("root/midiOutInfo.plist"))
            midi_channels = {
                k: v.get("MidiChannel") for k, v in info.items() if isinstance(v, dict)
            }
        except (ValueError, plistlib.InvalidFileException):
            logger.warning("midiOutInfo.plist illisible — canaux MIDI ignorés")

    # `names` est un set (ordre non déterministe) → on collecte (slot, nom)
    # puis on trie : la chaîne doit refléter l'ordre réel des slots plugins/<m>.
    tracks: dict[int, dict] = {}
    slots: dict[int, list[tuple[int, str]]] = {}
    for name in names:
        parts = name.split("/")
        # root/tracks/<n>/plugins/<m>/plugin_info
        if len(parts) == 6 and parts[1] == "tracks" and parts[5] == "plugin_info":
            try:
                t_idx, p_idx = int(parts[2]), int(parts[4])
            except ValueError:
                continue
            plug = _plugin_name(z, name)
            if plug is None:
                continue
            tracks.setdefault(
                t_idx,
                {"index": t_idx, "gadget": None, "fx_chain": [],
                 "midi_channel": midi_channels.get(str(t_idx))},
            )
            slots.setdefault(t_idx, []).append((p_idx, plug))
    for t_idx, plugs in slots.items():
        for p_idx, plug in sorted(plugs):
            if p_idx == 0 and plug not in _INTERNAL_PLUGINS:
                tracks[t_idx]["gadget"] = plug
            else:
                tracks[t_idx]["fx_chain"].append(plug)
    return [tracks[k] for k in sorted(tracks)]


def _read_chain_dir(z: zipfile.ZipFile, names: set[str], prefix: str) -> list[dict]:
    """Chaînes de plugins de root/buses ou root/master (plat, informatif)."""
    chains: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        if name.startswith(prefix) and name.endswith("plugin_info"):
            plug = _plugin_name(z, name)
            if plug is None:
                continue
            # root/master/plugins/<m>/… (clé '_') ou root/buses/<n>/plugins/<m>/…
            parts = name.split("/")
            key = parts[2] if parts[1] == "buses" else "_"
            try:
                slot = int(parts[-2])
            except ValueError:
                slot = 0
            chains.setdefault(key, []).append((slot, plug))
    return [
        {"id": k, "chain": [plug for _, plug in sorted(v)]}
        for k, v in sorted(chains.items())
    ]


def _plugin_name(z: zipfile.ZipFile, member: str) -> str | None:
    """`plugin_info` est un plist XML `{"Name": "<gadget>"}` (JSON toléré)."""
    raw = z.read(member)
    for loader in (plistlib.loads, json.loads):
        try:
            data = loader(raw)
        except Exception:
            continue
        if isinstance(data, dict):
            return data.get("Name")
    logger.warning("plugin_info illisible : %s", member)
    return None


# --------------------------------------------------------------------------- #
# Outils MCP — inspection (aucun prérequis REAPER)                            #
# --------------------------------------------------------------------------- #


@mcp.tool()
async def list_gadgets() -> dict:
    """Liste les instruments KORG Gadget installés sur ce Mac (utilisables dans REAPER).

    Scanne /Library/Audio/Plug-Ins/VST/KORG (source de vérité : ce que REAPER
    peut charger) et lit la catégorie déclarée par chaque plugin (Drum, Bass,
    Lead, Keys…). Lecture pure, aucun argument.

    Returns:
        {"gadgets": [{"name": "London", "category": "Drum"}, ...], "count": N,
         "by_role": {"drums": "London", "bass": "Madrid", ...},
         "gadget_app_installed": bool}
        — `by_role` = ce que choisirait `forge_song_with_gadgets` par défaut.
    """
    categories = _gadget_categories()
    return {
        "gadgets": [{"name": g, "category": categories.get(g, "")}
                    for g in _installed_gadgets()],
        "count": len(categories),
        "by_role": {role: _gadget_for_role(role, categories) for role in _ROLE_CATEGORIES},
        "gadget_app_installed": _GADGET_APP.is_dir(),
    }


@mcp.tool()
async def list_gadget_projects(directory: str | None = None) -> dict:
    """Cherche les projets KORG Gadget (.gdproj2/.gdproj) sur le disque.

    Args:
        directory: dossier à scanner (défaut : racines autorisées — Desktop,
            iCloud Drive, Music, Documents…). Profondeur max 4, dossiers cachés
            ignorés.

    Returns:
        {"projects": [{"name", "path", "modified"}...], "count": N}
    """
    roots = [safe_path(directory, roots=_project_roots())] if directory else _project_roots()
    # I/O disque (walk potentiellement large sur iCloud) → thread, jamais
    # l'event loop (leçon fix/conventions-blocking-scan).
    projects = await asyncio.to_thread(_scan_projects, roots)
    return {"projects": projects, "count": len(projects)}


def _scan_projects(roots: list[Path]) -> list[dict]:
    found: dict[str, dict] = {}
    for root in roots:
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            if len(d.parts) - base_depth >= 4:
                dirnames[:] = []
                continue
            # Les .gdproj2 sont des PACKAGES (dossiers) → ils vivent dans
            # dirnames ; on les collecte puis on ne descend pas dedans.
            packages = [n for n in dirnames if n.endswith(".gdproj2")]
            dirnames[:] = [
                n for n in dirnames
                if not n.startswith(".") and not n.endswith(".gdproj2")
            ]
            for entry in list(filenames) + packages:
                p = d / entry
                if p.suffix in _PROJECT_SUFFIXES and str(p) not in found:
                    try:
                        mtime = p.stat().st_mtime
                    except OSError:
                        continue
                    found[str(p)] = {"name": p.stem, "path": str(p), "modified": int(mtime)}
    return sorted(found.values(), key=lambda x: -x["modified"])


@mcp.tool()
async def read_gadget_project(path: str) -> dict:
    """Lit un projet KORG Gadget (.gdproj2) : tonalité, tempo, pistes, instruments.

    Lecture seule stricte — ne modifie jamais le projet. Format décodé :
    NSKeyedArchiver + ZIP embarqué (voir docstring module).

    Args:
        path: chemin du .gdproj2 (package) ou .gdproj.

    Returns:
        {"name", "key", "scale", "tempo", "format_version",
         "tracks": [{"index", "gadget", "fx_chain", "midi_channel"}...],
         "buses", "master_chain", "installed_locally": [...]}
        — `installed_locally` = sous-ensemble des gadgets du projet dispo en VST.
    """
    try:
        p = _safe_project_path(path)
        # Lecture + unzip dans un thread : un .gddat iCloud non matérialisé
        # peut bloquer le temps du téléchargement.
        data = await asyncio.to_thread(lambda: _parse_gddat(_locate_gddat(p)))
    except (PathGuardViolation, FileNotFoundError, ValueError, OSError,
            plistlib.InvalidFileException, zipfile.BadZipFile) as exc:
        return {"error": f"lecture projet Gadget impossible : {exc}"}
    installed = set(_installed_gadgets())
    used = {t["gadget"] for t in data["tracks"] if t["gadget"]}
    return {
        "name": p.stem,
        **data,
        "installed_locally": sorted(used & installed),
        "missing_locally": sorted(used - installed),
    }


# --------------------------------------------------------------------------- #
# Outils MCP — pilotage indirect via REAPER (pont :9000)                      #
# --------------------------------------------------------------------------- #


async def _add_gadget_track(gadget: str, track_name: str | None) -> dict:
    """Piste REAPER + instrument gadget chargé dessus. Cœur partagé."""
    added = await _bridge_call("add_track", {"name": track_name or gadget})
    if "error" in added:
        return added
    idx = added.get("inserted_index")
    # « <Nom> (KORG) » : nom exact du VSTi vu par REAPER — le suffixe évite
    # qu'une sous-chaîne ambiguë (« Berlin »…) matche un autre plugin.
    fx = await _bridge_call("add_fx", {"index": idx, "name": f"{gadget} (KORG)"})
    if "error" in fx:
        return {
            "track_index": idx, "guid": added.get("guid"), "gadget": gadget,
            "status": "missing", "error_fx": fx["error"],
        }
    return {
        "track_index": idx, "guid": fx.get("guid"), "gadget": gadget,
        "fx_index": fx.get("fx_index"), "fx_name": fx.get("fx_name"),
        "status": "ok",
    }


@mcp.tool()
async def create_gadget_track(gadget: str, track_name: str | None = None) -> dict:
    """Crée dans REAPER une piste jouant un instrument KORG Gadget.

    Pilotage INDIRECT : l'app Gadget n'est pas scriptable, mais ses instruments
    sont installés en VST — on les charge dans REAPER (pont :9000). La piste est
    ensuite pilotable par les outils reaper (insert_midi_notes, render…).

    Args:
        gadget: nom de l'instrument (voir list_gadgets), ex. "Chicago", "London".
        track_name: nom de piste optionnel (défaut : nom du gadget).

    Returns:
        {"track_index", "guid", "gadget", "fx_index", "fx_name", "status": "ok"}
        ou {"status": "missing", ...} si le VST n'est pas résolu par REAPER.
    """
    gadget = gadget.strip()
    catalogue = _installed_gadgets()
    match = next((g for g in catalogue if g.lower() == gadget.lower()), None)
    if match is None:
        return {
            "error": f"gadget inconnu : {gadget!r}",
            "installed": catalogue,
        }
    return await _add_gadget_track(match, track_name)


@mcp.tool()
async def import_gadget_project_to_reaper(path: str, max_tracks: int = 16) -> dict:
    """Recrée un projet KORG Gadget dans REAPER : tempo + une piste par gadget.

    Lit le .gdproj2 (lecture seule) puis, via le pont REAPER : règle le tempo,
    pose un marqueur tonalité à 0 s, crée chaque piste avec son instrument VST
    KORG chargé. Les gadgets non installés en VST sont rapportés `missing`
    (on continue — patron build_vocal_chain). Les presets/patterns ne sont PAS
    transférés (format propriétaire) : la composition se refait côté REAPER
    (insert_midi_notes), guidée par key/scale/tempo du projet.

    Args:
        path: chemin du .gdproj2.
        max_tracks: garde-fou nombre de pistes créées (défaut 16).

    Returns:
        {"project", "tempo_set", "key_marker", "tracks": [...], "summary"}
    """
    project = await read_gadget_project(path)
    if "error" in project:
        return project

    report: dict = {"project": project["name"], "tracks": []}

    if project.get("tempo"):
        tempo = await _bridge_call("set_tempo", {"bpm": project["tempo"]})
        report["tempo_set"] = tempo if "error" in tempo else tempo.get("bpm")

    if project.get("key") and project.get("scale"):
        label = f"Key: {project['key']} {project['scale']}"
        marker = await _bridge_call("add_marker", {"position": 0.0, "name": label})
        report["key_marker"] = marker.get("error", label)

    todo = [t for t in project["tracks"] if t["gadget"]][:max_tracks]
    ok = missing = 0
    for track in todo:
        result = await _add_gadget_track(track["gadget"], None)
        report["tracks"].append(result)
        if result.get("status") == "ok":
            ok += 1
        else:
            missing += 1
            if "error" in result:  # pont REAPER down → inutile de continuer
                break
    skipped = len([t for t in project["tracks"] if t["gadget"]]) - len(todo)
    report["summary"] = (
        f"{ok} piste(s) créée(s), {missing} manquante(s)"
        + (f", {skipped} au-delà de max_tracks" if skipped > 0 else "")
        + f" — clé {project.get('key')} {project.get('scale')}, tempo {project.get('tempo')}"
    )
    return report


@mcp.tool()
async def gadget_status() -> dict:
    """État du pont Gadget : app, instruments VST, pont REAPER, Libretto, racines.

    Lecture pure. Diagnostic rapide avant un import ou une création de piste.

    Returns:
        {"gadget_app", "vst_count", "reaper_bridge", "libretto", "project_roots"}
    """
    ping = await _bridge_call("ping")
    return {
        "gadget_app": _GADGET_APP.is_dir(),
        "vst_count": len(_installed_gadgets()),
        "reaper_bridge": ping.get("error", "ok"),
        "libretto": libretto_forge.status(),
        "project_roots": [str(r) for r in _project_roots()],
    }


# --------------------------------------------------------------------------- #
# Outils MCP — chaîne Forge → Libretto (gate) → Gadget → REAPER               #
# --------------------------------------------------------------------------- #


@mcp.tool()
async def analyze_midi_structure(midi_path: str) -> dict:
    """Note la STRUCTURE d'un fichier MIDI avec Libretto (score SMS, 29 axes).

    Juge la construction — forme, harmonie, mélodie, rythme, texture, cohérence
    — pas le timbre. Utile pour trancher entre deux versions ou vérifier ce
    qu'on s'apprête à envoyer dans le DAW. Lecture pure.

    Args:
        midi_path: chemin d'un .mid/.midi.

    Returns:
        {"score", "confidence", "level", "interpretable", "groups", "sections"}
        — `interpretable` False = le score n'est pas lisible, ne pas s'en servir
        pour décider. Ou {"error": "..."} si Libretto est absent/le MIDI vide.
    """
    try:
        p = safe_path(midi_path)
        if p.suffix.lower() not in (".mid", ".midi"):
            raise PathGuardViolation(f"pas un fichier MIDI : {midi_path}")
        return await asyncio.to_thread(libretto_forge.analyze_midi, p)
    except (libretto_forge.LibrettoUnavailable, PathGuardViolation, FileNotFoundError,
            ValueError, OSError) as exc:
        return {"error": f"analyse Libretto impossible : {exc}"}


@mcp.tool()
async def forge_song_with_gadgets(
    n: int = 12,
    seed: int = 1,
    min_confidence: float = 0.55,
    min_score: float = 0.0,
    instruments: dict[str, str] | None = None,
    render_to: str | None = None,
) -> dict:
    """Compose avec Forge, fait juger par Libretto, monte le gagnant sur des gadgets KORG.

    Chaîne complète : Forge génère `n` ébauches → Libretto les note (SMS) et
    ne laisse passer que celle qui franchit le gate → chaque piste reçoit un
    instrument KORG Gadget selon son rôle (percussions au canal 10, puis basse
    = plus grave, lead = plus aiguë, reste = accords) → REAPER reçoit tempo,
    notes et marqueurs de section.

    **Le gate est le point de tout l'outil** : si aucun candidat n'atteint
    `min_confidence`/`min_score`, RIEN n'est envoyé à REAPER et le rapport dit
    pourquoi. On ne monte pas une structure qu'on ne sait pas juger.

    Args:
        n: nombre d'ébauches générées (12 par défaut ; plus = meilleur gagnant,
            plus lent).
        seed: graine — même graine + même n = même résultat (reproductible).
        min_confidence: fiabilité minimale du score pour concourir (0.55).
        min_score: score SMS minimal (0.0 = pas de plancher).
        instruments: forçage {role: gadget}, ex. {"bass": "Chicago"}. Rôles :
            drums, bass, lead, chords. Non fourni = choix par catégorie déclarée
            par les plugins (voir `list_gadgets.by_role`).
        render_to: chemin .wav — rend le résultat audio après montage (long).

    Returns:
        {"winner", "structure", "tracks", "markers", "tempo", "rendered", "summary"}
        ou {"error", "report"} si le gate rejette tout / Libretto est absent.
    """
    scratch = Path(tempfile.mkdtemp(prefix="klody_forge_"))
    try:
        report = await asyncio.to_thread(
            libretto_forge.run_forge, scratch, n, seed, min_confidence, min_score
        )
    except (libretto_forge.LibrettoUnavailable, RuntimeError, OSError, ValueError) as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        return {"error": f"Forge indisponible : {exc}"}

    winner_path = report.get("winner_path")
    if not winner_path:
        shutil.rmtree(scratch, ignore_errors=True)
        return {
            "error": "aucun candidat n'a passé le gate — rien envoyé à REAPER",
            "gates": report.get("gates"),
            "rejected_low_confidence": report.get("n_rejected_confidence"),
            "rejected_low_score": report.get("n_rejected_score"),
            "hint": "baisser min_confidence/min_score, ou augmenter n",
        }

    try:
        midi = await asyncio.to_thread(libretto_forge.midi_to_tracks, Path(winner_path))
        if "error" in midi:
            return {"error": midi["error"]}
        return await _push_to_reaper(midi, report, instruments, render_to)
    except (libretto_forge.LibrettoUnavailable, OSError, ValueError) as exc:
        return {"error": f"lecture du gagnant impossible : {exc}"}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def _push_to_reaper(midi: dict, report: dict, instruments: dict[str, str] | None,
                          render_to: str | None) -> dict:
    """Monte le MIDI jugé dans REAPER : gadget par rôle, notes, marqueurs."""
    categories = _gadget_categories()
    forced = {k.lower(): v for k, v in (instruments or {}).items()}
    installed_lower = {g.lower(): g for g in categories}

    await _bridge_call("set_tempo", {"bpm": midi["tempo"]})

    pushed: list[dict] = []
    for track in midi["tracks"]:
        role = track["role"]
        choice = forced.get(role)
        gadget = installed_lower.get(choice.lower()) if choice else _gadget_for_role(role, categories)
        if choice and gadget is None:
            pushed.append({"role": role, "status": "missing", "error": f"gadget inconnu : {choice!r}"})
            continue
        if gadget is None:
            pushed.append({"role": role, "status": "missing", "error": "aucun instrument KORG installé"})
            continue

        created = await _add_gadget_track(gadget, f"{role} · {gadget}")
        if "error" in created:  # pont down → inutile d'insister
            pushed.append({"role": role, **created})
            break
        # Le pont garde chaque ligne JSON sous 64 KiB → notes par lots.
        notes = track["notes"]
        inserted = 0
        for i in range(0, len(notes), libretto_forge.NOTE_CHUNK):
            chunk = notes[i:i + libretto_forge.NOTE_CHUNK]
            res = await _bridge_call(
                "insert_midi_notes",
                {"guid": created.get("guid"), "notes": chunk},
                timeout=_INSERT_TIMEOUT,
            )
            if "error" in res:
                created["error_notes"] = res["error"]
                break
            inserted += len(chunk)
        pushed.append({
            "role": role, "gadget": gadget, "category": categories.get(gadget, ""),
            "notes": inserted, "mean_pitch": track["mean_pitch"],
            "status": created.get("status", "ok"),
        })

    for marker in midi["markers"]:
        await _bridge_call("add_marker", marker)

    result = {
        "winner": report.get("winner"),
        "structure": {k: report["winner"].get(k) for k in ("form", "mode", "meter", "bpm")}
        if report.get("winner") else None,
        "tempo": midi["tempo"],
        "tracks": pushed,
        "markers": len(midi["markers"]),
        "candidates_scored": report.get("n_generated"),
    }
    if render_to:
        rendered = await _bridge_call(
            "render_project", {"out_path": render_to}, timeout=_RENDER_TIMEOUT
        )
        result["rendered"] = rendered.get("output_files", rendered)

    win = report.get("winner") or {}
    ok = sum(1 for t in pushed if t.get("status") == "ok")
    result["summary"] = (
        f"gagnant sur {report.get('n_generated')} ébauches : score SMS {win.get('score')} "
        f"(fiabilité {win.get('confidence')}, {win.get('level')}) — {win.get('form')} "
        f"{win.get('mode')} {win.get('meter')} à {win.get('bpm')} bpm ; "
        f"{ok}/{len(pushed)} piste(s) montée(s) sur gadgets, "
        f"{sum(t.get('notes', 0) for t in pushed)} notes"
    )
    return result


# --------------------------------------------------------------------------- #
# Entrée                                                                      #
# --------------------------------------------------------------------------- #


def main() -> None:
    transport = os.getenv("GADGET_MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("GADGET_MCP_PORT", "8093"))
    host = os.getenv("GADGET_MCP_HOST", "127.0.0.1")

    if transport == "http":
        logger.info("Gadget MCP HTTP : http://%s:%d", host, port)
        mcp.run(transport="http", host=host, port=port)
    else:
        logger.info("Gadget MCP stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
