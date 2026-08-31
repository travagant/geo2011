"""Neutral data model shared between the dis3tool glTF side and the original
Disciples 3 (GM) side.

The GM engine file format (`.g`) and the glTF exported by *dis3tool* describe the
same skinned mesh.  The geometry is a flat list of vertices with:

* position (3 x float32)
* normal   (3 x float32)
* diffuse  (u32 RGBA colour, the exporter almost always writes 0xFFFFFFFF)
* uv       (2 x float32)
* stored weights (float32) -- the first ``weights_on_vertex - 1`` weights
* bone indices  (u8)       -- ``weights_on_vertex`` influencing bones

The game engine only stores the first ``w-1`` weights; the final weight is
implicitly ``1 - sum(stored)`` so the four influence weights always sum to one.
``weights_on_vertex`` (``w``) is therefore the number of *influence slots*.
:func:`pack_weights_joints` implements the exact float32 packing dis3tool
applies to these numbers on glTF export.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def _f32(x: float) -> float:
    """Round to the nearest float32 (ties-to-even), like the C++ exporter."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def pack_weights_joints(
    stored_weights: Tuple[float, ...], bones: Tuple[int, ...]
) -> Tuple[Tuple[float, float, float, float], Tuple[int, int, int, int]]:
    """Exact dis3tool ``WEIGHTS_0``/``JOINTS_0`` packing for one vertex.

    Byte-verified against every skinned vertex of the 85-unit reference
    corpus (292 569 vertices, 0 mismatches).  The rule, mirroring the C++
    exporter:

    * the stored lanes are copied verbatim (float32, no renormalisation,
      no trailing-zero trimming);
    * the complement ``c = float32(1.0 - sum(stored))`` is computed from
      the **double-precision** sum (not a float32 running sum);
    * ``c > 0`` is merged into the first already-listed lane whose bone
      equals the implied bone ``bones[len(stored)]`` (single-precision
      add), or appended as an extra lane when no such lane exists;
    * ``c <= 0`` changes nothing (the stored lanes already reach 1.0f or
      overshoot it by rounding -- both stay verbatim);
    * ``JOINTS_0`` is the bone array zero-padded to 4 lanes with every
      lane whose **exact** weight is ``0.0`` masked to joint 0 (tiny
      residues like ``2.98e-08`` keep their joint, matching the
      reference bytes).

    A vertex with no stored lanes at all reads as rigid: full weight on
    its single bone (or on joint 0 when it has no bones).
    """
    s = [_f32(x) for x in stored_weights]
    b = [int(x) & 0xFF for x in bones]
    if not s:
        j0 = b[0] if b else 0
        return (1.0, 0.0, 0.0, 0.0), (j0, 0, 0, 0)
    n = len(s)
    while len(b) <= n:
        b.append(0)
    implied = b[n]
    c = _f32(1.0 - sum(s))  # double-precision sum, then one f32 rounding
    out = list(s)
    dup = next((i for i in range(n) if b[i] == implied), None)
    if c > 0.0:
        if dup is not None:
            out[dup] = _f32(out[dup] + c)
        else:
            out.append(c)
    w = (out + [0.0, 0.0, 0.0, 0.0])[:4]
    j = [bb if wt != 0.0 else 0
         for bb, wt in zip((b + [0, 0, 0, 0])[:4], w)]
    return (w[0], w[1], w[2], w[3]), (j[0], j[1], j[2], j[3])


# default per-vertex "diffuse" GP colour written by dis3tool (opaque white)
DEFAULT_DIFFUSE: int = 0xFFFFFFFF


