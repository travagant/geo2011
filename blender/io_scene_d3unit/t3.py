# -*- coding: utf-8 -*-
"""Reader/writer for Disciples III texture files (.t).

    u32 version (=1)
    u32 format (1 R5G6B5 | 2 A4R4G4B4 | 3 A1R5G5B5 | 4 X8R8G8B8 |
                5 A8R8G8B8 | 6 DXT1 | 7 DXT3 | 8 DXT5 | 9 R32F | 10 R16F)
    u32 flags  (=0 in samples)
    u32 block_bytes  (8 for DXT1, 16 for DXT3/5)
    u32 width
    u32 height
    u8  unused[3]      -- header is 27 bytes total (not dword aligned!)
    mip chain, largest first, down to (and including) 8x8-ish level.

Decoding/encoding is pure python (no deps).  The bundled .t files in the
repo are single DXT1 textures of full mip chain.
"""
import struct

FMT = {1: 'R5G6B5', 2: 'A4R4G4B4', 3: 'A1R5G5B5', 4: 'X8R8G8B8',
       5: 'A8R8G8B8', 6: 'DXT1', 7: 'DXT3', 8: 'DXT5'}
FMT_ID = {v: k for k, v in FMT.items()}


def mip_sizes(w, h):
    sizes = []
    while True:
        sizes.append((max(w, 4), max(h, 4)))
        if w <= 8 or h <= 8:
            break
        w //= 2
        h //= 2
    return sizes


