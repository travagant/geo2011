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
* animation keyframe times == ``float32(k * float32(1/30))`` seconds — dis3tool
  lays one keyframe per frame on a 30 fps time base (the 3ds Max default), so
  the glTF ``frames`` input runs 0 .. (n_frames-1)/30 seconds, *not* a
  normalised 0..1 range.

Compound ``.g`` containers (``mesh.parts`` non-empty) take the
:func:`_write_compound_gltf` path: one glTF mesh/skin/node per sub-mesh, the
tga->dds material rename driven by the ``material0_diffuse`` attribute, an
optional light-map ``normalTexture`` (``material0_lightmap`` attribute ->
``TEXCOORD_1`` from the ``lm_uv`` block), and morph targets lifted from the
``AnimFile.morphs`` streams plus the synthesised identity ``morph_weights``
matrix — exactly the layout the dis3tool reference exports use (verified
byte-for-byte on the bundled Goblin / HolyAvenger / Golem corpus).
"""
from __future__ import annotations

import json
import math
import os
import struct
from typing import Dict, List, Optional, Tuple

from .anim import AnimFile, BoneAnim
from .model import MorphTrack, SkinnedMesh, pack_weights_joints


class _BufferShortError(Exception):
    """Raised internally when an accessor would read past the buffer."""


# glTF accessor enums, shared by the exporter and the structural validator
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_CTSIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_CTFMT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}


def _at(seq, idx):
    """``seq[idx]`` or ``None`` when the index is absent/out of range.

    `validate_gltf` checks *untrusted* documents, so a bad index must be
    reported as an error rather than escaping as an IndexError/KeyError.
    """
    if isinstance(idx, int) and 0 <= idx < len(seq):
        return seq[idx]
    return None


def _u32(idx: int) -> bytes:
    return struct.pack("<I", idx)


def _F32(x: float) -> float:
    """Round ``x`` to the nearest float32 value (stored in a Python float)."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def node_hierarchy(bones: List[BoneAnim],
                   n_primary: int = 0) -> Tuple[Dict[str, list], Dict[str, str], List[str], List[str]]:
    """Return ``(children_map, parent_map, roots)`` for the animated bones.

    dis3tool animation configs sometimes list the *same* tip bone under more
    than one parent (e.g. ``null_Bone_Tip`` appears under both ``Bone01`` and
    ``Bone11``).  The skeleton is a single tree, so each bone must have exactly
    one parent: the first occurrence wins and later duplicate edges are dropped
    (matching how dis3tool flattens the exported glTF node tree).
    """
    # Bones that concat_anims appended because only a *later* `.a` stream
    # carries them (AirElemental's LeftLeftHand/Tail02, DarkServant's Bone02)
    # stay out of the tree: the dis3tool reference emits them as trailing
    # nodes, in list order, not woven in under their parent.  Verified by
    # comparing the reference node order for all 83 bundled units — DFS over
    # the first stream's skeleton matches 79, and the only misses are the two
    # units that have such appended bones plus the rigid Blacknaga.
    # Bones that concat_anims appended because only a *later* `.a` stream
    # carries them (AirElemental's LeftLeftHand/Tail02, DarkServant's Bone02)
    # keep their parent edge — the reference lists LeftLeftHand under
    # LeftForeArm — but are emitted as *trailing* nodes, in list order, not
    # woven into the traversal, and get no animation channel.  Verified by
    # comparing the reference node order and children for all 83 bundled
    # units: DFS over the first stream's skeleton matches 79, and the only
    # misses are the two units with such appended bones plus rigid Blacknaga.
    primary = bones[:n_primary] if n_primary else bones
    primary_set = {b.name for b in primary}
    children: Dict[str, List[str]] = {b.name: [] for b in bones}
    parent: Dict[str, str] = {}
    for b in bones:
        parent.setdefault(b.name, b.parent)
    assigned: set = set()
    for b in bones:
        p = b.parent
        if p in children and p != b.name and b.name not in assigned:
            children[p].append(b.name)
            assigned.add(b.name)
    roots = [b.name for b in primary if b.parent not in children]
    if not roots:
        roots = [bones[0].name]
    # ensure all roots lead with a stable seed
    order: List[str] = []
    seen = set()

    def dfs(nm: str, only_primary: bool):
        if nm in seen:
            return
        seen.add(nm)
        order.append(nm)
        for c in children.get(nm, []):
            if not only_primary or c in primary_set:
                dfs(c, only_primary)

    for r in roots:
        dfs(r, True)
    # anything the primary traversal could not reach — the appended bones and
    # any orphan — follows in list order
    for b in bones:
        if b.name not in seen:
            dfs(b.name, False)
    return children, parent, order, roots


def _first_by_name(bones: List[BoneAnim]) -> Dict[str, BoneAnim]:
    """Map bone name -> bone, keeping the **first** occurrence.

    A plain ``{b.name: b for b in bones}`` keeps the last, which is wrong:
    three bundled `.a` files list a bone name more than once
    (Wildboar has two `null_Bone_Tip`, WaterSnake five `null`), and the
    dis3tool reference binds the node to the first one — verified
    bit-for-bit on both units' rest rotation/translation.
    """
    out: Dict[str, BoneAnim] = {}
    for b in bones:
        out.setdefault(b.name, b)
    return out


def _animation_targets(anim_primary: List[BoneAnim],
                       base: int) -> List[Tuple[BoneAnim, int]]:
    """Channel ``(bone, node)`` targets the way dis3tool emits them.

    The reference *counts* node slots rather than resolving names: the
    channel target for the bone at position ``i`` of the primary streams is
    simply ``base + i`` — the node index that bone *would* have if every
    list entry got its own node.  For a duplicate-name bone (WaterSnake
    lists `null` five times, Wildboar `null_Bone_Tip` twice) the later
    entries have no node of their own, so two things happen, both faithful
    to the references:

    * the channel target dangles past the node list (Wildboar's last bone
      targets 37 with 37 nodes; WaterSnake's last four target 47..50 with
      47), and
    * every *unique* bone after the duplicate is off by one, aiming at the
      next bone's node (Wildboar's Mirror_Bone11 is node 12 but its channel
      targets 13).

    Deduplicating by (node, path) — what a validator would want — emits 8
    (WaterSnake) and 2 (Wildboar) channels fewer than every reference.
    """
    return [(b, base + i) for i, b in enumerate(anim_primary)]


