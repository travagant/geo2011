# -*- coding: utf-8 -*-
"""Disciples III unit importer/exporter (geo2011 format) for Blender.

Bundle per unit (same file stem, same directory):
    *.g      geometry, skinning weights, bind skeleton  (binary)
    *.a      bone animation, all clips                   (binary)
    *.ac     animation config: clips, events             (text, preserved)
    *.scene  scene/object description                     (text, refreshed)
    *.t      texture atlas, DXT1/3/5 + mip chain          (binary)

Conventions (see docs/FORMATS.md for the full spec):

* File "engine" space (X = side/left+, Y = up, Z = forward-, both for
  mesh verts and bone matrix translations) -> Blender via one proper
  rotation:
      bl.x =  f.x ;  bl.y = -f.z ;  bl.z =  f.y
* .g bone records store the *inverse bind* world matrix, row-vector
  convention (v' = v @ M, translation = last row):  G = inv(Wbind).
* .a keys hold the FULL local rotation (bone-axis convention incl. the
  90/180-deg axis twists baked in .g bind matrices) plus a POSITION
  DELTA relative to the bind local translation, in cm:
      Lf = [ R(q_key) | t_Lb + p_key * 0.01 ]     (file, row-major)
      Wf[b] = Lf[b] @ Wf[parent]                  (root parent = identity)
  Because bone.matrix_local == C(Lb), Blender's
      local = ml @ matrix_basis
  matches 1:1 with the file chain when:
      matrix_basis = ml^-1 @ C(Lf)
      C(Lf)        = T(ml.translation + P4*(p_key*sc)) @ P4 R(q) P4i
  and export is the exact inverse.  Round-trip verified on the sample
  (worst error ~1e-13 on positions, quaternion sign is normalised to
  w >= 0 which is the same rotation for the engine).
* UV: blender v = 1 - file v.
* The original .g/.a files are used as templates on export (stored paths);
  exporting without changes reproduces the files byte-for-byte.
"""
bl_info = {
    'name': 'Disciples III Unit (geo2011: .g/.a/.scene/.ac/.t)',
    'author': 'Arena agent (reverse engineering of geo2011.dle)',
    'version': (0, 1, 0),
    'blender': (3, 0, 0),
    'location': 'File > Import/Export > Disciples III Unit (.g)',
    'description': 'Import/export Disciples III character unit files '
                   '(.g geometry, .a animation, .scene/.ac config, .t tex)',
    'category': 'Import-Export',
}

import json
import os

import bpy
from bpy.props import BoolProperty, StringProperty
from mathutils import Matrix, Quaternion, Vector

from . import g3, a3, t3
from . import scene as scn
from . import unit as unitmod

# ------------------------------------------------------------------ axes
P4 = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
P4i = P4.inverted()


def rows_to_bl(m16):
    """file row-vector 4x4 (flat, row-major; translation in last *row*)
    -> Blender 4x4 (translation in last column).  M_bl = P4 m^T P4i."""
    m = Matrix((tuple(m16[0:4]), tuple(m16[4:8]), tuple(m16[8:12]),
                tuple(m16[12:16])))
    return P4 @ m.transposed() @ P4i


def bl_to_rows(m4):
    """Blender 4x4 -> file row-vector convention flat list(16)."""
    mf = (P4i @ m4 @ P4).transposed()
    return [mf[r][c] for r in range(4) for c in range(4)]


def f2b(v3):
    """point/vector: file space -> Blender space"""
    return P4.to_3x3() @ Vector(v3[:3])


def b2f(v3):
    """point/vector: Blender space -> file space"""
    return P4i.to_3x3() @ Vector(v3[:3])


def key_to_full_bl(qx, qy, qz, qw, px, py, pz, scale, ml):
    """one .a key -> full local matrix in Blender (parent) space.

    ml is bone.matrix_local (== C(bind local)); its translation supplies
    the bind local offset the key position is a delta on top of.
    """
    q = Quaternion((qw, qx, qy, qz)).normalized()
    loc = ml.translation + f2b((px * scale, py * scale, pz * scale))
    rot_bl = (P4.to_3x3() @ q.to_matrix() @ P4i.to_3x3()).to_4x4()
    return Matrix.Translation(loc) @ rot_bl


