"""Disciples 3 ``.t`` texture container <-> standard ``.dds`` conversion.

The original Disciples 3 (GM engine) stores its textures in a custom ``.t``
file.  A ``.t`` file is a **59-byte GM header** followed by the exact same
compressed pixel data that a standard ``.dds`` stores after its 128-byte
header.  In other words the ``.t`` is a thin but faithful wrapper around the
same DirectDraw surface; the compressed payload is byte-for-byte identical.

This module parses both containers and converts between them:

* ``.t``  -> ``.dds``  (``t_to_dds``) — used when forward-exporting a unit to
  glTF, because dis3tool's glTF references the texture as ``.dds``.
* ``.dds`` -> ``.t``    (``dds_to_t``) — used when reverse-exporting a glTF
  back to the original GM files, so the native ``.t`` is produced.

Pixel formats supported (mirroring the ``geo2011.dle`` strings ``DXT1`` /
``DXT3`` / ``DXT5`` plus a 16-bit A1R5G5B5 form used by UI icons):

=================  ==========  ===========================
GM ``@4`` format   DDS fourCC  bytes per 4x4 block
=================  ==========  ===========================
``6``              ``DXT1``    8
``7``              ``DXT3``    16
``8``              ``DXT5``    16
``3``              (RGB)       16-bit A1R5G5B5 (2 bytes/pixel)
=================  ==========  ===========================

Conversion is lossless: ``t_to_dds`` reproduces the reference ``.dds`` files
in the ``Neutrals/*`` bundle byte-for-byte, and a ``.t`` -> ``.dds`` -> ``.t``
round-trip is byte-identical for the bundled textures.

Known limitation: the GM header carries an opaque flag at offset 24 whose
value is not present in a ``.dds`` (it is 0 for some DXT1 diffuse textures and
1 for others, even at the same size/format).  When converting a bare ``.dds``
to ``.t`` with no source ``.t`` header to preserve, this flag defaults to 1 —
the payload, dimensions and format are always correct, and the flag only
matters if byte-for-byte reproduction of a specific source ``.t`` is required.
"""
from __future__ import annotations

import struct
import os
import zlib
from dataclasses import dataclass
from typing import Dict, Optional

# GM `.t` pixel-format code -> DDS fourCC / block size for compressed codes.
_T_FMT_TO_DDS = {
    6: (b"DXT1", 8),
    7: (b"DXT3", 16),
    8: (b"DXT5", 16),
}
_DDS_FOURCC_TO_T = {v[0]: k for k, v in _T_FMT_TO_DDS.items()}

# Bytes of payload per-pixel rate for every known GM format code, checked
# against every bundled `.t` (the payload of mip i is exactly
# ``(w>>i) * (h>>i) * rate`` bytes, clamped to 1 px per side — the GM engine
# does NOT 4x4-block-align the sub-mips, unlike a naive DXT layout).
_T_FMT_RATE = {
    1: 2.0,     # 16-bit uncompressed (weapon diffuse leaders)
    2: 2.0,     # 16-bit uncompressed (UI icons)
    6: 0.5,     # DXT1
    7: 1.0,     # DXT3
    8: 1.0,     # DXT5
}

# GM format code for the 16-bit A1R5G5B5 form (UI rings/small icons).
_T_FMT_16BBP = 3
# GM format codes for the uncompressed 32-bit A8R8G8B8 form.
_T_FMT_32BBP = {4, 5}
# All 16-bit uncompressed GM codes (payload is w*h*2 per mip).
_T_FMT_16BBP_ALL = {1, 2, 3}

T_HEADER_SIZE = 59
DDS_HEADER_SIZE = 128


