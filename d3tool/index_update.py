"""Update existing loose-resource index entries, preserving all other bytes.

Optionally append records only for referenced auto-generated textures. This
does not edit vdpack archives. Use the actual game resources folder.
"""
from __future__ import annotations

import hashlib
import os
import re
import struct
import tempfile
from datetime import datetime
from pathlib import Path

from . import vdpack, gfile, texture


def _referenced_auto_textures(folder):
    """Names of auto textures referenced by valid geometry in this folder."""
    wanted = set()
    for file in folder.iterdir():
        if file.is_symlink() or not file.is_file() or file.suffix.lower() != '.g':
            continue
        parsed = gfile.parse_geometry_file(file.read_bytes())
        if getattr(parsed, 'parse_error', None):
            raise ValueError('cannot inspect geometry: ' + str(file))
        for part in [parsed] + list(getattr(parsed, 'parts', ()) or ()):
            for key, value in (getattr(part, 'attrs', None) or {}).items():
                if key.lower().endswith('_diffuse') and value:
                    stem = texture.clean_stem(value).lower()
                    if re.fullmatch(r'd3unit_[0-9a-f]{24}', stem):
                        wanted.add(stem + '.t')
    return wanted


def update_folder(index_path, folder, add_auto_textures=False):
    index = Path(index_path).absolute()
    folder = Path(folder).resolve(strict=True)
    if not folder.is_dir():
        raise ValueError('unit folder is not a directory')
    if index.is_symlink() or not index.is_file() or index.name.lower() != 'index':
        raise ValueError('select an existing regular file named index')
    candidates = [p for p in (folder, *folder.parents) if p.name.lower() == 'resources']
    if len(candidates) != 1 or folder == candidates[0]:
        raise ValueError('unit folder must be inside one resources folder')
    resource_root = candidates[0].parent
    rel = folder.relative_to(resource_root)
    prefix = '\\'.join(rel.parts).lower() + '\\'

    blob = index.read_bytes()
    doc = vdpack.parse_index(vdpack.decode_index(blob))
    seed = struct.unpack_from('<I', blob, 4)[0]
    plain = bytearray(a ^ b for a, b in zip(blob[8:], vdpack._index_keystream(seed, len(blob)-8)))
    records = {}
    for record in doc.files:
        key = record.path.replace('/', '\\').lower()
        if key in records:
            raise ValueError('index contains duplicate path: ' + record.path)
        records[key] = record
    # Match raw lines rather than decoding/re-encoding the index, preserving
    # seed, unknown encoding, whitespace and any trailing bytes exactly.
    changed, matched, skipped, added = [], [], [], []
    auto_refs = _referenced_auto_textures(folder) if add_auto_textures else set()
    seen = set()
    for file in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if file.is_symlink() or not file.is_file():
            continue
        key = prefix + file.name.lower()
        if key in seen:
            raise ValueError('ambiguous case-insensitive filenames: ' + file.name)
        seen.add(key)
        record = records.get(key)
        if record is None:
            if file.name.lower() not in auto_refs:
                skipped.append(file.name)
                continue
            path = '\\'.join(rel.parts + (file.name,))
            if any(c in path for c in '\"\r\n'):
                raise ValueError('unsafe index filename: ' + file.name)
            try:
                path_bytes = path.encode('ascii')
            except UnicodeEncodeError:
                raise ValueError('cannot safely add a non-ASCII index path: ' + path)
            data = file.read_bytes()
            md5, sha = hashlib.md5(data).hexdigest(), hashlib.sha256(data).hexdigest()
            end = len(plain.rstrip(b'\x00'))
            if end and plain[end - 1] != 10:
                raise ValueError('index has no final newline; cannot safely append')
            newline = b'\r\n' if b'\r\n' in plain else b'\n'
            line = (b'File = "' + path_bytes + b'", "' + md5.encode('ascii') +
                    b'", "' + sha.encode('ascii') + b'";' + newline)
            plain[end:end] = line
            added.append(file.name)
            continue
        old = ('"' + record.md5 + '", "' + record.sha256 + '"').encode('ascii')
        # Path may use non-ASCII; find the complete line through the same
        # encoding as the original document, then replace only its digests.
        hits = []
        for line in plain.splitlines(keepends=True):
            if old not in line:
                continue
            for enc in ('utf-8', 'cp1251', 'latin-1'):
                try:
                    line_text = line.decode(enc).strip('\r\n')
                    parsed = vdpack.parse_index(line_text)
                    if len(parsed.files) == 1 and parsed.files[0].path == record.path:
                        hits.append(line)
                        break
                except (UnicodeError, ValueError):
                    pass
        if len(hits) != 1 or plain.count(hits[0]) != 1:
            raise ValueError('cannot uniquely identify index record: ' + record.path)
        data = file.read_bytes()
        md5, sha = hashlib.md5(data).hexdigest(), hashlib.sha256(data).hexdigest()
        replacement = ('"' + md5 + '", "' + sha + '"').encode('ascii')
        matched.append(file.name)
        if replacement != old:
            plain = plain.replace(hits[0], hits[0].replace(old, replacement, 1), 1)
            changed.append(file.name)
    if not matched and not added:
        raise ValueError('no index entries matched files in ' + str(folder))
    if not changed and not added:
        return {'matched': matched, 'changed': changed, 'added': added, 'skipped': skipped, 'backup': None}
    output = blob[:8] + bytes(a ^ b for a, b in zip(plain, vdpack._index_keystream(seed, len(plain))))
    check = {r.path.replace('/', '\\').lower(): r for r in vdpack.parse_index(vdpack.decode_index(output)).files}
    if len(check) != len(doc.files) + len(added):
        raise ValueError('index record count changed unexpectedly')
    for name in matched + added:
        data = (folder / name).read_bytes()
        record = check[prefix + name.lower()]
        if (record.md5, record.sha256) != (hashlib.md5(data).hexdigest(), hashlib.sha256(data).hexdigest()):
            raise ValueError('file changed during index update: ' + name)
    # Fail rather than overwrite an index changed while hashes were computed.
    if index.read_bytes() != blob:
        raise ValueError('index changed during update; try again')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup = index.with_name(index.name + '.backup_' + stamp)
    with backup.open('xb') as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix='.d3index_', dir=str(index.parent))
        with os.fdopen(fd, 'wb') as fh:
            fh.write(output)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, index.stat().st_mode)
        if index.read_bytes() != blob:
            raise ValueError('index changed during update; backup retained')
        os.replace(tmp, index)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return {'matched': matched, 'changed': changed, 'added': added, 'skipped': skipped, 'backup': str(backup)}
