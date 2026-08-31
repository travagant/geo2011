# Disciples 3 / dis3tool format study

Reverse engineering notes gathered from the **geo2011.dle** plugin (the
`GM (R) File Exporter` / `GM MAX (R) File Format Exporter` for the old 3ds Max)
and from the bundled `Neutrals/*` unit files.  These are the formats a program
modelled on *dis3tool* (glTF export) must both read **and** write so it can
re-export glTF back into the original engine format.

The goal here was to understand enough to build the **reverse** export path:
`glTF -> original Disciples 3 files`.  What follows is what was confirmed by
byte-level comparison against the real files.

---

## 1. The dis3tool glTF export

Each unit folder contains a `<name>.gltf` plus a `<name>.bin`
(and `.dds`/`.tga`/`.t` textures).  The glTF is a standard glTF 2.0 file,
JSON `asset.generator = "generated with dis3tool v1.1 by root.ext@gmail.com"`.

Key structure (AirElemental as the reference unit, 3453 verts / 5056 tris /
38 bones):

* `buffers[0]` → `<name>.bin`.
* `bufferViews`:
  * 0 `mesh_indexes...` `byteLength = tri*3*4`, target 34963.
  * 1 `mesh_vertexes...` `byteStride = 52`, target 34962 —
    `pos(12) + normal(12) + uv(8) + weights(16) + joints(4)`.
  * 2 `mesh_bones...`   `byteLength = bones*4*16` (38 × MAT4 = 2432).
  * 3 `frames`, 4 `bones_rotate`, 5 `bones_translate` — the animation tracks.
    `frames[k] = float32(k * float32(1/30))` — one keyframe per frame on a
    30 fps clock (0 .. (n−1)/30 seconds), *not* a normalised 0..1 range.
* `meshes[0]` one primitive with `POSITION/NORMAL/TEXCOORD_0/WEIGHTS_0/JOINTS_0`.
* `skins[0]` with `joints` (list of node indices) and
  `inverseBindMatrices` (accessor → `bufferView` 2, `componentType` 5126,
  `type MAT4`, count = bones).
* `animations[0]` — sampled bone rotation + translation over `frames`.

### Critical finding
`skins[0].inverseBindMatrices[i]` in the glTF is **byte-for-byte identical**
to the `.g` bone descriptor matrix `i`.  The joint *order* in the glTF
`skins[0].joints` is the same as the `.g` bone descriptor order
(e.g. `RightArm` is index 0 in both).  So a skeleton round-trips perfectly:

```text
.g bone[i].matrix  ==  glTF inverseBindMatrices[i]
.g bone[i].name    ==  glTF nodes[skins[0].joints[i]].name
```

### Vertex / weight encoding
The glTF `WEIGHTS_0` / `JOINTS_0` are 4-component.  dis3tool (and the GM
format) use `weights_on_vertex` influence slots (`w` = 2, 3 or 4).  The GM
vertex does **not** store all weights:

* it stores the **first `w-1` weights** as float32,
* the **last** weight is implied as `1 - sum(stored)`,
* it stores the **`w` bone indices** as u8.

dis3tool **preserves the glTF influence order** — it does **not** re-sort by
weight.  So to convert back you take the first `w` of the 4 component
`(weight, joint)` pairs verbatim.

On export dis3tool repacks those numbers with an exact rule — implemented in
`d3tool.model.pack_weights_joints`, byte-verified against all 292 569 skinned
vertices of the 85-unit reference corpus (0 mismatches):

* stored lanes are copied **verbatim** (no renormalisation, no trimming);
* the complement `c = float32(1.0 - sum(stored))` is computed from the
  **double-precision** sum, then rounded once to float32;
* `c > 0` is merged into the first already-listed lane whose bone equals the
  implied bone `bones[w-1]` (a single-precision add), or appended as an extra
  lane when that bone is not listed;
* `c <= 0` changes nothing (the stored lanes already reach 1.0f or overshoot
  it by rounding — both stay verbatim, even mid-ulp ties);
* `JOINTS_0` is the bone array padded to 4 lanes where any lane whose weight
  is **exactly 0.0** is reported as joint 0; a tiny residue lane (2.98e-08)
  keeps its joint.

### Export conventions (what `d3tool export-gl` reproduces byte-for-byte)