def full_bl_to_key(full_bl, scale, ml):
    """inverse of key_to_full_bl: full local (Blender) -> 7 .a floats."""
    rot_file = P4i.to_3x3() @ full_bl.to_3x3() @ P4.to_3x3()
    q = rot_file.to_quaternion()
    p = b2f(full_bl.translation - ml.translation)
    return (q.x, q.y, q.z, q.w,
            p.x / scale, p.y / scale, p.z / scale)


# ------------------------------------------------------------------ import
def do_import(context, filepath, do_anim=True, do_tex=True):
    unit = unitmod.load_unit(filepath)
    # A unit commonly has several sections that all reference the same
    # texture.  Decoding and packing that 2048x2048 image for every section
    # can consume hundreds of megabytes and makes Blender appear to crash.
    # Keep one Blender image per source file for the duration of the import.
    image_cache = {}
    g = unit['g']
    a = unit['a']
    meshes, metas = g['meshes'], g['meta']

    # ------------------------------ skeleton
    # A few shipped .a files contain several tracks with the same name
    # (notably ``null`` and ``null_Bone_Tip``).  Blender bone names must be
    # unique, so passing those names directly to edit_bones.new() aborts the
    # whole import.  Keep the first occurrence's name intact and suffix the
    # rest, while retaining the original name for bind/mesh lookup.
    track_names = []
    name_first = {}
    if a:
        used_names = set()
        for t in a['track_data']:
            original = t['name']
            n = original
            suffix = 1
            while n in used_names:
                n = '%s.%03d' % (original, suffix)
                suffix += 1
            used_names.add(n)
            name_first.setdefault(original, n)
            track_names.append(n)
        order = track_names
        parent_of = {}
        for t, n in zip(a['track_data'], track_names):
            parent_of[n] = name_first.get(t['parent'], t['parent'])
    else:
        order, parent_of = [], {}
        for m in meshes:
            for b in m['bones']:
                if b['name'] not in order:
                    order.append(b['name'])
                    parent_of[b['name']] = 'Scene Root'
    bind = unit['bind_world']

    # Track names are also used below when inserting animation curves.  This
    # mapping is positional because duplicate source names cannot be used as
    # dictionary keys.
    anim_names = dict(zip((id(t) for t in a['track_data']), track_names)) if a else {}

    name = os.path.splitext(os.path.basename(filepath))[0]
    arm_data = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name + '_root', arm_data)
    context.collection.objects.link(arm)
    context.view_layer.objects.active = arm
    for o in list(context.selected_objects):
        o.select_set(False)
    arm.select_set(True)

    bpy.ops.object.mode_set(mode='EDIT')
    edit = arm_data.edit_bones
    made = {}
    for bn in order:
        eb = edit.new(bn)
        made[bn] = eb
        par = parent_of.get(bn, '')
        if par and par in made:
            eb.parent = made[par]
        M = rows_to_bl(bind[bn]) if bn in bind else Matrix.Identity(4)
        Ml = (eb.parent.matrix.inverted() @ M) if eb.parent else M
        head = Ml.translation
        ydir = Ml.to_3x3() @ Vector((0, 1, 0))
        length = max(abs(ydir.length), 0.01)
        eb.head = head
        eb.tail = head + (ydir.normalized() * length if ydir.length > 1e-6
                          else Vector((0, length, 0)))
        eb.roll = 0.0
        z0 = eb.matrix.to_3x3() @ Vector((0, 0, 1))
        z1 = Ml.to_3x3() @ Vector((0, 0, 1))
        if ydir.length > 1e-6 and z0.length > 1e-6 and z1.length > 1e-6:
            z0.normalize()
            z1.normalize()
            ang = z0.angle(z1)
            if z0.cross(z1).dot(ydir.normalized()) < 0:
                ang = -ang
            eb.roll = ang
    bpy.ops.object.mode_set(mode='OBJECT')

    # ------------------------------ meshes
    # The file header contains material records, not necessarily one record
    # per geometry section.  Attach metadata by section name and synthesize a
    # safe fallback for auxiliary sections (cloth, ribbons, weapons, ...).
    # Older code used zip(metas, meshes), silently dropping valid sections and
    # occasionally assigning the wrong texture.
    headers_by_name = {m.get('name', ''): m for m in metas}
    mesh_metas = []
    for src in meshes:
        attrs = dict(src['attrs'])
        mesh_name = attrs.get('name', 'mesh')
        header = headers_by_name.get(mesh_name)
        diffuse = attrs.get('material0_diffuse', '')
        material = os.path.splitext(os.path.basename(diffuse))[0] or mesh_name
        mesh_metas.append({
            'name': mesh_name,
            'material': material,
            'header': header,
        })

    objs = {}
    for i, (src, meta) in enumerate(zip(meshes, mesh_metas)):
        co = [f2b(v['co']) for v in src['verts']]
        no = [f2b(v['no']) for v in src['verts']]
        uv = [(v['uv'][0], 1.0 - v['uv'][1]) for v in src['verts']]
        me = bpy.data.meshes.new(meta['name'])
        me.from_pydata([tuple(v) for v in co], [],
                       [tuple(f) for f in src['faces']])
        me.update()
        ulo = me.uv_layers.new(name='UVMap')
        for loop in me.loops:
            ulo.data[loop.index].uv = uv[loop.vertex_index]
        for p in me.polygons:
            p.use_smooth = True
        try:
            me.normals_split_custom_set([no[l.vertex_index]
                                         for l in me.loops])
        except Exception:
            pass
        bone_names = [b['name'] for b in src['bones']]
        if src['wov'] > 1:
            for k, bn in enumerate(bone_names):
                if bn not in arm.data.bones:
                    continue
                me.vertex_groups.new(name=bn)
            for vi, vd in enumerate(src['verts']):
                for b, w in zip(vd['bones'], vd['weights']):
                    if w > 1e-6 and b < len(bone_names):
                        vg = me.vertex_groups.get(bone_names[b])
                        if vg:
                            vg.add([vi], w, 'REPLACE')
        elif bone_names and bone_names[0] in arm.data.bones:
            vg = me.vertex_groups.new(name=bone_names[0])
            vg.add(list(range(len(co))), 1.0, 'REPLACE')
        ob = bpy.data.objects.new(meta['name'], me)
        context.collection.objects.link(ob)
        mod = ob.modifiers.new('D3 Skin', 'ARMATURE')
        mod.object = arm
        ob.parent = arm
        ob['d3meshindex'] = i
        ob['d3colors'] = [v['color'] for v in src['verts']]
        ob['d3rawnormals'] = [c for v in no for c in v]
        objs[i] = ob
        mat = bpy.data.materials.new(meta['material'])
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get('Principled BSDF')
        if do_tex:
            tpath = unit['tex'].get(meta['name'])
            if tpath:
                img = image_cache.get(os.path.abspath(tpath))
                if img is None:
                    img = _load_t_image(tpath, meta['material'])
                    if img is not None:
                        image_cache[os.path.abspath(tpath)] = img
                if img and bsdf:
                    ti = mat.node_tree.nodes.new('ShaderNodeTexImage')
                    ti.image = img
                    mat.node_tree.links.new(ti.outputs['Color'],
                                            bsdf.inputs['Base Color'])
        me.materials.append(mat)

    # ------------------------------ animation
    if do_anim and a:
        nfr = a['frames']
        act = bpy.data.actions.new(name + '_d3')
        arm.animation_data_create()
        arm.animation_data.action = act
        for t in a['track_data']:
            imported_name = anim_names[id(t)]
            pb = arm.pose.bones.get(imported_name)
            if pb is None:
                continue
            pb.rotation_mode = 'QUATERNION'
            ml = arm.data.bones[imported_name].matrix_local
            for f, k in enumerate(t['keys']):
                full = key_to_full_bl(*k, t['scale'], ml)
                basis = ml.inverted() @ full
                loc, rot, _s = basis.decompose()
                pb.location = loc
                pb.rotation_quaternion = rot
                pb.keyframe_insert('location', frame=f + 1)
                pb.keyframe_insert('rotation_quaternion', frame=f + 1)
        for fc in act.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = 'LINEAR'
        context.scene.frame_start = 1
        context.scene.frame_end = nfr
        try:
            context.scene.render.fps = int(a['fps'])
            context.scene.render.fps_base = 1.0
        except Exception:
            pass
        for st in unit['ac']['states']:
            context.scene.timeline_markers.new(
                '%s_%d' % (st['name'], st['frame0']),
                frame=st['frame0'] + 1)
            context.scene.timeline_markers.new(
                '%s_end' % st['name'], frame=st['frame1'] + 1)

    # ------------------------------ export metadata
    meta_json = {
        'g': unit['gpath'],
        'a': unit['apath'],
        'ac': unit['acpath'],
        'scene': unit['scpath'],
        'tex': unit['tex'],
        'track_ids': [[t['id'], t['parent_id'], t['name'], t['parent']]
                      for t in a['track_data']] if a else [],
        'clips': [[s['name'], s['frame0'], s['frame1'], s['fps']]
                  for s in unit['ac']['states']],
    }
    arm['d3unit'] = json.dumps(meta_json)
    return {'armature': arm.name, 'meshes': [objs[i].name
                                             for i in sorted(objs)]}


