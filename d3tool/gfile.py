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

import math
import struct
from typing import Dict, List, Optional, Tuple

from .model import Bone, MeshPart, SkinnedMesh, Vertex

# default per-vertex prefix written by dis3tool for a 2-influence skin
VERTEX_MAGIC = bytes.fromhex("5ce6ac0b")


def latin1(text: str) -> bytes:
    """Encode a name for the binary formats without ever raising.

    Bone/part/material names are stored as ASCII/Cp1251-ish C strings.  A
    Blender user may rename an object or bone with any Unicode text (a
    Cyrillic object name is the common case for this game's modding
    community); an unencodable name must degrade to '?' instead of aborting
    the whole export.
    """
    try:
        return text.encode("latin1")
    except UnicodeEncodeError:
        return text.encode("latin1", "replace")


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


def _read_attrs(data: bytes, o: int) -> Tuple[Dict[str, str], int, List[Tuple[str, str]]]:
    """Parse an attribute block into a dict and the raw ordered pairs.

    The corpus contains blocks with duplicated keys (HolyAvenger hair has
    ``fps`` twice); the dict keeps the last value, the pair list keeps
    everything in the original order for a byte-exact rewrite.
    """
    num_attrs = _u32(data, o)
    o += 4
    attrs: Dict[str, str] = {}
    items: List[Tuple[str, str]] = []
    for _ in range(num_attrs):
        key, o = _cstr(data, o)
        val, o = _cstr(data, o)
        k = key.rstrip(b"\x00").decode("latin1")
        v = val.rstrip(b"\x00").decode("latin1")
        attrs[k] = v
        items.append((k, v))
    return attrs, o, items


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
    attrs, o, attr_items = _read_attrs(data, attr_start)

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
    try:
        declared = mesh_count(data)[1]
    except ValueError:
        declared = None
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
        attrs=attrs,
        attr_items=attr_items,
        declared_mesh_count=declared,
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
            attrs, vo, pitems = _read_attrs(buf, attr_o)
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

        # a part exported by the 3ds Max plugin with tangents enabled keeps
        # a tangent stream right after the bone table: 8 opaque bytes then
        # vertex_count * 24 bytes of float data (POSITION_STRIDE * 24).
        tail = b""
        if "tangent" in attrs:
            tsize = 8 + vc * 24
            if o + tsize <= n:
                tail = buf[o:o + tsize]
                o += tsize
        # a morph sub-mesh may carry a baked position track of its own (the
        # `morph_frames` attribute): frames * vc * 12 raw float positions
        # following the (empty) bone list — hair in the HolyAvenger .g.
        baked = int(attrs.get("morph_frames") or 0)
        baked_off = o
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
            attr_items=pitems,
            raw=buf[block_start:o],
            part_prefix=buf[block_start:attr_o],
            donor_box=position_bounds(vertices),
            part_tail=tail + (buf[baked_off:o] if baked else b""),
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


def _eff_width(declared: int, bone_count: int) -> int:
    """Record width a reader derives from a declared influence count."""
    return 2 if declared == 1 and bone_count > 1 else declared


def record_width(declared: int, bone_count: int) -> int:
    """Record width the engine derives from a part's declarations.

    Two corpus rules, both of them absolute (376 sub-meshes, 141 files):

    * a **single-bone part carries no influence data at all** — the record is
      the plain 40-byte one and the engine attaches the whole mesh to that
      bone.  Every retail weapon is this shape (``weights_on_vertex 1``,
      ``bones_num 1``, 148 parts); no corpus part declares more slots than it
      has bones, and ``(2, 1)`` never occurs.
    * a part that references **several** bones while declaring one still
      stores the two-influence record (``lod_empire_golem``: ``w=1`` with 18
      bones) — see :func:`vertex_stride`.
    """
    if bone_count <= 1:
        return 1 if bone_count == 1 else max(declared, 0)
    return 2 if declared == 1 else declared


def _fit_lm(lm: bytes, n: int) -> bytes:
    """Clamp a light-map UV block to exactly ``n`` bytes (one pair per vertex).

    Both the reader and the engine take the block's length from the vertex
    count, so a donor-sized block on a rebuilt (vertex-split) part shifts
    every following byte — the bone table included — by the difference.
    """
    if len(lm) == n:
        return lm
    return lm[:n].ljust(n, b"\x00")


def _scene_matrix() -> bytes:
    return struct.pack("<14f", *_SCENE_MATRIX_FLOATS)


