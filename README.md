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
| `d3tool/vdpack.py` | the `.vdpack` mod archive (one file = a whole unit folder): `vdpack list/extract/validate/manifest/pack/scan`. |
| `d3tool/char.py` | the `.char` unit profile: `char show/list/find/validate/set-id`. |
| `d3tool/weights.py` | skin weights of a `.g`: `weights <g> [--against <g>]`. |
| `Profiles/` | the retail unit profiles (874 `.char`: 214 units + their `desc`/`stats`/`sound` fragments). |
| `docs/PORT_ANALYSIS_COMPARE_RU.md` | cross-check of the Heroes-VII `Port` archive against this toolkit (the `.a` grammar taken from the official Max exporter, `.g` bind conventions, what was adopted). |
| `blender/io_scene_d3unit/` | the Blender add-on (import `.g`/`.a`/`.scene` → armature + animation, export back). |
| `bridge/` | AstraForge → unit → `.vdpack` bridge. `astra_d3.py doctor/list/build/pack/run/watch` drives Blender headless with the add-on and gates the pack on `vdpack scan`; `studio_link.py` + `link_up.py` (`Start studio link.cmd`) let a session drive that over a localhost link (see `bridge/README.md`, `bridge/QUICKSTART.ru.md`). |
| `tests/blender_addon_test.py` | headless Blender suite (21 scenarios, operator-driven). |
| `tests/blender_corpus_test.py` | every shipped unit through import → export in Blender, re-parsed and compared with its donor (~5 s per unit). |
| `d3tool/` | the Python toolkit (bidirectional: glTF↔original format). |
| `tests/test_d3tool.py` | unit tests for the individual readers/writers. |
| `tests/test_corpus.py` | corpus-wide import/export test over **all 87 units**. |
| `tests/corpus_parity.py` | benchmark: forward export vs the bundled dis3tool references. |
| `tests/anim_audit.py` | animation-only audit: import/export completeness + round-trip over every unit. |
| `gltf/`, `out_rev/` | checked-in sample output (forward and reverse export of the AirElemental); regenerable, kept for diffing. |

## Install / run

Pure Python 3 (no third-party dependencies).  Install as a package to get the
`d3tool` console script, or run it as a module.

```bash
pip install -e .          # provides the `d3tool` console script (or just `python3 -m d3tool`)
```

### The two everyday commands

```bash
# IMPORT a model FROM the game FOR editing: GM (.g/.a) -> glTF
#   the animation is auto-detected via the unit's .ac (use --no-anim for rigid)
d3tool import unit.g -o unit.gltf

# EXPORT an edited model BACK INTO the game: glTF -> GM
#   (.g/.scene/.ac/.a/.t + Aliases)
d3tool export unit.gltf -o unit
```

Both directions are byte-exact against the bundled dis3tool reference corpus
(85/85 each way).  `d3tool import` also accepts an explicit `-a anim.a`;
`export-gl` remains as an alias of `import`.  Mismatched extensions route to
the right command with a notice, so old scripts keep working.

### More commands