@dataclass
class TextureInfo:
    """A parsed texture container (either ``.t`` or ``.dds``)."""

    width: int
    height: int
    mip_count: int
    #: GM format code (6/7/8/3) for `.t`, or derived when reading a `.dds`.
    gm_format: Optional[int] = None
    #: DDS fourCC (``b'DXT1'`` etc.), or raw pixel format fields for 16-bit.
    fourcc: Optional[bytes] = None
    #: 16-bit A1R5G5B5 form (True when ``gm_format == _T_FMT_16BBP``).
    r5g5b5: bool = False
    #: Raw 59-byte GM header from the source ``.t`` (preserved for round-trip).
    t_header: Optional[bytes] = None
    #: Cubemap face count inferred at parse (``cubemap_default.t`` == 6).
    faces: int = 1
    #: The compressed pixel payload (byte-identical between `.t` and `.dds`).
    payload: bytes = b""
    #: Original filename extension (for error messages / logging).
    source: str = ""

    @property
    def block_size(self) -> int:
        if self.r5g5b5 or self.gm_format in _T_FMT_16BBP_ALL:
            return 2  # 16-bit form: 2 bytes per pixel
        if self.gm_format in _T_FMT_32BBP:
            return 4
        entry = _T_FMT_TO_DDS.get(self.gm_format)
        if entry is None:
            raise ValueError(
                f"unknown GM pixel-format code {self.gm_format!r}; expected "
                f"one of {sorted(_T_FMT_TO_DDS)} (DXT), "
                f"{sorted(_T_FMT_16BBP_ALL)} (16-bit) or "
                f"{sorted(_T_FMT_32BBP)} (32-bit)")
        return entry[1]

    @property
    def uncompressed_bpp(self) -> int:
        # bytes per pixel for the uncompressed GM formats (0 for compressed)
        if self.r5g5b5 or self.gm_format in _T_FMT_16BBP_ALL:
            return 2
        if self.gm_format in _T_FMT_32BBP:
            return 4
        return 0

    def payload_size(self) -> int:
        # Every mip is stored as pixel_count * rate bytes (no 4x4 block
        # alignment), the chain cutting off once a mip would hold less than
        # four bytes of data -- verified against all bundled textures.
        rate = _T_FMT_RATE.get(self.gm_format)
        if rate is None:
            rate = 2.0 if (self.r5g5b5 or self.gm_format in _T_FMT_16BBP_ALL) else 4.0
        total = 0.0
        for i in range(max(self.mip_count, 1)):
            wi = self.width >> i
            hi = self.height >> i
            if wi == 0 or hi == 0:
                # the GM mip chain stops once a side underflows (no clamping
                # to one pixel) — matches every bundled texture.
                break
            total += wi * hi * rate
        return int(total) * max(1, self.faces)


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


# --------------------------------------------------------------------------- #
#  `.t` parsing / writing
# --------------------------------------------------------------------------- #
def parse_t(data: bytes, source: str = "") -> TextureInfo:
    """Parse a GM ``.t`` container (59-byte header + DXT payload)."""
    if len(data) < T_HEADER_SIZE:
        raise ValueError(f".t too short ({len(data)} bytes)")
    fmt = _u32(data, 4)
    mips = _u32(data, 12)
    width = _u32(data, 16)
    height = _u32(data, 20)
    fourcc = None
    r5g5b5 = fmt == _T_FMT_16BBP
    if fmt not in _T_FMT_TO_DDS and fmt not in _T_FMT_16BBP_ALL \
            and fmt not in _T_FMT_32BBP:
        raise ValueError(f"unsupported .t pixel format code {fmt}")
    if fmt in _T_FMT_TO_DDS:
        fourcc = _T_FMT_TO_DDS[fmt][0]
    ti = TextureInfo(
        width=width,
        height=height,
        mip_count=mips,
        gm_format=fmt,
        fourcc=fourcc,
        r5g5b5=r5g5b5,
        t_header=bytes(data[:T_HEADER_SIZE]),
        payload=data[T_HEADER_SIZE:],
        source=source,
    )
    # cubemap: payload holds 6 identical faces of the base pixel block
    base = ti.payload_size()
    if fmt in _T_FMT_32BBP and base and len(ti.payload) == base * 6:
        ti.faces = 6
    return ti


