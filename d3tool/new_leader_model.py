"""Experimental geometry building block for a new skinned leader.

This module only creates geometry bytes in memory; it does NOT make a leader
profile, scenes, a mod, an index or a game-ready asset. It is not exposed in
the Blender interface. The caller must validate the Blender scene and textures.
"""
from __future__ import annotations

import copy
import math
import re

from . import frame, gfile, gltf
from .model import SkinnedMesh


def _animation_calibration(m, donor, used, tolerance=0.0005):
    """Verify that the exported action matches the donor at every 30-fps frame.

    The exporter does not emit a changed action. Reject it rather than silently
    returning a model with different poses. This numeric check is not an
    in-game animation test and does not validate event tables.
    """
    if donor.raw or not donor.bones or not donor.frame_count:
        raise ValueError('animation donor is unreadable or has no frames')
    q, warnings = gltf._blender_q_map(m, [donor])
    if not q:
        raise ValueError('could not calibrate the skeleton: ' + '; '.join(warnings))
    bone_map = {}
    for bone in donor.bones:
        bone_map.setdefault(bone.name, bone)
    for name in list(used):
        seen = set()
        while name in bone_map and name not in seen:
            seen.add(name)
            used.add(name)
            name = bone_map[name].parent or ''
    nodes = {}
    for i, node in enumerate(m.nodes):
        name = node.get('name')
        if name:
            nodes.setdefault(name, []).append(i)
    for name in sorted(used):
        bone = bone_map.get(name)
        if bone is None or name not in q or len(nodes.get(name, [])) != 1:
            raise ValueError('missing or ambiguous donor bone: ' + name)
        if not bone.frames or len(bone.frames) < donor.frame_count:
            raise ValueError('incomplete donor animation: ' + name)
        idx = nodes[name][0]
        node = m.nodes[idx]
        channels = m.anim_channels.get(idx, {})
        times = m.anim_times.get(idx, {})
        if not all(k in channels and k in times for k in ('rotation', 'translation')):
            raise ValueError('missing animation channels: ' + name)
        values = {key: [x for vec in channels[key] for x in vec[:n]]
                  for key, n in (('rotation', 4), ('translation', 3))}
        parent_q = q.get(bone.parent, frame.IDENTITY)
        for index in range(donor.frame_count):
            time = index / 30.0
            rot = frame.sample_channel(times['rotation'], values['rotation'], 4, time, slerp=True)
            pos = frame.sample_channel(times['translation'], values['translation'], 3, time)
            local = frame.trs_matrix(rot, pos, node.get('scale'))
            actual = frame.convert_local(local, q[name], parent_q)
            expected = frame.sample_matrix(bone.frames[index][:7])
            delta = max(abs(actual[r][c] - expected[r][c]) for r in range(4) for c in range(4))
            if not math.isfinite(delta) or delta > tolerance:
                raise ValueError('animation differs from donor: %s frame %d (%.6f)' % (name, index, delta))
    return q


def build_geometry(m, donor_animation, geometry_name, atlases):
    """Convert a Blender glTF's skinned meshes to an isolated compound .g.

    ``atlases`` maps each original mesh node name to a safe .tga atlas name;
    creating/copying the corresponding .t files is the caller's job. Reject
    mesh transforms the current conversion doesn't handle. No original files
    are touched and no game-readiness is implied.
    """
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,63}', geometry_name):
        raise ValueError('invalid geometry name')
    if not m.blender_layout:
        raise ValueError('expected a Blender glTF with a shared skin')
    models = [m] + list(m.submodels or [])
    names = [s.mesh_name for s in models]
    if not names or len(set(names)) != len(names) or any(not n for n in names):
        raise ValueError('mesh names are missing or ambiguous')
    if set(atlases) != set(names):
        raise ValueError('an explicit atlas is required for each mesh')
    used = set()
    for sub in models:
        if not re.fullmatch(r'[a-zA-Z0-9_]+\.tga', atlases[sub.mesh_name]):
            raise ValueError('unsafe atlas name: ' + atlases[sub.mesh_name])
        if sub.node_matrix is not None and not frame.is_identity(sub.node_matrix):
            raise ValueError('non-identity mesh transform is not supported: ' + sub.mesh_name)
        if sub.morph or not sub.skin_joint_names or not sub.vertices or not sub.indices:
            raise ValueError('only nonempty skinned meshes without morphs are supported: ' + sub.mesh_name)
        if len(sub.material_slots or {}) > 1 or len(sub.material_triangles or {}) > 1:
            raise ValueError('multiple materials per mesh are not supported: ' + sub.mesh_name)
        if len(sub.indices) % 3 or any(i < 0 or i >= len(sub.vertices) for i in sub.indices):
            raise ValueError('invalid triangle index: ' + sub.mesh_name)
        for vertex in sub.vertices:
            if not all(math.isfinite(x) for x in (*vertex.position, *vertex.normal, *vertex.uv, *vertex.gltf_weights)):
                raise ValueError('non-finite vertex data: ' + sub.mesh_name)
            if any(w < 0 for w in vertex.gltf_weights):
                raise ValueError('negative skin weight: ' + sub.mesh_name)
            active = [(w, j) for w, j in zip(vertex.gltf_weights, vertex.gltf_joints) if w > 1e-6]
            if not active or len(active) > 4 or abs(sum(w for w, _ in active) - 1.0) > 0.002:
                raise ValueError('missing or unnormalized skin weights: ' + sub.mesh_name)
            for _, joint in active:
                if joint < 0 or joint >= len(sub.skin_joint_names):
                    raise ValueError('invalid skin joint: ' + sub.mesh_name)
                used.add(sub.skin_joint_names[joint])
    q = _animation_calibration(m, donor_animation, used)
    parts = []
    for index, sub in enumerate(models):
        own = copy.copy(sub)
        own.mesh_name = '%s_part_%02d' % (geometry_name, index)
        own.material_diffuse = atlases[sub.mesh_name]
        own.material_override = own.material_image = ''
        own.material_slots = {}
        own.material_triangles = {}
        part, _ = gltf._extra_mesh_to_part(own, m, q, {}, True, own.mesh_name, own.material_diffuse)
        if not part.bones or any(b.name not in used for b in part.bones):
            raise ValueError('unexpected generated bone table: ' + sub.mesh_name)
        parts.append(part)
    first = parts[0]
    mesh = SkinnedMesh(name=first.name, unit_name=first.name, geometry_file=geometry_name,
                       vertices=first.vertices, indices=first.indices, bones=first.bones,
                       vertex_count=first.vertex_count, tri_count=first.tri_count,
                       weights_on_vertex=first.weights_on_vertex,
                       material_diffuse=first.material_diffuse, attrs=first.attrs,
                       parts=parts[1:])
    gfile.normalize_part_identity(mesh, mesh.attrs.get('dwParent'))
    data = gfile.write_geometry_file(mesh, mesh.attrs)
    reread = gfile.parse_geometry_file(data)
    if reread.parse_error or gfile.count_mismatches(reread):
        raise ValueError('created geometry did not pass a read-back check')
    if len(gfile.iter_parts(reread)) != len(parts):
        raise ValueError('created geometry has an unexpected number of parts')
    try:
        _, count = gfile.mesh_count(data)
    except ValueError as exc:
        raise ValueError('created geometry has an invalid node-chain length') from exc
    if count != len(parts):
        raise ValueError('created geometry has an invalid mesh count')
    return data
