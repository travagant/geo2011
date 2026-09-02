"""Read a dis3tool glTF export and convert it into the neutral model
(:class:`~d3tool.model.GltfModel`) and ultimately a
:class:`~d3tool.model.SkinnedMesh` that can be written back to the GM `.g`
format (reverse export).
"""
from __future__ import annotations

import json
import math
import struct
from typing import Dict, List, Optional, Tuple, cast

from . import anim as animmod
from . import frame as framemod
from .model import (Bone, GltfModel, MeshPart, SkinnedMesh, Vertex,
                    pack_weights_joints)

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


def _detect_sub_weights(wts, jts) -> int:
    """Slot count for a compound sub-mesh.

    A ``w = 1`` weapon sub (every vertex rigidly bound: single full weight,
    all other joints zero) must stay 1-slot; anything with a real second
    joint is at least 2, the rest goes through the usual auto-detect.
    """
    if not wts:
        return 1
    single = True
    for w, j in zip(wts, jts or []):
        if abs(w[0] - 1.0) > 1e-6 or any(abs(x) > 1e-6 for x in w[1:]) \
                or any(jj != 0 for jj in j[1:]):
            single = False
            break
    if single:
        return 1
    return detect_weights_on_vertex(wts)


def _build_vertices(vtf, nrm, uv, wts, jts, rigid: bool, w_slots: int):
    """GM vertices from glTF attribute accessors (the first ``w_slots``
    influence pairs verbatim, mirroring dis3tool's no-re-sort rule)."""
    vertices: List[Vertex] = []
    for i, pos in enumerate(vtf):
        w = wts[i] if wts else (1.0, 0.0, 0.0, 0.0)
        jn = jts[i] if jts else (0, 0, 0, 0)
        weights = list(w[:w_slots])
        bones_idx = [] if rigid else list(jn[:w_slots])
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
    return vertices


def _texture_uri(gltf, texture_index: int) -> str:
    """Image URI of a texture, renamed back to the historical ``.tga`` the
    GM attribute block carries (dis3tool renamed ``.tga`` -> ``.dds`` on
    forward export)."""
    textures = gltf.get("textures", [])
    images = gltf.get("images", [])
    if texture_index >= len(textures):
        return ""
    src = textures[texture_index].get("source")
    if src is None or src >= len(images):
        return ""
    uri = images[src].get("uri", "")
    if uri.endswith(".dds"):
        uri = uri[:-4] + ".tga"
    return uri


def _mesh_material_maps(gltf, mesh_json):
    """Diffuse/lightmap texture names of a mesh's first primitive."""
    diffuse = lightmap = ""
    pr = mesh_json["primitives"][0]
    materials = gltf.get("materials", [])
    if "material" in pr and pr["material"] < len(materials):
        mat = materials[pr["material"]]
        pbr = mat.get("pbrMetallicRoughness") or {}
        if "baseColorTexture" in pbr:
            diffuse = _texture_uri(gltf, pbr["baseColorTexture"]["index"])
        if "normalTexture" in mat:
            lightmap = _texture_uri(gltf, mat["normalTexture"]["index"])
    return diffuse, lightmap


