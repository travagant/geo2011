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

# one command: recursively convert every .g, with animations and textures
python3 -m d3tool export-all Neutrals -o gltf

# structural self-check of a glTF
python3 -m d3tool validate out/unit.gltf

# inspect a .g
python3 -m d3tool import Neutrals/AirElemental/character_neutrals_airelemental.g

# convert the native .t texture to a .dds (or back)
python3 -m d3tool texture convert Neutrals/AirElemental/character_neutrals_airelemental.t \
  -o out/character_neutrals_airelemental.dds
python3 -m d3tool texture info Neutrals/AirElemental/character_neutrals_airelemental.t

# run the tests
python3 tests/test_d3tool.py
```

### Portable release (no install needed)

`release/build.sh` assembles a **self-contained zipapp** folder at
`release/d3tool-dist/` — a single portable executable plus launchers for
Windows, Linux and macOS.  It needs only Python 3.8+ on the `PATH` (no native
binary; PyInstaller cannot build in this environment because the shared
libpython is missing).  Rebuild it after editing `d3tool/`:

```bash
bash release/build.sh
```

Then run it with any Python 3.8+:

```bash
release/d3tool-dist/d3tool --help           # Linux/macOS, or ./run.sh
release/d3tool-dist/d3tool.bat --help       # Windows
```

The folder is ignored by Git (`release/d3tool-dist/`); `release/build.sh` is
tracked so the bundle can always be reproduced from source.

### Standalone executable (a real `.exe`)

If the target machine has no Python at all, build a single self-contained
binary with PyInstaller — PyInstaller is installed automatically if missing:

```bash
python3 release/build_exe.py        # Linux / macOS
release\build_exe.bat               # Windows: double-click or terminal
```

This produces `release/d3tool-dist-exe/d3tool.exe` (no `.exe` suffix on
Linux/macOS), smoke-tests it with `--version` and prints usage.  The output
folder is ignored by Git.  Note: PyInstaller needs the shared libpython —
the python.org Windows installer ships it, while this repo's CI sandbox does
not, so the binary is built on demand, not committed.

## The CLI interface

The friendly interface (`d3tool/ui.py`) draws a banner, section headers, a
per-command result table, and status checkmarks (✔) / failures (✖), with ANSI
colour when the terminal supports it.  It is colour-aware and degrades
gracefully to plain text when piped.

## What the reverse-export produces

For `character_neutrals_airelemental.gltf` the exporter writes:

* `character_neutrals_airelemental.g` — the GM geometry (positions, normals,
  UVs, indices, skeleton as inverse-bind matrices, skin weights).
* `character_neutrals_airelemental.scene` — scene tree referencing the `.g` and
  `.ac`.  When the source unit folder already has a `.scene`, the exporter
  reuses it verbatim (so its particle emitters and GUI camera are preserved);
  otherwise a faithful, particle-free scene is generated.
* `character_neutrals_airelemental.ac` — animation config (states, frame
  ranges, links).
* `character_neutrals_airelemental_iadd.a` — the animation binary rebuilt from
  the glTF animation channels; per-frame values match the original.
* `character_neutrals_airelemental.t` — the native GM texture, converted from
  the `.dds` the glTF references.  The `.g`'s `material0_diffuse` is pointed at
  it.

Forward-export is the mirror image: `export-gl` auto-detects the material
diffuse from the `.g`, converts the native `.t` to a `.dds` (matching dis3tool)
and references it in the glTF.

For a whole asset tree, `export-all <folder> -o <output>` recursively finds all
`.g` files and preserves their relative folder layout in the output. It also
selects each model's `.a` animation from its `.ac` file or conventional filename
and exports textures automatically. Pass `--no-anim` for geometry-only output.

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

**Validator status (Khronos glTF-Validator):** every forward-exported glTF from
a bundled `.g`/`.a` now validates with **0 errors**, and every reverse-export
round-trip (glTF → `.g`/`.a` → glTF) also validates with **0 errors** across all
6 units.  Remaining messages are warnings only — `NODE_EMPTY` (empty skeleton
leaf nodes, present in the reference too) and `ACCESSOR_JOINTS_USED_ZERO_WEIGHT`
(a joint index sitting in a zero-weight slot, which dis3tool's own export
leaves in place).  These are informational, not defects.

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
* `.t` textures are a 59-byte GM header wrapping the same DXT payload a `.dds`
  stores after its 128-byte header.  `d3tool/texture.py` converts `.t` ↔ `.dds`
  losslessly (format codes 6/7/8 → DXT1/DXT3/DXT5; code 3 → 16-bit A1R5G5B5).

See `docs/FORMATS.md` for details.
