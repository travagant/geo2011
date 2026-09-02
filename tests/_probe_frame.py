#!/usr/bin/env python3
"""Probe v2: column-major correct math, self-verifying render identity."""
import json, struct, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from d3tool import gfile
from d3tool import anim as A

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def mm(a, b):
    return [[sum(a[r][k]*b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]

def mvec(m, v):
    return [sum(m[r][c]*v[c] for c in range(4)) for r in range(4)]

def inv4(m):
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(4)] for i, row in enumerate(m)]
    for c in range(4):
        p = max(range(c, 4), key=lambda r: abs(a[r][c])); a[c], a[p] = a[p], a[c]
        pv = a[c][c]; a[c] = [x/pv for x in a[c]]
        for r in range(4):
            if r != c and a[r][c] != 0:
                f = a[r][c]; a[r] = [x-f*y for x, y in zip(a[r], a[c])]
    return [row[4:] for row in a]

def qmat(q):
    x, y, z, w = q
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]

def cmaj16(f):  # glTF MAT4 floats -> row-major math matrix
    return [[f[c*4+r] for c in range(4)] for r in range(4)]

def local_from_sample(s):
    R = qmat(s[:4])
    L = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    for r in range(3):
        for c in range(3):
            L[r][c] = R[r][c]
        L[r][3] = s[4+r]
    return L

def quat_norm(q):
    n = math.sqrt(sum(x*x for x in q)) or 1.0
    return tuple(x/n for x in q)