def write_gltf(
    mesh: SkinnedMesh,
    anim: Optional[AnimFile] = None,
    output_name: str = "character",
    texture: Optional[str] = None,
    textures: Optional[Dict[str, str]] = None,
    lightmaps: Optional[Dict[str, str]] = None,
) -> Tuple[bytes, dict]:
    """Render a glTF 2.0 document + its binary buffer for a GM mesh.

    Returns ``(bin_bytes, gltf)``.  The ``gltf`` layout mimics the dis3tool
    export (accessors 0..9: indices, POSITION, NORMAL, TEXCOORD_0, WEIGHTS_0,
    JOINTS_0, inverseBindMatrices, frames, bones_rotate, bones_translate).

    Compound meshes (``mesh.parts`` non-empty) take the :func:`_write_compound_gltf`
    route.  ``textures`` / ``lightmaps`` optionally override the material URI
    per sub-mesh display name (``"*"`` is a catch-all); the defaults are the
    ``material0_diffuse`` / ``material0_lightmap`` attributes with the
    historical ``.tga`` -> ``.dds`` rename dis3tool applies.
    """
    if not mesh.parts and not mesh.vertices:
        raise ValueError(
            "cannot export glTF for an empty mesh (unreadable .g layout: "
            "the source bytes are only preserved verbatim via `raw`)")
    if not mesh.parts and not mesh.indices:
        # A node helper rather than a renderable mesh: the bundled Leader
        # stubs declare `materials_num 0` and carry a handful of vertices
        # with no index block.  Emitting a glTF for one would need a skin
        # with zero joints, which is not valid glTF — refuse instead.
        raise ValueError(
            f"cannot export glTF for {mesh.name or 'this mesh'}: it has no "
            "triangles (a node-helper .g with materials_num 0, not a "
            "renderable mesh)")
    if mesh.parts:
        return _write_compound_gltf(mesh, anim, output_name,
                                    textures or {}, lightmaps or {})
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
        # WEIGHTS_0/JOINTS_0 exactly as dis3tool packs them (see
        # pack_weights_joints -- byte-verified against the reference corpus).
        # The `.g` stores float32 weights with a tiny residual; the exporter
        # keeps it verbatim instead of renormalising.
        w, j = pack_weights_joints(v.stored_weights, v.bones)
        vbuf += struct.pack(
            "<3f3f2f4f4B",
            *v.position, *nrm, *v.uv, *w, *j,
        )
    ibuf = b"".join(struct.pack("<I", x) for x in mesh.indices)

    # ---- inverse bind matrices (bone descriptors) ----
    bone_bytes = bytearray()
    for b in bones:
        bone_bytes += struct.pack("<16f", *b.matrix)

    # ---- animation channel data ----
    anim_bones: List[BoneAnim] = anim.bones if anim and anim.bones else []
    # Only the first stream's bones are animated.  Bones that concat_anims
    # appended from a later `.a` still get a node, but the reference emits no
    # channel and no rotation/translation storage for them — verified on
    # AirElemental, whose LeftLeftHand/Tail02 are untargeted by all 84
    # channels, and whose buffer is exactly 2 x 363 x (16+12) = 20328 bytes
    # smaller than one that animates them.
    anim_primary: List[BoneAnim] = (
        anim_bones[:anim.n_primary] if anim and anim.n_primary else anim_bones)
    n_frames = anim.frame_count if anim else 0
    frames: List[float] = []
    rot: List[float] = []
    tra: List[float] = []
    if anim_bones:
        n_frames = anim.frame_count or max(len(b.frames) for b in anim_bones) or 1
        if n_frames > 1:
            # 30 fps time base, matching dis3tool byte-for-byte: a viewer gets
            # (n_frames-1)/30 seconds of animation per channel, the same
            # pace the reference export has.  Multiplying by the float32
            # step and packing to float32 reproduces the reference `frames`
            # accessor exactly (verified for all bundled units).
            step = _F32(1.0 / 30.0)
            frames = [k * step for k in range(n_frames)]
        else:
            frames = [0.0]
        for b in anim_primary:
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

    # dis3tool names the mesh node, the mesh and all three mesh bufferViews
    # after the `name` *attribute* (the logical armature name), not the leading
    # `name1` string of the binary, which is often a leftover 3ds Max material
    # name ("Material #35", "07 - Default").  Verified on all 15 single-mesh
    # bundled references: nodes[0].name, meshes[0].name and every
    # mesh_*_<unit> bufferView name equal SkinnedMesh.unit_name — including
    # Mermaid, whose attribute carries the upstream typo `neutral_mermaid`.
    unit_label = mesh.unit_name or mesh.name
    bufferViews = [
        {"name": f"mesh_indexes_{unit_label}", "buffer": 0,
         "byteOffset": idx_off, "byteLength": idx_len, "target": 34963},
        {"name": f"mesh_vertexes_{unit_label}", "buffer": 0,
         "byteOffset": vtx_off, "byteLength": vtx_len, "byteStride": 52,
         "target": 34962},
        {"name": f"mesh_bones_{unit_label}", "buffer": 0,
         "byteOffset": bone_off, "byteLength": bone_len},
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

    # Every reference accessor carries an explicit `byteOffset` (26717 of
    # 26760; the only 43 that omit it are the compound writer's morph_weights
    # accessors, a documented dis3tool quirk) and keys are ordered
    # bufferView / byteOffset / componentType / count / type [/ min / max].
    accessors = [
        {"bufferView": 0, "byteOffset": 0, "componentType": 5125,
         "count": len(mesh.indices), "type": "SCALAR"},
        {"bufferView": 1, "byteOffset": 0, "componentType": 5126,
         "count": n_verts, "type": "VEC3", "min": pos_min, "max": pos_max},
        {"bufferView": 1, "byteOffset": 12, "componentType": 5126,
         "count": n_verts, "type": "VEC3"},
        {"bufferView": 1, "byteOffset": 24, "componentType": 5126,
         "count": n_verts, "type": "VEC2"},
    ]
    if anim_bones:
        accessors += [
            {"bufferView": 1, "byteOffset": 32, "componentType": 5126,
             "count": n_verts, "type": "VEC4"},
            {"bufferView": 1, "byteOffset": 48, "componentType": 5121,
             "count": n_verts, "type": "VEC4"},
            {"bufferView": 2, "byteOffset": 0, "componentType": 5126,
             "count": len(bones), "type": "MAT4"},
        ]
        fmin = [min(frames)] if frames else [0.0]
        fmax = [max(frames)] if frames else [0.0]
        accessors += [
            {"bufferView": 3, "byteOffset": 0, "componentType": 5126,
             "count": n_frames, "type": "SCALAR", "min": fmin, "max": fmax},
        ]
        # one rotation (VEC4) + one translation (VEC3) accessor per bone, each
        # count=n_frames, sampled at distinct offsets into shared bufferViews.
        for i in range(len(anim_primary)):
            accessors.append({
                "bufferView": 4, "componentType": 5126, "count": n_frames,
                "type": "VEC4", "byteOffset": i * n_frames * 16,
            })
            accessors.append({
                "bufferView": 5, "componentType": 5126, "count": n_frames,
                "type": "VEC3", "byteOffset": i * n_frames * 12,
            })

    # ---- nodes ----
    node_list: List[dict] = [{"name": unit_label}]
    name_to_idx: Dict[str, int] = {}
    skin: Optional[dict] = None
    if anim_bones:
        children, parent, order, roots = node_hierarchy(
            anim_bones, anim.n_primary if anim else 0)
        # node 0 = mesh; skeleton nodes indices 1..N in hierarchy order
        for off, nm in enumerate(order):
            name_to_idx[nm] = 1 + off
        # build each bone node with rest TRS and children
        bmap: Dict[str, BoneAnim] = _first_by_name(anim_bones)
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
        # add any skin bones not animated as extra nodes (rare)
        for b in bones:
            if b.name not in name_to_idx:
                idx = len(node_list)
                name_to_idx[b.name] = idx
                node_list.append({"name": b.name})
        # dis3tool scene roots = every node whose parent is not a bone, in
        # node order — normally just the skeleton root, but DarkServant's
        # `ROOT_demons_thief_lod` / `Bone02` (parent `Scene Root`) trail as
        # extra roots in its reference (scene nodes 4, 72, 73).
        scene_children_idx = [name_to_idx[nm] for nm in order
                              if parent.get(nm) not in children]
        # ---- skin ----
        skin_joints = [name_to_idx.get(b.name, 1 + i) for i, b in enumerate(bones)]
        node_list[0]["mesh"] = 0
        node_list[0]["skin"] = 0
        # dis3tool names its skins skin0/skin1/... (all 98 bundled references do),
        # and the compound writer already follows that convention.
        skin = {"name": "skin0", "joints": skin_joints, "inverseBindMatrices": 6}
    else:
        # rigid export (the `.ac`'s animation is not resolvable inside the
        # unit folder — Blacknaga points at mermaid's `.a`, watersnake_sea at
        # a `.a` that ships with neither).  The dis3tool reference emits ONLY
        # the mesh node — no bone nodes, no skin — while the buffer keeps the
        # skinned stride-52 vertex block and the mesh_bones IBM block
        # unreferenced, and the primitive drops WEIGHTS_0/JOINTS_0.
        node_list[0]["mesh"] = 0
        scene_children_idx = []
    # ---- animation channels ----
    channels = []
    samplers = []
    if anim_bones:
        frames_acc = 7
        # One rotation+translation channel pair per animated bone, targets
        # counted positionally (see _animation_targets; WaterSnake /
        # Wildboar references).
        for i, (b, nidx) in enumerate(_animation_targets(anim_primary, 1)):
            rot_acc = 8 + 2 * i
            tra_acc = 8 + 2 * i + 1
            for path, acc_idx in (("rotation", rot_acc),
                                  ("translation", tra_acc)):
                base = len(samplers)
                samplers.append({"input": frames_acc, "output": acc_idx,
                                 "interpolation": "LINEAR"})
                channels.append({"sampler": base,
                                 "target": {"node": nidx, "path": path}})

    prim_attrs: Dict[str, int] = {"POSITION": 1, "NORMAL": 2, "TEXCOORD_0": 3}
    if anim_bones:
        prim_attrs["WEIGHTS_0"] = 4
        prim_attrs["JOINTS_0"] = 5
    prim = {
        "attributes": prim_attrs,
        "indices": 0,
        # every one of the 396 primitives across the 98 bundled references
        # carries an explicit mode; the compound writer already emitted it
        "mode": 4,
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
        "meshes": [{"name": unit_label, "primitives": [prim]}],
        "accessors": accessors,
        "bufferViews": bufferViews,
        "buffers": [{"uri": output_name + ".bin", "byteLength": total}],
    }
    if skin is not None:
        doc["skins"] = [skin]
    if anim_bones:
        doc["animations"] = [{"channels": channels, "samplers": samplers}]
    if texture:
        # Key order and contents mirror the references verbatim.  None of the
        # 396 primitives' materials across the 98 bundled glTFs carries
        # `doubleSided`, and 380 of them are exactly
        # name / alphaMode=MASK / pbrMetallicRoughness{baseColorTexture{index,
        # texCoord}, metallicFactor} / emissiveFactor=[0,0,0].  The compound
        # writer already followed this; the single-mesh one did not.
        pbr = {
            "name": "material0",
            "alphaMode": "MASK",
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": 0, "texCoord": 0},
                "metallicFactor": 0.0,
            },
            "emissiveFactor": [0.0, 0.0, 0.0],
        }
        doc["materials"] = [pbr]
        doc["images"] = [{"uri": texture}]
        doc["textures"] = [{"source": 0}]

    return bytes(buf), doc


