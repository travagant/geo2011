"""Exercise experimental visual staging operator with a private .blend."""
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
from d3tool import audit, gfile


def run(path):
    bpy.context.preferences.filepaths.use_scripts_auto_execute = False
    bpy.ops.wm.open_mainfile(filepath=str(Path(path).resolve()), load_ui=False)
    addon.register()
    rigs = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    assert len(rigs) == 1 and len(meshes) == 2
    for ob in bpy.context.view_layer.objects:
        ob.select_set(False)
    for ob in rigs + meshes:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    context = bpy.context
    sc = context.scene
    sc.frame_set(100)
    fps = sc.render.fps, sc.render.fps_base
    original_selection = {o.name for o in context.selected_objects}
    original_active = context.view_layer.objects.active.name
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        donor_dir = root / 'donor'
        donor_dir.mkdir()
        for suffix in ('.scene', '.ac', '_baseanims.a'):
            name = 'character_empire_leader-warrior' + suffix
            uploaded = ROOT / 'google_drive' / name
            source = uploaded if uploaded.exists() else ROOT / 'Empire/Leader-Warrior' / name
            shutil.copyfile(source, donor_dir / name)
        sc.d3unit_visual_scene = str(donor_dir / 'character_empire_leader-warrior.scene')
        sc.d3unit_profile_workspace = tmp
        sc.d3unit_profile_id = 'arbitr'
        assert bpy.ops.d3unit.stage_leader_visual() == {'FINISHED'}
        dest = root / 'arbitr_visual_experiment'
        directory = dest / 'resources/characters/empire/arbitr'
        geom = gfile.parse_geometry_file((directory / 'arbitr_model.g').read_bytes())
        assert not geom.parse_error and not gfile.count_mismatches(geom)
        nodes = audit.scene_gobjs(str(directory / 'arbitr_model.scene'))
        assert len(nodes) == 2 and [n.index for n in nodes] == [0, 1]
        assert list(directory.glob('*.t')) and (dest / 'EXPERIMENT.json').exists()
        assert not list(dest.rglob('*.char')) and not list(dest.rglob('*.vdpack'))
        assert (sc.render.fps, sc.render.fps_base) == fps and sc.frame_current == 100
        assert {o.name for o in context.selected_objects} == original_selection
        assert context.view_layer.objects.active.name == original_active
        assert 'НЕ УСТАНАВЛИВАТЬ' in bpy.data.texts[addon.new_unit_ui.REPORT].as_string()
        profiles = root / 'source' / 'Profiles'
        profiles.mkdir(parents=True)
        donor_id = 'empire_leader-warrior'
        for rel in [donor_id + '.char'] + [f'{k}/{donor_id}_{k}.char' for k in ('desc', 'stats', 'sound')] + [f'scripts/{k}_empire.s' for k in ('unit', 'town')]:
            target = profiles / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / 'Profiles' / rel, target)
        sc.d3unit_profile_donor = str(profiles / (donor_id + '.char'))
        sc.d3unit_profile_warrior_no_sets = True
        assert bpy.ops.d3unit.stage_profile() == {'FINISHED'}
        assert bpy.ops.d3unit.stage_empire_lists(leader=True) == {'FINISHED'}
        from d3tool import leader_icon_probe
        from test_leader_icon_image import png
        icons = root / 'png_icons'
        icons.mkdir()
        for key, (_, width, height, _) in leader_icon_probe._SLOTS.items():
            (icons / (key + '.png')).write_bytes(png(width, height, (255, 0, 0, 255)))
        sc.d3unit_leader_icons = str(icons)
        assert bpy.ops.d3unit.bundle_leader_test() == {'FINISHED'}
        from d3tool import vdpack, char
        pack = vdpack.parse_vdpack((root / 'arbitr_test_bundle/arbitr_test.vdpack').read_bytes())
        assert not pack.parse_error and len(pack.entries) == 16
        check = vdpack.index_report(pack.files())
        assert check['decoded'] and not (check['missing'] or check['mismatched'] or check['unlisted'])
        profile = char.parse_char(pack.payload('resources/Profiles/arbitr.char'))
        assert not profile.blocks and profile.unit_scene.endswith('arbitr_model.scene')
        assert profile.get('icon').endswith('icon_arbitr.t')
        assert pack.payload('resources/characters/empire/arbitr/arbitr_model.g') == (directory / 'arbitr_model.g').read_bytes()

        old = (directory / 'arbitr_model.g').read_bytes()
        try:
            addon.new_leader_ui.stage_selected(context, sc.d3unit_visual_scene, tmp, 'arbitr')
            raise AssertionError('existing project accepted')
        except ValueError as exc:
            assert 'already exists' in str(exc)
        assert (directory / 'arbitr_model.g').read_bytes() == old
    addon.unregister()
    print('PASS: Blender experimental visual operator; not a game test', flush=True)


try:
    path = os.environ.get('D3_LEADER_BLEND')
    if path:
        run(path)
    else:
        print('SKIP: set D3_LEADER_BLEND', flush=True)
except BaseException:
    traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
