# -*- coding: utf-8 -*-
"""Format-level logic: assemble a "unit" from the sibling files
(.g .a .ac .scene .t) and provide conversion helpers.  Pure python,
so it can be unit-tested outside Blender.

Axis spaces
-----------
File ("engine") space for geometry:  +X right, +Y up, +Z towards viewer?
Empirically the exporter (3ds Max, Z-up RH) baked a global transform:
the mapping into Blender (Z-up RH) we use is

    bl.x =  f.x
    bl.y = -f.z
    bl.z =  f.y

a proper rotation (det = +1), verified: up axis of bind matrices maps to
+Z up, left/right (file Y) maps to Blender X...  NOTE: for bind *bone
matrices* the file space looks permuted (height in X, side in Y) - the
same rotation still maps everything consistently because we transform
whole matrices, not components individually.

Bone matrices in the .g file are *inverse bind* matrices in row-vector
convention:  v_bone = v_world @ G.  The bind world matrix is G^-1.
"""
import os

try:
    from . import g3, a3
    from . import scene as scn
except ImportError:  # standalone (tests outside Blender)
    import g3, a3
    import scene as scn


# ------------------------------------------------------------------ 4x4
# flat 16, row-major (rows r0..r3), row-vector convention: v @ M

def ident4():
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def mm(a, b):
    r = [0.0] * 16
    for i in range(4):
        for j in range(4):
            r[i * 4 + j] = (a[i * 4 + 0] * b[0 * 4 + j] +
                            a[i * 4 + 1] * b[1 * 4 + j] +
                            a[i * 4 + 2] * b[2 * 4 + j] +
                            a[i * 4 + 3] * b[3 * 4 + j])
    return r


def transl(x, y, z):
    m = ident4()
    m[12], m[13], m[14] = x, y, z
    return m


def quat_rows(qx, qy, qz, qw):
    n = (qx * qx + qy * qy + qz * qz + qw * qw) or 1.0
    qx, qy, qz, qw = qx / n, qy / n, qz / n, qw / n
    return [
        1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw),
        2 * (qx * qz + qy * qw), 0,
        2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz),
        2 * (qy * qz - qx * qw), 0,
        2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw),
        1 - 2 * (qx * qx + qy * qy), 0,
        0, 0, 0, 1,
    ]


def inv4(m):
    """Invert an affine row-vector matrix: M = [R rows; T].  M^-1 =
    [R^T ; -T R^T]  (rows)."""
    r = [[m[i * 4 + j] for j in range(3)] for i in range(3)]
    t = [m[12], m[13], m[14]]
    rt = [[r[j][i] for j in range(3)] for i in range(3)]
    tr = [-(t[0] * rt[0][i] + t[1] * rt[1][i] + t[2] * rt[2][i])
          for i in range(3)]
    return [rt[0][0], rt[0][1], rt[0][2], 0.0,
            rt[1][0], rt[1][1], rt[1][2], 0.0,
            rt[2][0], rt[2][1], rt[2][2], 0.0,
            tr[0], tr[1], tr[2], 1.0]


# ------------------------------------------------------------------ load

def find_sibling(path, ext):
    stem = os.path.splitext(path)[0]
    for e in (ext, ext.upper()):
        if os.path.exists(stem + e):
            return stem + e
    # also scan directory for  <anything>_baseanims.a style names
    d = os.path.dirname(path) or '.'
    # Some shipped assets use a suffix such as ``_baseanims.a``.  Do not
    # blindly pick the first sibling, though: directories often contain
    # several units (and the wrong animation file can make Blender fail
    # while constructing the action).  Only accept files whose stem is the
    # requested stem plus a suffix, preferring the conventional baseanims.
    base = os.path.basename(stem).lower()
    cands = [os.path.join(d, f) for f in sorted(os.listdir(d))
             if f.lower().endswith(ext.lower()) and
             os.path.splitext(f)[0].lower().startswith(base + '_')]
    cands.sort(key=lambda p: (not os.path.splitext(os.path.basename(p))[0]
                              .lower().endswith('_baseanims'), p))
    return cands[0] if cands else None