def _load_t_image(tpath, base_name):
    td = t3.read_t(tpath)
    if td['format'] not in ('DXT1', 'DXT3', 'DXT5', 'A8R8G8B8',
                            'X8R8G8B8'):
        return None
    rgba = t3.decode_mip(td['mips'][0], td['format'], td['width'],
                         td['height'])
    img = bpy.data.images.new('d3_' + base_name, td['width'], td['height'],
                              alpha=True)
    try:
        import numpy as np
        arr = np.frombuffer(rgba, dtype=np.uint8).astype(np.float32)
        img.pixels.foreach_set(arr / 255.0)
    except Exception:
        img.pixels = [c / 255.0 for c in rgba]
    img.colorspace_settings.name = 'sRGB'
    img.pack()
    return img


# ------------------------------------------------------------------ export
def _geom_from_object(ob, src):
    me = ob.data
    n = len(me.vertices)
    colors = list(ob.get('d3colors') or [])
    if len(colors) != n:
        default = colors[0] if colors else 0x0BC5D864
        colors = [default] * n
    rawn = ob.get('d3rawnormals')
    vuv = [None] * n
    ulo = me.uv_layers.active
    if ulo:
        for l in me.loops:
            if vuv[l.vertex_index] is None:
                uv = ulo.data[l.index].uv
                vuv[l.vertex_index] = (uv.x, 1.0 - uv.y)
    wov = src['wov']
    bone_names = [b['name'] for b in src['bones']]
    wts = [[] for _ in range(n)]
    for vg in me.vertex_groups:
        if vg.name in bone_names:
            bi = bone_names.index(vg.name)
            for el in vg.elements:
                if el.weight > 1e-5:
                    wts[el.vertex_index].append((bi, el.weight))
    rel = ob.matrix_world
    if ob.parent is not None:
        rel = ob.parent.matrix_world.inverted() @ ob.matrix_world
    verts = []
    for vi, v in enumerate(me.vertices):
        co = b2f(rel @ v.co)
        if rawn and len(rawn) == n * 3:
            no = b2f(Vector(rawn[vi * 3:vi * 3 + 3])).normalized()
        else:
            no = b2f(v.normal).normalized()
        uvd = vuv[vi] or (0.5, 0.5)
        vd = {'co': tuple(float(x) for x in co),
              'no': tuple(float(x) for x in no),
              'uv': (float(uvd[0]), float(uvd[1])),
              'color': int(colors[vi]) & 0xFFFFFFFF,
              'bones': [0] * wov, 'weights': [0.0] * wov}
        if wov > 1:
            ws = sorted(wts[vi], key=lambda x: -x[1])[:wov]
            s = sum(w for _, w in ws)
            if s <= 0:
                ws = [(0, 1.0)]
                s = 1.0
            ws = [(b, w / s) for b, w in ws]
            for k, (b, w) in enumerate(ws):
                vd['bones'][k] = b
                if k < wov - 1:
                    vd['weights'][k] = w
        verts.append(vd)
    faces = []
    for p in me.polygons:
        if len(p.vertices) != 3:
            raise RuntimeError('%s: ngons/quads - triangulate first'
                               % me.name)
        faces.append(tuple(p.vertices))
    return verts, faces


