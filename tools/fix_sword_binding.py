"""Make the exported sword block's node identity file-consistent.

Why this script exists
----------------------
The add-on writes the sword as a fifth mesh block in
``character_empire_angel.g``.  The block itself is fine (the container's mesh
count is patched by :mod:`d3tool.gfile`, so the engine parses it and the sword
is visible), but two of its *node* attributes are exporter placeholders:

``dwNode`` / ``dwParent``
    :func:`d3tool.cli._default_attrs` hardcodes ``dwNode 375048704`` and
    ``dwParent 55867360``.  Every part of the Angel that came from retail
    shares one parent id (``53704704``, the file's own ``Scene Root`` node) --
    and so do all 376 compound parts of the retail corpus.  The sword block
    alone points at a parent that does not exist in the file, so it never joins
    the model's node tree and the engine draws it as a loose object instead of
    a bone-driven part.
``vdshader``
    Every retail weapon block carries ``vdshader`` (``dif_light2x``), the
    export's sword block carries none: the attribute is what selects the
    skinned vertex shader.
``attr "dwNode"`` in the ``.scene``
    The scene's ``gobj`` repeats the block's attribute block; the export left
    the placeholder in the ``.g`` and the retail Knight's value in the scene.
    Retail keeps the two in agreement, so the scene's copy follows the block.

Nothing else changes: the vertex layout (``weights_on_vertex 2`` -- legal, the
retail Imperial Knight spear is the same layout), the geometry, the bind
matrix, the LOD files and ``_lod.ac`` all stay exactly as the export wrote
them.  ``--lod`` additionally gives the LOD mesh the same sword block, which is
what the game's battle camera needs.

Usage::

    python tools/fix_sword_binding.py google_drive/ruru.vdpack \\
        -o deliver/ruru_sword9.vdpack
    python tools/fix_sword_binding.py google_drive/ruru.vdpack --lod \\
        -o deliver/ruru_sword9lod.vdpack
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import re
import sys
from typing import Dict, List, Tuple

from d3tool import gfile, vdpack

ANGEL = "resources\\characters\\empire\\angel\\"
FULL_G = ANGEL + "character_empire_angel.g"
LOD_G = ANGEL + "character_empire_angel_lod.g"
FULL_SCENE = ANGEL + "character_empire_angel.scene"
LOD_SCENE = ANGEL + "character_empire_angel_lod.scene"

SWORD = "empire_knight_sword"
LOD_SWORD = "lod_empire_knight_sword"
VD_SHADER = "dif_light2x"


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
def _items(part) -> List[Tuple[str, str]]:
    return [(k, v) for k, v in part.attr_items]


def _set(items: List[Tuple[str, str]], key: str, value: str) -> None:
    for i, (k, _) in enumerate(items):
        if k == key:
            items[i] = (key, value)
            return
    items.append((key, value))


def rigid(part):
    """Re-shape a weapon block as a retail rigid weapon: one 40-byte record.

    Retail stores every single-bone weapon as ``weights_on_vertex 1`` (151 of
    them in the corpus); the export writes the two-influence record instead.
    """
    from d3tool.model import MeshPart

    items = _items(part)
    part = MeshPart(
        name=part.attrs.get("name", ""),
        vertex_count=len(part.vertices),
        tri_count=len(part.indices) // 3,
        vertices=part.vertices,
        indices=part.indices,
        bones=list(part.bones),
        weights_on_vertex=1,
        attr_items=[(k, "1" if k == "weights_on_vertex" else v) for k, v in items],
        attrs=None,
        material_diffuse=part.material_diffuse,
        vertex_magic=part.vertex_magic,
        part_prefix=part.part_prefix,
        part_tail=part.part_tail,
        lm_uv=part.lm_uv,
    )
    part.attrs = dict(part.attr_items)
    part.raw = b""
    return part


def fix_part(part, *, name: str | None = None, parent: str | None = None):
    """Point a weapon block at the file's own node tree, retail-shaped."""
    items = _items(part)
    if parent is not None:
        _set(items, "dwParent", parent)
    _set(items, "vdshader", VD_SHADER)
    if name is not None:
        _set(items, "name", name)
    part.attr_items = items
    part.attrs = dict(items)
    if name is not None:
        part.name = name
    part.raw = b""                    # the writer keeps parsed blocks verbatim
    return part


