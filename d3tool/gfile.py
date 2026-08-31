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

from .model import Bone, MeshPart, SkinnedMesh, Vertex

# default per-vertex prefix written by dis3tool for a 2-influence skin
VERTEX_MAGIC = bytes.fromhex("5ce6ac0b")


def _u32(data, o):
    return struct.unpack_from("<I", data, o)[0]


def _cstr(data, o):
    ln = _u32(data, o)
    o += 4
    raw = data[o:o + ln]
    return raw, o + ln


def _scan_attr_block(data: bytes, start: int, limit: int = 64) -> int:
    """Locate the attribute block start by scanning for ``dwNode``.

    Returns -1 when no plausible block is found within ``limit`` bytes.
    """
    i = data.find(b"dwNode", start, start + limit + 6)
    if i < 0:
        return -1
    return i - 8  # [num_attrs u32][key_len u32] precede the key


def _find_attr_block(data: bytes, start: int) -> int:
    i = _scan_attr_block(data, start, 1 << 20)
    if i < 0:
        raise ValueError("could not locate dwNode attribute")
    return i


def _read_attrs(data: bytes, o: int) -> Tuple[Dict[str, str], int]:
    num_attrs = _u32(data, o)
    o += 4
    attrs: Dict[str, str] = {}
    for _ in range(num_attrs):
        key, o = _cstr(data, o)
        val, o = _cstr(data, o)
        attrs[key.rstrip(b"\x00").decode("latin1")] = val.rstrip(b"\x00").decode("latin1")
    return attrs, o


def _material_tri_total(attrs: Dict[str, str]) -> int:
    """Sum of all ``materialK_triangles_num`` entries (index block size)."""
    return sum(
        int(v) for k, v in attrs.items()
        if k.startswith("material") and k.endswith("_triangles_num")
    )


def _parse_vertices(data: bytes, o: int, vc: int, w: int,
                    step: int) -> Tuple[List[Vertex], bytes]:
    """Read ``vc`` vertex records of ``step`` bytes starting at ``o``."""
    vertices: List[Vertex] = []
    n_stored = (w - 1) if w >= 2 else 0
    n_bones = w if w >= 2 else 0
    for i in range(vc):
        base = o + i * step
        rec = data[base:base + step]
        pos = struct.unpack_from("<3f", rec, 4)
        nrm = struct.unpack_from("<3f", rec, 16)
        diffuse = _u32(rec, 28)
        uv = struct.unpack_from("<2f", rec, 32)
        stored = struct.unpack_from(f"<{n_stored}f", rec, 40) if n_stored else ()
        bones_off = 40 + 4 * n_stored
        bones = list(rec[bones_off:bones_off + n_bones]) if n_bones else []
        vertices.append(Vertex(pos, nrm, uv, diffuse, tuple(stored), tuple(bones)))
    return vertices, data[o:o + 4]


def _parse_bone_descriptors(data: bytes, o: int, bones_num: int):
    """Read up to ``bones_num`` bone descriptors after the index block.

    Each descriptor is ``[u32 str_len] name+NUL`` + 4x4 matrix (16 x f32).
    Stops early on implausible lengths (the layout variants put the morph
    track or the next mesh block right where bones would be).
    """
    bones: List[Bone] = []
    while o < len(data) and (not bones_num or len(bones) < bones_num):
        if o + 4 > len(data):
            break
        ln = _u32(data, o)
        if ln == 0 or ln > 256 or o + ln + 64 > len(data):
            break
        name_start = o + 4
        raw = data[name_start:name_start + ln]
        m_off = name_start + ln
        m = struct.unpack_from("<16f", data, m_off)
        o = m_off + 64
        bones.append(Bone(raw.rstrip(b"\x00").decode("latin1"), m))
    return bones, o


def _scan_block_count(data: bytes, o: int, tc: int) -> Tuple[int, bytes]:
    """Count vertex records of a morph block (fixed 40-byte stride).

    Morph sub-meshes keep no per-vertex weights: records are 40 bytes with a
    constant 4-byte prefix ("magic").  Scanning the prefix run length gives
    the vertex count; the following ``tc * 3`` indices must reference those
    vertices, which is used as a sanity cross-check.
    """
    magic = data[o:o + 4]
    if len(magic) < 4:
        return 0, magic
    vc = 0
    while o + 40 <= len(data) and data[o:o + 4] == magic:
        vc += 1
        o += 40
    # cross-check: the next block should be tc*3 plausible indices
    if tc:
        try:
            idx = struct.unpack_from(f"<{tc * 3}I", data, o)
            if not all(x < vc for x in idx):
                return 0, magic
        except struct.error:
            return 0, magic
    return vc, magic


