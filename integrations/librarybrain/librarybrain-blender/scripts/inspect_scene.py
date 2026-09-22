"""Read the active Blender scene; no object mutation, save, bake or render.

Run inside Blender with --python-exit-code 1 --python inspect_scene.py.
stdout contains a JSON report between explicit markers, alongside Blender logs.
"""
import json
from pathlib import Path

import bpy


def inspect_scene():
    scene = bpy.context.scene
    objects = []
    for obj in sorted(scene.objects, key=lambda item: item.name):
        record = {
            "name": obj.name,
            "type": obj.type,
            "parent": obj.parent.name if obj.parent else None,
            "location": list(obj.location),
            "scale": list(obj.scale),
            "dimensions": list(obj.dimensions),
            "hide_render": obj.hide_render,
            "linked_library": obj.library.filepath if obj.library else None,
            "modifiers": [{"name": mod.name, "type": mod.type} for mod in obj.modifiers],
        }
        if obj.type == "MESH":
            record["mesh"] = {
                "vertices": len(obj.data.vertices),
                "polygons": len(obj.data.polygons),
                "uv_layers": [uv.name for uv in obj.data.uv_layers],
                "materials": [mat.name if mat else None for mat in obj.data.materials],
                "shape_keys": (
                    [key.name for key in obj.data.shape_keys.key_blocks]
                    if obj.data.shape_keys else []
                ),
            }
        if obj.type == "ARMATURE":
            record["bones"] = [bone.name for bone in obj.data.bones]
        anim = obj.animation_data
        record["object_action"] = anim.action.name if anim and anim.action else None
        objects.append(record)
    images = []
    for img in bpy.data.images:
        if img.source not in {"FILE", "SEQUENCE", "TILED", "MOVIE"}:
            continue
        packed = bool(img.packed_file or img.packed_files)
        resolved = bpy.path.abspath(img.filepath, library=img.library) if img.filepath else None
        # Tiles/sequences need per-frame resolution; do not call them missing here.
        exists = Path(resolved).is_file() if resolved and img.source == "FILE" else None
        images.append({
            "name": img.name, "source": img.source, "path": img.filepath,
            "resolved_path": resolved, "packed": packed, "external_file_exists": exists,
        })
    return {
        "blender_version": bpy.app.version_string,
        "filepath": bpy.data.filepath,
        "scene": scene.name,
        "units": {"system": scene.unit_settings.system, "scale_length": scene.unit_settings.scale_length},
        "engine": scene.render.engine,
        "camera": scene.camera.name if scene.camera else None,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage],
        "frames": [scene.frame_start, scene.frame_end],
        "fps": scene.render.fps / scene.render.fps_base,
        "view_transform": scene.view_settings.view_transform,
        "objects": objects,
        "external_images": images,
        "limitations": [
            "Active scene only; mesh counts are original, before modifier evaluation.",
            "No manifold/intersection, animation motion, render quality or performance validation.",
            "Shape-key, material, node and other datablock actions are not enumerated.",
            "Sequences and tiled image paths are reported without completeness checks.",
        ],
    }


if __name__ == "__main__":
    print("BLENDER_INSPECTION_BEGIN")
    print(json.dumps(inspect_scene(), ensure_ascii=False, indent=2))
    print("BLENDER_INSPECTION_END")