def _morph_targets_raw(gltf: dict, buf: bytes, targets) -> Tuple[str, List[bytes]]:
    """Verbatim per-frame POSITION bytes of a mesh's morph targets.

    dis3tool writes each baked `.a` morph frame as one glTF target whose
    POSITION accessor is the raw frame positions unmodified; the target's
    bufferView is named ``morph_<stream>_<k>``, which names the `.a` stream.
    """
    name = ""
    frames: List[bytes] = []
    for t in targets or []:
        ai = t.get("POSITION")
        if ai is None:
            return "", []
        acc = gltf["accessors"][ai]
        bv = gltf["bufferViews"][acc["bufferView"]]
        off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        count = acc["count"]
        stride = bv.get("byteStride")
        if stride in (None, 12):
            frames.append(bytes(buf[off:off + count * 12]))
        else:
            frames.append(b"".join(
                buf[off + k * stride:off + k * stride + 12]
                for k in range(count)))
        if not name:
            nm = bv.get("name", "")
            if nm.startswith("morph_") and nm.rsplit("_", 1)[1].isdigit():
                name = nm[len("morph_"):].rsplit("_", 1)[0]
    return name, frames


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

    # ---- node bookkeeping (works for both layouts) ----
    # dis3tool puts the mesh nodes first, parentless at the scene root, each
    # with its OWN small skin (the `.g` part's bone table).  A Blender
    # re-save reorders the nodes (bones first), parents the mesh nodes under
    # the armature root and merges every per-mesh skin into ONE armature-wide
    # skin.  Everything below is driven by "the node that carries mesh i"
    # instead of by array position, so both layouts load.
    nodes = gltf.get("nodes", [])
    node_parent: Dict[int, int] = {}
    for ni, nd in enumerate(nodes):
        for c in nd.get("children", []):
            node_parent.setdefault(c, ni)

    def _node_global(idx: int, _depth: int = 0) -> list:
        """Global (row-major, column-vector) matrix of a node."""
        if _depth > 64 or idx is None:
            return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
        nd = nodes[idx] if 0 <= idx < len(nodes) else {}
        M = framemod.trs_matrix(nd.get("rotation"), nd.get("translation"),
                                nd.get("scale")) if "matrix" not in nd \
            else framemod.cmaj16(nd["matrix"])
        p = node_parent.get(idx)
        return framemod.mm(_node_global(p, _depth + 1), M) if p is not None else M

    mesh_nodes: Dict[int, Tuple[int, Optional[int]]] = {}   # mesh idx -> (node, skin)
    for ni, nd in enumerate(nodes):
        if "mesh" in nd and nd["mesh"] not in mesh_nodes:
            mesh_nodes[nd["mesh"]] = (ni, nd.get("skin"))
    skins = gltf.get("skins", [])
    skins_by_users: Dict[int, int] = {}
    for _ni, sk in mesh_nodes.values():
        if sk is not None:
            skins_by_users[sk] = skins_by_users.get(sk, 0) + 1
    shared_skin = any(v > 1 for v in skins_by_users.values())
    mesh_transformed = any(
        not framemod.is_identity(_node_global(ni))
        for ni, _sk in mesh_nodes.values())
    blender_layout = bool(skins) and (shared_skin or mesh_transformed)

    def _skin_tables(skin_i: Optional[int]):
        """(joint names, raw IBM 16-float tuples) of a skin."""
        if skin_i is None or skin_i >= len(skins):
            return [], []
        sk = skins[skin_i]
        names = [nodes[j].get("name", f"bone{k}")
                 for k, j in enumerate(sk["joints"])]
        flat = _read_accessor(gltf, buf, sk["inverseBindMatrices"])
        ibms = [tuple(x) for x in flat]
        return names, ibms

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
    # dis3tool emits *no* `skins` (and no WEIGHTS_0/JOINTS_0) for a rigid,
    # animation-less export — three bundled references do this (Blacknaga,
    # CityGuard, WaterSnake_sea) and so does our own compound writer.  Such a
    # glTF is a plain static mesh: no bone descriptors to read back.
    # The MAIN mesh's skin is the skin of the NODE carrying meshes[0] (a
    # Blender re-save merges everything into skins[0], but keeps the mesh
    # nodes' `skin` pointers honest).
    main_node, main_skin_i = mesh_nodes.get(0, (None, None))
    skin = skins[main_skin_i] if (main_skin_i is not None
                                  and main_skin_i < len(skins)) else None
    bone_indices: Dict[str, int] = {}
    joints = skin["joints"] if skin else []
    joint_names = [nodes[j].get("name", f"bone{i}") for i, j in enumerate(joints)]
    for i, name in enumerate(joint_names):
        bone_indices[name] = i

    bones: List[Bone] = []
    if skin is not None:
        # The GM bone descriptor array is *exactly* the glTF
        # inverseBindMatrices, in the same order as the skin joints.
        inverse_bind = _read_accessor(gltf, buf, skin["inverseBindMatrices"])
        for i, j in enumerate(joints):
            name = nodes[j].get("name", f"bone{i}")
            bones.append(Bone(name, tuple(inverse_bind[i])))

    # ---- vertices ----
    # A rigid export (no skin, no WEIGHTS_0/JOINTS_0) is a plain static mesh:
    # the GM record carries no influence slots at all (``weights_on_vertex``
    # 1, the 40-byte record).  Writing it as a 2-slot skin bound to a
    # non-existent joint 0 would leave dangling indices behind.
    rigid = skin is None and wts is None and jts is None
    # 0 means "auto-detect": derive the slot count from the actual data so we
    # never drop a real influence (this is what fixes Wildboar's 3-slot skin).
    if weights_on_vertex:
        w_slots = weights_on_vertex
    elif rigid:
        w_slots = 1
    else:
        w_slots = detect_weights_on_vertex(wts)
    vertices = _build_vertices(vtf, nrm, uv, wts, jts, rigid, w_slots)

    # ---- compound sub-meshes (meshes[1:]) ----
    # A compound container exports one glTF mesh per sub-mesh, each with its
    # own skin (weapon 1 joint, body 39, hair 5, ...).  They are loaded as
    # standalone sub-models; mesh_to_skinned turns them into `.g` parts.
    submodels: List[GltfModel] = []
    for mi, mesh_json in enumerate(gltf.get("meshes", [])[1:], start=1):
        spr = mesh_json["primitives"][0]
        sattrs = spr["attributes"]
        svtf = _read_accessor(gltf, buf, sattrs["POSITION"])
        snrm = _read_accessor(gltf, buf, sattrs["NORMAL"])
        suv = (_read_accessor(gltf, buf, sattrs["TEXCOORD_0"])
               if "TEXCOORD_0" in sattrs else [(0.0, 0.0)] * len(svtf))
        swts = (_read_accessor(gltf, buf, sattrs["WEIGHTS_0"])
                if "WEIGHTS_0" in sattrs else None)
        sjts = (_read_accessor(gltf, buf, sattrs["JOINTS_0"])
                if "JOINTS_0" in sattrs else None)
        sindices = [x[0] for x in _read_accessor(gltf, buf, spr["indices"])]
        slm = (_read_accessor(gltf, buf, sattrs["TEXCOORD_1"])
               if "TEXCOORD_1" in sattrs else None)
        slm_uv = b"" if slm is None else struct.pack(
            f"<{2 * len(slm)}f", *[x for uv2 in slm for x in uv2])

        # this sub's skin = the skin of the node carrying the mesh
        sbones: List[Bone] = []
        sub_node, skin_i = mesh_nodes.get(mi, (None, None))
        if skin_i is not None and gltf.get("skins"):
            skin_j = gltf["skins"][skin_i]
            sjoints = skin_j["joints"]
            sinv = _read_accessor(gltf, buf, skin_j["inverseBindMatrices"])
            for k, jn_ in enumerate(sjoints):
                sbones.append(Bone(nodes[jn_].get("name", f"bone{k}"),
                                   tuple(sinv[k])))
        # a skin SHARED with another mesh node is the merged Blender skin:
        # it is NOT this part's bone table (the table is re-derived from the
        # actually-used joints / the `.g` donor on reverse export)
        sjoint_names, sibms = _skin_tables(skin_i)
        if skins_by_users.get(skin_i, 0) > 1:
            sbones = []
        snode_matrix = None
        if sub_node is not None and not framemod.is_identity(
                _node_global(sub_node)):
            snode_matrix = _node_global(sub_node)

        smorph_name, starget_positions = _morph_targets_raw(
            gltf, buf, spr.get("targets"))
        sdiffuse, slightmap = _mesh_material_maps(gltf, mesh_json)
        srigid = swts is None and sjts is None and not sbones
        sw_slots = 1 if srigid else _detect_sub_weights(swts, sjts)
        svertices = _build_vertices(svtf, snrm, suv, swts, sjts,
                                    srigid, sw_slots)
        submodels.append(GltfModel(
            mesh_name=mesh_json.get("name", ""),
            vertex_count=len(svtf),
            tri_count=len(sindices) // 3,
            vertices=svertices,
            indices=sindices,
            nodes=nodes,
            bones=sbones,
            material_diffuse=sdiffuse,
            lightmap=slightmap,
            lm_uv=slm_uv,
            morph=bool(spr.get("targets")),
            weights_on_vertex=sw_slots,
            rigid=srigid,
            accessor_wj=(list(zip(swts, sjts))
                         if swts is not None and sjts is not None else []),
            morph_name=smorph_name,
            target_positions=starget_positions,
            skin_joint_names=sjoint_names,
            skin_ibms=sibms,
            node_matrix=snode_matrix,
        ))

    main_morph_name, main_target_positions = _morph_targets_raw(
        gltf, buf, prim.get("targets"))

    # the mesh node's name, not nodes[0]: a Blender re-save reorders the
    # node array (bones first), and nodes[0] would then be a bone name
    if main_node is not None:
        mesh_name = nodes[main_node].get("name", "") or mesh.get("name", "")
    elif nodes and "mesh" in nodes[0]:
        mesh_name = nodes[0].get("name", "")
    else:
        mesh_name = mesh.get("name", "")
    import os
    geometry_file = os.path.basename(path)
    if geometry_file.endswith(".gltf"):
        geometry_file = geometry_file[: -len(".gltf")]
    anim = None
    frames: List[float] = []
    channels: Dict[int, dict] = {}
    chan_times: Dict[int, dict] = {}
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
            tin = _read_accessor(gltf, buf, anim["samplers"][ch["sampler"]]["input"])
            channels.setdefault(node, {})[path] = out
            chan_times.setdefault(node, {})[path] = [x[0] for x in tin]

    main_jnames, main_ibms = _skin_tables(main_skin_i)
    main_node_matrix = None
    if main_node is not None and not framemod.is_identity(
            _node_global(main_node)):
        main_node_matrix = _node_global(main_node)
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
        anim_times=chan_times,
        weights_on_vertex=w_slots,
        rigid=rigid,
        morph=bool(prim.get("targets")),
        accessor_wj=(list(zip(wts, jts))
                     if wts is not None and jts is not None else []),
        morph_name=main_morph_name,
        target_positions=main_target_positions,
        submodels=submodels,
        skin_joint_names=main_jnames,
        skin_ibms=main_ibms,
        node_matrix=main_node_matrix,
        blender_layout=blender_layout,
    )
    return model