def parse_geometry_file(data: bytes) -> SkinnedMesh:
    """Parse a `.g` binary into a :class:`SkinnedMesh`.

    Counts are read from the attribute block (``vertexs_weights_num``,
    ``material0_triangles_num``, ``weights_on_vertex``) which is the most
    reliable source; the prelude is used only as a cross-check.  Compound
    containers (weapon + body + hair + morph meshes) stack further meshes
    after the first which are exposed via :attr:`SkinnedMesh.parts`; bytes
    that do not fit the parsed structure are kept verbatim (``raw``), so the
    file round-trips byte-for-byte regardless.
    """
    try:
        return _parse_geometry_file(data)
    except Exception as exc:  # noqa: BLE001 - unknown layout: pass through
        # Keep the bytes so the file still round-trips, but record *why* the
        # structured parse gave up.  `raw` alone cannot say this: compound
        # containers use it too.
        return SkinnedMesh(raw=data, parse_error=f"{type(exc).__name__}: {exc}")


def _locate_attr_block(data: bytes):
    """Locate the attribute block, returning the container ``form``.

    Two layouts occur in the corpus:

    ``classic``
        120-byte header, then the two length-prefixed name strings, then the
        prelude + scene-node block, then the attribute block.
    ``stub``
        a header-less node helper (``Empire/Leader-Ranger`` and
        ``Empire/Leader-Thief`` ship 602-byte ones): the 10-u32 prelude and
        the 14-float scene block come *first* and there are no name strings,
        so the attribute block sits at a fixed offset 96.

    Returns ``(form, header, name1, name2, names_end, attr_start)``.
    """
    if len(data) >= 120 and _u32(data, 0) == 3:
        try:
            o = 120
            name1, o = _cstr(data, o)
            name2, o = _cstr(data, o)
            if 0 <= o <= len(data):
                return ("classic", data[:120], name1, name2, o,
                        _find_attr_block(data, o + 4))
        except (struct.error, ValueError):
            pass
    # header-less node stub: no 120-byte header, no name strings
    return ("stub", b"", b"", b"", 0, _find_attr_block(data, 0))


