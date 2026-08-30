"""Read a dis3tool glTF export and convert it into the neutral model
(:class:`~d3tool.model.GltfModel`) and ultimately a
:class:`~d3tool.model.SkinnedMesh` that can be written back to the GM `.g`
format (reverse export).
"""
from __future__ import annotations

import json
import struct
from typing import Dict, List, Tuple

from . import anim as animmod
from .model import Bone, GltfModel, SkinnedMesh, Vertex

_COMP = {
    5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f",
}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _read_accessor(gltf, buf, idx):
    a = gltf["accessors"][idx]
    bv = gltf["bufferViews"][a["bufferView"]]
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    n = a["count"]
    typ = a["type"]
    comp = a["componentType"]
    ncomp = _NCOMP[typ]
    fmt = _COMP[comp]
    size = struct.calcsize(fmt)
    stride = bv.get("byteStride", ncomp * size)
    out = []
    for i in range(n):
        vals = struct.unpack_from("<" + fmt * ncomp, buf, off + i * stride)
        out.append(vals)
    return out


def detect_weights_on_vertex(wts) -> int:
    """Estimate the number of influence slots the source mesh actually uses.

    dis3tool stores the last influence weight implicitly (``1 - sum(rest)``),
    so a vertex whose influences occupy the first ``k`` slots needs at least
    ``k`` slots to reconstruct them without loss.  We look at the highest
    component index that carries a non-zero weight across all vertices and add
    one, clamped to ``[2, 4]``.  This is what saves e.g. Wildboar (which the
    source exporter wrote with 3 slots) from being silently truncated to 2.
    """
    if not wts:
        return 2
    max_slot = 1  # at least two slots (the first + the implied last)
    for w in wts:
        # find the last non-zero weight component
        for k in range(len(w) - 1, -1, -1):
            if abs(w[k]) > 1e-6:
                max_slot = max(max_slot, k + 1)
                break
    return max(2, min(4, max_slot))


def load_gltf(path: str, weights_on_vertex: int = 0) -> GltfModel:
    """Parse a dis3tool glTF export into a neutral :class:`GltfModel`.

    ``weights_on_vertex`` is the number of influence slots the resulting model
    should carry (2, 3 or 4).  The default (0) auto-detects it from the weight
    accessor, so assets written with 3 or 4 slots keep all their influences.
    """
    with open(path, "r", encoding="utf-8") as fh:
        gltf = json.load(fh)
    import os
    base_dir = os.path.dirname(os.path.abspath(path)) or "."
    uri = gltf["buffers"][0]["uri"]
    buf = open(os.path.join(base_dir, uri), "rb").read()

    # ---- mesh / skin ----
    mesh = gltf["meshes"][0]
    prim = mesh["primitives"][0]
    attrs = prim["attributes"]
    ind_acc = prim["indices"]

    vtf = _read_accessor(gltf, buf, attrs["POSITION"])
    nrm = _read_accessor(gltf, buf, attrs["NORMAL"])
    if "TEXCOORD_0" in attrs:
        uv = _read_accessor(gltf, buf, attrs["TEXCOORD_0"])
    else:
        uv = [(0.0, 0.0)] * len(vtf)
    wts = _read_accessor(gltf, buf, attrs["WEIGHTS_0"]) if "WEIGHTS_0" in attrs else None
    jts = _read_accessor(gltf, buf, attrs["JOINTS_0"]) if "JOINTS_0" in attrs else None
    indices = [x[0] for x in _read_accessor(gltf, buf, ind_acc)]

    # ---- nodes / skeleton ----
    nodes = gltf.get("nodes", [])
    skin = gltf.get("skins", [None])[0] if gltf.get("skins") else None
    bone_indices: Dict[str, int] = {}
    joints = skin["joints"] if skin else []
    joint_names = [nodes[j].get("name", f"bone{i}") for i, j in enumerate(joints)]
    for i, name in enumerate(joint_names):
        bone_indices[name] = i

    # The GM bone descriptor array is *exactly* the glTF inverseBindMatrices
    # (accessor 6), in the same order as the skin joints.
    inverse_bind = _read_accessor(gltf, buf, skin["inverseBindMatrices"])

    bones: List[Bone] = []
    for i, j in enumerate(joints):
        name = nodes[j].get("name", f"bone{i}")
        bones.append(Bone(name, tuple(inverse_bind[i])))

    # ---- vertices ----
    # 0 means "auto-detect": derive the slot count from the actual data so we
    # never drop a real influence (this is what fixes Wildboar's 3-slot skin).
    if weights_on_vertex:
        w_slots = weights_on_vertex
    else:
        w_slots = detect_weights_on_vertex(wts)
    vertices: List[Vertex] = []
    for i, pos in enumerate(vtf):
        w = wts[i] if wts else (1.0, 0.0, 0.0, 0.0)
        jn = jts[i] if jts else (0, 0, 0, 0)
        # dis3tool preserves the glTF influence order (no re-sorting): the
        # GM vertex holds the first `w` slots exactly as they appear.
        weights = list(w[: w_slots])
        bones_idx = list(jn[: w_slots])
        # GM stores the first w-1 weights; the last is implied.
        stored = list(weights[:-1])
        if not stored:
            stored = [1.0]
        vertices.append(
            Vertex(
                pos,
                nrm[i],
                uv[i],
                0xFFFFFFFF,
                tuple(stored),
                tuple(bones_idx),
            )
        )

    mesh_name = nodes[0].get("name", "") if nodes else ""
    import os
    geometry_file = os.path.basename(path)
    if geometry_file.endswith(".gltf"):
        geometry_file = geometry_file[: -len(".gltf")]
    anim = None
    frames: List[float] = []
    channels: Dict[int, dict] = {}
    if gltf.get("animations"):
        anim = gltf["animations"][0]
        # frame times from the first sampler input accessor
        if anim["channels"]:
            ch0 = anim["channels"][0]
            sampler = anim["samplers"][ch0["sampler"]]
            frames = [x[0] for x in _read_accessor(gltf, buf, sampler["input"])]
        # group channel outputs by target node and path
        for ch in anim["channels"]:
            node = ch["target"]["node"]
            path = ch["target"]["path"]
            out = _read_accessor(gltf, buf, anim["samplers"][ch["sampler"]]["output"])
            channels.setdefault(node, {})[path] = out

    model = GltfModel(
        mesh_name=mesh_name,
        geometry_file=geometry_file or "character",
        vertex_count=len(vtf),
        tri_count=len(indices) // 3,
        vertices=vertices,
        indices=indices,
        nodes=nodes,
        bones=bones,
        bone_indices=bone_indices,
        frames=frames,
        animation=anim,
        anim_channels=channels,
        weights_on_vertex=w_slots,
    )
    return model


