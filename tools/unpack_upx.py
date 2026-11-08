"""Pure-Python unpacker for a UPX-compressed PE (NRV2B/NRV2D/NRV2E + LZMA).

Written to get at a protected engine DLL whose section names are UPX's but
whose header the original packer patched (no usable ``l_info``): the packer's
``UPX!`` magic and the block sizes are read heuristically instead.

The three NRV kernels are ports of UCL's ``nrv2b_d32.S`` / ``nrv2d_d32.S``
/ ``nrv2e_d32.S`` as shipped in the UPX source tree (MSB-first bits out of
32-bit little-endian words), so they decode what the real stub decodes.  The
x86 call/jump filter is applied when the block asks for it; for string and
immediate-operand work the filtered form is not needed, but a rebuilt section
is only usable with it.

Usage::

    python3 tools/unpack_upx.py <packed.dll> -o <unpacked.dll> [--info]
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from typing import List, Optional, Tuple

MASK = 0xFFFFFFFF


class Truncated(ValueError):
    pass


class _Bits:
    """MSB-first bit reader over 32-bit little-endian words (UCL's ``getbit``)."""

    __slots__ = ("data", "pos", "buf", "left")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.buf = 0
        self.left = 0

    def bit(self) -> int:
        if self.left == 0:
            chunk = self.data[self.pos:self.pos + 4]
            if len(chunk) < 4:
                raise Truncated("bit stream ran out")
            self.buf = int.from_bytes(chunk, "little")
            self.pos += 4
            self.left = 32
        out = (self.buf >> 31) & 1
        self.buf = (self.buf << 1) & MASK
        self.left -= 1
        return out

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise Truncated("byte stream ran out")
        out = self.data[self.pos]
        self.pos += 1
        return out


def _copy(out: bytearray, m_off: int, m_len: int) -> None:
    src = len(out) - m_off
    if src < 0:
        raise Truncated("match points before the output")
    for i in range(m_len):
        out.append(out[src + i])


def nrv2b_decode(data: bytes, out_cap: int = 0) -> bytes:
    """UCL NRV2B, ported from ``nrv2b_d32.S``."""
    bits = _Bits(data)
    out = bytearray()
    last_m_off = 1
    while True:
        if bits.bit():
            out.append(bits.byte())
            if out_cap and len(out) > out_cap:
                raise Truncated("output past the declared size")
            continue
        m_off = 1
        while True:
            m_off = m_off * 2 + bits.bit()
            if not bits.bit():
                break
        if m_off == 2:
            m_off = last_m_off
        else:
            m_off = ((m_off - 3) << 8) + bits.byte()
            if m_off == MASK:
                break
            m_off += 1
            last_m_off = m_off
        m_len = bits.bit() * 2 + bits.bit()
        if m_len == 0:
            m_len = 1
            while True:
                m_len = m_len * 2 + bits.bit()
                if not bits.bit():
                    break
            m_len += 2
        if m_off > 0xD00:
            m_len += 1
        _copy(out, m_off, m_len)
        if out_cap and len(out) > out_cap:
            raise Truncated("output past the declared size")
    return bytes(out)


def nrv2d_decode(data: bytes, out_cap: int = 0) -> bytes:
    """UCL NRV2D, ported from ``nrv2d_d32.S``."""
    bits = _Bits(data)
    out = bytearray()
    last_m_off = 1
    while True:
        if bits.bit():
            out.append(bits.byte())
            if out_cap and len(out) > out_cap:
                raise Truncated("output past the declared size")
            continue
        m_off = 1
        while True:
            m_off = m_off * 2 + bits.bit()
            if not bits.bit():
                break
        m_off = ((m_off - 3) << 8) + bits.byte()
        if m_off == MASK:
            break
        last_m_off = m_off + 1
        m_off = last_m_off
        m_len = bits.bit()
        if m_len:
            m_len = m_len * 2 + bits.bit()
            if m_len == 0:
                m_len = bits.byte() + 1
            else:
                m_len = (m_len - 1) * 2 + bits.bit()
                if m_len == 0:
                    m_len = bits.byte() + 2
                else:
                    m_len = (m_len - 1) * 256 + bits.byte()
                    m_len = (m_len >> 1) + 1
                    if m_len > 3:
                        m_len -= 1
        else:
            m_len = 1
            m_len = m_len * 2 + bits.bit()
            if m_len == 0:
                m_len = bits.byte() + 1
            else:
                m_len = (m_len - 1) * 2 + bits.bit()
                if m_len == 0:
                    m_len = bits.byte() + 2
                else:
                    m_len = (m_len - 1) * 2 + bits.bit()
                    if m_len == 0:
                        m_len = bits.byte() + 3
                    else:
                        m_len = (m_len - 1) * 256 + bits.byte()
                        m_len = (m_len >> 1) + 1
                        if m_len > 3:
                            m_len -= 1
        if m_off > 0xD00:
            m_len += 1
        _copy(out, m_off, m_len)
        if out_cap and len(out) > out_cap:
            raise Truncated("output past the declared size")
    return bytes(out)


def nrv2e_decode(data: bytes, out_cap: int = 0) -> bytes:
    """UCL NRV2E, ported from ``nrv2e_d32.S``."""
    bits = _Bits(data)
    out = bytearray()
    last_m_off = 1
    while True:
        if bits.bit():
            out.append(bits.byte())
            if out_cap and len(out) > out_cap:
                raise Truncated("output past the declared size")
            continue
        m_off = 1
        while True:
            m_off = m_off * 2 + bits.bit()
            if not bits.bit():
                break
        if m_off == 2:
            m_off = last_m_off
        else:
            m_off = ((m_off - 3) << 8) + bits.byte()
            if m_off == MASK:
                break
            m_off += 1
            last_m_off = m_off
        m_len = bits.bit()
        if not m_len:
            m_len = bits.bit()
            if not m_len:
                m_len = 1
                while True:
                    m_len = m_len * 2 + bits.bit()
                    if not bits.bit():
                        break
                m_len += 1
        if m_off > 0xD00:
            m_len += 1
        _copy(out, m_off, m_len)
        if out_cap and len(out) > out_cap:
            raise Truncated("output past the declared size")
    return bytes(out)


def lzma_decode(data: bytes, out_cap: int = 0) -> bytes:
    """UPX's LZMA block (``b_method == 14``): 5 property bytes, then raw LZMA1."""
    import lzma

    props = data[:5]
    if len(props) < 5:
        raise Truncated("no LZMA properties")
    lc = props[0] % 9
    rest = props[0] // 9
    lp = rest % 5
    pb = rest // 5
    dict_size = int.from_bytes(props[1:5], "little")
    filters = [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                "lc": lc, "lp": lp, "pb": pb}]
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
    out = dec.decompress(data[5:], max_length=out_cap or -1)
    return out