def _build_t_header(
    info: TextureInfo,
    orig_header: Optional[bytes] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    mip_count: Optional[int] = None,
) -> bytes:
    """Build the 59-byte GM header for a ``.t``.

    When ``orig_header`` is given (from a source ``.t``), its opaque fields
    (notably the flag at offset 24 and the marker at offset 52) are preserved
    so that the round-trip is byte-identical.  Otherwise sensible defaults are
    used.
    """
    hdr = bytearray(T_HEADER_SIZE)
    if orig_header is not None and len(orig_header) >= T_HEADER_SIZE:
        hdr[:T_HEADER_SIZE] = orig_header[:T_HEADER_SIZE]
    else:
        struct.pack_into("<I", hdr, 0, 1)          # container version/magic
        struct.pack_into("<I", hdr, 4, info.gm_format)
        struct.pack_into("<I", hdr, 8, 0)
        # opaque flag: 1 for every bundled texture that has no source header
        struct.pack_into("<I", hdr, 24, 1)
        struct.pack_into("<I", hdr, 28, 0)
        for off in (32, 36, 40):
            struct.pack_into("<I", hdr, off, 0x01000000)
        struct.pack_into("<I", hdr, 52, 0x00417000)
    # Always refresh the fields that describe the pixel data.  The GM format
    # byte is kept when an original header is available: codes 2/3 share the
    # same 16-bit encoding on the DDS side, so only the source `.t` knows it.
    struct.pack_into(
        "<I", hdr, 12, mip_count if mip_count is not None else info.mip_count
    )
    struct.pack_into("<I", hdr, 16, width if width is not None else info.width)
    struct.pack_into(
        "<I", hdr, 20, height if height is not None else info.height
    )
    return bytes(hdr)


def write_t(info: TextureInfo, orig_header: Optional[bytes] = None) -> bytes:
    """Serialise a :class:`TextureInfo` back into a ``.t`` file."""
    hdr = _build_t_header(info, orig_header)
    return hdr + info.payload


# --------------------------------------------------------------------------- #
#  `.dds` parsing / writing
# --------------------------------------------------------------------------- #
def parse_dds(data: bytes, source: str = "") -> TextureInfo:
    """Parse a standard ``.dds`` (128-byte header + DXT/mip payload)."""
    if len(data) < DDS_HEADER_SIZE or data[:4] != b"DDS ":
        raise ValueError("not a DDS file (missing 'DDS ' magic)")
    height = _u32(data, 12)
    width = _u32(data, 16)
    mips = _u32(data, 28)
    pf_flags = _u32(data, 80)
    fourcc = data[84:88]
    pf_bits = _u32(data, 88)

    r5g5b5 = False
    gm_format = None
    # DDPF_RGB (0x40): an uncompressed form.  If 16-bit and the masks match
    # A1R5G5B5 we map it to the GM 16-bit form (used by UI icons).
    if pf_flags & 0x40:  # DDPF_RGB
        rmask = _u32(data, 92)
        if pf_bits == 16 and rmask == 0x7C00:
            gm_format = _T_FMT_16BBP
            r5g5b5 = True
        elif pf_bits == 32 and rmask == 0xFF0000:
            gm_format = 5  # uncompressed 32-bit A8R8G8B8-class GM code
        else:
            raise ValueError(f"unsupported DDS RGB format (bits={pf_bits}, "
                             f"rmask=0x{rmask:x})")
    elif fourcc in _DDS_FOURCC_TO_T:
        gm_format = _DDS_FOURCC_TO_T[fourcc]
    else:
        raise ValueError(f"unsupported DDS fourCC {fourcc!r}")

    return TextureInfo(
        width=width,
        height=height,
        mip_count=mips,
        gm_format=gm_format,
        fourcc=fourcc,
        r5g5b5=r5g5b5,
        faces=6 if _u32(data, 112) & 0x200 else 1,
        payload=data[DDS_HEADER_SIZE:],
        source=source,
    )


