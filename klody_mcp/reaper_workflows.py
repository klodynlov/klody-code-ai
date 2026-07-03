"""Workflows agentiques REAPER (P5) — macros métier composées SUR les primitives
P1-P3 déjà validées. Aucune logique REAPER ici : chaque workflow orchestre des
appels au pont via un callable asynchrone `call(cmd, args) -> dict`, ce qui les
rend testables hors REAPER (cf. tests/test_reaper_workflows.py injecte un faux
pont). Le serveur MCP (klody_mcp/reaper_server.py) passe son `_bridge_call`.

Profil cible : zouk / RnB / afro / trap soul / reggae / dancehall caribéen
(micro AT4047/SV + Apollo Twin X). Voir spec DAW agentique section 8 et la mémoire
klody_reaper_agentic_daw_roadmap.

Principes hérités de la spec :
  - observe-avant-modifie : snapshot avant les mutations.
  - jamais supposer un plugin installé : add_fx échoue proprement -> on COLLECTE le
    manque (added/missing) plutôt que planter tout le workflow.
  - réversibilité : chaque sous-étape est un bloc Undo côté pont ; un workflow
    rapporte `undo_steps` (nombre de Cmd-Z pour tout annuler).
  - idempotence : create_bus / create_send / add_fx / add_region sont idempotents
    -> rejouer un workflow ne duplique rien.
"""
from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from klody_mcp import reaper_plugins, reaper_samples
from klody_mcp._pathguard import PathGuardViolation, safe_path  # ASI02

# Callable du pont : (cmd, args) -> dict, asynchrone (le _bridge_call du serveur MCP).
Call = Callable[[str, dict], Awaitable[dict]]

# Chaîne vocale stock REAPER (toujours installée). On reste sur les Rea* pour ne
# jamais dépendre d'un plugin tiers ; si l'un manque, on dégrade (missing).
_VOCAL_CHAIN = ("ReaEQ", "ReaComp")

# Réglages de DÉPART conventionnels pour un ReaComp vocal, en unités NATIVES (raw) :
# Ratio = nombre (3:1), Threshold = dB. Ce ne sont PAS des valeurs analysées sur le
# signal (spec : ne jamais prétendre) — juste un point de départ sûr à affiner.
_REACOMP_VOCAL = (("Ratio", 3.0), ("Thresh", -18.0))

# Structure zouk par défaut (nom, nombre de mesures), 4/4. Modifiable via `sections`.
_DEFAULT_ZOUK = (
    ("Intro", 8), ("Couplet 1", 16), ("Refrain", 8), ("Couplet 2", 16),
    ("Refrain 2", 8), ("Pont", 8), ("Refrain 3", 8), ("Outro", 8),
)


def _is_err(r: Any) -> bool:
    return isinstance(r, dict) and bool(r.get("error"))


def _installed_chain(gate: bool) -> tuple[list[str], str]:
    """Chaîne vocale bâtie sur les plugins INSTALLÉS (préfère ceux de l'utilisateur),
    avec repli sur le stock Rea* par rôle. Renvoie (specs, nom_du_reverb_de_bus).
    reaper_plugins.resolve_plugin est fail-soft (None si rien) -> on retombe sur le stock."""
    def pick(role: str, fallback: str) -> str:
        r = reaper_plugins.resolve_plugin(role)
        return r["name"] if r else fallback
    specs = [pick("eq", "ReaEQ"), pick("comp", "ReaComp")]
    if gate:
        specs.append(pick("gate", "ReaGate"))
    return specs, pick("reverb", "ReaVerbate")


def _sanitize(name: str) -> str:
    """Nom de fichier sûr : alphanum + . _ - conservés, le reste -> '_'."""
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", (name or "").strip())
    return s.strip("_") or "track"


async def _find_track_by_name(call: Call, name: str) -> dict | None:
    """Première piste au nom EXACT, via list_tracks (lecture pure). None si absente."""
    r = await call("list_tracks", {})
    if _is_err(r):
        return None
    for t in r.get("tracks", []):
        if t.get("name") == name:
            return t
    return None