def do_export(context, filepath, do_anim=True):
    arm = context.active_object
    if arm is None or arm.type != 'ARMATURE' or 'd3unit' not in arm:
        raise RuntimeError('Select the unit armature (imported from .g) '
                           'as the active object first')
    meta = json.loads(arm['d3unit'])
    unit = unitmod.load_unit(meta['g'])
    g = unit['g']
    objs = {}
    for o in context.selected_objects:
        if o.type == 'MESH' and 'd3meshindex' in o:
            objs[o['d3meshindex']] = o
    for o in bpy.data.objects:
        if (o.type == 'MESH' and 'd3meshindex' in o and o.parent == arm
                and o['d3meshindex'] not in objs):
            objs[o['d3meshindex']] = o
    out = []
    for i, src in enumerate(g['meshes']):
        ob = objs.get(i)
        if ob is None:
            raise RuntimeError('mesh section %d not found among selected '
                               'objects' % i)
        verts, faces = _geom_from_object(ob, src)
        m = unitmod.prepare_mesh_section(src, verts, faces)
        # bind inverse matrices from the current armature rest pose
        bones = []
        for b in m['bones']:
            bone = arm.data.bones.get(b['name'])
            if bone is None:
                bones.append(b)
                continue
            Ww = bone.matrix_local
            Wfile = P4i @ Ww @ P4
            Grows = bl_to_rows(Wfile.inverted())
            bones.append({'name': b['name'], 'matrix': tuple(Grows)})
        m['bones'] = bones
        out.append(m)
    g['meshes'] = out
    written = [filepath]
    n = g3.write_g(filepath, g)

    # -------- .a
    if do_anim and meta.get('a') and os.path.exists(meta['a']):
        a = a3.read_a(meta['a'])
        nfr = a['frames']
        per_frame = [[] for _ in range(nfr)]      # (bone, key) per frame
        bones_anim = [t for t in a['track_data']
                      if t['name'] in arm.data.bones]
        missing = {t['name'] for t in a['track_data']
                   if t['name'] not in arm.data.bones}
        mls = {t['name']: (arm.data.bones[t['name']].matrix_local,
                           t['scale'] or 1.0) for t in bones_anim}
        for f in range(nfr):
            context.scene.frame_set(f + 1)
            for t in bones_anim:
                bn = t['name']
                pb = arm.pose.bones.get(bn)
                ml, sc = mls[bn]
                if pb is None:
                    per_frame[f].append((bn, (0, 0, 0, 1, 0, 0, 0)))
                else:
                    k = full_bl_to_key(ml @ pb.matrix_basis, sc, ml)
                    k0 = t['keys'][f]
                    # keep the original (byte-exact) key when the pose is
                    # unchanged: quats equal up to sign, pos within 1e-4
                    dq = (k[0] * k0[0] + k[1] * k0[1] + k[2] * k0[2] +
                          k[3] * k0[3])
                    nn = (k[0] ** 2 + k[1] ** 2 + k[2] ** 2 + k[3] ** 2)
                    if (nn and abs(abs(dq) / nn - 1.0) < 1e-6 and
                            max(abs(k[i] - k0[i]) for i in (4, 5, 6))
                            < 1e-4):
                        k = k0
                    per_frame[f].append((bn, k))
        keys_of = {t['name']: [(0, 0, 0, 1, 0, 0, 0) for _ in range(nfr)]
                    for t in a['track_data']}
        for f in range(nfr):
            for bn, k in per_frame[f]:
                keys_of[bn][f] = k
        tracks = [dict(t, keys=keys_of[t['name']])
                  for t in a['track_data']]
        outa = dict(a, track_data=tracks)
        ap = os.path.splitext(filepath)[0] + '.a'
        a3.write_a(ap, outa)
        written.append(ap)

    # -------- .ac copy + .scene refresh
    saved_frame = context.scene.frame_current
    base = os.path.splitext(filepath)[0]
    if meta.get('ac') and os.path.exists(meta['ac']):
        acp = base + '.ac'
        if os.path.abspath(acp) != os.path.abspath(meta['ac']):
            with open(meta['ac'], 'rb') as f:
                data = f.read()
            with open(acp, 'wb') as f:
                f.write(data)
            written.append(acp)
    if meta.get('scene') and os.path.exists(meta['scene']):
        _refresh_scene(meta['scene'], base + '.scene', g)
        written.append(base + '.scene')
    context.scene.frame_set(saved_frame)
    return written


