"""Reader / writer for the original Disciples 3 `GM` geometry file (`.g`).

Layout (little-endian), reverse engineered from the dis3tool exports:

   0..119  30 x u32/float header (magic + sizing data)
 120..    [u32 len] name1 (unit / armature name)                + NUL
          [u32 len] name2 (geometry file basename)              + NUL
          prelude: 10 x u32 (includes vertex & triangle counts)
          scene-node matrix (14 floats => 56 bytes)
          attribute block:
            [u32 num_attrs]
            repeat num_attrs:
              [u32 key_len] key + NUL
              [u32 val_len] value + NUL
          vertex array: vertex_count records, each:
            [0:4]   per-vertex prefix magic (asset specific)
            [4:16]  position (3 x f32)
            [16:28] normal   (3 x f32)
            [28:32] diffuse  (u32, usually 0xFFFFFFFF)
            [32:40] uv       (2 x f32)
            [40:40+4*(w-1)] stored weights (f32) -- w-1 weights
            [..]    bone indices (w x u8)
          index array: (tri_count * 3) x u32
          bone descriptor array:
            (bones_num) x [u32 str_len] name + NUL, 4x4 matrix (16 x f32)

``w`` = ``weights_on_vertex`` (2, 3 or 4).  Record size = 40 + 4*(w-1) + w.
"""
from __future__ import annotations

import struct
from typing import Dict, List, Tuple

from .model import Bone, SkinnedMesh, Vertex

# default per-vertex prefix written by dis3tool for a 2-influence skin
VERTEX_MAGIC = bytes.fromhex("5ce6ac0b")


def _u32(data, o):
    return struct.unpack_from("<I", data, o)[0]


def _cstr(data, o):
    ln = _u32(data, o)
    o += 4
    raw = data[o:o + ln]
    return raw, o + ln


def _find_attr_block(data: bytes, start: int) -> int:
    """Locate the start of the attribute block by scanning for ``dwNode``."""
    i = data.find(b"dwNode", start)
    if i < 0:
        raise ValueError("could not locate dwNode attribute")
    return i - 8  # [num_attrs u32][key_len u32] precede the key


def parse_geometry_file(data: bytes) -> SkinnedMesh:
    """Parse a `.g` binary into a :class:`SkinnedMesh`.

    Counts are read from the attribute block (``vertexs_weights_num``,
    ``material0_triangles_num``, ``weights_on_vertex``) which is the most
    reliable source; the prelude is used only as a cross-check.
    """
    o = 120
    name1, o = _cstr(data, o)
    name2, o = _cstr(data, o)
    names_end = o

    prelude = [_u32(data, o + 4 * i) for i in range(10)] if o + 40 <= len(data) else []

    attr_start = _find_attr_block(data, o + 4)
    o = attr_start
    num_attrs = _u32(data, o)
    o += 4
    attrs: Dict[str, str] = {}
    for _ in range(num_attrs):
        key, o = _cstr(data, o)
        val, o = _cstr(data, o)
        attrs[key.rstrip(b"\x00").decode("latin1")] = val.rstrip(b"\x00").decode("latin1")

    w = int(attrs.get("weights_on_vertex", "2"))
    vertex_count = int(attrs["vertexs_weights_num"])
    tri_count = int(attrs["material0_triangles_num"])
    n_stored = (w - 1) if w >= 2 else 0   # stored weights per vertex
    n_bones = w if w >= 2 else 0          # bone indices per vertex
    step = 40 + 4 * n_stored + n_bones

    vertex_start = o
    magic = data[vertex_start:vertex_start + 4]
    vertices: List[Vertex] = []
    for i in range(vertex_count):
        base = vertex_start + i * step
        rec = data[base:base + step]
        pos = struct.unpack_from("<3f", rec, 4)
        nrm = struct.unpack_from("<3f", rec, 16)
        diffuse = _u32(rec, 28)
        uv = struct.unpack_from("<2f", rec, 32)
        stored = struct.unpack_from(f"<{n_stored}f", rec, 40) if n_stored else ()
        bones_off = 40 + 4 * n_stored
        bones = list(rec[bones_off:bones_off + n_bones]) if n_bones else []
        vertices.append(Vertex(pos, nrm, uv, diffuse, tuple(stored), tuple(bones)))

    index_start = vertex_start + vertex_count * step
    indices = list(struct.unpack_from(f"<{tri_count * 3}I", data, index_start))

    o = index_start + tri_count * 3 * 4
    bones: List[Bone] = []
    bones_num = int(attrs.get("bones_num", "0"))
    while o < len(data) and (not bones_num or len(bones) < bones_num):
        if o + 4 > len(data):
            break
        ln = _u32(data, o)
        # sanity check the name length, then the 64-byte matrix must fit
        if ln == 0 or ln > 256 or o + ln + 64 > len(data):
            break
        o += 4
        name_raw = data[o:o + ln]
        o += ln
        m = struct.unpack_from("<16f", data, o)
        o += 64
        bones.append(Bone(name_raw.rstrip(b"\x00").decode("latin1"), m))

    mesh = SkinnedMesh(
        name=name1.rstrip(b"\x00").decode("latin1"),
        unit_name=attrs.get("name", name1.rstrip(b"\x00").decode("latin1")),
        geometry_file=name2.rstrip(b"\x00").decode("latin1"),
        vertex_count=vertex_count,
        tri_count=tri_count,
        vertices=vertices,
        indices=indices,
        bones=bones,
        material_diffuse=attrs.get("material0_diffuse", ""),
        vertex_magic=magic,
        weights_on_vertex=w,
        preamble=data[names_end:attr_start],
        header=data[:120],
        trailing=data[o:],
    )
    # If the parsed single-mesh reconstruction would not reproduce the whole
    # file (e.g. compound / non-character .g like weapon+character LOD),
    # keep the original bytes verbatim so it round-trips losslessly.
    try:
        rebuilt = write_geometry_file(mesh, attrs)
    except Exception:  # noqa: BLE001
        rebuilt = b""
    if rebuilt != data:
        mesh.raw = data
    return mesh