async def prepare_vocal_recording(
    call: Call, name: str = "Lead Vocal", input_channel: int = 0,
    monitor: bool = True, build_chain: bool = False,
) -> dict:
    """Prépare une session voix : crée/retrouve une piste `name`, l'arme (entrée mono
    input_channel + monitoring), pose optionnellement la chaîne vocale. N'ENREGISTRE
    PAS (aucun audio écrit ; lancer transport_record ensuite)."""
    snapshot = await call("get_project_snapshot", {"detail": "standard"})
    existing = await _find_track_by_name(call, name)
    undo_steps = 0
    if existing is not None:
        guid = existing.get("guid", "")
        created = False
    else:
        add = await call("add_track", {"name": name, "index": -1})
        if _is_err(add):
            return {"error": f"création piste voix échouée: {add.get('error')}"}
        guid = add.get("guid", "")
        created = True
        undo_steps += 1
    arm = await call("arm_track", {
        "guid": guid, "armed": True, "input": input_channel, "monitor": monitor,
    })
    if not _is_err(arm):
        undo_steps += 1
    chain = None
    if build_chain:
        chain = await build_vocal_chain(call, guid=guid)
        if isinstance(chain, dict):
            undo_steps += int(chain.get("undo_steps", 0) or 0)
    tempo = snapshot.get("project", {}).get("tempo") if isinstance(snapshot, dict) else None
    return {
        "track": {"name": name, "guid": guid, "created": created},
        "armed": arm,
        "chain": chain,
        "undo_steps": undo_steps,
        "tempo": tempo,
        "next": "transport_record pour enregistrer, transport_stop pour arrêter",
    }


async def build_vocal_chain(
    call: Call, index: int = -1, guid: str = "", gate: bool = False,
    reverb_send: bool = True, reverb_bus: str = "Reverb", reverb_db: float = -12.0,
    prefer_installed: bool = True, tune: bool = True,
    chain: list[str] | None = None, reverb_fx: str | None = None,
) -> dict:
    """Pose une chaîne vocale sur la piste ciblée (guid prioritaire) + un send optionnel
    vers un bus reverb.

    `prefer_installed` (défaut) : la chaîne est bâtie à partir des plugins RÉELLEMENT
    INSTALLÉS, en préférant ceux de l'utilisateur (KaribVoice/KlodVoice) au stock Rea*
    (registre = reaper_plugins, spec 7.6 « détecter le réel »). Sinon : chaîne stock.
    `chain` (liste de noms) force une chaîne explicite (court-circuite la résolution).
    Ne SUPPOSE aucun plugin : un effet absent est COLLECTÉ dans `missing`, le workflow
    continue. `tune` : règle quelques paramètres de DÉPART sûrs sur le ReaComp STOCK
    uniquement (unités natives via raw — Ratio/Threshold) ; jamais sur un plugin au
    layout inconnu (spec : ne jamais prétendre). À affiner ensuite (analyze_track)."""
    if not guid and index < 0:
        return {"error": "cible requise (guid ou index de piste)"}
    target = {"guid": guid, "index": index}
    # Décide la chaîne : explicite > installée (préférée) > stock.
    if chain is not None:
        specs = list(chain)
        rev_name = reverb_fx or "ReaVerbate"
        source = "explicit"
    elif prefer_installed:
        specs, rev_name = _installed_chain(gate)
        source = "installed"
    else:
        specs = list(_VOCAL_CHAIN) + (["ReaGate"] if gate else [])
        rev_name = reverb_fx or "ReaVerbate"
        source = "stock"
    added: list[dict] = []
    missing: list[dict] = []
    undo_steps = 0
    for fx in specs:
        r = await call("add_fx", {**target, "name": fx})
        if _is_err(r):
            missing.append({"fx": fx, "error": r.get("error")})
            continue
        added.append({"fx": r.get("fx_name"), "fx_index": r.get("fx_index"),
                      "created": r.get("created")})
        if r.get("created"):
            undo_steps += 1
    # Presets : seulement sur un ReaComp STOCK (params/units connus). raw=True ->
    # unités natives (Ratio = nombre, Threshold = dB), pas de devinette de normalisé.
    tuned: list[dict] = []
    if tune:
        for a in added:
            nm = a.get("fx") or ""
            if "reacomp" in nm.lower():
                for pname, val in _REACOMP_VOCAL:
                    tr = await call("set_fx_param", {
                        **target, "fx": nm, "param": pname, "value": val, "raw": True,
                    })
                    if not _is_err(tr):
                        tuned.append({"fx": nm, "param": pname, "value": val})
                        undo_steps += 1
    reverb: dict | None = None
    if reverb_send:
        bus = await call("create_bus", {"name": reverb_bus})
        if _is_err(bus):
            reverb = {"error": bus.get("error")}
        else:
            if bus.get("created"):
                undo_steps += 1
            rev_fx = await call("add_fx", {"guid": bus.get("guid", ""), "name": rev_name})
            if not _is_err(rev_fx) and rev_fx.get("created"):
                undo_steps += 1
            send = await call("create_send", {
                **target, "dest_guid": bus.get("guid", ""), "vol_db": reverb_db,
            })
            if not _is_err(send) and send.get("created"):
                undo_steps += 1
            reverb = {
                "bus": {"name": reverb_bus, "guid": bus.get("guid"), "created": bus.get("created")},
                "fx": rev_fx.get("fx_name") if not _is_err(rev_fx) else None,
                # fx_error surface un éventuel plugin reverb absent (le send reste valide,
                # routé vers un bus sans processeur -> à compléter à la main).
                "fx_error": rev_fx.get("error") if _is_err(rev_fx) else None,
                "send": send,
            }
    return {"added": added, "missing": missing, "reverb": reverb, "tuned": tuned,
            "chain_source": source, "undo_steps": undo_steps}


