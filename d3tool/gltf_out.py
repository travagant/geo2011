"""Forward exporter: original Disciples 3 `.g` / `.a` -> glTF 2.0.

This is the inverse of :mod:`d3tool.gltf` (which reads a dis3tool glTF and
rebuilds the GM files).  It produces a glTF that closely mirrors what the
*dis3tool* plugin (``geo2011.dle``) writes, so a D3 asset can be used in
standard glTF viewers/engines and validated against the reference exports.

Coordinate-system mapping (confirmed against the bundled units):

* ``.g`` vertex  ==  glTF ``POSITION`` / ``NORMAL`` / ``TEXCOORD_0``.
* ``.g`` bone descriptor matrix ``i`` ==  glTF ``skins[0].inverseBindMatrices``
  (same order as ``skins[0].joints``).
* ``.a`` bone rest frame ``[quat, trans]`` ==  the glTF node ``rotation`` +
  ``translation``.
* ``.a`` per-frame samples == the glTF animation channel outputs.
"""
from __future__ import annotations

import json
import struct
from typing import Dict, List, Optional, Tuple

from .anim import AnimFile, BoneAnim
from .model import SkinnedMesh


def _u32(idx: int) -> bytes:
    return struct.pack("<I", idx)


def node_hierarchy(bones: List[BoneAnim]) -> Tuple[Dict[str, int], Dict[str, str], List[str]]:
    """Return ``(children_map, parent_map, roots)`` for the animated bones."""
    children: Dict[str, List[str]] = {b.name: [] for b in bones}
    parent: Dict[str, str] = {b.name: b.parent for b in bones}
    for b in bones:
        p = b.parent
        if p in children and p != b.name:
            children[p].append(b.name)
    roots = [b.name for b in bones if b.parent not in children]
    if not roots:
        roots = [bones[0].name]
    # ensure all roots lead with a stable seed
    from collections import OrderedDict
    order: List[str] = []
    seen = set()

    def dfs(nm: str):
        if nm in seen:
            return
        seen.add(nm)
        order.append(nm)
        for c in children.get(nm, []):
            dfs(c)

    for r in roots:
        dfs(r)
    for b in bones:
        if b.name not in seen:
            dfs(b.name)
    return children, parent, order


