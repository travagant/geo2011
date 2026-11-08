# -*- coding: utf-8 -*-
"""Disciples III unit I/O for Blender (GM engine: .g/.a/.scene/.ac/.t).

Install: ``Edit > Preferences > Add-ons > Install…`` and pick the add-on
folder or the ``disciples3_io.zip`` produced by ``release/build_addon.py``.

Usage
-----
*Import*: ``File > Import > Disciples III Unit`` and pick the unit's ``.g``.
The add-on converts the bundle to a glTF with ``d3tool`` and imports it:
one armature, one object per mesh part (body, cloth, wings, weapon…), the
full concatenated animation at 30 fps, and the texture atlas as a material.

*Edit*: shape the mesh, repaint weights, retime the action, add objects and
skin them to the armature — anything Blender can do.

*Export*: ``File > Export > Disciples III Unit`` (or the sidebar panel).
The scene is written as a glTF and converted back by ``d3tool`` using the
original bundle as a donor, so the engine-visible parts a glTF cannot carry
(``.ac`` event/sound table, ``.scene`` particle emitters, ``.a`` record
scaffolding, per-vertex diffuse, light-map UVs) survive untouched.

The format code lives in :mod:`d3tool` (pure Python, no dependencies); this
package only builds and reads the Blender scene.
"""
bl_info = {
    "name": "Disciples III Unit (GM .g/.a/.scene/.ac/.t)",
    "author": "Arena agent / geo2011",
    "version": (1, 1, 27),
    "blender": (3, 0, 0),
    "location": "File > Import/Export > Disciples III Unit",
    "description": "Import and export Disciples III character units "
                   "(geometry, skinning, animation, scene, textures)",
    "category": "Import-Export",
    "doc_url": "https://github.com/travagant/geo2011",
    "tracker_url": "https://github.com/travagant/geo2011/issues",
}

import os  # noqa: E402  (kept for the panels)

import bpy  # noqa: E402
from bpy.props import (BoolProperty, FloatProperty,  # noqa: E402
                       PointerProperty, StringProperty)
from bpy.types import Panel  # noqa: E402

from . import (bridge, exporter, importer, preferences,  # noqa: E402
               unitprops, weapon)

class D3UNIT_OT_name_texture(bpy.types.Operator):
    """Name the texture this part wears in the game"""

    bl_idname = "d3unit.name_texture"
    bl_label = "Name the Texture…"
    bl_description = ("The name is written into the part's block in the .g, "
                      "the same block of the _lod.g and the gobj of both "
                      ".scene files on export")
    bl_options = {"REGISTER", "UNDO"}

    value: StringProperty(
        name="Texture",
        description="Material file name, e.g. weapon_empire_knight_sword_"
                    "diffuse.tga (the .t next to the unit is matched by stem)",
        default="")  # type: ignore[valid-type]

    def invoke(self, context, event):
        ob = getattr(context, "object", None)
        if ob is not None and ob.type == "MESH":
            self.value = str(ob.get(unitprops.DIFFUSE_PROP) or "")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        ob = getattr(context, "object", None)
        if ob is None or ob.type != "MESH":
            self.report({"ERROR"}, "select the mesh part first")
            return {"CANCELLED"}
        name = self.value.strip()
        try:
            if name:
                ob[unitprops.DIFFUSE_PROP] = name
            elif unitprops.DIFFUSE_PROP in ob:
                del ob[unitprops.DIFFUSE_PROP]
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"could not store the name: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"{ob.name}: {name or '(none)'}")
        return {"FINISHED"}


CLASSES = (
    preferences.D3UNIT_PT_preferences,
    preferences.D3UNIT_OT_check_tool,
    importer.D3UNIT_OT_import,
    importer.D3UNIT_OT_import_batch,
    exporter.D3UNIT_OT_export,
    weapon.D3UNIT_OT_place_weapon,
    D3UNIT_OT_name_texture,
)


# --------------------------------------------------------------------------- #
#  menu entries
# --------------------------------------------------------------------------- #
def _menu_import(self, context):
    self.layout.operator(importer.D3UNIT_OT_import.bl_idname,
                         text="Disciples III Unit (.g)")


def _menu_export(self, context):
    self.layout.operator(exporter.D3UNIT_OT_export.bl_idname,
                         text="Disciples III Unit (.g)")