def build_dds_header(info: TextureInfo) -> bytes:
    """Build a standard 128-byte DDS header from a :class:`TextureInfo`."""
    hdr = bytearray(DDS_HEADER_SIZE)
    hdr[0:4] = b"DDS "
    struct.pack_into("<I", hdr, 4, 124)                       # dwSize
    struct.pack_into("<I", hdr, 8, 0x000A1007)                # dwFlags
    struct.pack_into("<I", hdr, 12, info.height)
    struct.pack_into("<I", hdr, 16, info.width)
    if info.uncompressed_bpp:
        top_mip_pitch = info.width * info.height * info.uncompressed_bpp
    else:
        top_mip_pitch = (
            ((info.width + 3) // 4) * ((info.height + 3) // 4) * info.block_size
        )
    struct.pack_into("<I", hdr, 20, top_mip_pitch)             # pitch/linear size
    struct.pack_into("<I", hdr, 24, 0)                         # depth
    struct.pack_into("<I", hdr, 28, info.mip_count)            # mipmap count
    # DDS_PIXELFORMAT (offset 76)
    struct.pack_into("<I", hdr, 76, 32)                        # pf.dwSize
    if info.r5g5b5 or info.gm_format in _T_FMT_16BBP_ALL:
        struct.pack_into("<I", hdr, 80, 0x40 | 0x1)            # DDPF_RGB|ALPHAPIXELS
        struct.pack_into("<I", hdr, 88, 16)                    # dwRGBBitCount
        struct.pack_into("<I", hdr, 92, 0x7C00)                # R mask
        struct.pack_into("<I", hdr, 96, 0x03E0)                # G mask
        struct.pack_into("<I", hdr, 100, 0x001F)               # B mask
        struct.pack_into("<I", hdr, 104, 0x8000)               # A mask
    elif info.gm_format in _T_FMT_32BBP:
        struct.pack_into("<I", hdr, 80, 0x40 | 0x1)            # DDPF_RGB|ALPHAPIXELS
        struct.pack_into("<I", hdr, 88, 32)                    # dwRGBBitCount
        struct.pack_into("<I", hdr, 92, 0x00FF0000)            # R mask
        struct.pack_into("<I", hdr, 96, 0x0000FF00)            # G mask
        struct.pack_into("<I", hdr, 100, 0x000000FF)           # B mask
        struct.pack_into("<I", hdr, 104, 0xFF000000)           # A mask
    else:
        struct.pack_into("<I", hdr, 80, 0x4)                    # DDPF_FOURCC
        hdr[84:88] = info.fourcc
    # dwCaps (offset 108): texture | complex | mipmap
    struct.pack_into("<I", hdr, 108, 0x401008)
    if info.faces > 1:
        # dwCaps2 (offset 112): DDSCAPS2_CUBEMAP + all six face flags.  Without
        # this the header describes a 2D texture while the payload carries six
        # faces, which no DDS loader can reconcile.
        struct.pack_into("<I", hdr, 112, 0x200 | 0x400 | 0x800 | 0x1000
                         | 0x2000 | 0x4000 | 0x8000)
    return bytes(hdr)


def write_dds(info: TextureInfo) -> bytes:
    """Serialise a :class:`TextureInfo` into a standard ``.dds`` file."""
    return build_dds_header(info) + info.payload


# --------------------------------------------------------------------------- #
#  conversions
# --------------------------------------------------------------------------- #
def t_to_dds(data: bytes, source: str = "") -> bytes:
    """Convert a GM ``.t`` file into a standard ``.dds``."""
    return write_dds(parse_t(data, source))


def dds_to_t(data: bytes, orig_t_header: Optional[bytes] = None,
             source: str = "") -> bytes:
    """Convert a standard ``.dds`` back into a GM ``.t``.

    ``orig_t_header`` optionally supplies the original 59-byte ``.t`` header so
    the round-trip is byte-identical (preserving the opaque ``@24``/``@52``
    flags that are not present in a ``.dds``).
    """
    info = parse_dds(data, source)
    return write_t(info, orig_t_header)


_KNOWN_SUFFIXES = (".t", ".dds")


def convert_file(src_path: str, dst_path: str) -> TextureInfo:
    """Convert a texture file between ``.t`` and ``.dds`` based on extensions.

    The destination extension decides the direction.  Returns the
    :class:`TextureInfo` produced.
    """
    src_lower = src_path.lower()
    dst_lower = dst_path.lower()
    if not src_lower.endswith(_KNOWN_SUFFIXES):
        raise ValueError(f"unknown source extension: {src_path}")
    if not dst_lower.endswith(_KNOWN_SUFFIXES):
        raise ValueError(f"unknown destination extension: {dst_path}")
    with open(src_path, "rb") as fh:
        data = fh.read()
    if src_lower.endswith(".t") and dst_lower.endswith(".dds"):
        out = t_to_dds(data, os.path.basename(src_path))
    elif src_lower.endswith(".dds") and dst_lower.endswith(".t"):
        # Preserve the matching `.t` header if one exists next to the source
        # so the round-trip is byte-identical.
        orig = None
        cand = src_path[:-4] + ".t"
        if os.path.exists(cand):
            with open(cand, "rb") as fh:
                orig = fh.read(59)
        out = dds_to_t(data, orig, os.path.basename(src_path))
    else:
        raise ValueError(
            f"cannot convert {src_path} -> {dst_path} (same extension)"
        )
    with open(dst_path, "wb") as fh:
        fh.write(out)
    # Re-parse to return the info.
    with open(dst_path, "rb") as fh:
        raw = fh.read()
    if dst_lower.endswith(".dds"):
        return parse_dds(raw, os.path.basename(dst_path))
    return parse_t(raw, os.path.basename(dst_path))


# --------------------------------------------------------------------------- #
#  glTF texture discovery
# --------------------------------------------------------------------------- #
def find_diffuse_texture(g_path: str, attrs: Dict[str, str]) -> Optional[str]:
    """Find the on-disk diffuse texture referenced by a ``.g`` file.

    The ``.g`` attributes name e.g. ``material0_diffuse`` (often a ``.tga``
    reference that does not ship); the actual file in the bundle is ``.t`` or
    ``.dds``.  This tries, in order: ``.dds``, ``.t``, then the exact name.

    Returns the absolute texture path, or ``None`` if none could be located.
    """
    base_dir = os.path.dirname(os.path.abspath(g_path))
    # collect candidate names (diffuse then any material*_diffuse)
    names = []
    for key, val in attrs.items():
        if key.lower().endswith("_diffuse") and val:
            names.append(val)
        elif key.lower() in ("material0_diffuse", "diffuse") and val:
            names.append(val)
    for name in names:
        stem = os.path.splitext(name)[0]
        for cand in (name, stem + ".dds", stem + ".t"):
            p = os.path.join(base_dir, cand)
            if os.path.exists(p):
                return p
    return None


# --------------------------------------------------------------------------- #
#  PNG -> .t (Blender re-saved textures)
# --------------------------------------------------------------------------- #
def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png(data: bytes):
    """Minimal pure-Python PNG decoder -> ``(width, height, rgba_rows)``.

    Supports what image editors actually write for game textures: 8-bit
    depth, colour types 0 (grey), 2 (RGB), 3 (palette), 4 (grey+alpha),
    6 (RGBA), no interlace.  Returns a list of ``width*4``-byte rows.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos, idat, palette, trns = 8, [], b"", b""
    width = height = bit_depth = color_type = None
    while pos + 8 <= len(data):
        (length,), ctype = struct.unpack_from(">I", data, pos), data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = (
                struct.unpack(">IIBB", chunk[:10]))
        elif ctype == b"PLTE":
            palette = chunk
        elif ctype == b"tRNS":
            trns = chunk
        elif ctype == b"IDAT":
            idat.append(chunk)
        elif ctype == b"IEND":
            break
    if width is None:
        raise ValueError("PNG has no IHDR")
    if bit_depth != 8:
        raise ValueError(f"unsupported PNG bit depth {bit_depth}")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError(f"unsupported PNG colour type {color_type}")
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    # un-filter
    out = bytearray(stride * height)
    prev = bytearray(stride)
    ptr = 0
    for y in range(height):
        f = raw[ptr]
        ptr += 1
        line = bytearray(raw[ptr:ptr + stride])
        ptr += stride
        if f == 1:      # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif f == 2:    # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:    # Average
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:    # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                c = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    # to RGBA rows
    rows = []
    for y in range(height):
        row = bytearray(width * 4)
        base = y * stride
        if color_type == 6:
            row[:] = out[base:base + stride]
        elif color_type == 2:
            for x in range(width):
                o = base + 3 * x
                row[4 * x:4 * x + 3] = out[o:o + 3]
                row[4 * x + 3] = 0xFF
        elif color_type == 0:
            for x in range(width):
                g = out[base + x]
                row[4 * x:4 * x + 3] = bytes((g, g, g))
                row[4 * x + 3] = 0xFF
        elif color_type == 4:
            for x in range(width):
                g = out[base + 2 * x]
                row[4 * x:4 * x + 3] = bytes((g, g, g))
                row[4 * x + 3] = out[base + 2 * x + 1]
        elif color_type == 3:
            for x in range(width):
                idx = out[base + x] * 3
                row[4 * x:4 * x + 3] = palette[idx:idx + 3]
                row[4 * x + 3] = trns[out[base + x]] if len(trns) > out[base + x] \
                    else 0xFF
        rows.append(bytes(row))
    return width, height, rows


def png_to_t(data: bytes) -> bytes:
    """Re-encode a PNG into a native GM ``.t`` (uncompressed A8R8G8B8).

    Blender saves textures as PNG; the engine wants a `.t`.  A DXT
    compressor is out of scope, but the container also has an uncompressed
    32-bit form (code 4) every shipped build accepts, so the texture ships
    usable — just larger than the original DXT.
    """
    width, height, rows = decode_png(data)

    def mip_count_of(w: int, h: int) -> int:
        n = 0
        while w >= 1 and h >= 1:
            n += 1
            w, h = w >> 1, h >> 1
        return n

    def downsample(src, w: int, h: int):
        """2x2 box average of RGBA rows -> the (w>>1, h>>1) level."""
        half_w = max(w >> 1, 1)
        out = []
        for y in range(0, h, 2):
            y1 = min(y + 1, h - 1)
            r = bytearray(half_w * 4)
            xo = 0
            for x in range(0, w, 2):
                x1 = min(x + 1, w - 1)
                for c in range(4):
                    v = (src[y][4 * x + c] + src[y][4 * x1 + c]
                         + src[y1][4 * x + c] + src[y1][4 * x1 + c])
                    r[xo + c] = (v + 2) >> 2
                xo += 4
            out.append(bytes(r))
        return out

    payload = bytearray()
    cur_w, cur_h, cur = width, height, rows
    while cur_w >= 1 and cur_h >= 1:
        for row in cur:
            for x in range(cur_w):
                o = 4 * x
                payload += bytes((row[o + 2], row[o + 1], row[o],
                                  row[o + 3]))
        cur = downsample(cur, cur_w, cur_h)
        cur_w, cur_h = cur_w >> 1, cur_h >> 1
    info = TextureInfo(
        width=width, height=height,
        mip_count=mip_count_of(width, height),
        gm_format=4, payload=bytes(payload),
    )
    return write_t(info)
