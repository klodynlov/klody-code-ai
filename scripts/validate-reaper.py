#!/usr/bin/env python3
"""validate-reaper.py — valide la chaîne REAPER (serveur MCP → pont → DAW) en vrai.

Remplace le one-liner ad-hoc du README par un harnais REPETABLE et SUR : il
exerce chaque commande du pont dans un ordre logique, vérifie le résultat (lit
ce qu'il vient d'écrire), et NETTOIE derrière lui (ne supprime QUE la piste qu'il
a créée, ne sauvegarde JAMAIS le projet .rpp).

Cible la chaîne critique du pipeline chanson : add_track → insert_midi_note →
list_midi_notes (preuve de la génération mélodie MIDI) → contrôles de mix →
render_project (preuve du bounce audio).

Prérequis : REAPER lancé + pont chargé/actif (Actions > Load ReaScript), de
préférence sur un PROJET VIDE. Voir reaper_bridge/README.md.

Usage :
    .venv/bin/python scripts/validate-reaper.py            # tout sauf transport/record
    .venv/bin/python scripts/validate-reaper.py --render   # + bounce /tmp/*.wav
    .venv/bin/python scripts/validate-reaper.py --transport # + play/stop (pas record)

Code de sortie 0 = tout vert, 1 = au moins un rouge ou pont injoignable.
"""
from __future__ import annotations

import argparse
import os
import sys

# Lancé comme `python scripts/validate-reaper.py`, sys.path[0] = scripts/ et non
# la racine projet → klody_mcp introuvable. On insère la racine explicitement.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Réutilise le client du serveur MCP : valide aussi le framing socket réel.
from klody_mcp.reaper_server import _bridge_call_sync as call

TEST_TRACK = "KLODY_VALIDATE_melody"
# Petite gamme de Do majeur (Do Ré Mi Fa Sol) — preuve de génération mélodie.
SCALE = [60, 62, 64, 65, 67]
RENDER_OUT = "/tmp/klody_reaper_validate.wav"

_results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    _results.append((label, ok, detail))
    mark = "✅" if ok else "❌"
    print(f"  {mark} {label}" + (f" — {detail}" if detail else ""))
    return ok


