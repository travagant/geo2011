# -*- coding: utf-8 -*-
"""Put a hand-made weapon where the reference weapon sits.

Why this is not "pose it into the hand": the exporter writes each vertex as
``node matrix . local position``, i.e. its **world** position in the bind pose,
and then binds the part 1.0 to a single bone.  The engine adds that bone's
motion on top, so a mesh shaped onto an animated frame comes out displaced by
the animation as well.  The reliable recipe is the other way round: measure
where the reference weapon lies in the rest pose, and move the new mesh onto
it.

Two landmarks are enough for that: the point where the hand grips each weapon,
and the weapon's own long axis.

* the axis comes from a principal-component fit of the weapon's vertices (a
  3x3 symmetric eigenproblem, solved with Jacobi rotations — no numpy here),
  oriented away from the grip so "which end is the blade" needs no guessing;
* the motion is ``R`` (rotate the new axis onto the reference's) followed by
  the translation that puts the new grip on the reference grip.

Everything is world space and rigid: no scaling, no shear, so a mesh keeps its
size and proportions.  ``align_matrix`` returns the matrix plus a small report
(the two axes and the angle between them) for the operator to show.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .frame import IDENTITY, Mat4, mm, mvec

Vec3 = List[float]
Axis = Sequence[float]
Points = Sequence[Sequence[float]]


# --------------------------------------------------------------------------- #
#  small vector helpers
# --------------------------------------------------------------------------- #
def _sub(a: Axis, b: Axis) -> Vec3:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a: Axis, b: Axis) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Axis, b: Axis) -> Vec3:
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _norm(a: Axis) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Axis) -> Vec3:
    n = _norm(a)
    if n < 1e-12:
        return [0.0, 0.0, 0.0]
    return [a[0] / n, a[1] / n, a[2] / n]


def centroid(points: Points) -> Vec3:
    n = len(points)
    if not n:
        return [0.0, 0.0, 0.0]
    return [sum(p[i] for p in points) / n for i in range(3)]


# --------------------------------------------------------------------------- #
#  principal axis of a point cloud
# --------------------------------------------------------------------------- #
def _jacobi(a: List[List[float]], sweeps: int = 32
            ) -> Tuple[List[float], List[List[float]]]:
    """Eigen-decomposition of a symmetric 3x3 (values, eigenvector columns)."""
    a = [row[:] for row in a]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(sweeps):
        p, q = 0, 1
        best = abs(a[0][1])
        for i, j in ((0, 2), (1, 2)):
            if abs(a[i][j]) > best:
                p, q, best = i, j, abs(a[i][j])
        if best < 1e-14:
            break
        app, aqq, apq = a[p][p], a[q][q], a[p][q]
        theta = 0.5 * math.atan2(2.0 * apq, aqq - app)
        c, s = math.cos(theta), math.sin(theta)
        # a <- J^T a J for the (p,q) plane rotation
        for k in range(3):
            akp, akq = a[k][p], a[k][q]
            a[k][p] = c * akp - s * akq
            a[k][q] = s * akp + c * akq
        for k in range(3):
            apk, aqk = a[p][k], a[q][k]
            a[p][k] = c * apk - s * aqk
            a[q][k] = s * apk + c * aqk
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p] = c * vkp - s * vkq
            v[k][q] = s * vkp + c * vkq
    return [a[i][i] for i in range(3)], v


def main_axis(points: Points, grip: Optional[Axis] = None) -> Vec3:
    """Unit long axis of the cloud, pointing away from ``grip``.

    Returns ``[0, 0, 0]`` when the cloud is a single point (or a stack of
    duplicates): there is no direction to be had, and the caller falls back to
    translating the grip onto the grip.
    """
    pts = [p for p in points if p is not None]
    if len(pts) < 2:
        return [0.0, 0.0, 0.0]
    c = centroid(pts)
    cov = [[0.0] * 3 for _ in range(3)]
    for p in pts:
        d = _sub(p, c)
        for i in range(3):
            for j in range(3):
                cov[i][j] += d[i] * d[j]
    vals, vecs = _jacobi(cov)
    k = max(range(3), key=lambda i: vals[i])
    if vals[k] <= 1e-12:
        return [0.0, 0.0, 0.0]
    axis = _unit([vecs[0][k], vecs[1][k], vecs[2][k]])
    # orient it so the bulk of the weapon lies on the +axis side of the grip
    if grip is not None and _dot(_sub(c, grip), axis) < 0.0:
        axis = [-x for x in axis]
    return axis


# --------------------------------------------------------------------------- #
#  rigid transforms
# --------------------------------------------------------------------------- #
def translation(v: Axis) -> Mat4:
    m = [row[:] for row in IDENTITY]
    m[0][3], m[1][3], m[2][3] = float(v[0]), float(v[1]), float(v[2])
    return m


def _rotation(axis: Axis, radians: float) -> Mat4:
    """Right-handed rotation about a unit ``axis``."""
    x, y, z = _unit(axis)
    c, s = math.cos(radians), math.sin(radians)
    t = 1.0 - c
    return [[t * x * x + c, t * x * y - s * z, t * x * z + s * y, 0.0],
            [t * x * y + s * z, t * y * y + c, t * y * z - s * x, 0.0],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c, 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def rotation_between(src: Axis, dst: Axis) -> Mat4:
    """Shortest rotation taking unit ``src`` onto unit ``dst``."""
    a, b = _unit(src), _unit(dst)
    if a == [0.0, 0.0, 0.0] or b == [0.0, 0.0, 0.0]:
        return [row[:] for row in IDENTITY]
    axis = _cross(a, b)
    n = _norm(axis)
    d = _dot(a, b)
    if n < 1e-9:
        if d > 0.0:
            return [row[:] for row in IDENTITY]
        # antiparallel: half a turn about any axis perpendicular to `a`
        pick = [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]
        return _rotation(_unit(_cross(a, pick)), math.pi)
    return _rotation(axis, math.atan2(n, d))


def angle_between(src: Axis, dst: Axis) -> float:
    """Angle in degrees between two axes (0..180)."""
    a, b = _unit(src), _unit(dst)
    if a == [0.0, 0.0, 0.0] or b == [0.0, 0.0, 0.0]:
        return 0.0
    d = max(-1.0, min(1.0, _dot(a, b)))
    return math.degrees(math.acos(d))


# --------------------------------------------------------------------------- #
#  the placement itself
# --------------------------------------------------------------------------- #
def perpendicular(axis: Axis) -> Vec3:
    """Some unit vector perpendicular to ``axis`` (stable for any input)."""
    a = _unit(axis)
    pick = [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]
    return _unit(_cross(a, pick))


def align_matrix(ref_points: Points, ref_grip: Axis,
                 src_points: Points, src_grip: Axis,
                 roll_deg: float = 0.0, flip: bool = False
                 ) -> Tuple[Mat4, Dict[str, object]]:
    """Rigid matrix that lays ``src`` onto ``ref`` (grip on grip, axis on axis).

    ``roll_deg`` spins the mesh about the reference axis afterwards — the
    blade's flat face is the one thing the axis cannot decide.  ``flip``
    instead turns the mesh half a circle about a perpendicular, which is what
    "the blade points the wrong way" needs: a grip in the middle of a weapon
    (an origin at the crossguard) leaves the axis sign undecided.
    """
    ref_axis = main_axis(ref_points, ref_grip)
    src_axis = main_axis(src_points, src_grip)
    info: Dict[str, object] = {
        "ref_axis": list(ref_axis),
        "src_axis": list(src_axis),
        "angle_deg": angle_between(src_axis, ref_axis),
        "axis_found": bool(_norm(ref_axis) and _norm(src_axis)),
    }
    rx, sx = list(ref_axis), list(src_axis)
    rotate = rotation_between(sx, rx) if info["axis_found"] else \
        [row[:] for row in IDENTITY]
    mat = mm(mm(translation(ref_grip), rotate),
             translation([-c for c in src_grip]))
    if flip and info["axis_found"]:
        turn = mm(mm(translation(ref_grip),
                     _rotation(perpendicular(rx), math.pi)),
                  translation([-c for c in ref_grip]))
        mat = mm(turn, mat)
        info["flipped"] = True
    if roll_deg and info["axis_found"]:
        spin = mm(mm(translation(ref_grip), _rotation(rx, math.radians(roll_deg))),
                  translation([-c for c in ref_grip]))
        mat = mm(spin, mat)
    info["matrix"] = mat
    return mat, info


def place(points: Points, mat: Mat4) -> List[Vec3]:
    """``mat`` applied to every point (handy for a preview or a test)."""
    out = []
    for p in points:
        v = mvec(mat, [p[0], p[1], p[2], 1.0])
        out.append([v[0], v[1], v[2]])
    return out


def bbox_size(points: Points) -> Vec3:
    if not points:
        return [0.0, 0.0, 0.0]
    return [max(p[i] for p in points) - min(p[i] for p in points)
            for i in range(3)]
