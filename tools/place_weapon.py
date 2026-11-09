"""Put a hand-made weapon mesh exactly where the reference weapon sits.

Paste into Blender's Scripting workspace (or run with
``blender -P tools/place_weapon.py``) while the unit is open.

Why this and not "pose it into the hand": the exporter writes each vertex as
``node matrix x local position``, i.e. the **world** position in the bind pose,
and the part is then bound 1.0 to one bone (``Bip01 R Hand`` for the Angel's
sword).  The engine adds the bone's motion on top, so the mesh has to lie in
the rest pose exactly where the hand-held weapon lies -- posing it onto an
animated frame doubles the offset later.

Usage
-----
1. Armature > Data properties > Pose position: **Rest position** (so both
   objects are drawn in the bind pose).
2. In *Edit Mode* select the single vertex on the reference weapon that the
   hand grips (bottom of the handle), then Tab out.
3. Same on your mesh: select its grip vertex, Tab out.
4. Run this script.

It rotates your mesh about its grip until its long axis (PCA) matches the
reference weapon's shaft, then moves it so the two grip vertices coincide.
Everything is measured in world space; the objects' origins do not matter.
Set ``ROLL`` (degrees) to spin the blade about the shaft, and match the parent
bone explicitly if the two objects are not parented alike.
"""

import bpy                                    # noqa: F401  (Blender only)
import numpy as np
from mathutils import Matrix, Vector

REF = "weapon_empire_inquisitor"     # the reference weapon (mace)
SRC = "empire_knight_sword"          # the mesh to place
ROLL = 0.0                           # extra spin about the shaft, degrees
MATCH_PARENT = True                  # parent SRC to REF's bone, keeping place


def one_point(ob):
    """World position of the single selected vertex of ``ob``."""
    sel = [v for v in ob.data.vertices if v.select]
    if len(sel) != 1:
        raise RuntimeError("%s: select exactly one vertex (now %d)"
                           % (ob.name, len(sel)))
    return np.array(ob.matrix_world @ sel[0].co, dtype=float)


def cloud(ob):
    m = ob.matrix_world
    return np.array([list(m @ v.co) for v in ob.data.vertices], dtype=float)


def axis_away(P, grip):
    """Main axis of the point cloud, pointing away from the grip."""
    C = P - P.mean(axis=0)
    w, V = np.linalg.eigh(C.T @ C)          # covariance is symmetric
    a = V[:, int(np.argmax(w))]
    a = a / np.linalg.norm(a)
    if float((P.mean(axis=0) - grip) @ a) < 0:
        a = -a
    return Vector(a)


ref = bpy.data.objects.get(REF)
src = bpy.data.objects.get(SRC)
if ref is None or src is None:
    raise RuntimeError("no object named %s / %s" % (REF, SRC))

# --- 1. same bone as the reference weapon, world position preserved --------
print("parent: %s %r | reference: %s %r"
      % (src.parent_type, src.parent_bone, ref.parent_type, ref.parent_bone))
if MATCH_PARENT and ref.parent_type == "BONE" and \
        (src.parent_type, src.parent_bone) != (ref.parent_type,
                                              ref.parent_bone):
    world = src.matrix_world.copy()
    src.parent = ref.parent
    src.parent_type = "BONE"
    src.parent_bone = ref.parent_bone
    src.matrix_parent_inverse.identity()
    src.matrix_world = world
    print("re-parented to bone %r" % ref.parent_bone)

# --- 2. align: grip -> grip, long axis -> shaft ---------------------------
ref_grip = one_point(ref)
src_grip = one_point(src)
ref_axis = axis_away(cloud(ref), ref_grip)
src_axis = axis_away(cloud(src), src_grip)
print("axes: reference %s, mesh %s, angle %.1f deg"
      % (tuple(round(x, 3) for x in ref_axis),
         tuple(round(x, 3) for x in src_axis),
         np.degrees(ref_axis.angle(src_axis))))

M = (Matrix.Translation(Vector(ref_grip)) @
     src_axis.rotation_difference(ref_axis).to_matrix().to_4x4() @
     Matrix.Translation(-Vector(src_grip)))
if ROLL:
    M = (Matrix.Translation(Vector(ref_grip)) @
         Matrix.Rotation(np.radians(ROLL), 4, ref_axis) @
         Matrix.Translation(-Vector(ref_grip)) @ M)
src.matrix_world = M @ src.matrix_world
bpy.context.view_layer.update()

# --- 3. report -----------------------------------------------------------
ref_p, src_p = cloud(ref), cloud(src)
print("reference bbox size %s" % (tuple(round(x, 3) for x in
                                        (ref_p.max(axis=0) -
                                         ref_p.min(axis=0))),))
print("mesh bbox size      %s" % (tuple(round(x, 3) for x in
                                        (src_p.max(axis=0) -
                                         src_p.min(axis=0))),))
print("grip now at %s" % (tuple(round(x, 4) for x in ref_grip),))