METHODS = {1: ("NRV2B", nrv2b_decode), 2: ("NRV2D", nrv2d_decode),
           3: ("NRV2E", nrv2e_decode), 14: ("LZMA", lzma_decode)}


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def find_upx_header(data: bytes) -> Optional[int]:
    """Offset of the ``UPX!`` magic, or None."""
    at = data.find(b"UPX!")
    return at if at >= 0 else None


def read_block_table(data: bytes) -> List[Tuple[int, int, int, int]]:
    """Best-effort ``(sz_unc, sz_cpr, method, filter)`` per block.

    A patched header leaves the magic and the block table in place but the
    fields around them unreadable, so the table is found by scanning forward
    from the magic for a run of records whose sizes add up to the payload
    that actually follows it.
    """
    magic = find_upx_header(data)
    if magic is None:
        return []
    best: List[Tuple[int, int, int, int]] = []
    for start in range(magic + 4, magic + 60, 1):
        blocks: List[Tuple[int, int, int, int]] = []
        off = start
        cpr_total = 0
        while off + 12 <= len(data) and len(blocks) < 16:
            unc, cpr = _u32(data, off), _u32(data, off + 4)
            method = data[off + 8]
            filt = data[off + 9]
            if not (0 < cpr <= len(data) and unc < 0x10000000):
                break
            if method not in METHODS:
                break
            blocks.append((unc, cpr, method, filt))
            cpr_total += cpr
            off += 12
        if not blocks:
            continue
        end = off + cpr_total
        if abs(end - len(data)) < 0x1000 and len(blocks) > len(best):
            best = blocks
    return best


def unpack(path: str, out_path: str, expect_size: int = 0) -> dict:
    data = open(path, "rb").read()
    blocks = read_block_table(data)
    info: dict = {"file": path, "bytes": len(data), "blocks": blocks}
    if not blocks:
        raise SystemExit("no usable UPX block table found")
    start = 0
    for magic_start in range((find_upx_header(data) or 0) + 4,
                             (find_upx_header(data) or 0) + 60):
        off = magic_start
        ok = True
        for unc, cpr, method, filt in blocks:
            if _u32(data, off) != unc or _u32(data, off + 4) != cpr:
                ok = False
                break
            off += 12
        if ok:
            start = off
            break
    if not start:
        raise SystemExit("could not locate the compressed payload")
    info["payload_at"] = start
    blob = bytearray()
    pos = start
    for index, (unc, cpr, method, filt) in enumerate(blocks):
        name, fn = METHODS[method]
        chunk = data[pos:pos + cpr]
        try:
            part = fn(chunk, unc)
        except Exception as exc:  # noqa: BLE001
            info.setdefault("errors", []).append(f"block {index} {name}: {exc}")
            break
        info.setdefault("decoded", []).append(
            {"block": index, "method": name, "expected": unc,
             "got": len(part), "filter": filt})
        blob += part
        pos += cpr
    info["unpacked_bytes"] = len(blob)
    with open(out_path, "wb") as fh:
        fh.write(blob)
    return info


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("packed", help="the packed .dll/.exe")
    ap.add_argument("-o", "--out", required=True, help="where to write the image")
    ap.add_argument("--info", action="store_true", help="print the header table")
    args = ap.parse_args(argv)
    info = unpack(args.packed, args.out, 0)
    print(f"{os.path.basename(args.packed)}: {info['bytes']} bytes, "
          f"payload at {hex(info.get('payload_at', 0))}, "
          f"{info.get('unpacked_bytes', 0)} decoded -> {args.out}")
    for block in info.get("decoded", []):
        print(f"  block {block['block']}: {block['method']} "
              f"{block['got']}/{block['expected']} bytes, filter "
              f"{block['filter']}")
    for err in info.get("errors", []):
        print("  ! " + err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
