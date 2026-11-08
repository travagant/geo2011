# -*- coding: utf-8 -*-
"""Place an added weapon mesh exactly where the reference weapon sits.

The exported part becomes a rigid, single-bone block whose vertices are
written in **world** space, and the engine adds that bone's motion on top of
them.  So a weapon must be shaped onto the weapon it replaces **in the bind
pose** — posing it into the hand on an animated frame doubles the offset and
the sword drifts away from the palm in game (that is exactly what the first
attempts did).

Two clicks, no manual snapping:

1. In *Edit Mode* select the one vertex on the reference weapon that the hand
   grips, then Tab out.  Same for the new mesh: one vertex, the middle of its
   handle.
2. Press **Place on Reference Weapon** — the new mesh is rotated about its own
   grip until its long axis (a principal-component fit, ``d3tool.weapon``)
   matches the reference weapon's shaft, and moved so the two grip vertices
   coincide.  ``Roll`` spins the blade about that axis afterwards, which is
   the one thing the axis cannot decide.

Everything happens in world space, so object origins and existing parenting do
not matter; with *Same Bone* on, the mesh is re-parented to the bone the
reference weapon hangs from (Blender's bone parenting is compensated by the
exporter, so the placement survives).
"""
from __future__ import annotations

import bpy  # noqa: F401  (Blender only)
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.types import Operator

from . import bridge

#: A weapon has a few hundred vertices; scanning a body mesh is not worth it,
#: and the principal axis of 2000 samples is the same as of 200 000.
MAX_POINTS = 2000


def _points(ob, limit: int = MAX_POINTS):
    """World-space vertices of ``ob``, subsampled to ``limit``."""
    verts = ob.data.vertices
    n = len(verts)
    if not n:
        return []
    step = max(1, n // limit)
    m = ob.matrix_world
    return [list(m @ verts[i].co) for i in range(0, n, step)]


def _grip(ob):
    """The single selected vertex of ``ob`` in world space, and how many."""
    sel = [v for v in ob.data.vertices if v.select]
    if len(sel) != 1:
        return None, len(sel)
    return list(ob.matrix_world @ sel[0].co), 1


def _weapon_candidates(context, src):
    """Meshes in the scene that look like a retail weapon (``weapon_*``)."""
    out = []
    for ob in context.scene.objects:
        if ob.type != "MESH" or ob is src:
            continue
        if ob.name.lower().startswith("weapon_"):
            out.append(ob)
    return out


class D3UNIT_OT_place_weapon(Operator):
    bl_idname = "d3unit.place_weapon"
    bl_label = "Place on Reference Weapon"
    bl_description = ("Move the active mesh onto the reference weapon: its "
                      "grip vertex on the reference's grip vertex, its long "
                      "axis along the reference's shaft")
    bl_options = {"UNDO", "REGISTER"}

    reference: StringProperty(
        name="Reference",
        default="",
        description="Object name of the reference weapon; leave empty to use "
                    "the only weapon_* mesh in the scene",
    )
    roll: FloatProperty(
        name="Roll",
        default=0.0,
        description="Extra spin about the reference axis, in degrees",
    )
    match_parent: BoolProperty(
        name="Same Bone",
        default=True,
        description="Hang the mesh from the bone the reference weapon uses",
    )

    @classmethod
    def poll(cls, context):
        ob = getattr(context, "active_object", None)
        return ob is not None and ob.type == "MESH"

    # -- helpers ---------------------------------------------------------- #
    def _reference(self, context, src):
        if self.reference:
            ref = (context.scene.objects.get(self.reference)
                   or bpy.data.objects.get(self.reference))
            if ref is None:
                self.report({"ERROR"}, f"no object named {self.reference!r}")
                return None
            return ref
        cands = _weapon_candidates(context, src)
        if len(cands) == 1:
            return cands[0]
        if not cands:
            self.report({"ERROR"},
                        "no weapon_* mesh to use as a reference — type its "
                        "name into the Reference field")
            return None
        self.report({"ERROR"},
                    "several candidates (%s) — type the reference name"
                    % ", ".join(o.name for o in cands))
        return None

    def _reparent(self, src_obj, ref_obj):
        if not self.match_parent or ref_obj.parent_type != "BONE":
            return False
        if (src_obj.parent_type, src_obj.parent_bone) == \
                (ref_obj.parent_type, ref_obj.parent_bone):
            return False
        world = src_obj.matrix_world.copy()
        src_obj.parent = ref_obj.parent
        src_obj.parent_type = "BONE"
        src_obj.parent_bone = ref_obj.parent_bone
        src_obj.matrix_parent_inverse.identity()
        src_obj.matrix_world = world
        return True

    # -- operator --------------------------------------------------------- #
    def execute(self, context):
        # the grip vertex is read from the mesh data, which Blender only
        # refreshes when Edit Mode is left
        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                self.report({"ERROR"},
                            "leave Edit Mode first — the selected vertex is "
                            "read from the mesh data")
                return {"CANCELLED"}
        src = context.active_object
        if src is None or src.type != "MESH":
            self.report({"ERROR"}, "activate the mesh you want to place")
            return {"CANCELLED"}
        ref = self._reference(context, src)
        if ref is None:
            return {"CANCELLED"}
        if ref.type != "MESH":
            self.report({"ERROR"}, "the reference must be a mesh")
            return {"CANCELLED"}

        src_grip, n_src = _grip(src)
        ref_grip, n_ref = _grip(ref)
        if src_grip is None:
            self.report({"ERROR"},
                        f"{src.name}: select exactly one vertex — the point "
                        f"the hand grips it by (selected: {n_src})")
            return {"CANCELLED"}
        if ref_grip is None:
            self.report({"ERROR"},
                        f"{ref.name}: select exactly one vertex as its grip "
                        f"point (selected: {n_ref})")
            return {"CANCELLED"}

        try:
            wmod = bridge.module("weapon")
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        mat, info = wmod.align_matrix(_points(ref), ref_grip,
                                      _points(src), src_grip,
                                      float(self.roll))
        if not info.get("axis_found"):
            self.report({"WARNING"},
                        "no long axis could be measured for one of the "
                        "meshes — only translated the grip")

        from mathutils import Matrix
        src.matrix_world = Matrix([list(row) for row in mat]) @ src.matrix_world
        moved = self._reparent(src, ref)
        context.view_layer.update()

        arm = src.find_armature()
        posed = arm is not None and arm.data.pose_position == "POSE"
        self.report({"INFO"},
                    f"{src.name} onto {ref.name}: axes {info['angle_deg']:.1f}°"
                    f" apart" + (f", re-parented to {ref.parent_bone!r}"
                                 if moved else "")
                    + (" — armature is posed, set Pose Position: Rest for a "
                       "bind-pose placement" if posed else ""))
        return {"FINISHED"}
