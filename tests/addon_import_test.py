#!/usr/bin/env python3
"""Import the Blender add-on without Blender, against a stub ``bpy``.

    python3 tests/addon_import_test.py

The add-on is only ever exercised for real inside Blender (`tests/
addon_zip_test.py` runs there), so a plain typo at module scope — a class the
``CLASSES`` tuple uses before it is defined, a property named wrong in a panel
draw, a missing import — survives every test and only shows up as "the add-on
did not register" on the user's side.  This walks the package with a stub
``bpy``/``bpy_extras`` that answers every attribute, so module-level code runs
and class bodies are executed, then checks the registration list.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STUB = {
    "bpy/__init__.py": '''from . import props, types, utils, ops, data, app, path, context


def register_class(cls):
    pass


def unregister_class(cls):
    pass
''',
    "bpy/_any.py": '''class Any:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, item):
        return Any()

    def __call__(self, *a, **k):
        return Any()

    def __iter__(self):
        return iter(())

    def __getitem__(self, item):
        return Any()

    def __setitem__(self, key, value):
        pass
''',
    "bpy/types.py": '''from ._any import Any


class Operator(Any):
    pass


class Panel(Any):
    pass


class PropertyGroup(Any):
    pass


class OperatorFileListElement(Any):
    pass


class AddonPreferences(Any):
    pass


class Mesh(Any):
    pass


class Object(Any):
    pass


class Scene(Any):
    pass


class UIList(Any):
    pass
''',
    "bpy_extras/__init__.py": "",
    "bpy_extras/io_utils.py": '''class ExportHelper:
    pass


class ImportHelper:
    pass
''',
}

CHILD = r'''
import importlib
import sys

import bpy
from bpy import props, types, utils  # noqa: F401  (the stub's own modules)

for name in ("unitprops", "weapon", "bridge", "preferences", "exporter",
             "importer"):
    importlib.import_module("blender.io_scene_d3unit." + name)
addon = importlib.import_module("blender.io_scene_d3unit")
assert addon.bl_info["version"] >= (1, 1, 27), addon.bl_info["version"]
assert addon.unitprops.DIFFUSE_PROP == "d3unit_diffuse"
assert addon.D3UNIT_OT_name_texture in addon.CLASSES
# the sidebar panel is registered by `register()` itself, not through CLASSES
assert addon.D3UNIT_PT_panel.bl_idname == "D3UNIT_PT_panel"
print("add-on imports and registers", addon.bl_info["version"])
'''


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="d3addon_")
    try:
        for rel in ("bpy", "bpy_extras"):
            os.makedirs(os.path.join(tmp, rel), exist_ok=True)
        for name, body in STUB.items():
            path = os.path.join(tmp, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        for name in ("props.py", "utils.py", "ops.py", "data.py", "app.py",
                     "path.py", "context.py"):
            with open(os.path.join(tmp, "bpy", name), "w",
                      encoding="utf-8") as fh:
                if name == "props.py":
                    fh.write("from ._any import Any\n\n\n"
                             "def __getattr__(name):\n    return Any\n")
                elif name == "app.py":
                    fh.write("from ._any import Any\n\nversion = (3, 6, 0)\n\n\n"
                             "def __getattr__(name):\n    return Any\n")
                else:
                    fh.write("from ._any import Any\n\n\n"
                             "def __getattr__(name):\n    return Any\n")
        shutil.copytree(os.path.join(REPO, "blender"), os.path.join(tmp, "blender"))
        env = dict(os.environ)
        env["PYTHONPATH"] = tmp
        env.pop("PYTHONPATH_EXTRA", None)
        res = subprocess.run([sys.executable, "-c", CHILD], cwd=tmp, env=env,
                             capture_output=True, text=True)
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        if res.returncode != 0:
            print("FAILED: the add-on package does not import cleanly")
            return res.returncode
        print("PASS addon import (stub bpy)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