def fix_full(data: bytes, use_rigid: bool = False) -> Tuple[bytes, object]:
    mesh = gfile.parse_geometry_file(data)
    if mesh.parse_error:
        raise SystemExit(f"cannot parse the full .g: {mesh.parse_error}")
    parts = gfile.iter_parts(mesh)
    sword = parts[-1]
    if sword.attrs.get("name") != SWORD:
        raise SystemExit(f"the export's last block is {sword.attrs.get('name')!r}")
    parent = parts[0].attrs.get("dwParent")
    if not parent:
        raise SystemExit("the export's first block has no dwParent")
    if use_rigid:
        sword = rigid(sword)
    fix_part(sword, parent=parent, name=SWORD)
    mesh.parts[-1] = sword
    mesh.raw = b""                                # force a rewrite of the blocks
    return gfile.write_geometry_file(mesh, dict(mesh.attrs)), sword


def add_lod(data: bytes, full_sword) -> Tuple[bytes, int]:
    """Append the sword block to the LOD mesh; returns ``(bytes, index)``."""
    lod = gfile.parse_geometry_file(data)
    if lod.parse_error:
        raise SystemExit(f"cannot parse the LOD .g: {lod.parse_error}")
    parts = gfile.iter_parts(lod)
    if any(p.attrs.get("name") in (SWORD, LOD_SWORD) for p in parts):
        raise SystemExit("the LOD .g already carries a sword")
    appended = fix_part(copy.copy(full_sword), name=LOD_SWORD)
    appended.raw = gfile._part_block(appended)     # append_parts copies raw bytes
    out = gfile.append_parts(data, [appended])
    # the export also ships a stale part header in the LOD mesh
    # (``lod_empire_angel``: 1869 vertices declared, 1895 in the data); the
    # engine trusts the header and desynchronises everything behind it
    repaired, fixes = gfile.repair_counts(out)
    if fixes:
        print(f"repaired LOD counts: {fixes}")
    return repaired, len(parts)


# --------------------------------------------------------------------------- #
# scene
# --------------------------------------------------------------------------- #
def _bounds(text: str, name: str) -> Tuple[int, int]:
    start = text.rindex("\t", 0, text.index('child gobj "%s"' % name))
    end = text.index("\n}\r\n", start) + len("\n}\r\n")
    return start, end


def _one(block: str, pattern: str, repl: str) -> str:
    out, count = re.subn(pattern, repl, block, count=1)
    if count != 1:
        raise SystemExit(f"scene: {pattern!r} did not match")
    return out


def fix_full_scene(text: str, node: str, weights_on_vertex: str) -> bytes:
    start, end = _bounds(text, SWORD)
    block = text[start:end]
    block = _one(block, r'attr "dwNode" "[0-9]+"', f'attr "dwNode" "{node}"')
    block = _one(block, r'attr "weights_on_vertex" "[0-9]+"',
                 f'attr "weights_on_vertex" "{weights_on_vertex}"')
    return (text[:start] + block + text[end:]).encode("latin-1")