def position_bounds(vertices) -> Optional[Tuple[float, ...]]:
    """min.xyz + max.xyz of a vertex list, or ``None`` when it has none."""
    if not vertices:
        return None
    xs = [v.position[0] for v in vertices]
    ys = [v.position[1] for v in vertices]
    zs = [v.position[2] for v in vertices]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def scene_node_block(part, donor: bytes = b"") -> bytes:
    """The 56-byte scene-node record that precedes a part's attributes.

    Fourteen floats, and — measured against all 376 sub-meshes of the
    corpus — every one of them is derived from the mesh itself::

        [0:3]   bounding-box centre
        [3:6]   zero
        [6:9]   bounding-box max
        [9:12]  bounding-box min
        [12]    radius: distance from the origin to the far box corner
        [13]    -1.0

    277 of the 376 reproduce exactly from the vertex array.  The other 99
    are the morph parts (capes, skirts, sleeves) whose centre is the extent
    of the animated frames rather than of the stored base pose — for those
    the donor's centre is kept and only the box and the radius are
    recomputed, which is what the corpus does too.

    This matters for a part that did not come from the file it lives in: a
    donated prefix carries the *donor's* box, so a mesh appended from
    another unit kept a bounding volume from a different model (the axe
    handle the LODs were built around, 3 m above the character) and the
    engine had nothing sensible to cull or place it by.
    """
    box = position_bounds(getattr(part, "vertices", None) or [])
    if box is None:
        return donor or _scene_matrix()
    mn = box[:3]
    mx = box[3:]
    if getattr(part, "morph", False) and len(donor) >= 56:
        centre = struct.unpack_from("<3f", donor, 0)
    else:
        centre = tuple((a + b) / 2.0 for a, b in zip(mn, mx))
    radius = math.sqrt(sum(max(abs(a), abs(b)) ** 2 for a, b in zip(mn, mx)))
    return struct.pack("<14f", *centre, 0.0, 0.0, 0.0, *mx, *mn, radius, -1.0)


def _attr_block(attrs: Dict[str, str], items=None,
                patch_textures: bool = False) -> bytes:
    out = bytearray()
    if items is not None:
        # The donated block keeps its order (and duplicate keys) verbatim;
        # only the counts a rebuild genuinely changed may replace them.  A
        # block that still announces the donor's vertex/bone counts over a
        # longer list desynchronises the parser — and the engine — halfway
        # through the file, while rewriting values that did not change
        # byte-diffs a pure round-trip.
        orig: Dict[str, str] = {}
        for k, v in items:
            orig[k] = v  # the parser's dict keeps the LAST of a duplicate
        # `vertexs_weights_num` and `bones_num` drive the loader directly
        # and always follow the rebuilt data.  `material0_triangles_num`
        # may be only ONE slice of a multi-material block (the total lives
        # in the header counts), so sync it only in a single-material
        # block where it IS the total.
        patchable = {"vertexs_weights_num", "bones_num", "weights_on_vertex"}
        # Texture names are the one thing a modder edits by hand or by
        # re-assigning a material, and a donated block spells the donor's
        # atlas.  Without them here a retextured armour or body was written
        # back with the old name -- the new `.t` sat in the folder unused and
        # the unit looked untouched in the game.
        if patch_textures:
            for k in orig:
                low = k.lower()
                if low.endswith("_diffuse") or low.endswith("_lightmap"):
                    patchable.add(k)
        tri_keys = [k for k in orig if k.startswith("material")
                    and k.endswith("_triangles_num")]
        if len(tri_keys) <= 1:
            patchable.add("material0_triangles_num")
        else:
            # A multi-material block splits one index buffer into ranges, so
            # the per-material counts must add up to the triangles actually
            # written.  A rebuilt mesh can carry one triangle less than the
            # donor (the glTF form holds one triangle per vertex set, so a
            # reversed-winding twin cannot survive the trip); the announced
            # total then disagrees with the data and every reader desyncs
            # halfway through the file.  Absorb the difference in
            # `material0`, the first range, which keeps the ranges that
            # follow it aligned.
            try:
                real_total = int(attrs["material0_triangles_num"])
                others = sum(int(orig[k]) for k in tri_keys
                             if k != "material0_triangles_num")
                first = real_total - others
                if first >= 0 and first != int(
                        orig.get("material0_triangles_num", first)):
                    attrs = dict(attrs)
                    attrs["material0_triangles_num"] = str(first)
                    patchable.add("material0_triangles_num")
            except (KeyError, ValueError):
                pass
        out += struct.pack("<I", len(items))
        for k, v in items:
            if (attrs and k in patchable and k in attrs
                    and attrs[k] != orig.get(k)):
                # an emptied bone table keeps the block's own `bones_num`
                # (dis3tool stubs announce bones they do not carry, and
                # rewriting it to 0 byte-diffs the corpus round-trip)
                if k == "bones_num" and attrs[k] == "0":
                    pass
                else:
                    v = attrs[k]
            kb = latin1(k) + b"\x00"
            vb = latin1(v) + b"\x00"
            out += struct.pack("<I", len(kb)) + kb
            out += struct.pack("<I", len(vb)) + vb
        return bytes(out)
    out += struct.pack("<I", len(attrs))
    for k, v in attrs.items():
        kb = latin1(k) + b"\x00"
        vb = latin1(v) + b"\x00"
        out += struct.pack("<I", len(kb)) + kb
        out += struct.pack("<I", len(vb)) + vb
    return bytes(out)


