"""Experimental scene binding for a new leader mesh; NOT an installable mod.

The donor bones/animation config and FX remain references to the donor. How
leader sets, LOD, recruitment and the game interpret this scene is unverified.
All functions work in memory and never modify the donor or game files.
"""
from __future__ import annotations

import re
from . import gfile, scene


def build_scene(donor_bytes, geometry_bytes, resource_path):
    """Clone one-gobj leader scene and point its gobjs at the new compound .g.

    Only a simple donor with one bones node and its single gobj is supported.
    Keep the donor .ac and FX untouched; a caller must not interpret this as
    generating a complete, independent leader. Resource path must be new and
    strictly relative to the game's resources/characters subtree.
    """
    if not re.fullmatch(r'resources\\characters(?:\\[a-z0-9_-]+)+\\[a-z][a-z0-9_]{1,63}\.g', resource_path):
        raise ValueError('unsafe or unsupported geometry resource path')
    geometry = gfile.parse_geometry_file(geometry_bytes)
    if geometry.parse_error or gfile.count_mismatches(geometry):
        raise ValueError('invalid geometry')
    parts = list(gfile.iter_parts(geometry))
    if (not parts or any(not re.fullmatch(r'[a-z][a-z0-9_]{1,63}', p.name or '') for p in parts)
            or len({p.name for p in parts}) != len(parts)):
        raise ValueError('invalid or duplicate geometry part names')
    text = donor_bytes.decode('latin-1')
    doc = scene.parse_scene(text)
    bones = doc.find_all('bones')
    gobjs = doc.find_all('gobj')
    if len(bones) != 1 or len(gobjs) != 1 or gobjs[0] not in bones[0].children:
        raise ValueError('donor scene must contain one gobj directly under one bones node')
    if not bones[0].prop('file') or scene.node_mesh_index(gobjs[0]) != 0:
        raise ValueError('donor scene animation or slot is ambiguous')
    matches = [scene._MESH_LINE_RE.match(s.rstrip('\r\n')) for s in gobjs[0].items if isinstance(s, str)]
    matches = [m for m in matches if m]
    if len(matches) != 1 or matches[0].group(2).lower() == resource_path.lower():
        raise ValueError('ambiguous or unsafe donor mesh path')
    # Clone the donor's gobj for each additional part, preserving bonesuid and
    # keeping the weapon-trace object outside the gobj intact.
    text, added = scene.ensure_part_gobjs(text, parts)
    if len(added) != len(parts) - 1:
        raise ValueError('could not create all scene nodes')
    doc = scene.parse_scene(text)
    nodes = doc.find_all('gobj')
    if len(nodes) != len(parts) or [scene.node_mesh_index(n) for n in nodes] != list(range(len(parts))):
        raise ValueError('unexpected scene mesh binding')
    for index, (node, part) in enumerate(zip(nodes, parts)):
        indent = node.header[:len(node.header) - len(node.header.lstrip())]
        node.header = indent + 'child gobj "' + part.name + '" ' + scene._eol(node.header)
        refs = 0
        for i, item in enumerate(node.items):
            if not isinstance(item, str):
                continue
            match = scene._MESH_LINE_RE.match(item.rstrip('\r\n'))
            if match:
                node.items[i] = f'{match.group(1)}"{resource_path}" {index} {match.group(4)}' + scene._eol(item)
                refs += 1
        if refs != 1:
            raise ValueError('ambiguous generated mesh reference')
        attrs = {
            'name': part.name, 'materials_num': '1',
            'material0_diffuse': part.attrs.get('material0_diffuse', ''),
            'material0_triangles_num': str(part.tri_count),
            'vertexs_weights_num': str(part.vertex_count),
            'weights_on_vertex': str(part.weights_on_vertex),
            'bones_num': str(len(part.bones)),
        }
        for key, value in attrs.items():
            if not value or not scene._set_attr(node.items, key, value):
                raise ValueError('missing scene attribute: ' + key)
    result = scene.render_scene(doc).encode('latin-1')
    check = scene.parse_scene(result.decode('latin-1'))
    if len(check.find_all('gobj')) != len(parts) or len(check.find_all('bones')) != 1:
        raise ValueError('generated scene did not pass read-back')
    return result
