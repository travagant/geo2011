"""Check experimental new-leader geometry with a provided Blender scene.

The .blend is private input and is not committed. Example:
  D3_LEADER_BLEND='google_drive/новый юнит тест.blend' \
  LD_LIBRARY_PATH=/tmp/blender-libs PYTHONPATH=/tmp/d3_bpy45:/tmp/d3_bpy:. \
  python tests/blender_new_leader_model_test.py

This is not a game test and does not create a mod.
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'blender'))
import bpy
from mathutils.kdtree import KDTree
import io_scene_d3unit as addon
from d3tool import anim, audit, gfile, gltf, gltf_out, scene, texture
from io_scene_d3unit import auto_texture
from d3tool.new_leader_model import build_geometry
from d3tool.new_leader_scene import build_scene
from d3tool.new_leader_staging import stage_visual_assets


def points(objects, frames):
    out = {}
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        deps = bpy.context.evaluated_depsgraph_get()
        for obj in objects:
            ev = obj.evaluated_get(deps)
            mesh = ev.to_mesh()
            try:
                out[(frame, obj.name)] = [tuple(ev.matrix_world @ v.co) for v in mesh.vertices]
            finally:
                ev.to_mesh_clear()
    return out


def run(path):
    bpy.context.preferences.filepaths.use_scripts_auto_execute = False
    bpy.ops.wm.open_mainfile(filepath=str(Path(path).resolve()), load_ui=False)
    arms = [obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE']
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    assert len(arms) == 1 and len(meshes) == 2
    arm = arms[0]
    for obj in meshes:
        assert any(mod.type == 'ARMATURE' and mod.object == arm for mod in obj.modifiers)
        assert addon.exporter._influence_report(obj)[0] == 0
    frames = [1, 100, 350, 500, 700]
    original = points(meshes, frames)
    donor = anim.parse_anim((ROOT / 'Empire/Leader-Warrior/character_empire_leader-warrior_baseanims.a').read_bytes())
    with tempfile.TemporaryDirectory(prefix='leader_model_test_') as tmp:
        intermediate = os.path.join(tmp, 'source.gltf')
        assert addon.exporter._export_gltf(bpy.context, intermediate, [arm] + meshes, True, 'source') == {'FINISHED'}
        model = gltf.load_gltf(intermediate)
        models = [model] + list(model.submodels)
        names = [item.mesh_name for item in models]
        addon.bridge.require()
        atlases, textures, warnings, failed, per_slot = auto_texture.prepare(meshes)
        assert not warnings and not failed and not per_slot and set(atlases) == set(names)
        assert textures
        for atlas in atlases.values():
            name = atlas[:-4] + '.t'
            assert name in textures
            texture.parse_t(textures[name])
        data = build_geometry(model, donor, 'arbiter_test', atlases)
        rebuilt = gfile.parse_geometry_file(data)
        assert not rebuilt.parse_error and not gfile.count_mismatches(rebuilt)
        # Follow the engine-style node chain, not only the tolerant parser.
        _, declared_count = gfile.mesh_count(data)
        assert declared_count == 2
        parts = list(gfile.iter_parts(rebuilt))
        assert len(parts) == 2
        # Use the actual uploaded base scene when present, otherwise the local donor.
        source = ROOT / 'google_drive/character_empire_leader-warrior.scene'
        if not source.is_file():
            source = ROOT / 'Empire/Leader-Warrior/character_empire_leader-warrior.scene'
        source_bytes = source.read_bytes()
        scene_bytes = build_scene(source_bytes, data, r'resources\characters\empire\arbitr\arbiter_test.g')
        scene_path = os.path.join(tmp, 'arbitr.scene')
        Path(scene_path).write_bytes(scene_bytes)
        nodes = audit.scene_gobjs(scene_path)
        assert len(nodes) == len(parts)
        for i, (node, part) in enumerate(zip(nodes, parts)):
            assert node.index == i and node.name == part.name and node.g_base == 'arbiter_test.g'
            assert node.attrs['material0_diffuse'] == atlases[names[i]]
            assert int(node.attrs['vertexs_weights_num']) == len(part.vertices)
            assert int(node.attrs['material0_triangles_num']) == len(part.indices) // 3
            assert int(node.attrs['bones_num']) == len(part.bones)
            assert int(node.attrs['weights_on_vertex']) == part.weights_on_vertex
        before_scene = scene.parse_scene(source_bytes.decode('latin-1'))
        after_scene = scene.parse_scene(scene_bytes.decode('latin-1'))
        assert before_scene.find_all('bones')[0].prop('file') == after_scene.find_all('bones')[0].prop('file')
        assert len(before_scene.find_all('goclass')) == len(after_scene.find_all('goclass'))
        for path in (r'..\bad.g', r'resources\characters\empire\leader-warrior\character_empire_leader-warrior.g'):
            try:
                build_scene(source_bytes, data, path)
                raise AssertionError('unsafe donor or path was accepted')
            except ValueError:
                pass
        print('PASS: scene, geometry and extracted texture bindings; donor animation retained', flush=True)
        stage = Path(tmp) / 'new_visual_test'
        report = stage_visual_assets(model, donor, source_bytes, 'arbitr', atlases, textures, stage)
        assetdir = stage / 'resources/characters/empire/arbitr'
        assert (assetdir / 'arbitr_model.g').is_file() and (assetdir / 'arbitr_model.scene').is_file()
        assert all((assetdir / name).read_bytes() == payload for name, payload in textures.items())
        assert len(report['parts']) == 2 and (stage / 'EXPERIMENT.json').is_file()
        assert not list(stage.rglob('*.char')) and not list(stage.rglob('*.vdpack'))
        original_bytes = (assetdir / 'arbitr_model.g').read_bytes()
        try:
            stage_visual_assets(model, donor, source_bytes, 'arbitr', atlases, textures, stage)
            raise AssertionError('existing destination accepted')
        except ValueError:
            pass
        assert (assetdir / 'arbitr_model.g').read_bytes() == original_bytes
        for bad in (Path(tmp) / 'resources' / 'game', Path(tmp) / 'Profiles' / 'game'):
            bad.parent.mkdir(exist_ok=True)
            try:
                stage_visual_assets(model, donor, source_bytes, 'arbitr', atlases, textures, bad)
                raise AssertionError('game tree accepted')
            except ValueError:
                assert not bad.exists()
        incomplete = Path(tmp) / 'missing_texture'
        try:
            stage_visual_assets(model, donor, source_bytes, 'arbitr', atlases, {}, incomplete)
            raise AssertionError('missing texture accepted')
        except ValueError:
            assert not incomplete.exists()
        print('PASS: isolated visual assets staging and overwrite protection', flush=True)
        # A modified action must not silently ship with the unmodified .a.
        node_index = next(i for i, node in enumerate(model.nodes) if node.get('name') == 'Hips')
        channel = model.anim_channels[node_index]['translation']
        previous = channel[100]
        channel[100] = (previous[0] + .05, *previous[1:])
        try:
            try:
                build_geometry(model, donor, 'arbiter_test', atlases)
                raise AssertionError('changed animation was accepted')
            except ValueError as exc:
                assert 'animation differs' in str(exc)
        finally:
            channel[100] = previous
        back = os.path.join(tmp, 'back.gltf')
        gltf_out.write_gltf_to(back, rebuilt, donor)
        addon.importer._prune_missing_images(back)
        before = set(bpy.data.objects)
        assert bpy.ops.import_scene.gltf(filepath=back) == {'FINISHED'}
        created = set(bpy.data.objects) - before
        new = {obj.name: obj for obj in created if obj.type == 'MESH'}
        generated = [new['arbiter_test_part_%02d' % index] for index in range(len(names))]
        roundtrip = points(generated, frames)
        for index, name in enumerate(names):
            for frame in frames:
                old = original[(frame, name)]
                current = roundtrip[(frame, generated[index].name)]
                tree = KDTree(len(old))
                for i, point in enumerate(old):
                    tree.insert(point, i)
                tree.balance()
                distances = [tree.find(point)[2] for point in current]
                mean, maximum = sum(distances) / len(distances), max(distances)
                print(name, frame, 'mean %.8f max %.8f' % (mean, maximum), flush=True)
                assert maximum < .012 and mean < .0001
    print('PASS: experimental geometry round-trip; no game test', flush=True)


try:
    path = os.environ.get('D3_LEADER_BLEND')
    if not path:
        print('SKIP: set D3_LEADER_BLEND to a private Blender scene', flush=True)
    else:
        run(path)
except BaseException:
    traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
