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
| `tests/test_d3tool.py` | unit tests for the individual readers/writers. |
| `tests/test_corpus.py` | corpus-wide import/export test over **all 87 units**. |
| `tests/corpus_parity.py` | benchmark: forward export vs the bundled dis3tool references. |
| `gltf/`, `out_rev/` | checked-in sample output (forward and reverse export of the AirElemental); regenerable, kept for diffing. |

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

# run the unit tests
python3 tests/test_d3tool.py

# run the corpus-wide import/export test over every bundled unit
# (~40 s: parses all 247 .g / 153 .a / 125 .ac / 98 .gltf / 751 .t / 285 .dds,
#  forward- and reverse-exports them, and checks every round-trip)
python3 tests/test_corpus.py

# or both at once, under pytest
python3 -m pytest tests/ -q

# parity benchmark against the bundled dis3tool reference exports
python3 tests/corpus_parity.py
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

The Windows launcher resolves the interpreter with `where py` / `where python`
and an explicit `if not defined` fallback, *not* a `where py && (py ...) ||
(python ...)` chain.  In cmd that chain re-runs the program whenever it exits
non-zero, so every d3tool error path (exit 1) would have executed the tool
twice and returned the second run's code.  `release/build_exe.bat` had the same
shape and would have attempted a failed PyInstaller build twice.

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
* `character_neutrals_airelemental.ac` — animation config.  When the source
  unit folder already has an `.ac`, the exporter reuses it verbatim so its
  `event2` entries survive (the attack/damage/death sound aliases and the
  `FxStrike`/`fxcast` cues — the AirElemental carries seven of them);
  otherwise a faithful five-state config is generated.
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

**Round-trip coverage** (measured by `tests/test_corpus.py` over the whole
`Empire/` + `Neutrals/` tree):

| format | files | `parse → write` byte-identical |
|---|---|---|
| `.g` | 247 | 247 (all parse structurally; 141 are compound, 2 are header-less node helpers) |
| `.a` | 153 | 153 |
| `.t` ↔ `.dds` | 751 | 751 |

The `.g` writer handles `w = 2/3/4` character meshes and also passes compound
files (e.g. the Zombie LOD, which stacks a weapon mesh and a character LOD
mesh) through verbatim so nothing is lost.

`SkinnedMesh.raw` is *not* a parse-failure flag: compound containers set it too.
A file that could not be structured at all carries `parse_error`, and
`d3tool analyze` reports it as an error (exit 1) instead of labelling it
`(compound)`.  `analyze` prints `(compound)` only when `parts` is non-empty and
`(node helper)` for the header-less `stub` form.

**Validator status (Khronos glTF-Validator):** every forward-exported glTF from
a bundled `.g`/`.a` now validates with **0 errors**, and every reverse-export
round-trip (glTF → `.g`/`.a` → glTF) also validates with **0 errors** across all
6 units.  Remaining messages are warnings only — `NODE_EMPTY` (empty skeleton
leaf nodes, present in the reference too) and `ACCESSOR_JOINTS_USED_ZERO_WEIGHT`
(a joint index sitting in a zero-weight slot, which dis3tool's own export
leaves in place).  These are informational, not defects.

**Corpus coverage.** `tests/test_corpus.py` walks all 87 unit folders.  Every
one of the 247 `.g` files forward-exports and every one of the 98 bundled
`.gltf` files reverse-exports, with two documented exceptions each:

* `Empire/Leader-Ranger` and `Empire/Leader-Thief` ship a 602-byte *node
  helper* (`.g` form `stub`: prelude first, no 120-byte header, no name
  strings, `materials_num 0`, four vertices, no index block).  A glTF for one
  would need a skin with zero joints, which is not valid glTF, so the exporter
  refuses it with an explicit message instead.
* `Blacknaga`, `CityGuard` and `WaterSnake_sea` were exported by dis3tool as
  *rigid* meshes (no `skins`, no `animations`, no `WEIGHTS_0`/`JOINTS_0`), so
  their reverse export correctly writes no `.a`.

**Parity benchmark.** `tests/corpus_parity.py` compares a forward export
against the bundled dis3tool reference, byte for byte: **10 EXACT, 69
BIN-NEAR** (differing only in a handful of ±1ulp float32 lanes) and **6
FAIL** out of 85, down from 9/46/30 at the start of this work.

**Root cause of the morph failures (measured).** 24 bundled units have an
`.ac` that references *more than one* `.a` (Angel names five: idle/attack/run/
damage/death).  dis3tool **concatenates every one of them** — verified on all
24, the reference glTF frame count equals the sum exactly (Angel
64+84+28+32+55 = 263, Golem 632, Cleric 381).  Its morph targets come from the
*last* stream, not the first: Cleric's reference has **25** targets
(`_run.a`, 25 frames), while `_iadd.a` carries 356.  Target #0 matches
`character_empire_cleric_run.a` frame 0 bit-for-bit (max diff 0.0000).

**Implemented.** `anim.concat_anims` joins the streams in `.ac` order (a bone
absent from one stream holds its rest pose for that stretch) and takes the
morph tracks from the **last** one; `_export_gl` calls it through
`cli._load_anim_stream`, and a lone stream passes through untouched so the 97
single-`.a` units are unaffected.  All 16 multi-`.a` units that have a
reference now match it on both frame count *and* morph-target count.

Two further details the reference pinned down:

* the `morph_weights` buffer is an identity matrix sized by the **total
  animation frame count**, not the target count — Cleric's is 580644 bytes =
  381×381×4 with all 145161 cells matching an identity, on a mesh with 25
  targets;