def _parse_geometry_file(data: bytes) -> SkinnedMesh:
    form, header, name1, name2, names_end, attr_start = _locate_attr_block(data)
    attrs, o = _read_attrs(data, attr_start)

    w = int(attrs.get("weights_on_vertex", "0" if "morph" in attrs else "2"))
    is_morph = w == 0
    vertex_count = int(attrs.get("vertexs_weights_num") or 0)
    # a mesh may carry several material groups; the index block holds all of
    # them back to back (dis3tool merges them into one primitive, material0).
    tri_count = _material_tri_total(attrs) or int(attrs.get("material0_triangles_num") or 0)
    bones_num = int(attrs.get("bones_num", "0"))
    # a mesh nominally bound to a single bone but referencing several bones
    # (lod_empire_golem: w=1, 18 bones) actually stores the full w=2 record
    # (1.0 weight + two joint bytes per vertex) — same rule as for parts.
    w_eff = 2 if w == 1 and bones_num > 1 else w
    # a morph mesh keeps full 40-byte records (no weights): count by prefix scan
    step = 40 + (4 * (w_eff - 1) + w_eff if w_eff >= 2 else 0)

    vertex_start = o
    if vertex_count == 0 and is_morph:
        vertex_count, magic = _scan_block_count(data, vertex_start, tri_count)
    elif vertex_start + 4 <= len(data):
        magic = data[vertex_start:vertex_start + 4]
    else:
        magic = b""
    if not vertex_count or vertex_count * step + tri_count * 12 > len(data) - vertex_start:
        raise ValueError("vertex counts unusable")

    vertices, magic0 = _parse_vertices(
        data, vertex_start, vertex_count, w_eff, step)
    if magic0:
        magic = magic0

    index_start = vertex_start + vertex_count * step
    indices = list(struct.unpack_from(f"<{tri_count * 3}I", data, index_start))
    o = index_start + tri_count * 3 * 4

    # lightmap UV block (TEXCOORD_1), between the index block and the bone
    # descriptors; its presence is flagged by the `lmuvdata` attribute.
    lm_uv = b""
    if "lmuvdata" in attrs:
        lm_n = vertex_count * 8
        lm_uv = data[o:o + lm_n]
        o += lm_n

    bones: List[Bone] = []
    # morph meshes are boneless: what follows is the morph track, which must
    # stay in `trailing` (a "bone" here would eat its first ~70 bytes).
    if not is_morph:
        bones, o = _parse_bone_descriptors(data, o, bones_num)

    # a header-less stub carries no name strings; the `name` attribute
    # ("BaseMesh" on the Leader stubs) is the only label it has.
    default_name = (name1.rstrip(b"\x00").decode("latin1")
                    or attrs.get("name", ""))
    mesh = SkinnedMesh(
        name=default_name,
        unit_name=attrs.get("name", default_name),
        geometry_file=name2.rstrip(b"\x00").decode("latin1"),
        form=form,
        vertex_count=vertex_count,
        tri_count=tri_count,
        vertices=vertices,
        indices=indices,
        bones=bones,
        material_diffuse=attrs.get("material0_diffuse", ""),
        lm_uv=lm_uv,
        lightmap=attrs.get("material0_lightmap", ""),
        morph=is_morph,
        vertex_magic=magic,
        weights_on_vertex=w_eff,
        preamble=data[names_end:attr_start],
        header=header,
        trailing=data[o:],
    )

    # compound containers (Empire characters etc.) stack further sub-meshes in
    # the trailing region; try to parse the stack (robust: unknown layouts
    # simply stop and stay in `trailing`).
    if mesh.trailing:
        parts, end_rest = _parse_compound_parts(mesh.trailing)
        if parts:
            mesh.parts = parts
            mesh.trailing = mesh.trailing[end_rest:]

    # If the parsed reconstruction would not reproduce the whole file, keep
    # the original bytes verbatim so it round-trips losslessly (the writer
    # short-circuits on `raw`); `parts` stay available for glTF export.
    try:
        rebuilt = write_geometry_file(mesh, attrs)
    except Exception:  # noqa: BLE001
        rebuilt = b""
    if rebuilt != data:
        mesh.raw = data
    return mesh