def vertex_stride(w: int) -> Tuple[int, int, int]:
    """Return ``(stored_weights, bone_indices, stride)`` for ``w`` slots."""
    n_stored = (w - 1) if w >= 2 else 0
    n_bones = w if w >= 2 else 0
    return n_stored, n_bones, 40 + 4 * n_stored + n_bones


def _vertex_record(magic: bytes, v: Vertex, w: int, morph: bool) -> bytes:
    """One `.g` vertex record: magic + pos + normal + diffuse + uv, then the
    influence slots (skinned).  Morph and w=1 records carry no slots."""
    rec = bytearray(magic)
    rec += struct.pack("<3f", *v.position)
    rec += struct.pack("<3f", *v.normal)
    rec += struct.pack("<I", v.diffuse)
    rec += struct.pack("<2f", *v.uv)
    if not morph:
        n_stored, n_bones, _ = vertex_stride(w)
        stored = list(v.stored_weights[:n_stored])
        stored += [0.0] * (n_stored - len(stored))
        bones = [int(x) & 0xFF for x in v.bones[:n_bones]]
        bones += [0] * (n_bones - len(bones))
        rec += struct.pack(f"<{n_stored}f", *stored)
        rec += struct.pack(f"<{n_bones}B", *bones)
    return bytes(rec)


def _patch_preamble_mesh_count(preamble: bytes, blocks: int) -> bytes:
    """Set the mesh-count field that closes the prelude's node chain.

    The prelude carries the tail of the container header: the ``[1][4][count]``
    terminator followed by the 84-byte prefix of the first mesh block, so the
    count sits 88 bytes before the end of the prelude.  Anything that does not
    match that shape is left untouched (header-less node stubs).
    """
    if len(preamble) < 96:
        return preamble
    marker = struct.unpack_from("<II", preamble, len(preamble) - 96)
    if marker != (1, 4):
        return preamble
    out = bytearray(preamble)
    struct.pack_into("<I", out, len(out) - 88, blocks)
    return bytes(out)


def mesh_count(data: bytes) -> Tuple[int, int]:
    """``(offset, count)`` of a `.g` container's mesh-count field.

    The container opens with a chain of ``[kind 3][length][payload]`` node
    chunks and terminates it with ``[kind 1][length 4][count]``; ``count`` is
    the number of *mesh blocks* that follow (the first mesh plus every
    compound part).  The engine walks the chain and reads exactly that many
    blocks, so a file whose count is one short silently loses its last mesh —
    the defect a weapon appended by a tool leaves behind (the mesh is in the
    file, byte-perfect, and the game never reads it).
    """
    o = 0
    while o + 12 <= len(data):
        kind, length = struct.unpack_from("<II", data, o)
        if kind == 1 and length == 4:
            return o + 8, struct.unpack_from("<I", data, o + 8)[0]
        if kind != 3 or length == 0 or o + 8 + length > len(data):
            break
        o += 8 + length
    raise ValueError("no mesh-count field in this .g")


def set_mesh_count(data: bytes, count: int) -> bytes:
    """Return ``data`` with the container's mesh-count field rewritten."""
    offset, _ = mesh_count(data)
    out = bytearray(data)
    struct.pack_into("<I", out, offset, count)
    return bytes(out)


def patch_header_counts(prefix: bytes, vertex_count: int,
                        tri_count: int) -> bytes:
    """Return a part container header with its vertex/triangle counts fixed.

    The 28-byte standard header stores ``[tag 2, id, 0, vertices, triangles,
    ?, 6]``; anything behind it (the 56-byte scene block, for one) is left
    alone.  Idempotent: a header that already agrees comes back unchanged.
    """
    if len(prefix) < 28 or struct.unpack_from("<I", prefix, 0)[0] != 2:
        return prefix
    have = struct.unpack_from("<7I", prefix, 0)
    if (have[3], have[4]) == (vertex_count, tri_count):
        return prefix
    patched = bytearray(prefix)
    struct.pack_into("<I", patched, 12, vertex_count)
    struct.pack_into("<I", patched, 16, tri_count)
    return bytes(patched)


def count_mismatches(mesh) -> List[str]:
    """Headers of a parsed `.g` whose declared counts disagree with the data.

    An independent reader (the game, the toolkit) trusts those u32s, so a
    mismatch is a crash rather than a cosmetic defect — it is reported here so
    an export can be checked *before* it reaches the game.
    """
    out: List[str] = []
    for block in [mesh] + list(getattr(mesh, "parts", []) or []):
        prefix = getattr(block, "part_prefix", b"") or b""
        if len(prefix) < 28 or struct.unpack_from("<I", prefix, 0)[0] != 2:
            continue
        have = struct.unpack_from("<7I", prefix, 0)
        vc, tc = len(block.vertices), len(block.indices) // 3
        if (have[3], have[4]) != (vc, tc):
            out.append(f"{block.name}: header says {have[3]} vertices / "
                       f"{have[4]} triangles, the data holds {vc} / {tc}")
    declared = getattr(mesh, "declared_mesh_count", None)
    blocks = len([mesh] + list(getattr(mesh, "parts", []) or []))
    if declared is not None and declared != blocks:
        out.append(f"container: header says {declared} mesh block(s), the "
                   f"file carries {blocks} — the engine reads only the "
                   f"declared number and never parses the rest")
    return out


