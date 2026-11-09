#!/usr/bin/env python3
"""Rebuild an exported Inquisitor pack so it can actually load in game.

    PYTHONPATH=. python3 tools/patch_inquisitor_pack.py \
        --export /tmp/uv8 --retail Empire/Inquisitor \
        --pack google_drive/ruru.vdpack -o deliver/ruru_inq_fix.vdpack

`:mod:`d3tool` writes a unit from a Blender scene, so what comes out of the
export is the *scene*, not the retail asset: the body mesh is re-triangulated,
the animation stream holds the bones a mesh is weighted to, the `.scene`/
`.ac` text is re-emitted with the writer's line endings, and an appended
weapon part names whichever texture the source object carried.  Each of those
is harmless on its own except the last two, and this script puts the pack
back on the retail baseline while keeping the export's own weapon geometry:

* the body/LOD blocks and both scene files come from the retail asset — the
  export's re-triangulated body differed from retail in 3699/4660 vs
  3693/4664 vertices/triangles for no gain;
* the weapon part keeps the **export's** vertices and indices but takes its
  node identity and attribute block from the retail block it replaces, so
  `dwNode`/`dwParent`/`bones_num`/`weights_on_vertex` stay exactly the ones the
  engine already resolves, and its diffuse is repointed at a texture the pack
  ships;
* `…_baseanims.a` comes from retail: the export kept 25 bone tracks where
  retail has 27, dropping `Trail1`/`Trail2`, and the `.scene`'s
  `VDGoWeaponTrace` names those bones **by index** (`boneid 11`, `boneid 12`);
* both `.scene` files and the `.ac` come from retail, which also reverts the
  CRLF rewrite the writer does.

The result is packed with ``--like`` the user's own archive, the one shape
replacement that is confirmed to load in game: same entry order, names, flags
and the opaque `index` resource.
"""
from __future__ import annotations

import argparse
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from d3tool import gfile, vdpack  # noqa: E402
from d3tool import ui  # noqa: E402

WEAPON = "weapon_empire_inquisitor"
LOD_WEAPON = "lod_" + WEAPON
TEXTURE = "weapon_empire_knight_sword_diffuse.tga"
SCENE_SWAPS = (
    ('attr "material0_diffuse" "weapon_empire_inquisitor.tga"',
     'attr "material0_diffuse" "' + TEXTURE + '"'),
    ('attr "material0_triangles_num" "250"',
     'attr "material0_triangles_num" "124"'),
    ('attr "vertexs_weights_num" "537"', 'attr "vertexs_weights_num" "136"'),
)


def split_compound(data: bytes):
    """``(prelude, [part, …])`` of a `.g`, byte-exactly."""
    parts = list(gfile.iter_parts(gfile.parse_geometry_file(data)))
    body = b"".join(part.raw for part in parts)
    prelude = data[:len(data) - len(body)]
    if prelude + body != data:
        raise SystemExit("`.g` does not split back into its blocks")
    return prelude, parts


def swap_weapon(retail: bytes, sword) -> bytes:
    """The retail `.g` with its weapon block carrying ``sword``'s geometry."""
    prelude, parts = split_compound(retail)
    block = copy.copy(parts[-1])          # retail node identity + attributes
    block.vertices = list(sword.vertices)
    block.indices = list(sword.indices)
    items = [(key, TEXTURE if key == "material0_diffuse" else value)
             for key, value in block.attr_items]
    block.attr_items = items
    block.attrs = dict(items)
    block.raw = gfile._part_block(block)  # counts patched from the new data
    return prelude + parts[0].raw + block.raw


def patch_scene(text: str, keep_material: bool = False) -> str:
    """Retail's scene text with the weapon node's material/counts updated."""
    head = 'child gobj "' + WEAPON + '"'
    if head not in text:
        head = 'child gobj "' + LOD_WEAPON + '"'
    start = text.index(head)
    end = text.index("\n}", start)
    block = text[start:end]
    swaps = SCENE_SWAPS[1:] if keep_material else SCENE_SWAPS
    for old, new in swaps:
        if old not in block:
            raise SystemExit(f"scene block does not hold {old!r}")
        block = block.replace(old, new)
    return text[:start] + block + text[end:]


