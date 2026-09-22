"""Run inside Blender; data API only, independent of the active editor."""
from pathlib import Path

import bpy

_SOURCE_KEY = 'klody_asset_source'
_COLLECTION_KEY = 'klody_asset_collection'


def _library(library_name):
    matches = [lib for lib in bpy.context.preferences.filepaths.asset_libraries
               if lib.name.casefold() == library_name.casefold()]
    if len(matches) != 1:
        raise ValueError(f'Bibliothèque absente ou ambiguë : {library_name!r}. '
                         'Consulter les asset_libraries, pas les add-ons.')
    root = Path(bpy.path.abspath(matches[0].path)).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'Dossier de bibliothèque introuvable : {root}')
    return matches[0].name, root


def _blend_files(root):
    paths = []
    for path in sorted(root.rglob('*.blend')):
        if path.is_file() and path.resolve().is_relative_to(root):
            paths.append(path)
        if len(paths) > 200:
            raise ValueError('Bibliothèque trop volumineuse : limiter à 200 fichiers .blend.')
    return paths


def _collections(path):
    # Reading names does not append anything and does not execute embedded scripts.
    with bpy.data.libraries.load(str(path), link=False, assets_only=True) as (src, dst):
        return sorted(src.collections)


def list_asset_library_assets(library_name='Z-Anatomy'):
    """List registered libraries or their collection assets without modifying scenes."""
    libraries = [{'name': lib.name, 'path': bpy.path.abspath(lib.path)}
                 for lib in bpy.context.preferences.filepaths.asset_libraries]
    if not library_name:
        return {'libraries': libraries}
    name, root = _library(library_name)
    assets, errors = [], []
    for path in _blend_files(root):
        relative = str(path.relative_to(root))
        try:
            assets.extend({'blend_file': relative, 'collection_name': collection}
                          for collection in _collections(path))
        except (OSError, RuntimeError) as exc:
            errors.append({'blend_file': relative, 'error': str(exc)})
    return {'library_name': name, 'path': str(root), 'assets': assets,
            'errors': errors, 'libraries': libraries, 'kind': 'asset_library'}


def _import_result(collection, scene, path, source_name, reused):
    objects = list(collection.all_objects)
    return {'library_file': str(path), 'source_collection': source_name,
            'collection_name': collection.name, 'scene_name': scene.name,
            'reused': reused, 'object_count': len(objects),
            'mesh_count': sum(o.type == 'MESH' for o in objects),
            'objects': [o.name for o in objects],
            'file_saved': False}


def import_asset_collection(collection_name, library_name='Z-Anatomy',
                            blend_file='', scene_name='', reuse_existing=True):
    """Append a named collection to a scene, preserving existing objects and UI state."""
    _, root = _library(library_name)
    scene = bpy.data.scenes.get(scene_name) if scene_name else bpy.context.scene
    if scene is None:
        raise ValueError(f'Scène introuvable : {scene_name!r}')
    if blend_file:
        path = (root / blend_file).resolve()
        if not path.is_relative_to(root) or path.suffix != '.blend' or not path.is_file():
            raise ValueError('blend_file doit désigner un .blend existant dans la bibliothèque.')
        paths = [path]
    else:
        paths = _blend_files(root)
    matches = [path for path in paths if collection_name in _collections(path)]
    if len(matches) != 1:
        raise ValueError(f'Collection absente ou ambiguë : {collection_name!r}. '
                         'Lister les assets et reprendre exactement collection_name et blend_file.')
    path = matches[0].resolve()
    if reuse_existing:
        for collection in scene.collection.children_recursive:
            if (collection.get(_SOURCE_KEY) == str(path)
                    and collection.get(_COLLECTION_KEY) == collection_name):
                return _import_result(collection, scene, path, collection_name, True)
    # Only append the selected collection; never load a new file/startup scene.
    with bpy.data.libraries.load(str(path), link=False, assets_only=True) as (src, dst):
        dst.collections = [collection_name]
    collection = dst.collections[0]
    if collection is None:
        raise RuntimeError('Blender n’a pas chargé la collection demandée.')
    scene.collection.children.link(collection)
    collection[_SOURCE_KEY] = str(path)
    collection[_COLLECTION_KEY] = collection_name
    # Imported atlas collections can carry hidden flags from their original layout.
    # Adjust only newly appended data; retain labels as hidden editable objects.
    for child in [collection, *collection.children_recursive]:
        child.hide_viewport = False
        child.hide_render = False
    for obj in collection.all_objects:
        annotation = obj.type == 'FONT' or (obj.type == 'MESH' and not obj.data.polygons)
        obj.hide_viewport = annotation
        obj.hide_render = annotation
    for layer in scene.view_layers:
        layer.update()
    return _import_result(collection, scene, path, collection_name, False)
