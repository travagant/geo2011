"""Build the Angel's empire-knight sword pack from a working export.

Why this script exists
----------------------
An add-on export of the Angel writes the sword mesh into
``character_empire_angel.g`` as a fifth mesh block but leaves the *container
header* saying four blocks: the header closes its node chain with
``[1][4][count]`` and ``count`` is the number of mesh blocks that follow.  The
engine reads exactly that many, so the sword sits in the file, byte-perfect,
and is never parsed -- "the add-on exports the mesh but the game does not read
the sword out of the model".  Every retail ``.g`` in the corpus agrees
(``d3tool`` reports ``count == len(iter_parts(mesh))`` for 247/247 files).

So the pack is rebuilt from the user's own (loading) export with three
changes and nothing else:

``character_empire_angel.g``
    the sword block is rewritten as a *retail rigid weapon* (one influence,
    one bone, 40-byte vertex records) and the mesh count is bumped to 5.
``character_empire_angel_lod.g``
    the same block is appended as ``lod_empire_knight_sword`` and the count is
    bumped to 5 -- retail ships the weapon in *both* meshes
    (``character_empire_knight_lod.g`` part 0 is the LOD sword), and a battle
    camera that switches to the LOD model otherwise loses the sword again.
``character_empire_angel_lod.scene``
    the matching ``gobj`` is added inside the ``bones`` node, pointing at mesh
    index 4 of the LOD ``.g``, exactly like the retail Knight's LOD scene.
``character_empire_angel.scene``
    the sword ``gobj`` already exists in the export; only its
    ``weights_on_vertex`` echo is corrected to match the rewritten block.

Everything else -- animations, textures, aliases, ``index``, the aliased
``_lod.ac`` -- is copied byte for byte from the source pack.

Usage::

    python tools/build_sword_pack.py google_drive/ruru.vdpack \\
        -o deliver/ruru_sword8.vdpack
"""
from __future__ import annotations

import argparse
import copy
import re
import hashlib
import sys
from typing import Dict, List, Tuple

from d3tool import gfile, vdpack
from d3tool.model import MeshPart

ANGEL = "resources\\characters\\empire\\angel\\"
FULL_G = ANGEL + "character_empire_angel.g"
LOD_G = ANGEL + "character_empire_angel_lod.g"
FULL_SCENE = ANGEL + "character_empire_angel.scene"
LOD_SCENE = ANGEL + "character_empire_angel_lod.scene"
LOD_AC = ANGEL + "character_empire_angel_lod.ac"

SWORD = "empire_knight_sword"
LOD_SWORD = "lod_empire_knight_sword"
BONE = "Bip01 R Hand"
VD_SHADER = "dif_light2x"


def _sword_part(parts, name: str, extra_attrs: Dict[str, str] | None = None):
    """The export's sword block, re-shaped as a retail rigid weapon.

    Retail weapon blocks are ``weights_on_vertex 1`` with ``bones_num 1`` and
    40-byte vertex records; the export writes the two-influence record
    (``weights_on_vertex 2``), a layout no retail part uses.  The geometry,
    the uv set, the indices, the bind matrix and the suffix of the block's
    opaque 84-byte prefix all stay the donor's.
    """
    sword = parts[-1]
    if not sword.bones:
        raise SystemExit("the export's last part carries no bone")
    if sword.bones[0].name != BONE:
        raise SystemExit(f"unexpected sword bone {sword.bones[0].name!r}")

    items: List[Tuple[str, str]] = []
    for key, value in sword.attr_items:
        if key == "weights_on_vertex":
            value = "1"
        elif key == "name":
            value = name
        items.append((key, value))
    have = {key for key, _ in items}
    for key, value in {**{"vdshader": VD_SHADER}, **(extra_attrs or {})}.items():
        if key not in have:
            items.append((key, value))

    return MeshPart(
        name=name,
        vertex_count=len(sword.vertices),
        tri_count=len(sword.indices) // 3,
        vertices=copy.deepcopy(sword.vertices),
        indices=list(sword.indices),
        bones=[copy.copy(sword.bones[0])],
        weights_on_vertex=1,
        attr_items=items,
        attrs=dict(items),
        material_diffuse=sword.material_diffuse,
        vertex_magic=sword.vertex_magic,
        part_prefix=sword.part_prefix,
    )


