"""Diagnostic only: evaluate reverse conversion of unedited leader meshes.

Run from the repository root with Blender's Python, for example:
  LD_LIBRARY_PATH=/tmp/blender-libs PYTHONPATH=/tmp/d3_bpy:. python tools/leader_export_probe.py

Never writes game files. Nearest-position checks do NOT validate topology,
weights, normals, materials, animation, or actual in-game compatibility.
"""
import copy
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'blender'))
sys.path.insert(0, str(ROOT))

import bpy
from mathutils import kdtree
import io_scene_d3unit as addon
from d3tool import anim, gfile, gltf


def run():
    addon.register()
    failures = []
    for leader in ('warrior', 'ranger', 'thief', 'archmage'):
        for kit in ('1', '2', '3'):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            result = addon.leader.import_leader(
                bpy.context, str(ROOT / 'Profiles' / ('empire_leader-' + leader + '.char')), kit)
            arm = result['armature']
            anim_path = ROOT / 'Empire' / ('Leader-' + leader.capitalize()) / result['info']['anims'][0]
            original_anim = anim.parse_anim(anim_path.read_bytes())
            with tempfile.TemporaryDirectory(prefix='d3_leader_probe_') as tmp:
                target = os.path.join(tmp, 'probe.gltf')
                assert addon.exporter._export_gltf(
                    bpy.context, target, [arm] + result['meshes'], True, 'probe') == {'FINISHED'}
                m = gltf.load_gltf(target)
                if not m.blender_layout:
                    raise ValueError('Expected Blender glTF layout')
                subs = {part.mesh_name: part for part in [m] + m.submodels}
                for path in result['files']:
                    donor = gfile.parse_geometry_file(Path(path).read_bytes())
                    slots = list(gfile.iter_parts(donor))
                    names = [getattr(p, 'unit_name', '') or p.name for p in slots if p.indices]
                    if not names:
                        continue
                    selected = [subs[name] for name in names]
                    probe = copy.copy(selected[0])
                    probe.nodes = m.nodes
                    probe.anim_channels = m.anim_channels
                    probe.anim_times = m.anim_times
                    probe.blender_layout = True
                    probe.submodels = selected[1:]
                    reconstructed = gltf.mesh_to_skinned(
                        probe, donor=donor, anim_donors=[original_anim], inverse_blend=True)
                    data = gfile.write_geometry_file(reconstructed, reconstructed.attrs)
                    reread = gfile.parse_geometry_file(data)
                    if reread.parse_error:
                        raise ValueError(reread.parse_error)
                    output_slots = list(gfile.iter_parts(reread))
                    if len(slots) != len(output_slots):
                        raise ValueError('Part count changed: ' + path)
                    for original, output in zip(slots, output_slots):
                        if not original.indices:
                            continue
                        if not original.vertices or not output.vertices:
                            raise ValueError('Empty part: ' + path)
                        tree = kdtree.KDTree(len(original.vertices))
                        for index, vertex in enumerate(original.vertices):
                            tree.insert(vertex.position[:3], index)
                        tree.balance()
                        distances = [tree.find(v.position[:3])[2] for v in output.vertices]
                        outside = sum(d > 0.002 for d in distances)
                        if outside:
                            failures.append((leader, kit, os.path.basename(path),
                                             original.name, outside, len(distances), max(distances)))
                    for warning in getattr(reconstructed, 'blender_warnings', []):
                        print('WARNING', leader, kit, os.path.basename(path), warning, flush=True)
            print('PROBED', leader, kit, flush=True)
    print('\nParts with vertices farther than 0.002 from any donor vertex:', flush=True)
    for row in failures:
        print('%s set%s %s %s: %d/%d, max %.6f' % row, flush=True)
    print('Diagnostic only: nearest-position matches are not game validation.', flush=True)


if __name__ == '__main__':
    try:
        run()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    # The standalone bpy wheel can segfault during interpreter shutdown.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
