"""Reader / writer for the Disciples 3 animation file (`.a`).

The `.a` is a compact binary holding the animated skeleton.  Its layout
(confirmed against the dis3tool `*_iadd.a` / `*_run.a` exports):

```text
 global header (16 fields):
   [u32 magic]            (e.g. 9)
   [u32 file_len-8]       (offset 4, equals file size - 8)
   [u32 bone_count]       (number of bone records)
   [u32 frame_count]      (e.g. 346)
   [u32 ..] [u32 ..]
   [u32 ..] [u32 ..]
   [float 0.02]           (small per-frame parameter)

 for each bone record (bone_count records):
   [u32 a] [u32 b] [u32 frame_count] [float 0.02]     <- record preamble
   [cstr] bone name
   [cstr] parent bone name
   frame_count * (7 x float32)                         <- TRS per frame
```

The per-frame values are ``[rotation quaternion(x,y,z,w)] + [translation(x,y,z)]``,
exactly the data dis3tool lifts into the glTF animation `bones_rotate`
(`VEC4`) and `bones_translate` (`VEC3`) buffers.  So the glTF animation
round-trips back into the `.a` byte stream.

This module parses `.a` into bone records and can re-emit them byte-for-byte.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .model import MorphTrack

_SAMPLE = 7  # floats per TRS sample: quat(4) + trans(3)
_SAMPLE_BYTES = _SAMPLE * 4  # 28
_PREAMBLE = 16  # bytes: [a][b][frame_count][0.02]


@dataclass
class BoneAnim:
    name: str
    parent: str
    frame_count: int
    # per-frame [quat(x,y,z,w), trans(x,y,z)] samples
    frames: List[Tuple[float, ...]] = field(default_factory=list)
    # verbatim record preamble bytes ([a][b][frame_count][0.02])
    preamble: bytes = b""

    @property
    def rest(self) -> Tuple[float, ...]:
        """Bind/rest pose = first sample (all bones start at the bind pose)."""
        return self.frames[0] if self.frames else (0.0,) * _SAMPLE


@dataclass
class AnimFile:
    bone_count: int = 0
    frame_count: int = 0
    header: bytes = b""          # verbatim global header (first 36 bytes)
    bones: List[BoneAnim] = field(default_factory=list)
    trailing: bytes = b""
    # set when the stream could not be parsed completely: write_anim emits
    # these bytes verbatim so a corrupt `.a` still round-trips losslessly
    raw: bytes = b""
    # vertex-morph streams parsed out of the trailing block (byte ranges stay
    # inside `trailing`, so round-tripping remains verbatim).
    morphs: List[MorphTrack] = field(default_factory=list)
    # how many leading entries of `bones` came from the *first* stream when
    # this file was built by concat_anims; 0 means "all of them".  Bones only
    # present in later streams are appended after it, and the glTF writer must
    # keep them appended rather than weaving them into the skeleton tree.
    n_primary: int = 0


def _scan_morph_tracks(trailing: bytes) -> List[MorphTrack]:
    """Recover vertex-morph streams from an `.a` file's trailing block.

    Morph-record marker: ``[u32 14][u32 len-8][u32 15][frame_count]
    [vertex_count][name_len][name + NUL]`` followed by ``frame_count *
    vertex_count * 3`` float32 positions (absolute frame positions, the first
    frame being the base pose).  Bounds are checked aggressively — some `.a`
    files ship streams belonging to *other* units (e.g. the DarkServant
    carries 7 foreign tracks), and anything that does not fit the buffer is
    quietly ignored.
    """
    out: List[MorphTrack] = []
    o = 0
    n = len(trailing)
    while o + 24 <= n:
        if struct.unpack_from("<I", trailing, o)[0] != 14:
            o += 4
            continue
        # total payload length including this header (the u32 stores total-8)
        want_len = struct.unpack_from("<I", trailing, o + 4)[0] + 8
        # third u32 is a record-type tag — observed 15 / 16 / 30 across the
        # corpus and never used by the reader, so it is not gated on.
        frames = struct.unpack_from("<I", trailing, o + 12)[0]
        vc = struct.unpack_from("<I", trailing, o + 16)[0]
        nlen = struct.unpack_from("<I", trailing, o + 20)[0]
        if (want_len < 24 or o + want_len > n or not (0 < nlen < 128)
                or o + 24 + nlen > n):
            o += 4
            continue
        name_raw = trailing[o + 24:o + 24 + nlen]
        if name_raw[-1:] != b"\x00" or not all(32 <= c < 127 for c in name_raw[:-1]):
            o += 4
            continue
        name = name_raw[:-1].decode("latin1", "replace")
        data_len = frames * vc * 12
        pos_o = o + 24 + nlen
        # exact-fit check: the header + name + frames*vc positions must come
        # out to exactly the declared stream length (kills false positives
        # inside animation data)
        if vc == 0 or frames == 0 or pos_o + data_len > n or \
                data_len != want_len - 24 - nlen:
            o += 4
            continue
        out.append(MorphTrack(
            name=name, frame_count=frames, vertex_count=vc,
            positions=trailing[pos_o:pos_o + data_len],
            tag=struct.unpack_from("<I", trailing, o + 8)[0],
            raw_record=trailing[o:pos_o + data_len]))
        o = pos_o + data_len
    return out


def _u32(data, o):
    return struct.unpack_from("<I", data, o)[0]


class _Truncated(ValueError):
    """Raised when a record stream ends before its NUL-terminated names."""


def _cstr(data, o):
    e = data.find(b"\x00", o)
    if e < 0:
        raise _Truncated("bone name is not NUL-terminated (file truncated?)")
    return data[o:e].decode("latin1"), e + 1


def _cstr_bytes(data, o):
    e = data.find(b"\x00", o)
    if e < 0:
        raise _Truncated("bone name is not NUL-terminated (file truncated?)")
    return data[o:e + 1], e + 1


def _header_fields(data: bytes) -> Optional[List]:
    if len(data) < 20:
        return None
    return [_u32(data, i * 4) for i in range(5)]  # magic, len-8, bones, frames, unk


def parse_anim(data: bytes) -> AnimFile:
    """Parse an `.a` file into an :class:`AnimFile` (byte-faithful).

    Like :func:`d3tool.gfile.parse_geometry_file`, a truncated or corrupt
    stream degrades instead of raising: whatever records did parse stay
    readable, and the original bytes are kept in :attr:`AnimFile.raw` so
    :func:`write_anim` still reproduces the file exactly.
    """
    hf = _header_fields(data)
    anim = AnimFile()
    if hf is None:
        anim.raw = data
        return anim
    anim.bone_count = hf[2]
    anim.frame_count = hf[3]
    anim.header = data[:20]

    o = 20
    try:
        for _ in range(anim.bone_count):
            if o + _PREAMBLE > len(data):
                break
            nf = _u32(data, o + 8)
            pream = data[o:o + _PREAMBLE]
            o += _PREAMBLE
            name, o = _cstr(data, o)
            parent, o = _cstr(data, o)
            frames = []
            for _ in range(nf):
                if o + _SAMPLE_BYTES > len(data):
                    break
                frames.append(struct.unpack_from("<7f", data, o))
                o += _SAMPLE_BYTES
            anim.bones.append(BoneAnim(name, parent, nf, frames, pream))
    except (_Truncated, struct.error):
        # A truncated record stream: keep the records that did parse and the
        # verbatim bytes, so nothing is silently lost on re-serialisation.
        anim.trailing = b""
        anim.raw = data
        return anim

    anim.trailing = data[o:]
    anim.morphs = _scan_morph_tracks(anim.trailing)
    return anim


def concat_anims(anims: List[AnimFile]) -> AnimFile:
    """Concatenate several `.a` streams the way dis3tool does.

    A unit whose `.ac` names more than one `.a` (Angel names five:
    idle/attack/run/damage/death) is exported by dis3tool as **one** animation
    spanning every stream — verified on all 24 such bundled units, where the
    reference glTF frame count equals the sum exactly.  Morph tracks are taken
    from the **last** stream (verified on all 8 morph-bearing units: Cleric's
    25 targets come from `_run.a`, not the 356 in `_iadd.a`).

    Frame filling: the output bone list is the first-seen-name union (the
    first stream's bones, then any later-stream bones with new names), but
    the *frames* of a primary slot (index < first-stream bone count) come
    from each stream's **record at the same index**, name notwithstanding —
    the C++ exporter walks the per-stream record arrays in parallel.  Byte
    evidence: AirElemental's `run.a` names its record 6 `LeftLeftHand` while
    the `iadd.a` slot 6 is `LeftHand`, and the reference export's LeftHand
    frames 346..362 are exactly `run.a` record 6's 17 samples (same for
    `Tail02` → `RightTail02`); DarkServant's `run.a` record 68 `Bone02`
    lands on the `ROOT_demons_thief_lod` slot the same way.  Bones appended
    beyond the primary range keep their own samples (they get no channels,
    matching the reference node list).

    A single stream is returned unchanged, so units with one `.a` are untouched.
    """
    anims = [a for a in anims if a is not None]
    if not anims:
        return AnimFile()
    if len(anims) == 1:
        return anims[0]

    total = sum(a.frame_count for a in anims)
    by_name = [{b.name: b for b in stream.bones} for stream in anims]
    n_primary = len(anims[0].bones)
    order: List[str] = []
    seen: set = set()
    for stream in anims:
        for b in stream.bones:
            if b.name not in seen:
                seen.add(b.name)
                order.append(b.name)

    out_bones: List[BoneAnim] = []
    for pos, name in enumerate(order):
        # rest pose = first sample of the first stream that carries this bone
        rest = None
        parent = ""
        pream = b""
        for i, _stream in enumerate(anims):
            src: Optional[BoneAnim] = (
                _stream.bones[pos] if pos < len(_stream.bones)
                else by_name[i].get(name))
            if src is not None and src.frames:
                rest = src.frames[0]
                parent = parent or src.parent
                pream = pream or src.preamble
                break
        if rest is None:
            rest = (0.0,) * _SAMPLE
        frames: List[Tuple[float, ...]] = []
        for i, stream in enumerate(anims):
            # primary slots read the stream's record at the same index
            # (names may drift between streams); appended names fall back
            # to a name lookup
            if pos < n_primary:
                src = (stream.bones[pos]
                       if pos < len(stream.bones) else None)
            else:
                src = by_name[i].get(name)
            if src is not None and src.frames:
                frames.extend(src.frames)
                if len(src.frames) < stream.frame_count:
                    frames.extend([src.frames[-1]] *
                                  (stream.frame_count - len(src.frames)))
            else:
                frames.extend([rest] * stream.frame_count)
        out_bones.append(BoneAnim(name, parent, total, frames, pream))

    out = AnimFile(bone_count=len(out_bones), frame_count=total,
                   header=anims[0].header, bones=out_bones, trailing=b"")
    out.morphs = list(anims[-1].morphs)
    out.n_primary = len(anims[0].bones)
    return out


def write_anim(anim: AnimFile) -> bytes:
    """Re-emit an :class:`AnimFile` byte-for-byte (round-trip safe)."""
    if anim.raw:
        return anim.raw
    out = bytearray(anim.header)
    for b in anim.bones:
        out += b.preamble
        out += b.name.encode("latin1") + b"\x00"
        out += b.parent.encode("latin1") + b"\x00"
        for fr in b.frames:
            out += struct.pack("<7f", *fr[:7])
    out += anim.trailing
    return bytes(out)


def _preamble_for(nframes: int, a: int = 5, b: int = 5) -> bytes:
    """Build a record preamble (``[a][b][frame_count][0.02]``)."""
    return struct.pack("<IIIf", a, b, nframes, 0.02)


def build_anim(
    bones: List[Tuple[str, str, List[Tuple[float, ...]]]],
    frame_count: int,
) -> AnimFile:
    """Build an :class:`AnimFile` from ``(name, parent, frames)`` tuples.

    The global header mirrors the dis3tool layout; each record preamble is
    ``[len(name) + 1][len(parent) + 1][frame_count][0.02]`` — verified
    against all 6764 bone records of the shipped corpus (the pair counts
    the NUL-terminated strings, matching the C-struct writing code).
    """
    # 20-byte header: [magic][len-8 (padded later)][bones][frames][unk=15]
    hdr = bytearray(struct.pack("<5I", 9, 0, len(bones), frame_count, 15))

    anim = AnimFile(bone_count=len(bones), frame_count=frame_count)
    for name, parent, frames in bones:
        nf = len(frames) or frame_count
        anim.bones.append(BoneAnim(
            name, parent, nf, frames,
            _preamble_for(nf, len(name) + 1, len(parent) + 1)))
    anim.header = bytes(hdr)
    # recompute the header length field (offset 4): it covers everything the
    # writer emits except the trailing block, minus the 8-byte magic/len pair
    # itself (verified against all 152 corpus `.a` files)
    total = len(write_anim(anim))
    anim.header = struct.pack("<5I", 9, total - 8 - len(anim.trailing),
                              len(bones), frame_count, 15)
    return anim