# ---------------- blender ----------------
jb = json.load(open(os.path.join(REPO, 'Blender/character_empire_angel.gltf')))
db = open(os.path.join(REPO, 'Blender/character_empire_angel.bin'), 'rb').read()
NC = {'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}
FMT = {5126:'f',5123:'H',5121:'B',5125:'I'}
def raw(j, d, ai):
    a = j['accessors'][ai]; bv = j['bufferViews'][a['bufferView']]
    off = bv.get('byteOffset',0)+a.get('byteOffset',0)
    return struct.unpack_from('<'+FMT[a['componentType']]*(a['count']*NC[a['type']]), d, off)

bpar = {}
for i, n in enumerate(jb['nodes']):
    for c in n.get('children', []):
        bpar[c] = i
names_b = [n.get('name','') for n in jb['nodes']]

an = jb['animations'][0]
chan = {}
for ch in an['channels']:
    nd = ch['target']['node']; path = ch['target']['path']
    s = an['samplers'][ch['sampler']]
    chan.setdefault(nd, {})[path] = (list(raw(jb, db, s['input'])), list(raw(jb, db, s['output'])))

def sample_chan(node, path, t):
    if node not in chan or path not in chan[node]:
        return None
    times, vals = chan[node][path]
    n = len(times)
    lo, hi = 0, n-1
    while lo < hi-1:
        mid = (lo+hi)//2
        if times[mid] <= t: lo = mid
        else: hi = mid
    if path == 'rotation':
        nc = 4
        if n == 1: return vals[:4]
        q0 = vals[lo*4:lo*4+4]; q1 = vals[hi*4:hi*4+4]
        d = sum(a*b for a, b in zip(q0, q1))
        if d < 0: q1 = [-x for x in q1]; d = -d
        if d > 0.9995 or t <= times[0]:
            return q0 if t <= times[0] else q1 if t >= times[-1] else [a+((t-t0)/(times[hi]-t0))*(b-a) for a,b,t0 in ()] if False else q0
        t0, t1 = times[lo], times[hi]
        f = (t-t0)/(t1-t0)
        th = math.acos(max(-1.0, min(1.0, d)))
        s2 = math.sin(th)
        w0, w1 = math.sin((1-f)*th)/s2, math.sin(f*th)/s2
        return [a*w0+b*w1 for a, b in zip(q0, q1)]
    else:
        nc = 3
        if n == 1: return vals[:3]
        t0, t1 = times[lo], times[hi]
        if t1 <= t0: return vals[lo*3:lo*3+3]
        f = (t-t0)/(t1-t0)
        return [vals[lo*3+i]+f*(vals[hi*3+i]-vals[lo*3+i]) for i in range(3)]

def local_matrix_b(node, t):
    nd = jb['nodes'][node]
    rot = sample_chan(node, 'rotation', t) or nd.get('rotation', [0,0,0,1])
    tra = sample_chan(node, 'translation', t) or nd.get('translation', [0,0,0])
    sc = sample_chan(node, 'scale', t) or nd.get('scale', [1,1,1])
    R = qmat(quat_norm(rot))
    L = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    for r in range(3):
        for c in range(3):
            L[r][c] = R[r][c]*sc[c]
        L[r][3] = tra[r]
    return L

def global_b(node, t):
    chain = []
    while node is not None:
        chain.append(node)
        node = bpar.get(node)
    M = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    for nd in reversed(chain):
        M = mm(M, local_matrix_b(nd, t))
    return M

# ---------------- gm donor ----------------
don = gfile.parse_geometry_file(open(os.path.join(REPO, 'Empire/Angel/character_empire_angel.g'), 'rb').read())
part = next(p for p in don.parts if p.name == 'empire_angel')
IBMg = {b.name: cmaj16(list(b.matrix)) for b in part.bones}
idle = A.parse_anim(open(os.path.join(REPO, 'Empire/Angel/character_empire_angel_idle.a'), 'rb').read())
arec = {b.name: b for b in idle.bones}
aparent = {b.name: (b.parent or '') for b in idle.bones}

def global_o(name, frame):
    if name not in arec:
        return None
    p = aparent.get(name)
    base = global_o(p, frame) if (p and p in arec) else [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    rec = arec[name]
    s = rec.frames[frame] if frame < len(rec.frames) else rec.rest
    return mm(base, local_from_sample(s))

nodeidx_b = {}
for i, nm in enumerate(names_b):
    if nm and nm not in nodeidx_b:
        nodeidx_b[nm] = i

FPS_O = 30.0
def Q_at(t):
    frame = int(round(t*FPS_O))
    out = {}
    for nm, nd in nodeidx_b.items():
        if nm not in arec:
            continue
        Go = global_o(nm, frame)
        if Go is None: continue
        out[nm] = mm(inv4(Go), global_b(nd, t))
    return out

# ---------- SELF-TEST: render identity on the ORIGINAL import ----------
jo = json.load(open('/tmp/orig_imp/character_empire_angel.gltf'))
d_obin = open('/tmp/orig_imp/character_empire_angel.bin', 'rb').read()
sk = jo['skins'][2]
ro = raw(jo, d_obin, sk['inverseBindMatrices'])
jn_o = [jo['nodes'][jj]['name'] for jj in sk['joints']]
par_o = {}
for i, n in enumerate(jo['nodes']):
    for c in n.get('children', []):
        par_o[c] = i
def global_o_gltf(node, frame):
    chain = []
    while node is not None:
        chain.append(node)
        node = par_o.get(node)
    M = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    for nd in reversed(chain):
        n = jo['nodes'][nd]
        # static TRS of the original == .a rest
        R = qmat(n.get('rotation', [0,0,0,1])); tra = n.get('translation', [0,0,0]); scc = n.get('scale', [1,1,1])
        L = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
        for r in range(3):
            for c in range(3):
                L[r][c] = R[r][c]*scc[c]
            L[r][3] = tra[r]
        M = mm(M, L)
    return M

vo = raw(jo, d_obin, jo['meshes'][2]['primitives'][0]['attributes']['POSITION'])
wo = raw(jo, d_obin, jo['meshes'][2]['primitives'][0]['attributes']['WEIGHTS_0'])
ko = raw(jo, d_obin, jo['meshes'][2]['primitives'][0]['attributes']['JOINTS_0'])
# donor .g verts == gltf verts? verify quickly then render a few via gltf nodes+IBM
cnt_eq = sum(1 for i in range(0, 400) if part.vertices[i].position == vo[i*3:i*3+3])
print("donor verts == gltf verts (first400):", cnt_eq)
# rendered point of donor vertex i (head region: pick vertex with max rendered y among sample)
def render_o(i):
    p = None
    for k4 in range(4):
        w = wo[i*4+k4]
        if w <= 1e-6: continue
        ji = int(ko[i*4+k4])
        if ji >= len(jn_o): continue
        nm = jn_o[ji]
        G = global_o_gltf(jo['skins'][2]['joints'][ji], 0)
        IBM = cmaj16(list(ro[16*ji:16*ji+16]))
        pv = mvec(mm(G, IBM), list(vo[i*3:i*3+3])+[1.0])
        p = [a+w*b for a, b in zip(p or [0,0,0,0], pv)] if p else [w*x for x in pv]
    return p[:3] if p else None
tops = []
for i in range(0, len(vo)//3, 97):
    r = render_o(i)
    if r: tops.append((r[1], i, r))
tops.sort(reverse=True)
print("original: top rendered verts (world):", [(round(y,3), i) for y, i, _ in tops[:3]])
print("            their render pos:", [tuple(round(x,3) for x in r) for _,_,r in tops[:3]])

# ---------- Q and the vertex map ----------
Q0 = Q_at(0.0)
Q1 = Q_at(2.0)
devs = sorted((max(abs(Q0[nm][r][c]-Q1[nm][r][c]) for r in range(4) for c in range(4)), nm) for nm in Q0 if nm in Q1)
print("Q constancy worst:", [(n, round(d,6)) for d, n in devs[-4:]])

skb = jb['skins'][0]
rb = raw(jb, db, skb['inverseBindMatrices'])
jn_bl = [jb['nodes'][jj]['name'] for jj in skb['joints']]
IBMb = {}
for k, nm in enumerate(jn_bl):
    if nm not in IBMb:
        IBMb[nm] = cmaj16(list(rb[16*k:16*k+16]))

pb = jb['meshes'][0]['primitives'][0]
nv = jb['accessors'][pb['attributes']['POSITION']]['count']
vb = raw(jb, db, pb['attributes']['POSITION'])
wb = raw(jb, db, pb['attributes']['WEIGHTS_0'])
kb = raw(jb, db, pb['attributes']['JOINTS_0'])

# rendered world position of blender verts (t=0) for cross-check
def render_b(i, t=0.0):
    p = None
    for k4 in range(4):
        w = wb[i*4+k4]
        if w <= 1e-6: continue
        nm = jn_bl[int(kb[i*4+k4])]
        G = global_b(nodeidx_b[nm], t)
        pv = mvec(mm(G, IBMb[nm]), list(vb[i*3:i*3+3])+[1.0])
        p = [a+w*b for a, b in zip(p or [0,0,0,0], pv)] if p else [w*x for x in pv]
    return p[:3] if p else None
ib_top = max(range(nv), key=lambda i: vb[i*3+1])
print("blender head-top render:", tuple(round(x,3) for x in render_b(ib_top)))

vo_verts = [(v.position[0], v.position[1], v.position[2]) for v in part.vertices]
G = {}
for p in vo_verts:
    G.setdefault((round(p[0]*100), round(p[1]*100), round(p[2]*100)), []).append(p)
def nearest(pq, cells=3):
    best = 1e9
    gx, gy, gz = round(pq[0]*100), round(pq[1]*100), round(pq[2]*100)
    for dx in range(-cells, cells+1):
        for dy in range(-cells, cells+1):
            for dz in range(-cells, cells+1):
                for q in G.get((gx+dx, gy+dy, gz+dz), ()):
                    d = math.dist(pq, q)
                    if d < best: best = d
    return best

Kc = {}
dists = []
for i in range(0, nv, 23):
    v = list(vb[i*3:i*3+3]) + [1.0]
    acc = [0.0]*4
    ok = True
    for k4 in range(4):
        w = wb[i*4+k4]
        if w <= 1e-6: continue
        nm = jn_bl[int(kb[i*4+k4])]
        if nm not in IBMg or nm not in IBMb or nm not in Q0:
            ok = False; break
        if nm not in Kc:
            Kc[nm] = mm(inv4(IBMg[nm]), mm(Q0[nm], IBMb[nm]))
        acc = [a + w*b for a, b in zip(acc, mvec(Kc[nm], v))]
    if not ok: continue
    dists.append(nearest(acc[:3]))
dists.sort()
print(f"v_gm map: sampled {len(dists)} median={dists[len(dists)//2]:.6f} <0.001:{sum(1 for d in dists if d<0.001)} <0.02:{sum(1 for d in dists if d<0.02)}")