```bash
# analyze a unit folder
python3 -m d3tool analyze Neutrals/AirElemental

# one command: recursively convert every .g, with animations and textures
python3 -m d3tool export-all Neutrals -o gltf

# full cycle over a unit folder (reverse + forward round-trip)
python3 -m d3tool bundle Neutrals/AirElemental -o bundle

# structural self-check of a glTF
python3 -m d3tool validate out/unit.gltf

# dump a parsed .g as JSON
python3 -m d3tool dump Neutrals/AirElemental/character_neutrals_airelemental.g

# pack a finished unit folder into a single .vdpack mod archive (or unpack one)
python3 -m d3tool vdpack pack Empire/Hierophant -o hierophant.vdpack
python3 -m d3tool vdpack list hierophant.vdpack
python3 -m d3tool vdpack extract hierophant.vdpack -o hierophant/
python3 -m d3tool vdpack validate hierophant.vdpack   # every internal reference inside?
python3 -m d3tool vdpack validate hierophant.vdpack --strict   # ... and no wrong folder
python3 -m d3tool vdpack manifest hierophant/ -o manifest.json  # sha256 per file
# build on the only game-confirmed pack shape: keep the `index` manifest
python3 -m d3tool vdpack pack hierophant/ -o my.vdpack --keep-index NewUnit.vdpack
# `index` is the mod's manifest (Name/UUID/Type + per-file MD5 and SHA-256) and
# the loader compares the archive against it, so `vdpack pack` now writes it for
# the bytes it ships — no flag needed; `--no-reindex` carries the old one over
python3 -m d3tool vdpack index ruru.vdpack                       # decode + verify
python3 -m d3tool vdpack pack hierophant/ --like ruru.vdpack -o my.vdpack
# which installed pack is the one the game rejects? `run.log` names the file,
# never the archive — scan the install (or the Mods folder) and it names both
python3 -m d3tool vdpack scan "/path/to/Disciples III Rebirth" --strict
# ... and cross a run.log against it: which archive carries the rejected path?
python3 -m d3tool vdpack scan "/path/to/Mods" --log "/path/to/run.log"

# the .char unit profile: what a unit declares, and which profile is whose
python3 -m d3tool char show Profiles/empire_angel.char
python3 -m d3tool char list --root .                 # 214 units, scene? column
python3 -m d3tool char find Empire/Angel             # folder -> profile
python3 -m d3tool char validate Profiles --root .    # every reference present?
# one attribute at a time: a part the engine refuses to draw gets it back
# (the Angel's appended sword had no `new_vertex_weights_format`, so the game
# read its weight block with the old stride and drew nothing)
python3 -m d3tool gattr Empire/Angel/character_empire_angel.g \
  --part 4 --insert new_vertex_weights_format=1

# give the LOD mesh the parts the main mesh gained (and the scene its gobjs)
python3 -m d3tool lod Empire/Angel

# rename the browser entry (the port's way of adding a unit; keeps every byte)
python3 -m d3tool char set-id Profiles/empire_angel.char empire_angel_v2 \
  --scene "resources\characters\empire\angel\character_empire_angel_v2.scene" -o new.char

# convert the native .t texture to a .dds (or back)
python3 -m d3tool texture convert Neutrals/AirElemental/character_neutrals_airelemental.t \
  -o out/character_neutrals_airelemental.dds
python3 -m d3tool texture info Neutrals/AirElemental/character_neutrals_airelemental.t

# a texture you painted yourself (png/tga/bmp/dds) -> the <name>.t the engine
# reads, written straight into the unit's folder; then name the part after it
# (`--name` to choose the stem) and export: the .g, the _lod.g and both .scene
# files get that name
python3 -m d3tool texture add C:/paint/order_helmet.tga -o Empire/Inquisitor

# run the unit tests
python3 tests/test_d3tool.py

# run the corpus-wide import/export test over every bundled unit
# (~40 s: parses all 247 .g / 153 .a / 125 .ac / 98 .gltf / 751 .t / 285 .dds,
#  forward- and reverse-exports them, and checks every round-trip)
python3 tests/test_corpus.py

# or both at once, under pytest
python3 -m pytest tests/ -q

# parity benchmark against the bundled dis3tool reference exports
python3 tests/corpus_parity.py         # forward: 84/84 EXACT
python3 tests/reverse_parity.py        # reverse: 84/84 EXACT

# animation-only audit: every .g imports with its full .ac animation, every
# reference glTF exports every stream it names, 94/94 anim round-trips close
python3 tests/anim_audit.py            # 0 problems

# every shipped unit through Blender: import -> export -> re-parse, compared
# with its donor (needs Blender or `pip install bpy`; ~5 s per unit)
blender --background --python tests/blender_corpus_test.py
```

### The Blender workflow (import → paint weights → export)

Editing a unit in Blender and re-saving the glTF changes its shape in
ways the reverse exporter now handles explicitly (`tests/blender_repro.py`
runs six such scenarios, all battle-ready):

