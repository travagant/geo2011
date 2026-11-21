"""Assemble a narrow, non-destructive Warrior leader test pack.

Inputs are the profile/list project and visual staging output made by this
addon. This validates consistency, not game compatibility or mod conflicts.
"""
import hashlib
import json
import re
import shutil
from pathlib import Path

from . import char, gfile, new_unit, vdpack, leader_icon_probe, leader_icon_image

_ID = re.compile(r'[a-z][a-z0-9_]{1,39}')


def _tree(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('input must be a real directory, not a symlink')
    result = {}
    for item in root.rglob('*'):
        if item.is_symlink() or (not item.is_dir() and not item.is_file()):
            raise ValueError('unsupported or symlinked input: ' + str(item))
        if item.is_file():
            name = item.relative_to(root).as_posix()
            if name.casefold() in result:
                raise ValueError('case-insensitive input path collision')
            result[name.casefold()] = (name, item.read_bytes())
    return result


def build_test_bundle(project_dir, visual_dir, profiles_dir, leader_id, destination, *, icon_png_dir=None):
    """Build a new directory with an indexed test .vdpack and a report.

    Strictly checks input inventory, staging hashes, profile identity and list
    copies. Does not install anything, validate other mods, or prove game use.
    Destination must be new and outside all input and game resource trees.
    """
    if not _ID.fullmatch(leader_id):
        raise ValueError('leader ID must contain 2-40 safe characters')
    roots = [Path(x) for x in (project_dir, visual_dir, profiles_dir)]
    if any(p.is_symlink() or not p.is_dir() for p in roots):
        raise ValueError('input must be a real directory')
    project, visual, profiles = [p.resolve(strict=True) for p in roots]
    dest = Path(destination).absolute()
    parent = dest.parent.resolve(strict=True)
    dest = parent / dest.name
    if any(p.name.casefold() in ('resources', 'profiles') for p in (parent, *parent.parents)):
        raise ValueError('destination must be outside game resources and Profiles')
    if any(parent == p or p in parent.parents for p in (project, visual, profiles)):
        raise ValueError('destination must be outside input trees')
    if any(p.name.casefold() == dest.name.casefold() for p in parent.iterdir()):
        raise ValueError('destination already exists')
    pfiles, vfiles = _tree(project), _tree(visual)
    prefix = 'resources/profiles/'
    profile_key = prefix + leader_id + '.char'
    required = {profile_key, *(prefix + k + '/' + leader_id + '_' + k + '.char' for k in ('desc', 'stats', 'sound')),
                *(prefix + 'scripts/' + k + '_empire.s' for k in ('unit', 'town'))}
    optional = {prefix + 'skills/' + leader_id + '.grid'}
    if not required <= pfiles.keys() or set(pfiles) - required - optional:
        raise ValueError('unexpected or missing profile project files')
    profile = char.parse_char(pfiles[profile_key][1])
    target_scene = f'resources/characters/empire/{leader_id}/{leader_id}_model.scene'
    if (profile.parse_error or profile.get_all('id') != [leader_id] or profile.blocks or
            char.normalize_reference(profile.unit_scene).replace('\\', '/') != target_scene):
        raise ValueError('profile must point to this visual scene and have no set blocks')
    for kind in ('unit', 'town'):
        source = new_unit._safe_child(profiles, ['scripts', kind + '_empire.s']).read_bytes()
        expected = new_unit._unit_list_copy(source, leader_id, kind='leader', donor_id='empire_leader-warrior')
        if pfiles[prefix + 'scripts/' + kind + '_empire.s'][1] != expected:
            raise ValueError('leader list differs from the selected source: ' + kind)
    try:
        manifest = json.loads(vfiles['experiment.json'][1].decode('utf-8'))
    except (KeyError, ValueError, UnicodeError) as exc:
        raise ValueError('missing or invalid visual manifest') from exc
    if manifest.get('leader_id') != leader_id or not isinstance(manifest.get('files_sha256'), dict):
        raise ValueError('visual manifest ID or files invalid')
    base = f'resources/characters/empire/{leader_id}/'
    expected_keys = {base + leader_id + '_model.g', base + leader_id + '_model.scene'}
    payloads = {k: v for k, v in vfiles.items() if k not in ('experiment.json', 'readme.txt')}
    if not expected_keys <= payloads.keys() or not any(k.startswith(base) and k.endswith('.t') for k in payloads):
        raise ValueError('missing visual geometry, scene or texture')
    if any(not k.startswith(base) or '/' in k[len(base):] or not k.endswith(('.g', '.scene', '.t')) for k in payloads):
        raise ValueError('unexpected visual resource')
    hashes = manifest['files_sha256']
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in hashes.items()):
        raise ValueError('invalid visual hashes')
    lowered = {k.casefold(): v for k, v in hashes.items()}
    if len(lowered) != len(hashes) or set(lowered) != set(payloads):
        raise ValueError('visual manifest inventory mismatch')
    for key, (_, data) in payloads.items():
        if hashlib.sha256(data).hexdigest() != lowered[key]:
            raise ValueError('visual resource differs from manifest: ' + key)
    geom = payloads[base + leader_id + '_model.g'][1]
    parsed = gfile.parse_geometry_file(geom)
    parts = list(gfile.iter_parts(parsed))
    if parsed.parse_error or not parts or gfile.count_mismatches(parsed) or gfile.mesh_count(geom)[1] != len(parts):
        raise ValueError('invalid visual geometry or mesh count')
    icon_resources = {}
    if icon_png_dir:
        folder = Path(icon_png_dir)
        files = _tree(folder)
        expected_icons = {key + '.png' for key in leader_icon_probe._SLOTS}
        if set(files) != expected_icons:
            raise ValueError('icon folder must contain exactly: ' + ', '.join(sorted(expected_icons)))
        converted = {key: leader_icon_image.png_to_icon(files[key + '.png'][1], key)
                     for key in leader_icon_probe._SLOTS}
        updated, icon_resources = leader_icon_probe.prepare_icon_probe(pfiles[profile_key][1], leader_id, converted)
        if any(k.casefold() in payloads for k in icon_resources):
            raise ValueError('icon filename collides with visual resource')
        pfiles[profile_key] = (pfiles[profile_key][0], updated)
    pairs = sorted([*pfiles.values(), *payloads.values(), *icon_resources.items()], key=lambda x: x[0].lower())
    pack_name = leader_id + '_test'
    indexed = vdpack.reindex_pack(pairs, name=pack_name)
    blob = vdpack.build_vdpack(indexed)
    unpacked = vdpack.parse_vdpack(blob)
    check = vdpack.index_report(unpacked.files())
    if (unpacked.parse_error or not check['decoded'] or check['missing'] or check['mismatched'] or check['unlisted'] or
            any(unpacked.payload(name) != data for name, data in indexed)):
        raise ValueError('archive verification failed')
    report = {'status': 'experimental test only', 'leader_id': leader_id,
              'archive': pack_name + '.vdpack', 'archive_sha256': hashlib.sha256(blob).hexdigest(),
              'resource_count': len(pairs), 'lod': profile.unit_scene_lod,
              'custom_icons': bool(icon_resources),
              'warnings': ['No donor equipment sets; equipment and saves untested.',
                           'Donor LOD, animation, effects and other dependencies remain.',
                           'Complete Empire scripts may conflict with other mods.',
                           'Other installed mods and game behavior not checked.',
                           'PNG-to-icon conversion is experimental and not yet game-tested.' if icon_resources else 'Donor icons remain.']}
    dest.mkdir()
    try:
        with (dest / report['archive']).open('xb') as f:
            f.write(blob)
        with (dest / 'REPORT.json').open('x', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            f.write('\n')
        with (dest / 'README.txt').open('x', encoding='utf-8') as f:
            f.write('EXPERIMENTAL TEST PACK - back up saves. Enable only one pack with this ID.\n'
                    'Donor LOD and dependencies remain. Equipment and saves are untested.\n'
                    'Whole Empire scripts may conflict with other mods. See REPORT.json.\n'
                    'Custom PNG icon conversion, when selected, is not game-tested.\n')
    except Exception:
        shutil.rmtree(dest)
        raise
    return report