def _normalize_sections(sections: Any) -> list[tuple[str, float]]:
    """Accepte [{'name','bars'}], [['name', bars]] ou [('name', bars)] -> [(name, bars)]."""
    out: list[tuple[str, float]] = []
    for s in sections:
        if isinstance(s, dict):
            name = str(s.get("name", "Section"))
            bars = float(s.get("bars", 8) or 0)
        elif isinstance(s, (list, tuple)) and len(s) >= 2:
            name = str(s[0])
            bars = float(s[1] or 0)
        else:
            continue
        if bars > 0:
            out.append((name, bars))
    return out


async def create_zouk_arrangement(
    call: Call, bpm: float | None = None, sections: Any = None,
    beats_per_bar: int = 4, start: float = 0.0,
) -> dict:
    """Pose une structure de morceau en RÉGIONS (intro/couplet/refrain/pont/outro).
    Règle le tempo si `bpm` fourni, sinon utilise celui du projet. Convertit les
    mesures en secondes (4/4 par défaut). Idempotent (add_region dédup par position) :
    rejouer au MÊME tempo ne duplique rien."""
    if bpm is not None:
        st = await call("set_tempo", {"bpm": bpm})
        if _is_err(st):
            return {"error": f"réglage tempo échoué: {st.get('error')}"}
    snap = await call("get_project_snapshot", {"detail": "summary"})
    if bpm is not None:
        eff_bpm = float(bpm)
    else:
        proj = snap.get("project", {}) if isinstance(snap, dict) else {}
        eff_bpm = float(proj.get("tempo", 120.0) or 120.0)
    if eff_bpm <= 0:
        return {"error": "tempo projet invalide"}
    secs = _normalize_sections(sections) if sections else list(_DEFAULT_ZOUK)
    if not secs:
        return {"error": "aucune section valide"}
    seconds_per_bar = (60.0 / eff_bpm) * beats_per_bar
    t = float(start)
    regions: list[dict] = []
    undo_steps = 0
    for name, bars in secs:
        end = t + bars * seconds_per_bar
        r = await call("add_region", {"start": round(t, 4), "end": round(end, 4), "name": name})
        regions.append({
            "name": name, "bars": bars, "start": round(t, 4), "end": round(end, 4),
            "created": r.get("created") if isinstance(r, dict) else None,
            "error": r.get("error") if isinstance(r, dict) else None,
        })
        if isinstance(r, dict) and r.get("created"):
            undo_steps += 1
        t = end
    return {
        "bpm": eff_bpm, "beats_per_bar": beats_per_bar,
        "seconds_per_bar": round(seconds_per_bar, 4),
        "regions": regions, "total_seconds": round(t - float(start), 4),
        "undo_steps": undo_steps,
    }


async def prepare_mix(call: Call, reverb: bool = True, delay: bool = True) -> dict:
    """Prépare la structure de mix : bus d'effets stock (Reverb -> ReaVerbate, Delay
    -> ReaDelay), idempotents. NE route PAS les pistes automatiquement (l'agent
    enchaîne create_send selon le besoin). Plugin absent -> collecté, le bus reste
    créé (jamais de suppositon sur les plugins installés)."""
    plan = (
        ([("Reverb", "ReaVerbate")] if reverb else [])
        + ([("Delay", "ReaDelay")] if delay else [])
    )
    buses: list[dict] = []
    undo_steps = 0
    for bus_name, fx_name in plan:
        b = await call("create_bus", {"name": bus_name})
        if _is_err(b):
            buses.append({"bus": bus_name, "error": b.get("error")})
            continue
        if b.get("created"):
            undo_steps += 1
        fx = await call("add_fx", {"guid": b.get("guid", ""), "name": fx_name})
        if not _is_err(fx) and fx.get("created"):
            undo_steps += 1
        buses.append({
            "bus": bus_name, "guid": b.get("guid"), "created": b.get("created"),
            "fx": fx.get("fx_name") if not _is_err(fx) else None,
            "fx_error": fx.get("error") if _is_err(fx) else None,
        })
    return {
        "buses": buses, "undo_steps": undo_steps,
        "hint": "router une piste vers un bus : create_send(guid_source, dest_guid=bus.guid)",
    }


