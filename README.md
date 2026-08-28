# geo2011 — Disciples 3 / dis3tool reverse-export tooling

This repository contains the study of the **dis3tool**/`geo2011.dle` plugin for
the old 3ds Max that imports/exports original Disciples 3 (GM engine) assets,
the bundled `Neutrals/*` unit files (glTF + original `.g`/`.scene`/`.ac`), and a
Python toolkit (`d3tool/`) that can:

* **read** a dis3tool glTF export, and
* **reverse-export** it back into the original Disciples 3 format
  (`.g` geometry, `.scene` scene, `.ac` animation config, `.a` animation),
  and
* **forward-export** `.g`/`.a` back into a viewable glTF (for cross-checking).

The point is to support a future program that, like dis3tool, exports glTF but
**also** needs the inverse direction (glTF → original format).

## Contents

| path | what it is |
|---|---|
| `geo2011.dle` | the original 3ds Max plugin (binary). Strings reveal it is the `GM (R) File Exporter` / `GM MAX (R) File Format Exporter`. |
| `Neutrals/*` | bundled units: `character_*.g` (geometry), `*.gltf`/`*.bin` (dis3tool export), `*.scene`, `*.ac`, `Aliases/*`, textures. |
| `docs/FORMATS.md` | reverse-engineering notes for `.g`, `.gltf`, `.ac`, `.scene`. |
| `d3tool/` | the Python toolkit (bidirectional: glTF↔original format). |
| `tests/` | self-contained tests. |

## Install / run

Pure Python 3 (no third-party dependencies).  Install as a package to get the
`d3tool` console script, or run it as a module.

```bash
pip install -e .          # provides the `d3tool` console script (or just `python3 -m d3tool`)
# analyze a unit folder
python3 -m d3tool analyze Neutrals/AirElemental

# reverse export: glTF -> original .g / .scene / .ac / .a
python3 -m d3tool export Neutrals/AirElemental/character_neutrals_airelemental.gltf -o out

# forward export: original .g/.a -> glTF (viewer-ready)
python3 -m d3tool export-gl \
  Neutrals/AirElemental/character_neutrals_airelemental.g \
  -a Neutrals/AirElemental/character_neutrals_airelemental_iadd.a \
  -o out/unit.gltf

# structural self-check of a glTF
python3 -m d3tool validate out/unit.gltf

# inspect a .g
python3 -m d3tool import Neutrals/AirElemental/character_neutrals_airelemental.g

# run the tests
python3 tests/test_d3tool.py
```

## What the reverse-export produces

For `character_neutrals_airelemental.gltf` the exporter writes:

* `character_neutrals_airelemental.g` — the GM geometry (positions, normals,
  UVs, indices, skeleton as inverse-bind matrices, skin weights).
* `character_neutrals_airelemental.scene` — scene tree referencing the `.g` and
  `.ac`.
* `character_neutrals_airelemental.ac` — animation config (states, frame
  ranges, links).
* `character_neutrals_airelemental_iadd.a` — the animation binary rebuilt from
  the glTF animation channels; per-frame values match the original.

For the AirElemental unit the generated `.g` matches the original in positions,
normals, UVs, triangle indices, bone names/matrices and material.  The only
known divergence is the first bone index of 21 vertices that have a zero-weight
first influence slot (dis3tool fills an extra joint there; irrelevant to the
pose because the weight is 0).

**Round-trip coverage:** `parse → write` reproduces the original bytes exactly
for **all 11** bundled `.g` files and **all 7** bundled `.a` files.  The `.g`
writer handles `w = 2/3/4` character meshes and also passes compound files
(e.g. the Zombie LOD, which stacks a weapon mesh and a character LOD mesh)
through verbatim so nothing is lost.

## Key format facts (summary)

* `.g` and glTF `skins[0].inverseBindMatrices` are identical, in the same
  order; joint names match.
* `.g` vertex records are `40 + 4*(w-1) + w` bytes for `w = weights_on_vertex`
  (2/3/4 → 46/51/56).  They store position, normal, diffuse, uv, the first
  `w-1` weights (last weight implied) and `w` bone indices, prefixed by a
  per-file 4-byte magic.
* dis3tool preserves glTF influence order — it does **not** re-sort weights.
* `.ac`/`.scene` are text formats (simple to generate once the names/paths are
  known).
* `.a` animation binaries hold per-bone descriptors + per-frame TRS
  (quat + translation).  The `.a` record set equals the set of nodes animated
  in the glTF, so the animation can be rebuilt from the glTF; per-frame values
  round-trip exactly.

See `docs/FORMATS.md` for details.
