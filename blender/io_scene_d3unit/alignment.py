# -*- coding: utf-8 -*-
"""Reversible unit work placement and model alignment."""
import contextlib
import math
import uuid

import bpy
from bpy.props import FloatProperty, PointerProperty
from mathutils import Matrix, Vector
from . import unitprops

WORK_PROP = "d3unit_work_id"
WRAPPER_PROP = "d3unit_work_wrapper"
INVERSE_PROP = "d3unit_work_original_parent_inverse"


def _matrix(values):
    if len(values) != 16:
        raise ValueError("Invalid saved parent inverse")
    return Matrix([values[i:i + 4] for i in range(0, 16, 4)])


def _flat(matrix):
    return [float(x) for row in matrix for x in row]


def _objects(arm):
    from .exporter import _collect_unit
    return _collect_unit(arm)


def _wrapper(arm):
    uid = str(arm.get(WORK_PROP) or "")
    matches = [o for o in bpy.context.scene.objects if o.get(WRAPPER_PROP) == uid] if uid else []
    if len(matches) != 1:
        raise ValueError("Work placement helper is missing or duplicated; restore a backup")
    return matches[0]


def _initial_roots(arm):
    objects = _objects(arm)
    members = set(objects)
    roots = [o for o in objects if o.parent not in members]
    for ob in roots:
        if ob.parent is not None or ob.constraints:
            raise ValueError("Cannot safely place an object with an external parent or constraints: " + ob.name)
        if INVERSE_PROP in ob:
            raise ValueError("Object already has saved work placement: " + ob.name)
    return roots


def begin_work(arm, extra_degrees=0.0):
    if WORK_PROP in arm:
        raise ValueError("Work placement is already active")
    roots = _initial_roots(arm)
    # Parent at the identity first: the animated object's basis and all of
    # its bone and object-level action channels remain unchanged.
    wrapper = bpy.data.objects.new("D3 work placement", None)
    bpy.context.scene.collection.objects.link(wrapper)
    uid = uuid.uuid4().hex
    wrapper[WRAPPER_PROP] = uid
    touched = []
    try:
        for ob in roots:
            basis = ob.matrix_basis.copy()
            ob[INVERSE_PROP] = _flat(ob.matrix_parent_inverse)
            touched.append(ob)
            ob.parent = wrapper
            ob.matrix_parent_inverse = Matrix.Identity(4)
            ob.matrix_basis = basis
        bpy.context.view_layer.update()
        origin = arm.matrix_world.translation.copy()
        yaw = arm.matrix_world.to_euler().z
        wrapper.matrix_world = (Matrix.Rotation(-yaw + math.radians(extra_degrees), 4, 'Z')
                                @ Matrix.Translation(-origin))
        arm[WORK_PROP] = uid
        bpy.context.view_layer.update()
    except Exception:
        for ob in touched:
            basis = ob.matrix_basis.copy()
            ob.parent = None
            ob.matrix_parent_inverse = _matrix(ob[INVERSE_PROP])
            ob.matrix_basis = basis
            del ob[INVERSE_PROP]
        bpy.data.objects.remove(wrapper, do_unlink=True)
        raise


def _detached_roots(arm, wrapper):
    """Identify managed roots and new unparented meshes included in export."""
    objects = _objects(arm)
    managed = [o for o in objects if o.parent == wrapper]
    if arm not in managed and not any(p == wrapper for p in arm.parent_recursive):
        raise ValueError("The unit is no longer under its work placement helper")
    for ob in managed:
        if INVERSE_PROP not in ob:
            raise ValueError("A helper child lacks its saved original parent transform: " + ob.name)
    new_roots = [o for o in objects if o != wrapper and o.parent is None]
    for ob in new_roots:
        if ob.constraints or ob.animation_data:
            raise ValueError("Bind the newly added animated or constrained object to the armature before export: " + ob.name)
    return managed, new_roots