def repair_counts(data: bytes) -> Tuple[bytes, List[str]]:
    """Rewrite every stale container count in a `.g`'s bytes.

    Returns ``(bytes, fixes)``; the bytes come back unchanged when there is
    nothing to fix.  ``d3tool repair`` uses it to rescue a file an older export
    wrote, without re-exporting it.
    """
    mesh = parse_geometry_file(data)
    fixes = count_mismatches(mesh)
    if not fixes:
        return data, []
    declared = getattr(mesh, "declared_mesh_count", None)
    blocks = len([mesh] + list(getattr(mesh, "parts", []) or []))
    if declared is not None and declared != blocks:
        data = set_mesh_count(data, blocks)
    out = bytearray(data)
    for block in [mesh] + list(getattr(mesh, "parts", []) or []):
        prefix = getattr(block, "part_prefix", b"") or b""
        if len(prefix) < 28 or struct.unpack_from("<I", prefix, 0)[0] != 2:
            continue
        vc, tc = len(block.vertices), len(block.indices) // 3
        have = struct.unpack_from("<7I", prefix, 0)
        if (have[3], have[4]) == (vc, tc):
            continue
        start = _locate_prefix(data, prefix)
        if start < 0:
            continue
        struct.pack_into("<I", out, start + 12, vc)
        struct.pack_into("<I", out, start + 16, tc)
    return bytes(out), fixes


def _locate_prefix(data: bytes, prefix: bytes) -> int:
    """Byte offset of a part's container header inside the whole file.

    The 84-byte prefix (header + scene block) is unique in practice; a repeat
    is disambiguated by the counts it carries.
    """
    want = struct.unpack_from("<7I", prefix, 0)
    at = data.find(prefix)
    while at >= 0:
        if at + 28 <= len(data) and \
                struct.unpack_from("<7I", data, at) == want:
            return at
        at = data.find(prefix, at + 1)
    return -1


