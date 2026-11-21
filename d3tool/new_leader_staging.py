"""Stage experimental leader visual assets only, never an installable mod.

This deliberately does not touch profiles, scripts, donor files, or pack an
archive: leader equipment sets, LOD and game behaviour remain unverified.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from . import gfile, new_leader_model, new_leader_scene, texture

_ID = re.compile(r'[a-z][a-z0-9_]{1,39}')
_ATLAS = re.compile(r'[a-zA-Z0-9_]+\.tga')


def stage_visual_assets(model, donor_animation, donor_scene_bytes, leader_id,
                        atlases, textures, destination):
    """Create a *new* staging directory from already loaded data.

    ``textures`` maps .t basenames to bytes; every atlas must have exactly one
    corresponding supplied payload and no extra payloads are allowed. The
    destination must not exist, must have an existing parent, and must be
    outside resources/ and Profiles/ trees. The function writes nothing before
    all data is built and checked; failures after mkdir remove only its own
    newly created directory. Other installed mods are not checked for conflicts.
    """
    if not _ID.fullmatch(leader_id):
        raise ValueError('leader ID must be 2-40 lowercase ASCII letters, digits or underscores')
    dest = Path(destination).absolute()
    parent = dest.parent.resolve(strict=True)
    if not parent.is_dir() or not dest.name or dest.name in ('.', '..'):
        raise ValueError('destination must be a new directory in an existing workspace')
    if any(p.name.lower() in ('resources', 'profiles') for p in (parent, *parent.parents)):
        raise ValueError('staging must be outside game resources and profiles trees')
    if any(entry.name.casefold() == dest.name.casefold() for entry in parent.iterdir()):
        raise ValueError('staging destination already exists')
    dest = parent / dest.name
    if not atlases or any(not _ATLAS.fullmatch(a) for a in atlases.values()):
        raise ValueError('invalid texture atlas name')
    expected = {a[:-4].casefold() + '.t' for a in atlases.values()}
    if any(not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_]+\.t', name) for name in textures):
        raise ValueError('unsafe texture filename')
    actual = [name.casefold() for name in textures]
    if len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError('missing, extra or ambiguous texture payloads')
    for name, data in textures.items():
        if not isinstance(data, bytes) or not data:
            raise ValueError('invalid texture payload: ' + name)
        info = texture.parse_t(data)
        if not info.width or not info.height or not info.mip_count or len(info.payload) != info.payload_size():
            raise ValueError('texture has invalid dimensions or payload size: ' + name)
    base = leader_id + '_model'
    # Atlas names are content-derived by the caller; parent folders are unique.
    geom = new_leader_model.build_geometry(model, donor_animation, base, atlases)
    reference = 'resources\\characters\\empire\\' + leader_id + '\\' + base + '.g'
    scene_data = new_leader_scene.build_scene(donor_scene_bytes, geom, reference)
    parsed = gfile.parse_geometry_file(geom)
    parts = list(gfile.iter_parts(parsed))
    resource_dir = Path('resources') / 'characters' / 'empire' / leader_id
    payloads = {resource_dir / (base + '.g'): geom,
                resource_dir / (base + '.scene'): scene_data}
    payloads.update({resource_dir / name: data for name, data in textures.items()})
    manifest = {
        'status': 'experimental visual resources only; not an installable mod',
        'leader_id': leader_id,
        'donor_scene_sha256': hashlib.sha256(donor_scene_bytes).hexdigest(),
        'dependencies': ['donor animation configuration and assets referenced by the scene'],
        'not_included': ['leader profile', 'recruitment', 'equipment sets', 'LOD', 'archive'],
        'parts': [{'name': p.name, 'vertices': p.vertex_count,
                   'triangles': p.tri_count, 'bones': len(p.bones),
                   'texture': p.attrs.get('material0_diffuse')} for p in parts],
        'files_sha256': {p.as_posix(): hashlib.sha256(b).hexdigest() for p, b in payloads.items()},
    }
    payloads[Path('EXPERIMENT.json')] = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    payloads[Path('README.txt')] = (
        'EXPERIMENTAL VISUAL ASSETS ONLY - DO NOT INSTALL\n'
        'No leader profile, equipment sets, LOD, recruitment or mod archive.\n'
        'The scene references the donor animation configuration and effects.\n'
        'Game behaviour and textures have NOT been tested.\n'
    ).encode('utf-8')
    dest.mkdir()
    try:
        for rel, data in payloads.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as file:
                file.write(data)
    except Exception:
        shutil.rmtree(dest)
        raise
    return manifest