def rebuild_full_geometry(data: bytes) -> Tuple[bytes, MeshPart]:
    mesh = gfile.parse_geometry_file(data)
    if mesh.parse_error:
        raise SystemExit(f"cannot parse the full .g: {mesh.parse_error}")
    parts = gfile.iter_parts(mesh)
    if parts[-1].attrs.get("name") != SWORD:
        raise SystemExit("the export's last part is not the sword")
    rebuilt = _sword_part(parts, SWORD)
    mesh.parts[-1] = rebuilt
    # the parsed bytes only round-trip verbatim while nothing is edited
    mesh.raw = b""
    out = gfile.write_geometry_file(mesh, dict(mesh.attrs))
    return out, rebuilt


def append_lod_geometry(data: bytes, full: MeshPart) -> Tuple[bytes, MeshPart, int]:
    lod = gfile.parse_geometry_file(data)
    if lod.parse_error:
        raise SystemExit(f"cannot parse the LOD .g: {lod.parse_error}")
    parts = gfile.iter_parts(lod)
    if any(p.attrs.get("name") in (SWORD, LOD_SWORD) for p in parts):
        raise SystemExit("the LOD .g already carries a sword")
    part = _sword_part([full], LOD_SWORD)
    part.raw = gfile._part_block(part)          # append_parts copies raw bytes
    out = gfile.append_parts(data, [part])
    return out, part, len(parts)


def lod_scene(full_scene: str, lod_scene_text: str, triangles: int,
              vertices: int) -> bytes:
    """Add the sword ``gobj`` to the LOD scene, retail-shaped.

    The block is lifted from the full scene's sword ``gobj`` -- same flags,
    ``tech``, shaders and attribute block -- and re-pointed at the LOD ``.g``:
    name, ``uid``, ``bonesuid`` (the LOD scene's own ``bones`` node) and mesh
    index 4 (the appended block).  The retail Knight's LOD scene nests it in
    exactly that place: as the last ``child gobj`` inside ``bones``.
    """
    start, end = _block_bounds(full_scene, SWORD)
    block = full_scene[start:end]
    if not block.endswith("}\r\n"):
        raise SystemExit("could not cut the sword gobj out of the full scene")

    def one(pattern: str, repl: str) -> None:
        nonlocal block
        block, count = re.subn(pattern, repl, block, count=1)
        if count != 1:
            raise SystemExit(f"LOD scene: {pattern!r} did not match")

    one(r'child gobj "empire_knight_sword"', f'child gobj "{LOD_SWORD}"')
    one(r'attr "name" "empire_knight_sword"', f'attr "name" "{LOD_SWORD}"')
    one(r'attr "weights_on_vertex" "2"', 'attr "weights_on_vertex" "1"')
    one(r'attr "material0_triangles_num" "124"',
        f'attr "material0_triangles_num" "{triangles}"')
    one(r'attr "vertexs_weights_num" "136"',
        f'attr "vertexs_weights_num" "{vertices}"')
    one(r'character_empire_angel\.g"', 'character_empire_angel_lod.g"')
    one(r'\tuid 6980 [0-9]+', '\tuid 6982 204017008')

    # the bones node's own uid -- the value the other gobjs of that scene use
    bones_at = lod_scene_text.index("child bones")
    bones_uid = None
    for line in lod_scene_text[bones_at:].splitlines():
        if line.startswith("\tuid "):
            bones_uid = line.strip()[4:]
            break
    if bones_uid is None:
        raise SystemExit("the LOD scene has no bones uid")
    one(r'bonesuid [0-9]+ [0-9]+', f'bonesuid {bones_uid}')

    suffix = "}\r\n\r\n}\r\n\r\n"                # bones node + scene root
    if not lod_scene_text.endswith(suffix):
        raise SystemExit("unexpected LOD scene tail")
    body = lod_scene_text[: -len(suffix)]
    return (body + block + "\r\n" + suffix).encode("latin-1")


def _block_bounds(text: str, name: str) -> Tuple[int, int]:
    """``(start, end)`` of a ``gobj`` block, trailing blank line included."""
    start = text.rindex("\t", 0, text.index('child gobj "%s"' % name))
    end = text.index("\n}\r\n", start) + len("\n}\r\n")
    return start, end


def full_scene(text: str) -> bytes:
    """Align the sword gobj's attribute echo with the rewritten block.

    The scene repeats each part's attribute block; retail keeps the two in
    agreement, so ``weights_on_vertex`` has to follow the part from the
    two-influence record to the retail one.  Nothing else in the scene moves.
    """
    start, end = _block_bounds(text, SWORD)
    block = text[start:end]
    if 'attr "weights_on_vertex" "2"' not in block:
        raise SystemExit("the sword gobj does not echo weights_on_vertex 2")
    fixed = block.replace('attr "weights_on_vertex" "2"',
                          'attr "weights_on_vertex" "1"')
    out = text[:start] + fixed + text[end:]
    return out.encode("latin-1")


