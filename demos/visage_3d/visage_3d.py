#!/usr/bin/env python3
"""Pipeline « visage 3D » bout-en-bout — UNE commande pour Klody.

Depuis la requête langage naturel « séquence-moi un visage 3D », Klody n'a qu'à
lancer :  python visage_3d.py   (ou --skip-capture pour rejouer la dernière capture)

Étapes : capture webcam → mesh (Delaunay, iris élagué) → .blend animé → .mp4

CAPTURE + macOS TCC : la caméra exige un process GUI autorisé. En DAEMON (Klody),
macOS bloque la caméra → on REBONDIT la capture dans Terminal.app (qui a la perm,
accordée 1 fois par l'humain) via osascript, puis on attend le vrai JSON. Les
étapes build/render tournent headless partout. Aucun sudo/admin requis (et il
n'aiderait pas : TCC caméra n'est PAS contournable par root, SIP actif)."""
import argparse
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
BLENDER = "/opt/homebrew/bin/blender"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
DATA = os.path.join(BASE, "face_animation_data.json")


def _log(msg):
    print(f"[visage_3d] {msg}", flush=True)


def capture(duration, device):
    """Capture la webcam. Directe si terminal GUI ; sinon rebond Terminal.app."""
    if sys.stdin.isatty():
        _log("terminal interactif → capture directe")
        cmd = [PY, os.path.join(BASE, "sequencer_visage_3d.py"),
               "--capture", "--duration", str(duration)]
        if device is not None:
            cmd += ["--device", str(device)]
        subprocess.run(cmd, check=True)
        return

    _log("contexte daemon → rebond de la capture dans Terminal.app (perm caméra)")
    before = os.path.getmtime(DATA) if os.path.exists(DATA) else 0.0
    dev = f" --device {device}" if device is not None else ""
    inner = f"cd {BASE} && {PY} sequencer_visage_3d.py --capture --duration {duration}{dev}"
    subprocess.run(
        ["osascript", "-e", f'tell application "Terminal" to do script "{inner}"'],
        check=True,
    )
    _log("attente de la capture réelle (≤ 90 s)…")
    t0 = time.time()
    while time.time() - t0 < 90:
        if os.path.exists(DATA) and os.path.getmtime(DATA) > before:
            try:
                with open(DATA) as f:
                    d = json.load(f)
                if d.get("source") == "webcam" and d.get("frames"):
                    _log(f"capture OK : {len(d['frames'])} frames réelles")
                    return
            except (json.JSONDecodeError, OSError):
                pass  # JSON encore en cours d'écriture par la capture → réessai au tour suivant
        time.sleep(1)
    raise SystemExit(
        "Capture non aboutie. Vérifie : Terminal.app autorisé pour la Caméra "
        "(Réglages > Confidentialité), et la fenêtre de capture ouverte."
    )


def build():
    """Triangule les landmarks (iris élagué) puis construit le .blend animé."""
    import numpy as np
    from scipy.spatial import Delaunay

    with open(DATA) as f:
        d = json.load(f)
    f0 = np.array(d["frames"][0])
    tri = Delaunay(f0[:, :2])

    def edges(a, b, c):
        return (np.linalg.norm(f0[a, :2] - f0[b, :2]),
                np.linalg.norm(f0[b, :2] - f0[c, :2]),
                np.linalg.norm(f0[c, :2] - f0[a, :2]))

    thr = np.percentile([e for s in tri.simplices for e in edges(*s)], 95) * 1.8
    faces = [[int(a), int(b), int(c)] for a, b, c in tri.simplices
             if max(a, b, c) < 468 and max(edges(a, b, c)) <= thr]  # <468 = hors iris
    with open(os.path.join(BASE, "face_mesh_faces.json"), "w") as f:
        json.dump({"faces": faces, "n_verts": int(len(f0))}, f)
    _log(f"mesh : {len(f0)} verts, {len(faces)} faces")

    subprocess.run([BLENDER, "--background", "--python-exit-code", "1",
                    "--python", os.path.join(BASE, "face_anim_blender.py")], check=True)
    _log("build .blend OK")


def render():
    """Rend l'animation en PNG (Blender) puis assemble un MP4 (ffmpeg)."""
    subprocess.run([BLENDER, "--background", "--python-exit-code", "1",
                    "--python", os.path.join(BASE, "render_face_video.py")], check=True)
    frames = os.path.join(BASE, "_frames", "f%04d.png")
    mp4 = os.path.join(BASE, "face_animation.mp4")
    subprocess.run([FFMPEG, "-y", "-framerate", "24", "-i", frames, "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", mp4],
                   check=True)
    _log(f"vidéo : {mp4}")


def main():
    ap = argparse.ArgumentParser(description="Pipeline visage 3D bout-en-bout")
    ap.add_argument("--duration", type=int, default=8, help="durée capture (s)")
    ap.add_argument("--device", type=int, default=None, help="index caméra (défaut: auto)")
    ap.add_argument("--skip-capture", action="store_true",
                    help="réutilise la dernière capture au lieu d'en refaire une")
    a = ap.parse_args()
    if not a.skip_capture:
        capture(a.duration, a.device)
    elif not os.path.exists(DATA):
        raise SystemExit("--skip-capture mais aucune capture existante.")
    build()
    render()
    _log("TERMINÉ → face_animation.blend + face_animation.mp4")


if __name__ == "__main__":
    main()
