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
assert addon.bl_info["version"] >= (1, 1, 32), addon.bl_info["version"]
assert addon.unitprops.DIFFUSE_PROP == "d3unit_diffuse"
assert addon.D3UNIT_OT_name_texture in addon.CLASSES
assert addon.D3UNIT_OT_apply_texture in addon.CLASSES
# the sidebar panel is registered by `register()` itself, not through CLASSES
assert addon.D3UNIT_PT_panel.bl_idname == "D3UNIT_PT_panel"


class FakeMesh:
    """Just enough of an object for the texture helpers (no Blender here)."""

    type = "MESH"

    def __init__(self, name, props=None):
        self.name = name
        self._props = dict(props or {})
        self.material_slots = []

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __contains__(self, key):
        return key in self._props

    def __setitem__(self, key, value):
        self._props[key] = value

    def __delitem__(self, key):
        del self._props[key]


class FakeScene:
    objects = []


class FakeContext:
    def __init__(self, ob):
        self.object = ob
        self.active_object = ob
        self.scene = FakeScene()
        self.selected_objects = [ob]
        self.screen = None


# a nameless part resolves to nothing, and saying so must not raise
unnamed = FakeMesh("weapon_empire_inquisitor")
ok, message = addon.apply_part_texture(FakeContext(unnamed), unnamed)
assert ok is False and "no texture name" in message, (ok, message)

# a named part whose file is nowhere: same, but the message names the file
named = FakeMesh("weapon_empire_inquisitor",
                 {addon.unitprops.DIFFUSE_PROP:
                  "weapon_empire_knight_sword_diffuse.tga"})
name, path = addon.find_part_texture(FakeContext(named), named)
assert name == "weapon_empire_knight_sword_diffuse.tga", name
ok, message = addon.apply_part_texture(FakeContext(named), named)
assert ok is False and "not in" in message, (ok, message)

# the export's own texture report: it reads the written .g back and has to name
# every part's texture plus the ones the folder cannot supply
import os
import shutil
import tempfile

repo = os.environ["D3UNIT_REPO"]
work = tempfile.mkdtemp(prefix="d3rep_")
try:
    for name in ("character_empire_inquisitor.g",
                 "character_empire_inquisitor.t"):
        shutil.copyfile(os.path.join(repo, "Empire", "Inquisitor", name),
                        os.path.join(work, name))
    warns = []
    lines = addon.exporter._texture_lines(work, "character_empire_inquisitor",
                                         "", warns)
    joined = " ".join(lines)
    assert "Standard_8" in joined and "character_empire_inquisitor.tga" in joined, lines
    assert "weapon_empire_inquisitor.tga" in joined, lines
    assert any("weapon_empire_inquisitor.tga" in w for w in warns), warns

    # with the atlas gone from the folder the report has to say so, by name
    os.remove(os.path.join(work, "character_empire_inquisitor.t"))
    warns = []
    lines = addon.exporter._texture_lines(work, "character_empire_inquisitor",
                                         "", warns)
    assert any("character_empire_inquisitor.tga" in w and
               "no such texture" in w for w in warns), warns
finally:
    shutil.rmtree(work, ignore_errors=True)
# --------------------------------------------------------------------------- #
#  the material tells the export which atlas the part wears
# --------------------------------------------------------------------------- #
class FakeImage:
    def __init__(self, name, path=""):
        self.name = name
        self.filepath = path


class FakeSocket:
    def __init__(self, name):
        self.name = name


class FakeLink:
    def __init__(self, node, socket_name):
        self.from_node = node
        self.to_socket = FakeSocket(socket_name)


class FakeNode:
    type = "TEX_IMAGE"

    def __init__(self, image):
        self.image = image


class FakeTree:
    def __init__(self, nodes, links):
        self.nodes = nodes
        self.links = links


class FakeMaterial:
    def __init__(self, name, image=None, socket="Base Color"):
        self.name = name
        self.use_nodes = image is not None
        if image is None:
            self.node_tree = FakeTree([], [])
        else:
            node = FakeNode(image)
            self.node_tree = FakeTree([node], [FakeLink(node, socket)])
        self._id = id(self)

    def as_pointer(self):
        return self._id


class FakeSlot:
    def __init__(self, material):
        self.material = material


def mesh_with(*materials):
    ob = FakeMesh("part" + str(len(materials)))
    ob.material_slots = [FakeSlot(m) for m in materials]
    return ob


unitprops = addon.unitprops
# any spelling of a texture file reduces to the `.tga` the attribute block wants
assert unitprops.clean_atlas_name("weapon_x_diffuse.dds.png") == \
    "weapon_x_diffuse.tga"
assert unitprops.clean_atlas_name("//textures/weapon_x_diffuse.dds") == \
    "weapon_x_diffuse.tga"
