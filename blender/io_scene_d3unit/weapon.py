# -*- coding: utf-8 -*-
"""Place an added weapon mesh exactly where the reference weapon sits.

The exported part becomes a rigid, single-bone block whose vertices are
written in **world** space, and the engine adds that bone's motion on top of
them.  So a weapon must be shaped onto the weapon it replaces **in the bind
pose** — posing it into the hand on an animated frame doubles the offset and
the sword drifts away from the palm in game (that is exactly what the first
attempts did).

One click, no manual snapping:

1. Pick the reference weapon in the panel's *Reference* field (an object
   picker — the eyedropper works), or leave it empty when the scene has only
   one weapon-like mesh.
2. Press **Place Active Mesh on Reference**: the new mesh is rotated about its
   own grip until its long axis (a principal-component fit, ``d3tool.weapon``)
   matches the reference weapon's shaft, and moved so the two grips coincide.
   ``Roll`` spins the blade about that axis afterwards, which is the one thing
   the axis cannot decide.

The grip is the **object origin** — retail weapons are modelled that way and
*Object > Set Origin > Origin to 3D Cursor* moves it in one step.  A mesh that
already has exactly one vertex selected uses that vertex instead, which covers
an origin that must stay where it is.

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
    """Where the hand holds ``ob``, in world space, and how it was decided.

    The **object origin** is the answer in every practical case: retail
    weapons are modelled with their origin at the grip (the Inquisitor flail
    spans ``x -0.066 .. 1.896`` from its own origin), and a hand-made mesh can
    be given that origin in one step — *Object > Set Origin > Origin to 3D
    Cursor*.  Requiring a vertex selection instead was a dead end: a freshly
    imported mesh has every vertex selected, so "exactly one vertex" is a trap
    rather than a convention.

    A single selected vertex still wins when there is one, which covers a mesh
    whose origin cannot be moved (shared with other parts).
    """
    sel = [v for v in ob.data.vertices if v.select]
    if len(sel) == 1:
        return list(ob.matrix_world @ sel[0].co), "vertex"
    return list(ob.matrix_world.translation), "origin"


def _looks_like_a_weapon(ob) -> bool:
    name = ob.name.lower()
    return "weapon" in name or name.startswith(("sword", "mace", "axe",
                                                "bow", "staff", "shield"))


def _weapon_candidates(context, src):
    """Meshes in the scene that may be the reference weapon, best first."""
    others = [ob for ob in context.scene.objects
              if ob.type == "MESH" and ob is not src]
    named = [ob for ob in others if _looks_like_a_weapon(ob)]
    return named or others


class D3UNIT_OT_place_weapon(Operator):
    bl_idname = "d3unit.place_weapon"
    bl_label = "Place on Reference Weapon"
    bl_description = ("Move the active mesh onto the reference weapon: its "
                      "origin (or single selected vertex) on the reference's, "
                      "its long axis along the reference's shaft")
    bl_options = {"UNDO", "REGISTER"}

    source: StringProperty(
        name="Mesh",
        default="",
        description="Object name of the mesh to place; empty = the panel's "
                    "Mesh field, then the active mesh",
    )
    reference: StringProperty(
        name="Reference",
        default="",
        description="Object name of the reference weapon; empty = the panel's "
                    "Reference field, else the only weapon-like mesh",
    )
    flip: BoolProperty(
        name="Flip",
        default=False,
        description="Turn the mesh half a circle about a perpendicular — the "
                    "answer to a blade pointing the wrong way",
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
        # the panel's Mesh field allows an armature or nothing to be active
        if getattr(context.scene, "d3unit_weapon_src", None) is not None:
            return True
        ob = getattr(context, "active_object", None)
        return ob is not None and ob.type == "MESH"

    # -- helpers ---------------------------------------------------------- #
    def _source(self, context):
        """The mesh to place: the operator field, the panel field, the active."""
        if self.source:
            ob = context.scene.objects.get(self.source)
            if ob is not None:
                return ob
        picked = getattr(context.scene, "d3unit_weapon_src", None)
        if picked is not None:
            try:
                if picked.name in context.scene.objects:
                    return picked
            except ReferenceError:      # the object was deleted
                pass
        ob = getattr(context, "active_object", None)
        if ob is not None and ob.type == "MESH":
            return ob
        return None

    def _reference(self, context, src):
        """The weapon to copy the placement from, or ``None`` (already reported).

        Resolution order: the operator's own ``reference`` (scripting), the
        panel's eyedropper field, the only other selected mesh, the only
        weapon-looking mesh in the scene, and finally the only other mesh —
        so the common case is a plain click with both fields empty.
        """
        if self.reference:
            ref = (context.scene.objects.get(self.reference)
                   or bpy.data.objects.get(self.reference))
            if ref is None:
                self.report({"ERROR"}, f"no object named {self.reference!r}")
                return None
            return ref

        picked = getattr(context.scene, "d3unit_weapon_ref", None)
        if picked is not None:
            try:
                if picked.name in context.scene.objects:
                    return picked
            except ReferenceError:      # the object was deleted
                pass

        selected = [ob for ob in context.selected_objects
                    if ob.type == "MESH" and ob is not src]
        if len(selected) == 1:
            return selected[0]

        named = [ob for ob in _weapon_candidates(context, src)
                 if _looks_like_a_weapon(ob)]
        if len(named) == 1:
            return named[0]

        others = _weapon_candidates(context, src)
        if len(others) == 1:
            return others[0]

        meshes = [ob.name for ob in context.scene.objects if ob.type == "MESH"]
        self.report({"ERROR"},
                    "cannot tell which mesh is the reference weapon — pick it "
                    "in the Reference field.  Meshes in the scene: "
                    + (", ".join(sorted(meshes)[:12]) if meshes else "none")
                    + (" ..." if len(meshes) > 12 else ""))
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
        src = self._source(context)
        if src is None or src.type != "MESH":
            self.report({"ERROR"},
                        "no mesh to place — pick one in the panel's Mesh "
                        "field (or select it in the viewport)")
            return {"CANCELLED"}
        ref = self._reference(context, src)
        if ref is None:
            return {"CANCELLED"}
        if ref.type != "MESH":
            self.report({"ERROR"}, "the reference must be a mesh")
            return {"CANCELLED"}

        src_points, ref_points = _points(src), _points(ref)
        if not src_points:
            self.report({"ERROR"}, f"{src.name} has no vertices")
            return {"CANCELLED"}
        if not ref_points:
            self.report({"ERROR"}, f"{ref.name} has no vertices")
            return {"CANCELLED"}
        src_grip, src_how = _grip(src)
        ref_grip, ref_how = _grip(ref)

        try:
            wmod = bridge.module("weapon")
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        mat, info = wmod.align_matrix(ref_points, ref_grip,
                                      src_points, src_grip,
                                      float(self.roll), bool(self.flip))
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
                    f" apart; grips from {src_how}/{ref_how}"
                    + (", flipped" if self.flip else "")
                    + (f", re-parented to {ref.parent_bone!r}"
                       if moved else "")
                    + (" — armature is posed, set Pose Position: Rest for a "
                       "bind-pose placement" if posed else ""))
        return {"FINISHED"}
