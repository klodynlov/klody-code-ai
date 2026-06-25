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
    call("set_track_volume", {"index": idx, "db": -6.0})
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
