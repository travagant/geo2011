# -*- coding: utf-8 -*-
"""Reader/writer for Disciples III geometry files (.g).

Format reverse-engineered from `geo2011.dle` (3ds Max 2011 exporter) and
sample unit files.  Little-endian, byte-packed (no alignment padding
anywhere except explicit string NUL terminators).

File layout
-----------
    header_blocks : one or two "gobj meta" blocks:
        u32 version (=3), u32 aux (e.g. 159), u32 one (=1)
        27 float32:   [0..2]   material ambient color (0.588 = 150/255)
                      [3],[7]  unused (=0)
                      [4..6]   diffuse color
                      [8..13]  zero
                      [14..16] scale (1,1,1)
                      [17]     zero
                      [18..26] zero
        u32 len + material_name bytes + NUL      (len includes NUL)
        u32 len + object_name  bytes + NUL
    mesh sections : one per gobj (body, weapon, ...), back to back:
        opaque u32 prefix, ending with  (nverts, nfaces, shadow_nv, 6)
            body sample   : (1, 4, 2, 2, 204245, 0, 3693, 4664, 0, 6)
            weapon sample : (2, 24945, 0, 537, 250, 1, 6)
        14 float32: object position (3), rotation (3),
                    bbox max (3), bbox min (3), bounding radius, -1.0
        u32 nattr
        nattr * ( u32 len + key + NUL , u32 len + value + NUL )   (attrs)
        vertex stream  (nverts * stride, see VERTEX below)
        face stream    (nfaces * 3 * u32, absolute indices)
        bone table     (bones_num records, see below)

Vertex record (new_vertex_weights_format == 1)
    u32   vertex color (packed RGBA, constant per mesh in samples)
    f32x3 position
    f32x3 normal
    u32   0xFFFFFFFF  (padding / "tangent w" placeholder, always -1 as float)
    f32x2 uv  (v is stored flipped: V_blender = 1 - v_file ... actually
               exporter writes UV with V down; we keep raw and flip on import)
    if weights_on_vertex > 1:
        f32 * (wov-1)  weights for bone slots 0..wov-2
        u8   *  wov    bone indices (table-local); last weight is implicit
                       1 - sum(others)
Face stream  : u32 i0, i1, i2 (CCW in engine space)
Bone record   : u32 len + name + NUL ; 16 f32 = 4x4 matrix, row major,
                last row = translation (row-vector convention), w=1/0.
"""
import struct

U32 = struct.Struct('<I')
FLT = struct.Struct('<f')


class _Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def u32(self):
        v, = U32.unpack_from(self.d, self.p)
        self.p += 4
        return v

    def take(self, n):
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def floats(self, n):
        v = struct.unpack_from('<%df' % n, self.d, self.p)
        self.p += 4 * n
        return v

    def cstr32(self):
        n = self.u32()
        s = self.take(n)
        return s[:-1].decode('utf-8', 'replace')


def _find_attrs_start(data, start, limit=4096):
    """Locate `u32 nattr ; u32 7 'dwNode\\0'` after *start*."""
    p = data.find(b'dwNode\x00', start, start + limit)
    while p != -1:
        nattr, = U32.unpack_from(data, p - 8)
        ln, = U32.unpack_from(data, p - 4)
        if ln == 7 and 1 <= nattr <= 128 and (nattr * 2 - 1) * 0 == 0:
            return p - 8
        p = data.find(b'dwNode\x00', p + 1, start + limit)
    raise ValueError('mesh attribute block not found after %d' % start)


def read_attributes(data, p):
    n = U32.unpack_from(data, p)[0]
    p += 4
    r = _Reader(data)
    r.p = p
    attrs = []
    for _ in range(n):
        k = r.cstr32()
        v = r.cstr32()
        attrs.append((k, v))
    return attrs, r.p


class Vertex:
    __slots__ = ('co', 'no', 'uv', 'color', 'bones', 'weights')


def read_vertices(data, p, n, wov):
    """Return (list[dict], new_offset)."""
    stride = 40 + (4 * (wov - 1) + wov) if wov > 1 else 40
    verts = []
    for i in range(n):
        off = p + stride * i
        color, = U32.unpack_from(data, off)
        px, py, pz, nx, ny, nz = struct.unpack_from('<6f', data, off + 4)
        u, v = struct.unpack_from('<2f', data, off + 32)
        bones, weights = [], []
        if wov > 1:
            ws = struct.unpack_from('<%df' % (wov - 1), data, off + 40)
            bs = data[off + 40 + 4 * (wov - 1): off + 40 + 4 * (wov - 1) + wov]
            total = 1.0 - sum(ws)
            for k in range(wov):
                w = ws[k] if k < wov - 1 else total
                bones.append(bs[k])
                weights.append(w)
        verts.append({'co': (px, py, pz), 'no': (nx, ny, nz), 'uv': (u, v),
                      'color': color, 'bones': bones, 'weights': weights})
    return verts, p + stride * n


