"""Experimental PNG -> leader icon .t encoder; no game verification implied."""
import struct
import zlib
from . import texture, leader_icon_probe


def png_to_icon(data, slot):
    """Encode a correctly sized, non-interlaced 8-bit RGB/RGBA PNG.

    No rescaling or cropping. DXT3 for five icons, 1-bit alpha A1R5G5B5 for
    the ring, like the inspected Warrior assets. Lossy, not yet game-tested.
    """
    if slot not in leader_icon_probe._SLOTS or not isinstance(data, bytes) or len(data) > 16 * 1024 * 1024:
        raise ValueError('invalid slot or PNG data')
    _, w, h, fmt = leader_icon_probe._SLOTS[slot]
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('expected PNG')
    pos = 8
    idat = bytearray()
    seen = []
    channels = 0
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError('truncated PNG chunk')
        length = struct.unpack_from('>I', data, pos)[0]
        end = pos + 12 + length
        if end > len(data):
            raise ValueError('truncated PNG chunk')
        kind, chunk = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        if zlib.crc32(kind + chunk) & 0xffffffff != struct.unpack_from('>I', data, end - 4)[0]:
            raise ValueError('PNG CRC mismatch')
        seen.append(kind)
        if kind == b'IHDR':
            if pos != 8 or length != 13:
                raise ValueError('invalid PNG IHDR')
            iw, ih, depth, color, comp, filt, interlace = struct.unpack('>IIBBBBB', chunk)
            if (iw, ih) != (w, h) or depth != 8 or color not in (2, 6) or (comp, filt, interlace) != (0, 0, 0):
                raise ValueError(f'{slot} requires {w}x{h} non-interlaced 8-bit RGB/RGBA PNG')
            channels = 3 if color == 2 else 4
        elif kind == b'IDAT':
            idat.extend(chunk)
        elif kind == b'IEND':
            if length or end != len(data):
                raise ValueError('invalid PNG end')
            break
        elif kind == b'tRNS':
            raise ValueError('PNG transparency chunk unsupported; use RGBA')
        elif kind[0] & 32 == 0 and kind != b'PLTE':
            raise ValueError('unsupported critical PNG chunk')
        pos = end
    if seen.count(b'IHDR') != 1 or seen.count(b'IEND') != 1 or not idat or seen[-1] != b'IEND' or not channels:
        raise ValueError('invalid PNG structure')
    size = h * (1 + w * channels)
    dec = zlib.decompressobj()
    raw = dec.decompress(bytes(idat), size + 1)
    if len(raw) != size or not dec.eof or dec.unused_data or dec.unconsumed_tail:
        raise ValueError('invalid PNG compressed size')
    if any(raw[y * (1 + w * channels)] > 4 for y in range(h)):
        raise ValueError('invalid PNG row filter')
    iw, ih, rows = texture.decode_png(data)
    assert (iw, ih) == (w, h)
    if fmt == 7:
        payload = bytearray()
        for y in range(0, h, 4):
            for x in range(0, w, 4):
                pixels = texture._block_pixels(rows, x, y, w, h)
                alpha = sum(((px[3] * 15 + 127) // 255) << (4 * i) for i, px in enumerate(pixels))
                payload.extend(alpha.to_bytes(8, 'little'))
                payload.extend(texture._bc1_block(pixels))
        info = texture.TextureInfo(w, h, 1, gm_format=7, fourcc=b'DXT3', payload=bytes(payload))
    else:
        payload = bytearray()
        for row in rows:
            for i in range(0, len(row), 4):
                r, g, b, a = row[i:i + 4]
                color = (0x8000 if a >= 128 else 0) | ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)
                payload.extend(struct.pack('<H', color))
        info = texture.TextureInfo(w, h, 1, gm_format=3, r5g5b5=True, payload=bytes(payload))
    result = texture.write_t(info)
    if len(info.payload) != info.payload_size():
        raise ValueError('unexpected icon payload size')
    return result