def read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", required=True,
                    help="the extracted export, e.g. /tmp/uv8")
    ap.add_argument("--retail", default="Empire/Inquisitor",
                    help="retail unit folder the export was made from")
    ap.add_argument("--pack", required=True,
                    help="the .vdpack to rebuild in the shape of")
    ap.add_argument("--texture", default="",
                    help="a .t to ship as the sword's diffuse (defaults to "
                         "the one the shape archive carries for the Angel)")
    ap.add_argument("--keep-material", action="store_true",
                    help="leave the weapon node on the texture the retail "
                         "scene already names (the export's own .g says "
                         "weapon_empire_inquisitor.tga) instead of "
                         "repointing it at the knight-sword diffuse")
    ap.add_argument("--tree", default="",
                    help="where to stage the patched files (default: a "
                         "temporary folder)")
    ap.add_argument("-o", "--out", default="deliver/ruru_inq_fix.vdpack")
    args = ap.parse_args(argv)

    folder = os.path.join(args.export, "resources", "Characters", "Empire",
                          "Inquisitor")
    sub = ("resources", "Characters", "Empire", "Inquisitor")
    tree = args.tree or os.path.join(
        os.path.dirname(os.path.abspath(args.out)) or ".",
        ".stage_inquisitor")
    dest = os.path.join(tree, *sub)

    his = read(os.path.join(folder, "character_empire_inquisitor.g"))
    sword = list(gfile.iter_parts(gfile.parse_geometry_file(his)))[-1]
    ui.info(f"weapon block from the export: {sword.name}, "
            f"{len(sword.vertices)} v, {len(sword.indices) // 3} tris")

    for name in ("character_empire_inquisitor.g",
                 "character_empire_inquisitor_lod.g"):
        data = swap_weapon(read(os.path.join(args.retail, name)), sword)
        write(os.path.join(dest, name), data)
        ui.ok(f"{name}: retail body + the export's sword block "
              f"({len(data)} bytes)")

    for name in ("character_empire_inquisitor.scene",
                 "character_empire_inquisitor_lod.scene"):
        with open(os.path.join(args.retail, name), encoding="latin-1",
                  newline="") as fh:
            text = fh.read()
        out = patch_scene(text, args.keep_material)
        write(os.path.join(dest, name), out.encode("latin-1"))
        ui.ok(f"{name}: retail text, weapon node counted for the export's "
              f"block" + ("" if args.keep_material
                          else f", repointed at {TEXTURE}"))

    for name in ("character_empire_inquisitor_baseanims.a",
                 "character_empire_inquisitor.ac"):
        write(os.path.join(dest, name), read(os.path.join(args.retail, name)))
        ui.ok(f"{name}: from retail")

    if args.keep_material:
        texture = ""
    else:
        texture = args.texture or os.path.join(
            args.export, "resources", "Characters", "Empire", "Angel",
            "weapon_empire_knight_sword_diffuse.t")
    if texture:
        data = read(texture)
        write(os.path.join(dest, "weapon_empire_knight_sword_diffuse.t"), data)
        ui.ok(f"weapon_empire_knight_sword_diffuse.t: {len(data)} bytes from "
              f"{os.path.basename(texture)}")

    source = vdpack.read_vdpack(args.pack)
    if source.parse_error:
        raise SystemExit(f"{args.pack}: {source.parse_error}")
    files = vdpack.pack_folder(tree)
    pack, extra = vdpack.pack_like(source, files)
    for name in extra:
        ui.warn(f"{name}: not in {os.path.basename(args.pack)} — dropped")
    write(args.out, pack)
    check = vdpack.parse_vdpack(pack)
    ui.ok(f"{args.out}: {len(pack)} bytes, {len(check)} file(s)")
    # validate what the archive *carries*, not the patch tree: the body
    # texture and everything else the export already had comes from `--pack`
    report = vdpack.reference_findings(
        {entry.name: check.payload(entry) for entry in check.entries})
    for line in report.problems:
        ui.warn(line)
    if not report.problems:
        ui.ok("every reference inside the packed tree resolves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