async def render_all_stems(
    call: Call, out_dir: str, include_empty: bool = False, prefix: str = "",
) -> dict:
    """Rend CHAQUE piste en isolation (stems) dans out_dir. Réutilise
    render_track_isolated (P3) : restaure exactement solo/mute après chaque rendu,
    n'altère donc pas l'état du projet (net-zéro, aucun Undo). Saute par défaut les
    pistes vides (0 item — ex. bus). out_dir est créé au besoin."""
    if not (out_dir or "").strip():
        return {"error": "out_dir requis (dossier de sortie des stems)"}
    try:
        # ASI02 : confine le dossier (bloque out_dir=/etc). for_write : le dossier
        # peut ne pas exister encore, son parent doit être sous une racine.
        out_dir = str(safe_path(out_dir, for_write=True))
    except PathGuardViolation as exc:
        return {"error": str(exc)}
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        return {"error": f"dossier de sortie inutilisable: {exc}"}
    snap = await call("get_project_snapshot", {"detail": "full"})
    if _is_err(snap):
        return {"error": f"snapshot échoué: {snap.get('error')}"}
    tracks = snap.get("tracks", []) if isinstance(snap, dict) else []
    stems: list[dict] = []
    rendered = 0
    for pos, t in enumerate(tracks):
        # Le pont fournit toujours 'index' ; repli sur la position pour ne JAMAIS
        # planter le workflow entier sur un snapshot malformé (int(None) -> TypeError).
        idx = t.get("index")
        if idx is None:
            idx = pos
        nm = t.get("name") or "track"
        if not include_empty and int(t.get("item_count", 0) or 0) == 0:
            stems.append({"index": idx, "name": nm, "skipped": "piste vide (0 item)"})
            continue
        fname = f"{prefix}{int(idx):02d}_{_sanitize(nm)}.wav"
        out_path = os.path.join(out_dir, fname)
        r = await call("render_track_isolated", {"guid": t.get("guid", ""), "out_path": out_path})
        files = (r.get("output_files") or []) if isinstance(r, dict) else []
        ok = bool(isinstance(r, dict) and r.get("rendered") and files)
        if ok:
            rendered += 1
        stems.append({
            "index": idx, "name": nm, "rendered": ok, "output_files": files,
            "error": r.get("error") if isinstance(r, dict) else None,
        })
    return {"out_dir": out_dir, "track_count": len(tracks), "rendered": rendered, "stems": stems}


async def place_sample(
    call: Call, query: str, index: int = -1, guid: str = "",
    position: float = 0.0, root: str | None = None,
) -> dict:
    """Cherche un sample dans la bibliothèque LOCALE (search->rank), importe le MEILLEUR
    sur la piste ciblée à `position` (sec), et renvoie la PROVENANCE (chemin source).
    Spec 8.8 (search->rank->import->place->provenance). SampleBrain absent du repo ->
    bibliothèque filesystem (racines via env KLODY_SAMPLES_DIR ou `root`)."""
    if not (query or "").strip():
        return {"error": "query requis (mot-clé de recherche du sample)"}
    if not guid and index < 0:
        return {"error": "cible requise (guid ou index de piste)"}
    hits = reaper_samples.search_samples(query, root=root, limit=8)
    if not hits:
        return {"error": f"aucun sample trouvé pour {query!r}", "query": query,
                "candidates": [], "roots": [str(p) for p in reaper_samples._roots(root)]}
    best = hits[0]
    r = await call("insert_media", {
        "index": index, "guid": guid, "path": best["path"], "position": position,
    })
    if _is_err(r):
        return {"error": r.get("error"), "chosen": best}
    return {
        "chosen": {"name": best["name"], "path": best["path"], "score": best["score"]},
        "candidates": [{"name": h["name"], "score": h["score"]} for h in hits[:5]],
        "placed": {
            "track_index": r.get("track_index"), "guid": r.get("guid"),
            "position": r.get("position"), "length": r.get("length"),
            "inserted": r.get("inserted"),
        },
        "provenance": best["path"],  # source exacte (spec 8.8)
        "undo_steps": 1 if r.get("inserted") else 0,
    }