def err(resp: dict) -> str | None:
    """Renvoie le message d'erreur du pont s'il y en a un, sinon None."""
    return resp.get("error") if isinstance(resp, dict) else f"réponse non-dict: {resp!r}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true", help="teste render_project (écrit /tmp/*.wav)")
    ap.add_argument("--transport", action="store_true", help="teste play/stop (PAS record)")
    args = ap.parse_args()

    print("━━━ Validation chaîne REAPER ━━━")

    # 0. ping — vivacité du pont
    r = call("ping")
    if err(r):
        print(f"  ❌ ping — {err(r)}")
        print("\nPont injoignable. Lance REAPER + charge/exécute "
              "reaper_bridge/klody_reaper_bridge.py (Actions > Load ReaScript).")
        return 1
    check("ping", bool(r.get("pong")), f"REAPER {r.get('reaper')} / protocole v{r.get('protocol')}")

    # 1. Lectures pures
    r = call("get_track_count")
    baseline = r.get("track_count")
    check("get_track_count", isinstance(baseline, int), f"baseline={baseline} piste(s)")
    if not isinstance(baseline, int):
        print("\nAbandon : get_track_count n'a pas renvoyé d'entier.")
        return 1
    if baseline > 0:
        print(f"  ⚠️  projet NON vide ({baseline} pistes) — on n'ajoute/supprime QUE notre piste de test.")

    r = call("list_tracks")
    check("list_tracks", isinstance(r.get("tracks"), list), f"count={r.get('count')}")
    r = call("get_play_position")
    check("get_play_position", "play_position" in r, f"playing={r.get('playing')}")

    # 2. Création + nommage de la piste de test
    r = call("add_track", {"name": TEST_TRACK, "index": -1})
    idx = r.get("inserted_index")
    ok_add = (r.get("track_count") == baseline + 1) and isinstance(idx, int)
    check("add_track", ok_add, f"index={idx}, count={r.get('track_count')}")
    if not ok_add:
        print("\nAbandon : add_track a échoué — pas de cleanup à faire.")
        return 1

    # 3. Contrôles de mix — on écrit puis on relit pour confirmer l'effet réel.
    rv = call("set_track_volume", {"index": idx, "db": -6.0})
    # Régression _track_at : une op ciblée par index doit ré-échoer l'INDEX (pas le
    # nombre de pistes). Sur projet à 1 piste : index attendu 0, le bug renvoyait 1.
    check("index ré-échoé correct (pas le count)", rv.get("index") == idx,
          f"set_track_volume a renvoyé index={rv.get('index')} (attendu {idx})")
    call("set_track_pan", {"index": idx, "pan": -0.5})
    call("set_track_mute", {"index": idx, "mute": True})
    call("set_track_solo", {"index": idx, "solo": True})
    r = call("list_tracks")
    tr = next((t for t in r.get("tracks", []) if t.get("index") == idx), {})
    check("set_track_volume", abs(tr.get("volume_db", 0) - (-6.0)) < 0.5, f"lu volume_db={tr.get('volume_db')}")
    check("set_track_pan", abs(tr.get("pan", 0) - (-0.5)) < 0.05, f"lu pan={tr.get('pan')}")
    check("set_track_mute", tr.get("mute") is True, f"lu mute={tr.get('mute')}")
    check("set_track_solo", tr.get("solo") is True, f"lu solo={tr.get('solo')}")
    # On démute/désolote pour ne pas fausser un éventuel render.
    call("set_track_mute", {"index": idx, "mute": False})
    call("set_track_solo", {"index": idx, "solo": False})
    call("rename_track", {"index": idx, "name": TEST_TRACK + "_2"})

    # 4. CŒUR PIPELINE : génération mélodie MIDI puis relecture.
    t = 0.0
    for pitch in SCALE:
        call("insert_midi_note", {"track_index": idx, "pitch": pitch,
                                  "start": t, "length": 0.4, "velocity": 96})
        t += 0.5
    # insert_midi_note crée un item PAR appel → on lit l'item 0 (1re note).
    r = call("list_midi_notes", {"track_index": idx, "item_index": 0})
    note0 = (r.get("notes") or [{}])[0]
    check("insert_midi_note + list_midi_notes",
          r.get("note_count", 0) >= 1 and note0.get("pitch") == SCALE[0],
          f"item0 note_count={r.get('note_count')}, pitch={note0.get('pitch')} (attendu {SCALE[0]})")

    # 4bis. P1 — snapshot (observe), GUID (identité stable), undo (réversibilité),
    # modes (garde-fou). Voir spec DAW agentique sections 4.3 / 4.4 / 5 / 6.
    snap = call("get_project_snapshot", {"detail": "standard"})
    proj = snap.get("project", {}) if isinstance(snap, dict) else {}
    check("get_project_snapshot", isinstance(proj, dict) and "tempo" in proj,
          f"tempo={proj.get('tempo')}, time_sig={proj.get('time_signature')}, "
          f"tracks={len(snap.get('tracks', []))}")
    snap_tr = next((t for t in snap.get("tracks", []) if t.get("index") == idx), {})
    guid = snap_tr.get("guid")
    check("snapshot expose le GUID", isinstance(guid, str) and guid.startswith("{"),
          f"guid={guid}")

    if isinstance(guid, str) and guid.startswith("{"):
        # Ciblage par GUID (pas par index) : on agit puis on relit par GUID.
        call("set_track_volume", {"guid": guid, "db": -9.0})
        r = call("get_project_snapshot", {"detail": "standard"})
        g_tr = next((t for t in r.get("tracks", []) if t.get("guid") == guid), {})
        check("ciblage par GUID (set_track_volume)",
              abs(g_tr.get("volume_db", 0) + 9.0) < 0.5,
              f"lu volume_db={g_tr.get('volume_db')} (attendu -9.0)")

        # Réversibilité : on change le volume puis undo -> retour à -9.0 (le bloc
        # Undo encadre exactement la dernière mutation).
        call("set_track_volume", {"guid": guid, "db": 0.0})
        u = call("undo")
        label = u.get("label") or ""
        check("undo_last (bloc Undo agent)",
              u.get("undone") is True and label.startswith("klody:"),
              f"undone={u.get('undone')}, label={label!r}")
        r = call("get_project_snapshot", {"detail": "standard"})
        g_tr = next((t for t in r.get("tracks", []) if t.get("guid") == guid), {})
        check("undo a bien annulé la dernière op",
              abs(g_tr.get("volume_db", 0) + 9.0) < 0.5,
              f"volume revenu à {g_tr.get('volume_db')} (attendu -9.0)")

    # Modes : read_only doit REFUSER une mutation ; on restaure autonomous AVANT le
    # cleanup (sinon delete_track serait bloqué).
    call("set_mode", {"mode": "read_only"})
    r = call("set_track_volume", {"index": idx, "db": -2.0})
    check("mode read_only bloque la mutation",
          bool(err(r)) and "read_only" in (err(r) or ""),
          f"erreur attendue: {err(r)}")
    mm = call("get_mode")
    check("get_mode", mm.get("mode") == "read_only", f"mode={mm.get('mode')}")
    call("set_mode", {"mode": "autonomous"})  # restaure pour autoriser le cleanup

    # 4ter. P2 — FX / routing / markers (spec 7.3 / 7.6 / 7.8). Effets stock REAPER
    # (ReaEQ toujours installé) ; bus + send + region + marqueur IDEMPOTENTS.
    r = call("add_fx", {"index": idx, "name": "ReaEQ"})
    fxi = r.get("fx_index")
    check("add_fx (ReaEQ)",
          isinstance(fxi, int) and fxi >= 0 and "EQ" in (r.get("fx_name") or ""),
          f"fx_index={fxi}, name={r.get('fx_name')}")
    r2 = call("add_fx", {"index": idx, "name": "ReaEQ"})  # 2e add = pas de doublon
    check("add_fx idempotent", r2.get("created") is False and r2.get("fx_index") == fxi,
          f"created={r2.get('created')}, fx_index={r2.get('fx_index')}")
    r = call("get_fx_params", {"index": idx, "fx": "ReaEQ"})
    pc = r.get("param_count", 0)
    check("get_fx_params", pc > 0 and isinstance(r.get("params"), list), f"param_count={pc}")
    if pc > 0:
        call("set_fx_param", {"index": idx, "fx": "ReaEQ", "param": 0, "value": 0.25})
        r = call("get_fx_params", {"index": idx, "fx": "ReaEQ"})
        p0 = (r.get("params") or [{}])[0]
        check("set_fx_param (normalisé)", abs(p0.get("normalized", 0) - 0.25) < 0.02,
              f"param0 normalized={p0.get('normalized')} (attendu 0.25)")
    r = call("bypass_fx", {"index": idx, "fx": "ReaEQ", "bypass": True})
    check("bypass_fx", r.get("bypassed") is True and r.get("enabled") is False,
          f"bypassed={r.get('bypassed')}, enabled={r.get('enabled')}")
    call("bypass_fx", {"index": idx, "fx": "ReaEQ", "bypass": False})
    r = call("remove_fx", {"index": idx, "fx": "ReaEQ"})
    check("remove_fx", r.get("ok") is True, f"removed={r.get('removed_fx')}")

    # Bus + send idempotents (assertions repeatable : on exige l'idempotence du 2e
    # appel, pas l'état frais — le harnais doit pouvoir tourner plusieurs fois).
    bus_name = TEST_TRACK + "_bus"
    b1 = call("create_bus", {"name": bus_name})
    b2 = call("create_bus", {"name": bus_name})
    bus_idx = b2.get("index")
    check("create_bus idempotent",
          isinstance(bus_idx, int) and b2.get("created") is False and bus_idx == b1.get("index"),
          f"index={bus_idx}, created2={b2.get('created')}")
    if isinstance(bus_idx, int) and bus_idx >= 0:
        s1 = call("create_send", {"index": idx, "dest_index": bus_idx, "vol_db": -3.0})
        s2 = call("create_send", {"index": idx, "dest_index": bus_idx})
        check("create_send idempotent",
              isinstance(s1.get("send_index"), int) and s2.get("created") is False
              and s2.get("send_index") == s1.get("send_index"),
              f"send_index={s1.get('send_index')}, created2={s2.get('created')}")
        # cleanup du bus (piste KLODY_VALIDATE* ; le send vers lui part avec)
        cr = call("list_tracks")
        btr = next((t for t in cr.get("tracks", []) if t.get("index") == bus_idx), {})
        if btr.get("name", "").startswith("KLODY_VALIDATE"):
            call("delete_track", {"index": bus_idx})

    # Région + marqueur : on prouve l'idempotence (2e appel created=False). Non
    # nettoyés (pas de delete_marker en P2) mais éphémères — le projet n'est JAMAIS
    # sauvegardé, ils disparaissent à la fermeture de REAPER.
    rg1 = call("add_region", {"start": 0.0, "end": 2.0, "name": TEST_TRACK + "_rgn"})
    rg2 = call("add_region", {"start": 0.0, "end": 2.0, "name": TEST_TRACK + "_rgn"})
    check("add_region idempotent",
          isinstance(rg1.get("region_id") or rg2.get("region_id"), int)
          and rg2.get("created") is False,
          f"region_id={rg1.get('region_id')}, created2={rg2.get('created')}")
    mk1 = call("add_marker", {"position": 1.0, "name": TEST_TRACK + "_mk"})
    mk2 = call("add_marker", {"position": 1.0, "name": TEST_TRACK + "_mk"})
    check("add_marker idempotent", mk2.get("created") is False,
          f"marker_id={mk1.get('marker_id')}, created2={mk2.get('created')}")

    # 5. Transport (optionnel) — play/stop, jamais record (record écrit de l'audio).
    if args.transport:
        r = call("transport_play")
        check("transport_play", r.get("playing") is True, "")
        r = call("transport_stop")
        check("transport_stop", r.get("playing") is False, "")

    # 6. Render (optionnel) — bounce projet → fichier. Preuve du chemin de sortie mix.
    if args.render:
        r = call("render_project", {"out_path": RENDER_OUT})
        files = r.get("output_files") or []
        check("render_project", bool(r.get("rendered")) and bool(files),
              f"fichiers={files}" if not err(r) else err(r))

    # 7. Cleanup — supprime UNIQUEMENT notre piste (vérifie le nom avant).
    r = call("list_tracks")
    tr = next((t for t in r.get("tracks", []) if t.get("index") == idx), {})
    if tr.get("name", "").startswith("KLODY_VALIDATE"):
        r = call("delete_track", {"index": idx})
        check("delete_track (cleanup)", r.get("track_count") == baseline,
              f"retour à {r.get('track_count')} (baseline {baseline})")
    else:
        check("delete_track (cleanup)", False,
              f"piste {idx} renommée/déplacée ('{tr.get('name')}') — suppression annulée par sécurité, "
              "supprime-la à la main (Cmd-Z)")

    # Résumé
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"\n━━━ {passed}/{total} verts ━━━")
    if passed < total:
        print("Rouges :")
        for label, ok, detail in _results:
            if not ok:
                print(f"  ❌ {label} — {detail}")
        return 1
    print("Chaîne REAPER validée bout-en-bout (incl. génération mélodie MIDI).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
