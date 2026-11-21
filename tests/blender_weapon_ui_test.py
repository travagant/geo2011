#!/usr/bin/env python3
"""Integration checks for weapon placement using bpy (not a graphical UI)."""
import os
import sys
import traceback

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(repo, 'blender'), repo]
import bpy
import io_scene_d3unit as addon


def armature(name):
    bpy.ops.object.armature_add()
    arm = bpy.context.object
    arm.name = name
    # Both skeletons intentionally have a bone with the same name.
    return arm


def weapon(name, arm, location):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    ob = bpy.context.object
    ob.name = name
    world = ob.matrix_world.copy()
    ob.parent = arm
    ob.parent_type = 'BONE'
    ob.parent_bone = arm.data.bones[0].name
    ob.matrix_world = world
    bpy.context.view_layer.update()
    return ob


def main():
    addon.register()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    a = armature('Skeleton A')
    b = armature('Skeleton B')
    src = weapon('new sword', a, (4, 0, 0))
    ref = weapon('reference sword', b, (0, 2, 0))
    bpy.context.view_layer.objects.active = src
    assert src.parent_bone == ref.parent_bone and src.parent is not ref.parent
    assert bpy.ops.d3unit.place_weapon(source=src.name, reference=ref.name, match_parent=True) == {'FINISHED'}
    assert src.parent == b and src.parent_bone == ref.parent_bone
    assert (src.matrix_world.translation - ref.matrix_world.translation).length < 1e-4
    src.parent = a
    src.parent_type = 'BONE'
    src.parent_bone = a.data.bones[0].name
    bpy.context.view_layer.update()
    assert bpy.ops.d3unit.place_weapon(source=src.name, reference=ref.name, match_parent=False) == {'FINISHED'}
    assert src.parent == a
    before = src.matrix_world.copy()
    try:
        result = bpy.ops.d3unit.place_weapon(source=src.name, reference=src.name)
        assert result == {'CANCELLED'}
    except RuntimeError as exc:
        assert 'разными объектами' in str(exc)
    assert src.parent == a and src.matrix_world == before
    assert bpy.context.scene.d3unit_weapon_match_parent is True
    addon.unregister()
    assert not hasattr(bpy.types.Scene, 'd3unit_weapon_match_parent')
    print('PASS weapon alignment: different armatures, opt-out, same-object guard', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
