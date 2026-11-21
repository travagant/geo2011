"""Narrow, in-memory diagnostic: remove known Warrior set blocks from a copy.

Not a production conversion: removing sets may break the engine, equipment or
savegames. The result must only be used in an isolated game experiment.
"""
import re
from . import char


def without_donor_sets(profile_data: bytes, expected_id: str):
    """Remove exactly three conventional seven-slot set blocks; preserve all else."""
    profile = char.parse_char(profile_data)
    if profile.parse_error or profile.get_all('id') != [expected_id]:
        raise ValueError('invalid or unexpected profile')
    blocks = profile.blocks
    if len(blocks) != 3 or [(b.name.lower(), b.argument) for b in blocks] != [('set', '1'), ('set', '2'), ('set', '3')]:
        raise ValueError('expected exactly three ordered set blocks')
    # Apply strict grammar to raw lines to avoid dropping unrecognized data.
    lines = profile_data.decode('latin-1').splitlines(keepends=True)
    for i, block in enumerate(blocks):
        start, end = block.open_line, block.close_line
        if start < 0 or end <= start or end >= len(lines):
            raise ValueError('invalid set boundaries')
        if not re.fullmatch(r'\s*set\s+' + str(i + 1) + r'\s*\{\s*', lines[start], re.I):
            raise ValueError('unsupported set header')
        if not re.fullmatch(r'\s*}\s*;?\s*', lines[end]):
            raise ValueError('unsupported set footer')
        seen = []
        for line in lines[start + 1:end]:
            m = re.fullmatch(r'\s*(weapon|feet|body|head|legs|arms|hands)\s+"([^"\r\n]+)"\s*;\s*', line, re.I)
            if not m:
                raise ValueError('unexpected set content')
            slot = m.group(1).lower()
            expected = (f'resources\\characters\\empire\\leader-warrior\\{slot}\\'
                        f'empire_leader-warrior_set{i + 1}_{slot}.scene')
            if char.normalize_reference(m.group(2)) != expected:
                raise ValueError('unexpected set reference')
            seen.append(slot)
        if len(seen) != 7 or set(seen) != set(char.SET_SLOT_KEYS):
            raise ValueError('set must have exactly seven unique slots')
    for block in reversed(blocks):
        del lines[block.open_line:block.close_line + 1]
    out = ''.join(lines).encode('latin-1')
    result = char.parse_char(out)
    if result.parse_error or result.blocks or result.get_all('id') != [expected_id] or result.unit_scene != profile.unit_scene or result.unit_scene_lod != profile.unit_scene_lod:
        raise ValueError('unexpected profile after removal')
    return out
