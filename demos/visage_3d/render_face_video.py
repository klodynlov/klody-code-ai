#!/usr/bin/env python3
"""Rend face_animation.blend en MP4 lisible d'un double-clic (cadrage auto sur
le visage, caméra + lumière, 129 frames)."""
import math
import os

import bpy

BASE = os.path.dirname(os.path.abspath(__file__))
bpy.ops.wm.open_mainfile(filepath=os.path.join(BASE, "face_animation.blend"))

obj = bpy.data.objects["Face"]
scene = bpy.context.scene

# --- cadrage auto : centre + taille de la bbox du mesh (Basis) ---
xs = [v.co.x for v in obj.data.vertices]
ys = [v.co.y for v in obj.data.vertices]
zs = [v.co.z for v in obj.data.vertices]
cx = (min(xs) + max(xs)) / 2
cy = (min(ys) + max(ys)) / 2
cz = (min(zs) + max(zs)) / 2
extent = max(max(xs) - min(xs), max(zs) - min(zs))

cam_data = bpy.data.cameras.new("Cam")
cam = bpy.data.objects.new("Cam", cam_data)
cam.location = (cx, cy - extent * 2.1, cz)     # recule sur -Y, aligné au centre
cam.rotation_euler = (math.radians(90), 0.0, 0.0)  # regarde vers +Y
bpy.context.collection.objects.link(cam)
scene.camera = cam

sun_data = bpy.data.lights.new("Sun", "SUN")
sun_data.energy = 4.5
sun = bpy.data.objects.new("Sun", sun_data)
sun.rotation_euler = (math.radians(55), math.radians(10), math.radians(25))
bpy.context.collection.objects.link(sun)

mat = bpy.data.materials.new("Skin")
mat.diffuse_color = (0.86, 0.72, 0.63, 1.0)
obj.data.materials.append(mat)

# --- sortie : séquence PNG (ce build Blender n'a pas le mux FFMPEG) ---
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = 600
scene.render.resolution_y = 600
scene.render.fps = 24
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = os.path.join(BASE, "_frames", "f####")

scene.frame_start = 1
scene.frame_end = len(obj.data.shape_keys.key_blocks) - 1  # nb de frames

bpy.ops.render.render(animation=True)
print(f"FRAMES rendues {scene.frame_start}..{scene.frame_end} dans _frames/")
