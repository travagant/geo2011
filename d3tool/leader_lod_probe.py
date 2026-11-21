"""In-memory Warrior LOD hypothesis: use the main scene for both distances.

This does not simplify geometry and is not known to work in the game. It must
only be used for a separate experimental package, never on game source files.
"""
import re
from . import char

_ID = re.compile(r'[a-z][a-z0-9_]{1,39}')
_DONOR_LOD = 'resources\\characters\\empire\\leader-warrior\\character_empire_leader-warrior_lod.scene'


def use_main_scene_at_lod(profile_data: bytes, leader_id: str):
    """Replace only the LOD path; preserve its distance, spelling and all else."""
    if not _ID.fullmatch(leader_id):
        raise ValueError('invalid leader ID')
    profile = char.parse_char(profile_data)
    ids = [x for x in profile.statements if x.key.lower() == 'id']
    mains = [x for x in profile.statements if x.key.lower() == 'unit_scene']
    lods = [x for x in profile.statements if x.key.lower() == 'unit_scene_lod']
    if profile.parse_error or profile.blocks or len(ids) != 1 or ids[0].block != -1 or ids[0].value != leader_id:
        raise ValueError('expected no-sets profile with matching ID')
    if len(mains) != 1 or mains[0].block != -1 or not mains[0].quoted or not mains[0].quoted[0]:
        raise ValueError('ambiguous main scene')
    if len(lods) != 1 or lods[0].block != -1 or lods[0].values[1:] != ['10'] or lods[0].quoted != [True, False]:
        raise ValueError('expected exactly one donor LOD with distance 10')
    target = f'resources\\characters\\empire\\{leader_id}\\{leader_id}_model.scene'
    if char.normalize_reference(mains[0].value) != target or char.normalize_reference(lods[0].value) != _DONOR_LOD:
        raise ValueError('unexpected main scene or donor LOD')
    original_line = profile.lines[lods[0].line]
    if not re.fullmatch(r'\s*unit_scene_lod\s+"[^"\r\n]+"\s*,\s*10\s*;\s*', original_line, re.I):
        raise ValueError('unsupported LOD line')
    # Preserve the original single-backslash style and the entire distance suffix.
    if not profile.set_value('unit_scene_lod', target):
        raise ValueError('cannot update LOD safely')
    result = char.encode_char(profile)
    check = char.parse_char(result)
    if check.parse_error or check.unit_scene != mains[0].value or check.statement('unit_scene_lod').values != [target, '10']:
        raise ValueError('LOD profile round-trip failed')
    before = profile_data.decode('latin-1').splitlines(keepends=True)
    after = result.decode('latin-1').splitlines(keepends=True)
    if len(before) != len(after) or sum(a != b for a, b in zip(before, after)) != 1:
        raise ValueError('unexpected changes outside LOD line')
    return result
