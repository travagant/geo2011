"""Real bpy operator test; uses only temporary copies of source profiles."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'blender')]
import bpy
import io_scene_d3unit as addon

try:
    addon.register()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        profiles = root / 'source' / 'Profiles'
        profiles.mkdir(parents=True)
        donor = 'empire_leader-warrior'
        for path in [donor + '.char'] + [f'{k}/{donor}_{k}.char' for k in ('desc', 'stats', 'sound')] + [f'scripts/{k}_empire.s' for k in ('unit', 'town')]:
            target = profiles / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / 'Profiles' / path, target)
        workspace = root / 'workspace'
        workspace.mkdir()
        scene = bpy.context.scene
        scene.d3unit_profile_donor = str(profiles / (donor + '.char'))
        scene.d3unit_profile_workspace = str(workspace)
        scene.d3unit_profile_id = 'arbitr'
        scene.d3unit_profile_warrior_no_sets = True
        assert bpy.ops.d3unit.stage_profile() == {'FINISHED'}
        from d3tool import char
        profile = char.parse_char((workspace / 'arbitr/resources/Profiles/arbitr.char').read_bytes())
        assert not profile.blocks and profile.unit_scene.endswith('arbitr_model.scene')
        assert 'leader-warrior' in profile.unit_scene_lod
        assert bpy.ops.d3unit.stage_empire_lists(leader=True) == {'FINISHED'}
        for name in ('unit_empire.s', 'town_empire.s'):
            data = (workspace / 'arbitr/resources/Profiles/scripts' / name).read_bytes()
            assert b'leader "arbitr";' in data and b'unit "arbitr";' not in data
        assert 'Тип записи: leader' in bpy.data.texts[addon.new_unit_ui.REPORT].as_string()
    addon.unregister()
    print('PASS: leader list staging operator (not a game test)', flush=True)
except BaseException:
    traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