def _restore_from_donor(vertices, gltf_vertices, donor_vertices,
                        rigid: bool, accessor_wj=()) -> None:
    """Adopt the donated original `.g` influence data where it is provably
    the data the glTF came from.

    The forward export is lossy in two ways -- a positive weight complement
    is merged into a duplicate-bone lane, and a joint whose weight is
    exactly 0.0 is masked to 0 -- so a reverse-built vertex cannot always
    reproduce the original bytes.  The donated original can, but only when
    the user has not edited the influences since: the check re-packs the
    donor's stored weights/bones and adopts them only when the result equals
    the glTF lanes bit-for-bit.  Per-vertex diffuse has no glTF expression
    at all, so it is always adopted.  A rigid export carries no influence
    accessors whatsoever, so there the donor wins outright.
    """
    if len(donor_vertices) != len(vertices):
        return
    for i, (dst, src) in enumerate(zip(vertices, donor_vertices)):
        dst.diffuse = src.diffuse
        if rigid:
            dst.stored_weights = src.stored_weights
            dst.bones = src.bones
            continue
        pw, pj = pack_weights_joints(tuple(src.stored_weights),
                                     tuple(src.bones))
        if i < len(accessor_wj):
            # ground truth: the glTF accessor lanes themselves (a re-pack of
            # the truncated model vertex would not round-trip for the
            # bone-0 merge class)
            gw, gj = accessor_wj[i]
        else:
            gv = gltf_vertices[i]
            gw, gj = gv.gltf_weights, gv.gltf_joints
        if pw == gw and pj == gj:
            dst.stored_weights = src.stored_weights
            dst.bones = src.bones


def _blender_q_map(m: "GltfModel", anim_donors):
    """Q frame-change map for a Blender re-save, calibrated on the donor `.a`.

    Returns ({bone name: Q}, warnings)."""
    warns: List[str] = []
    # accept both bare AnimFiles and (name, AnimFile) stream pairs
    donors = [d if not isinstance(d, tuple) else d[1] for d in anim_donors]
    if not donors:
        return {}, ["no donor .a next to the glTF — the Blender frame "
                    "conversion cannot be calibrated"]
    # per-bone local rest matrices: the channel at t=0 when animated (a
    # Blender export's static TRS is the pose at the playhead, not frame 0)
    name_to_node: Dict[str, int] = {}
    for i, nd in enumerate(m.nodes):
        nm = nd.get("name")
        if nm and nm not in name_to_node:
            name_to_node[nm] = i
    parents: Dict[str, str] = {}
    for donor in donors:
        for b in donor.bones:
            parents.setdefault(b.name, b.parent or "")
    locals_b: Dict[str, "framemod.Mat4"] = {}
    for nm, ni in name_to_node.items():
        if nm not in parents:
            continue
        ch = m.anim_channels.get(ni, {})
        tm = m.anim_times.get(ni, {})

        def _flat(path, ncomp):
            vals = ch.get(path) or []
            return [x for tup in vals for x in tup[:ncomp]]

        if "rotation" in ch:
            q = framemod.sample_channel(
                tm.get("rotation") or [0.0], _flat("rotation", 4), 4, 0.0,
                slerp=True)
        else:
            q = m.nodes[ni].get("rotation")
        if "translation" in ch:
            t = framemod.sample_channel(
                tm.get("translation") or [0.0], _flat("translation", 3), 3,
                0.0)
        else:
            t = m.nodes[ni].get("translation")
        sc = m.nodes[ni].get("scale")
        try:
            locals_b[nm] = framemod.trs_matrix(q, t, sc)
        except Exception:  # noqa: BLE001
            continue
    Q = framemod.build_q_map(donors, locals_b, parents)
    if not Q:
        warns.append("frame calibration failed (no common bone names)")
    return Q, warns


def _vertex_key(v) -> tuple:
    return (struct.pack("<3f", *v.position[:3]),
            struct.pack("<2f", *v.uv[:2]))


def _pair_donor_vertices(gltf_vertices, donor_vertices):
    """Pair each glTF vertex with its donor original.

    Returns (index_pairs, by_index): ``by_index`` is True when the two lists
    are the same length and equal position/uv order (the dis3tool layout —
    pairing is positional and bit-exact); otherwise a (pos,uv)->donor-index
    lookup matches Blender's re-ordered / split vertices, and ``index_pairs``
    maps glTF index -> donor index or -1.
    """
    if len(gltf_vertices) == len(donor_vertices) and all(
            _vertex_key(a) == _vertex_key(b)
            for a, b in zip(gltf_vertices, donor_vertices)):
        return list(range(len(gltf_vertices))), True
    table: Dict[tuple, int] = {}
    for j, dv in enumerate(donor_vertices):
        table.setdefault(_vertex_key(dv), j)
    # a second, position-only table catches vertices Blender split at a
    # UV/normal seam (same position, different uv)
    pos_table: Dict[bytes, int] = {}
    for j, dv in enumerate(donor_vertices):
        pos_table.setdefault(struct.pack("<3f", *dv.position[:3]), j)
    pairs = []
    for i, gv in enumerate(gltf_vertices):
        j = table.get(_vertex_key(gv))
        if j is None:
            j = pos_table.get(struct.pack("<3f", *gv.position[:3]), -1)
        pairs.append(j)
    return pairs, False


def _lanes_by_name(weights, joint_ids, joint_names):
    """[(weight_f32, bone_name)] for a vertex's accessor lanes.

    Lanes below 1e-6 are treated as unweighted (Blender's renormalisation
    leaves epsilon residues that must not grow a part's bone table)."""
    out = []
    for w, j in zip(weights, joint_ids):
        if abs(w) > 1e-6 and 0 <= int(j) < len(joint_names):
            out.append((w, joint_names[int(j)]))
    return out


def _remap_slot_vertices(sub: GltfModel, slot, Q, donor_of_bone,
                         frame_convert: bool):
    """Rebuild one donor slot's vertices from its matched glTF mesh.

    A Blender re-save writes JOINTS_0 as indices into the merged
    armature-wide skin; the `.g` slot needs indices into its OWN bone table,
    so every lane is remapped by bone name (bones the slot never listed are
    appended, their descriptor taken from another donor part when one
    carries it).  Positions/normals are converted from the Blender frame
    back into the slot's `.g` frame through joint space.  The donor's
    influence bytes are adopted only where they re-pack to the glTF lanes
    exactly — painted weights always win.

    Returns (vertices, slot_bones, appended_names).
    """
    donor_vertices = slot.vertices
    slot_bones: List[Bone] = list(slot.bones or [])
    local_of = {b.name: i for i, b in enumerate(slot_bones)}
    ibmg_of = {b.name: framemod.cmaj16(b.matrix) for b in slot_bones}
    jnames = sub.skin_joint_names or []
    ibmb_of = {nm: framemod.cmaj16(list(flat))
               for nm, flat in zip(jnames, sub.skin_ibms)}

    pairs, _by_index = _pair_donor_vertices(sub.vertices, donor_vertices)

    K: Dict[str, "framemod.Mat4"] = {}
    appended: List[str] = []

    def k_of(nm: str):
        """Composite map inv(IBMg_slot_j) . Q_j . IBMb_j (built lazily,
        appending missing bones to the slot table on the way)."""
        if nm in K:
            return K[nm]
        if nm not in local_of and nm:
            bone = donor_of_bone.get(nm)
            if bone is None and nm in ibmb_of and nm in Q:
                # any descriptor is engine-consistent (the vertex map
                # absorbs it); Q.IBMb makes the map the identity
                bone = Bone(nm, tuple(framemod.flatten16(
                    framemod.mm(Q[nm], ibmb_of[nm]))))
            if bone is None:
                bone = Bone(nm, tuple(framemod.flatten16(framemod.IDENTITY)))
            slot_bones.append(bone)
            appended.append(nm)
            local_of[nm] = len(slot_bones) - 1
            ibmg_of[nm] = framemod.cmaj16(bone.matrix)
        if nm not in ibmg_of or nm not in ibmb_of or nm not in Q:
            return None
        K[nm] = framemod.mm(framemod.inv4(ibmg_of[nm]),
                            framemod.mm(Q[nm], ibmb_of[nm]))
        return K[nm]

    # pre-build the map for every joint this mesh actually uses
    used: set = set()
    for v in sub.vertices:
        for w, j in zip(v.gltf_weights, v.gltf_joints):
            if abs(w) > 1e-6 and int(j) < len(jnames):
                used.add(jnames[int(j)])
    for nm in sorted(used):
        k_of(nm)

    out: List[Vertex] = []
    for i, v in enumerate(sub.vertices):
        gw = list(v.gltf_weights)
        gj = list(v.gltf_joints)
        lanes = _lanes_by_name(gw, gj, jnames)
        pos = list(v.position[:3])
        nrm = list(v.normal[:3])
        if frame_convert and Q and lanes:
            pos = list(framemod.convert_positions(pos, lanes, K))
            # a normal converts through the dominant joint's rotation
            Mk = K.get(lanes[0][1])
            if Mk is not None:
                rn = [sum(Mk[r][c] * nrm[c] for c in range(3)) for r in range(3)]
                ln = math.sqrt(sum(x * x for x in rn))
                if ln > 1e-12:
                    nrm = [x / ln for x in rn]
        dj = pairs[i] if i < len(pairs) else -1
        src_v = donor_vertices[dj] if 0 <= dj < len(donor_vertices) else None
        diffuse = src_v.diffuse if src_v is not None else v.diffuse
        # weights: glTF lanes -> slot-local bone indices.  Only a lane
        # above the epsilon threshold may grow the slot's bone table; a
        # residue lane maps to joint 0 (its weight is ~0 for the engine).
        stored = list(gw[:-1]) if len(gw) > 1 else [1.0]
        bones_local = []
        for k in range(len(gj)):
            nm = jnames[int(gj[k])] if int(gj[k]) < len(jnames) else ""
            real = nm and abs(gw[k]) > 1e-6
            if real and nm not in local_of:
                k_of(nm)
            bones_local.append(local_of.get(nm, 0) if real else 0)
        # donor adoption where the original provably re-packs to the lanes
        if src_v is not None:
            pw, pj = pack_weights_joints(tuple(src_v.stored_weights),
                                         tuple(src_v.bones))
            donor_lanes = _lanes_by_name(
                pw, pj, [b.name for b in (slot.bones or [])])
            if donor_lanes == lanes and len(src_v.bones) == len(bones_local):
                stored = list(src_v.stored_weights)
                bones_local = list(src_v.bones)
        out.append(Vertex(tuple(pos), tuple(nrm), tuple(v.uv[:2]), diffuse,
                          tuple(stored), tuple(bones_local)))
    return out, slot_bones, appended