def load_unit(path):
    """path: a .g or .scene file of the unit."""
    ext = os.path.splitext(path)[1].lower()
    stem = os.path.splitext(path)[0]
    gpath = path if ext == '.g' else stem + '.g'
    if not os.path.exists(gpath):
        raise FileNotFoundError('no .g file found for ' + path)
    g = g3.read_g(gpath)
    apath = find_sibling(stem, '.a')
    a = a3.read_a(apath) if apath else None
    acpath = find_sibling(stem, '.ac')
    ac = scn.parse_ac(acpath) if acpath else {'states': []}
    scpath = path if ext == '.scene' else stem + '.scene'
    sc = scn.parse_scene(scpath) if os.path.exists(scpath) else \
        {'gobjs': [], 'bones_file': ''}
    # texture files: prefer <material>.t next to the .g
    tex = {}
    for m in g['meshes']:
        ad = dict(m['attrs'])
        name = ad.get('material0_diffuse', '')
        base = os.path.splitext(os.path.basename(name))[0]
        t = find_sibling(os.path.join(os.path.dirname(gpath), base), '.t')
        if t is None:
            t = find_sibling(stem, '.t')
        if t:
            tex[ad.get('name', base)] = t
    # ---- bone bind world matrices (file space, row-vector)
    bind = {}
    if a:
        order = [t['name'] for t in a['track_data']]
        parent = {t['name']: t['parent'] for t in a['track_data']}
        gbonemats = {}
        for m in g['meshes']:
            for b in m['bones']:
                if b['name'] not in gbonemats:
                    gbonemats[b['name']] = inv4(b['matrix'])
        # A valid unit may contain animation tracks but no renderable mesh
        # (for example an animation-only asset).  Do not dereference mesh 0
        # while building its fallback root bind transform.
        root_pos = (transl(*g['meshes'][0]['header_floats'][0:3])
                    if g['meshes'] else ident4())
        for n in order:
            if n in gbonemats:
                bind[n] = gbonemats[n]
            elif parent.get(n, '') in ('Scene Root', ''):
                bind[n] = root_pos
            else:
                bind[n] = bind.get(parent[n], ident4())
    return {'g': g, 'a': a, 'ac': ac, 'scene': sc,
            'bind_world': bind, 'gpath': gpath, 'apath': apath,
            'acpath': acpath, 'scpath': scpath if os.path.exists(scpath)
            else None, 'tex': tex}


def prepare_mesh_section(src, verts, faces):
    """Build a mesh dict for g3.write_g from edited geometry.

    verts: list of dicts {'co','no','uv','color','bones','weights'}
           (already in FILE space, v is 0..1 engine UV, not flipped)
    Recalculates header floats (bbox) and attr counts.
    """
    wov = src['wov']
    # if geometry is untouched, keep the original header floats verbatim
    # (byte-exact re-export; the engine's own bbox/radius rounding is not
    # reproducible from float32 vertex data alone)
    same = (len(verts) == len(src['verts']) and
            all(v['co'] == o['co'] for v, o in zip(verts, src['verts'])))
    if same:
        attrs = list(src['attrs'])
        return {'u_prefix': src['u_prefix'], 'shadow_nv': src['shadow_nv'],
                'header_floats': tuple(src['header_floats']),
                'attrs': attrs, 'wov': wov,
                'verts': verts, 'faces': faces, 'bones': src['bones']}
    xs = [v['co'][0] for v in verts]
    ys = [v['co'][1] for v in verts]
    zs = [v['co'][2] for v in verts]
    hf = list(src['header_floats'])
    hf[6:9] = (max(xs), max(ys), max(zs))
    hf[9:12] = (min(xs), min(ys), min(zs))
    hf[12] = max((x * x + y * y + z * z) ** 0.5 for x, y, z in
                 zip(xs, ys, zs))
    attrs = list(src['attrs'])

    def set_attr(key, val):
        for i, (k, v) in enumerate(attrs):
            if k == key:
                attrs[i] = (k, str(val))
                return
        attrs.append((key, str(val)))
    set_attr('material0_triangles_num', len(faces))
    set_attr('vertexs_weights_num', len(verts))
    set_attr('bones_num', len(src['bones']))
    return {'u_prefix': src['u_prefix'], 'shadow_nv': src['shadow_nv'],
            'header_floats': tuple(hf), 'attrs': attrs, 'wov': wov,
            'verts': verts, 'faces': faces, 'bones': src['bones']}