def read_bones(data, p, n):
    bones = []
    for _ in range(n):
        ln, = U32.unpack_from(data, p)
        name = data[p + 4: p + 4 + ln - 1].decode('utf-8', 'replace')
        p += 4 + ln
        mat = struct.unpack_from('<16f', data, p)
        p += 64
        bones.append({'name': name, 'matrix': mat})
    return bones, p


def read_g(path):
    data = open(path, 'rb').read()
    r = _Reader(data)
    metas = []
    while True:
        v, aux, one = struct.unpack_from('<3I', data, r.p)
        if v != 3 or one != 1:
            break
        r.p += 12
        meta = {'aux': aux, 'floats': r.floats(27),
                'material': r.cstr32(), 'name': r.cstr32()}
        metas.append(meta)
    meshes = []
    while r.p < len(data):
        sec_start = r.p
        a = _find_attrs_start(data, r.p)
        # geometry counts: 4 u32 immediately before the 14 floats
        nattr_pos = a
        floats14_pos = nattr_pos - 56
        cnt4_pos = floats14_pos - 16
        nv, nf, shv, six = struct.unpack_from('<4I', data, cnt4_pos)
        prefix = list(struct.unpack('<%dI' % ((cnt4_pos - sec_start) // 4),
                                    data[sec_start:cnt4_pos]))
        hdr = struct.unpack_from('<14f', data, floats14_pos)
        attrs, p = read_attributes(data, a)
        ad = dict(attrs)
        wov = int(ad.get('weights_on_vertex', 1))
        nv2 = int(ad.get('vertexs_weights_num', nv))
        nf2 = int(ad.get('material0_triangles_num', nf))
        if nv2 != nv or nf2 != nf:
            raise ValueError('mesh header counts %r disagree with attrs %r'
                             % ((nv, nf), (nv2, nf2)))
        verts, p = read_vertices(data, p, nv, wov)
        faces = struct.unpack_from('<%dI' % (nf * 3), data, p)
        p += 12 * nf
        nbones = int(ad.get('bones_num', 0))
        bones, p = read_bones(data, p, nbones)
        meshes.append({
            'u_prefix': prefix, 'shadow_nv': shv, 'header_floats': hdr,
            'attrs': attrs, 'wov': wov, 'verts': verts,
            'faces': [(faces[3 * i], faces[3 * i + 1], faces[3 * i + 2])
                      for i in range(nf)],
            'bones': bones,
        })
        r.p = p
    return {'meta': metas, 'meshes': meshes}


# ----------------------------------------------------------------- writer

def _cstr(s):
    b = s.encode('utf-8') + b'\x00'
    return U32.pack(len(b)) + b


def write_g(path, g):
    out = bytearray()
    for meta in g['meta']:
        out += U32.pack(3) + U32.pack(meta['aux']) + U32.pack(1)
        out += struct.pack('<27f', *meta['floats'])
        out += _cstr(meta['material'])
        out += _cstr(meta['name'])
    for m in g['meshes']:
        nv, nf = len(m['verts']), len(m['faces'])
        wov = m['wov']
        for u in m['u_prefix']:
            out += U32.pack(u)
        out += struct.pack('<4I', nv, nf, m['shadow_nv'], 6)
        out += struct.pack('<14f', *m['header_floats'])
        out += U32.pack(len(m['attrs']))
        for k, v in m['attrs']:
            out += _cstr(k) + _cstr(v)
        stride = 40 + (4 * (wov - 1) + wov) if wov > 1 else 40
        for vd in m['verts']:
            rec = bytearray(stride)
            struct.pack_into('<I', rec, 0, vd['color'])
            struct.pack_into('<6f', rec, 4, *vd['co'], *vd['no'])
            struct.pack_into('<I', rec, 28, 0xFFFFFFFF)
            struct.pack_into('<2f', rec, 32, *vd['uv'])
            if wov > 1:
                bones = vd['bones'] + [0] * wov
                wts = list(vd['weights']) + [0.0] * wov
                struct.pack_into('<%df' % (wov - 1), rec, 40, *wts[:wov - 1])
                boff = 40 + 4 * (wov - 1)
                for k in range(wov):
                    rec[boff + k] = bones[k] & 0xFF
            out += rec
        for f in m['faces']:
            out += struct.pack('<3I', *f)
        for b in m['bones']:
            nb = b['name'].encode('utf-8') + b'\x00'
            out += U32.pack(len(nb)) + nb
            out += struct.pack('<16f', *b['matrix'])
    open(path, 'wb').write(bytes(out))
    return len(out)