def _part_block(part: MeshPart) -> bytes:
    """Serialize one compound sub-mesh whose ``raw`` bytes are unknown.

    Mirrors :func:`_parse_compound_parts`: the prefix (28-byte header +
    56-byte scene block, verbatim when donated by the original `.g`, with a
    standard header's vertex/tri counts patched), the attribute block, the
    vertex records, the index block, the optional light-map UVs, the bone
    descriptors and any tail bytes (baked morph-frame positions).
    """
    vc, tc = len(part.vertices), len(part.indices) // 3
    magic = part.vertex_magic or VERTEX_MAGIC
    # one declared bone but several referenced: the corpus stores the full
    # two-influence record (see the same rule in `write_geometry_file`)
    w_rec = part.weights_on_vertex
    if w_rec == 1 and len(part.bones) > 1:
        w_rec = 2
    if len(part.bones) == 1:
        # a single bone means a rigid, influence-free record: the engine
        # attaches the mesh to that bone, and a part that declares slots it
        # cannot fill is drawn unskinned (the sword hung in mid-air)
        w_rec = 1

    prefix = part.part_prefix
    if prefix and len(prefix) >= 28 and struct.unpack_from("<I", prefix, 0)[0] == 2:
        # A standard header carries the counts at byte offsets 12/16 and the
        # engine's reader believes it: a stale pair desynchronises every block
        # behind it (vertices, indices, bone descriptors) and the unit crashes
        # the game and the toolkit on load.  The condition used to demand a
        # bare 28-byte prefix, but a parsed one is 28 + the 56-byte scene
        # block (84), so an *edited* mesh kept the donor's counts while the
        # data below it moved -- weights-only edits never showed it, a
        # vertex/triangle edit always did.
        prefix = patch_header_counts(prefix, vc, tc)
    elif not prefix:
        # a part rebuilt without a donor: synthesize the standard container
        # block — the 28-byte header [tag 2, id, 0, vc, tc, 0, 6] plus the
        # 56-byte scene-node block — so the parser can recover the part
        prefix = struct.pack("<7I", 2, 0, 0, vc, tc, 0, 6)
    if prefix and len(prefix) >= 84:
        # the scene-node record is the mesh's own bounding volume, not the
        # donor's: a part appended from another `.g` used to keep the box it
        # was cut out of (an axe handle three metres up) — see
        # `scene_node_block`.
        #
        # But when the vertices still have the box the donor's record was
        # built from, that record *is* this mesh's own and is emitted
        # verbatim: recomputing it produced a volume that differs from the
        # file's in the last bits (3 bytes of the Inquisitor's weapon part),
        # and retail centres are not derivable from the vertex array by any
        # rounding of (min+max)/2, so "recompute" can never be exact.
        box = position_bounds(part.vertices)
        donor_box = getattr(part, "donor_box", None)
        same_box = (donor_box is not None and box is not None
                    and tuple(donor_box) == box)
        prefix = (prefix[:28]
                  + (prefix[28:84] if same_box
                     else scene_node_block(part, prefix[28:84]))
                  + prefix[84:])
    elif prefix and len(prefix) >= 28:
        prefix = prefix + scene_node_block(part)

    attrs = dict(part.attrs)

    def set_count(key: str, value: str) -> None:
        # patch counts in donated attributes without introducing keys the
        # original block did not carry (a morph part has neither
        # vertexs_weights_num nor bones_num); a synthesized part (no
        # donated attrs) gets the defaults
        if not attrs or key in attrs:
            attrs[key] = value

    set_count("vertexs_weights_num", str(vc))
    set_count("material0_triangles_num", str(tc))
    set_count("bones_num", str(len(part.bones)))
    declared = attrs.get("weights_on_vertex")
    if declared is not None and \
            _eff_width(int(declared), len(part.bones)) != w_rec:
        set_count("weights_on_vertex",
                  str(w_rec if len(part.bones) == 1
                      else part.weights_on_vertex))

    items = list(part.attr_items)
    skinned = bool(part.bones) or "weights_on_vertex" in attrs
    if items and skinned and not part.morph \
            and "new_vertex_weights_format" not in attrs:
        # every skinned sub-mesh of the corpus declares it, and the engine
        # takes the weight stride from that declaration: a part that omits it
        # (a mesh appended by a tool that only copied the donor's keys) is
        # read five bytes short apiece and never drawn.  The block is
        # ordered the way the corpus writes it -- right before the counts.
        # A rigid, attr-less sub-block (rod-1's sword: no `bones_num`, no
        # `weights_on_vertex`, 40-byte records) declares nothing of the sort
        # and must not be given it: the key is a weight stride, and the engine
        # would look for weight data that is not in the block.
        pair = ("new_vertex_weights_format", "1")
        at = next((i for i, (k, _) in enumerate(items)
                   if k == "vertexs_weights_num"), len(items))
        items.insert(at, pair)
        attrs.setdefault(*pair)

    out = bytearray(prefix)
    if items:
        # the donated block verbatim (order and duplicate keys preserved);
        # the counts it carries are the original's and match the rebuilt
        # geometry whenever the donor could be adopted
        out += _attr_block(attrs, items=items)
    else:
        out += _attr_block(attrs)
    for v in part.vertices:
        out += _vertex_record(magic, v, w_rec, part.morph)
    out += struct.pack(f"<{len(part.indices)}I", *part.indices)
    if "lmuvdata" in attrs:
        # exactly one pair per vertex; the reader derives the length from the
        # vertex count, so a stale donor-sized block would desync the file
        out += _fit_lm(part.lm_uv, vc * 8)
    for b in part.bones:
        nb = latin1(b.name) + b"\x00"
        out += struct.pack("<I", len(nb)) + nb
        out += struct.pack("<16f", *b.matrix)
    out += part.part_tail
    return bytes(out)


# Attribute keys whose value lives inside the raw bytes a donated mesh keeps
# verbatim.  A caller that changes one of these must be rebuilt, not copied:
# the raw block still spells the donor's texture, so the rename was silently
# dropped and the unit looked untouched in the game (a retextured armour, a
# body wearing another atlas, a renamed `.t`).
_RAW_BOUND_ATTRS = ("material0_diffuse", "material0_lightmap")


