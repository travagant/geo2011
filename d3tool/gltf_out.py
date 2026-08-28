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
import math
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
        # Some source `.g` files carry NaN normals on a few vertices (e.g.
        # Zombie LOD).  glTF does not allow NaN, so substitute a safe normal.
        nrm = v.normal
        if any(math.isnan(x) or math.isinf(x) for x in nrm):
            nrm = (0.0, 0.0, 1.0)
        # Normalise the 4 weight slots so they sum to exactly 1.0 as float32,
        # matching the reference dis3tool export.  The `.g` stores weights as
        # float32 and keeps a tiny residual, so the naive sum can read e.g.
        # 0.9999995, which the Khronos validator rejects.  We drop weight
        # components that are effectively zero, then renormalise.
        w = list(v.gltf_weights)
        # drop negligible influences (the real dis3tool exporter collapses them)
        w = [x if x > 1e-4 else 0.0 for x in w]
        s = float(sum(w))
        if s > 0.0:
            w = [x / s for x in w]
        # float32-quantise so the stored buffer is exact
        w = [struct.unpack("<f", struct.pack("<f", x))[0] for x in w]
        vbuf += struct.pack(
            "<3f3f2f4f4B",
            *v.position, *nrm, *v.uv, *w,
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
        if n_frames > 1:
            step = 1.0 / (n_frames - 1)
            frames = [k * step for k in range(n_frames)]
        else:
            frames = [0.0]
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
    ]
    if anim_bones:
        bufferViews += [
            {"name": "frames", "buffer": 0, "byteOffset": fr_off,
             "byteLength": fr_len},
            {"name": "bones_rotate", "buffer": 0, "byteOffset": rot_off,
             "byteLength": rot_len},
            {"name": "bones_translate", "buffer": 0, "byteOffset": tra_off,
             "byteLength": tra_len},
        ]

    # min/max for the POSITION accessor (required by glTF)
    xs = [v.position[0] for v in mesh.vertices]
    ys = [v.position[1] for v in mesh.vertices]
    zs = [v.position[2] for v in mesh.vertices]
    pos_min = [min(xs), min(ys), min(zs)]
    pos_max = [max(xs), max(ys), max(zs)]

    accessors = [
        {"bufferView": 0, "componentType": 5125, "count": len(mesh.indices),
         "type": "SCALAR"},
        {"bufferView": 1, "componentType": 5126, "count": n_verts,
         "type": "VEC3", "byteOffset": 0, "min": pos_min, "max": pos_max},
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
        fmin = [min(frames)] if frames else [0.0]
        fmax = [max(frames)] if frames else [0.0]
        accessors += [
            {"bufferView": 3, "componentType": 5126, "count": n_frames,
             "type": "SCALAR", "min": fmin, "max": fmax},
        ]
        # one rotation (VEC4) + one translation (VEC3) accessor per bone, each
        # count=n_frames, sampled at distinct offsets into shared bufferViews.
        for i in range(len(anim_bones)):
            accessors.append({
                "bufferView": 4, "componentType": 5126, "count": n_frames,
                "type": "VEC4", "byteOffset": i * n_frames * 16,
            })
            accessors.append({
                "bufferView": 5, "componentType": 5126, "count": n_frames,
                "type": "VEC3", "byteOffset": i * n_frames * 12,
            })

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
        # dis3tool keeps the mesh node childless and puts the skeleton root as a
        # *sibling* in the scene (scene nodes = [mesh, skeleton_root]).  Nesting
        # the skeleton under the mesh makes the Khronos validator evaluate the
        # skin differently and flags the same joint as duplicated.
        root = order[0] if order else None
        # add any skin bones not animated as extra nodes (rare)
        for bi, b in enumerate(bones):
            if b.name not in name_to_idx:
                idx = len(node_list)
                name_to_idx[b.name] = idx
                node_list.append({"name": b.name})
        scene_children_idx = [name_to_idx[root]] if root is not None else []
    else:
        # no animation: the `.g` does not store a hierarchy (that lives in the
        # `.a`), so build a flat skeleton as a scene sibling of the mesh node.
        name_to_idx = {b.name: i + 1 for i, b in enumerate(bones)}
        for b in bones:
            node = {"name": b.name, "rotation": [0.0, 0.0, 0.0, 1.0],
                    "translation": [0.0, 0.0, 0.0]}
            node_list.append(node)
        scene_children_idx = [name_to_idx[bones[0].name]] if bones else []

    # ---- skin ----
    skin_joints = [name_to_idx.get(b.name, 1 + i) for i, b in enumerate(bones)]
    node_list[0]["mesh"] = 0
    node_list[0]["skin"] = 0
    skin = {"joints": skin_joints, "inverseBindMatrices": 6}
    # ---- animation channels ----
    channels = []
    samplers = []
    if anim_bones:
        frames_acc = 7
        for i, b in enumerate(anim_bones):
            nidx = name_to_idx.get(b.name, 1)
            rot_acc = 8 + 2 * i
            tra_acc = 8 + 2 * i + 1
            base = len(samplers)
            samplers.append({"input": frames_acc, "output": rot_acc,
                             "interpolation": "LINEAR"})
            channels.append({"sampler": base,
                             "target": {"node": nidx, "path": "rotation"}})
            samplers.append({"input": frames_acc, "output": tra_acc,
                             "interpolation": "LINEAR"})
            channels.append({"sampler": base + 1,
                             "target": {"node": nidx, "path": "translation"}})

    prim = {
        "attributes": {"POSITION": 1, "NORMAL": 2, "TEXCOORD_0": 3,
                       "WEIGHTS_0": 4, "JOINTS_0": 5},
        "indices": 0,
    }
    if texture:
        prim["material"] = 0

    doc = {
        "asset": {"version": "2.0", "generator": "d3tool (geo2011 reverse)"},
        "scene": 0,
        # mesh node (0) plus the skeleton root(s) as scene siblings, mirroring
        # dis3tool.  This keeps the skin graph fully reachable from the scene.
        "scenes": [{"nodes": [0] + scene_children_idx}],
        "nodes": node_list,
        "skins": [skin],
        "meshes": [{"name": mesh.name, "primitives": [prim]}],
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
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    bin_bytes, doc = write_gltf(mesh, anim, os.path.basename(base), texture)
    bin_path = base + ".bin"
    with open(bin_path, "wb") as fh:
        fh.write(bin_bytes)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return path, bin_path


def validate_gltf(path: str, base_dir: Optional[str] = None) -> Tuple[int, int, int]:
    """Lightweight, pure-Python structural self-check of a glTF 2.0 document.

    Reports ``(errors, warnings, info)`` counts for the invariants my own
    exporter guarantees (buffer/accessor count matches, skin joints in range,
    animation sampler counts, node/mesh references).  It is *not* a full
    Khronos validator but catches the structural mistakes a round-trip tool can
    make.
    """
    import json
    import os
    base_dir = base_dir or os.path.dirname(os.path.abspath(path))
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    errors = warnings = info = 0
    buf_uri = doc.get("buffers", [{}])[0].get("uri", "")
    bin_data = b""
    if buf_uri:
        bp = os.path.join(base_dir, buf_uri)
        if os.path.exists(bp):
            bin_data = open(bp, "rb").read()
        else:
            errors += 1

    nacc = len(doc.get("accessors", []))
    # every accessor must reference a valid bufferView and its data must fit
    # within that bufferView.  accessor.byteOffset is relative to the view;
    # the view's own byteOffset is relative to the buffer.
    for i, a in enumerate(doc.get("accessors", [])):
        bv = doc.get("bufferViews", [])[a["bufferView"]] if "bufferView" in a else None
        if bv is None:
            errors += 1
            continue
        ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
        size = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}[a["componentType"]]
        need = a["count"] * ncomp * size
        off = a.get("byteOffset", 0)
        if off + need > bv["byteLength"]:
            errors += 1
    # skin joints must be valid node indices
    for s in doc.get("skins", []):
        for j in s["joints"]:
            if j >= len(doc.get("nodes", [])):
                errors += 1
    # WEIGHTS_0 must sum to 1.0 per vertex (spec REQUIRES normalized weights).
    # The Khronos validator flags non-normalized weight sums, which our own
    # structural check must catch too.  We also mirror its dedup-by-joint rule:
    # when the same joint index appears in several influence slots, the weights
    # for those slots are summed (a vertex that maps two influences to the same
    # joint only "uses" one joint, so the leftover weight must still total 1).
    def _read_float_accessor(index: int, ncomp: int):
        a = doc["accessors"][index]
        bv = doc["bufferViews"][a["bufferView"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride", ncomp * 4)
        return [
            struct.unpack_from("<" + "f" * ncomp, bin_data, off + i * stride)
            for i in range(a["count"])
        ]

    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            w_idx = attrs.get("WEIGHTS_0")
            j_idx = attrs.get("JOINTS_0")
            if w_idx is None:
                continue
            weights = _read_float_accessor(w_idx, 4)
            joints = _read_float_accessor(j_idx, 4) if j_idx is not None else None
            for i, wrow in enumerate(weights):
                if joints is not None:
                    sums = {}
                    for w, j in zip(wrow, joints[i]):
                        jv = int(j)
                        sums[jv] = sums.get(jv, 0.0) + w
                    total = sum(sums.values())
                else:
                    total = sum(wrow)
                if abs(total - 1.0) > 1e-4:
                    errors += 1
    # animation sampler inputs/outputs must have matching counts, and channel
    # output types must match the path (rotation=VEC4, translation=VEC3).
    for anim in doc.get("animations", []):
        path_of = {c["sampler"]: c["target"]["path"] for c in anim["channels"]}
        for idx, smp in enumerate(anim["samplers"]):
            i_acc = doc["accessors"][smp["input"]]
            o_acc = doc["accessors"][smp["output"]]
            if i_acc["count"] != o_acc["count"]:
                errors += 1
            want = path_of.get(idx)
            if want == "rotation" and o_acc["type"] != "VEC4":
                errors += 1
            elif want == "translation" and o_acc["type"] != "VEC3":
                errors += 1
    # mesh node must reference an existing mesh
    for n in doc.get("nodes", []):
        if "mesh" in n and n["mesh"] >= len(doc.get("meshes", [])):
            errors += 1
        if "skin" in n and n["skin"] >= len(doc.get("skins", [])):
            errors += 1
    info = max(0, nacc)
    return errors, warnings, info
