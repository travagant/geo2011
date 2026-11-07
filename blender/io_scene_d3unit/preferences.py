# -*- coding: utf-8 -*-
"""Add-on preferences: where ``d3tool`` lives and how exports behave."""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import AddonPreferences

from . import bridge


WEIGHTS_ITEMS = [
    ("AUTO", "Auto (from the scene)",
     "Keep every influence the mesh actually uses (2, 3 or 4 slots)"),
    ("2", "2 influences", "Smaller vertex records"),
    ("3", "3 influences", "The most common Disciples 3 character layout"),
    ("4", "4 influences", "Widest records, needed for heavy remapping"),
]


class D3UNIT_PT_preferences(AddonPreferences):
    bl_idname = __package__

    d3tool_path: StringProperty(
        name="d3tool path",
        subtype="DIR_PATH",
        default="",
        description="Folder that contains the d3tool package (leave empty to "
                    "use the bundled copy)",
    )
    weights_on_vertex: EnumProperty(
        name="Influence slots",
        items=WEIGHTS_ITEMS,
        default="AUTO",
        description="How many bone influences the exported .g vertex records "
                    "carry",
    )
    pack_textures: BoolProperty(
        name="Pack textures into the .blend",
        default=True,
        description="Embed the imported .dds textures so the .blend stays "
                    "self-contained after the temporary import folder is "
                    "removed",
    )
    keep_temp: BoolProperty(
        name="Keep the temporary glTF",
        default=False,
        description="Leave the intermediate glTF files on disk for debugging",
    )

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.prop(self, "d3tool_path")
        row = col.row(align=True)
        row.operator("d3unit.check_tool", text="Check d3tool")
        col.separator()
        col.prop(self, "weights_on_vertex")
        col.prop(self, "pack_textures")
        col.prop(self, "keep_temp")


def get_prefs(context=None):
    """The preferences object, or ``None`` when the add-on is not registered."""
    context = context or bpy.context
    try:
        addon = context.preferences.addons.get(__package__)
    except AttributeError:
        return None
    return getattr(addon, "preferences", None)


def d3tool_path(context=None) -> str:
    prefs = get_prefs(context)
    return getattr(prefs, "d3tool_path", "") if prefs else ""


def weights(context=None) -> int:
    """The ``weights_on_vertex`` value for ``d3tool`` (0 = auto)."""
    prefs = get_prefs(context)
    raw = getattr(prefs, "weights_on_vertex", "AUTO") if prefs else "AUTO"
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


class D3UNIT_OT_check_tool(bpy.types.Operator):
    bl_idname = "d3unit.check_tool"
    bl_label = "Check d3tool"
    bl_description = "Verify which d3tool build the add-on will use"

    def execute(self, context):
        mod = bridge.load(d3tool_path(context), force=True)
        if mod is None:
            self.report({"ERROR"},
                        "d3tool not found — set the path in the add-on "
                        "preferences")
            return {"CANCELLED"}
        where = bridge.loaded_from()
        self.report({"INFO"}, f"d3tool {getattr(mod, '__version__', '?')} "
                              f"loaded from {where}")
        print(f"[d3unit] {bridge.version_hint()}")
        return {"FINISHED"}
