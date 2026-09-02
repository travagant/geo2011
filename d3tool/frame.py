"""Pure-python 4x4 matrix / quaternion helpers and the *frame conversion*
between a Blender re-saved glTF and the GM `.g`/`.a` coordinate frames.

Why this module exists
----------------------

A dis3tool glTF export stores every mesh in the `.g`'s own bind frame: the
mesh nodes sit at the scene root with an identity transform, each sub-mesh
carries its own small skin (the `.g` part's bone table verbatim) and the
animation channels are the `.a` records one-to-one.  Reverse-exporting such
a file is a byte-exact round-trip.

Blender's glTF I/O cannot preserve that layout.  On a round-trip it

* merges the per-mesh skins into ONE armature-wide skin (62 joints for the
  Angel, where the `.g` parts carry 3/16/41/16-bone tables of their own),
* parents the mesh nodes under the armature root (a non-identity transform),
* bakes whatever rest pose the user applied into both the bones and the
  mesh ("Apply Pose as Rest Pose"), and
* re-samples the 30 fps animation onto the scene frame rate (default 24).

The *rendered* character is preserved, though: at every animation time t
both files describe the same world-space pose.  Writing that down gives the
conversion used here.  With ``G_x(j, t)`` the global (world) matrix of
joint ``j`` at time ``t`` in file ``x``:

    Q_j = inv(G_o(j, t)) . G_b(j, t)          -- constant in t (measured:
                                                   worst deviation 5e-06)

* vertices (through joint space, per part bone table ``M_j`` = the `.g`
  bone descriptor == the dis3tool glTF IBM):

    v_gm = SUM_j w_j . inv(M_j) . Q_j . IBM_b_j . v_b

* animation locals (``L`` the per-bone local TRS matrix):

    L_o = Q_parent . L_b . inv(Q_j)

All three are verified numerically against the bundled
``Blender/character_empire_angel.gltf`` (four meshes, median error 0.000000,
every sampled vertex < 1e-3 from its donor original).

Matrices are row-major nested lists of floats with column-vector semantics
(``p' = M . p``); glTF stores MAT4 accessors **column-major**, so
:func:`cmaj` must be applied when reading skin ``inverseBindMatrices``.
The `.g` bone descriptors and the dis3tool IBM accessors hold the *same 16
floats*, i.e. a `.g` matrix must go through :func:`cmaj` as well to obtain
its math form.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Mat4 = List[List[float]]
Vec4 = List[float]

IDENTITY: Mat4 = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def mm(a: Mat4, b: Mat4) -> Mat4:
    """Matrix product ``a . b``."""
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
            for r in range(4)]


def mvec(m: Mat4, v: Sequence[float]) -> Vec4:
    """Transform a 4-component point ``m . v``."""
    return [sum(m[r][c] * v[c] for c in range(4)) for r in range(4)]


def inv4(m: Mat4) -> Mat4:
    """Inverse of a 4x4 matrix (Gauss-Jordan with partial pivoting)."""
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(4)]
         for i, row in enumerate(m)]
    for c in range(4):
        p = max(range(c, 4), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-12:
            raise ValueError("singular matrix")
        a[c], a[p] = a[p], a[c]
        pv = a[c][c]
        a[c] = [x / pv for x in a[c]]
        for r in range(4):
            if r != c and a[r][c] != 0.0:
                f = a[r][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [row[4:] for row in a]


def cmaj16(flat: Sequence[float]) -> Mat4:
    """glTF MAT4 floats (column-major) -> row-major math matrix."""
    return [[flat[c * 4 + r] for c in range(4)] for r in range(4)]


def flatten16(m: Mat4) -> List[float]:
    """Row-major math matrix -> glTF MAT4 floats (column-major)."""
    return [m[r][c] for c in range(4) for r in range(4)]


def quat_to_mat(q: Sequence[float]) -> Mat4:
    """Unit quaternion (x, y, z, w) -> rotation matrix (column-vector)."""
    x, y, z, w = q
    R = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    M = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    for r in range(3):
        for c in range(3):
            M[r][c] = R[r][c]
    return M


def mat_to_quat(m: Mat4) -> Tuple[float, float, float, float]:
    """Rotation part of a matrix -> quaternion (x, y, z, w), unit norm."""
    m00, m01, m02 = m[0][0], m[0][1], m[0][2]
    m10, m11, m12 = m[1][0], m[1][1], m[1][2]
    m20, m21, m22 = m[2][0], m[2][1], m[2][2]
    tr = m00 + m11 + m22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        q = ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        q = (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        q = ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        q = ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)   # noqa: TUPLE


def trs_matrix(rotation: Optional[Sequence[float]] = None,
               translation: Optional[Sequence[float]] = None,
               scale: Optional[Sequence[float]] = None) -> Mat4:
    """TRS -> matrix (any component may be None for its default)."""
    M = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    R = quat_to_mat(rotation) if rotation else IDENTITY
    S = scale or (1.0, 1.0, 1.0)
    t = translation or (0.0, 0.0, 0.0)
    for r in range(3):
        for c in range(3):
            M[r][c] = R[r][c] * S[c]
        M[r][3] = t[r]
    return M


def sample_matrix(sample: Sequence[float]) -> Mat4:
    """A `.a` sample ``(qx, qy, qz, qw, tx, ty, tz)`` -> local matrix."""
    return trs_matrix(sample[:4], sample[4:7])


def quat_slerp(q0: Sequence[float], q1: Sequence[float], f: float
               ) -> Tuple[float, float, float, float]:
    """Spherical linear interpolation between two quaternions."""
    d = sum(a * b for a, b in zip(q0, q1))
    if d < 0.0:
        q1 = [-x for x in q1]
        d = -d
    if d > 0.9995 or f <= 0.0:
        g = max(0.0, min(1.0, f))
        out = [a + g * (b - a) for a, b in zip(q0, q1)]
        n = math.sqrt(sum(c * c for c in out)) or 1.0
        return tuple(c / n for c in out)   # type: ignore[return-value]
    th = math.acos(max(-1.0, min(1.0, d)))
    s = math.sin(th)
    w0 = math.sin((1.0 - f) * th) / s
    w1 = math.sin(f * th) / s
    out = [a * w0 + b * w1 for a, b in zip(q0, q1)]
    n = math.sqrt(sum(c * c for c in out)) or 1.0
    return tuple(c / n for c in out)   # type: ignore[return-value]


def quat_close(q0: Sequence[float], q1: Sequence[float],
               tol: float = 2e-3) -> bool:
    """Rotation equality up to sign (q and -q are the same rotation)."""
    d = max(abs(a - b) for a, b in zip(q0, q1))
    ds = max(abs(a + b) for a, b in zip(q0, q1))
    return min(d, ds) <= tol


# --------------------------------------------------------------------------- #
#  the Blender -> GM frame conversion
# --------------------------------------------------------------------------- #

def is_identity(m: Mat4, tol: float = 1e-6) -> bool:
    return all(abs(m[r][c] - (1.0 if r == c else 0.0)) <= tol
               for r in range(4) for c in range(4))


def build_q_map(anim_donors, node_locals_b: Dict[str, Mat4],
                node_parents: Dict[str, str]) -> Dict[str, Mat4]:
    """Calibrate the per-bone frame change ``Q_j`` against the donor `.a`.

    ``anim_donors`` -- parsed donor `.a` AnimFile(s); the first frame of the
    *first* stream is the GM rest pose every glTF node TRS was built from.
    ``node_locals_b`` -- the Blender glTF's per-bone local rest matrices
    (the node's static TRS, which for a Blender export is whatever pose the
    scene was left in -- NOT necessarily the bind), keyed by bone name.
    ``node_parents`` -- bone name -> parent bone name ("" for scene roots).

    ``L_o = Q_parent . L_b . inv(Q_j)`` must hold for the rest frame; solving
    for ``Q_j`` top-down (parents first) pins every bone to the donor's own
    rest data, absorbing any scene-root offset without assuming one.
    """
    order: List[str] = []
    seen = set()

    def visit(name: str) -> None:
        if name in seen or name not in node_locals_b:
            return
        seen.add(name)
        p = node_parents.get(name, "")
        if p:
            visit(p)
        order.append(name)

    for name in node_locals_b:
        visit(name)

    rest: Dict[str, Sequence[float]] = {}
    parents: Dict[str, str] = {}
    for donor in anim_donors:
        for b in donor.bones:
            parents.setdefault(b.name, b.parent or "")
            if b.name not in rest and b.frames:
                rest[b.name] = b.frames[0]
    Q: Dict[str, Mat4] = {}
    for name in order:
        Lb = node_locals_b.get(name)
        if Lb is None:
            continue
        Lo = sample_matrix(rest[name][:7]) if name in rest else None
        if Lo is None:
            continue
        p = parents.get(name, "") or node_parents.get(name, "")
        Qp = Q.get(p, IDENTITY) if p else IDENTITY
        # L_o = Qp . Lb . inv(Q)  =>  Q = inv(inv(Lb) . inv(Qp) . L_o)
        try:
            A = mm(inv4(Lb), mm(inv4(Qp), Lo))
            Q[name] = inv4(A)
        except ValueError:
            continue
    return Q


def convert_positions(pos: Sequence[float],
                      lanes: Sequence[Tuple[float, str]],
                      K: Dict[str, Mat4],
                      fallback: Optional[Mat4] = None) -> Tuple[float, float, float]:
    """v_gm = SUM w . K_j . v_b (blended through joint space)."""
    v = [pos[0], pos[1], pos[2], 1.0]
    acc = [0.0, 0.0, 0.0]
    total = 0.0
    for w, nm in lanes:
        M = K.get(nm, fallback)
        if M is None:
            continue
        p = mvec(M, v)
        acc = [a + w * b for a, b in zip(acc, p)]
        total += w
    if total <= 0.0:
        return (float(pos[0]), float(pos[1]), float(pos[2]))
    return (acc[0] / total, acc[1] / total, acc[2] / total)


def convert_local(Lb: Mat4, Qj: Mat4, Qp: Mat4) -> Mat4:
    """L_o = Q_parent . L_b . inv(Q_j)."""
    return mm(Qp, mm(Lb, inv4(Qj)))


def sample_channel(times: Sequence[float], vals: Sequence[float], ncomp: int,
                   t: float, slerp: bool = False, jump: bool = False):
    """Sample a glTF animation channel at time ``t`` (linear/slerp, clamped).

    ``jump`` -- guard against the re-sampling seams: when two neighbouring
    keys hold very different values (a stream boundary the 24 fps grid
    folded into one interpolation segment), interpolating would blend two
    poses that were never adjacent -- take the *nearest* key instead.
    """
    n = len(times)
    if n == 0:
        return None
    if n == 1 or t <= times[0]:
        return list(vals[:ncomp])
    if t >= times[-1]:
        return list(vals[(n - 1) * ncomp:(n) * ncomp])
    lo, hi = 0, n - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if times[mid] <= t:
            lo = mid
        else:
            hi = mid
    t0, t1 = times[lo], times[hi]
    if t1 <= t0:
        return list(vals[lo * ncomp:lo * ncomp + ncomp])
    f = (t - t0) / (t1 - t0)
    a = vals[lo * ncomp:lo * ncomp + ncomp]
    b = vals[hi * ncomp:hi * ncomp + ncomp]
    if jump:
        if ncomp == 4:
            d = abs(sum(x * y for x, y in zip(a, b)))
            if d < 0.99:
                return list(a if f < 0.5 else b)
        else:
            span = max(abs(x - y) for x, y in zip(a, b))
            if span > 0.3:
                return list(a if f < 0.5 else b)
    if slerp and ncomp == 4:
        return list(quat_slerp(a, b, f))
    return [x + f * (y - x) for x, y in zip(a, b)]