These behaviours were established by diffing all 85 bundled dis3tool
reference exports; the writer reproduces them — quirks included — and the
export is **byte-identical** to the reference (`tests/corpus_parity.py`:
85/85 EXACT — both the glTF JSON, float32-bitwise, and the `.bin`):

* **Animation resolution / rigid exports.**  dis3tool loads only the `.a`
  stream(s) the unit's own `.ac` names, resolved inside the unit folder.
  When the named stream is not there the unit ships **rigid**: one mesh
  node, no bone nodes, no `skins` key, primitives without
  `WEIGHTS_0/JOINTS_0`, accessors stopping after `TEXCOORD_0` — while the
  buffer keeps the skinned stride-52 vertex block and the `mesh_bones` IBM
  block, unreferenced (Blacknaga's config points at mermaid's `.a`,
  watersnake_sea at a `.a` bundled with neither).
* **Channel targets are counted, not resolved.**  Every bone of the primary
  stream(s) gets a rotation + translation channel aimed at
  `node_slot + list position`.  A duplicate-name bone (`null` five times in
  WaterSnake, `null_Bone_Tip` twice in Wildboar) has no node of its own, so
  its channels dangle past the node list (Wildboar 37 of 37 nodes,
  WaterSnake 47..50 of 47) and every *unique* bone after a duplicate aims
  one slot high.
