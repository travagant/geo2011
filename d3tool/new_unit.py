"""Create a staging-only copy of a donor's unit profile.

This does not register a unit, modify recruitment scripts, create a mod or
copy the model. The new profile intentionally keeps the donor's resource and
localization references; game compatibility has not yet been established.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import char

_ID = re.compile(r'^[a-z][a-z0-9_]{1,63}$')


def _safe_child(root, components):
    """Find a case-insensitive path without following symlinks or escaping."""
    path = root
    for component in components:
        if component in ('', '.', '..') or ':' in component:
            raise ValueError('unsafe include path')
        hits = [p for p in path.iterdir() if p.name.lower() == component.lower()]
        if len(hits) != 1 or hits[0].is_symlink():
            raise ValueError('missing, ambiguous or symlinked include: ' + '/'.join(components))
        path = hits[0]
    if not path.is_file():
        raise ValueError('include is not a file: ' + str(path))
    return path


def create_profile_project(profiles_dir, donor_file, new_id, destination, *, copy_skill_grid=False, warrior_visual_no_sets=False):
    """Create a new staging directory, never altering the donor or an existing project.

    The selected source tree is checked for the ID, but other installed mods
    and archives cannot be checked. Only direct desc/stats/sound includes are
    copied; other and nested includes continue referencing donor resources.
    """
    profiles = Path(profiles_dir).resolve(strict=True)
    donor = Path(donor_file).resolve(strict=True)
    dest = Path(destination).absolute()
    if not profiles.is_dir() or donor.parent != profiles or donor.suffix.lower() != '.char':
        raise ValueError('select a donor .char directly inside the profiles folder')
    if not _ID.fullmatch(new_id):
        raise ValueError('new ID must be 2-64 lowercase ASCII letters, digits or underscores, starting with a letter')
    parent = dest.parent.resolve(strict=True)
    dest = parent / dest.name
    if not dest.name or dest.name in ('.', '..'):
        raise ValueError('select a new project folder')
    if profiles == parent or profiles in parent.parents or any(p.name.lower() == 'resources' for p in (parent, *parent.parents)):
        raise ValueError('project must be outside the source profiles and game resources trees')
    if any(p.name.lower() == dest.name.lower() for p in parent.iterdir()):
        raise ValueError('project folder already exists: ' + str(dest))
    if any(p.name.lower() == (new_id + '.char').lower() for p in profiles.iterdir()):
        raise ValueError('profile filename already exists in selected tree: ' + new_id + '.char')
    profile = char.parse_char(donor.read_bytes())
    ids = profile.get_all('id')
    if len(ids) != 1 or not ids[0] or not profile.unit_scene:
        raise ValueError('donor must have exactly one id and a unit_scene')
    # IDs are compared, not filenames: some retail profiles differ from filenames.
    for filename in char.find_profiles(str(profiles)):
        path = Path(filename)
        if path.is_symlink():
            raise ValueError('symlinked profile cannot be checked: ' + str(path))
        existing = char.parse_char(path.read_bytes())
        if any(value.lower() == new_id.lower() for value in existing.get_all('id')):
            raise ValueError('ID already exists in the selected profile tree: ' + new_id)
    if not profile.set_id(new_id):
        raise ValueError('cannot safely rewrite donor id; check its formatting')
    output = {}
    copied = set()
    for stmt in profile.statements:
        if stmt.directive != '#include' or not stmt.value:
            continue
        if stmt.value.startswith(('/', '\\')) or ':' in stmt.value:
            raise ValueError('absolute include path is unsafe: ' + stmt.value)
        parts = [x for x in re.split(r'[\\/]+', stmt.value) if x]
        if any(x in ('.', '..') for x in parts):
            raise ValueError('relative traversal in include: ' + stmt.value)
        # Only the ordinary direct, named include types are rewritten. Never
        # guess paths for arbitrary custom includes or nested profile graphs.
        if len(parts) != 2 or parts[0].lower() not in ('desc', 'stats', 'sound'):
            continue
        kind = parts[0].lower()
        if kind in copied:
            raise ValueError('multiple ' + kind + ' includes require manual handling')
        src = _safe_child(profiles, parts)
        include = char.parse_char(src.read_bytes())
        if include.get_all('id'):
            raise ValueError('included file declares an id: ' + str(src))
        if src.suffix.lower() != '.char':
            raise ValueError('unsupported include type: ' + str(src))
        filename = new_id + '_' + kind + '.char'
        if any(p.name.lower() == filename.lower() for p in src.parent.iterdir()):
            raise ValueError('include filename already exists in selected tree: ' + filename)
        # The game's common escaped-backslash spelling, as in the donor.
        new_ref = kind + '\\\\' + filename
        line = profile.lines[stmt.line]
        start, end = line.find('"'), line.rfind('"')
        if start < 0 or end <= start:
            raise ValueError('include must use a quoted path: ' + stmt.value)
        profile.lines[stmt.line] = line[:start + 1] + new_ref + line[end:]
        output[('resources', 'Profiles', kind, filename)] = src.read_bytes()
        copied.add(kind)
    if not copied:
        raise ValueError('donor has no supported desc/stats/sound includes')
    if copy_skill_grid:
        stats_key = ('resources', 'Profiles', 'stats', new_id + '_stats.char')
        if stats_key not in output:
            raise ValueError('copying a skill grid requires a direct stats include')
        stats = char.parse_char(output[stats_key])
        grid_values = stats.get_all('skill_grid')
        if len(grid_values) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]+', grid_values[0]):
            raise ValueError('stats must contain exactly one safe skill_grid name')
        skills = _safe_child_dir(profiles, ['skills'])
        grid_file = _safe_child(profiles, ['skills', grid_values[0] + '.grid'])
        # Check IDs as well as file names: a grid can have a nonmatching name.
        grid_header = re.compile(r'(?im)^[ \t]*grid[ \t]+"([^"\r\n]+)"[ \t]*;?[ \t]*(?:\r?\n|$)')
        donor_bytes = grid_file.read_bytes()
        for path in skills.iterdir():
            if path.suffix.lower() != '.grid':
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError('cannot safely check grid: ' + str(path))
            headers = list(grid_header.finditer(char._mask_comments(path.read_bytes().decode('latin-1'))))
            if len(headers) != 1:
                raise ValueError('cannot safely check grid header: ' + str(path))
            if path.name.lower() == (new_id + '.grid').lower() or headers[0].group(1).lower() == new_id.lower():
                raise ValueError('skill grid ID or filename already exists: ' + new_id)
        grid_text = donor_bytes.decode('latin-1')
        headers = list(grid_header.finditer(char._mask_comments(grid_text)))
        if len(headers) != 1 or headers[0].group(1).lower() != grid_values[0].lower():
            raise ValueError('donor grid header does not match stats skill_grid')
        start, end = headers[0].span(1)
        output[('resources', 'Profiles', 'skills', new_id + '.grid')] = (grid_text[:start] + new_id + grid_text[end:]).encode('latin-1')
        if not stats.set_value('skill_grid', new_id):
            raise ValueError('cannot safely rewrite skill_grid')
        output[stats_key] = char.encode_char(stats)
    profile_bytes = char.encode_char(profile)
    if warrior_visual_no_sets:
        if ids != ['empire_leader-warrior']:
            raise ValueError('no-sets visual experiment requires the Empire Warrior leader donor')
        from . import new_leader_profile
        profile_bytes, _ = new_leader_profile.preview_visual_profile_without_warrior_sets(profile_bytes, new_id)
    output[('resources', 'Profiles', new_id + '.char')] = profile_bytes
    # A newly created directory is the ownership boundary for rollback.
    dest.mkdir()
    try:
        for parts, data in output.items():
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as fh:
                fh.write(data)
    except Exception:
        shutil.rmtree(dest)
        raise
    return {'project': str(dest), 'profile': str(dest / 'resources' / 'Profiles' / (new_id + '.char')),
            'donor_id': ids[0], 'new_id': new_id, 'copied': sorted(copied),
            'skill_grid_copied': copy_skill_grid, 'warrior_visual_no_sets': warrior_visual_no_sets}


def _unit_list_copy(data, new_id, *, kind="unit", donor_id=None):
    """Return a copy with a unit entry next to the first top-level unit.

    Parse just enough to distinguish list entries from commented-out text and
    nested building blocks. Reject ambiguous/malformed files instead of guessing.
    Latin-1 is used as a reversible byte mapping, not as a charset conversion.
    """
    if kind not in ('unit', 'leader') or not _ID.fullmatch(new_id):
        raise ValueError('invalid list entry or ID')
    text = data.decode('latin-1')
    clean = list(text)
    depth = 0
    depths = [0] * len(text)
    i = 0
    quoted = False
    while i < len(text):
        depths[i] = depth
        c = text[i]
        if quoted:
            if c in '\r\n':
                raise ValueError('multiline string in script')
            if c == '\\' and i + 1 < len(text):
                depths[i + 1] = depth
                i += 2
                continue
            if c == '"':
                quoted = False
            i += 1
            continue
        if c == '"':
            quoted = True
            i += 1
            continue
        if text.startswith('//', i) or text.startswith('/*', i):
            block = text.startswith('/*', i)
            end = text.find('*/' if block else '\n', i + 2)
            if block and end < 0:
                raise ValueError('unterminated comment in script')
            end = (end + 2) if block else (len(text) if end < 0 else end)
            for j in range(i, end):
                depths[j] = depth
                if clean[j] not in '\r\n':
                    clean[j] = ' '
            i = end
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth < 0:
                raise ValueError('unbalanced script braces')
        i += 1
    if quoted or depth:
        raise ValueError('unterminated string or unbalanced script braces')
    clean = ''.join(clean)
    entries = list(re.finditer(r'(?m)^[ \t]*(unit|leader)[ \t]+"([^"\r\n]+)"[ \t]*;[ \t]*(?:\r?\n|$)', clean, re.I))
    all_refs = re.finditer(r'\b(?:unit|leader)[ \t]+"([^"\r\n]+)"[ \t]*;', clean, re.I)
    if any(m.group(1).lower() == new_id.lower() for m in all_refs):
        raise ValueError('ID already appears in script: ' + new_id)
    anchors = [m for m in entries if depths[m.start()] == 0 and m.group(1).lower() == kind]
    if donor_id is not None:
        anchors = [m for m in anchors if m.group(2).lower() == donor_id.lower()]
    if not anchors or (donor_id is not None and len(anchors) != 1):
        raise ValueError('no unambiguous top-level ' + kind + ' list or donor in script')
    at = anchors[0].start()
    newline = '\r\n' if '\r\n' in text else '\n'
    return (text[:at] + kind + ' "' + new_id + '";' + newline + text[at:]).encode('latin-1')


def stage_empire_recruitment(profiles_dir, project_dir, new_id, *, kind="unit", donor_id=None):
    """Stage experimental list additions; never overwrite source or project files.

    The selected project must already hold this ID's staged profile. This does
    not establish that the game registers the ID, permits recruitment, or that
    these two scripts are current or compatible with other active mods.
    """
    if not _ID.fullmatch(new_id):
        raise ValueError('invalid unit ID')
    profiles = Path(profiles_dir).resolve(strict=True)
    project = Path(project_dir).resolve(strict=True)
    if not profiles.is_dir() or not project.is_dir():
        raise ValueError('expected directories')
    if profiles == project or profiles in project.parents or any(p.name.lower() == 'resources' for p in (project, *project.parents)):
        raise ValueError('project must be outside source profiles and resources trees')
    staged = _safe_child(project, ['resources', 'Profiles', new_id + '.char'])
    if char.parse_char(staged.read_bytes()).id != new_id:
        raise ValueError('staged profile ID does not match')
    source = _safe_child(profiles, ['scripts', 'unit_empire.s'])
    town = _safe_child(profiles, ['scripts', 'town_empire.s'])
    if kind == 'leader' and not donor_id:
        raise ValueError('leader staging requires a donor ID')
    contents = {p.name.lower(): _unit_list_copy(p.read_bytes(), new_id, kind=kind, donor_id=donor_id) for p in (source, town)}
    target_profiles = _safe_child_dir(project, ['resources', 'Profiles'])
    hits = [p for p in target_profiles.iterdir() if p.name.lower() == 'scripts']
    if hits:
        raise ValueError('project scripts folder already exists; refusing to overwrite')
    output = target_profiles / 'scripts'
    output.mkdir()
    try:
        for name, data in contents.items():
            with (output / name).open('xb') as f:
                f.write(data)
    except Exception:
        shutil.rmtree(output)
        raise
    return [str(output / name) for name in contents]


def _safe_child_dir(root, components):
    path = root
    for component in components:
        hits = [p for p in path.iterdir() if p.name.lower() == component.lower()]
        if len(hits) != 1 or hits[0].is_symlink() or not hits[0].is_dir():
            raise ValueError('missing, ambiguous or symlinked directory: ' + component)
        path = hits[0]
    return path
