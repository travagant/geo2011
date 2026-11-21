"""Experimental profile icon paths with six already-encoded .t payloads.

No image conversion, in-game compatibility or original game files are changed.
"""
import re
from . import char, texture

_ID = re.compile(r'[a-z][a-z0-9_]{1,39}')
_SLOTS = {
    'icon': ('', 64, 128, 7),
    'subicon': ('_small', 64, 64, 7),
    'layout_icon': ('_node', 128, 128, 7),
    'capital_icon': ('_capital', 256, 256, 7),
    'dialog_icon': ('_dialog', 512, 512, 7),
    'leader_icon': ('_ring', 128, 128, 3),
}


def prepare_icon_probe(profile_data: bytes, leader_id: str, icons: dict):
    """Return (profile bytes, resource path -> bytes), never modify inputs.

    Only the six known Warrior icon directives and exact original slot formats
    are accepted. Existing .t payloads are copied, not converted. The result is
    an experiment; the game may cache icons or interpret references differently.
    """
    if not _ID.fullmatch(leader_id) or not isinstance(icons, dict) or set(icons) != set(_SLOTS):
        raise ValueError('invalid ID or icon slots')
    p = char.parse_char(profile_data)
    if p.parse_error or p.blocks or p.get_all('id') != [leader_id]:
        raise ValueError('expected no-sets profile with matching ID')
    resources = {}
    for key, (suffix, width, height, fmt) in _SLOTS.items():
        stmts = [s for s in p.statements if s.key.lower() == key]
        expected = 'resources\\characters\\empire\\leader-warrior\\icon_leader-warrior' + suffix + '.t'
        if len(stmts) != 1 or stmts[0].block != -1 or not stmts[0].quoted or not stmts[0].quoted[0] or char.normalize_reference(stmts[0].value) != expected:
            raise ValueError('unexpected donor icon reference: ' + key)
        data = icons[key]
        if not isinstance(data, bytes):
            raise ValueError('icon must contain bytes: ' + key)
        info = texture.parse_t(data)
        if (info.width, info.height, info.gm_format, info.mip_count, info.faces) != (width, height, fmt, 1, 1) or len(info.payload) != info.payload_size():
            raise ValueError('unexpected icon dimensions, format or payload: ' + key)
        rel = f'resources/characters/empire/{leader_id}/icon_{leader_id}{suffix}.t'
        if not p.set_value(key, rel.replace('/', '\\')):
            raise ValueError('cannot update icon: ' + key)
        resources[rel] = data
    out = char.encode_char(p)
    old, new = profile_data.splitlines(keepends=True), out.splitlines(keepends=True)
    if len(old) != len(new) or sum(a != b for a, b in zip(old, new)) != len(_SLOTS):
        raise ValueError('unexpected profile changes')
    check = char.parse_char(out)
    if check.parse_error or check.get_all('id') != [leader_id] or check.unit_scene != p.unit_scene or check.unit_scene_lod != p.unit_scene_lod:
        raise ValueError('profile read-back failed')
    for key, (suffix, _, _, _) in _SLOTS.items():
        if char.normalize_reference(check.get(key)) != f'resources\\characters\\empire\\{leader_id}\\icon_{leader_id}{suffix}.t':
            raise ValueError('icon reference read-back failed: ' + key)
    return out, resources