* **Textures become `.png`.**  The exporter resolves each image by stem to
  the sibling `.dds` (byte-faithful `.t`), then the shipped `.t` (copied
  verbatim), and only then re-encodes the PNG into an *uncompressed*
  32-bit `.t` (pure-Python decoder; loadable, just larger).  Save the
  glTF **into the unit folder** to get the byte-faithful path.
* **An extra `Armature` node appears** and the four weight lanes re-sort —
  both are absorbed; donor verification still gates every adoption, so
  painted weights always win over the donor `.g`.  Repainting a vertex keeps
  its dominant bone, the magnitude of its weights and their sum (the `.g`
  stores `w-1` of them as bytes and implies the last, so the file carries at
  most 4 influences summing to 1.0) — `tests/blender_addon_test.py` swaps
  weight pairs on a dozen vertices and reads the file back to prove it.
* **Moving vertices and deleting faces is a normal edit**: the `.g` is
  rebuilt from the Blender mesh (triangle counts follow the polygons, the
  vertex count follows the mesh), and the nudged geometry lands in the file
  `~0.03` away from the donor's own vertices — not silently reverted.
* **A compound unit imports as several armatures.**  Every part of a `.g`
  compound carries its own skin, so Blender builds one armature per skin
  (Rod-1: the body's `Reference` and the alpha part's
  `Head`/`Neck`/`Spine1`, with the second armature hanging under an empty
  that follows the first's bone).  The export gathers **all** of them, plus
  the empties on the way down — leaving one behind used to re-hang its
  meshes under a bone with no parent bone recorded, which crashed Blender's
  own glTF exporter.
* **A renamed file** (`angel_edit.gltf`) generates its `.ac` from the
  folder's real animation streams (sibling `.ac` fallback in
  `detect_anim_files`), each state's frame range inside the file it
  names — never a `<base>_iadd.a` that was never written.
* **Meshes the unit owns but nothing skins** (a morph base such as the
  Hierophant robe, a rigid CPU-side part) are exported too: a `.g` compound
  is rebuilt slot-by-slot against the donor, so a mesh missing from the glTF
  shifts every remaining slot.  Import tags them (`d3unit_part` +
  `d3unit_owner`), and an export only takes its own unit's meshes — a second
  unit left in the scene is never swallowed.
* **The vertex list is preserved exactly.**  Blender's glTF importer welds
  vertices that share position/normal/UV by default, which loses the exact
  correspondence with the original `.g`; the add-on imports with
  `merge_vertices=False`, so a unit that was not edited re-exports with the
  donor's own vertex counts.
* **A `.g` bone the animation never names** (the WitchHunter's
  `Marksman_eyelid_up`) is written into the glTF as the skeleton's root joint
  — that is the dis3tool convention, and every reference export does it.  The
  reverse export puts the donor name back instead of adding a 22nd bone to
  the table, so the parts keep animating.
* **A multi-material part** splits one index buffer into ranges; the
  per-material triangle counts have to add up to what was actually written,
  so a rebuild absorbs a lost triangle in the first range instead of
  announcing a total the reader then runs past.