@dataclass
class Vertex:
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    uv: Tuple[float, float] = (0.0, 0.0)
    diffuse: int = DEFAULT_DIFFUSE
    # first (weights_on_vertex - 1) stored weights; the last is implied
    stored_weights: Tuple[float, ...] = (1.0,)
    # influence bone indices (length == weights_on_vertex)
    bones: Tuple[int, ...] = (0, 0)

    def influence_weights(self) -> Tuple[float, ...]:
        """Weights for the ``len(bones)`` influence slots (last implied)."""
        w, _ = pack_weights_joints(self.stored_weights, self.bones)
        n = max(len(self.bones), 1)
        return w[:n]

    @property
    def gltf_weights(self) -> Tuple[float, float, float, float]:
        return pack_weights_joints(self.stored_weights, self.bones)[0]

    @property
    def gltf_joints(self) -> Tuple[int, int, int, int]:
        # dis3tool resets a joint index to 0 in any influence slot whose
        # exact weight is 0.0 (padding).  Keeping the raw `.g` bone index
        # there would make the Khronos validator flag the vertex as having
        # a joint index used with zero weight (and report duplicate
        # joints).  Tiny non-zero residues (e.g. 2.98e-08) keep their
        # joint -- byte parity with the reference export wins.
        return pack_weights_joints(self.stored_weights, self.bones)[1]


@dataclass
class Bone:
    name: str
    # 4x4 row-major local (bind) transform relative to the parent bone
    matrix: Tuple[float, ...] = field(default_factory=lambda: (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ))


@dataclass
class MorphTrack:
    """Vertex-morph stream stored in an `.a` file's trailing block.

    Layout (little-endian): ``[u32 14][u32 len-8][u32 15][frame_count]
    [vertex_count][name_len][name + NUL]`` then ``frame_count * vertex_count
    * 3`` float32 positions.  dis3tool lifts every frame into a glTF morph
    target and synthesises the identity ``morph_weights`` matrix.
    """

    name: str = ""
    frame_count: int = 0
    vertex_count: int = 0
    # raw position bytes: frame-major, vertex_count * vec3 float32 per frame
    positions: bytes = b""
    # record-type tag (third u32; observed 15 / 16 / 30) and the verbatim
    # full record bytes (header + name + positions) when parsed from a file
    tag: int = 15
    raw_record: bytes = b""


@dataclass
class MeshPart:
    """One sub-mesh of a compound `.g` container (after the first mesh).

    Empire characters stack several mesh nodes in a single `.g` — weapon
    (``w = 1``), body, hair and a morph cloak — each introduced by a
    ``[7 x u32]`` header (tag 2, id, 0, vertex_count, tri_count, ?, 6), a
    56-byte scene block and its own attribute block.  ``raw`` keeps the exact
    original sub-block bytes so the compound round-trips byte-for-byte.
    """

    name: str = ""
    vertex_count: int = 0
    tri_count: int = 0
    vertices: List[Vertex] = field(default_factory=list)
    indices: List[int] = field(default_factory=list)
    bones: List[Bone] = field(default_factory=list)
    weights_on_vertex: int = 0
    morph: bool = False
    # the donated attribute block verbatim (ordered pairs, duplicates kept)
    attr_items: List[Tuple[str, str]] = field(default_factory=list)
    material_diffuse: str = ""
    vertex_magic: bytes = b""
    # lightmap UV block (vc * vec2 float32), exported as TEXCOORD_1
    lm_uv: bytes = b""
    # material0_lightmap value (dis3tool puts it into glTF normalTexture)
    lightmap: str = ""
    attrs: Dict[str, str] = field(default_factory=dict)
    # verbatim sub-block bytes (header + scene + attrs + arrays + bones)
    raw: bytes = b""
    # sub-block scaffolding kept so a *rebuilt* part (reverse export) can
    # reproduce the container layout: the bytes between the block start and
    # the attribute block (28-byte header + 56-byte scene block, or the rare
    # attribute-first prefix), and the bytes that follow the geometry arrays
    # (baked morph-frame positions).  Both empty for a synthesized part.
    part_prefix: bytes = b""
    part_tail: bytes = b""


