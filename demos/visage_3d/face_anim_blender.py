#!/usr/bin/env python3
"""
Construit un visage 3D animé dans Blender depuis les landmarks MediaPipe.

À lancer OBLIGATOIREMENT avec --python-exit-code 1 pour que Blender rende un
code de sortie ≠ 0 si ce script lève (sinon Blender AVALE l'erreur, exit 0) :

  blender --background --python-exit-code 1 --python face_anim_blender.py

Méthode (API bpy CORRECTE) :
  • mesh.from_pydata(verts, [], faces)         → surface réelle (Delaunay 468 pts)
  • obj.shape_key_add(name=...)                → 1 Shape Key / frame (PAS
    mesh.shape_keys.add_key — qui N'EXISTE PAS ; Mesh.shape_keys est en
    lecture seule et vaut None tant qu'aucune clé n'est ajoutée par l'Object)
  • key.value keyframé (0/1/0) + interp CONSTANT → rejoue frame par frame
"""

import json
import os

import bpy

BASE = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(BASE, "face_animation_data.json")))
faces = json.load(open(os.path.join(BASE, "face_mesh_faces.json")))["faces"]

frames = data["frames"]
W, _H = data["resolution"]
S = 2.0 / W  # échelle pixels → ~2 unités Blender de large

# Centre sur le centroïde de la frame 0 (recentre le nuage sur l'origine)
f0 = frames[0]
n = len(f0)
cx = sum(p[0] for p in f0) / n
cy = sum(p[1] for p in f0) / n
cz = sum(p[2] for p in f0) / n


def to_blender(p):
    """MediaPipe (x px, y px descendant, z profondeur) → Blender (X droite, Y
    profondeur vers -Y = caméra, Z haut)."""
    return ((p[0] - cx) * S, -(p[2] - cz) * S, -(p[1] - cy) * S)


# Scène propre
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

# Mesh de base = frame 0
verts0 = [to_blender(p) for p in f0]
mesh = bpy.data.meshes.new("FaceMesh")
mesh.from_pydata(verts0, [], faces)
mesh.update()
obj = bpy.data.objects.new("Face", mesh)
bpy.context.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)

# Basis (position de repos) — crée le système de Shape Keys sur l'OBJET
obj.shape_key_add(name="Basis", from_mix=False)

scene = bpy.context.scene
scene.frame_start = 1
scene.frame_end = len(frames)

# Une Shape Key par frame, isolée dans le temps (0 avant / 1 sur sa frame / 0 après)
for fi, frame in enumerate(frames):
    sk = obj.shape_key_add(name=f"F{fi + 1:03d}", from_mix=False)
    for i, p in enumerate(frame):
        sk.data[i].co = to_blender(p)
    frame_no = fi + 1
    sk.value = 0.0
    if frame_no > 1:
        sk.keyframe_insert("value", frame=frame_no - 1)
    sk.value = 1.0
    sk.keyframe_insert("value", frame=frame_no)
    sk.value = 0.0
    if frame_no < len(frames):
        sk.keyframe_insert("value", frame=frame_no + 1)

# Interpolation CONSTANT → rejeu net image par image (pas de morphing fantôme).
# Cosmétique : l'accès aux fcurves a changé en Blender 4.4+/5.x (actions à slots),
# donc on tente plusieurs chemins et on n'échoue JAMAIS le build là-dessus.
def _iter_fcurves(action):
    if hasattr(action, "fcurves"):  # Blender ≤ 4.3
        return list(action.fcurves)
    fcs = []  # Blender 4.4+/5.x : action → layers → strips → channelbags
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for cbag in getattr(strip, "channelbags", []):
                fcs.extend(getattr(cbag, "fcurves", []))
    return fcs


skeys = obj.data.shape_keys
try:
    if skeys.animation_data and skeys.animation_data.action:
        for fc in _iter_fcurves(skeys.animation_data.action):
            for kp in fc.keyframe_points:
                kp.interpolation = "CONSTANT"
except Exception as exc:  # noqa: BLE001 — polish optionnel, jamais bloquant
    print(f"(interp CONSTANT ignorée : {exc})")

out = os.path.join(BASE, "face_animation.blend")
bpy.ops.wm.save_as_mainfile(filepath=out)
print(
    f"SAVED {out} | verts={len(verts0)} faces={len(faces)} "
    f"frames={len(frames)} shape_keys={len(skeys.key_blocks)}"
)