def _parse_compound_parts(buf: bytes) -> Tuple[List[MeshPart], int]:
    """Parse the stacked sub-meshes of a compound `.g` trailing block.

    Each sub-mesh is either introduced by a standard header ``[7 x u32]``
    (tag 2, id, 0, vertex_count, tri_count, ?, 6) followed by the 56-byte
    scene block and an attribute block, or (rarely, immediately after
    another block) starts with a short float-ish prefix before its attribute
    block.  Parsing stops at the first layout variant that does not fit.
    Returns the parsed parts plus the number of consumed bytes.
    """
    parts: List[MeshPart] = []
    o = 0
    n = len(buf)
    while o + 4 <= n:
        block_start = o
        hdr_vc = hdr_tc = 0
        # standard 28-byte header + 56-byte scene block?
        if (o + 28 <= n and _u32(buf, o) == 2 and _u32(buf, o + 24) == 6):
            # the header itself carries the vertex/tri counts (a couple of
            # blocks omit them from the attribute block, e.g. rod-1's sword)
            hdr_vc, hdr_tc = _u32(buf, o + 12), _u32(buf, o + 16)
            attr_o = _scan_attr_block(buf, o + 28, 120)
        else:
            attr_o = _scan_attr_block(buf, o, 96)
        if attr_o < 0:
            break
        try:
            attrs, vo = _read_attrs(buf, attr_o)
        except Exception:
            break

        name = attrs.get("name", "")
        if not name:
            break
        # every corpus part without a `weights_on_vertex` attribute exports
        # from dis3tool as a morph-static mesh (zeroed base positions, stride
        # 32) — e.g. rod-1's sword has neither the weights nor the morph
        # attribute, yet lands in the morph bucket — so the safe default for
        # a *part* is morph, not the w=2 skinned default of the main mesh.
        w = int(attrs.get("weights_on_vertex", "0"))
        morph = w == 0 or "morph" in attrs
        bones_num = int(attrs.get("bones_num", "0"))
        vc = int(attrs.get("vertexs_weights_num") or 0)
        tc = _material_tri_total(attrs)
        magic = b""
        if not vc and morph:
            vc, magic = _scan_block_count(buf, vo, tc)
        if not vc:
            vc = hdr_vc
        if not tc:
            tc = hdr_tc
        if not vc:
            break

        # vertex stride:
        #  - skinned: 40 + 4*(w-1) + w
        #  - rigid w=1 single-bone weapon: 40
        #  - w=1 bound to several bones: the engine deliberately stores the
        #    full two-influence record (the 1.0 weight + two joint bytes) —
        #    byte-for-byte the w=2 layout (spider abdomen, armour plates).
        w_eff = 2 if w == 1 and bones_num > 1 else w
        step = 40 if w_eff <= 1 else 40 + 4 * (w_eff - 1) + w_eff
        if vc * step + tc * 12 > n - vo and w_eff > 1 \
                and vc * 40 + tc * 12 <= n - vo and bones_num == 0 \
                and "weights_on_vertex" not in attrs:
            # attr-less rigid sub-block (rod-1's sword): 40-byte records,
            # no weights, no bones
            w_eff = 1
            step = 40
        if vc * step + tc * 12 > n - vo:
            break
        try:
            vertices, vmagic = _parse_vertices(buf, vo, vc, w_eff, step)
        except struct.error:
            break
        oi = vo + vc * step
        if vmagic and not magic:
            magic = vmagic
        try:
            indices = list(struct.unpack_from(f"<{tc * 3}I", buf, oi)) if tc else []
        except struct.error:
            break
        o = oi + tc * 12

        lm_uv = b""
        if "lmuvdata" in attrs:
            lm_uv = buf[o:o + vc * 8]
            o += vc * 8

        bones: List[Bone] = []
        if not morph:
            bones, o = _parse_bone_descriptors(buf, o, bones_num)

        # a morph sub-mesh may carry a baked position track of its own (the
        # `morph_frames` attribute): frames * vc * 12 raw float positions
        # following the (empty) bone list — hair in the HolyAvenger .g.
        baked = int(attrs.get("morph_frames") or 0)
        if baked and morph:
            o += baked * vc * 12
        if o > n or o < block_start:
            break

        parts.append(MeshPart(
            name=name,
            vertex_count=vc,
            tri_count=tc,
            vertices=vertices,
            indices=indices,
            bones=bones,
            weights_on_vertex=w_eff,
            morph=morph,
            material_diffuse=attrs.get("material0_diffuse", ""),
            lm_uv=lm_uv,
            lightmap=attrs.get("material0_lightmap", ""),
            vertex_magic=magic,
            attrs=attrs,
            raw=buf[block_start:o],
        ))
    return parts, o


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

    if mesh.form == "stub":
        # Header-less node helper: the prelude + scene-node block come first
        # and there are no name strings at all.
        header = mesh.header
        names = b""
    else:
        header = mesh.header if len(mesh.header) == 120 else _header_block()
        names = cstr(mesh.name) + cstr(mesh.geometry_file or mesh.name)

    if mesh.preamble:
        # Reuse the asset-specific prelude + scene-node block verbatim so a
        # parsed `.g` round-trips byte-for-byte (the counts already live there).
        preamble = mesh.preamble
    else:
        preamble = _prelude_block(vc, tc) + _scene_matrix()

    chunks = [header, names]
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

    # lightmap UV block (TEXCOORD_1), between indices and the bone descriptors
    if mesh.lm_uv:
        chunks.append(mesh.lm_uv)

    for b in mesh.bones:
        nb = b.name.encode("latin1") + b"\x00"
        chunks.append(struct.pack("<I", len(nb)) + nb)
        chunks.append(struct.pack("<16f", *b.matrix))

    # compound sub-meshes keep their original bytes verbatim
    for part in mesh.parts:
        chunks.append(part.raw)

    # any trailing payload (morph frames, shadow volumes, ...)
    chunks.append(mesh.trailing)

    return b"".join(chunks)


def parse_attributes(data: bytes) -> Tuple[Dict[str, str], int]:
    """Parse only the attribute block; returns (attrs, offset after block).

    Handles both container forms (see :func:`_locate_attr_block`), so the
    header-less node stubs parse here too.
    """
    try:
        _form, _hdr, _n1, _n2, _ne, o = _locate_attr_block(data)
        return _read_attrs(data, o)
    except (IndexError, struct.error, ValueError) as exc:
        raise ValueError(f"corrupt or truncated .g attribute block: {exc}") \
            from exc
