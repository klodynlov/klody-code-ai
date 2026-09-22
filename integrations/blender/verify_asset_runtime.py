"""Isolated real-bpy regression checks; run with Blender --background --factory-startup."""
import importlib.util
import json
import tempfile
from pathlib import Path

import bpy

spec=importlib.util.spec_from_file_location('runtime',Path(__file__).with_name('asset_library_runtime.py'))
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
checks=[]
with tempfile.TemporaryDirectory(prefix='klody-assets-') as tmp:
    root=Path(tmp)
    source=bpy.data.collections.new('Asset "test"')
    mesh=bpy.data.meshes.new('fixture mesh')
    mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    obj=bpy.data.objects.new('Asset geometry',mesh)
    source.objects.link(obj)
    obj.hide_viewport=True;source.hide_viewport=True
    source.asset_mark()
    bpy.data.libraries.write(str(root/'fixture.blend'),{source})
    bpy.data.collections.remove(source)
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)
    lib=bpy.context.preferences.filepaths.asset_libraries.new(name='Fixture',directory=tmp)
    original={o.name:tuple(o.matrix_world) for o in bpy.context.scene.objects}
    selected=list(bpy.context.selected_objects)
    active=bpy.context.view_layer.objects.active
    frame=bpy.context.scene.frame_current
    all_objects=set(bpy.data.objects)
    listing=mod.list_asset_library_assets('Fixture')
    assert listing['assets']==[{'blend_file':'fixture.blend','collection_name':'Asset "test"'}],listing
    assert set(bpy.data.objects)==all_objects
    checks.append('list is read only and returns exact asset names')
    result=mod.import_asset_collection('Asset "test"','Fixture','fixture.blend')
    assert result['mesh_count']==1 and not result['reused']
    imported=bpy.data.collections[result['collection_name']]
    assert not imported.hide_viewport
    assert all(not o.hide_viewport for o in imported.all_objects)
    assert all(o.name in bpy.context.scene.objects for o in imported.all_objects)
    assert list(bpy.context.selected_objects)==selected
    assert bpy.context.view_layer.objects.active==active
    assert bpy.context.scene.frame_current==frame
    assert all(tuple(bpy.data.objects[n].matrix_world)==m for n,m in original.items())
    checks.append('append preserves selection frame transforms and makes new geometry visible')
    count=len(bpy.data.objects)
    again=mod.import_asset_collection('Asset "test"','Fixture')
    assert again['reused'] and len(bpy.data.objects)==count
    checks.append('retry reuses existing import')
    for kwargs in [{'collection_name':'not present'}, {'collection_name':'Asset "test"','blend_file':'../outside.blend'}, {'collection_name':'Asset "test"','scene_name':'absent'}]:
        try:mod.import_asset_collection(library_name='Fixture',**kwargs)
        except ValueError:pass
        else:raise AssertionError(f'Expected refusal: {kwargs}')
        assert len(bpy.data.objects)==count
    checks.append('invalid collection path and target scene cause no scene mutation')
    target=bpy.data.scenes.new('Other scene')
    second=mod.import_asset_collection('Asset "test"','Fixture',scene_name=target.name)
    assert not second['reused'] and second['scene_name']==target.name
    assert bpy.context.scene!=target
    checks.append('explicit target scene import leaves active scene unchanged')
    # Duplicate source names across files must require an exact blend_file.
    import shutil
    shutil.copyfile(root/'fixture.blend',root/'duplicate.blend')
    try:mod.import_asset_collection('Asset "test"','Fixture')
    except ValueError:pass
    else:raise AssertionError('Ambiguous asset accepted')
    checks.append('ambiguous collection requires exact source file')
print('ASSET_RUNTIME_CHECKS',json.dumps(checks))
