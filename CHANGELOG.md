# Changelog

The Blender add-on ships as a built zip, `release/d3unit-addon/io_scene_d3unit.zip`, committed at
every release tag together with the sources it was built from. A release tag therefore always
contains the exact zip it announces — the download link never rots.

## 1.1.51 — `v1.1.51-alpha`

Built zip: 251 038 bytes, md5 `8e9e0e3ddf0694f23b9e65e8ba995c73`. `bl_info["version"]` moved from
`(1, 1, 6)` to `(1, 1, 51)`; the zip vendored `d3tool` grew with the library.

**The scene now names every part of a `.g`.** The engine draws what the `.scene` lists in its `gobj`
entries, not what the `.g` contains. The exporter reuses the unit's stock scene (to keep the particle
emitters and the GUI camera), so any part the stock scene does not name was silently invisible — the
knight's sword was in the file, textured and skinned, and never drawn. `scene.ensure_part_gobjs()`
clones the closest opaque `gobj` and rewrites name, diffuse material, triangle / weight / bone counts
and `uid`/`dwNode` for each unnamed part, and `d3tool export` warns per added part. Ten shipped units
turn out to carry parts their scene never names (Apprentice's skull, sleeve and head among them).

**Weapons.** New `d3tool/weapon.py`: puts a hand-made weapon where the reference weapon sits, instead
of posing it into the hand. The exporter writes world positions from the bind pose and binds the part
to a single bone, so a mesh shaped onto an animated frame comes back displaced by the animation.

**Audit.** New `d3tool/audit.py`: structural cross-check of a unit folder — `.scene` ↔ `.g` ↔ `.ac` ↔
`materialN_diffuse` → `.t` — because no single file of the bundle validates itself. `d3tool/parts.py`
compares the parts an export wrote with the donor the game ships, which catches the classic unapplied
`Object > Apply > Scale` (a sword 1.9× too long exports happily and only breaks on the battlefield).

**Round-trip proof.** New `d3tool/roundtrip.py` answers one question about the add-on's promise
("import, touch nothing, export, get the same bytes"): which files differ, and where.
`d3tool/weights.py` reads a unit's skin weights without opening Blender.

**Library work behind the add-on.** `.scene` reader/writer (`d3tool/scene.py`), `.vdpack` archive
work (`vdpack.py`), textures (`texture.py`), `.g` reader/writer (`gfile.py`), glTF in/out (`gltf.py`),
`anim.py`, `model.py`, and a much larger `cli.py` — 5 783 insertions over 15 files since 1.1.6.
In the add-on itself: `importer.py`, `exporter.py`, `unitprops.py` reworked, new `weapon.py`.

## 1.1.6-alpha — `v1.1.6-alpha`

First packaged add-on: import `.g`/`.a`/`.scene` into an armature with animation, export back to the
original format, with `d3tool` vendored inside the zip (158 597 bytes, md5
`38b1899d99bda228a1b17f97f03b45a4`).