def write_gltf_to(path: str, mesh: SkinnedMesh, anim: Optional[AnimFile] = None,
                  texture: Optional[str] = None,
                  textures: Optional[Dict[str, str]] = None,
                  lightmaps: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """Write ``<base>.gltf`` and ``<base>.bin``; returns (gltf_path, bin_path)."""
    base = os.path.splitext(path)[0]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    bin_bytes, doc = write_gltf(mesh, anim, os.path.basename(base), texture,
                                textures=textures, lightmaps=lightmaps)
    bin_path = base + ".bin"
    with open(bin_path, "wb") as fh:
        fh.write(bin_bytes)
    with open(path, "w", encoding="utf-8") as fh:
        if mesh.parts:
            # the dis3tool reference exports are indent-2 JSON
            json.dump(doc, fh, indent=2)
        else:
            json.dump(doc, fh)
    return path, bin_path


# ---------------------------------------------------------------------------
# Compound containers (mesh.parts)
# ---------------------------------------------------------------------------

class _Sub:
    """One sub-mesh of a compound `.g` container, flattened for export."""

    __slots__ = ("name", "vertices", "indices", "bones", "morph", "lm_uv",
                 "lightmap", "material_diffuse", "zero_base")

    def __init__(self, name, vertices, indices, bones, morph, lm_uv,
                 lightmap, material_diffuse, zero_base=True):
        self.name = name
        self.vertices = vertices
        self.indices = indices
        self.bones = bones
        self.morph = morph
        self.lm_uv = lm_uv
        self.lightmap = lightmap
        self.material_diffuse = material_diffuse
        # True: morph-deformer semantics — dis3tool zeroes the base POSITION
        # block (the shape lives in the morph targets).  False: a part that
        # carries no `.g` attributes at all (rod-1's sword) lands in the
        # morph-static bucket only by the parts default; its reference export
        # keeps the real positions at the same stride-32 layout.
        self.zero_base = zero_base


def _compound_subs(mesh: SkinnedMesh) -> List[_Sub]:
    """Flatten ``mesh`` + ``mesh.parts`` into ordered sub-mesh records."""
    base_name = mesh.unit_name or mesh.name or "mesh"
    subs = [_Sub(base_name, mesh.vertices, mesh.indices, mesh.bones,
                 getattr(mesh, "morph", False), mesh.lm_uv, mesh.lightmap,
                 mesh.material_diffuse)]
    for part in mesh.parts:
        subs.append(_Sub(part.name, part.vertices, part.indices, part.bones,
                         part.morph, part.lm_uv, part.lightmap,
                         part.material_diffuse,
                         zero_base=bool(part.attrs.get("morph"))
                         or not part.morph))
    return subs


def _tex_uri(value: str, overrides: Dict[str, str], sub_name: str) -> Optional[str]:
    """Resolve the glTF image URI for a sub-mesh material attribute.

    The `.g` attribute stores a ``.tga`` name (historical 3ds Max texture
    reference); dis3tool renames it to the ``.dds`` actually shipped with
    the game.  ``overrides`` maps the sub-mesh display name (or ``"*"`` for
    a catch-all) to an explicit URI.
    """
    override = overrides.get(sub_name) or overrides.get("*")
    if override:
        return override
    if not value:
        return None
    stem, _ = os.path.splitext(value)
    return stem + ".dds"


def _compound_weights_joints(vertex) -> Tuple[List[float], List[int]]:
    """dis3tool's verbatim WEIGHTS_0/JOINTS_0 packing for compound exports.

    Same exact rule as every other export path — see
    :func:`d3tool.model.pack_weights_joints` (byte-verified against all
    292 569 skinned vertices of the reference corpus): stored lanes are
    copied verbatim, the float32 complement of the double-precision sum is
    merged into a duplicate-bone lane or appended, and joints with an
    exactly-zero weight are masked to 0.
    """
    w, j = pack_weights_joints(vertex.stored_weights, vertex.bones)
    return list(w), list(j)


def _write_compound_gltf(mesh: SkinnedMesh, anim: Optional[AnimFile],
                         output_name: str, textures: Dict[str, str],
                         lightmaps: Dict[str, str]) -> Tuple[bytes, dict]:
    """Export a compound ``.g`` container the way dis3tool does.

    Buffer layout, sub by sub (in container order): ``mesh_indexes_<name>``,
    ``mesh_vertexes_<name>`` (stride 52 skinned / 32 morph-static / 20
    static), optional ``mesh_bones_<name>`` (inverse bind matrices),
    optional ``mesh_lmuv_<name>`` (light-map UVs -> ``TEXCOORD_1``).  Then
    the shared animation arrays (``frames`` / ``bones_rotate`` /
    ``bones_translate``), one ``morph_<name>_<k>`` float3 position buffer
    per morph-target frame per morph sub-mesh, and finally the single
    identity ``morph_weights`` matrix sampled by every ``weights`` channel.
    Accessor order per sub: indices, POSITION, NORMAL, TEXCOORD_0,
    WEIGHTS_0, JOINTS_0, IBM, TEXCOORD_1 (light-map accessor always last for
    its sub-mesh); then frames + interleaved rotation/translation channels;
    then the morph-target buffers; then the weights matrix accessor.
    """
    subs = _compound_subs(mesh)
    anim_bones: List[BoneAnim] = anim.bones if anim and anim.bones else []
    # Only the first stream's bones are animated (same rule as the single-mesh
    # writer): bones concat_anims appended from a later `.a` — DarkServant's
    # Bone02, carried only by its `_run.a` — get their trailing node but no
    # channel and no rotation/translation storage in the reference export.
    anim_primary: List[BoneAnim] = (
        anim_bones[:anim.n_primary] if anim and anim.n_primary else anim_bones)
    n_frames = 0
    if anim_bones:
        n_frames = anim.frame_count or max(len(b.frames) for b in anim_bones) or 1

    # morph streams, matched to sub-meshes by name (with a unique-vertex-size
    # fallback for `.a` files that carry foreign streams, e.g. DarkServant)
    morph_tracks = list(anim.morphs) if anim else []
    sub_track: List[Optional[MorphTrack]] = [None] * len(subs)
    used: set = set()
    for i, sub in enumerate(subs):
        if not sub.morph:
            continue
        for track in morph_tracks:
            if track.name == sub.name and id(track) not in used:
                sub_track[i] = track
                used.add(id(track))
                break
    for i, sub in enumerate(subs):
        if not sub.morph or sub_track[i] is not None:
            continue
        cands = [t for t in morph_tracks
                 if t.vertex_count == len(sub.vertices) and id(t) not in used]
        if len(cands) == 1:
            sub_track[i] = cands[0]
            used.add(id(cands[0]))

    # ---- binary blobs, in reference order ----
    blobs: List[Tuple[str, bytes, Optional[int], Optional[int]]] = []

    def _append(name, data, target=None, stride=None):
        blobs.append((name, data, target, stride))
        return len(blobs) - 1

    sub_blobs: List[dict] = []
    for sub in subs:
        entry = {}
        entry["indexes"] = _append(
            f"mesh_indexes_{sub.name}",
            b"".join(struct.pack("<I", x) for x in sub.indices), 34963)
        if sub.bones:
            # dis3tool copies the vertex verbatim; a handful of assets
            # carry NaN normals (e.g. the HolyAvenger muzzle) and the
            # reference export keeps NaN as-is
            vbuf = b"".join(
                struct.pack("<3f3f2f4f4B", *v.position, *v.normal, *v.uv,
                            *w, *j)
                for v in sub.vertices
                for w, j in (_compound_weights_joints(v),)
            )
            entry["vertexes"] = _append(f"mesh_vertexes_{sub.name}",
                                        vbuf, 34962, 52)
        elif sub.morph and sub.zero_base:
            # dis3tool zeroes the base POSITION of a morph-deformer mesh —
            # the morph targets carry the full absolute shape per frame.
            vbuf = b"".join(
                struct.pack("<3f3f2f", 0.0, 0.0, 0.0, *v.normal, *v.uv)
                for v in sub.vertices
            )
            entry["vertexes"] = _append(f"mesh_vertexes_{sub.name}", vbuf,
                                        34962, 32)
        elif sub.morph:
            # morph-static layout, but the part has no `.g` attributes at all
            # (rod-1's sword): the reference keeps the real base positions in
            # the buffer — at this stride, unlike a true static (stride 20) —
            # while still reporting zeroed POSITION min/max (the accessor
            # loop below keeps the zeroed bounds for every morph bucket).
            vbuf = b"".join(
                struct.pack("<3f3f2f", *v.position, *v.normal, *v.uv)
                for v in sub.vertices
            )
            entry["vertexes"] = _append(f"mesh_vertexes_{sub.name}", vbuf,
                                        34962, 32)
        else:
            vbuf = b"".join(struct.pack("<3f2f", *v.position, *v.uv)
                            for v in sub.vertices)
            entry["vertexes"] = _append(f"mesh_vertexes_{sub.name}", vbuf,
                                        34962, 20)
        if sub.bones:
            entry["bones"] = _append(
                f"mesh_bones_{sub.name}",
                b"".join(struct.pack("<16f", *b.matrix) for b in sub.bones))
        if sub.lm_uv:
            entry["lmuv"] = _append(f"mesh_lmuv_{sub.name}", sub.lm_uv)
        sub_blobs.append(entry)

    # frames / rotations / translations (dis3tool order, shared by all skins
    # and by the morph-weights samplers)
    frames: List[float] = []
    rot: List[float] = []
    tra: List[float] = []
    n_morph_frames = 0
    for t in sub_track:
        if t is not None:
            n_morph_frames = t.frame_count
            break
    if anim_bones:
        if n_frames > 1:
            step = _F32(1.0 / 30.0)
            frames = [k * step for k in range(n_frames)]
        else:
            frames = [0.0]
        for b in anim_primary:
            for k in range(n_frames):
                fr = b.frames[k] if k < len(b.frames) else b.rest[:7]
                rot.extend(fr[0:4])
                tra.extend(fr[4:7])
        frames_bv = _append("frames", struct.pack(f"<{len(frames)}f", *frames))
        rot_bv = _append("bones_rotate", struct.pack(f"<{len(rot)}f", *rot))
        tra_bv = _append("bones_translate", struct.pack(f"<{len(tra)}f", *tra))
    elif n_morph_frames:
        # morph-only animation object (no bone tracks at all, e.g.
        # fatimp_lod.a): the weights samplers still need a times input
        step = _F32(1.0 / 30.0)
        frames = ([k * step for k in range(n_morph_frames)]
                  if n_morph_frames > 1 else [0.0])
        frames_bv = _append("frames", struct.pack(f"<{len(frames)}f", *frames))

    # morph target buffers: all frames of sub A, then all frames of sub B
    # (sub order), each with `vertex_count` absolute float3 positions
    morph_blobs: List[List[int]] = [[] for _ in subs]
    for si, (_sub, track) in enumerate(zip(subs, sub_track)):
        if track is None:
            continue
        rec = track.vertex_count * 12
        # dis3tool names the morph buffers after the `.a` stream, not after
        # the mesh (they differ for e.g. the Goblin `neutrals_` streams)
        for k in range(track.frame_count):
            morph_blobs[si].append(_append(
                f"morph_{track.name}_{k}",
                track.positions[k * rec:(k + 1) * rec], 34962))
    n_morph_frames = 0
    for track in sub_track:
        if track is not None:
            n_morph_frames = track.frame_count
            break
    # The identity matrix is sized by the *total animation frame count*, not by
    # the morph-target count.  Verified against the Cleric reference: its
    # morph_weights bufferView is 580644 bytes = 381*381*4, and all 145161
    # cells match a 381x381 identity exactly, while the mesh carries only 25
    # morph targets.  For a morph-only object (no bone tracks) the frame count
    # and the target count coincide.
    n_weight_frames = n_frames if anim_bones else n_morph_frames
    if n_morph_frames:
        block = bytearray()
        for i in range(n_weight_frames):
            block += struct.pack("<f", 0.0) * i
            block += struct.pack("<f", 1.0)
            block += struct.pack("<f", 0.0) * (n_weight_frames - 1 - i)
        weights_bv = _append("morph_weights", bytes(block))

    # ---- assemble buffer + bufferViews ----
    buf = bytearray()
    bufferViews: List[dict] = []
    bv_of: List[int] = []
    for name, data, target, stride in blobs:
        view = {"name": name, "buffer": 0, "byteOffset": len(buf),
                "byteLength": len(data)}
        buf += data
        if stride is not None:
            view["byteStride"] = stride
        if target is not None:
            view["target"] = target
        bufferViews.append(view)
        bv_of.append(len(bufferViews) - 1)

    # ---- accessors, in reference order ----
    accessors: List[dict] = []

    def _acc(bv, ct, count, typ, off=0, extra=None):
        a = {"bufferView": bv_of[bv], "byteOffset": off, "componentType": ct,
             "count": count, "type": typ}
        if extra:
            a.update(extra)
        accessors.append(a)
        return len(accessors) - 1

    subs_acc: List[Tuple[int, dict, Optional[int]]] = []
    for sub, entry in zip(subs, sub_blobs):
        idx_acc = _acc(entry["indexes"], 5125, len(sub.indices), "SCALAR")
        if sub.morph and not sub.bones:
            # the base POSITION block of a morph mesh is all-zero (the shape
            # lives in the targets) and dis3tool reports zeroed min/max too
            pos_mm = {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]}
        else:
            xs = [v.position[0] for v in sub.vertices]
            ys = [v.position[1] for v in sub.vertices]
            zs = [v.position[2] for v in sub.vertices]
            pos_mm = {"min": [min(xs), min(ys), min(zs)],
                      "max": [max(xs), max(ys), max(zs)]} if sub.vertices else None
        attrs = {}
        attrs["POSITION"] = _acc(entry["vertexes"], 5126, len(sub.vertices),
                                 "VEC3", 0, pos_mm)
        if sub.bones or sub.morph:
            attrs["NORMAL"] = _acc(entry["vertexes"], 5126,
                                   len(sub.vertices), "VEC3", 12)
            attrs["TEXCOORD_0"] = _acc(entry["vertexes"], 5126,
                                       len(sub.vertices), "VEC2", 24)
        else:
            attrs["TEXCOORD_0"] = _acc(entry["vertexes"], 5126,
                                       len(sub.vertices), "VEC2", 12)
        ibm_acc = None
        if sub.bones and anim_bones:
            attrs["WEIGHTS_0"] = _acc(entry["vertexes"], 5126,
                                      len(sub.vertices), "VEC4", 32)
            attrs["JOINTS_0"] = _acc(entry["vertexes"], 5121,
                                     len(sub.vertices), "VEC4", 48)
            ibm_acc = _acc(entry["bones"], 5126, len(sub.bones), "MAT4")
        if sub.lm_uv:
            # TEXCOORD_1 always comes last in the sub's accessor range
            attrs["TEXCOORD_1"] = _acc(entry["lmuv"], 5126,
                                       len(sub.vertices), "VEC2")
        subs_acc.append((idx_acc, attrs, ibm_acc))

    frames_acc = rot_acc0 = None
    if anim_bones:
        frames_acc = _acc(frames_bv, 5126, len(frames), "SCALAR", 0,
                          {"min": [min(frames)], "max": [max(frames)]})
        rot_acc0 = _acc(rot_bv, 5126, n_frames, "VEC4")
        first_tra = _acc(tra_bv, 5126, n_frames, "VEC3")
        assert first_tra == rot_acc0 + 1
        for i in range(1, len(anim_primary)):
            _acc(rot_bv, 5126, n_frames, "VEC4", i * n_frames * 16)
            _acc(tra_bv, 5126, n_frames, "VEC3", i * n_frames * 12)
    elif n_morph_frames:
        frames_acc = _acc(frames_bv, 5126, len(frames), "SCALAR", 0,
                          {"min": [min(frames)], "max": [max(frames)]})

    morph_acc: List[Optional[List[int]]] = [None] * len(subs)
    for si, track in enumerate(sub_track):
        if track is None:
            continue
        accs = []
        rec = track.vertex_count * 12
        for k, blob in enumerate(morph_blobs[si]):
            frame = track.positions[k * rec:(k + 1) * rec]
            pts = struct.unpack(f"<{track.vertex_count * 3}f", frame)
            pos_mm = {
                "min": [min(pts[0::3]), min(pts[1::3]), min(pts[2::3])],
                "max": [max(pts[0::3]), max(pts[1::3]), max(pts[2::3])],
            } if track.vertex_count else None
            accs.append(_acc(blob, 5126, track.vertex_count, "VEC3", 0, pos_mm))
        morph_acc[si] = accs
    weights_acc = None
    if n_morph_frames:
        # quirk of the dis3tool exporter: the closing morph_weights accessor
        # carries no explicit byteOffset key
        acc = {"bufferView": bv_of[weights_bv], "componentType": 5126,
               "count": n_weight_frames * n_weight_frames, "type": "SCALAR"}
        accessors.append(acc)
        weights_acc = len(accessors) - 1

    # ---- nodes ----
    n_subs = len(subs)
    node_list: List[dict] = []
    name_to_idx: Dict[str, int] = {}
    scene_roots: List[int] = []
    if anim_bones:
        children, parent, order, roots = node_hierarchy(
            anim_bones, anim.n_primary if anim else 0)
        for off, nm in enumerate(order):
            name_to_idx[nm] = n_subs + off
        bmap: Dict[str, BoneAnim] = _first_by_name(anim_bones)

    # sub-mesh nodes first (flat siblings), then the skeleton hierarchy
    skins: List[dict] = []
    n_skin = 0
    for si, sub in enumerate(subs):
        node = {"name": sub.name, "mesh": si}
        if sub.bones and anim_bones:
            node["skin"] = n_skin
            n_skin += 1
        node_list.append(node)
    if anim_bones:
        for nm in order:
            b = bmap[nm]
            rest = b.frames[0][:7] if b.frames else b.rest[:7]
            node = {"name": nm, "rotation": list(rest[0:4]),
                    "translation": list(rest[4:7])}
            kids = [name_to_idx[c] for c in children.get(nm, [])]
            if kids:
                node["children"] = kids
            node_list.append(node)
        # sub-bones missing from the animation bind to the skeleton root —
        # that is what dis3tool emits (e.g. the WitchHunter's
        # Marksman_eyelid_up maps onto the Reference root), not a stub node
        root_idx = name_to_idx[order[0]] if order else n_subs
        for sub in subs:
            for b in sub.bones:
                name_to_idx.setdefault(b.name, root_idx)
        # dis3tool scene roots = every skeleton node whose parent is not a
        # bone, in node order — normally just the root, but DarkServant's
        # `ROOT_demons_thief_lod` / `Bone02` (parent `Scene Root`) trail as
        # extra roots in its reference (scene nodes 4, 72, 73).
        scene_roots = [name_to_idx[nm] for nm in order
                       if parent.get(nm) not in children]

    for si, sub in enumerate(subs):
        if not sub.bones or not anim_bones:
            # without animation dis3tool emits no skins at all (the skinned
            # stride and IBM buffers stay, but the primitives lose
            # WEIGHTS_0/JOINTS_0 and the scene only holds the sub-meshes)
            continue
        joints = [name_to_idx[b.name] for b in sub.bones]
        skins.append({"name": f"skin{len(skins)}", "joints": joints,
                      "inverseBindMatrices": subs_acc[si][2]})

    # ---- meshes / materials ----
    images: List[dict] = []
    textures_js: List[dict] = []
    image_slot: Dict[str, int] = {}

    def _image(uri: str) -> int:
        slot = image_slot.get(uri)
        if slot is None:
            slot = len(images)
            image_slot[uri] = slot
            images.append({"uri": uri})
            textures_js.append({"source": slot})
        return slot

    meshes_js: List[dict] = []
    mat_list: List[dict] = []
    for si, sub in enumerate(subs):
        idx_acc, attrs, _ibm = subs_acc[si]
        prim = {"attributes": attrs, "indices": idx_acc, "mode": 4}
        track = sub_track[si]
        if track is not None:
            prim["targets"] = [
                {"POSITION": a} for a in (morph_acc[si] or [])]
        mat = {"name": f"material{si}", "alphaMode": "MASK"}
        diffuse_uri = _tex_uri(sub.material_diffuse, textures, sub.name)
        lm_uri = _tex_uri(sub.lightmap, lightmaps, sub.name)
        if diffuse_uri:
            mat["pbrMetallicRoughness"] = {
                "baseColorTexture": {"index": _image(diffuse_uri),
                                     "texCoord": 0},
                "metallicFactor": 0.0}
        if lm_uri:
            mat["normalTexture"] = {"index": _image(lm_uri), "texCoord": 0}
        mat["emissiveFactor"] = [0.0, 0.0, 0.0]
        mat_list.append(mat)
        prim["material"] = si
        mesh_js = {"name": sub.name, "primitives": [prim]}
        if track is not None:
            mesh_js["weights"] = [0.0] * track.frame_count
        meshes_js.append(mesh_js)

    # ---- final document ----
    doc: dict = {
        "asset": {"version": "2.0", "generator": "d3tool (geo2011 reverse)"},
        "scene": 0,
        "scenes": [{"nodes": list(range(n_subs)) + scene_roots}],
        "nodes": node_list,
        "meshes": meshes_js,
        "accessors": accessors,
        "bufferViews": bufferViews,
        "buffers": [{"uri": output_name + ".bin", "byteLength": len(buf)}],
        "images": images,
        "textures": textures_js,
        "materials": mat_list,
    }
    if skins:
        # dis3tool omits the key entirely for animation-less exports
        doc["skins"] = skins

    if anim_bones or any(t is not None for t in sub_track):
        channels = []
        samplers = []
        if anim_bones:
            # One rotation+translation channel pair per primary bone — the
            # same positional rule as the single-mesh writer (see
            # _animation_targets).
            for i, (b, nidx) in enumerate(_animation_targets(anim_primary,
                                                             n_subs)):
                rot_acc = rot_acc0 + 2 * i
                tra_acc = rot_acc0 + 2 * i + 1
                for path, acc in (("rotation", rot_acc),
                                  ("translation", tra_acc)):
                    samplers.append({"input": frames_acc, "output": acc,
                                     "interpolation": "LINEAR"})
                    channels.append({"sampler": len(samplers) - 1,
                                     "target": {"node": nidx, "path": path}})
            # dis3tool quirk reproduced from the Rod-1 reference: an attrless
            # static part (exported at the morph-static stride with real
            # positions) makes the exporter append one stray sampler aimed at
            # the accessor index just past the end — output 33 of 33 — that
            # no channel ever references.
            if any(not s.zero_base for s in subs):
                samplers.append({"input": frames_acc,
                                 "output": len(accessors),
                                 "interpolation": "LINEAR"})
        if frames_acc is not None and weights_acc is not None:
            for si, track in enumerate(sub_track):
                if track is None:
                    continue
                samplers.append({"input": frames_acc, "output": weights_acc,
                                 "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1,
                                 "target": {"node": si, "path": "weights"}})
        doc["animations"] = [{"channels": channels, "samplers": samplers}]

    return bytes(buf), doc


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
    # An empty / trivially-incomplete document (no asset/version or no scenes)
    # should not pass silently: report it rather than return 0 errors.
    if doc.get("asset", {}).get("version") is None:
        errors += 1
        warnings += 1
        info = 0
        return errors, warnings, info
    nacc = len(doc.get("accessors", []))
    # guard against a missing / malformed buffer without raising
    buf_uri = doc.get("buffers", [{}])[0].get("uri", "") if doc.get("buffers") else ""
    bin_data = b""
    if buf_uri:
        bp = os.path.join(base_dir, buf_uri)
        if os.path.exists(bp):
            bin_data = open(bp, "rb").read()
            declared = doc["buffers"][0].get("byteLength")
            if declared is not None and len(bin_data) < declared:
                errors += 1
        else:
            errors += 1

    # a bufferView must lie wholly inside the buffer it names.  Without this
    # the accessor check below can pass while the view itself points past the
    # end of the .bin, which is exactly the kind of mistake a writer that
    # accumulates byteOffsets by hand makes.
    views = doc.get("bufferViews", [])
    for v in views:
        bl = _at(doc.get("buffers", []), v.get("buffer", 0))
        total = bl.get("byteLength") if bl else None
        if total is None:
            continue
        if v.get("byteOffset", 0) + v.get("byteLength", 0) > total:
            errors += 1

    # every accessor must reference a valid bufferView and its data must fit
    # within that bufferView.  accessor.byteOffset is relative to the view;
    # the view's own byteOffset is relative to the buffer.
    for a in doc.get("accessors", []):
        bv = _at(views, a.get("bufferView", -1))
        if bv is None:
            errors += 1
            continue
        ncomp = _NCOMP.get(a.get("type"))
        size = _CTSIZE.get(a.get("componentType"))
        if ncomp is None or size is None:
            # an unknown accessor type/componentType is an error, not a crash
            errors += 1
            continue
        need = a["count"] * ncomp * size
        off = a.get("byteOffset", 0)
        if off + need > bv.get("byteLength", 0):
            errors += 1
    # skin joints must be valid node indices
    n_nodes = len(doc.get("nodes", []))
    # ...and so must animation channel targets.  dis3tool aims the channels
    # of duplicate-name bones at dangling indices right past the node list
    # (WaterSnake targets 47..50 with 47 nodes, Wildboar 37 with 37) and the
    # references reproduce that verbatim, so `_animation_targets` does too.
    # Such a contiguous dangling block is reported as a warning; anything
    # else out of range stays a hard error.
    for anim in doc.get("animations", []):
        bad = [ch["target"]["node"] for ch in anim.get("channels", [])
               if ch.get("target", {}).get("node") is not None
               and not 0 <= ch["target"]["node"] < n_nodes]
        for ni in bad:
            if 0 <= ni - n_nodes < len(bad):
                warnings += 1
            else:
                errors += 1
    for s in doc.get("skins", []):
        if "joints" not in s:
            errors += 1
            continue
        for j in s["joints"]:
            if not 0 <= j < n_nodes:
                errors += 1
    # WEIGHTS_0 must sum to 1.0 per vertex (spec REQUIRES normalized weights).
    # The Khronos validator flags non-normalized weight sums, which our own
    # structural check must catch too.  We also mirror its dedup-by-joint rule:
    # when the same joint index appears in several influence slots, the weights
    # for those slots are summed (a vertex that maps two influences to the same
    # joint only "uses" one joint, so the leftover weight must still total 1).
    def _read_accessor(index: int, ncomp: int):
        """Read ``count`` x ``ncomp`` elements of an accessor as floats.

        WEIGHTS_0 is float32 but JOINTS_0 is normally ``componentType`` 5121
        (unsigned byte) inside the same interleaved view, so the element size
        and format must come from the accessor — reading the joint bytes as
        float32 decodes them as garbage and silently defeats the
        dedup-by-joint rule below.
        """
        a = _at(doc["accessors"], index)
        if a is None:
            raise _BufferShortError(0, 0, 0, len(bin_data))
        bv = _at(doc.get("bufferViews", []), a.get("bufferView", -1))
        if bv is None:
            raise _BufferShortError(0, 0, 0, len(bin_data))
        size = _CTSIZE.get(a.get("componentType"), 4)
        fmt = _CTFMT.get(a.get("componentType"), "f")
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride", ncomp * size)
        if off + a["count"] * stride > len(bin_data):
            # data does not fit in the buffer; mark via a sentinel the caller
            # can count instead of crashing with struct.error.
            raise _BufferShortError(off, a["count"], stride, len(bin_data))
        return [
            struct.unpack_from("<" + fmt * ncomp, bin_data, off + i * stride)
            for i in range(a["count"])
        ]

    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            w_idx = attrs.get("WEIGHTS_0")
            j_idx = attrs.get("JOINTS_0")
            if w_idx is None:
                continue
            try:
                weights = _read_accessor(w_idx, 4)
                joints = _read_accessor(j_idx, 4) if j_idx is not None else None
            except _BufferShortError:
                errors += 1
                continue
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
    # Morph-weight samplers are special: each input tick expands to a scalar
    # per morph target, so output.count == input.count * len(prim.targets).
    for anim in doc.get("animations", []):
        path_of = {c["sampler"]: c["target"]["path"] for c in anim["channels"]}
        node_of = {c["sampler"]: c["target"].get("node")
                   for c in anim["channels"]}
        for idx, smp in enumerate(anim["samplers"]):
            i_acc = _at(doc["accessors"], smp.get("input", -1))
            o_acc = _at(doc["accessors"], smp.get("output", -1))
            if i_acc is None or o_acc is None:
                # dis3tool quirk (Rod-1 reference): one stray sampler per
                # attrless static part, aimed at the accessor index just past
                # the end and referenced by no channel.  `write_gltf`
                # reproduces it; flag it only as a warning.
                if idx not in path_of and smp.get("output") == nacc:
                    warnings += 1
                else:
                    errors += 1
                continue
            want = path_of.get(idx)
            if want == "weights":
                n_targets = 0
                node_i = node_of.get(idx)
                if node_i is not None and node_i < len(doc.get("nodes", [])):
                    mesh_i = doc["nodes"][node_i].get("mesh")
                    if mesh_i is not None:
                        prims = doc["meshes"][mesh_i].get("primitives", [])
                        if prims:
                            n_targets = len(prims[0].get("targets", []))
                if n_targets:
                    # dis3tool writes the morph-weight output as an
                    # input.count * input.count square, not the spec's
                    # input.count * len(targets); `_write_compound_gltf`
                    # replicates that for byte parity.  Both shapes are
                    # accepted so the validator does not flag its own
                    # output — nor the 8 bundled reference files that
                    # carry a target count different from the frame count.
                    if o_acc["count"] not in (
                            i_acc["count"] * n_targets,
                            i_acc["count"] * i_acc["count"]):
                        errors += 1
                elif o_acc["count"] != i_acc["count"]:
                    errors += 1
            elif i_acc["count"] != o_acc["count"]:
                errors += 1
            if want == "rotation" and o_acc["type"] != "VEC4":
                errors += 1
            elif want == "translation" and o_acc["type"] != "VEC3":
                errors += 1
    # mesh node must reference an existing mesh
    n_meshes, n_skins = len(doc.get("meshes", [])), len(doc.get("skins", []))
    for n in doc.get("nodes", []):
        if "mesh" in n and not 0 <= n["mesh"] < n_meshes:
            errors += 1
        if "skin" in n and not 0 <= n["skin"] < n_skins:
            errors += 1
    info = max(0, nacc)
    return errors, warnings, info