def _mesh_to_skinned_blender(m: GltfModel, weights_on_vertex: int,
                             donor: SkinnedMesh, anim_donors) -> SkinnedMesh:
    """Reverse-export a Blender re-saved glTF against the original `.g`.

    A Blender re-save is NOT the dis3tool layout: the per-mesh skins are
    merged into one armature-wide skin, the mesh nodes move under the
    armature root and the meshes reorder in the array.  The rebuild keeps
    the DONOR's container structure (root + parts, their scaffolding and
    bone tables) and feeds each slot the glTF mesh that matches it:

    * by node/mesh name first (the gobj names survive a Blender round-trip),
    * then by the mesh's used-joint NAME set vs the slot's bone names,
    * leftovers pair up in order.

    Vertices are converted back into the `.g` frame (see
    :mod:`d3tool.frame`), joint indices remapped into each slot's own table,
    and the whole result verified against the donor (a low match rate warns
    loudly instead of shipping a silently distorted `.g`).
    """
    Q, qwarns = _blender_q_map(m, anim_donors)
    frame_convert = bool(Q)
    # bone name -> descriptor, from ANY donor part that carries it (the same
    # bone keeps the same descriptor across a compound's tables)
    donor_of_bone: Dict[str, Bone] = {}
    for part in ([donor] + list(donor.parts or [])):
        for b in (part.bones or []):
            donor_of_bone.setdefault(b.name, b)

    # -- the donor's slots, in output order -- #
    class _Slot:
        def __init__(self, holder, is_root):
            self.holder = holder
            self.is_root = is_root
            self.name = holder.name
            self.bone_names = {b.name for b in (holder.bones or [])}

        def used_joint_names(self):
            return {b.name for b in (self.holder.bones or [])}

    slots = [_Slot(donor, True)] + [_Slot(p, False) for p in donor.parts]

    # -- the glTF's meshes -- #
    meshes: List[Tuple[str, GltfModel]] = [(m.mesh_name, m)]
    meshes += [(s.mesh_name, s) for s in m.submodels]

    def used_names(sub: GltfModel) -> set:
        jn = sub.skin_joint_names or []
        used = set()
        for v in sub.vertices:
            for w, j in zip(v.gltf_weights, v.gltf_joints):
                if abs(w) > 1e-6 and int(j) < len(jn):
                    used.add(jn[int(j)])
        return used

    used_cache = {id(sub): used_names(sub) for _nm, sub in meshes}

    # matching: names, then joint-set equality, then leftovers in order
    pair_of_slot: Dict[int, Tuple[str, GltfModel]] = {}
    used_meshes = set()
    for si, slot in enumerate(slots):
        for mi, (nm, sub) in enumerate(meshes):
            if mi in used_meshes:
                continue
            if nm == slot.name:
                pair_of_slot[si] = (nm, sub)
                used_meshes.add(mi)
                break
    for si, slot in enumerate(slots):
        if si in pair_of_slot:
            continue
        for mi, (nm, sub) in enumerate(meshes):
            if mi in used_meshes:
                continue
            if used_cache[id(sub)] == slot.used_joint_names() and \
                    used_cache[id(sub)]:
                pair_of_slot[si] = (nm, sub)
                used_meshes.add(mi)
                break
    for si, slot in enumerate(slots):
        if si in pair_of_slot:
            continue
        # best Jaccard overlap of the joint-name sets (a mesh the user
        # painted extra bones onto no longer equals its slot's set)
        best, best_j = None, -1.0
        for mi, (nm, sub) in enumerate(meshes):
            if mi in used_meshes:
                continue
            a = used_cache[id(sub)]
            b = slot.used_joint_names()
            if not a or not b:
                continue
            j = len(a & b) / len(a | b)
            if j > best_j:
                best, best_j = mi, j
        if best is not None and best_j >= 0.5:
            pair_of_slot[si] = meshes[best]
            used_meshes.add(best)

    warns = list(qwarns)
    # -- rebuild every slot -- #
    rebuilt: Dict[int, Tuple[List[Vertex], List[Bone], List[str]]] = {}
    for si, slot in enumerate(slots):
        if si not in pair_of_slot:
            warns.append(f"`.g` slot '{slot.name}' has no mesh in the glTF "
                         f"— keeping the original bytes")
            continue
        nm, sub = pair_of_slot[si]
        verts, bones, appended = _remap_slot_vertices(
            sub, slot.holder, Q, donor_of_bone, frame_convert)
        rebuilt[si] = (verts, bones, appended)
        if appended:
            warns.append(f"'{slot.name}': bone(s) {', '.join(appended)} "
                         f"added to the part table (painted onto it)")

    # -- verification: converted positions must land on donor geometry -- #
    if frame_convert:
        checked = 0
        matched = 0
        for si, (verts, _b, _a) in rebuilt.items():
            dv = slots[si].holder.vertices
            grid: Dict[tuple, list] = {}
            for p in dv:
                key = (round(p.position[0] * 100),
                       round(p.position[1] * 100),
                       round(p.position[2] * 100))
                grid.setdefault(key, []).append(p.position[:3])
            for i in range(0, len(verts), max(1, len(verts) // 60)):
                x, y, z = verts[i].position[:3]
                gx, gy, gz = round(x * 100), round(y * 100), round(z * 100)
                near = False
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            for q in grid.get((gx + dx, gy + dy, gz + dz), ()):
                                if (abs(q[0] - x) < 2e-3
                                        and abs(q[1] - y) < 2e-3
                                        and abs(q[2] - z) < 2e-3):
                                    near = True
                                    break
                            if near:
                                break
                        if near:
                            break
                    if near:
                        break
                checked += 1
                if near:
                    matched += 1
        if checked and matched / checked < 0.5:
            warns.append(
                f"frame conversion verified on only {matched}/{checked} "
                f"sampled vertices — the `.g` geometry may not match the "
                f"original bind frame")

    # -- assemble the container -- #
    root_verts, root_bones, root_app = rebuilt.get(
        0, (list(donor.vertices), list(donor.bones or []), []))
    root_slot = slots[0].holder
    w_root = root_slot.weights_on_vertex or m.weights_on_vertex or 2
    # painted 4th influence on a w<4 slot needs the wider record
    sub0 = pair_of_slot.get(0, (None, m))[1]
    jn_root = sub0.skin_joint_names or []
    max_lane = 1
    for v in sub0.vertices:
        max_lane = max(max_lane, len(
            _lanes_by_name(list(v.gltf_weights), list(v.gltf_joints), jn_root)))
    w_root = max(w_root, min(4, max_lane))

    parts: List[MeshPart] = []
    for si in range(1, len(slots)):
        slot = slots[si].holder
        if si in rebuilt:
            verts, bones, _app = rebuilt[si]
            sub = pair_of_slot[si][1]
            sw = slot.weights_on_vertex or sub.weights_on_vertex or 2
            jn_s = sub.skin_joint_names or []
            ml = 1
            for v in sub.vertices:
                ml = max(ml, len(_lanes_by_name(list(v.gltf_weights),
                                                list(v.gltf_joints), jn_s)))
            sw = max(sw, min(4, ml))
            parts.append(MeshPart(
                name=slot.name,
                vertex_count=len(verts),
                tri_count=len(sub.indices) // 3,
                vertices=verts,
                indices=list(sub.indices),
                bones=bones,
                weights_on_vertex=sw,
                morph=bool(slot.morph) or bool(sub.morph),
                material_diffuse=(slot.material_diffuse
                                  or sub.material_diffuse),
                lm_uv=slot.lm_uv or sub.lm_uv,
                lightmap=slot.lightmap or sub.lightmap,
                vertex_magic=slot.vertex_magic or b"",
                attrs=dict(slot.attrs),
                attr_items=list(slot.attr_items),
                part_prefix=slot.part_prefix,
                part_tail=slot.part_tail,
            ))
        else:
            # no glTF mesh for this slot: keep the donor part verbatim
            parts.append(slot)

    mesh = SkinnedMesh(
        name=donor.name or m.mesh_name,
        geometry_file=donor.geometry_file,
        vertex_count=len(root_verts),
        tri_count=(len(pair_of_slot[0][1].indices) // 3
                   if 0 in pair_of_slot else donor.tri_count),
        vertices=root_verts,
        indices=(list(pair_of_slot[0][1].indices) if 0 in pair_of_slot
                 else list(donor.indices)),
        bones=root_bones,
        weights_on_vertex=w_root,
        parts=parts,
        morph=bool(donor.morph),
        material_diffuse=donor.material_diffuse or m.material_diffuse,
        lm_uv=donor.lm_uv or m.lm_uv,
        lightmap=donor.lightmap or m.lightmap,
        attr_items=list(donor.attr_items),
    )
    # donor scaffolding for the root block
    if donor.vertex_magic:
        mesh.vertex_magic = donor.vertex_magic
    if donor.header:
        mesh.header = donor.header
    if donor.preamble:
        mesh.preamble = donor.preamble
    if donor.unit_name:
        mesh.unit_name = donor.unit_name
    if donor.attrs:
        mesh.attrs = dict(donor.attrs)
    if donor.parts:
        mesh.trailing = donor.trailing
    mesh.blender_warnings = warns
    return mesh


def mesh_to_skinned(m: GltfModel, weights_on_vertex: int = 0,
                    donor: Optional[SkinnedMesh] = None,
                    anim_donors=None) -> SkinnedMesh:
    """Convert a :class:`GltfModel` into a :class:`SkinnedMesh` ready for `.g`.

    ``weights_on_vertex`` selects the number of influence slots written to the
    GM vertex format (2, 3 or 4).  0 (default) uses the slot count detected on
    the :class:`GltfModel`, preserving every influence from the source.

    ``donor`` — the original `.g` sitting next to the source glTF, parsed
    with :func:`d3tool.gfile.parse_geometry_file`.  Like the `.scene`/`.ac`
    reuse it donates the authoring data a glTF cannot carry: per-vertex
    diffuse, the container header/prelude/vertex magic, light-map UVs, the
    per-part attribute blocks and container scaffolding, and the original
    weight/bone split (gated on a bit-exact re-pack, see
    :func:`_restore_from_donor`).

    ``anim_donors`` — the parsed `.a` streams of the unit (reverse export);
    used to calibrate the Blender->GM frame conversion when the glTF is a
    Blender re-save (merged skins / re-parented mesh nodes), see
    :mod:`d3tool.frame`.
    """
    if donor is not None and m.blender_layout:
        return _mesh_to_skinned_blender(m, weights_on_vertex, donor,
                                        anim_donors or [])
    w = weights_on_vertex or m.weights_on_vertex or 2
    if donor is not None and not weights_on_vertex \
            and len(donor.vertices) == m.vertex_count:
        # a morph base mesh has no influence slots at all (w == 0); the
        # record layout then follows mesh.morph, not w
        w = donor.weights_on_vertex or w
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
    # a donor may declare the main mesh a morph base even when the glTF
    # carries no targets on it (Hierophant robe): the record layout follows
    # the donor attr block
    morph = m.morph or bool(donor is not None
                            and "morph" in (donor.attrs or {}))
    if donor is not None and len(donor.vertices) == len(vertices):
        _restore_from_donor(vertices, m.vertices, donor.vertices, m.rigid,
                            m.accessor_wj)
        if morph and "morph" in (donor.attrs or {}):
            # the forward export zeroes a morph base mesh's positions (the
            # shapes live in the targets); the original base comes from the
            # donor
            for dst, src in zip(vertices, donor.vertices):
                dst.position = src.position

    parts: List[MeshPart] = []
    for sub in m.submodels:
        dpart = None
        if donor is not None:
            dpart = next((p for p in donor.parts
                          if p.name == sub.mesh_name), None)
        sw = sub.weights_on_vertex
        if dpart is not None and dpart.weights_on_vertex:
            sw = dpart.weights_on_vertex
        svertices: List[Vertex] = []
        for v in sub.vertices:
            weights = list(v.gltf_weights)
            joints = list(v.gltf_joints)
            weights = weights[:sw]
            joints = joints[:sw]
            stored = list(weights[:-1])
            if not stored:
                stored = [1.0]
            svertices.append(
                Vertex(v.position, v.normal, v.uv, v.diffuse,
                       tuple(stored), tuple(joints))
            )
        pmorph = sub.morph or bool(dpart is not None
                                   and "morph" in (dpart.attrs or {}))
        if dpart is not None:
            _restore_from_donor(svertices, sub.vertices, dpart.vertices,
                                sub.rigid or pmorph, sub.accessor_wj)
            if pmorph and "morph" in (dpart.attrs or {}):
                for dst, src in zip(svertices, dpart.vertices):
                    dst.position = src.position
        sbones = sub.bones
        if dpart is not None and dpart.bones:
            sbones = dpart.bones
        parts.append(MeshPart(
            name=sub.mesh_name,
            vertex_count=sub.vertex_count,
            tri_count=sub.tri_count,
            vertices=svertices,
            indices=sub.indices,
            bones=sbones,
            weights_on_vertex=sw,
            morph=pmorph,
            material_diffuse=(dpart.material_diffuse if dpart is not None
                              else sub.material_diffuse),
            lm_uv=(dpart.lm_uv if dpart is not None and dpart.lm_uv
                   else sub.lm_uv),
            lightmap=(dpart.lightmap if dpart is not None and dpart.lightmap
                      else sub.lightmap),
            vertex_magic=(dpart.vertex_magic if dpart is not None
                          else b""),
            attrs=(dict(dpart.attrs) if dpart is not None else
                   _synth_part_attrs(sub, sbones)),
            attr_items=(list(dpart.attr_items) if dpart is not None else []),
            part_prefix=(dpart.part_prefix if dpart is not None else b""),
            part_tail=(dpart.part_tail if dpart is not None else b""),
        ))

    mesh = SkinnedMesh(
        name=m.mesh_name,
        geometry_file=m.geometry_file,
        vertex_count=m.vertex_count,
        tri_count=m.tri_count,
        vertices=vertices,
        indices=m.indices,
        bones=m.bones,
        weights_on_vertex=w,
        parts=parts,
        morph=morph,
        material_diffuse=m.material_diffuse,
        lm_uv=m.lm_uv,
        lightmap=m.lightmap,
        attr_items=(list(donor.attr_items) if donor is not None else []),
    )
    if donor is not None:
        # the donated original is the ground truth for the bone-descriptor
        # table: a glTF skin may carry extra scene nodes (weapon holders)
        # the `.g` never listed, and a morph base mesh has no descriptors
        # at all
        mesh.bones = donor.bones
        # the leading `name1` string of the `.g` header is a 3ds-Max-era
        # leftover that dis3tool does not carry into the glTF node name
        if donor.name:
            mesh.name = donor.name
        if donor.vertex_magic:
            mesh.vertex_magic = donor.vertex_magic
        if donor.header:
            mesh.header = donor.header
        if donor.preamble:
            mesh.preamble = donor.preamble
        if donor.lm_uv and not mesh.lm_uv:
            mesh.lm_uv = donor.lm_uv
        if donor.lightmap and not mesh.lightmap:
            mesh.lightmap = donor.lightmap
        if donor.unit_name:
            mesh.unit_name = donor.unit_name
        if donor.geometry_file:
            mesh.geometry_file = donor.geometry_file
        # a compound donor's `trailing` holds its parts; only adopt a rest
        # that the donor kept *after* the parsed parts (shadow volumes etc.)
        if donor.parts:
            mesh.trailing = donor.trailing
        elif not mesh.parts:
            mesh.trailing = donor.trailing
    return mesh


def _node_parent_map(nodes):
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i
    return parent


def _morph_record(tag: int, name: str, frames: int, vc: int,
                  positions: bytes) -> bytes:
    """One trailing morph-stream record:
    ``[14][len-8][tag][frames][vc][name_len][name+NUL][positions]``."""
    nb = name.encode("latin1") + b"\x00"
    total = 24 + len(nb) + len(positions)
    return (struct.pack("<6I", 14, total - 8, tag, frames, vc, len(nb))
            + nb + positions)


def _synth_part_attrs(sub: GltfModel, sbones: List[Bone]) -> Dict[str, str]:
    """Attribute block for a sub-mesh rebuilt without a donor part.

    The writer only patches counts into an *existing* attribute dict and
    adds nothing else, so a donorless part needs the counts seeded here —
    plus the `material0_diffuse` the glTF primitive already carries (the
    per-part texture name is otherwise lost, e.g. the Leader variant
    sets reference 8 body parts each with its own texture).
    """
    if not sub.material_diffuse:
        return {}
    attrs = {
        "dwNode": "375048704",
        "dwParent": "55867360",
        "name": sub.mesh_name,
        "groupname": "Scene Root",
        "materials_num": "1",
        "material0_diffuse": sub.material_diffuse,
        "material0_triangles_num": str(len(sub.indices) // 3),
    }
    if sub.morph:
        # a morph-deformer part keeps 40-byte records and no weight keys
        attrs["morph"] = "1"
        attrs["morph_track"] = "1"
    else:
        # declare the record layout the writer emits for this part
        attrs["vertexs_weights_num"] = str(sub.vertex_count)
        attrs["weights_on_vertex"] = str(sub.weights_on_vertex)
        attrs["bones_num"] = str(len(sbones))
    return attrs


def _split_concat_anim(anim: "animmod.AnimFile", m: GltfModel,
                       streams: List[Tuple[str, "animmod.AnimFile"]],
                       out_name: str, donor: "animmod.AnimFile",
                       ) -> Optional["animmod.AnimFile"]:
    """Split a concatenated reference animation back into one stream.

    dis3tool exports a unit whose `.ac` names several `.a` files as **one**
    glTF animation spanning every stream (:func:`d3tool.anim.concat_anims`).
    Rebuilding a single `.a` therefore means slicing the concatenated frame
    sequences back at the stream boundaries: the primary bone slots (the
    first stream's record count) were channelled per stream at the *same
    slot index*, so stream *i*'s samples are the slice
    ``[offset_i : offset_i + frame_count_i]`` of every concat record.  Bones
    appended by later streams got no glTF channels at all — they come from
    the donor verbatim, as do the record preambles, the trailing morph
    streams and the header magic/record-type word.

    Returns ``None`` when the inputs do not fit the concat layout (then the
    caller falls back to the single-stream rebuild).
    """
    names = [n for n, _a in streams]
    if out_name not in names:
        return None
    i = names.index(out_name)
    parsed = [a for _n, a in streams]
    fcs = [a.frame_count for a in parsed]
    total = anim.frame_count or len(m.frames)
    if sum(fcs) != total or donor.frame_count != fcs[i]:
        return None
    n_primary = len(parsed[0].bones)
    if len(anim.bones) != n_primary:
        return None
    off = sum(fcs[:i])
    out = animmod.AnimFile(bone_count=len(donor.bones),
                           frame_count=fcs[i])
    for pos, db in enumerate(donor.bones):
        if pos < n_primary:
            ab = anim.bones[pos]
            fs = ab.frames[off:off + fcs[i]]
            if tuple(db.frames) == tuple(fs[:len(db.frames)]):
                frames = list(db.frames)   # verified: the donor bytes
            else:
                # glTF-edited values inside the donor record shape
                frames = list(fs[:len(db.frames)])
            out.bones.append(animmod.BoneAnim(db.name, db.parent,
                                              len(frames), frames,
                                              db.preamble))
        else:
            # appended bone: no glTF channels exist for it
            out.bones.append(animmod.BoneAnim(db.name, db.parent,
                                              len(db.frames),
                                              list(db.frames), db.preamble))
    out.trailing = donor.trailing
    out.morphs = list(donor.morphs)
    magic, unk = 9, 15
    if len(donor.header) >= 20:
        magic = struct.unpack_from("<I", donor.header, 0)[0]
        unk = struct.unpack_from("<I", donor.header, 16)[0]
    out.header = b"\x00" * 20  # placeholder: the writer emits it verbatim
    total_len = len(animmod.write_anim(out))
    out.header = struct.pack("<5I", magic, total_len - 8 - len(out.trailing),
                             len(out.bones), out.frame_count, unk)
    return out


def _resample_split_anim(m: GltfModel,
                         streams: List[Tuple[str, "animmod.AnimFile"]],
                         out_name: str, donor: "animmod.AnimFile",
                         Q: Optional[Dict[str, "framemod.Mat4"]] = None,
                         ) -> Optional["animmod.AnimFile"]:
    """Slice one stream out of a *re-sampled* concatenated animation.

    A Blender re-save lays the concatenated 30 fps animation onto its own
    scene frame rate (default 24: the Angel's 263 keys become 210) in its
    own bone-local frames.  The `.ac` still names every stream with 30 fps
    frame indices, so each output frame k of stream i is the glTF animation
    sampled at time (offset_i + k) / 30, converted back into the GM frame
    with the calibrated Q maps, verified per record against the donor and
    adopted byte-exact where it matches (a weights-only edit then rebuilds
    the original `.a` files bit-for-bit).

    Returns ``None`` when the stream boundaries cannot be established.
    """
    names = [n for n, _a in streams]
    if out_name not in names:
        return None
    i = names.index(out_name)
    fcs = [a.frame_count for a in (a for _n, a in streams)]
    if donor.frame_count != fcs[i]:
        return None
    if not m.frames or len(m.frames) < 2:
        return None
    off = sum(fcs[:i])
    times = m.frames
    dt = (times[-1] - times[0]) / (len(times) - 1)
    if dt <= 0:
        return None
    fps = 30.0   # the engine's (and dis3tool's) time base
    need = (off + fcs[i] - 1) / fps
    if times[-1] < need - 2.0 * dt:
        return None   # the glTF animation does not cover this stream

    node_of: Dict[str, int] = {}
    for idx, nd in enumerate(m.nodes):
        nm = nd.get("name")
        if nm and nm not in node_of:
            node_of[nm] = idx
    parents = {}
    for _n, a in streams:
        for b in a.bones:
            parents.setdefault(b.name, b.parent or "")

    def sample_local(name: str, t: float):
        """(rotation, translation) of the glTF channel for `name` at t, or
        the node's static TRS, or None when the node does not exist."""
        ni = node_of.get(name)
        if ni is None:
            return None
        ch = m.anim_channels.get(ni, {})
        tm = m.anim_times.get(ni, {})
        nd = m.nodes[ni]
        if "rotation" in ch:
            flat = [x for tup in ch["rotation"] for x in tup[:4]]
            rot = framemod.sample_channel(tm.get("rotation") or [0.0],
                                          flat, 4, t, slerp=True, jump=True)
        else:
            rot = nd.get("rotation")
        if "translation" in ch:
            flat = [x for tup in ch["translation"] for x in tup[:3]]
            tra = framemod.sample_channel(tm.get("translation") or [0.0],
                                          flat, 3, t, jump=True)
        else:
            tra = nd.get("translation")
        return rot, tra

    tol = 2e-3
    records: List[Tuple[str, str, list]] = []
    for db in donor.bones:
        nm = db.name
        frames_out = list(db.frames)     # donor bytes by default
        if Q and nm in Q and db.frames:
            Qp = Q.get(parents.get(nm, ""), framemod.IDENTITY) \
                if parents.get(nm) else framemod.IDENTITY
            built = []
            for k in range(min(fcs[i], len(db.frames))):
                t = (off + k) / fps
                st = sample_local(nm, t)
                if st is None:
                    built = None
                    break
                rot, tra = st
                if rot is None:
                    rot = (0.0, 0.0, 0.0, 1.0)
                if tra is None or len(tra) < 3:
                    tra = (0.0, 0.0, 0.0)
                Lb = framemod.trs_matrix(rot, tra[:3])
                try:
                    Lo = framemod.convert_local(Lb, Q[nm], Qp)
                except ValueError:
                    built = None
                    break
                q = framemod.mat_to_quat(Lo)
                built.append(tuple(q) + (Lo[0][3], Lo[1][3], Lo[2][3]))
            if built is not None:
                # per frame: the donor byte where the conversion verifies,
                # the donor byte too on a stream boundary (the re-sampled
                # grid folded two never-adjacent poses into the seam, and
                # past the last key there is no data at all), the converted
                # value elsewhere (the user's animation edits)
                mixed = []
                last_t = times[-1]
                for k in range(len(built)):
                    t = (off + k) / fps
                    close = (framemod.quat_close(built[k][:4],
                                                 db.frames[k][:4], tol)
                             and all(abs(built[k][4 + c]
                                         - db.frames[k][4 + c]) <= tol
                                     for c in range(3)))
                    seam = (k == 0 or k == len(built) - 1
                            or t > last_t - dt)
                    mixed.append(db.frames[k] if (close or seam)
                                 else built[k])
                # a 24 fps re-sample carries a real interpolation error on
                # fast motion (an arm strike lands a few degrees off the
                # 30 fps original).  When the stream as a whole still
                # matches the donor (median deviation over every frame and
                # record), the animation was not edited: take the donor's
                # bytes wholesale and the `.a` round-trips bit-for-bit.
                devs = sorted(
                    max(abs(built[k][c] - db.frames[k][c])
                        for c in range(7))
                    for k in range(len(built)))
                median_dev = devs[len(devs) // 2]
                frames_out = (list(db.frames) if median_dev <= 0.004
                              else mixed)
        records.append((nm, db.parent, frames_out))

    out = animmod.build_anim(records, fcs[i])
    # donor scaffolding: preambles, trailing morph streams, header
    for pos, rb in enumerate(out.bones):
        if pos < len(donor.bones):
            rb.preamble = donor.bones[pos].preamble
    out.trailing = donor.trailing
    out.morphs = list(donor.morphs)
    magic, unk = 9, 15
    if len(donor.header) >= 20:
        magic = struct.unpack_from("<I", donor.header, 0)[0]
        unk = struct.unpack_from("<I", donor.header, 16)[0]
    out.header = b"\x00" * 20
    total_len = len(animmod.write_anim(out))
    out.header = struct.pack("<5I", magic, total_len - 8 - len(out.trailing),
                             len(out.bones), out.frame_count, unk)
    return out


def animation_from_gltf(m: GltfModel, donor: Optional["animmod.AnimFile"] = None,
                        streams: Optional[List[Tuple[str, "animmod.AnimFile"]]] = None,
                        out_name: str = "") -> "animmod.AnimFile":
    """Rebuild a Disciples 3 `.a` animation file from the glTF animation.

    The `.a` records are exactly the nodes targeted by the glTF animation
    rotation/translation channels (Root + every animated bone), in hierarchy
    (depth-first) order, with parents resolved from the node tree.  Each frame
    is ``[quat(x,y,z,w) + translation(x,y,z)]`` (7 floats), matching what
    dis3tool writes into `bones_rotate` / `bones_translate`.

    Nodes animated through a morph-target ``weights`` channel only (the
    morph-deformer meshes) are *not* bones: their animation lands in the
    trailing block as vertex-morph streams, rebuilt verbatim from the morph
    target POSITION accessors (dis3tool stored the frame bytes unmodified).

    ``donor`` — the original `.a` next to the source glTF (same reuse rule as
    the `.g` donor): donates the bone order, the parent strings, the record
    preambles and the trailing block (record tag + any foreign streams a
    reference export kept from other units) whenever the rebuilt data
    verifies against it.
    """
    if not m.animation:
        return animmod.AnimFile()
    parent = _node_parent_map(m.nodes)

    # Some dis3tool exports reference animation nodes that are outside the
    # node array (e.g. Wildboar targets node index 37 while nodes are 0..36).
    # Drop those so the rebuild never indexes out of range.  A weights-only
    # node (morph-deformer mesh) is not a `.a` bone.
    n_nodes = len(m.nodes)
    animated = {i for i, ch in m.anim_channels.items()
                if 0 <= i < n_nodes and ("rotation" in ch
                                         or "translation" in ch)}

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

    for root in roots:
        dfs(root)

    frames = []
    # `GltfModel` keeps the sampled times in `frames`; a node animated on one
    # path only (e.g. translation without rotation) needs the *global* frame
    # count to pad the missing channel.
    n_global = len(m.frames)
    for i in order:
        name = m.nodes[i].get("name", f"bone{i}")
        # parent name within the animated set, or 'Scene Root'
        p = parent.get(i)
        pname = m.nodes[p].get("name", "Scene Root") if p in animated else "Scene Root"
        ch = m.anim_channels.get(i, {})
        rotations = ch.get("rotation") or [(0.0, 0.0, 0.0, 1.0)] * n_global
        translations = ch.get("translation") or [(0.0, 0.0, 0.0)] * n_global
        samples = []
        nf = max(len(rotations), len(translations))
        for k in range(nf):
            r = rotations[k] if k < len(rotations) else (0.0, 0.0, 0.0, 1.0)
            t = translations[k] if k < len(translations) else (0.0, 0.0, 0.0)
            samples.append((r[0], r[1], r[2], r[3], t[0], t[1], t[2]))
        frames.append((name, pname, samples))

    anim = animmod.build_anim(frames, len(m.frames))

    # ---- a multi-stream unit: slice the concat back into the named stream ----
    if streams and len(streams) >= 2 and donor is not None and not donor.raw:
        if m.blender_layout:
            # a Blender re-save re-frames every channel (and usually re-
            # samples onto its own fps grid): rebuild through the calibrated
            # Q maps, adopting the donor bytes wherever they verify
            Qb, _qw = _blender_q_map(m, [a for _n, a in streams])
            res = _resample_split_anim(m, streams, out_name, donor, Qb)
            if res is not None:
                return res
        split = _split_concat_anim(anim, m, streams, out_name, donor)
        if split is not None:
            return split

    # ---- the original keeps records the glTF animates through nodes
    # outside the node array (Wildboar channels target node 37 of 0..36):
    # rebuild positionally, mapping the k-th channel group onto the k-th
    # donor record (dis3tool writes the channels in record order) ----
    if (donor is not None and not donor.raw
            and len(donor.bones) > len(anim.bones)
            and m.animation is not None):
        chan_nodes: List[int] = []
        seen_nodes: set = set()
        for ch in m.animation["channels"]:
            nd = ch["target"].get("node")
            if nd is None or nd in seen_nodes:
                continue
            seen_nodes.add(nd)
            chan_nodes.append(nd)
        if len(chan_nodes) == len(donor.bones):
            out = animmod.AnimFile(bone_count=len(donor.bones),
                                   frame_count=len(m.frames))
            n_global = len(m.frames)
            for pos, db in enumerate(donor.bones):
                ch = m.anim_channels.get(chan_nodes[pos], {})
                rotations = ch.get("rotation") or [(0.0, 0.0, 0.0, 1.0)] * n_global
                translations = ch.get("translation") or [(0.0, 0.0, 0.0)] * n_global
                nf = max(len(rotations), len(translations))
                samples = []
                for k in range(nf):
                    r = rotations[k] if k < len(rotations) else (0.0, 0.0, 0.0, 1.0)
                    t = translations[k] if k < len(translations) else (0.0, 0.0, 0.0)
                    samples.append((r[0], r[1], r[2], r[3], t[0], t[1], t[2]))
                if tuple(db.frames) == tuple(samples):
                    rec_frames = list(db.frames)   # verified: donor bytes
                else:                              # glTF-edited values
                    rec_frames = cast("List[Tuple[float, ...]]", samples)
                pream = db.preamble if len(rec_frames) == len(db.frames) \
                    else animmod._preamble_for(len(rec_frames),
                                               len(db.name) + 1,
                                               len(db.parent) + 1)
                out.bones.append(animmod.BoneAnim(db.name, db.parent,
                                                  len(rec_frames), rec_frames,
                                                  pream))
            magic, unk = 9, 15
            if len(donor.header) >= 20:
                magic = struct.unpack_from("<I", donor.header, 0)[0]
                unk = struct.unpack_from("<I", donor.header, 16)[0]
            out.header = b"\x00" * 20
            total_len = len(animmod.write_anim(out))
            out.header = struct.pack("<5I", magic,
                                     total_len - 8 - len(out.trailing),
                                     len(out.bones), out.frame_count, unk)
            return out

    # ---- donated scaffolding: bone order, parents, preambles ----
    if donor is not None and donor.bones and not donor.raw:
        dbones = {b.name: b for b in donor.bones}
        if {nm for nm, _p, _s in frames} == set(dbones):
            # same bone set: reproduce the original record order, parents
            # and preambles (the glTF hierarchy walk may interleave)
            order = [dbones[nm] for nm, _p, _s in frames]
            by_name = {b.name: b for b in anim.bones}
            anim.bones = []
            for db in order:
                rb = by_name[db.name]
                rb.parent = db.parent
                if len(db.frames) == len(rb.frames):
                    rb.preamble = db.preamble
                anim.bones.append(rb)
        else:
            for rb in anim.bones:
                dn = dbones.get(rb.name)
                if dn is None:
                    continue
                rb.parent = dn.parent
                if len(dn.frames) == len(rb.frames):
                    rb.preamble = dn.preamble

    # ---- trailing vertex-morph streams ----
    morphs = []  # (name, frames, vc, positions)
    for sub in [m] + list(m.submodels):
        if sub.target_positions and sub.morph_name:
            morphs.append((sub.morph_name, len(sub.target_positions),
                           sub.vertex_count,
                           b"".join(sub.target_positions)))
    if morphs:
        dtracks = {}
        foreign = []
        if donor is not None and not donor.raw:
            seen = set()
            for t in donor.morphs:
                dtracks[t.name] = t
                if t.name not in {mm[0] for mm in morphs}:
                    foreign.append(t)
        records = []
        all_match = bool(dtracks)
        for name, nfr, vc, mpos in morphs:
            dt = dtracks.get(name)
            tag = dt.tag if dt is not None else 15
            if dt is None or dt.frame_count != nfr or dt.vertex_count != vc \
                    or dt.positions != mpos:
                all_match = False
                records.append(_morph_record(tag, name, nfr, vc, mpos))
            else:
                records.append(dt.raw_record)
        if all_match and not foreign and donor is not None:
            # the donor trailing reproduces byte-for-byte (covers any
            # non-stream payload between the records too)
            anim.trailing = donor.trailing
        else:
            anim.trailing = b"".join(records)
            # foreign streams the reference kept (e.g. DarkServant): re-append
            anim.trailing += b"".join(t.raw_record for t in foreign)
    elif donor is not None and not donor.raw and donor.morphs:
        # the glTF carries no morph targets at all: the original trailing
        # belonged to streams the reference export did not lift (foreign
        # units' tracks) — keep it verbatim
        anim.trailing = donor.trailing
    # refresh the header length field (excludes the trailing block); the
    # magic and the record-type word (offset 16: 15/16/30, observed) are
    # donated by the original — they are exporter-version data a glTF
    # animation does not carry
    total = len(animmod.write_anim(anim))
    magic, unk = 9, 15
    if donor is not None and not donor.raw and len(donor.header) >= 20:
        magic = struct.unpack_from("<I", donor.header, 0)[0]
        unk = struct.unpack_from("<I", donor.header, 16)[0]
    anim.header = struct.pack("<5I", magic, total - 8 - len(anim.trailing),
                              len(anim.bones), anim.frame_count, unk)
    return anim
