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

---

## 5. Implemented in `d3tool/`

* `d3tool/gfile.py` — parse & write `.g` (byte-exact for all bundled files;
  handles `w = 2/3/4` character meshes and passes compound files through).
* `d3tool/gltf.py` — parse the dis3tool glTF; convert to `.g`; rebuild `.a`.
* `d3tool/gltf_out.py` — forward export `.g`/`.a` → glTF + a lightweight
  structural `validate_gltf` self-check (bidirectional tool).
* `d3tool/ac.py` — parse & write `.ac`; detect the real `.a` files.
* `d3tool/anim.py` — parse & write the `.a` animation binary (byte-faithful).
* `d3tool/scene.py` — generate a minimal `.scene`.
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
aside).  The animation is sampled from the `.a` frame count (the `.a` uses its
own frame rate; dis3tool re-samples to 30 fps in the glTF).  The exported glTF
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
| 0x04   | u32    | pixel-format code: 6=DXT1, 7=DXT3, 8=DXT5, 3=A1R5G5B5 (16-bit) |
| 0x0c   | u32    | mipmap level count |
| 0x10   | u32    | width |
| 0x14   | u32    | height |
| 0x18   | u32    | opaque flag (varies; see below) |
| 0x34   | u32    | marker (0x00417000) |

The payload immediately follows at offset 59 (and is byte-identical to the
`.dds` payload at offset 128).  The `@4` format code maps to the DDS fourCC
`DXT1`/`DXT3`/`DXT5` or, for code 3, to a 16-bit A1R5G5B5 surface (used by UI
icons, e.g. `icon_*_ring.t`).

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