def mesh_to_skinned(m: GltfModel, weights_on_vertex: int = 0) -> SkinnedMesh:
    """Convert a :class:`GltfModel` into a :class:`SkinnedMesh` ready for `.g`.

    ``weights_on_vertex`` selects the number of influence slots written to the
    GM vertex format (2, 3 or 4).  0 (default) uses the slot count detected on
    the :class:`GltfModel`, preserving every influence from the source.
    """
    w = weights_on_vertex or m.weights_on_vertex or 2
    vertices: List[Vertex] = []
    for v in m.vertices:
        # map back to the glTF 4-component influence order, then keep the
        # first `w` slots verbatim (mirrors dis3tool, which never re-sorts).
        weights = list(v.gltf_weights)
        joints = list(v.gltf_joints)
        weights = weights[:w]
        joints = joints[:w]
        stored = list(weights[:-1])
        if not stored:
            stored = [1.0]
        vertices.append(
            Vertex(
                v.position, v.normal, v.uv, v.diffuse,
                tuple(stored), tuple(joints),
            )
        )

    return SkinnedMesh(
        name=m.mesh_name,
        geometry_file=m.geometry_file,
        vertex_count=m.vertex_count,
        tri_count=m.tri_count,
        vertices=vertices,
        indices=m.indices,
        bones=m.bones,
        weights_on_vertex=w,
    )


def _node_parent_map(nodes):
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i
    return parent


def animation_from_gltf(m: GltfModel) -> "animmod.AnimFile":
    """Rebuild a Disciples 3 `.a` animation file from the glTF animation.

    The `.a` records are exactly the nodes targeted by the glTF animation
    channels (Root + every animated bone), in hierarchy (depth-first) order,
    with parents resolved from the node tree.  Each frame is
    ``[quat(x,y,z,w) + translation(x,y,z)]`` (7 floats), matching what dis3tool
    writes into `bones_rotate` / `bones_translate`.
    """
    if not m.animation:
        return animmod.AnimFile()
    parent = _node_parent_map(m.nodes)

    # Some dis3tool exports reference animation nodes that are outside the
    # node array (e.g. Wildboar targets node index 37 while nodes are 0..36).
    # Drop those so the rebuild never indexes out of range.
    n_nodes = len(m.nodes)
    animated = {i for i in m.anim_channels.keys() if 0 <= i < n_nodes}

    # root of the skeleton: the most senior animated node (usually 'Root')
    roots = [i for i in animated if parent.get(i) not in animated]
    roots.sort()

    # depth-first walk over animated nodes, guarding against bad children refs
    order = []
    seen = set()

    def dfs(i):
        if i in seen or not (0 <= i < n_nodes):
            return
        seen.add(i)
        order.append(i)
        for c in m.nodes[i].get("children", []):
            if c in animated and 0 <= c < n_nodes:
                dfs(c)

    for r in roots:
        dfs(r)

    frames = []
    for i in order:
        name = m.nodes[i].get("name", f"bone{i}")
        # parent name within the animated set, or 'Scene Root'
        p = parent.get(i)
        pname = m.nodes[p].get("name", "Scene Root") if p in animated else "Scene Root"
        ch = m.anim_channels.get(i, {})
        rotations = ch.get("rotation") or [(0.0, 0.0, 0.0, 1.0)] * m.frame_count
        translations = ch.get("translation") or [(0.0, 0.0, 0.0)] * m.frame_count
        samples = []
        nf = max(len(rotations), len(translations))
        for k in range(nf):
            r = rotations[k] if k < len(rotations) else (0.0, 0.0, 0.0, 1.0)
            t = translations[k] if k < len(translations) else (0.0, 0.0, 0.0)
            samples.append((r[0], r[1], r[2], r[3], t[0], t[1], t[2]))
        frames.append((name, pname, samples))

    return animmod.build_anim(frames, len(m.frames))