* morph tracks bind to sub-meshes **by name**, which is why DarkServant's
  `_run.a` carries three tracks but only two meshes animate (`Dinamix_hair`
  has no matching sub-mesh and is dropped).

Together these moved 14 units out of FAIL (LivingArmor to EXACT) and took the
benchmark from 9/46/30 to 10/59/16.  `tests/corpus_parity.py` now calls the
shipped loader rather than re-implementing the animation choice, so it measures
the code that actually runs.

**Single-mesh conventions, also measured rather than guessed.** All 15
single-mesh references name the mesh node, the mesh and the three
`mesh_indexes_` / `mesh_vertexes_` / `mesh_bones_` bufferViews after the `name`
*attribute* (`SkinnedMesh.unit_name`), not the binary's leading `name1` string,
which is often a leftover 3ds Max material name (`Material #35`, `07 -
Default`).  All 396 primitives across the 98 references carry `"mode": 4`; 380
of 396 materials are exactly `name / alphaMode=MASK / pbrMetallicRoughness{
baseColorTexture{index,texCoord}, metallicFactor} / emissiveFactor=[0,0,0]` and
**none** carries `doubleSided`; and 26717 of 26760 accessors declare an
explicit `byteOffset`, the only 43 that omit it being the compound writer's
`morph_weights` accessors.

**Duplicate bone names.** Three bundled `.a` files list a bone name more than
once (Wildboar has two `null_Bone_Tip`, WaterSnake five `null`).  The reference
binds the node to the **first** occurrence — verified bit-for-bit on both
units' rest rotation and translation — where a `{b.name: b for b in bones}`
comprehension keeps the last.

**The 7 remaining FAILs.** Four of them are units whose *reference* is invalid
glTF, so matching them would mean emitting invalid output:

| unit | defect in dis3tool's own file |
|---|---|
| `Empire/Rod-1` | animation sampler 14 declares `output=33` with 33 accessors (valid 0..32) |
| `Neutrals/WaterSnake` | 4 animation channels target nodes 47-50 with only 47 nodes |
| `Neutrals/Wildboar` | 1 animation channel targets node 37 with only 37 nodes |

`Neutrals/WaterSnake_sea` fails for the same reason as `WaterSnake`.
`validate_gltf` now detects all of these (it previously skipped out-of-range
channel targets silently) and flags none of d3tool's own exports.

**The remaining three are not fidelity gaps.**

*`Blacknaga`* (and `CityGuard`, `WaterSnake`) — their references are *rigid*,
but the assets are not. Measured with `Vertex.influence_weights()`: Blacknaga's
`.g` carries 38 bones and 1681 vertices with more than one non-zero weight,
CityGuard 22 / 427, WaterSnake 42 / 2896 — the same profile as any normally
skinned unit (Wolf: 33 / 1126).  Rigidness was an operator's choice at
dis3tool export time; nothing in the `.g`, the `.ac` or the file names signals
it.  d3tool therefore skins and animates them, which is the faithful reading.

*`AirElemental`* — structural parity reached; only ±1 ulp float lanes differ.
Its two extra bones (`LeftLeftHand`, `Tail02`) exist only in the *second*
animation stream (`_run.a`), which `concat_anims` appends.  The reference
emits them as the last two nodes, still lists `LeftLeftHand` under
`LeftForeArm` (`children [7, 43]`), and gives them **no channel**: its 84
channels target exactly the 42 primary bones and its buffer is
`2 * 363 * (16 + 12) = 20328` bytes smaller than one that animated them.
`node_hierarchy` now walks only the primary skeleton and trails the rest, and
the single-mesh writer allocates accessors and channels for the primary bones
alone.

**Node order (measured).**  The reference order is a depth-first walk of the
skeleton with children in `.a` record order; it matches 79 of the 83 bundled
references.  The four misses are `Blacknaga` (rigid, no bone nodes),
`WaterSnake` and `Wildboar` (duplicate bone names) and `AirElemental` (above).

*`DarkServant`* still differs — 394 JSON diffs, first at
`/meshes/1/primitives/0/targets/0/POSITION` (161 vs 163) — and the compound
writer has not yet had the same primary-bone treatment applied.  Open.

**Validator note.** `validate_gltf` accepts *both* morph-weight output shapes:
the spec's `input.count * len(targets)` and dis3tool's `input.count ** 2`
(which `_write_compound_gltf` replicates for byte parity).  Requiring only the
former made the validator reject 8 of the 98 bundled reference glTFs — i.e.
ground truth.  One reference still fails, correctly: `Empire/Rod-1` declares
animation sampler 14 `output=33` while its document has 33 accessors, so the
index is out of range in dis3tool's own file.

**Sound aliases.** The corpus holds 1294 `.alias` files, referenced from the
`.ac` `event2` entries.  Two things worth knowing: the files on disk are
lowercase (`attack00.alias`) while the references are CamelCase
(`Attack00.alias`), so any resolver must be case-insensitive; and 261 of the
2065 references point at resources outside this repository (shared
`resources/sounds/alias/*` and cross-unit folders such as
`Characters/elves/scout/`).  d3tool does not *resolve* alias paths, but a
reverse export now copies the unit's own `Aliases/` folder alongside the `.ac`
that references it, so the exported unit is self-contained instead of shipping
with 2065 dangling `event2` references.

**Known limitation.** `Neutrals/OrcKing/weapon_neutrals_orcking_sword.dds` is
written by dis3tool with a 24-bit RGB header over a 32-bit payload, so its own
header is self-contradictory and `parse_dds` refuses it.  `d3tool export`
warns and passes the shipped `.t` through verbatim rather than failing the
whole unit.

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