def build(source: str, out_path: str) -> dict:
    pack = vdpack.read_vdpack(source)
    payloads: Dict[str, bytes] = {
        entry.name.replace("/", "\\").lower(): pack.payload(entry)
        for entry in pack.entries
    }

    def get(name: str) -> bytes:
        try:
            return payloads[name.lower()]
        except KeyError:
            raise SystemExit(f"{source} does not carry {name}") from None

    full_g, sword = rebuild_full_geometry(get(FULL_G))
    lod_g, lod_part, lod_parts_before = append_lod_geometry(get(LOD_G), sword)
    # the source export also ships a stale part header in the LOD mesh
    # (``lod_empire_angel``: 1869 vertices declared, 1895 in the data); retail
    # never does (247/247 files agree), and a reader that trusts the header
    # desynchronises the whole block behind it
    for label, blob in (("full", full_g), ("lod", lod_g)):
        repaired, fixes = gfile.repair_counts(blob)
        if label == "full":
            full_g = repaired
        else:
            lod_g = repaired
        if fixes:
            print(f"repaired counts ({label}): {fixes}")
    scene = full_scene(get(FULL_SCENE).decode("latin-1"))
    lod = lod_scene(get(FULL_SCENE).decode("latin-1"),
                    get(LOD_SCENE).decode("latin-1"),
                    len(sword.indices) // 3, len(sword.vertices))

    # the export writes its LOD animation config against a resource root built
    # from the *object* name (``resources\\characters\\character_empire_angel_lod``),
    # a folder no install has: every state's ``file``/``meshfile`` then asks
    # for a path that cannot resolve, so the LOD model never loads and the
    # battlefield keeps drawing the far LOD without the sword
    lod_ac = get(LOD_AC).decode("latin-1")
    lod_ac = lod_ac.replace("\\characters\\character_empire_angel_lod\\",
                            "\\characters\\empire\\angel\\")

    files = dict(payloads)
    files[FULL_G.lower()] = full_g
    files[LOD_G.lower()] = lod_g
    files[FULL_SCENE.lower()] = scene
    files[LOD_SCENE.lower()] = lod
    files[LOD_AC.lower()] = lod_ac.encode("latin-1")
    blob, extras = vdpack.pack_like(pack, files)
    if extras:
        raise SystemExit(f"paths with no slot in the archive: {extras}")

    # ---- verification ---------------------------------------------------- #
    text = gfile.parse_geometry_file(full_g)
    full_parts = gfile.iter_parts(text)
    lod_mesh = gfile.parse_geometry_file(lod_g)
    lod_parts = gfile.iter_parts(lod_mesh)
    checks = {
        "full parts": len(full_parts) == 5,
        "full mesh count": gfile.mesh_count(full_g)[1] == len(full_parts),
        "lod parts": len(lod_parts) == lod_parts_before + 1,
        "lod mesh count": gfile.mesh_count(lod_g)[1] == len(lod_parts),
        "sword rigid": full_parts[-1].weights_on_vertex == 1
                       and len(full_parts[-1].bones) == 1,
        "sword bone": full_parts[-1].bones[0].name == BONE,
        "sword shader": full_parts[-1].attrs.get("vdshader") == VD_SHADER,
        "lod sword": lod_parts[-1].attrs.get("name") == LOD_SWORD,
    }
    for label, ok in checks.items():
        if not ok:
            raise SystemExit(f"check failed: {label}")

    with open(out_path, "wb") as stream:
        stream.write(blob)
    return {
        "pack": out_path,
        "bytes": len(blob),
        "md5": hashlib.md5(blob).hexdigest(),
        "source": source,
        "changed": [FULL_G, LOD_G, FULL_SCENE, LOD_SCENE, LOD_AC],
        "full_parts": [(p.attrs.get("name"), len(p.vertices),
                        len(p.indices) // 3, p.weights_on_vertex)
                       for p in full_parts],
        "lod_parts": [(p.attrs.get("name"), len(p.vertices),
                       len(p.indices) // 3, p.weights_on_vertex)
                      for p in lod_parts],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args()
    report = build(args.source, args.out)
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
