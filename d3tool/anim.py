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


def _u32(data, o):
    return struct.unpack_from("<I", data, o)[0]


def _cstr(data, o):
    e = data.index(b"\x00", o)
    return data[o:e].decode("latin1"), e + 1


def _cstr_bytes(data, o):
    e = data.index(b"\x00", o)
    return data[o:e + 1], e + 1


def _header_fields(data: bytes) -> Optional[List]:
    if len(data) < 20:
        return None
    return [_u32(data, i * 4) for i in range(5)]  # magic, len-8, bones, frames, unk


def parse_anim(data: bytes) -> AnimFile:
    """Parse an `.a` file into an :class:`AnimFile` (byte-faithful)."""
    hf = _header_fields(data)
    anim = AnimFile()
    if hf is None:
        return anim
    anim.bone_count = hf[2]
    anim.frame_count = hf[3]
    anim.header = data[:20]

    o = 20
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

    anim.trailing = data[o:]
    return anim


def write_anim(anim: AnimFile) -> bytes:
    """Re-emit an :class:`AnimFile` byte-for-byte (round-trip safe)."""
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
    """Build a canonical record preamble (``[a][b][frame_count][0.02]``)."""
    return struct.pack("<IIIf", a, b, nframes, 0.02)


def build_anim(
    bones: List[Tuple[str, str, List[Tuple[float, ...]]]],
    frame_count: int,
) -> AnimFile:
    """Build an :class:`AnimFile` from ``(name, parent, frames)`` tuples.

    The global header mirrors the dis3tool layout; each record uses the
    canonical preamble with ``a = b = 5`` (engine accepts any small value;
    the exporter writes varying values that are not required for playback).
    """
    # 20-byte header: [magic][len-8 (padded later)][bones][frames][unk=15]
    hdr = bytearray(struct.pack("<5I", 9, 0, len(bones), frame_count, 15))

    anim = AnimFile(bone_count=len(bones), frame_count=frame_count)
    for name, parent, frames in bones:
        nf = len(frames) or frame_count
        anim.bones.append(BoneAnim(name, parent, nf, frames, _preamble_for(nf)))
    anim.header = bytes(hdr)
    # recompute header length field (offset 4 = total_len - 8)
    total = len(write_anim(anim))
    anim.header = struct.pack("<5I", 9, total - 8, len(bones), frame_count, 15)
    return anim