@contextlib.contextmanager
def original_for_export(arm):
    if arm is None or WORK_PROP not in arm:
        yield
        return
    wrapper = _wrapper(arm)
    managed, new_roots = _detached_roots(arm, wrapper)
    delta = wrapper.matrix_world.copy()
    saved = [(ob, ob.matrix_basis.copy(), ob.matrix_parent_inverse.copy()) for ob in managed]
    new_saved = [(ob, ob.matrix_world.copy()) for ob in new_roots]
    try:
        for ob, basis, _ in saved:
            ob.parent = None
            ob.matrix_parent_inverse = _matrix(ob[INVERSE_PROP])
            ob.matrix_basis = basis
        for ob, matrix in new_saved:
            ob.matrix_world = delta.inverted() @ matrix
        bpy.context.view_layer.update()
        yield
    finally:
        for ob, matrix in new_saved:
            ob.matrix_world = matrix
        for ob, basis, parent_inverse in saved:
            ob.parent = wrapper
            ob.matrix_parent_inverse = parent_inverse
            ob.matrix_basis = basis
        bpy.context.view_layer.update()


def end_work(arm):
    if WORK_PROP not in arm:
        raise ValueError("Work placement is not active")
    wrapper = _wrapper(arm)
    managed, new_roots = _detached_roots(arm, wrapper)
    delta = wrapper.matrix_world.copy()
    for ob in managed:
        basis = ob.matrix_basis.copy()
        ob.parent = None
        ob.matrix_parent_inverse = _matrix(ob[INVERSE_PROP])
        ob.matrix_basis = basis
        del ob[INVERSE_PROP]
    for ob in new_roots:
        ob.matrix_world = delta.inverted() @ ob.matrix_world
    del arm[WORK_PROP]
    bpy.data.objects.remove(wrapper, do_unlink=True)
    bpy.context.view_layer.update()


def _bounds(ob):
    points = [ob.matrix_world @ Vector(p) for p in ob.bound_box]
    return Vector((sum(p.x for p in points) / 8, sum(p.y for p in points) / 8,
                   min(p.z for p in points)))


class D3UNIT_OT_align_model(bpy.types.Operator):
    bl_idname = "d3unit.align_model"
    bl_label = "Place Model at Reference"
    bl_description = "Move the new model to the reference body; does not bind, rotate or resize it"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        src = scene.d3unit_align_source
        ref = scene.d3unit_align_reference
        selected = [o for o in context.selected_objects if o.type == 'MESH']
        if src is None and ref is None and len(selected) == 2 and context.active_object in selected:
            ref = context.active_object
            src = next(o for o in selected if o != ref)
        if not src or not ref or src == ref:
            self.report({'ERROR'}, "Choose Model and Reference body, or select two meshes with the reference active")
            return {'CANCELLED'}
        if src.mode != 'OBJECT' or ref.mode != 'OBJECT' or src.constraints:
            self.report({'ERROR'}, "Use Object Mode and an unconstrained source mesh")
            return {'CANCELLED'}
        try:
            matrix = src.matrix_world.copy()
            matrix.translation += _bounds(ref) - _bounds(src)
            src.matrix_world = matrix
            context.view_layer.update()
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, "Model moved; rotate, scale and bind it as needed")
        return {'FINISHED'}


class D3UNIT_OT_work_placement(bpy.types.Operator):
    bl_idname = "d3unit.work_placement"
    bl_label = "Center Unit for Editing"
    bl_description = "Move the whole unit into a reversible work position; game export uses the original placement"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm = unitprops.find_unit_root(context)
        if arm is None or arm.type != 'ARMATURE' or context.mode != 'OBJECT':
            self.report({'ERROR'}, "Select the unit armature in Object Mode")
            return {'CANCELLED'}
        try:
            if WORK_PROP in arm:
                end_work(arm)
                self.report({'INFO'}, "Original unit placement restored")
            else:
                begin_work(arm, context.scene.d3unit_work_angle)
                self.report({'INFO'}, "Work placement enabled; export uses original placement")
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


def register_properties():
    bpy.types.Scene.d3unit_align_source = PointerProperty(type=bpy.types.Object, name="Model", poll=lambda self, ob: ob.type == 'MESH')
    bpy.types.Scene.d3unit_align_reference = PointerProperty(type=bpy.types.Object, name="Reference body", poll=lambda self, ob: ob.type == 'MESH')
    bpy.types.Scene.d3unit_work_angle = FloatProperty(name="Extra turn", default=0.0, description="Additional rotation around vertical, in degrees")


def unregister_properties():
    for name in ('d3unit_align_source', 'd3unit_align_reference', 'd3unit_work_angle'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