def add_lod_scene(full_text: str, lod_text: str, index: int) -> bytes:
    """Insert the sword ``gobj`` into the LOD scene, retail-shaped.

    The block is lifted from the full scene's sword ``gobj`` and re-pointed at
    the LOD ``.g``: name, ``uid``, ``bonesuid`` (the LOD scene's own ``bones``
    node) and the appended mesh index.  Retail puts the weapon ``gobj`` among
    the ``Scene Root`` group's children, in mesh-block order (the Knight's LOD
    scene lists ``lod_empire_knight_sword`` right after the ``bones`` node
    because the sword is that mesh's block 0).
    """
    start, end = _bounds(full_text, SWORD)
    block = full_text[start:end]
    block = block.replace("\r\n", "\n").replace("\n", "\r\n")   # retail CRLF
    if not block.endswith("}\r\n"):
        raise SystemExit("could not cut the sword gobj out of the full scene")
    block = _one(block, r'child gobj "empire_knight_sword"', f'child gobj "{LOD_SWORD}"')
    block = _one(block, r'attr "name" "empire_knight_sword"', f'attr "name" "{LOD_SWORD}"')
    block = _one(block, r'character_empire_angel\.g"', 'character_empire_angel_lod.g"')
    block = _one(block, r'(\.g" )[0-9]+ ', rf'\g<1>{index} ')

    used = {int(m) for m in re.findall(r"\tuid ([0-9]+) ", lod_text)}
    block = _one(block, r'\tuid [0-9]+ [0-9]+', f'\tuid {max(used) + 1} 204017008')

    bones_uid = None
    for line in lod_text[lod_text.index("child bones"):].splitlines():
        if line.startswith("\tuid "):
            bones_uid = line.strip()[4:]
            break
    if bones_uid is None:
        raise SystemExit("the LOD scene has no bones uid")
    block = _one(block, r'bonesuid [0-9]+ [0-9]+', f'bonesuid {bones_uid}')

    close = "}\r\n\r\n"                         # the Scene Root group's brace
    if not lod_text.endswith(close):
        raise SystemExit("unexpected LOD scene tail")
    return (lod_text[: -len(close)] + block + "\r\n" + close).encode("latin-1")


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def build(source: str, out_path: str, with_lod: bool,
          use_rigid: bool = False) -> Dict[str, object]:
    pack = vdpack.read_vdpack(source)
    payloads: Dict[str, bytes] = {
        e.name.replace("/", "\\").lower(): pack.payload(e) for e in pack.entries
    }

    def get(name: str) -> bytes:
        try:
            return payloads[name.lower()]
        except KeyError:
            raise SystemExit(f"{source} does not carry {name}") from None

    full_g, sword = fix_full(get(FULL_G), use_rigid)
    node = sword.attrs["dwNode"]
    files = dict(payloads)
    files[FULL_G.lower()] = full_g
    files[FULL_SCENE.lower()] = fix_full_scene(
        get(FULL_SCENE).decode("latin-1"), node,
        sword.attrs["weights_on_vertex"])

    if with_lod:
        lod_g, index = add_lod(get(LOD_G), sword)
        files[LOD_G.lower()] = lod_g
        files[LOD_SCENE.lower()] = add_lod_scene(
            files[FULL_SCENE.lower()].decode("latin-1"),
            get(LOD_SCENE).decode("latin-1"),
            index,
        )

    blob, extras = vdpack.pack_like(pack, files)
    if extras:
        raise SystemExit(f"paths with no slot in the archive: {extras}")
    open(out_path, "wb").write(blob)

    report = {
        "out": out_path,
        "size": len(blob),
        "md5": hashlib.md5(blob).hexdigest(),
        "changed": sorted(k for k, v in files.items() if v != payloads.get(k)),
        "node": node,
        "parent": sword.attrs["dwParent"],
        "weights_on_vertex": sword.attrs["weights_on_vertex"],
        "sword_bones": [b.name for b in sword.bones],
    }
    return report


def verify(out_path: str, with_lod: bool = False) -> None:
    """Re-open the built pack and re-check everything the game reads."""
    from d3tool.vdpack import read_vdpack

    pack = read_vdpack(out_path)
    payloads = {e.name.replace("/", "\\").lower(): pack.payload(e) for e in pack.entries}
    full = payloads[FULL_G.lower()]
    mesh = gfile.parse_geometry_file(full)
    parts = gfile.iter_parts(mesh)
    assert not gfile.count_mismatches(mesh), gfile.count_mismatches(mesh)
    assert len(parts) == gfile.mesh_count(full)[1], "container count"
    sword = parts[-1]
    assert sword.attrs["name"] == SWORD, sword.attrs["name"]
    assert sword.attrs["dwParent"] == parts[0].attrs["dwParent"], sword.attrs["dwParent"]
    assert sword.attrs.get("vdshader"), "no vdshader"
    scene = payloads[FULL_SCENE.lower()].decode("latin-1")
    start, end = _bounds(scene, SWORD)
    block = scene[start:end]
    assert f'attr "dwNode" "{sword.attrs["dwNode"]}"' in block, "scene node id"
    if with_lod:
        lod = payloads[LOD_G.lower()]
        lod_mesh = gfile.parse_geometry_file(lod)
        lod_parts = gfile.iter_parts(lod_mesh)
        assert not gfile.count_mismatches(lod_mesh), gfile.count_mismatches(lod_mesh)
        assert len(lod_parts) == gfile.mesh_count(lod)[1], "LOD container count"
        assert lod_parts[-1].attrs["name"] == LOD_SWORD
    print("verify: ok")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--rigid", action="store_true",
                    help="rewrite the sword block as a retail rigid weapon (w=1)")
    ap.add_argument("--lod", action="store_true",
                    help="also give the LOD mesh the sword block and gobj")
    args = ap.parse_args(argv)
    report = build(args.source, args.out, args.lod, args.rigid)
    for key, value in report.items():
        print(f"{key}: {value}")
    verify(args.out, args.lod)
    return 0


if __name__ == "__main__":
    sys.exit(main())
