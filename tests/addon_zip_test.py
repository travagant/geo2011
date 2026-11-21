#!/usr/bin/env python3
"""End-to-end check of the *installed* add-on zip.

    python3 tests/addon_zip_test.py            # needs bpy (Blender or `pip install bpy`)

`release/build_addon.py` packages the add-on together with a vendored copy of
`d3tool`.  That package is what a user actually installs, and it is the only
place where the `vendor/` lookup in `bridge.py` is exercised — running from
the git tree always finds `../../d3tool` first.  So this test

1. builds the zip into a temporary folder,
2. unpacks it somewhere outside the checkout,
3. runs a *separate* Blender process that puts only the unpacked folder on
   ``sys.path`` (the checkout is hidden), imports a unit, exports it again and
   re-reads the result with the vendored `d3tool`.

Exit code 0 means an installed zip works on a machine that has nothing but
Blender.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: imported by the child process — no reference to the checkout at all
CHILD = r'''
import os, sys
sys.path.insert(0, sys.argv[1])                 # the unpacked add-on folder
sys.path[:] = [p for p in sys.path if p not in ("", ".", os.getcwd())]
import io_scene_d3unit as addon
assert "/vendor/" in addon.bridge.load(force=True).__file__, "not the vendored copy"
addon.register()
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
unit = sys.argv[2]
out_dir = sys.argv[3]
os.makedirs(out_dir, exist_ok=True)
base = os.path.splitext(os.path.basename(unit))[0]
res = bpy.ops.d3unit.import_unit(filepath=unit)
assert res == {"FINISHED"}, res
res = bpy.ops.d3unit.export_unit(filepath=os.path.join(out_dir, base + ".g"))
assert res == {"FINISHED"}, res
log = bpy.data.texts[addon.EXPORT_LOG].as_string()
assert 'Папка экспорта: ' + out_dir in log
assert 'Примечания:' in log and 'Предупреждения:' in log
assert '.t в папке экспорта:' in log or '.t только у донора:' in log or '.t в проверенных папках не найден' in log
from d3tool import gfile
mesh = gfile.parse_geometry_file(open(os.path.join(out_dir, base + ".g"), "rb").read())
assert mesh.parse_error is None, mesh.parse_error
want = [n for n in sys.argv[4].split(",") if n]
got = [p.name for p in mesh.parts]
assert got == want, "part order changed: %r != %r" % (got, want)
scene = bpy.context.scene
# A first failed attempt still creates a report, even without a project.
try:
    assert bpy.ops.d3unit.stage_profile() == {'CANCELLED'}
except RuntimeError:
    pass
assert 'Ошибка создания проекта' in bpy.data.texts[addon.new_unit_ui.REPORT].as_string()
assert 'Ошибка создания проекта' in scene['d3unit_profile_status']
scene.d3unit_profile_donor = sys.argv[5]
scene.d3unit_profile_workspace = out_dir
scene.d3unit_profile_id = 'empire_zip_test'
scene.d3unit_profile_copy_grid = True
assert bpy.ops.d3unit.stage_profile() == {'FINISHED'}
profile_path = os.path.join(out_dir, 'empire_zip_test', 'resources', 'Profiles', 'empire_zip_test.char')
from d3tool import char
assert char.parse_char(open(profile_path, 'rb').read()).id == 'empire_zip_test'
stats_path = os.path.join(out_dir, 'empire_zip_test', 'resources', 'Profiles', 'stats', 'empire_zip_test_stats.char')
grid_path = os.path.join(out_dir, 'empire_zip_test', 'resources', 'Profiles', 'skills', 'empire_zip_test.grid')
assert char.parse_char(open(stats_path, 'rb').read()).get('skill_grid') == 'empire_zip_test'
assert b'grid "empire_zip_test"' in open(grid_path, 'rb').read()
assert bpy.ops.d3unit.stage_empire_lists() == {'FINISHED'}
for script in ('unit_empire.s', 'town_empire.s'):
    staged = os.path.join(out_dir, 'empire_zip_test', 'resources', 'Profiles', 'scripts', script)
    assert b'unit "empire_zip_test";' in open(staged, 'rb').read()
report = bpy.data.texts[addon.new_unit_ui.REPORT].as_string()
assert 'Проект профиля создан' in report and 'Списки Empire подготовлены' in report
# Repeat staging to force an error; the previous project must remain intact.
before = open(profile_path, 'rb').read()
try:
    assert bpy.ops.d3unit.stage_profile() == {'CANCELLED'}
except RuntimeError:
    pass
assert open(profile_path, 'rb').read() == before
report = bpy.data.texts[addon.new_unit_ui.REPORT].as_string()
assert 'Проект профиля создан' in report and 'Ошибка создания проекта' in report
assert 'Ошибка создания проекта' in scene['d3unit_profile_status']
assert scene['d3unit_profile_result'] == os.path.join(out_dir, 'empire_zip_test')
scene.d3unit_profile_id = 'different_project'
try:
    assert bpy.ops.d3unit.stage_empire_lists() == {'CANCELLED'}
except RuntimeError:
    pass
report = bpy.data.texts[addon.new_unit_ui.REPORT].as_string()
assert 'Ошибка подготовки списков Empire' in report and 'ID: different_project' in report
assert 'Ошибка подготовки списков' in scene['d3unit_profile_status']
assert scene['d3unit_profile_result'] == os.path.join(out_dir, 'empire_zip_test')
print("ZIPOK parts=%d root_verts=%d; profile and lists staging ok" % (len(got) + 1, mesh.vertex_count))
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)   # bpy segfaults in its exit handlers under a plain CPython
'''


def main() -> int:
    unit = os.path.join(REPO, "Empire", "Hierophant",
                        "character_empire_hierophant.g")
    if not os.path.isfile(unit):
        print("SKIP: sample unit missing")
        return 0
    # what the donor file contains, straight from the checkout's d3tool
    sys.path.insert(0, REPO)
    from d3tool import gfile
    donor = gfile.parse_geometry_file(open(unit, "rb").read())
    want = ",".join(p.name for p in donor.parts)

    with tempfile.TemporaryDirectory(prefix="d3unit_zip_") as tmp:
        dist = os.path.join(tmp, "dist")
        rc = subprocess.call([sys.executable,
                              os.path.join(REPO, "release", "build_addon.py"),
                              "-o", dist, "--zip-only"])
        if rc:
            print("FAIL: build_addon.py exited %d" % rc)
            return 1
        zpath = os.path.join(dist, "io_scene_d3unit.zip")
        unpacked = os.path.join(tmp, "installed")
        with zipfile.ZipFile(zpath) as zf:
            assert zf.read('io_scene_d3unit/GUIDE_RU.md') == open(
                os.path.join(REPO, 'docs', 'BLENDER_ADDON_GUIDE_RU.md'), 'rb').read()
            zf.extractall(unpacked)
        assert os.path.isfile(os.path.join(unpacked, "io_scene_d3unit",
                                           "vendor", "d3tool", "__init__.py")), \
            "the zip carries no vendored d3tool"
        out = os.path.join(tmp, "out")
        # run in the temp folder so the checkout is not on sys.path[0]
        donor_profile = os.path.join(REPO, 'Profiles', 'empire_inquisitor.char')
        proc = subprocess.run([sys.executable, "-c", CHILD, unpacked, unit, out,
                               want, donor_profile], cwd=tmp, capture_output=True, text=True)
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
        for line in tail:
            print("   " + line)
        if proc.returncode:
            print("FAIL: the installed add-on could not do a round trip")
            return 1
        if not any("ZIPOK" in line for line in tail):
            print("FAIL: child produced no result line")
            return 1
        shutil.rmtree(unpacked, ignore_errors=True)
    print("PASS: installed zip imports and exports on its own")
    return 0


if __name__ == "__main__":
    sys.exit(main())