def read_t(path):
    d = open(path, 'rb').read()
    ver, fmt, flags, block, w, h = struct.unpack_from('<6I', d, 0)
    name = FMT[fmt]
    p = 27
    data = []
    for mw, mh in mip_sizes(w, h):
        n = (mw // 4) * (mh // 4) * block
        data.append(d[p:p + n])
        p += n
    return {'width': w, 'height': h, 'format': name, 'flags': flags,
            'mips': data}


def _u565(c):
    return ((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, \
        (c & 31) * 255 // 31


def decode_mip(raw, fmt, w, h):
    """Return RGBA bytes (w*h*4) for one mip of DXT1/3/5 or 8888."""
    out = bytearray(w * h * 4)
    bw, bh = w // 4, h // 4
    if fmt in ('DXT1', 'DXT3', 'DXT5'):
        for byi in range(bh):
            for bxi in range(bw):
                boff = (byi * bw + bxi) * (8 if fmt == 'DXT1' else 16)
                o = (byi * 4 * w + bxi * 4) * 4
                if fmt != 'DXT1':
                    a = raw[boff:boff + 8]
                    boff += 8
                c0, c1, r0, r1 = struct.unpack_from('<HHHH', raw, boff)
                cols = [_u565(c0), _u565(c1)]
                if c0 > c1:
                    cols.append(tuple((2 * cols[0][i] + cols[1][i]) // 3
                                      for i in range(3)))
                    cols.append(tuple((cols[0][i] + 2 * cols[1][i]) // 3
                                      for i in range(3)))
                else:
                    cols.append(tuple((cols[0][i] + cols[1][i]) // 2
                                      for i in range(3)))
                    cols.append((0, 0, 0))
                alphas = None
                if fmt == 'DXT3':
                    bits = int.from_bytes(a, 'little')
                    alphas = [[0] * 4 for _ in range(4)]
                    for yy in range(4):
                        for xx in range(4):
                            n = (bits >> (48 - 4 * (yy * 4 + xx))) & 0xF
                            alphas[yy][xx] = n * 17
                elif fmt == 'DXT5':
                    a0, a1 = a[0], a[1]
                    lut = [a0, a1]
                    if a0 > a1:
                        lut += [(6 * a0 + a1) // 7, (5 * a0 + 2 * a1) // 7,
                                (4 * a0 + 3 * a1) // 7, (3 * a0 + 4 * a1) // 7,
                                (2 * a0 + 5 * a1) // 7, (a0 + 6 * a1) // 7]
                    else:
                        lut += [(4 * a0 + a1) // 5, (3 * a0 + 2 * a1) // 5,
                                (2 * a0 + 3 * a1) // 5, (a0 + 4 * a1) // 5,
                                0, 255]
                    bits = int.from_bytes(a[2:8], 'little')
                    alphas = [[0] * 4 for _ in range(4)]
                    for yy in range(4):
                        for xx in range(4):
                            alphas[yy][xx] = lut[(bits >> (3 * (yy * 4 + xx))) & 7]
                for yy in range(4):
                    bits = (r0 >> (12 - 4 * yy)) if yy < 2 \
                        else (r1 >> (12 - 4 * (yy - 2)))
                    for xx in range(4):
                        q = o + (yy * w + xx) * 4
                        out[q:q + 3] = bytes(
                            cols[(bits >> (12 - 4 * xx)) & 3])
                        out[q + 3] = alphas[yy][xx] if alphas else 255
    elif fmt in ('A8R8G8B8', 'X8R8G8B8'):
        for i in range(w * h):
            b, g, r, a = raw[4 * i:4 * i + 4]
            out[4 * i:4 * i + 4] = bytes((r, g, b, 255 if a == 0 else a))
    else:
        raise NotImplementedError('decode for ' + fmt)
    return bytes(out)


def _pack565(r, g, b):
    return (r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)


def encode_dxt1_block(cols):
    """cols: 16 (r,g,b) tuples, row-major 4x4 -> 8 bytes."""
    lum = [0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] for c in cols]
    li, hi = lum.index(min(lum)), lum.index(max(lum))
    p0 = _pack565(*cols[hi])
    p1 = _pack565(*cols[li])
    if p0 == p1:
        return struct.pack('<HHHH', p0, p0, 0, 0)
    if p0 < p1:
        p0, p1 = p1, p0
    c0, c1 = _u565(p0), _u565(p1)
    pal = [c0, c1,
           tuple((2 * c0[i] + c1[i]) // 3 for i in range(3)),
           tuple((c0[i] + 2 * c1[i]) // 3 for i in range(3))]
    r0 = r1 = 0
    for yy in range(4):
        for xx in range(4):
            c = cols[yy * 4 + xx]
            best, bi = 1e12, 0
            for i, p in enumerate(pal):
                e = (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2
                if e < best:
                    best, bi = e, i
            shift = 12 - 4 * xx
            if yy < 2:
                r0 |= bi << shift
            else:
                r1 |= bi << shift
    return struct.pack('<HHHH', p0, p1, r0, r1)


def encode_dxt1(rgb, w, h):
    out = bytearray()
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            cols = []
            for yy in range(4):
                for xx in range(4):
                    y2 = min(by + yy, h - 1)
                    x2 = min(bx + xx, w - 1)
                    o = (y2 * w + x2) * 3
                    cols.append((rgb[o], rgb[o + 1], rgb[o + 2]))
            out += encode_dxt1_block(cols)
    return bytes(out)


def downscale_x2(rgba, w, h):
    nw, nh = max(w // 2, 1), max(h // 2, 1)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        for x in range(nw):
            acc = [0, 0, 0, 0]
            n = 0
            for dy in (0, 1):
                for dx in (0, 1):
                    sy = min(y * 2 + dy, h - 1)
                    sx = min(x * 2 + dx, w - 1)
                    o = (sy * w + sx) * 4
                    for c in range(4):
                        acc[c] += rgba[o + c]
                    n += 1
            o = (y * nw + x) * 4
            out[o:o + 4] = bytes(c // n for c in acc)
    return bytes(out), nw, nh


def write_t_dxt1(path, rgba, w, h):
    """rgba: w*h*4 bytes; writes DXT1 + full mip chain (alpha ignored)."""
    mips = []
    cur, cw, ch = rgba, w, h
    while True:
        rgb = bytearray(cw * ch * 3)
        for i in range(cw * ch):
            rgb[3 * i:3 * i + 3] = cur[4 * i:4 * i + 3]
        mips.append(encode_dxt1(bytes(rgb), cw, ch))
        if cw <= 8 or ch <= 8:
            break
        cur, cw, ch = downscale_x2(cur, cw, ch)
    out = bytearray(struct.pack('<6I', 1, 6, 0, 8, w, h) + b'\x00\x00\x00')
    for m in mips:
        out += m
    open(path, 'wb').write(bytes(out))
    return len(out)