@dataclass
class SkinnedMesh:
    name: str = ""
    geometry_file: str = ""
    vertex_count: int = 0
    tri_count: int = 0
    vertices: List[Vertex] = field(default_factory=list)
    indices: List[int] = field(default_factory=list)
    bones: List[Bone] = field(default_factory=list)
    material_diffuse: str = ""
    # 4-byte per-vertex prefix written by dis3tool (asset specific)
    vertex_magic: bytes = bytes.fromhex("5ce6ac0b")
    # number of influence slots per vertex (2, 3 or 4)
    weights_on_vertex: int = 2
    # raw bytes of the prelude + scene-node block (asset specific)
    preamble: bytes = b""
    # raw 120-byte binary header (asset specific)
    header: bytes = b""
    # logical armature / node name (from the `name` attribute), may differ
    # from the leading `name1` string of the binary for LOD files
    unit_name: str = ""
    # any trailing bytes after the bone descriptor array (morph / shadow etc.)
    trailing: bytes = b""
    # raw frame marker: when set, write_geometry_file emits these bytes
    # verbatim instead of re-serialising (used for compound / non-character
    # .g files whose layout is not a single character mesh)
    raw: bytes = b""
    # Container form: ``"classic"`` (120-byte header + two name strings before
    # the prelude) or ``"stub"`` (a header-less node helper — prelude first, no
    # name strings, e.g. the Leader-Ranger/Leader-Thief ``BaseMesh`` files).
    # Set by the reader; write_geometry_file emits the matching layout.
    form: str = "classic"
    # a boneless vertex-morph mesh (attr `morph`/`morph_track`): positions
    # live in 40-byte records and the actual shape comes from the `.a` morph
    # stream (compounds can also have a *base* morph mesh, e.g. the
    # Kingsguard raincoat).
    morph: bool = False
    # lightmap UV block (vc * vec2 float32) sitting between the index block
    # and the bone descriptors; exported as TEXCOORD_1.
    lm_uv: bytes = b""
    # set only when the binary could not be parsed at all, in which case
    # `raw` holds the whole file verbatim.  Distinct from `raw` itself,
    # which compound containers also use; callers must not read a truthy
    # `raw` as "compound".
    parse_error: Optional[str] = None
    # material0_lightmap value (dis3tool puts it into glTF normalTexture)
    lightmap: str = ""
    # additional sub-meshes of a compound container (mesh 2..N); the first
    # mesh is described by this SkinnedMesh's own fields.
    parts: List[MeshPart] = field(default_factory=list)
    # the main mesh's attribute block, as parsed (donated on reverse export)
    attrs: Dict[str, str] = field(default_factory=dict)
    # the donated main attribute block verbatim (ordered pairs, dupes kept)
    attr_items: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class GltfModel:
    """glTF side of the asset (reconstructed from the dis3tool export)."""

    mesh_name: str = ""
    geometry_file: str = ""
    vertex_count: int = 0
    tri_count: int = 0
    vertices: List[Vertex] = field(default_factory=list)
    indices: List[int] = field(default_factory=list)
    nodes: List[dict] = field(default_factory=list)
    bones: List[Bone] = field(default_factory=list)
    bone_indices: Dict[str, int] = field(default_factory=dict)
    frames: List[float] = field(default_factory=list)
    animation: Optional[dict] = None
    # node index -> {"rotation": [quat], "translation": [vec3]} per frame
    anim_channels: Dict[int, dict] = field(default_factory=dict)
    # number of influence slots carried by the source glTF (2/3/4); used for
    # fidelity when reverse-exporting (0 == auto-detect).
    weights_on_vertex: int = 0
    # True when the glTF carries no skin and no WEIGHTS_0/JOINTS_0 at all
    # (a rigid dis3tool export): the GM influence data simply is not in the
    # document, only in the (donated) original `.g`.
    rigid: bool = False
    # sub-mesh material names (the historical `.tga` spelling) and light-map
    # payload, restored on reverse export
    material_diffuse: str = ""
    lightmap: str = ""
    lm_uv: bytes = b""
    # the mesh carries morph targets (a morph-deformer sub-mesh)
    morph: bool = False
    # per-vertex raw WEIGHTS_0/JOINTS_0 accessor lanes of the source glTF
    # (empty for a rigid export) -- the ground truth a donated original is
    # verified against on reverse export
    accessor_wj: List[tuple] = field(default_factory=list)
    # morph-target animation on this sub-mesh: the `.a` stream name (from the
    # ``morph_<name>_<k>`` bufferView names) and the verbatim per-frame
    # POSITION accessor bytes (one entry per baked frame)
    morph_name: str = ""
    target_positions: List[bytes] = field(default_factory=list)
    # sub-meshes 2..N of a compound export (meshes[1:]); the first mesh is
    # described by this model's own fields.
    submodels: List["GltfModel"] = field(default_factory=list)