def write_geometry_file(mesh: SkinnedMesh, attrs: Dict[str, str]) -> bytes:
    """Serialize a :class:`SkinnedMesh` back into the `.g` binary format."""
    if mesh.raw:
        donor_attrs = getattr(mesh, "attrs", None) or {}
        if not any(str(attrs.get(k, "")) != str(donor_attrs.get(k, ""))
                   for k in _RAW_BOUND_ATTRS):
            return mesh.raw
    vc, tc = len(mesh.vertices), len(mesh.indices) // 3
    w = mesh.weights_on_vertex

    attrs = dict(attrs)
    # A donated morph-base block carries no count/weights keys at all; a
    # donated skinned block and any fresh export do.  Patch counts in
    # place, add them only for the layouts that actually carry them.
    morph_block = "morph" in attrs

    def set_attr(key: str, value: str) -> None:
        if not attrs or key in attrs:
            attrs[key] = value

    if morph_block:
        set_attr("vertexs_weights_num", str(vc))
        set_attr("bones_num", str(len(mesh.bones)))
    else:
        attrs["vertexs_weights_num"] = str(vc)
        attrs["material0_triangles_num"] = str(tc)
        attrs["bones_num"] = str(len(mesh.bones))
        attrs.setdefault("new_vertex_weights_format", "1")
        # the DECLARED influence count must be the one a reader derives the
        # record stride from: keep a donated value that already resolves to
        # the width we write (`w == 1` with several bones stores the
        # two-influence record), otherwise announce what is really there — a
        # donor may say 3 while the rebuilt mesh only carries one
        declared = attrs.get("weights_on_vertex")
        single = len(mesh.bones) == 1
        want = 1 if single else (2 if w == 1 and len(mesh.bones) > 1 else w)
        if declared is None:
            attrs["weights_on_vertex"] = str(want)
        elif _eff_width(int(declared), len(mesh.bones)) != want:
            attrs["weights_on_vertex"] = str(want)
    set_attr("material0_triangles_num", str(tc))
    attrs.setdefault("name", mesh.name)
    attrs.setdefault("groupname", "Scene Root")
    attrs.setdefault("materials_num", "1")
    if mesh.morph:
        set_attr("morph", "1")
        set_attr("morph_track", "1")

    def cstr(s: str) -> bytes:
        b = latin1(s) + b"\x00"
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
    preamble = _patch_preamble_mesh_count(preamble, 1 + len(mesh.parts))

    chunks = [header, names]
    chunks.append(preamble)
    if mesh.attr_items:
        # the donated block verbatim (order and duplicate keys preserved)
        # the file's own block may take a renamed atlas from `attrs`: the
        # donor spells its texture here, and a body/armour retexture that only
        # reaches the dataclass leaves the old name in the file (the unit then
        # looks untouched in the game).  Parts are different -- their ordered
        # item list is authoritative, see `_part_block`.
        chunks.append(_attr_block(attrs, items=mesh.attr_items,
                                  patch_textures=True))
    else:
        chunks.append(_attr_block(attrs))

    magic = mesh.vertex_magic or VERTEX_MAGIC
    # The DECLARED influence count stays in `weights_on_vertex`, but a mesh
    # bound to a single bone while referencing several stores the full
    # two-influence record (verified on the corpus: lod_empire_golem is w=1
    # with 18 bones) — every reader derives the stride the same way.
    w_rec = 2 if w == 1 and len(mesh.bones) > 1 else w
    if len(mesh.bones) == 1:
        w_rec = 1                     # rigid: see `record_width`
    if mesh.morph:
        # a morph base mesh carries no influence slots: 40-byte records of
        # magic + pos + normal + diffuse + uv (the shapes live in the
        # morph streams)
        for v in mesh.vertices:
            chunks.append(_vertex_record(magic, v, w, True))
    else:
        n_stored, n_bones, _ = vertex_stride(w_rec)
        for v in mesh.vertices:
            chunks.append(_vertex_record(magic, v, w_rec, False))

    chunks.append(struct.pack(f"<{len(mesh.indices)}I", *mesh.indices))

    # lightmap UV block (TEXCOORD_1), between indices and the bone
    # descriptors: present iff the attribute block says so, and always
    # exactly one 8-byte pair per vertex (see `_fit_lm`)
    if "lmuvdata" in attrs or (mesh.lm_uv and not attrs):
        if not attrs:
            attrs["lmuvdata"] = str(vc)
        chunks.append(_fit_lm(mesh.lm_uv, vc * 8))

    for b in mesh.bones:
        nb = latin1(b.name) + b"\x00"
        chunks.append(struct.pack("<I", len(nb)) + nb)
        chunks.append(struct.pack("<16f", *b.matrix))

    # compound sub-meshes keep their original bytes verbatim; a part
    # rebuilt by the reverse export (empty `raw`) is serialised from data
    for part in mesh.parts:
        chunks.append(part.raw if part.raw else _part_block(part))

    # any trailing payload (morph frames, shadow volumes, ...)
    chunks.append(mesh.trailing)

    return b"".join(chunks)


def iter_parts(mesh) -> list:
    """Every mesh part of a parsed `.g`, in file order.

    The first block of a `.g` is itself a mesh part (`empire_angel_hairs` for
    the Angel) while the rest live in :attr:`SkinnedMesh.parts`, so "how many
    parts does this file have" is ``len(iter_parts(mesh))`` — the count a
    `.scene` has to bind one `gobj` per part.
    """
    first = mesh if (getattr(mesh, "vertices", None) or
                     getattr(mesh, "indices", None)) else None
    return ([first] if first is not None else []) + list(
        getattr(mesh, "parts", ()) or [])


