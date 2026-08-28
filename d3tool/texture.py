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
from dataclasses import dataclass, field
from typing import Dict, Optional

# GM `.t` pixel-format code -> DDS fourCC / block size.
_T_FMT_TO_DDS = {
    6: (b"DXT1", 8),
    7: (b"DXT3", 16),
    8: (b"DXT5", 16),
}
_DDS_FOURCC_TO_T = {v[0]: k for k, v in _T_FMT_TO_DDS.items()}

# GM format code for the 16-bit A1R5G5B5 form (UI rings/small icons).
_T_FMT_16BBP = 3

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
    #: The compressed pixel payload (byte-identical between `.t` and `.dds`).
    payload: bytes = b""
    #: Original filename extension (for error messages / logging).
    source: str = ""

    @property
    def block_size(self) -> int:
        if self.r5g5b5:
            return 2  # A1R5G5B5 is 2 bytes per pixel
        return _T_FMT_TO_DDS[self.gm_format][1]

    def payload_size(self) -> int:
        return sum(
            ((max(1, self.width // 2 ** i) + 3) // 4)
            * ((max(1, self.height // 2 ** i) + 3) // 4)
            * self.block_size
            for i in range(max(self.mip_count, 1))
        )


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
    if not r5g5b5:
        if fmt not in _T_FMT_TO_DDS:
            raise ValueError(f"unsupported .t pixel format code {fmt}")
        fourcc = _T_FMT_TO_DDS[fmt][0]
    return TextureInfo(
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
        struct.pack_into("<I", hdr, 24, 1 if not info.r5g5b5 else 1)
        struct.pack_into("<I", hdr, 28, 0)
        for off in (32, 36, 40):
            struct.pack_into("<I", hdr, off, 0x01000000)
        struct.pack_into("<I", hdr, 52, 0x00417000)
    # Always refresh the fields that describe the pixel data.
    struct.pack_into("<I", hdr, 4, info.gm_format)
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
    top_mip_pitch = (
        ((info.width + 3) // 4) * ((info.height + 3) // 4) * info.block_size
    )
    struct.pack_into("<I", hdr, 20, top_mip_pitch)             # pitch/linear size
    struct.pack_into("<I", hdr, 24, 0)                         # depth
    struct.pack_into("<I", hdr, 28, info.mip_count)            # mipmap count
    # DDS_PIXELFORMAT (offset 76)
    struct.pack_into("<I", hdr, 76, 32)                        # pf.dwSize
    if info.r5g5b5:
        struct.pack_into("<I", hdr, 80, 0x40 | 0x1)            # DDPF_RGB|ALPHAPIXELS
        struct.pack_into("<I", hdr, 88, 16)                    # dwRGBBitCount
        struct.pack_into("<I", hdr, 92, 0x7C00)                # R mask
        struct.pack_into("<I", hdr, 96, 0x03E0)                # G mask
        struct.pack_into("<I", hdr, 100, 0x001F)               # B mask
        struct.pack_into("<I", hdr, 104, 0x8000)               # A mask
    else:
        struct.pack_into("<I", hdr, 80, 0x4)                    # DDPF_FOURCC
        hdr[84:88] = info.fourcc
    # dwCaps (offset 108): texture | complex | mipmap
    struct.pack_into("<I", hdr, 108, 0x401008)
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
            orig = open(cand, "rb").read(59)
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