def write_gltf(
    mesh: SkinnedMesh,
    anim: Optional[AnimFile] = None,
    output_name: str = "character",
    texture: Optional[str] = None,
) -> Tuple[bytes, dict]:
    """Render a glTF 2.0 document + its binary buffer for a GM mesh.

    Returns ``(bin_bytes, gltf)``.  The ``gltf`` layout mimics the dis3tool
    export (accessors 0..9: indices, POSITION, NORMAL, TEXCOORD_0, WEIGHTS_0,
    JOINTS_0, inverseBindMatrices, frames, bones_rotate, bones_translate).
    """
    n_verts = len(mesh.vertices)
    bones = mesh.bones

    # ---- interleaved vertex data (stride 52) ----
    vbuf = bytearray()
    for v in mesh.vertices:
        vbuf += struct.pack(
            "<3f3f2f4f4B",
            *v.position, *v.normal, *v.uv, *v.gltf_weights,
            *[int(x) & 0xFF for x in v.gltf_joints],
        )
    ibuf = b"".join(struct.pack("<I", x) for x in mesh.indices)

    # ---- inverse bind matrices (bone descriptors) ----
    bone_bytes = bytearray()
    for b in bones:
        bone_bytes += struct.pack("<16f", *b.matrix)

    # ---- animation channel data ----
    anim_bones: List[BoneAnim] = anim.bones if anim and anim.bones else []
    n_frames = anim.frame_count if anim else 0
    frames: List[float] = []
    rot: List[float] = []
    tra: List[float] = []
    if anim_bones:
        n_frames = anim.frame_count or max(len(b.frames) for b in anim_bones) or 1
        # times: 0 .. (n_frames-1) / (n_frames-1) unless a single frame
        frames = [k / (n_frames - 1) for k in range(n_frames)] if n_frames > 1 \
            else [0.0]
        for b in anim_bones:
            for k in range(n_frames):
                fr = b.frames[k] if k < len(b.frames) else b.rest[:7]
                rot.extend(fr[0:4])
                tra.extend(fr[4:7])

    # ---- buffer sections ----
    idx_off = 0
    idx_len = len(ibuf)
    vtx_off = idx_len
    vtx_len = len(vbuf)
    bone_off = vtx_off + vtx_len
    bone_len = len(bone_bytes)
    fr_off = bone_off + bone_len
    fr_len = len(frames) * 4
    rot_off = fr_off + fr_len
    rot_len = len(rot) * 4
    tra_off = rot_off + rot_len
    tra_len = len(tra) * 4
    total = tra_off + tra_len

    buf = bytearray(total)
    buf[idx_off:idx_off + idx_len] = ibuf
    buf[vtx_off:vtx_off + vtx_len] = vbuf
    buf[bone_off:bone_off + bone_len] = bone_bytes
    for i, f in enumerate(frames):
        struct.pack_into("<f", buf, fr_off + i * 4, f)
    for i, f in enumerate(rot):
        struct.pack_into("<f", buf, rot_off + i * 4, f)
    for i, f in enumerate(tra):
        struct.pack_into("<f", buf, tra_off + i * 4, f)

    bufferViews = [
        {"name": "mesh_indexes", "buffer": 0, "byteOffset": idx_off,
         "byteLength": idx_len, "target": 34963},
        {"name": "mesh_vertexes", "buffer": 0, "byteOffset": vtx_off,
         "byteLength": vtx_len, "byteStride": 52, "target": 34962},
        {"name": "mesh_bones", "buffer": 0, "byteOffset": bone_off,
         "byteLength": bone_len},
        {"name": "frames", "buffer": 0, "byteOffset": fr_off,
         "byteLength": fr_len},
        {"name": "bones_rotate", "buffer": 0, "byteOffset": rot_off,
         "byteLength": rot_len},
        {"name": "bones_translate", "buffer": 0, "byteOffset": tra_off,
         "byteLength": tra_len},
    ]

    accessors = [
        {"bufferView": 0, "componentType": 5125, "count": len(mesh.indices),
         "type": "SCALAR"},
        {"bufferView": 1, "componentType": 5126, "count": n_verts,
         "type": "VEC3", "byteOffset": 0},
        {"bufferView": 1, "componentType": 5126, "count": n_verts,
         "type": "VEC3", "byteOffset": 12},
        {"bufferView": 1, "componentType": 5126, "count": n_verts,
         "type": "VEC2", "byteOffset": 24},
        {"bufferView": 1, "componentType": 5126, "count": n_verts,
         "type": "VEC4", "byteOffset": 32},
        {"bufferView": 1, "componentType": 5121, "count": n_verts,
         "type": "VEC4", "byteOffset": 48},
        {"bufferView": 2, "componentType": 5126, "count": len(bones),
         "type": "MAT4"},
    ]
    if anim_bones:
        accessors += [
            {"bufferView": 3, "componentType": 5126, "count": n_frames,
             "type": "SCALAR"},
            {"bufferView": 4, "componentType": 5126,
             "count": len(anim_bones) * n_frames, "type": "VEC4"},
            {"bufferView": 5, "componentType": 5126,
             "count": len(anim_bones) * n_frames, "type": "VEC3"},
        ]

    # ---- nodes ----
    node_list: List[dict] = [{"name": mesh.name}]
    name_to_idx: Dict[str, int] = {}
    if anim_bones:
        children, parent, order = node_hierarchy(anim_bones)
        # node 0 = mesh; skeleton nodes indices 1..N in hierarchy order
        for off, nm in enumerate(order):
            name_to_idx[nm] = 1 + off
        # build each bone node with rest TRS and children
        bmap = {b.name: b for b in anim_bones}
        for nm in order:
            b = bmap[nm]
            rest = b.frames[0][:7] if b.frames else b.rest[:7]
            node = {"name": nm,
                    "rotation": list(rest[0:4]),
                    "translation": list(rest[4:7])}
            kids = [name_to_idx[c] for c in children.get(nm, [])]
            if kids:
                node["children"] = kids
            node_list.append(node)
        root = order[0] if order else None
        if root is not None:
            node_list[0]["children"] = [name_to_idx[root]]
        # add any skin bones not animated as extra nodes (rare)
        for bi, b in enumerate(bones):
            if b.name not in name_to_idx:
                idx = len(node_list)
                name_to_idx[b.name] = idx
                node_list.append({"name": b.name})
    else:
        # no animation: minimal skeleton from skin bones
        name_to_idx = {b.name: i + 1 for i, b in enumerate(bones)}
        children: Dict[str, list] = {b.name: [] for b in bones}
        parent = {b.name: b.parent for b in bones}
        for b in bones:
            if b.parent in name_to_idx and b.parent != b.name:
                children[b.parent].append(b.name)
        for b in bones:
            node = {"name": b.name, "rotation": [0.0, 0.0, 0.0, 1.0],
                    "translation": [0.0, 0.0, 0.0]}
            kids = [name_to_idx[c] for c in children.get(b.name, [])]
            if kids:
                node["children"] = kids
            node_list.append(node)
        node_list[0]["children"] = [1]

    # ---- skin ----
    skin_joints = [name_to_idx.get(b.name, 1 + i) for i, b in enumerate(bones)]
    node_list[0]["skin"] = 0
    skin = {"joints": skin_joints, "inverseBindMatrices": 6}

    # ---- animation channels ----
    channels = []
    samplers = []
    if anim_bones:
        frames_acc = 7
        rot_acc = 8
        tra_acc = 9
        for b in anim_bones:
            nidx = name_to_idx.get(b.name, 1)
            base = len(samplers)
            samplers.append({"input": frames_acc, "output": rot_acc,
                             "interpolation": "LINEAR"})
            channels.append({"sampler": base,
                             "target": {"node": nidx, "path": "rotation"}})
            samplers.append({"input": frames_acc, "output": tra_acc,
                             "interpolation": "LINEAR"})
            channels.append({"sampler": base + 1,
                             "target": {"node": nidx, "path": "translation"}})

    doc = {
        "asset": {"version": "2.0", "generator": "d3tool (geo2011 reverse)"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": node_list,
        "skins": [skin],
        "meshes": [{
            "name": mesh.name,
            "primitives": [{
                "attributes": {"POSITION": 1, "NORMAL": 2, "TEXCOORD_0": 3,
                               "WEIGHTS_0": 4, "JOINTS_0": 5},
                "indices": 0,
                "material": 0,
            }],
        }],
        "accessors": accessors,
        "bufferViews": bufferViews,
        "buffers": [{"uri": output_name + ".bin", "byteLength": total}],
    }
    if anim_bones:
        doc["animations"] = [{"channels": channels, "samplers": samplers}]
    if texture:
        pbr = {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                        "metallicFactor": 0.0},
               "doubleSided": True}
        doc["materials"] = [pbr]
        doc["images"] = [{"uri": texture}]
        doc["textures"] = [{"source": 0}]

    return bytes(buf), doc


def write_gltf_to(path: str, mesh: SkinnedMesh, anim: Optional[AnimFile] = None,
                  texture: Optional[str] = None) -> Tuple[str, str]:
    """Write ``<base>.gltf`` and ``<base>.bin``; returns (gltf_path, bin_path)."""
    import os
    base = os.path.splitext(path)[0]
    bin_bytes, doc = write_gltf(mesh, anim, os.path.basename(base), texture)
    bin_path = base + ".bin"
    with open(bin_path, "wb") as fh:
        fh.write(bin_bytes)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return path, bin_path
