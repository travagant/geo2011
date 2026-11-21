"""Prepare a candidate leader profile in memory, not an installable mod.

Only the main scene reference is changed. Equipment and LOD intentionally
remain donor references pending game tests; callers must not silently install
this candidate or represent it as an independent visual leader.
"""
from __future__ import annotations

import re

from . import char, leader_set_probe

_ID = re.compile(r'[a-z][a-z0-9_]{1,39}')


def preview_visual_profile(profile_data: bytes, leader_id: str):
    """Return (candidate bytes, report), without touching the input or filesystem.

    Accept only a profile already created for leader_id. The visual path is
    fixed to the staging convention and cannot be an arbitrary game path.
    All donor set slots, LOD, and other profile directives are preserved.
    A successful return is not a compatibility or dependency check.
    """
    if not _ID.fullmatch(leader_id):
        raise ValueError('invalid leader ID')
    profile = char.parse_char(profile_data)
    if profile.parse_error:
        raise ValueError('profile parse error: ' + profile.parse_error)
    ids = [s for s in profile.statements if s.key.lower() == 'id']
    scenes = [s for s in profile.statements if s.key.lower() == 'unit_scene']
    if len(ids) != 1 or ids[0].block != -1 or ids[0].value != leader_id:
        raise ValueError('expected one top-level profile ID matching leader ID')
    if len(scenes) != 1 or scenes[0].block != -1 or not scenes[0].value:
        raise ValueError('expected one nonempty top-level unit_scene')
    if not scenes[0].quoted or not scenes[0].quoted[0]:
        raise ValueError('unit_scene must be quoted')
    old_scene = scenes[0].value
    # Match the escaped-backslash style of the supplied Warrior profile.
    new_scene = r'resources\\characters\\empire\\' + leader_id + r'\\' + leader_id + '_model.scene'
    if not profile.set_value('unit_scene', new_scene):
        raise ValueError('could not safely replace unit_scene')
    candidate = char.encode_char(profile)
    check = char.parse_char(candidate)
    if check.unit_scene != new_scene or check.get_all('id') != [leader_id]:
        raise ValueError('profile did not round-trip')
    equipment = []
    for index, block in enumerate(check.blocks):
        if block.name.lower() == 'set':
            for statement in check.statements:
                if statement.block == index and statement.key.lower() in char.SET_SLOT_KEYS:
                    equipment.append({'set': block.argument, 'slot': statement.key.lower(), 'path': statement.value})
    return candidate, {
        'status': 'in-memory experiment only; not an installable mod',
        'leader_id': leader_id,
        'old_scene': old_scene,
        'new_scene': new_scene,
        'lod': check.unit_scene_lod,
        'equipment': equipment,
        'warnings': [
            'Equipment slots remain unchanged and may overlay the new model.',
            'LOD remains unchanged and may show the donor at a distance.',
            'Registration, resources, animations, textures and game behavior are not verified.',
        ],
    }


def preview_visual_profile_without_warrior_sets(profile_data: bytes, leader_id: str):
    """Prepare an isolated candidate matching the tested no-sets experiment.

    Reject anything other than the exact known Warrior equipment blocks; do not
    install or overwrite a profile. A single successful battle does not establish
    equipment, save/load or LOD compatibility.
    """
    candidate, report = preview_visual_profile(profile_data, leader_id)
    candidate = leader_set_probe.without_donor_sets(candidate, leader_id)
    report['removed_equipment'] = report.pop('equipment')
    report['equipment'] = []
    report['status'] = 'in-memory no-sets experiment only; not an installable mod'
    report['warnings'] = [
        'Warrior equipment sets removed; equipment changes and saves are not verified.',
        'LOD remains unchanged and may show the donor at a distance.',
        'One manually assembled no-sets package completed a reported battle; this candidate is not game-tested.',
        'Registration, dependencies, other animations and game behavior remain unverified.',
    ]
    return candidate, report