# ...while a name that is not an atlas at all stays empty: a material called
# "material0" must never be mistaken for the texture it happens to sit on
assert unitprops.clean_atlas_name("material0") == ""
assert unitprops.clean_atlas_name("Material.001") == ""
assert unitprops.clean_atlas_name("leaf") == ""
# ...but a texture the artist painted himself is kept when the file is there:
# the export must not silently fall back to the donor's atlas just because a
# new atlas does not follow the retail naming conventions
_texdir = tempfile.mkdtemp(prefix="d3atlas_")
try:
    open(os.path.join(_texdir, "order_helmet_of_the_order.t"), "wb").close()
    assert unitprops.clean_atlas_name(
        "order_helmet_of_the_order.png", [_texdir]) == \
        "order_helmet_of_the_order.tga"
    assert unitprops.clean_atlas_name("order_helmet_of_the_order.png") == ""
    assert unitprops.clean_atlas_name("Image", [_texdir]) == ""
    assert unitprops.clean_atlas_name("material0", [_texdir]) == ""
finally:
    import shutil as _sh
    _sh.rmtree(_texdir, ignore_errors=True)

# the material's image is the atlas, even though a packed image never reaches
# the glTF as a file URI (which is why the export used to fall back to the
# donor's mace atlas and call the part "nameless")
sword = mesh_with(FakeMaterial("material0.002",
                               FakeImage("weapon_empire_knight_sword_diffuse.dds.png",
                                         "//textures/weapon_empire_knight_sword_diffuse.dds.png")))
assert unitprops.material_texture_name(sword) == \
    "weapon_empire_knight_sword_diffuse.tga", \
    unitprops.material_texture_name(sword)
# an image that only feeds the normal map is the second choice, not the first
body_mat = FakeMaterial("material0",
                        FakeImage("weapon_x_diffuse.png"), socket="Normal")
assert unitprops.material_texture_name(mesh_with(body_mat)) == "weapon_x_diffuse.tga"
# no image, but a material named after the atlas
assert unitprops.material_texture_name(
    mesh_with(FakeMaterial("weapon_empire_knight_sword_diffuse"))) == \
    "weapon_empire_knight_sword_diffuse.tga"
# nothing usable anywhere
assert unitprops.material_texture_name(mesh_with(FakeMaterial("material0"))) == ""
assert unitprops.material_texture_name(FakeMesh("bare")) == ""

# numbering: the body is material0 and the weapon material1, once across the
# whole unit — Blender's own ".001"/".002" suffixes never reach the panel
body = mesh_with(FakeMaterial("material0"))
weapon = mesh_with(FakeMaterial("material0.002"))
rows = unitprops.normalise_material_names([body, weapon])
assert [slots for _n, slots in rows] == [["material0"], ["material1"]], rows
assert unitprops.normalise_material_names([body, weapon]) == rows   # idempotent

# a mesh whose only material already carries the name keeps it, and two parts
# sharing one datablock share one index
shared = FakeMaterial("material0")
rows = unitprops.normalise_material_names([mesh_with(shared), mesh_with(shared)])
assert [slots for _n, slots in rows] == [["material0"], ["material0"]], rows

assert addon.D3UNIT_OT_number_materials in addon.CLASSES

# the name read off the material reaches the glTF as extras, which is the only
# place d3tool.gltf reads it from (a packed image has no file URI to follow)
import json
import tempfile as _tempfile

work = _tempfile.mkdtemp(prefix="d3stamp_")
try:
    target = os.path.join(work, "u.gltf")
    with open(target, "w", encoding="utf-8") as fh:
        json.dump({"nodes": [{"name": "empire_inquisitor", "mesh": 0},
                             {"name": "empire_knight_sword", "mesh": 1}],
                   "meshes": [{"name": "empire_inquisitor"},
                              {"name": "empire_knight_sword"}]}, fh)
    body_ob = FakeMesh("empire_inquisitor")
    sword_ob = FakeMesh("empire_knight_sword", {
        addon.unitprops.DIFFUSE_PROP: "weapon_empire_knight_sword_diffuse.tga"})
    mat_ob = FakeMesh("empire_knight_shield")
    stamped, missing = addon.exporter._stamp_diffuse_names(
        target, [body_ob, sword_ob, mat_ob],
        {"empire_inquisitor": "character_empire_inquisitor.tga"})
    assert (stamped, missing) == (2, []), (stamped, missing)
    doc = json.load(open(target, encoding="utf-8"))
    extras = [m.get("extras", {}) for m in doc["meshes"]]
    assert extras[0].get(addon.unitprops.DIFFUSE_PROP) == \
        "character_empire_inquisitor.tga", extras
    assert extras[1].get(addon.unitprops.DIFFUSE_PROP) == \
        "weapon_empire_knight_sword_diffuse.tga", extras
finally:
    shutil.rmtree(work, ignore_errors=True)

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
        env["PYTHONPATH"] = tmp + os.pathsep + REPO
        env["D3UNIT_REPO"] = REPO
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