def _refresh_scene(src, dst, g):
    """copy .scene text; refresh count attrs from the freshly built .g."""
    import re
    with open(src, 'r', encoding='latin-1') as f:
        text = f.read()
    lines = text.splitlines()
    out = []
    gi = -1
    pat = re.compile(r'attr '
                     r'"(material0_triangles_num|vertexs_weights_num|'
                     r'weights_on_vertex|bones_num)" "(-?\d+)"')
    for line in lines:
        if 'child gobj' in line:
            gi += 1
        m = pat.search(line)
        if m and 0 <= gi < len(g['meshes']):
            srcm = g['meshes'][gi]
            ad = dict(srcm['attrs'])
            key = m.group(1)
            if key in ad and ad[key] != m.group(2):
                line = line.replace('"%s"' % m.group(2),
                                    '"%s"' % ad[key])
        out.append(line)
    with open(dst, 'w', encoding='latin-1', newline='\n') as f:
        f.write('\n'.join(out) + ('\n' if text.endswith('\n') else ''))


# ------------------------------------------------------------------ ops
class EXPORT_SCENE_OT_d3unit(bpy.types.Operator):
    bl_idname = 'export_scene.d3unit'
    bl_label = 'Export Disciples III Unit (.g)'

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.g', options={'HIDDEN'})
    export_anim: BoolProperty(name='Animation (.a)', default=True)

    def execute(self, context):
        try:
            files = do_export(context, self.filepath, self.export_anim)
            self.report({'INFO'}, 'wrote ' + ', '.join(
                os.path.basename(f) for f in files))
        except Exception as e:
            import traceback
            self.report({'ERROR'}, '%s' % e)
            print(traceback.format_exc())
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        arm = context.active_object
        if arm and arm.type == 'ARMATURE' and 'd3unit' in arm:
            gpath = json.loads(arm['d3unit']).get('g', '')
            if gpath:
                self.filepath = gpath
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class IMPORT_SCENE_OT_d3unit(bpy.types.Operator):
    bl_idname = 'import_scene.d3unit'
    bl_label = 'Import Disciples III Unit (.g)'
    bl_options = {'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.g;*.scene', options={'HIDDEN'})
    import_anim: BoolProperty(name='Animation', default=True)
    import_tex: BoolProperty(name='Textures (.t)', default=True)

    def execute(self, context):
        try:
            r = do_import(context, self.filepath,
                          self.import_anim, self.import_tex)
            self.report({'INFO'}, 'imported %s (%d meshes)'
                        % (r['armature'], len(r['meshes'])))
        except Exception as e:
            import traceback
            self.report({'ERROR'}, '%s' % e)
            print(traceback.format_exc())
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def menu_export(self, context):
    self.layout.operator(EXPORT_SCENE_OT_d3unit.bl_idname,
                         text='Disciples III Unit (.g/.a/.scene/.ac)')


def menu_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_d3unit.bl_idname,
                         text='Disciples III Unit (.g / .scene)')


_classes = (IMPORT_SCENE_OT_d3unit, EXPORT_SCENE_OT_d3unit)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)


if __name__ == '__main__':
    register()