* **Only the first stream's bones are animated.**  Bones that
  `concat_anims` appended from a later `.a` (AirElemental's
  LeftLeftHand/Tail02, DarkServant's Bone02) get a trailing node but no
  channel and no rot/tra storage; `skin.joints` stays on the full list.
  Frame filling across streams is **positional**: each stream's record at
  index *i* lands on the output slot *i* even when the name drifted
  (AirElemental's `run.a` names record 6 `LeftLeftHand` while the slot is
  `LeftHand` — the reference's LeftHand frames 346..362 are exactly
  `run.a` record 6's 17 samples; same for `Tail02` → `RightTail02` and
  DarkServant's `Bone02` → `ROOT_demons_thief_lod`).
* **Scene roots** are the sub-meshes plus every skeleton node whose parent
  is not a bone, in node order (DarkServant's `ROOT_demons_thief_lod` /
  `Bone02`, parent `Scene Root`, trail the skeleton root).
* **Compound static parts.**  A part with `morph: 1` in its attribute block
  is a morph-deformer: stride-32 vertexes with the base positions zeroed
  and zeroed POSITION min/max.  A part with **no attributes at all**
  (rod-1's sword) keeps the same stride but *real* positions, still with
  zeroed POSITION min/max — and its presence makes dis3tool append one
  stray animation sampler aimed at the accessor index just past the end
  (output 33 of 33 in Rod-1), referenced by no channel.
* `validate_gltf` reports the two reference quirk classes above as
  warnings, not errors, so a faithful export still validates with 0 errors.

---

## 2. The GM `.g` geometry file

Binary, little-endian.  Layout (all byte offsets are for the AirElemental file;
other units shift only because the two leading strings have different lengths):

```text
 0..119   30 x u32/float "header" (magic + axis-scale/null data)
 120..    [u32 len] name1 (unit / armature name)      + NUL
          [u32 len] name2 (geometry file basename)    + NUL
          prelude: 10 x u32  (includes the counts)
          scene-node block: 14 floats (node translation/rotation)
          attribute block:
            [u32 num_attrs]
            repeat num_attrs:
              [u32 key_len] key + NUL
              [u32 val_len] value + NUL
          vertex array: vertex_count records
          index array: (tri_count * 3) x u32
          bone descriptor array
```

### Two container forms

`classic` (the overwhelming majority) starts with the 120-byte header above.
`stub` is a header-less *node helper*: the 10-u32 prelude and the 14-float
scene-node block come **first**, at offset 0, and there are no name strings,
so the attribute block sits at a fixed offset 96.  `Empire/Leader-Ranger` and
`Empire/Leader-Thief` each ship one 602-byte file of this form
(`name = "BaseMesh"`, `materials_num 0`, four vertices, no index block).
`gfile._locate_attr_block` detects the form and stores it in
`SkinnedMesh.form`, which `write_geometry_file` mirrors on output.  Because
such a file has no triangles, the glTF exporter refuses it with an explicit
message instead of emitting a skin with zero joints.

### Counts
The prelude holds the counts, but the authoritative source is the attribute
block: `vertexs_weights_num` (vertex count), `material0_triangles_num`
(triangle count), `weights_on_vertex` (`w`), `bones_num`.  `dwNode` is the
first attribute key and makes the attribute block trivial to locate.

### Vertex record
```
[0:4]   per-vertex prefix magic (asset specific, e.g. 5ce6ac0b / 28e7ce19)
[4:16]  position (3 x f32)
[16:28] normal   (3 x f32)
[28:32] diffuse  (u32, almost always 0xFFFFFFFF)
[32:40] uv       (2 x f32)
[40:40+4*(w-1)] stored weights (f32)  -- w-1 weights
[...]   bone indices (w x u8)
```
Record size `= 40 + 4*(w-1) + w`:

| `w` (weights_on_vertex) | stride |
|---|---|
| 2  | 46 |
| 3  | 51 |
| 4  | 56 |

The leading 4-byte magic is constant *within* a file but differs between files;
it must be preserved for round-trips.

### Bone descriptor
`[u32 str_len] name + NUL, 4x4 row-major matrix (16 x f32)` repeated `bones_num`
times.  The matrix equals the glTF `inverseBindMatrices`.

### Attribute block
String key/value pairs, written by the exporter.  Standard keys for a skinned
character (11–14 of them depending on shader/format):
`dwNode, dwParent, name, groupname, materials_num, material0_diffuse,
material0_triangles_num, new_vertex_weights_format, vertexs_weights_num,
weights_on_vertex, bones_num` (plus `tech`, `vdshader`, `techname`,
`material0_detail`, `material0_bump`, `material0_lightmap`, `morph_frames`,
`shadowvolume_vertexs_num`, ... for other asset types).

---

## 3. The `.ac` animation config

Text.  A header then `state "Name" { ... }` blocks:

| field | meaning |
|---|---|
| `file`   | external `.a` animation file |
| `frame0/1` | frame range |
| `fps`    | frame rate |
| `priority` / `flags` | blend priority / flags |
| `link "X" dir; blend n;` | transition to another state |
| `event2` | sound / fx events at a frame |
| `gaestate` | game state word |
| `meshfile` | the `.g` geometry |

The `.ac` only *references* `.a` files by name, so a reverse exporter can point
at the actual `.a` files already present in the asset folder rather than
re-creating them.

## 3b. The `.a` animation binary

The `.a` holds, for each bone, a descriptor plus a per-frame stream:

```
global header (16 bytes, e.g. 9 / 408508 / 42 / 346 ...)
repeat per bone:
  [1 byte marker '<']
  [cstr] bone name
  [cstr] parent name
  [7 float32] bind/rest TRS   (translation 3 + quaternion 4)
  [7 float32] * frame_count   (per-frame TRS, 28 bytes each)
```

The per-frame TRS is exactly what dis3tool exports into the glTF animation
`bones_rotate` (`VEC4` quat) and `bones_translate` (`VEC3` trans) buffers, so a
**full round trip** is possible: `d3tool/anim.py` parses the header and the bone
records (names, parents, per-frame TRS) and `write_anim` re-emits them
byte-for-byte.  The `.a` record set is exactly the set of nodes animated by the
glTF (Root + every bone), in hierarchy order, so `d3tool/gltf.py::animation_from_gltf`
rebuilds a `.a` from the glTF animation channels with matching per-frame values.

---

## 4. The `.scene` scene file

Text, a `globalsettings { ... }` block then a `group "Scene Root"` tree:

```
group "Scene Root"
  child bones "<unit>"
     child gobj "<unit>"
        ``"resources\\...\\<base>.g"``   <- geometry
        attr "dwNode" "..."
        attr "bones_num" "38"
        ...
```
The `.g` is referenced as a `gobj`; the `.ac` is referenced by the parent
`bones` node; particle emitters are attached to bones via
`child particles "BoneName"` blocks, each with a `boneslink "<Bone>"`, a `file`
(texture), and numerous `ps_*`/`ps_spline_track` parameters.

Particle emitters (and the GUI-camera/glow settings) are **authoring data** that
is not present in the geometry/glTF, so a reverse export cannot reconstruct
them.  `d3tool/scene.py` therefore does two things:

* if the source unit folder already has a `<base>.scene`, the exporter reuses
  it verbatim (preserving every particle emitter and the camera);
* otherwise it generates a faithful, particle-free scene (correct
  `bones`/`gobj` node names, full `globalsettings`, attribute block).

### Parsing `.scene`

`parse_scene` / `render_scene` round-trip every shipped `.scene` byte-for-byte
(244 under Empire//Neutrals plus the `out_rev` sample)
byte-for-byte (Latin-1 bytes; five files mix LF and CRLF per line, so brace
lines are stored verbatim).  The grammar, quirks included:

* `globalsettings`, `group "<name>"` and `child <kind> "<name>"` open blocks;
  braces sit alone on their line, at column 0 (dis3tool exports) or
  tab-indented (d3tool's own generated scenes);
* kinds seen across the corpus: `gobj` (618), `group` (271 incl. nested),
  `bones` (160), `goclass` (74 - the only header with a second quoted
  argument), `particles` (52);
* props are `key value[;]` - the semicolon is optional (`uid 181 40875272`),
  and quoted values may contain commas and semicolons, so a prop never spans
  lines; a few props are keyless (the gobj mesh line `"<path>.g" 0 0.000000`);
* indentation does not encode depth - dis3tool puts `child particles` at
  column 0 inside a `bones` block.

## 4b. The `.alias` sound-alias file

Text, one block per file (1294 across the corpus):

    // alias configuration file
    //  ...

    alias "Attack00" {
    	sound 100, "$(Sounds)\clothes\cloth\cloth_02_03.wav", 100, 3;
    }

* `sound <use chance>, "<file>", <play chance>, <flags>;` - `flags` bit 0 =
  enabled, bit 1 = play with accelerated animation; 236 files ship without
  the comment header, 87 have an **empty** block (a muted event);
* 1293 files are ASCII/UTF-8; Craken's Cyrillic-named file is CP1251 -
  `parse_alias_bytes` decodes UTF-8 -> CP1251 -> Latin-1 and records the
  codec so `write_alias_bytes` re-encodes losslessly;
* `parse_alias` / `write_alias` round-trip all 1294 files byte-for-byte.

---

## 5. Implemented in `d3tool/`

* `d3tool/gfile.py` — parse & write `.g` (byte-exact for all bundled files;
  handles `w = 2/3/4` character meshes and passes compound files through).
* `d3tool/gltf.py` — parse the dis3tool glTF; convert to `.g`; rebuild `.a`.
* `d3tool/gltf_out.py` — forward export `.g`/`.a` → glTF + a lightweight
  structural `validate_gltf` self-check (bidirectional tool).
* `d3tool/ac.py` — parse & write `.ac`; detect the real `.a` files.
* `d3tool/anim.py` — parse & write the `.a` animation binary (byte-faithful).
* `d3tool/scene.py` — parse & write `.scene`: any shipped scene parses into a
  node tree (`group` / `bones` / `gobj` / `goclass` / `particles`, props with
  optional semicolons, keyless mesh lines, mixed LF/CRLF endings) and
  re-renders byte-for-byte; generate a minimal `.scene` for units that ship
  none.
* `d3tool/alias.py` — parse & write `.alias` (byte-exact for all 1294 bundled
  files, including the CP1251 one; empty "muted" blocks preserved).
* `d3tool/cli.py` — `analyze`, `export` (glTF → original), `export-gl`
  (original → glTF), `validate`, `import`.

### Reverse-export fidelity
For the AirElemental unit the exported `.g` matches the original in:
positions, normals, UVs, triangle indices, skeleton (bone names + matrices),
and material.  The only mismatch is the *first* bone index of the 21 vertices
that carry a zero-weight first influence slot (dis3tool fills an extra joint
there); this does not affect the rendered pose because the weight is 0.

### Forward-export fidelity
`d3tool/gltf_out.py` writes a glTF whose POSITION, NORMAL, TEXCOORD_0,
WEIGHTS_0, JOINTS_0, indices and inverse-bind matrices are byte-for-byte
identical to the reference dis3tool export (the 21 zero-weight joint slots
aside).  The animation is sampled from the `.a` frame count, one keyframe per frame on
the dis3tool 30 fps clock: input times are `float32(k * float32(1/30))`
seconds, reproduced byte-for-byte (a normalised 0..1 range would play the clip
~12× too fast and look coarse in viewers).  The exported glTF
passes the official Khronos glTF Validator with **0 errors**.

### `.g` round-trip fidelity
`parse_geometry_file` → `write_geometry_file` reproduces the original bytes
**exactly for all 11** bundled `character_*.g` files.  The writer preserves
the per-unit binary header, prelude+scene-node block, per-vertex magic and any
trailing payload (morph/shadow data).

The vertex record rules:
* `w = 2/3/4` (skinned character): stride `= 40 + 4*(w-1) + w` (46/51/56);
  stores the first `w-1` weights and `w` bone indices.
* `w = 1` (single-bone / weapon mesh): stride `= 40`; no stored weight and no
  per-vertex bone index (the single bone is implicit).

Some `.g` files (e.g. the Zombie LOD) are **compound** — after the first mesh's
bone descriptor they carry further mesh objects (a weapon mesh plus a full
character LOD mesh stacked together).  The parser reads the first mesh and keeps
the remainder in `SkinnedMesh.trailing`; `write_geometry_file` appends it, so the
file round-trips byte-for-byte even though it is not a single character mesh.
`SkinnedMesh.raw` is a fallback that stores the original bytes verbatim for any
layout the parser does not fully understand.

## `.t` texture container

The GM engine stores textures in a native `.t` file: a **59-byte header**
followed by the exact same compressed (DXT) pixel data that a standard `.dds`
stores after its 128-byte header.  `d3tool/texture.py` parses both and converts
between them losslessly.

`.t` header (little-endian):

| offset | type   | meaning |
|--------|--------|---------|
| 0x00   | u32    | container version / magic (1) |
| 0x04   | u32    | pixel-format code (see the table below) |
| 0x0c   | u32    | mipmap level count |
| 0x10   | u32    | width |
| 0x14   | u32    | height |
| 0x18   | u32    | opaque flag (varies; see below) |
| 0x34   | u32    | marker (0x00417000) |

The payload immediately follows at offset 59 (and is byte-identical to the
`.dds` payload at offset 128).  Format codes seen across the 752 bundled `.t`
files:

| `@4` code | encoding | bytes/px | count | DDS side |
|---|---|---|---|---|
| 6 | DXT1 | 0.5 | 258 | fourCC `DXT1` |
| 7 | DXT3 | 1.0 | 384 | fourCC `DXT3` |
| 8 | DXT5 | 1.0 | 10  | fourCC `DXT5` |
| 3 | 16-bit A1R5G5B5 | 2 | 80 | DDPF_RGB, 16 bpp, masks 0x7C00/0x03E0/0x001F/0x8000 |
| 1, 2 | 16-bit uncompressed | 2 | 15 | same DDPF_RGB 16-bit form |
| 4, 5 | 32-bit A8R8G8B8 | 4 | 5 | DDPF_RGB, 32 bpp, masks 0xFF0000/0xFF00/0xFF/0xFF000000 |

Every mip is stored as `width>>i * height>>i * bytes_per_pixel` with **no 4x4
block alignment** on the sub-mips, and the chain stops once a side underflows
(no clamping to one pixel).

**Cubemaps.** A `.t` whose payload is exactly six faces of the base level is a
cubemap (`Empire/Apprentice/cubemap_default.t`, code 4).  `build_dds_header`
sets `dwCaps2` (offset 112) to `DDSCAPS2_CUBEMAP` plus all six face flags
(`0xFE00`) for those, and `parse_dds` reads the flag back, so the converted
`.dds` stays a valid cubemap rather than a 2D header over six faces of data.

`d3tool texture convert a.t -o out.dds` and `d3tool texture convert a.dds -o
out.t` perform the conversion based on the destination extension; `d3tool
texture info a.t` prints the header fields.

Forward-export (`export-gl`) auto-detects the material diffuse from the `.g`
and emits a `.dds` (converting the `.t` if present) alongside the glTF,
matching what dis3tool references.  Reverse-export (`export`) converts any
referenced `.dds` image back to the native `.t`.

Known caveat: the `.t` flag at offset 0x18 is not stored in a `.dds`.  It is 0
for some DXT1 diffuse textures and 1 for others at identical size/format, so it
cannot be recovered when converting a bare `.dds`.  The payload, dimensions and
format are always correct; only this flag may differ from a specific source
`.t` unless the original header is preserved.
