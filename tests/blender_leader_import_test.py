"""Run with bpy: assembled warrior kits, animation, safe refusal, missing input."""
import os
import sys
import tempfile
import traceback
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / 'blender'))
import bpy
import io_scene_d3unit as addon


def main():
    addon.register()
    for leader in ('warrior', 'ranger', 'thief', 'archmage'):
        profile = repo / ('Profiles/empire_leader-' + leader + '.char')
        for kit in ('1', '2', '3'):
            bpy.ops.wm.read_factory_settings(use_empty=True)
            result = addon.leader.import_leader(bpy.context, str(profile), kit)
            arm = result['armature']
            expected = {'warrior': 8, 'ranger': 9, 'thief': 7, 'archmage': 9}[leader]
            assert len(result['meshes']) == expected and len(result['armatures']) == 1
            assert arm.animation_data and arm.animation_data.action
            assert all(any(m.type == 'ARMATURE' and m.object == arm for m in ob.modifiers)
                       for ob in result['meshes'])
            assert all(ob.get(addon.unitprops.OWNER_PROP) == result['info']['base'] for ob in result['meshes'])
            assert not result['missing_textures'] and result['info']['textures'] >= 7
            assert all(img.packed_file for img in bpy.data.images if img.source == 'FILE')
            bpy.context.view_layer.objects.active = arm
            with tempfile.TemporaryDirectory() as tmp:
                target = os.path.join(tmp, 'must_not_exist', 'unit.g')
                try:
                    addon.exporter.export_unit(bpy.context, target)
                    raise AssertionError('reference export must be blocked')
                except ValueError as exc:
                    assert 'лидера' in str(exc)
                assert not os.path.exists(os.path.dirname(target))
            print(leader, kit, 'PASS', flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        from shutil import copyfile
        os.mkdir(os.path.join(tmp, 'Profiles'))
        partial = os.path.join(tmp, 'Profiles', 'empire_leader-warrior.char')
        copyfile(repo / 'Profiles/empire_leader-warrior.char', partial)
        before = len(bpy.data.objects)
        try:
            addon.leader.import_leader(bpy.context, partial, '1')
            raise AssertionError('missing resources must fail')
        except (FileNotFoundError, ValueError):
            pass
        assert len(bpy.data.objects) == before
    print('leader import PASS', flush=True)

try:
    main()
except BaseException:
    traceback.print_exc()
    os._exit(1)
os._exit(0)
