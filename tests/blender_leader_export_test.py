"""Geometry-only leader export through real bpy. No game validation.

LD_LIBRARY_PATH=/tmp/blender-libs PYTHONPATH=/tmp/d3_bpy:. python tests/blender_leader_export_test.py
"""
import os
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'blender'))
import bpy
from mathutils import kdtree
import io_scene_d3unit as addon
from d3tool import gfile


def reject(fn, expected):
    try:
        fn()
    except (ValueError, FileExistsError) as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError('expected refusal: ' + expected)


def run():
    addon.register()
    for name in ('warrior', 'ranger', 'thief', 'archmage'):
        for kit in ('1', '2', '3'):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            result = addon.leader.import_leader(
                bpy.context, str(ROOT / 'Profiles' / ('empire_leader-' + name + '.char')), kit)
            arm = result['armature']
            bpy.context.view_layer.objects.active = arm
            sources = result['info']['leader_sources']
            assert len(sources) == len(result['meshes'])
            with tempfile.TemporaryDirectory() as tmp:
                target = os.path.join(tmp, 'leader.zip')
                out = addon.leader.export_leader(bpy.context, target)
                assert out['files'] == len({s['path'] for s in sources})
                if name == 'ranger' and kit == '3':
                    assert len(out['original_files']) == 1
                else:
                    assert not out['original_files']
                with zipfile.ZipFile(target) as z:
                    assert z.testzip() is None
                    for s in sources:
                        if s['resource'] in out['original_files']:
                            assert z.read(s['resource']) == Path(s['path']).read_bytes()
                            continue
                        donor = gfile.parse_geometry_file(Path(s['path']).read_bytes())
                        generated = gfile.parse_geometry_file(z.read(s['resource']))
                        assert not generated.parse_error and not gfile.count_mismatches(generated)
                        orig = gfile.iter_parts(donor)[s['index']]
                        actual = gfile.iter_parts(generated)[s['index']]
                        assert len(orig.indices) == len(actual.indices), (name, kit, s["name"], s["path"], s["index"], len(orig.indices), len(actual.indices))
                        assert [x.name for x in orig.bones] == [x.name for x in actual.bones]
                        assert actual.material_diffuse == orig.material_diffuse
                        assert actual.attrs.get('materials_num') == orig.attrs.get('materials_num')
                        tree = kdtree.KDTree(len(orig.vertices))
                        for idx, v in enumerate(orig.vertices):
                            tree.insert(v.position[:3], idx)
                        tree.balance()
                        for v in actual.vertices:
                            near = tree.find_range(v.position[:3], 0.0002)
                            assert near, (name, kit, s['name'], v.position)
                            # Position, UV and normal should match at least one
                            # donor vertex; seam duplicates can share a position.
                            def weights(vertex, bones):
                                return {bones[j].name: w for w, j in zip(vertex.gltf_weights, vertex.gltf_joints) if w > 1e-3}
                            vw = weights(v, actual.bones)
                            def agrees(candidate):
                                ow = weights(candidate, orig.bones)
                                return (sum((a-b)**2 for a,b in zip(v.uv, candidate.uv)) < 1e-8
                                        and sum((a-b)**2 for a,b in zip(v.normal, candidate.normal)) < 0.0004
                                        and set(vw) == set(ow)
                                        and all(abs(vw[n] - ow[n]) < 0.002 for n in vw))
                            assert any(agrees(orig.vertices[idx]) for _, idx, _ in near), (name, kit, s['name'], 'normal/UV/weights', vw, [weights(orig.vertices[idx], orig.bones) for _, idx, _ in near])
                reject(lambda: addon.leader.export_leader(bpy.context, target), 'существует')
                if name == 'ranger' and kit == '3':
                    record = next(s for s in sources if s['resource'] in out['original_files'])
                    obj = next(o for o in result['meshes'] if o.name == record['name'])
                    obj.data.vertices[0].co.x += 0.025
                    invalid = os.path.join(tmp, 'weapon_changed.zip')
                    reject(lambda: addon.leader.export_leader(bpy.context, invalid), 'потерял треугольники')
                    assert not os.path.exists(invalid)
                if name == 'ranger' and kit == '1':
                    obj = next(o for o in result['meshes'] if o.name.endswith('set1_body'))
                    # Edit vertex positions without changing UV/topology.
                    obj.data.vertices[0].co.x += 0.025
                    changed = os.path.join(tmp, 'changed.zip')
                    addon.leader.export_leader(bpy.context, changed)
                    s = next(s for s in sources if s['name'] == obj.name)
                    with zipfile.ZipFile(target) as before, zipfile.ZipFile(changed) as after:
                        old = gfile.parse_geometry_file(before.read(s['resource']))
                        new = gfile.parse_geometry_file(after.read(s['resource']))
                        assert [v.position for v in old.vertices] != [v.position for v in new.vertices]
                        for resource in set(before.namelist()) - {s['resource'], 'LEADER_EXPORT_README.txt'}:
                            assert before.read(resource) == after.read(resource), resource
                    # Reweight an existing vertex using bones already in the part.
                    vertex = next(v for v in obj.data.vertices if len([g for g in v.groups if g.weight > 1e-4]) >= 2)
                    groups = [g for g in vertex.groups if g.weight > 1e-4]
                    obj.vertex_groups[groups[0].group].add([vertex.index], 0.7, 'REPLACE')
                    obj.vertex_groups[groups[1].group].add([vertex.index], 0.3, 'REPLACE')
                    for group in groups[2:]:
                        obj.vertex_groups[group.group].remove([vertex.index])
                    weighted = os.path.join(tmp, 'weighted.zip')
                    addon.leader.export_leader(bpy.context, weighted)
                    with zipfile.ZipFile(changed) as before, zipfile.ZipFile(weighted) as after:
                        old = gfile.parse_geometry_file(before.read(s['resource']))
                        new = gfile.parse_geometry_file(after.read(s['resource']))
                        assert [(v.stored_weights, v.bones) for v in old.vertices] != [(v.stored_weights, v.bones) for v in new.vertices]
                    # A forbidden topology/UV modification fails before writing.
                    uv = obj.data.uv_layers.active.data[0]
                    uv.uv.x += .1
                    invalid = os.path.join(tmp, 'invalid.zip')
                    reject(lambda: addon.leader.export_leader(bpy.context, invalid), 'топология')
                    assert not os.path.exists(invalid)
                    uv.uv.x -= .1
                    arm.animation_data.action.fcurves[0].keyframe_points[0].co.y += .2
                    reject(lambda: addon.leader.export_leader(bpy.context, invalid), 'анимация изменены')
                    assert not os.path.exists(invalid)
            print(name, kit, 'PASS', flush=True)
    # Provenance and rig hash must survive saving and reopening a .blend.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = addon.leader.import_leader(bpy.context, str(ROOT / 'Profiles/empire_leader-warrior.char'), '1')
    bpy.context.view_layer.objects.active = result['armature']
    with tempfile.TemporaryDirectory() as tmp:
        blend = os.path.join(tmp, 'leader.blend')
        bpy.ops.wm.save_as_mainfile(filepath=blend)
        bpy.ops.wm.open_mainfile(filepath=blend)
        arm = next(o for o in bpy.context.scene.objects if o.type == 'ARMATURE' and addon.unitprops.PROP in o)
        bpy.context.view_layer.objects.active = arm
        addon.leader.export_leader(bpy.context, os.path.join(tmp, 'after_reopen.zip'))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = addon.leader.import_leader(bpy.context, str(ROOT / 'Profiles/empire_leader-ranger.char'), '3')
    bpy.context.view_layer.objects.active = result['armature']
    with tempfile.TemporaryDirectory() as tmp:
        blend = os.path.join(tmp, 'ranger.blend')
        bpy.ops.wm.save_as_mainfile(filepath=blend)
        bpy.ops.wm.open_mainfile(filepath=blend)
        arm = next(o for o in bpy.context.scene.objects if o.type == 'ARMATURE' and addon.unitprops.PROP in o)
        bpy.context.view_layer.objects.active = arm
        out = addon.leader.export_leader(bpy.context, os.path.join(tmp, 'after_reopen.zip'))
        assert len(out['original_files']) == 1
    print('leader export PASS', flush=True)

try:
    run()
except BaseException:
    traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