def append_parts(base: bytes, parts) -> bytes:
    """Concatenate whole part blocks onto an existing `.g`.

    A `.g` is a sequence of self-contained part blocks (each carries its own
    84-byte prefix, attribute block, vertices, indices and bone descriptors),
    so a part lifted from another `.g` can be appended byte for byte — this is
    how a weapon mesh joins a unit's LOD model, which ships without it.  Every
    part must have been parsed from a `.g` (its ``raw`` bytes are required);
    nothing else in ``base`` is touched **except the container's mesh count**,
    which has to grow with the blocks: the engine reads exactly that many
    blocks, so an unpatched count leaves the appended part in the file and
    out of the model.
    """
    # A `.g` may end with a short epilogue that no reader parses as a block
    # (the Cleric's LOD carries 141 bytes of it).  Appending after that
    # epilogue hid the new block from the parser -- and from the engine, which
    # walks the blocks in order: the mesh landed in the file and never in the
    # model.  Put the blocks in front of the epilogue instead.
    tail = b""
    try:
        tail = getattr(parse_geometry_file(base), "trailing", b"") or b""
    except Exception:  # noqa: BLE001 - an unparsable base is appended to as-is
        tail = b""
    body = base[:len(base) - len(tail)] if tail else base
    out = bytearray(body)
    for part in parts:
        raw = getattr(part, "raw", b"") or b""
        if not raw:
            raise ValueError(
                f"part {getattr(part, 'name', '?')!r} has no raw bytes; a "
                "part can only be appended straight from a parsed .g")
        out += raw
    out += tail
    appended = len(out) - len(base)
    if appended:
        _, declared = mesh_count(base)
        out = bytearray(set_mesh_count(bytes(out), declared + len(parts)))
    return bytes(out)


def lod_part(part, parent: str | None = None, prefix: str = "lod_"):
    """A copy of a main-mesh part, shaped for the unit's LOD mesh.

    The LOD mesh is the same unit at lower detail: its blocks are the main
    mesh's in the same order, spelled ``lod_<name>`` (the Knight's LOD mesh
    carries ``lod_empire_knight_sword``), and they hang off *that file's* own
    parent node — the ids are per file, so a part lifted from the main `.g`
    would otherwise name a node the LOD file does not have.

    The copy carries freshly serialized ``raw`` bytes, which is what
    :func:`append_parts` writes.
    """
    import copy

    clone = copy.copy(part)
    name = getattr(part, "name", "") or part.attrs.get("name", "")
    if prefix and not name.startswith(prefix):
        name = prefix + name
    items = [(k, v) for k, v in (getattr(part, "attr_items", ()) or ())]
    items = [(k, name if k == "name" else v) for k, v in items]
    if parent:
        if any(k == "dwParent" for k, _ in items):
            items = [(k, parent if k == "dwParent" else v) for k, v in items]
        else:
            items.append(("dwParent", parent))
    clone.name = name
    clone.attr_items = items
    clone.attrs = dict(items)
    clone.raw = _part_block(clone)
    return clone


def normalize_part_identity(mesh, parent: str | None = None) -> List[str]:
    """Give every block of a compound mesh the file's own node identity.

    ``dwNode``/``dwParent`` are the ``.g`` side of the scene graph: a part's
    node hangs off the parent node named by ``dwParent`` and carries its own
    ``dwNode``.  All 376 sub-meshes of the retail corpus share one parent id
    per file — the file's own ``Scene Root`` node — and each holds a distinct
    node id.

    A block built by a tool that cannot know the file's ids (the reverse
    export of a mesh the donor file never had — an added weapon) is written
    with the placeholder pair of :func:`d3tool.cli._default_attrs`, a parent
    no node in the file answers to.  The part is then not part of the model's
    node tree and the engine draws it as a loose object at its stored
    coordinates instead of following the bone it is bound to.

    Returns the changes made, e.g.
    ``["empire_knight_sword: dwParent 55867360 -> 53704704"]``.
    """
    parts = list(getattr(mesh, "parts", ()) or ())
    if not parts:
        return []
    if not parent:
        for part in parts:
            parent = parent or (part.attrs or {}).get("dwParent")
    parent = parent or getattr(mesh, "attrs", {}).get("dwParent")
    if not parent:
        return []
    used = set()
    for part in [mesh] + parts:
        node = (getattr(part, "attrs", {}) or {}).get("dwNode")
        if node and node.isdigit():
            used.add(int(node))
    changes: List[str] = []
    for part in parts:
        attrs = dict(getattr(part, "attrs", {}) or {})
        if not attrs:
            continue
        items = list(getattr(part, "attr_items", ()) or ())
        touched = False
        if attrs.get("dwParent") != parent:
            changes.append(f"{attrs.get('name', '?')}: dwParent "
                           f"{attrs.get('dwParent')} -> {parent}")
            attrs["dwParent"] = parent
            items = _items_set(items, "dwParent", parent) if items else items
            touched = True
        node = attrs.get("dwNode")
        if not node or not node.isdigit():
            fresh = (max(used) if used else 375048704) + 8
            used.add(fresh)
            node = str(fresh)
            changes.append(f"{attrs.get('name', '?')}: dwNode -> {node}")
            attrs["dwNode"] = node
            items = _items_set(items, "dwNode", node) if items else items
            touched = True
        if touched:
            part.attrs = attrs
            if items:
                part.attr_items = items
            part.raw = b""            # the writer keeps parsed bytes verbatim
    return changes


