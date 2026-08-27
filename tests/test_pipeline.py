# -*- coding: utf-8 -*-
"""Pure-python pipeline test for io_scene_d3unit (mathutils shim + bpy stub)."""
import sys
import os
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
sys.path.insert(0, os.path.join(ROOT, 'blender'))

import mini_mathutils as shim
shim._install()

bpy = types.ModuleType('bpy')
props = types.ModuleType('bpy.props')
props.StringProperty = props.BoolProperty = lambda *a, **k: None
bpy.props = props
bpy.types = types.SimpleNamespace(Operator=object)
bpy.utils = types.SimpleNamespace(register_class=lambda c: None,
                                  unregister_class=lambda c: None)
sys.modules['bpy'] = bpy
sys.modules['bpy.props'] = props

import io_scene_d3unit as addon          # noqa: E402
from io_scene_d3unit import unit         # noqa: E402

Matrix = shim.Matrix
Vector = shim.Vector
ident = Matrix.Identity(4)

G = os.path.join(ROOT, 'character_empire_inquisitor.g')
u = unit.load_unit(G)
a = u['a']
bind = u['bind_world']
parent = {t['name']: t['parent'] for t in a['track_data']}
scale = {t['name']: t['scale'] for t in a['track_data']}
trackmap = {t['name']: t for t in a['track_data']}
fail = 0

# rest world locals (as the addon builds: matrix_local = C(Lb))
wbind = {}
for bn in bind:
    wbind[bn] = addon.rows_to_bl(bind[bn])


def rows_to_mat16(m16):
    return Matrix(tuple(tuple(m16[i * 4 + j] for j in range(4))
                        for i in range(4)))


ml = {}
for t in a['track_data']:
    bn = t['name']
    par = parent.get(bn, '')
    W = wbind.get(bn, ident)
    ml[bn] = wbind[par].inverted() @ W if par in wbind else W

# ---- 1. animation key -> basis -> decompose/recompose -> key roundtrip
worstq = 0.0
worstp = 0.0
for t in a['track_data']:
    sc = scale[t['name']]
    r = ml[t['name']]
    for k in t['keys']:
        full = addon.key_to_full_bl(*k, sc, r)
        basis = r.inverted() @ full
        loc, rot, _s = basis.decompose()
        basis2 = Matrix.Translation(loc) @ rot.to_matrix().to_4x4()
        k2 = addon.full_bl_to_key(r @ basis2, sc, r)
        worstp = max(worstp, max(abs(k[i] - k2[i]) for i in (4, 5, 6)))
        dot = sum(x * y for x, y in zip(k[:4], k2[:4]))
        nrm = sum(x * x for x in k[:4])
        worstq = max(worstq, abs(abs(dot / nrm) - 1.0))
print('1. key roundtrip: worst pos err %.2e, worst quat |dot|-1 %.2e'
      % (worstp, worstq))
if worstp > 1e-6 or worstq > 1e-4:
    print('   FAIL'); fail += 1

# ---- 2. pose plausibility (world z) - idle stable, feet low, head high
def chain(bn, f, memo):
    if bn in memo:
        return memo[bn]
    t = trackmap[bn]
    full = addon.key_to_full_bl(*t['keys'][f], scale[bn], ml[bn])
    par = parent.get(bn, '')
    Wp = chain(par, f, memo) if par in trackmap else ident
    W = Wp @ full
    memo[bn] = W
    return W


checks = {'Hips': (0.8, 1.15), 'Head': (0.5, 1.9), 'LeftFoot': (-0.3, 1.3),
          'RightFoot': (-0.3, 1.4), 'LeftHand': (0.3, 2.2)}
okall = True
for bn, (lo, hi) in checks.items():
    zs = [round(chain(bn, f, {}).translation.z, 3)
          for f in range(0, 301, 10)]
    ok = all(lo <= z <= hi for z in zs)
    okall = okall and ok
    print('2.', bn.ljust(9), 'z range %.2f..%.2f' % (min(zs), max(zs)),
          'OK' if ok else 'FAIL')
if not okall:
    fail += 1
hz = [chain('Head', f, {}).translation.z for f in range(0, 134)]
print('   idle head z: min %.3f max %.3f (bind 1.656)'
      % (min(hz), max(hz)))
if not all(1.5 < z < 1.8 for z in hz):
    print('   IDLE HEAD FAIL'); fail += 1

# ---- 3. .g bind inverse re-export from rest world: G' == G
worst = 0.0
for m in u['g']['meshes']:
    for b in m['bones']:
        Wrows = bind[b['name']]
        Wbl = addon.rows_to_bl(Wrows)
        Gr2 = addon.bl_to_rows(Wbl.inverted())
        worst = max(worst, max(abs(x - y) for x, y in zip(b['matrix'], Gr2)))
print('3. bind inverse re-export worst err: %.2e' % worst)
if worst > 1e-6:
    print('   FAIL'); fail += 1

# ---- 4. vertex f2b/b2f roundtrip
worst = 0.0
for v in u['g']['meshes'][0]['verts'][:500]:
    f = addon.b2f(addon.f2b(v['co']))
    worst = max(worst, max(abs(x - y) for x, y in zip(v['co'], f)))
print('4. vertex space roundtrip worst err: %.2e' % worst)
if worst > 1e-6:
    print('   FAIL'); fail += 1

# ---- 5. full mesh geometry roundtrip through g3 (edited=unchanged)
from io_scene_d3unit import g3
out = [unit.prepare_mesh_section(m, m['verts'], m['faces'])
       for m in u['g']['meshes']]
g = dict(u['g'], meshes=out)
n = g3.write_g('/tmp/rt3.g', g)
orig = open(G, 'rb').read()
new = open('/tmp/rt3.g', 'rb').read()
same = orig == new
print('5. .g re-write after parse+prepare: sizes %d/%d identical=%s'
      % (len(orig), len(new), same))
if not same:
    d0 = next(i for i in range(min(len(orig), len(new)))
              if orig[i] != new[i])
    nb = sum(1 for i in range(len(orig)) if orig[i] != new[i])
    print('   first diff @%d, total %d bytes' % (d0, nb))

print('RESULT:', 'ALL OK' if fail == 0 else '%d FAILURES' % fail)
sys.exit(1 if fail else 0)