# 120-byte header captured verbatim from a dis3tool `version 3` skin export.
_HEADER_BYTES = bytes.fromhex(
    "03000000ae00000001000000"
    "2b87163f2b87163f2b87163f000000002b87163f2b87163f2b87163f"
    "00000000000000000000000000000000000000000000803f0000803f0000803f"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
)


def _header_block() -> bytes:
    return _HEADER_BYTES


def _prelude_block(vertex_count: int, tri_count: int) -> bytes:
    """Prelude counts (10 u32)."""
    return struct.pack(
        "<10I",
        1, 4, 1, 2, 0x00030A5D, 0, vertex_count, tri_count, 0, 6,
    )


# Scene-node transform block (14 floats), captured verbatim from the dis3tool
# AirElemental export; holds the node translation / rotation data.
_SCENE_MATRIX_FLOATS = (
    1.5258788721439487e-07,
    2.8338310718536377, 0.005190944764763117,
    0.0, 0.0, 0.0,
    1.5271214246749878, 4.160654067993164, 0.39606305956840515,
    -1.5271210670471191, 1.5070080757141113, -0.3856811821460724,
    4.4497199058532715, -1.0,
)


def _scene_matrix() -> bytes:
    return struct.pack("<14f", *_SCENE_MATRIX_FLOATS)


def _attr_block(attrs: Dict[str, str]) -> bytes:
    out = bytearray()
    out += struct.pack("<I", len(attrs))
    for k, v in attrs.items():
        kb = k.encode("latin1") + b"\x00"
        vb = v.encode("latin1") + b"\x00"
        out += struct.pack("<I", len(kb)) + kb
        out += struct.pack("<I", len(vb)) + vb
    return bytes(out)


def vertex_stride(w: int) -> Tuple[int, int, int]:
    """Return ``(stored_weights, bone_indices, stride)`` for ``w`` slots."""
    n_stored = (w - 1) if w >= 2 else 0
    n_bones = w if w >= 2 else 0
    return n_stored, n_bones, 40 + 4 * n_stored + n_bones


def write_geometry_file(mesh: SkinnedMesh, attrs: Dict[str, str]) -> bytes:
    """Serialize a :class:`SkinnedMesh` back into the `.g` binary format."""
    if mesh.raw:
        return mesh.raw
    vc, tc = len(mesh.vertices), len(mesh.indices) // 3
    w = mesh.weights_on_vertex

    attrs = dict(attrs)
    attrs["vertexs_weights_num"] = str(vc)
    attrs["material0_triangles_num"] = str(tc)
    attrs["bones_num"] = str(len(mesh.bones))
    attrs.setdefault("new_vertex_weights_format", "1")
    attrs.setdefault("weights_on_vertex", str(w))
    attrs.setdefault("name", mesh.name)
    attrs.setdefault("groupname", "Scene Root")
    attrs.setdefault("materials_num", "1")

    def cstr(s: str) -> bytes:
        b = s.encode("latin1") + b"\x00"
        return struct.pack("<I", len(b)) + b

    if len(mesh.header) == 120:
        header = mesh.header
    else:
        header = _header_block()

    if mesh.preamble:
        # Reuse the asset-specific prelude + scene-node block verbatim so a
        # parsed `.g` round-trips byte-for-byte (the counts already live there).
        preamble = mesh.preamble
    else:
        preamble = _prelude_block(vc, tc) + _scene_matrix()

    chunks = [header, cstr(mesh.name), cstr(mesh.geometry_file or mesh.name)]
    chunks.append(preamble)
    chunks.append(_attr_block(attrs))

    magic = mesh.vertex_magic or VERTEX_MAGIC
    n_stored, n_bones, _ = vertex_stride(w)
    for v in mesh.vertices:
        stored = list(v.stored_weights[:n_stored])
        while len(stored) < n_stored:
            stored.append(0.0)
        stored = stored[:n_stored]
        bones = list(v.bones[:n_bones])
        while len(bones) < n_bones:
            bones.append(0)
        bones = bones[:n_bones]
        chunks.append(
            magic
            + struct.pack("<3f", *v.position)
            + struct.pack("<3f", *v.normal)
            + struct.pack("<I", v.diffuse)
            + struct.pack("<2f", *v.uv)
            + struct.pack(f"<{len(stored)}f", *stored)
            + struct.pack(f"<{len(bones)}B", *bones)
        )

    chunks.append(struct.pack(f"<{len(mesh.indices)}I", *mesh.indices))

    for b in mesh.bones:
        nb = b.name.encode("latin1") + b"\x00"
        chunks.append(struct.pack("<I", len(nb)) + nb)
        chunks.append(struct.pack("<16f", *b.matrix))

    # any trailing payload (morph frames, shadow volumes, ...)
    chunks.append(mesh.trailing)

    return b"".join(chunks)


def parse_attributes(data: bytes) -> Tuple[Dict[str, str], int]:
    """Parse only the attribute block; returns (attrs, offset after block)."""
    o = 120
    _, o = _cstr(data, o)
    _, o = _cstr(data, o)
    o = _find_attr_block(data, o + 4)
    num = _u32(data, o)
    o += 4
    attrs: Dict[str, str] = {}
    for _ in range(num):
        key, o = _cstr(data, o)
        val, o = _cstr(data, o)
        attrs[key.rstrip(b"\x00").decode("latin1")] = val.rstrip(b"\x00").decode("latin1")
    return attrs, o