* **A foreign mesh bound to the armature** (a new weapon, a prop parented to
  a bone, a part split off with "Separate by Material") becomes an extra `.g`
  part: skinned meshes get their own bone table, rigid ones follow the bone in
  `extras.d3unit_bone` (else the nearest joint above them).  Bone-parented
  props are re-parented to the armature for the glTF write and re-bound via
  that custom property, because glTF cannot express Blender's bone parenting.
  A part added that way also has to be *announced*: the container header closes
  its node chain with the number of mesh blocks, and an export that appends a
  fifth block while leaving the donor prelude at four leaves the mesh in the
  file and out of the model — see `docs/FORMATS.md` ("How many mesh blocks the
  container declares").
* **A new weapon gets put where the reference weapon is, by one operator.**
  Pick both meshes in the *Disciples III* sidebar (*Mesh* and *Reference*, two
  object pickers) and press *Place on Reference*: the mesh is rotated about its
  own grip until its principal axis (a Jacobi fit in `d3tool.weapon`) matches
  the reference weapon's shaft, then moved so the two grips coincide.
  *Flip* turns it half a circle about a perpendicular when the blade ends up
  pointing the wrong way — the answer to an origin in the middle of a handle,
  which leaves the axis sign undecided — and *Roll* spins it about the shaft.  The grip is
  the **object origin** — how retail weapons are modelled, and *Set Origin >
  Origin to 3D Cursor* moves it in one step; a mesh with exactly one vertex
  selected uses that vertex instead.  *Roll* spins the blade about that axis, *Same Bone*
  hangs the mesh from the reference weapon's bone.  This has to happen **in the
  bind pose** (armature → Pose Position → Rest): the vertices are written in
  world space and the bone's motion is added on top, so shaping a weapon onto
  an animated frame doubles the offset and it drifts out of the palm in game.
  `tools/place_weapon.py` is the same recipe as a stand-alone script.
* **A new part's own texture is a file of its own.**  The engine reads only
  `<stem>.t` next to the unit, while an atlas an artist paints comes as a
  `.png`/`.tga`/`.bmp`/`.dds`.  *Add Texture File…* in the sidebar (or
  `d3tool texture add <image> -o <unit folder>`) converts it into place and
  names the part after it; the export then writes that name into the `.g`, the
  `_lod.g` and both `.scene` files, and decodes an atlas that only exists next
  to the glTF as well.  A name the unit already ships is refused unless forced
  with `--force` — the export resolves textures by stem and prefers the
  shipped `.dds`/`.t`, so silently replacing the body atlas would be undone by
  the next export.
* **Every export ends with a battle-readiness check**: each `.ac` state's
  file present, `frame1` within that `.a`'s real frame count, meshfile
  and `.scene` references resolvable, a `.t` behind every
  `material0_diffuse`.  Findings print as loud warnings — the classes of
  defect that used to surface only as an in-game crash on entering
  battle.

### Did my edit actually reach the game?

Weights are invisible until the unit moves, and a file that the game never
loads looks exactly like a file with nothing wrong in it.  Four checks settle
it, in this order:

0. **Is this the model the camera actually draws?** Every unit ships two of
   them, and its `.char` names both:

   ```
   unit_scene      "…\character_empire_angel.scene"       # what you see
   unit_scene_lod  "…\character_empire_angel_lod.scene",10 # only past 10 units
   ```

   Painting `character_empire_angel_lod.g` and packing it changes the angel you
   look at *from far away* and nothing else, so the battle model stays
   untouched and the report reads "my weights never reached the game".  The
   importer now warns when the file you picked is the `_lod` one; check with
   `python3 -m d3tool char show Profiles/empire_angel.char` (or
   `d3tool analyze <unit folder>`) before you paint.

1. **Does the file carry what you painted?** Compare what Blender wrote with
   the file the game ships:

   ```bash
   python3 -m d3tool weights path/to/character_empire_angel.g \
       --against Empire/Angel/character_empire_angel.g
   ```

   It prints the influence width of every part, the bones each part is bound
   to and the weight sum per vertex (`1.000..1.000` is what the engine
   wants), then `weights changed` / `dominant bone moved` counts and a few
   before→after lines.  Zero changed means nothing you painted reached the
   `.g` — stop here and re-export rather than hunting in the game.

2. **Does the unit crash the game on load?** Then the file's own counts are
   the first suspect: every sub-mesh header advertises a vertex/triangle pair
   and the engine's loader trusts it, so a stale pair desynchronises the
   vertex, index and bone blocks behind it and takes the process down — the
   unit never gets drawn, so there is nothing to look at.  Check and fix an
   existing file with

   ```bash
   python3 -m d3tool repair path/to/character_empire_angel.g        # report
   python3 -m d3tool repair path/to/character_empire_angel.g -i     # fix
   ```

   `d3tool analyze <unit folder>` and the add-on's export report warn about it
   now, and `repair` is only for files an older export already wrote — a
   current export keeps the header in step with whatever Blender did to the
   mesh.

3. **Does the header count the block you added?** A `.g` closes its node chain
   with the number of mesh blocks, and the engine reads exactly that many — a
   weapon appended as block five under a header that still says four is in the
   file and not in the model, with nothing else wrong with it.  Compare:

   ```bash
   python3 -c "from d3tool import gfile as g;\
   d=open('character_empire_angel.g','rb').read();\
   print(g.mesh_count(d), len(g.iter_parts(g.parse_geometry_file(d))))"
   ```

   The two numbers must be equal (every retail file in the corpus is).
   `python3 -m d3tool lod <unit folder>` appends a missing part to the LOD
   model *and* the gobj that binds it, and both the writer and
   `gfile.append_parts` keep the count in step now; a file an older export
   already wrote is fixed by `python3 -m d3tool repair … -i`.

4. **Does the game read that file at all?** Make an *obvious* geometry edit
   (move a whole limb, scale the mesh), export, restart the game and enter a
   battle.  If the silhouette is unchanged, the weights were never the
   question: the game is loading a different file.  Things worth checking:

   * the unit folder the game uses is `resources\characters\<faction>\<unit>\`
     and the `.scene` there names the `.g` you replaced;
   * a `.vdpack` (or a mod archive) in the game folder can carry the same
     `character_*.g` — list the candidates with
     `python3 -m d3tool vdpack list <archive>`; a unit shipped inside a pack
     has to be rebuilt into that pack (`vdpack pack … --like <that pack>` — the
     proven pack's own entry order, names, flags and `index`, only the payloads
     swapped; `--keep-index` is the weaker form of the same idea), a loose file
     next to it may simply be ignored;
   * a pack whose own `.ac`/`.scene` names the right file in the wrong folder
     is the crash that looks like a missing file: `vdpack validate` prints
     those as warnings (and `--strict` exits 1).  A generated `_lod.ac` used
     to be written with the model's *object name* as its resource root, so
     `resources\characters\character_x_lod\…` was asked for while the pack
     held `resources\characters\<faction>\<unit>\…`; drop such a file or
     re-export it with a current add-on.  This exact case is settled in game:
     with that one file removed and both `.g` payloads replaced in place
     (`index` untouched) the Angel loads and the edited wing is what the
     battle shows — the pack keeps no `.dds` and no icons and does not need
     them;
   * leaders draw several `.g` parts through `set 1|2|3` equipment scenes — a
     weight change in the body shows only if that body is the part on screen;
   * weights show up while the unit animates (idle/attack), and an id from a
     save may restore a unit that was already loaded — start a fresh battle.

### The add-on zip (what you install in Blender)

```bash
python3 release/build_addon.py     # -> release/d3unit-addon/io_scene_d3unit.zip
```

The zip carries the six add-on modules **and** a vendored copy of `d3tool`
(`<add-on>/vendor/d3tool`), so `Edit > Preferences > Add-ons > Install…`
gives a working importer/exporter on a machine that has nothing but Blender —
no checkout, no `pip install`.  The preference *d3tool path* still wins when
set, and *Check d3tool* reports which copy is live (the vendored one, the
checkout, or a pip-installed package).  `tests/addon_zip_test.py` builds the
zip, unpacks it outside the tree, and does a full import→export round trip in
a Blender process that cannot see the checkout.

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
  otherwise a faithful, particle-free scene is generated.  Either way the
  engine binds the i-th mesh part to the i-th `gobj` **in document order** — a
  scene carries one block per part, and a part past the last block is
  invisible, because nothing draws it.  Adding a mesh in Blender (the Angel's
  sword is the fifth part) therefore needs a fifth block, which the exporter
  appends and warns about.
* the same part has to reach the **LOD model** too: the `.char` profile swaps
  to `unit_scene_lod` beyond a draw distance (`10` for nearly every unit), and
  `<base>_lod.g` is a separate, lower-poly mesh.  The reverse export runs the
  sync itself (`d3tool lod <unit folder>` does the same by hand): it appends
  the added parts to the LOD mesh, renamed `lod_<name>` and re-pointed at that
  file's own `dwParent` (`gfile.lod_part` — the node ids are per file, the
  Angel's main mesh uses 53704704 and its LOD mesh 55867360), and gives
  `<base>_lod.scene` the matching `gobj`.  Only the container's mesh count
  moves in the shipped bytes; running it twice changes nothing.
* `character_neutrals_airelemental.ac` — animation config.  When the source
  unit folder already has an `.ac`, the exporter reuses it verbatim so its
  `event2` entries survive (the attack/damage/death sound aliases and the
  `FxStrike`/`fxcast` cues — the AirElemental carries seven of them);
  otherwise a faithful five-state config is generated.
* `character_neutrals_airelemental_iadd.a` — the animation binary rebuilt from
  the glTF animation channels; byte-identical to the original (bone order,
  record preambles, header and trailing morph streams included).  When the
  reused `.ac` names **several** streams (Cleric: `_iadd.a` + `_run.a`), each
  one is sliced back out of the concatenated glTF animation and written, so
  no `.ac` reference dangles — 105 of 105 rebuilt `.a` files with an
  original are byte-identical.
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

**Full import/export sweep (CLI end-to-end).** Every command path was run
over the whole tree: reverse `export` of all 98 reference glTFs (98/98
succeed; every produced file with an original is byte-identical), forward
`export-gl` of all 247 `.g` (245/247 — the two node-helper stubs refuse by
design; 85/85 reference pairs byte-EXACT), `export-all` over both trees
(85/85 reference EXACT), `bundle` over all 84 unit folders (98 units
round-trip glTF -> GM -> glTF without a failure), `import` on all 247 `.g`
and `analyze` on all 114 folders.  The full cycle `gltf -> GM -> gltf`
closes **byte-for-byte for 89 of the 98 reference glTFs**; the remaining 9
are the Leader variant sets, whose GM originals were never shipped — their
residual byte gaps are authoring data a glTF cannot carry (per-vertex
diffuse colours, a solid-color fallback material), which is exactly what
the donor mechanism supplies when originals exist.

**Reverse parity.** `tests/reverse_parity.py` runs the CLI's own reverse
export over every bundled dis3tool reference unit and compares each produced
file against the shipped original: **85/85 EXACT** — the rebuilt `.g`, the
rebuilt `.a` (bone order, record preambles, header, trailing morph streams,
concat-slice and out-of-array channel recovery), the reused `.scene`/`.ac`,
the converted `.t` textures and the copied `Aliases/` are all byte-identical
to the originals.  The original files next to the source glTF act as donors
for the authoring data a glTF cannot carry (diffuse colours, container
scaffolding, attribute blocks, weight splits, record preambles, morph-stream
tags), always gated on the rebuilt data verifying against them.

**Animation completeness** (`tests/anim_audit.py`, three sweeps over the
whole tree, **0 problems**):

* *Import (GM → glTF)* — all 247 `.g` forward-export; the 152 skinned ones
  animate with **exactly the frames their authoritative `.ac` names**
  (own `<stem>.ac` first, lod configs included, then the de-lodded main
  one).  LOD meshes concatenate their **own** config's stream pair
  (`cleric_lod.ac` → `iadd_lod` 356 + `run_lod` 25 = 381; resolving the set
  from the main `.ac` used to hand back the lod Idle stream alone).  An
  explicit `-a` selects the *unit*, never the layout: the concatenation
  stays in `.ac` order, which is also what picks the morph targets (they
  come from the last stream, Cleric's `_run.a`), byte-identically to the
  auto-detected import.
* *Export (glTF → GM)* — all 98 references reverse-export; the 95 animated
  ones each write an `.a`, and a unit whose `.ac` names several streams
  gets **every** stream sliced back out of the concatenated glTF animation
  (105 of 105 with an original byte-identical — writing only the Idle file
  used to leave `run.a` a dangling engine reference).  The 12 leader-set
  variants, which ship no `.ac` of their own, adopt the folder's
  `baseanims.a` as a verified donor and come out **byte-identical** to it.
* *Animation round-trip* — glTF → GM → glTF closes **95/95** on the
  animation (channel structure and every sampler accessor byte), rigid
  references aside.
* The fallback that hands a lone `.a` to a mesh in a multi-mesh folder now
  requires a *claim*: a sibling `.ac` naming that `.a` for this very
  `meshfile` (Wolfsnow's config claims `character_neutrals_wolf.g`, its
  `_lod` variant included), else the mesh stays rigid — the Cyclop's rock
  projectile no longer borrows the Cyclop's 39-bone skeleton.

Seven `.a` files are *structurally* never imported, each for a measured
reason: `Empire/Leader-Ranger` / `Leader-Thief` base their only `.g` on a
node-helper stub (their `baseanims.a` animate the set variants, which ship
as reference glTFs instead), `Empire/Mage/character_empire_mage.a` is a
legacy unreferenced cut (49 bones vs the 47 the `.ac` names),
`Blacknaga/baseanims.a` is shadowed by its own `.ac` (which points at
mermaid's, matching dis3tool's rigid reference), `FatImp/*_lod.a` is a
morph-only stream (0 bones in its header) that no config names, and
`WaterSnake_cast.a` / `Fire_Wall.a` have a `.ac` but no geometry at all —
animation-only objects with nothing to animate.

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
against the bundled dis3tool reference, byte for byte: **85 EXACT, 0
BIN-NEAR, 0 FAIL** out of 85 — the glTF JSON is float32-bitwise equal *and*
every `.bin` byte matches.  (Trajectory: 9/46/30 at the start of this work,
10/69/6 after the animation-concatenation round, 10/75/0 after the
channel-target/rigid/compound fixes.)  The last red zone fell to two
findings, each pinned by an exhaustive per-vertex/per-frame diff:

* **Weight packing** (`d3tool.model.pack_weights_joints`): dis3tool keeps
  the stored lanes verbatim, computes the complement
  `c = float32(1 - sum(stored))` from the *double-precision* sum, merges a
  positive `c` into the first lane whose bone repeats in the implied bone
  slot (else appends it), never touches a non-positive `c`, and masks a
  joint only when its weight is *exactly* 0.0 — a 2.98e-08 residue keeps
  its joint.  Byte-verified on all 292 569 skinned vertices of the corpus
  (0 mismatches); the earlier ±1ulp red zone was the renormalising
  approximation this replaced.
* **Cross-stream frame filling** (`anim.concat_anims`): the bone *list* is
  the first-seen-name union, but each stream's *frames* land on the output
  slot of the same **record index**, name notwithstanding — AirElemental's
  `run.a` names record 6 `LeftLeftHand` while the slot is `LeftHand`, and
  the reference's LeftHand frames 346..362 are exactly that record's 17
  samples (DarkServant's `Bone02` → the `ROOT_demons_thief_lod` slot the
  same way).

Earlier rounds (10/75/0 state) closed the last six FAILs with: the compound
writer animating only the first stream's bones (DarkServant's `Bone02`),
duplicate-name bones getting one channel pair each with targets *counted
positionally* (WaterSnake's four extra `null` bones dangle past the node
list exactly like the reference, Wildboar 37 of 37), units whose `.ac`
names a stream outside their folder shipping rigid (Blacknaga,
watersnake_sea), and Rod-1's attrless sword part keeping real positions at
the morph-static stride with the stray 15th sampler aimed at accessor 33.

**Root cause of the morph failures (measured).** 24 bundled units have an
`.ac` that references *more than one* `.a` (Angel names five: idle/attack/run/
damage/death).  dis3tool **concatenates every one of them** — verified on all
24, the reference glTF frame count equals the sum exactly (Angel
64+84+28+32+55 = 263, Golem 632, Cleric 381).  Its morph targets come from the
*last* stream, not the first: Cleric's reference has **25** targets
(`_run.a`, 25 frames), while `_iadd.a` carries 356.  Target #0 matches
`character_empire_cleric_run.a` frame 0 bit-for-bit (max diff 0.0000).

**Implemented.** `anim.concat_anims` joins the streams in `.ac` order —
primary slots read each stream's record at the same index (name drift
included; a slot with no record at all holds its rest pose for that
stretch) — and takes the morph tracks from the **last** one;
`_export_gl` calls it through `cli._load_anim_stream`, and a lone stream
passes through untouched so the 97 single-`.a` units are unaffected.  All
16 multi-`.a` units that have a reference now match it on frame count,
morph-target count *and* bytes.

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

*`AirElemental`* — byte-exact now.  Its two extra bones (`LeftLeftHand`,
`Tail02`) exist only in the *second* animation stream (`_run.a`), which
`concat_anims` appends; the same stream's records 6/41 also *fill* the
`LeftHand`/`RightTail02` slots' second stretch (the name-drifted record
above).  The reference emits the extras as the last two nodes, still lists
`LeftLeftHand` under `LeftForeArm` (`children [7, 43]`), and gives them
**no channel**: its 84 channels target exactly the 42 primary bones and its
buffer is `2 * 363 * (16 + 12) = 20328` bytes smaller than one that
animated them.  `node_hierarchy` walks only the primary skeleton and trails
the rest, and the single-mesh writer allocates accessors and channels for
the primary bones alone.

**Node order (measured).**  The reference order is a depth-first walk of the
skeleton with children in `.a` record order; it matches 79 of the 83 bundled
references.  The four misses are `Blacknaga` (rigid, no bone nodes),
`WaterSnake` and `Wildboar` (duplicate bone names) and `AirElemental` (above).

*`DarkServant`* was the last compound holdout — the compound writer now has
the same primary-bone treatment (its `Bone02`, carried only by `_run.a`,
keeps the trailing node but gets no channel), so it matches the reference
structurally like every other unit.

**Validator note.** `validate_gltf` accepts *both* morph-weight output shapes:
the spec's `input.count * len(targets)` and dis3tool's `input.count ** 2`
(which `_write_compound_gltf` replicates for byte parity).  Requiring only the
former made the validator reject 8 of the 98 bundled reference glTFs — i.e.
ground truth.  Two further dis3tool quirks are now *reproduced for parity*
and reported as warnings, not errors: Rod-1's stray sampler 14 (`output=33`
with 33 accessors, referenced by no channel) and the dangling duplicate-bone
channel targets of WaterSnake (nodes 47–50 of 47) / Wildboar (node 37 of 37).

**Sound aliases.** The corpus holds 1294 `.alias` files, referenced from the
`.ac` `event2` entries.  Three things worth knowing: the files on disk are
lowercase (`attack00.alias`) while the references are CamelCase
(`Attack00.alias`), so any resolver must be case-insensitive; 87 of them have
an empty block (a muted event — they must round-trip as empty, not be
dropped); and 261 of the 2065 references point at resources outside this
repository (shared `resources/sounds/alias/*` and cross-unit folders such as
`Characters/elves/scout/`).  `d3tool/alias.py` now parses and re-emits all
1294 byte-for-byte — including Craken's CP1251-encoded `ттт.alias` — the
reverse export copy validates each file, and `analyze` reports per-folder
alias health instead of copying blind.  1293 files are ASCII/UTF-8 and one is
CP1251, so the byte-level readers decode UTF-8 → CP1251 → Latin-1 and
remember which codec to re-encode with.

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
  losslessly (format codes 6/7/8 → DXT1/DXT3/DXT5; code 3 → 16-bit A1R5G5B5),
  and decodes the formats an artist actually paints in (PNG, TGA, BMP) into a
  block-compressed `.t` — the engine reads no other container.

See `docs/FORMATS.md` for details.