def _items_set(items, key: str, value: str):
    """Set ``key`` in a ``(key, value)`` attribute list, appending if absent."""
    out = list(items)
    for i, (k, _) in enumerate(out):
        if k == key:
            out[i] = (key, value)
            return out
    out.append((key, value))
    return out


def attr_block_bounds(data: bytes, part) -> Tuple[int, int, int]:
    """``(start, end, count)`` of one sub-mesh's attribute block in ``data``.

    The block is a ``u32`` count followed by that many
    ``(u32 length, "key\\0")`` / ``(u32 length, "value\\0")`` pairs — the
    lengths *include* the NUL, so a pair can be inserted without touching
    anything else.  ``start`` points at the count, ``end`` just past the last
    value (where the vertex block begins).

    The part must carry its own bytes (:func:`parse_geometry_file` keeps them
    in ``part.raw``) and must occur exactly once in ``data``, otherwise there
    is no safe place to write.
    """
    raw = getattr(part, "raw", b"") or b""
    if not raw:
        raise ValueError(
            f"part {getattr(part, 'name', '?')!r} has no raw bytes; parse it "
            "from a .g first")
    at = data.find(raw)
    if at < 0:
        raise ValueError("the part's bytes are not in this file")
    if data.find(raw, at + 1) >= 0:
        raise ValueError("the part's bytes occur more than once in this file")
    start = at + len(getattr(part, "part_prefix", b"") or b"")
    count = struct.unpack_from("<I", data, start)[0]
    o = start + 4
    for _ in range(count):
        key_len = struct.unpack_from("<I", data, o)[0]
        o += 4 + key_len
        value_len = struct.unpack_from("<I", data, o)[0]
        o += 4 + value_len
    if o > at + len(raw):
        raise ValueError("the attribute block runs past the part's own bytes")
    return start, o, count


def _attr_pair(key: str, value: str) -> bytes:
    k = latin1(key) + b"\0"
    v = latin1(value) + b"\0"
    return (struct.pack("<I", len(k)) + k
            + struct.pack("<I", len(v)) + v)


def insert_part_attr(data: bytes, part, key: str, value: str) -> bytes:
    """Add one attribute to a sub-mesh, bumping the block's count.

    This is how a part that lost an attribute gets it back: the engine reads
    the vertex-weight block by the *format* the block declares, and a skinned
    part that does not say ``new_vertex_weights_format 1`` is read with the
    old stride — every vertex lands a few bytes off, so the part parses as
    garbage and the engine draws nothing.  Retail parts all carry it; a part
    appended by a tool that dropped the attribute is invisible in game even
    though every reader that takes the stride from ``weights_on_vertex``
    (this package included) sees it fine.

    The block is length-prefixed, so this shifts every following byte of the
    file: the part's own data stays intact, the file just grows.
    """
    if key in dict(getattr(part, "attr_items", ()) or ()):
        raise ValueError(f"part already carries {key!r}; replace it instead")
    start, end, count = attr_block_bounds(data, part)
    return (data[:start] + struct.pack("<I", count + 1) + data[start + 4:end]
            + _attr_pair(key, value) + data[end:])


def replace_part_attr(data: bytes, part, key: str, value: str) -> bytes:
    """Rewrite the value of an attribute a part already carries.

    Values are length-prefixed, so a replacement of a *different* length is
    accepted and shifts the rest of the file.  Same-length replacements are
    the way to patch a value without changing the archive payload's size.
    """
    start, end, count = attr_block_bounds(data, part)
    o = start + 4
    for _ in range(count):
        key_len = struct.unpack_from("<I", data, o)[0]
        found = data[o + 4:o + 4 + key_len - 1].decode("latin-1")
        o += 4 + key_len
        value_len = struct.unpack_from("<I", data, o)[0]
        if found == key:
            # the length prefix counts the NUL too: a value of a different
            # length has to rewrite it, or every reader after this block reads
            # the old width and the rest of the container is lost
            blob = latin1(value) + b"\0"
            return (data[:o] + struct.pack("<I", len(blob)) + blob
                    + data[o + 4 + value_len:])
        o += 4 + value_len
    raise KeyError(f"part {getattr(part, 'name', '?')!r} has no {key!r}")


def parse_attributes(data: bytes) -> Tuple[Dict[str, str], int]:
    """Parse only the attribute block; returns (attrs, offset after block).

    Handles both container forms (see :func:`_locate_attr_block`), so the
    header-less node stubs parse here too.
    """
    try:
        _form, _hdr, _n1, _n2, _ne, o = _locate_attr_block(data)
        attrs, o, _items = _read_attrs(data, o)
        return attrs, o
    except (IndexError, struct.error, ValueError) as exc:
        raise ValueError(f"corrupt or truncated .g attribute block: {exc}") \
            from exc
