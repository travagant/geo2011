#!/usr/bin/env python3
"""Point one mesh part (and its `.scene` node) at a different diffuse texture.

    python3 tools/retexture_part.py --folder Empire/Inquisitor \\
        --part weapon_empire_inquisitor \\
        --diffuse weapon_empire_knight_sword_diffuse.tga \\
        --texture Empire/Knight/weapon_empire_knight_sword_diffuse.t

A unit's material name lives in **three** places that have to agree: the
`material0_diffuse` attribute of the part's block in the main `.g`, the same
attribute in the `_lod.g`, and the `attr "material0_diffuse"` of the matching
`gobj` in both `.scene` files.  Editing the mesh in Blender only changes the
first one (and only when the object's own material differs from the donor
part's); the scene keeps the donor's name and the engine keeps drawing the old
texture.  This rewrites all of them, so the part and its texture stay in step.

The part is matched by name, `lod_`-prefixed variants included.  ``--texture``
copies a `.t` into the folder under the new name, which is what makes the unit
self-contained (the engine matches material to file by stem: ``x.tga`` ->
``x.t``).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from d3tool import gfile  # noqa: E402
from d3tool import ui  # noqa: E402


def _rewrite_g(path: str, names, diffuse: str) -> int:
    data = open(path, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    parts = list(gfile.iter_parts(mesh))
    # the part blocks are the tail of the file; the prelude in front of them
    # must be cut at the ORIGINAL length, or a renamed material (whose name
    # string changes length) shifts the prelude and the file desynchronises
    prelude = data[:len(data) - sum(len(part.raw) for part in parts)]
    touched = 0
    for part in parts:
        if part.name not in names:
            continue
        items = [(key, diffuse if key == "material0_diffuse" else value)
                 for key, value in (part.attr_items or [])]
        if not items:
            raise SystemExit(f"{path}: part {part.name!r} has no attribute "
                             f"block to rewrite")
        part.attr_items = items
        part.attrs = dict(items)
        part.material_diffuse = diffuse
        part.raw = gfile._part_block(part)
        touched += 1
    if not touched:
        return 0
    # rebuild exactly as the file was: prelude + part blocks, byte for byte
    body = b"".join(part.raw for part in parts)
    out = prelude + body
    check = gfile.parse_geometry_file(out)
    if check.parse_error or [p.name for p in gfile.iter_parts(check)] != \
            [p.name for p in parts]:
        raise SystemExit(f"{path}: the rewritten file does not re-parse "
                         f"({check.parse_error}) — refusing to write it")
    open(path, "wb").write(out)
    return touched


def _rewrite_scene(path: str, names, diffuse: str) -> int:
    with open(path, "r", encoding="latin-1", newline="") as fh:
        text = fh.read()
    touched = 0
    for name in names:
        for quoted in (f'gobj "{name.lstrip("lod_")}"',
                       f'gobj "lod_{name.lstrip("lod_")}"'):
            start = text.find(quoted)
            if start < 0:
                continue
            end = text.find("\n}", start)
            if end < 0:
                raise SystemExit(f"{path}: unterminated gobj block for {name}")
            block = text[start:end]
            new_block, n = re.subn(
                r'attr "material0_diffuse" "[^"]*"',
                f'attr "material0_diffuse" "{diffuse}"', block)
            if n and new_block != block:
                text = text[:start] + new_block + text[end:]
                touched += 1
    if touched:
        with open(path, "w", encoding="latin-1", newline="") as fh:
            fh.write(text)
    return touched


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--folder", required=True, help="the unit folder")
    ap.add_argument("--part", required=True,
                    help="mesh part name, without the lod_ prefix")
    ap.add_argument("--diffuse", required=True,
                    help='the material name to write, e.g. "x.tga"')
    ap.add_argument("--texture", default="",
                    help="a .t/.dds to copy in under <diffuse stem>.t")
    args = ap.parse_args(argv)

    stem = os.path.splitext(os.path.basename(args.diffuse))[0]
    names = {args.part, "lod_" + args.part}
    base = args.part
    for name in os.listdir(args.folder):
        if name.lower().endswith(".g"):
            path = os.path.join(args.folder, name)
            n = _rewrite_g(path, names, args.diffuse)
            if n:
                ui.ok(f"{name}: {n} part(s) now name {args.diffuse}")
                base = name[:-2]
    for name in os.listdir(args.folder):
        if name.lower().endswith(".scene"):
            path = os.path.join(args.folder, name)
            n = _rewrite_scene(path, {args.part}, args.diffuse)
            if n:
                ui.ok(f"{name}: {n} gobj material(s) now name {args.diffuse}")
    if args.texture:
        src = args.texture
        dst = os.path.join(args.folder, stem + ".t")
        if src.lower().endswith(".t"):
            shutil.copyfile(src, dst)
        elif src.lower().endswith(".dds"):
            sys.path.insert(0, os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            from d3tool import texture as texmod
            texmod.convert_file(src, dst)
        else:
            raise SystemExit(f"{src}: expected a .t or .dds")
        ui.ok(f"{os.path.basename(dst)}: {os.path.getsize(dst)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