# --------------------------------------------------------------------------- #
#  sidebar panel
# --------------------------------------------------------------------------- #
class D3UNIT_PT_panel(Panel):
    bl_idname = "D3UNIT_PT_panel"
    bl_label = "Disciples III Unit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Disciples III"

    def draw(self, context):
        layout = self.layout
        # weapon placement is a plain scene operation: it works on a unit that
        # was imported by this add-on and on a hand-assembled test scene alike
        ob = getattr(context, "active_object", None)
        scene = context.scene
        box = layout.box()
        box.label(text="Weapon placement", icon="SNAP_ON")
        col = box.column(align=True)
        col.prop(scene, "d3unit_weapon_src", text="Mesh")
        col.prop(scene, "d3unit_weapon_ref", text="Reference")
        opts = col.row(align=True)
        opts.prop(scene, "d3unit_weapon_roll", text="Roll")
        opts.prop(scene, "d3unit_weapon_flip", text="Flip")
        src = scene.d3unit_weapon_src
        if src is None and ob is not None and ob.type == "MESH":
            src = ob
        row = col.row(align=True)
        row.enabled = src is not None
        op = row.operator(weapon.D3UNIT_OT_place_weapon.bl_idname,
                          text="Place on Reference", icon="SNAP_ON")
        op.roll = scene.d3unit_weapon_roll
        op.flip = scene.d3unit_weapon_flip
        op.source = scene.d3unit_weapon_src.name if scene.d3unit_weapon_src else ""
        hint = col.column(align=True)
        hint.scale_y = 0.8
        if src is not None:
            hint.label(text=f"Mesh: {src.name}", icon="MESH_DATA")
        else:
            hint.label(text="pick the mesh to place", icon="INFO")
        hint.label(text="grip = the object's origin")
        hint.label(text="(or its one selected vertex)")

        root = unitprops.find_unit_root(context)
        info = unitprops.get_info(root) if root else {}
        if not info:
            layout.label(text="No unit imported yet", icon="INFO")
            col = layout.column(align=True)
            col.operator(importer.D3UNIT_OT_import.bl_idname,
                         text="Import Unit…", icon="IMPORT")
            return
        box = layout.box()
        box.label(text=info.get("base", "?"),
                  icon="ARMATURE_DATA" if root and root.type == "ARMATURE"
                  else "MESH_DATA")
        col = box.column(align=True)
        col.label(text=f"Folder: {info.get('dir', '?')}")
        anims = info.get("anims") or []
        col.label(text=f"Animation streams: {len(anims)}")
        if info.get("imported"):
            col.label(text=f"Imported: {info['imported']}")
        meshes = [o for o in context.scene.objects
                  if o.type == "MESH" and (o is root or o.parent is root
                                           or o.get(unitprops.PART_PROP))]
        col.label(text=f"Meshes: {len(meshes)}")
        # a part's material name: the one thing about a re-textured part that
        # Blender cannot carry by itself, because the engine reads the name
        # out of the `.scene`, not out of the material
        mesh = ob if (ob is not None and ob.type == "MESH") else None
        if mesh is not None:
            tb = layout.box()
            tb.label(text=f"Texture of {mesh.name}", icon="TEXTURE")
            row = tb.row(align=True)
            if unitprops.DIFFUSE_PROP in mesh:
                row.prop(mesh, '["' + unitprops.DIFFUSE_PROP + '"]',
                         text="Material")
            else:
                row.label(text="(inherited from the donor .g)")
            op = row.operator("d3unit.name_texture", text="", icon="GREASEPENCIL")
            op.value = str(mesh.get(unitprops.DIFFUSE_PROP) or "")
            if str(mesh.get(unitprops.DIFFUSE_PROP) or "").lower().endswith(
                    ".tga"):
                hint = tb.column(align=True)
                hint.scale_y = 0.8
                hint.label(text="file next to the unit: "
                                + os.path.splitext(str(mesh.get(
                                    unitprops.DIFFUSE_PROP)))[0] + ".t")
        op = layout.operator(exporter.D3UNIT_OT_export.bl_idname,
                             text="Export Unit", icon="EXPORT")
        op.filepath = info.get("g", "")
        row = layout.row(align=True)
        row.operator(importer.D3UNIT_OT_import.bl_idname, text="Re-import")
        layout.operator("d3unit.check_tool", text="Check d3tool")


# --------------------------------------------------------------------------- #
#  registration
# --------------------------------------------------------------------------- #
def register():
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            # a reloaded add-on hits already-registered classes
            bpy.utils.unregister_class(cls)
            bpy.utils.register_class(cls)
    try:
        bpy.utils.register_class(D3UNIT_PT_panel)
    except ValueError:
        bpy.utils.unregister_class(D3UNIT_PT_panel)
        bpy.utils.register_class(D3UNIT_PT_panel)

    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)

    # the weapon-placement panel remembers its reference across re-registers
    for prop in ("d3unit_weapon_src", "d3unit_weapon_ref",
                 "d3unit_weapon_roll", "d3unit_weapon_flip"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
    bpy.types.Scene.d3unit_weapon_src = PointerProperty(
        type=bpy.types.Object,
        poll=lambda self, ob: ob.type == "MESH",
        name="Mesh",
        description="Mesh to place on the reference weapon (empty = the "
                    "selected mesh)")
    bpy.types.Scene.d3unit_weapon_ref = PointerProperty(
        type=bpy.types.Object,
        poll=lambda self, ob: ob.type == "MESH",
        name="Reference weapon",
        description="Mesh whose grip and long axis the placed mesh is aligned "
                    "to; empty = the only weapon-like mesh in the scene")
    bpy.types.Scene.d3unit_weapon_roll = FloatProperty(
        name="Roll",
        default=0.0,
        description="Extra spin about the reference axis, in degrees")
    bpy.types.Scene.d3unit_weapon_flip = BoolProperty(
        name="Flip",
        default=False,
        description="Turn the mesh half a circle about a perpendicular — the "
                    "answer to a blade pointing the wrong way")

    # a cheap self-check, visible in the console at start-up
    mod = bridge.load()
    print(f"[d3unit] add-on registered; {bridge.version_hint()}"
          if mod is not None else
          "[d3unit] add-on registered; d3tool NOT found (set its path in the "
          "add-on preferences)")


def unregister():
    for funcs, menu in ((_menu_import, bpy.types.TOPBAR_MT_file_import),
                        (_menu_export, bpy.types.TOPBAR_MT_file_export)):
        try:
            menu.remove(funcs)
        except (ValueError, AttributeError):
            pass
    for cls in (D3UNIT_PT_panel,) + tuple(reversed(CLASSES)):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
    for prop in ("d3unit_weapon_src", "d3unit_weapon_ref",
                 "d3unit_weapon_roll", "d3unit_weapon_flip"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)


if __name__ == "__main__":
    register()
