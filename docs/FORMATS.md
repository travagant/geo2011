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

A reader that resolves channels by *name* never notices the dangling
targets; one that indexes the node list without a bound check rejects the
whole document.  Blender's importer is the latter (`IndexError` in
`pre_compute`), so the add-on prunes the dangling channels out of the glTF
it hands over (`importer._prune_dangling_channels`) — they name no joint, so
nothing is lost.  The same import pass repairs Blender's own reading of a
multi-skin compound: the node tree is shared between the skins, Blender
builds one armature per skin, and a node hanging under a joint of *another*
skin comes back bone-parented to a bone its armature does not have.

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

### How many mesh blocks the container declares
The file opens with a chain of `[u32 kind 3][u32 length][payload]` node chunks
(the 120-byte header, the two name strings and the prelude/scene-node block are
three of them) and the chain is terminated by `[u32 1][u32 4][u32 count]`.  The
first mesh block follows immediately, and **`count` is the number of mesh
blocks in the file** — the first mesh plus every compound part.  Every retail
`.g` in the corpus satisfies `count == len(iter_parts(mesh))` (247/247), and
nothing else in the format repeats the number: the attribute blocks carry
per-part vertex and triangle counts, not how many parts there are.

A tool that appends a part has to bump it.  The reverse export appended the
Angel's weapon as a fifth block while the donor prelude said four, and the
engine read exactly four: the mesh was in the file, byte-perfect, and simply
never parsed — "the add-on exports the mesh but the game does not read the
sword out of the model".  `gfile.write_geometry_file` now rewrites the field
from the blocks it emits, `gfile.append_parts` bumps it, and `gfile.mesh_count`
/ `set_mesh_count` read and write it.

### Node identity: `dwNode` / `dwParent`
Both keys are part of every attribute block and name the block's node in the
model's scene graph: `dwParent` is the node the part hangs off, `dwNode` is
the part's own node.  The corpus is unanimous — all 376 sub-meshes of the 141
compound `.g` files share one parent id per file (the file's own `Scene Root`
node) and each carries a distinct node id.  The ids themselves are arbitrary
integers and are *not* the ids of the `.scene`: a retail file's parts use
values that never appear in its scene.

A block written by a tool that cannot know the file's ids gets placeholders
(`d3tool.cli._default_attrs`: `dwNode 375048704`, `dwParent 55867360`) — the
Angel's exported sword carried exactly that pair, so its parent named no node
in the file.  `gfile.normalize_part_identity(mesh, parent)` rewrites such a
block onto the file's own parent (and allocates a node id when one is
missing), and the reverse export runs it before writing the geometry.

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
| 1 (`bones_num 1`) | 40 |
| 2  | 46 |
| 3  | 51 |
| 4  | 56 |

A declared `w = 1` on a mesh that references **several** bones actually stores
the full two-influence record (46 bytes) — the attribute keeps `1`, readers
derive the width from `w` + `bones_num` (independently confirmed on the
Heroes VII port corpus, see `docs/PORT_ANALYSIS_COMPARE_RU.md`).

`w = 1` with a *single* bone is the rigid weapon layout: no weight tail, no
per-vertex joint bytes, one bone descriptor.  Every retail weapon part is
written that way (`empire_knight_sword`, `paladin_sword`, ... — 111 parts over
the corpus), so a weapon that arrives as a two-influence skin is not shaped
like anything the engine ships.

The corpus is absolute about this: **no part declares more influence slots
than it has bones**, and a part with `bones_num 1` always declares `w = 1`
(148 parts).  `(2, 1)` occurs zero times in 376 sub-meshes — the export that
wrote the Angel's sword that way got a part the engine does not skin, so it
was drawn at its stored coordinates, hanging in mid-air beside the model
instead of following the bone.  `gfile.record_width` states the rule and both
writers apply it (`_part_block`, `write_geometry_file`), and the reverse
export clamps an added mesh to `min(w, bones)`.

Binding a rigid part to a bone the body block also skins to is ordinary
retail practice, not a mistake: `empire_defenderoffaith_head` hangs off
`Head`, `neutrals_dwarf_hair` off `Head`, `weapon_empire_hierophant` off
`Weapon1` — all `w = 1`, all with their vertices in model space at the bone's
bind origin and the bone descriptor holding that bone's inverse bind.  That is
exactly the shape the Angel's sword needs, which is why the record width was
the whole of its defect.

Because those vertices are stored in **model space**, a weapon added in
Blender has to be shaped onto the weapon it replaces while the armature is in
its rest pose: the part is bound 1.0 to one bone and the engine adds that
bone's motion on top, so a mesh modelled onto an animated frame comes out
displaced by the animation too.  The add-on's *Place Active Mesh on Reference*
does that measurement for you — grip onto grip, the mesh's principal axis onto
the reference weapon's shaft (`d3tool.weapon`, a 3x3 symmetric eigenproblem
solved with Jacobi rotations) — and `Roll` decides the one thing an axis cannot:
which way the blade's flat face looks.  The grip is the object's own origin,
which is where retail weapons carry it (`weapon_empire_inquisitor` spans
`x -0.066 .. 1.896` measured from its origin); a single selected vertex is used
instead when there is one.  Asking for a vertex selection *always* was a
mistake worth recording: a freshly imported mesh has every vertex selected, so
the operator's "exactly one vertex" check rejected the obvious first click.

The leading 4-byte magic is constant *within* a file but differs between files;
it must be preserved for round-trips.

Two invariants make a rebuilt file readable by the engine, and both were the
cause of corrupted reverse exports until they were enforced:

* whatever `weights_on_vertex` announces must resolve to the width actually
  written (`gfile._eff_width`); a donor may say 3 while the rebuilt mesh only
  carries one influence;
* the light-map block (`lmuvdata`) is **exactly `vertex_count * 8`** bytes —
  the reader takes its length from the vertex count, so a donor-sized block on
  a vertex-split rebuild shifts every following section (`gfile._fit_lm`).

A part exported with tangents enabled additionally carries a tangent stream
right after its bone table: 8 opaque bytes then `vertex_count * 24` bytes
(`gfile._parse_compound_parts` keeps it in `part_tail`).

### What a Blender round-trip costs, measured (Inquisitor, 2026-11)

Re-exporting `Empire/Inquisitor` through the add-on and diffing the result
against the folder it came from leaves exactly three differences, none of them
a defect of the writer, and all three worth recognising before hunting a
phantom bug:

* **the body comes back with 6 more vertices and 4 fewer triangles**
  (`3693 v / 4664 tri` → `3699 / 4660`) although nobody touched the body.
  The four lost triangles are *degenerate* — every index equal — which retail
  ships and the glTF form cannot carry back through Blender; the six extra
  vertices are seam splits.  The surface is the same: 2392 distinct positions
  on both sides.  `audit --against <original>` prints the counts, so a delta of
  this shape is the trip, not an edit;
* **the `.a` differs in the last bits** if the donor stream was not beside the
  glTF at export time (max measured delta `4.7e-06` on `Left_Pola`).  An export
  that *does* see the donor keeps its bytes (`cli._anim_bytes` /
  `anim.same_motion`), so a noisy stream means the donor was missing — the
  first export of a folder, typically, which then donates to all later ones;
* **the `.ac` came back with CRLF line endings** on a machine where the `.ac`
  had to be *generated* (a plain `open(..., "w")` is CRLF on Windows while
  every retail `.ac` is LF).  Survivable — the parser tokenises whitespace —
  but it rewrites 96 of 96 lines and hides real edits in a diff, so the writer
  now passes `newline="\n"`.

Anything *else* that moves between the original folder and the export is a real
difference: the `.g`'s attribute counts, the two `.scene` attribute blocks and
the LOD pair must all agree with the mesh, and `audit` says so part by part.

Two more invariants of the block a rebuilt file must honour, both found by
round-tripping the whole corpus through Blender:

* **the per-material triangle counts are one index buffer split into
  ranges**, so `sum(materialN_triangles_num)` is what every reader trusts.
  A rebuild can legitimately carry one triangle fewer than the donor (the
  glTF form holds one triangle per vertex set, so a reversed-winding twin
  cannot survive the trip) — the difference is absorbed in `material0`,
  the first range, which keeps the ranges behind it aligned;
* **the skeleton root is a joint name, not a bone name**: the forward export
  binds a `.g` bone the `.a` never animates onto the animation's root node,
  which dis3tool always calls `Reference` (the WitchHunter's
  `Marksman_eyelid_up` is the corpus example).  The reverse export maps that
  joint back onto the donor's unanimated bone so the bone table keeps its
  size and the part stays driven by the streams.

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

`frame0`/`frame1` in a state body are **0-based inclusive indices** into the
`.a` stream the state names.  Measured over the 575 bundled states whose `.a`
sits in the same folder: `frame0 0` occurs 23 times (129 states start at 1,
the rest at 5/20/30/45…), and **no** state names a frame equal to its
stream's frame count — the largest value ever written is `count - 1` (125
states), then `count - 2` (26).  A 1-based reading would make `frame0 0`
meaningless and would put `frame1 == count` past the end of the stream, which
never happens.  The one ported pack confirmed working in game (Blade Dancer,
13 clips sliced out of a single `.a`) also writes `start … start + frames - 1`.

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
global header (20 bytes)
  [u32] magic 9
  [u32] body length  (everything the writer emits except the trailing
                      block, minus these 8 bytes — verified on all 152
                      corpus files)
  [u32] bone count
  [u32] frame count
  [u32] frame rate word as spelled by the official writer (15 in 139 of
                      the 153 corpus files, 30 in 12, 16 in 2) — the
                      per-state `fps` in `.ac` is what the engine plays;
                      `build_anim(fps=...)` exposes it
repeat per bone:
  [16-byte preamble: u32 len(name)+1, u32 len(parent)+1,
                     u32 nframes, float32 track scale]
      -- the pair counts the NUL-terminated strings (verified on all
      6764 corpus records); a parent length of 0 means the track has no
      parent at all and *no bytes follow*; the scale varies per stream
      (0.01 is the corpus mode over 15 observed values, dis3tool
      reference exports use 0.02, Heroes VII ports 0.0075)
  [name + NUL]      -- len(name)+1 bytes
  [parent + NUL]    -- len(parent)+1 bytes, or nothing at all
  [7 float32] * nframes  (per-frame TRS: quat xyzw + translation xyz)
optional trailing block:
  vertex-morph streams [u32 14][u32 len-8][u32 tag][u32 frames]
  [u32 vertex_count][u32 name_len][name + NUL] then frames *
  vertex_count * float32 absolute positions (frame-major).  The tag
  follows the header record-type word; some files carry foreign units'
  streams (DarkServant: 7 tracks of other units).
```

The grammar above is not a reconstruction: it is what the official Max
exporter `geo2011.dle` writes (disassembly of VA `100077c0` / `10007610` /
`10020d20`, cross-checked against all 153 corpus files — see
`docs/PORT_ANALYSIS_COMPARE_RU.md`).  An earlier "36-byte header + 16-byte
per-track quaternion prefix + one-frame position shift" reading of the same
bytes is wrong: those 16 bytes are simply the next track's preamble.

The per-frame TRS is exactly what dis3tool exports into the glTF animation
`bones_rotate` (`VEC4` quat) and `bones_translate` (`VEC3` trans) buffers, so a
**full round trip** is possible: `d3tool/anim.py` parses the header and the bone
records (names, parents, per-frame TRS) and `write_anim` re-emits them
byte-for-byte.  The `.a` record set is exactly the set of nodes animated by the
glTF (Root + every bone), in hierarchy order, so `d3tool/gltf.py::animation_from_gltf`
rebuilds a `.a` from the glTF animation channels with matching per-frame values.

Reverse rebuild details (byte-parity with the originals, see
`tests/reverse_parity.py`):

* a node animated through a morph-target `weights` channel only (a
  morph-deformer mesh) is **not** a bone: its animation lands in the
  trailing block as vertex-morph streams; dis3tool stores each baked
  frame verbatim as a glTF morph-target POSITION accessor, with the
  stream name in the `morph_<stream>_<k>` bufferView names;
* a unit whose `.ac` names several `.a` files is exported as ONE
  concatenated glTF animation (`concat_anims`); the reverse rebuild
  slices the named stream back out at the donor frame-count boundaries
  (primary slots read the same record index per stream, appended bones
  keep the donor records) — and it writes **every** named stream, not
  just the Idle one, so no `.ac` reference dangles.  A `<mesh>_lod.g`
  concatenates the streams of its **own** `<mesh>_lod.ac` (lod stream
  pairs, e.g. cleric `iadd_lod` 356 + `run_lod` 25 = 381), falling back
  to the main `.ac` when no lod config ships (FatImp); the concatenation
  is always in `.ac` order, which is what selects the morph targets
  (they come from the last stream).  A variant stem with no `.ac` of its
  own (the leader set1/2/3 exports) adopts the folder's sole sibling
  stream as a verified donor and rebuilds byte-identically to it;
* records the reference animates through nodes outside the node array
  (Wildboar targets node 37 of 0..36) are recovered positionally from
  the channel order, verified against the donor `.a`;
* the original `.a` next to the source glTF donates the record-type
  word and magic, the record order/parents/preambles and the trailing
  block (tags + foreign streams), adopted when the rebuilt data
  verifies against it.
* a **Blender re-save** cannot keep the `.g` bind frame: Blender bakes the
  scene's rest pose into the joints (its bone axis is the bone's Y, the GM
  skeleton's is not), merges the per-part skins into one armature-wide skin
  and re-parents the meshes under the armature, so the channel TRS are local
  matrices in *Blender's* frame.  Every record therefore goes back through
  the calibrated per-bone change of basis `L_o = Q_parent · L_b · inv(Q_j)`
  (`d3tool/frame.py`; `Q` is solved from the donor `.a`'s frame 0 against the
  glTF's rest TRS, and is constant in time — verified on a real export, where
  `inv(G_new)·G_retail` drifts by 0.0 over 301 frames for all 27 bones).
  The animation side used to do this only for a unit whose `.ac` names
  several streams; every single-stream unit (62 of the 153 corpus `.a`, the
  Inquisitor among them) shipped Blender-frame rotations — off by up to 180°
  per bone and 1.47 units of translation, which contorts the skinned body
  into spikes, throws a rigid weapon across the model, and still parses with
  every count and size matching.

---

**Naming a part's texture from Blender.**  The add-on records each part's
`material0_diffuse` on its mesh object as the custom property
`d3unit_diffuse` (set on import, editable in the *Disciples III* sidebar panel,
or *Object Properties > Custom Properties*).  On export it is written into the
glTF as `meshes[i].extras.d3unit_diffuse`, `d3tool/gltf.py::_extras_diffuse`
lets it outrank both the primitive's image and the donor part's attribute, and
the ordinary export path then restates the `gobj` of both `.scene` files from
the written parts (`d3tool/scene.py::refresh_part_gobjs`) and carries the `.t`
(`d3tool/cli.py::_carry_material_textures`).  That is the supported way to make
a grafted part wear another unit's texture, since the name the engine reads
lives in the `.scene`, not in the material.

**The viewport has to agree with the file.**  Naming a part changes nothing
on screen: Blender keeps drawing the image the material had, and only the `.g`
gets the new name, so a correct re-texture looks exactly like a broken export.
*Show in the Viewport* (`d3unit.apply_texture`, run automatically after the
name is typed) resolves the name to the file that ships it
(`d3tool/texture.py::find_texture_file` — `<stem>.t`, `<stem>.dds`, a doubled
`<stem>.dds.t`, any case), unwraps a `.t` into a temporary `.dds` because
Blender has no `.t` loader, and puts it on the material's image node.  The
export then reports what it actually wrote, part by part, read back out of the
files it just saved (`d3tool/parts.py::texture_report`), and warns about a name
whose texture is not in the folder — plus about every part that has no
`d3unit_diffuse` of its own, i.e. one imported before 1.1.27 or hand-assembled.

**Writing a `.t` compresses it.**  The container can hold raw A8R8G8B8
(format code 4) as well as DXT blocks, and the raw form is eight times the size
for identical pixels — a 128x1024 blade is 683 KiB raw and 86 KiB as DXT1.
`png_to_t` (a texture re-saved from Blender) and every carried atlas are now
block-compressed: DXT1 normally, DXT5 when the image really uses alpha.  The
mip chain follows the container's own accounting — each level is exactly
`w * h * rate` bytes with no 4x4 padding, so a re-encoded 128x1024 blade is
87 439 bytes, the same as the shipped file.

**Same atlas, different weight.**  A `.t` can hold DXT1 blocks (the Knight's
blade atlas: 128x1024, 86 KiB) or raw A8R8G8B8 pixels (the same picture
re-saved from Blender: 683 KiB) — an eightfold difference in size for identical
pixels, which reads like the wrong texture in any report that only prints
bytes.  `d3tool.texture.describe(path, True)` prints the size *and* the layout,
and `find_texture_file` prefers a block-compressed copy when a folder holds
several files with one stem (an exact `<stem>.t` still wins: that is the
shipped file).

**Materials are numbered per part, in slot order.**  Every shipped part has
exactly one material (`materials_num: 1`, `material0_diffuse`,
`material0_triangles_num`); a part that really carries two ranges has
`material0_*` and `material1_*` and the export rewrites both, each from its own
primitive's atlas.  In Blender the numbering runs once across the unit in part
order, so the body is `material0` and the weapon `material1` — without it
Blender's own uniqueness rule hands out `material0`, `material0.001`,
`material0.002`, names the engine never sees.  A part that names no texture of
its own takes the atlas off the material it wears (an image node, or a material
named after the atlas); a *packed* image never reaches the glTF as a URI, which
is why reading the name off the material itself is what makes "assign the
sword's atlas to the sword" work.

**Whose name wins.**  Two rules, and the second one was wrong for a long
time: a Blender re-save's part takes its texture from the *mesh*, not from the
donor slot it replaced (`slot.material_diffuse or sub.material_diffuse` pinned
a swapped sword to the mace's atlas), and the name is written from the block's
ordered `attr_items` — `attrs` only drives the counts, so setting
`material_diffuse` alone changes nothing in the file
(`d3tool.gltf._patch_material`).  A `d3unit_diffuse` that still says what the
part was imported with yields to the object's material once they disagree
(`_effective_diffuse`); any other value is the artist's decision and wins.

A part's material name is stored in **three** places that the engine reads
independently: the `material0_diffuse` attribute of the block in the main
`.g`, the same attribute in the `_lod.g`, and the `attr "material0_diffuse"`
of the matching `gobj` in each `.scene`.  Re-texturing a part means rewriting
all of them (`tools/retexture_part.py`); an export that only changes the
geometry leaves the scene naming the donor's texture, and the engine keeps
drawing that one.

### Texture naming: the stem is the material name

The attribute block names the material `material0_diffuse "<stem>.tga"` and
the folder ships `<stem>.t` — the engine matches the two by **stem**, dropping
the extension.  Everything that derives a name from an image has to reduce it
to that stem first: a texture that entered Blender as `x.dds` is re-saved as
`x.dds.png`, and a plain `splitext` leaves `x.dds` in the stem, producing a
material `x.dds.tga` and a file `x.dds.t` — a pair nothing can match, so the
part renders untextured in game and in Blender while every file looks present
(`d3tool/texture.py::clean_stem`).  The same rule applies in the other
direction: an export has to **carry** every diffuse its own `.g` names
(`d3tool/cli.py::_carry_material_textures`), because a unit re-textured from
another unit's folder otherwise names a texture it does not ship — invisible
in game, where the retail copy still resolves, and immediately visible the
moment the export is read back.

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
  `bones`/`gobj` node names, full `globalsettings`, attribute block);
* in both cases it then calls `ensure_part_gobjs`: **the engine binds the
  i-th mesh part to the i-th `gobj`, in document order**, and `attr "name"` is
  decoration — the LOD scenes prove it, since their gobjs are named
  `empire_angel_wing` while the parts they draw are `lod_empire_angel_wing`.
  Every shipped scene carries one `gobj` per mesh part (159 of 160 exactly;
  the Cyclop keeps a spare), where "one part" counts the file's own first
  block — `iter_parts(mesh)` — not just `mesh.parts`.  A `.g` with more parts
  than the scene has gobjs leaves the *trailing* parts unbound and invisible,
  so each one gets a `gobj` cloned from the largest opaque sibling: same
  `tech`, shaders, flags, `.g` path and `bonesuid`, the next free mesh slot,
  and the attribute block re-stated for that part (`attr "name"`,
  `material0_diffuse`, `material0_triangles_num`, `vertexs_weights_num`,
  `weights_on_vertex`, `bones_num`) with a fresh `uid`/`dwNode`.  The function
  is idempotent and returns the text unchanged when every part is bound,
  which is the whole bundled corpus — the reverse-export parity bench stays at
  84/84 byte-exact with the fix in place.
* a part added to `<base>.g` must also reach `<base>_lod.g`, because a unit's
  profile names a second scene for distance: `unit_scene_lod
  "…\character_empire_angel_lod.scene",10;` — beyond 10 the engine swaps to
  the LOD mesh, which is a separate, lower-poly `.g` (`lod_empire_angel*`,
  1869 verts against 4165).  `d3tool lod <unit folder>` appends the parts the
  LOD lacks (its parts are the main mesh's, in the same order, so the added
  ones are the trailing tail) with `gfile.append_parts` — a `.g` is a sequence
  of self-contained part blocks, so the new part is concatenated byte for byte
  and not one byte of the original file moves — and then binds it in
  `<base>_lod.scene` the same way.

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

## 4c. The `.char` unit profile

Text, one file per unit under `Profiles/`.  The retail tree this repository
now carries holds **874** of them: 214 root profiles plus the three fragments
each root `#include`s — `desc\` (220 files, the browser strings), `stats\` (226,
the numbers) and `sound\` (214, the voice/weapon events).

A root profile, verbatim (`Profiles/demons_beast.char`, backslashes trimmed to
one for reading):

    //Unit Profile
    //
    id                       "demons_beast";
    unit_scene              "resources\characters\demons\beast\character_demons_beast.scene";
    unit_scene_lod          "resources\characters\demons\beast\character_demons_beast_lod.scene",10;
    icon                     "resources\characters\demons\beast\icon_beast.t";
    subicon                  "resources\characters\demons\beast\icon_beast_small.t";
    layout_icon              "resources\characters\demons\beast\icon_beast_node.t";
    capital_icon             "resources\characters\demons\beast\icon_beast_capital.t";
    dialog_icon              "resources\characters\demons\beast\icon_beast_dialog.t";
    leader_icon              "resources\characters\demons\beast\icon_beast_ring.t";
    fxproc                   "lighting";
    fxscene                  "resources\fx\Arena_FX\Physical_Damage\fx_small_attack_damage_2x.scene";
    fxcast                   "resources\fx\";
    fxscene_ko               "resources\fx\Arena_FX\Death\fx_die_demons.scene";
    fxlevelup                "Resources\FX\Arena_FX\LevelUp\fx_demons_levelup.scene";
    centerbone               "Spine1";
    move_speed                2;
    nodesx                   1;
    nodesz                   1;
    #include                 "desc\demons_beast_desc.char";
    #include                 "sound\demons_beast_sound.char";
    #include                 "stats\demons_beast_stats.char";

and the fragments it pulls in:

    desc\   nameid "DemonsBeastName";  descid "DemonsBeastDesc";  minidesc "DemonsBeast_MD";
    sound\  sndevent_attack "$(Sounds)\sound_attack\attack_claw_1.wav";   (+ step/damage/ko/custom1)
    stats\  unit_class melee;  unit_side "legions_giant";  ship "legions2";
            attack_type "slash";  level 22;  side legions;  exp_base 159;
            str/dex/agi/vit/spd;  ap/up/mov/movwm;  atck_range;  zone_radius;
            atck_zone;  Control;  Cover;  res_<damage type>;  cost;
            leadership_cost;  skill_grid "<stem>";  viewradius;  *_mod;  level_cap

Notes that matter for a parser (111 distinct keys occur across the tree):

* **identity is the `id`, not the file name.**  Three shipped files disagree
  with their name — `story_berthrezen.char` holds `story_Berthrezen` (case),
  `story_myzrael_undead.char` holds `empire_myzrael_undead`, and
  `undead_templar .char` (note the space) holds `undead_templar`.  Nothing
  opens a profile by path, so a new browser entry is a copied profile with a
  fresh `id` — which is how the port's own `Profiles` tree adds units.
* **`;` is optional in practice.**  212 of the 214 root profiles lose one,
  always on the last field of a run (`leader_icon` in `elves_rod-1.char`,
  `unit_scene` in every leader profile).  A statement therefore also ends at
  the end of its line.
* **`/* … */` comments span lines** (`elves_rod-1.char` keeps a whole
  commented-out attack zone grid that way) and `//` is ordinary.
* **backslash runs are not escape sequences.**  The same path is written with
  one, two or four `\` separators — sometimes in the same file (`unit_scene`
  vs `unit_scene_lod` of `demons_beast`).  Windows collapses them, so the
  toolkit keeps the bytes and compares through one normalised spelling.
* `unit_scene_lod` carries a second value, the draw distance (`10` or `20`);
  `shadowtexdim 512,512;` and `move_speed 4.5;` are the only other
  multi-value/float forms.
* `unit_class melee;`, `side legions;` and `atck_zone;` are values without
  quotes; `unit_side "legions_giant"` is quoted.  Keys are mixed case
  (`Control`, `Discount_Mod`, `Resources\…`).
* `$(Sounds)` / `$(Texts)` values are engine macros, not `resources\` paths.
* 869 files are UTF-8, 5 are CP1251 (`story_amina`, `undead_ghost`,
  `undead_shade`, `undead_specter`, `desc\undead_wyvern_desc`); none has a BOM.
* **leaders carry equipment sets**: 21 profiles add `set 1|2|3 { … }` blocks
  with one scene per slot (`weapon`, `feet`, `body`, `head`, `legs`, `arms`,
  `hands`), which is why a leader's geometry is a `.scene` of several `.g`
  parts rather than one mesh.

`d3tool/char.py` parses a profile into raw lines plus positioned statements,
so `write_char` reproduces a file byte for byte (all 874 do, CP1251 and CRLF
included) and `set_value`/`set_id` patch one string without reformatting the
rest.  `ReferenceIndex` maps a `resources\characters\<faction>\<unit>\…`
reference onto either the game's data folder or this repository's donor
corpus, where the same unit lives at `<Faction>/<Unit>/`: **86 of the 214
profiles resolve their `unit_scene` here** (all of Empire and the Neutrals;
Demons, Elves, Undead and Story are not in the corpus), and 85 of those have
the `.g` next to the scene.  `d3tool char show|list|find|validate|set-id`
exposes that, `d3tool analyze <unit folder>` prints the profile that points at
the folder when one is present, and `vdpack validate` checks a packed profile's
includes and `characters\…` references like every other unit-local reference.

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
* `d3tool/weights.py` — read the skin weights of a `.g` back out: the
  influence width per part, the bones each part is bound to, the per-vertex
  weight sum, and a donor-vs-export diff (`weights changed` / `dominant bone
  moved`).  Comparisons ignore the sub-byte residue lanes the donor keeps, so
  an untouched round trip reports zero changes.
* `d3tool/char.py` — parse & write `.char` unit profiles (byte-exact for all
  874 shipped files); `ReferenceIndex` resolves a profile's `resources\…`
  paths against the game folder or the donor corpus; `set_id`/`set_value`
  patch a single value without touching the layout.
* `d3tool/cli.py` — `analyze`, `export` (glTF → original), `export-gl`
  (original → glTF), `validate`, `import`, `texture`, `vdpack`, `char`,
  `lod`, `gattr`.

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

**PNG re-encode (Blender textures).**  Blender saves a glTF's textures as
`.png`.  `d3tool/texture.py:png_to_t` decodes a PNG in pure Python (8-bit
grey/RGB/palette/grey+alpha/RGBA, no interlace) and writes an
*uncompressed* 32-bit A8R8G8B8 `.t` (code 4) with the full GM mip chain
(each mip `w>>i * h>>i * 4`, no clamping to one pixel, 2x2 box average).
The reverse exporter prefers the byte-faithful sources first: the sibling
`.dds` (converted), then the shipped `.t` (copied); the re-encode is the
last resort so a bare folder still yields a loadable texture.

Forward-export (`export-gl`) auto-detects the material diffuse from the `.g`
and emits a `.dds` (converting the `.t` if present) alongside the glTF,
matching what dis3tool references.  Reverse-export (`export`) converts any
referenced `.dds` image back to the native `.t`.

Known caveat: the `.t` flag at offset 0x18 is not stored in a `.dds`.  It is 0
for some DXT1 diffuse textures and 1 for others at identical size/format, so it
cannot be recovered when converting a bare `.dds`.  The payload, dimensions and
format are always correct; only this flag may differ from a specific source
`.t` unless the original header is preserved.

---

## 6b. Why the retail binaries cannot be read on disk

Every question this document still leaves open — the `index` record layout, the
`.g` invariants the loader actually enforces, where a bad unit kills the
process — lives in code that ships protected.  Measured on the copies provided
for this port (`google_drive/`, kept out of git):

**`Toolkit.exe`** (6 225 920 B, the editor).  Twelve sections, names repeated
(`.text .rdata .rdata .data .data .text .rdata .discard .discard .data .rsrc
.ps4`); `.text` 4 993 024 B at entropy **8.00**, `.rdata`/`.data` likewise, so
the code and the string table are both ciphertext.  Not one printable run
exists anywhere in the file — `resources`, `MZ`-adjacent names, `StarForce`,
`protect.dll`, `sfdrv01` all absent; the only survivors are `ijaqemox.dll` in
`.rdata` and a stray `UPX` at 0x6ea81.  `CheckSum` is 0 against a computed
0x5f6971, there is no TLS directory, no debug directory, no certificate table,
no version info and no manifest.  The entry point is a three-times repeated
`jmp dword [0x126af90]` whose target lies outside `SizeOfImage` — a slot the
loader fills at run time.  A `.data` section declares `VirtualSize` 8 716 288
with only 98 304 B of raw data: an unbacked multi-megabyte hole where the real
image is written after decryption.  The imports were rebuilt by the protector
(the engine is reached as `ijaqemox.dll!ordinal 1`, the one import with no
name), and the readable remainder is 20 KB of position-independent,
junk-interleaved stub at VA 0xdab000 that ends in `call $+5 / pop / sub` and
`xchg [esp], ebp / ret` — a computed return, not a call graph.  `.ps4` is an
empty section with no raw data.

**`ijaqemox.dll`** (10 642 096 B, the engine module).  UPX-named sections
(`UPX0` VA 0x1000, 5 861 376 B virtual and 0 raw; `UPX1` VA 0x598000,
10 633 216 B virtual, 10 632 704 raw), a `UPX!` signature at 0x3e0 and the
string `3.02` at 0x3dc — and a header that decodes to nonsense: `l_magic`
0x451f7e68 where a UCL stream carries 0x01308198, an implausible `l_size`, a
block method byte of 0, and a block table no scan can recover.  `tools/unpack_upx.py`
is a complete NRV2B / NRV2D / NRV2E and raw-LZMA decoder written from the UPX
3.96 kernels (`src/stub/src/arch/i386/nrv2*_d32.S`); run at every byte offset
from 0x3e8 to 0x42c it produces at most 16 bytes before the first match points
behind the output.  The payload is therefore not a UCL stream: the UPX header
is a costume over a crypter.

**What that means for this work.**  A protected image exists in clear only
inside a running process, and the one artefact that carries it without a
debugger, an unpacker or a modified executable is a **minidump** of that
process — its own licensed execution.  `tools/dump_extract.py` reads one
(`MDMP`, streams 3/4/5/6/7/9) and emits, per module, the image exactly as the
process had it plus a `.rebuilt` copy whose section pointers point at their
virtual addresses, which is the only fix a memory image needs before a reader
can walk it.  It also prints the crash: the exception code and address, the
module that owns it with the offset into it, and every return address found by
scanning the faulting thread's stack and resolving it to `module+offset`.
`--strings` writes the printable runs of one module, which is how the loader's
own names (`index`, `resources\`, `vdpack`) become visible, and `--module`
limits the work to one image.

**Getting the dump without tools.**  A dump taken by hand only catches the
process while it is still alive, so the crash itself is missed.  Windows Error
Reporting already writes one at the moment of the crash if it is told to:
`tools/windows/enable_localdumps.reg` sets `LocalDumps` for both registry views
(a 32-bit game on 64-bit Windows reads the `Wow6432Node` one) with
`DumpType=2` (full, so the module images are actually in the file, not just
their headers) and `DumpCount=10`, and points the folder at
`%USERPROFILE%\Desktop\D3dumps`.  Double-click it, accept the elevation
prompt, reproduce the crash, and send the newest file from that folder;
`disable_localdumps.reg` removes both keys again.  A `MiniDumpNormal` dump is
useless here even though it carries the exception record: it does not contain
the module pages, so the decrypted code is not in it.

So the order of operations for every remaining question is: arm the crash dump
(or take one by hand from `Task Manager -> right click -> Create dump file`, or
`procdump -ma <pid>`), reproduce, run `dump_extract.py`, read the module.
Nothing in this document has to be guessed from statistics as long as that
artefact exists.

**What statistics cannot do: the `index` entry.**  The one `.vdpack` entry no
tool decodes was attacked every way that does not need the loader, on the only
independent sample available (28 entries, 4 738 bytes, 1 674 bytes of names):

* entropy 7.96 with all 256 byte values present, no repeated 4-byte or 8-byte
  word, length 4 738 ≡ 2 (mod 8) — a cipher or hash construction, not a table
  with slack;
* no stride structure: over every record size r in 1…399 the count of equal
  bytes at distance r stays inside the noise band expected for random data
  (best |z| ≈ 3.1 at r = 140, against 399 trials), so there is no fixed-size
  per-entry record anywhere in it;
* not compressed at any offset 0…95 as zlib (all three window modes), raw or
  aligned LZMA, or bzip2;
* not a single-byte XOR, and none of the paths appear in it in ASCII, upper
  case, or UTF-16LE;
* none of the obvious four-byte name digests appear either — CRC32, Adler32,
  FNV-1a, DJB2, or the first four bytes of MD5/SHA-1, in either byte order.

The consequence is worth stating plainly: `index` is not recoverable by
inspection, because it is almost certainly keyed by a constant inside the
packer.  That constant is in `Toolkit.exe`, which is encrypted, so the constant
comes out of a dump like everything else here.  Independent packs would help
one question only — whether the blob depends on the entry set or on the names
alone — and packs rebuilt by this toolkit cannot answer it, because they inherit
the `index` they were given.

## 6c. The game executable is readable, and it names the pack rules

`DisciplesIII.exe` (5 914 624 B, "rebirth exe #68 May 21 2012") is a different
animal from the editor: six sections, `.text` 4 539 116 B at entropy **6.54**
and `.rdata` at **5.78**, **24 277 printable strings** — real code and a real
string table.  The only encrypted-looking region is `.bind` (376 832 B at
entropy 8.00), which is the Steam stub; `.data` again declares 8.6 MB of
virtual space against 98 KB of raw data, but that is uninitialised BSS, not a
protector's hole.  Everything this document was waiting on can therefore be
read out of the file, and `tools/pe_find.py` is the bench for it: resolve a
string to its address, an address to the `push`/`mov` sites that use it, and a
site to an annotated disassembly (the image is PE32 with absolute addressing,
so a `.rdata` address appears in `.text` as a 4-byte immediate).

**The pack layer mounts a set, not a file.**  `.rdata` holds the names the
loader asks for, in this order of assembly: `system.vdpack`,
`resources0.vdpack`, `resources1.vdpack`, `resources2.vdpack`, then the patches
`system01..03.vdpatch`, `resources01..03.vdpatch`, then a `*.vdpack` scan
(0x878444–0x8784d4).  Mods are ordered and checked against each other, and the
messages say what the checks are: `"%s '%s' has wrong type"`,
`"%s '%s' has wrong index"`, `"%s '%s' has wrong files : %s"`,
`"%s '%s' intersects other mods"`, `"Error load %s '%s' index"`,
`"Error open %s '%s' pak file"`, `"Index error : %s : %s"`
(0x8785b8–0x87863c, referenced from the loader at 0x4149fa and 0x414bac).  A
mod that fails any of these is not mounted — which is a *stronger* explanation
for "the unit does not appear at all" than anything a payload check can give.

**The `index` entry is a mod descriptor, not a file table.**  The strings next
to it name the fields: `MOD_INDEX_FILE`, `MOD_INDEX_TYPE`, `MOD_INDEX_UUID`,
`MOD_INDEX_NAME` (0x8782f4–0x878334), the field names `Name`, `UUID`, `Hash1`,
`Hash2` and their serialised forms `Name="`, `UUID="`, `Hash1="`, `Hash2="`
(0x878510–0x87854c), plus `:index` / `index` (0x878344, 0x87834c) as the VFS
path the loader opens.  So the entry carries an identity (name, type, uuid) and
**two hashes of the file list**, and the loader validates the list against them
before it mounts anything (the same region logs `"Not found indexed file '%s'"`
and `"Unindexed file '%s'"` at 0x8782a8/0x8782c4 for a file that is present but
absent from the index, and the reverse).  The 4 738-byte blob a unit pack
carries is consistent with that list serialised and encrypted: 1 674 bytes of
names become 4 738 uniform bytes, an unstructured ~2.8× expansion — not a fixed
record table, which is what the earlier "≈169 B per record" reading assumed and
why no record stride exists (see §6b).

Two consequences that change how a pack should be built:

* a pack whose file list differs from the one its `index` describes is
  rejected outright (`has wrong index` / `not found indexed file`), and carrying
  the original `index` into a modified pack is exactly that mistake;
* name lookup itself is a hand-inlined **case-insensitive `strcmp`** over the
  entry list (0x41a540–0x41a56b compares two bytes at a time, `sbb` for the
  ordering), so paths are not hashed and a path only resolves if an entry
  spells it.

The engine also checks the executable it is running from — `str_cant_read_exe_file`,
`str_main_index_is_wrong`, `str_error_load_main_index` (0x878568–0x878598) — so
patching the binary alive is not the way in; reading it is.

What is left to pin down in this file, in order: the routine at 0x419830 that
opens `index` through the VFS and hands the payload to the decoder (the call
chain at 0x419963 continues through 0x41b030, a vector splice, to a virtual
`[eax+0x10]`), and the hash the two fields hold — the binary carries CRC32
(0xEDB88320 at 0x11837), MD5 (init 0x67452301 at 0xa56e) and SHA-1
(0x6A09E667 at 0xe568) implementations, so the field is one of those over some
canonical rendering of the list.  Once that is named, an index can be
*recomputed*, and a pack that carries modified payloads becomes mountable.

## 6d. The packer is readable too — and it writes the `index`

Two further builds of the editor were provided: `TOOLKIT_EXTENDED.EXE` and
`TOOLKIT_CRACKED.EXE`, both 6 070 784 B, differing in 45 163 bytes.  Neither is
protected: four sections, `.text` 4 993 024 B at entropy **6.57**, `.rdata` at
5.63, and the strings are there — `vdpack` ×9, `index` ×29, `Hash1` ×2,
`resources\` ×127, `material0_diffuse`, `weights_on_vertex`.  So the packer side
of every open question is reachable from a file, and the *cracked* build is not
needed for it: the extended one is just as readable, and nothing here depends on
the protection being removed.  (`okidifox.dll`, 8 358 576 B, is a different
story: `UPX0`/`UPX1` with 7.94 entropy and the same patched header as
`ijaqemox.dll`, so the game's engine *module* still only exists in clear inside
a running process — the exe of §6c and these toolkits cover the rest.)

What the packer says about the `index`:

* **`.d_hash.cpp`** is compiled in (string at 0x8c6158, asserted from
  0x40b2dc, 0x40b351, 0x40f564 and 0x40f60c).  The module carries **MD5**
  (round table from 0x40aa09, init 0x67452301 at 0x40b280), **SHA-256**
  (K[0] 0x428a2f98 at 0x40b5ea) and **CRC32** (polynomial 0xEDB88320 at
  0x12e17).
* The index is **written as text**: the builder at 0x41eeb0 registers the keys
  `name`, `UUID`, `Type`, `File` (ids 0x32–0x35) against the resource ids
  `MOD_INDEX_NAME`, `MOD_INDEX_UUID`, `MOD_INDEX_TYPE`, `MOD_INDEX_FILE`
  (0x8c6d70–0x8c6db0), and `UUID = "` (0x8c6c08) is the serialised form.
* The two hashes are written per file by the routine at 0x41d860: `Hash1` at
  0x41dd0b, `Hash2` at 0x41dbbc, and a **48-byte** digest — twelve dwords —
  is copied into both fields from a buffer built around the call at 0x41d886.
  Sixteen plus thirty-two is MD5 plus SHA-256, which is the pairing the text
  form implies: `Hash1="…32 hex…"`, `Hash2="…64 hex…"`.  The per-file record
  these live in is **76 bytes** in memory (`add esi, 0x4c` in the vector
  splice at 0x41af40).
* `can't calculate file hash for: '%s'` (0x8c6dc0, used at 0x41f571) is the
  failure path when a file named by the index is not readable.
* The packer can build the whole thing headless: the `-createindex` switch
  (0x8c65b8) is read at 0x412515, and `:index` (0x8c6eb4) is opened at
  0x4207dc — the pack read/write path.  The editor's own packs are
  `toolkit_system.vdpack` and `toolkit_resources.vdpack` (0x8c6d2c, 0x8c6d50).

So an `index` is not a mystery any more, it is a *derived artefact*: a textual
table per file — name, uuid, type, `Hash1` = MD5 of the content, `Hash2` =
SHA-256 of the content — serialised and then stored in the pack, which is why
the stored bytes are uniform.  §6f closes the last two gaps: the storage
transform and the grammar, both of which `d3tool` now implements in both
directions.

## 6e. The engine's own attribute vocabulary and diagnostics

The mesh loader in `DisciplesIII.exe` (`.d_rc_mesh.cpp`, 0x89c488) keeps its
attribute names and its complaints in one run of `.rdata` (0x89c004–0x89ccf8).
Read together they are the closest thing to a specification of what a `.g` may
say, and they confirm the vocabulary this toolkit writes:

* per material — `material%d_diffuse`, `material%d_detail`, `material%d_bump`,
  `material%d_lightmap`, `material%d_triangles_num` (0x89c990–0x89c9fc);
* per mesh — `materials_num`, `vertexnum`, `trianglesnum`, `bonesnum` /
  `bones_num`, `texnum`, `tex%d`, `texname%d`, `uniquename`
  (0x89ca40–0x89cb8c);
* vertex data — `normals`, `tangent`, `weights_on_vertex`,
  `new_vertex_weights_format`, `vertexs_copy`, `rotatez`, `df_lm_nm`,
  `lmuvdata`, `bumpuvdata`, `detailuvdata`, and the morph family
  `morph_frames`, `morph_normal`, `morph_cyclic_blend`, `morph_uv%d`
  (0x89c448–0x89caa8);
* material binding — `system\materials.s` with `material "%s"` and
  `invalid name in materials list` (0x89caa28–0x89caad8), plus `vdmtl` and
  `groupname` (0x89c514–0x89c51c).

The diagnostics separate cleanly into **cosmetic** and **structural**, and one
of them says so in words:

* `Object '%s' have no material - checker assigned` (0x89ca10) with
  `CreateCheckerIfNoTexture` (0x89c5f0) — a part with no material still draws,
  wearing the checker texture.  Cosmetic.
* `%s has empty texture` (0x89caf8), `0 triangles in object: '%s' with
  material: %d` (0x89c8fc), `Invalide bones num:%d` (0x89cab0),
  `*** Error: out of memory: mesh "%s"` (0x89cad0) — the mesh is built from
  declared counts, so these are the structural ones: a count that disagrees
  with its data.
* `error MeshMake, texture "%s" not found in model "%s : %s"`,
  `error MakeMesh, not found uv set ...`, `not found technique "%s", set in
  mesh "%s"`, `unknown mesh`, `invalid object: %s` (0x89cc40–0x89ccf8,
  0x89c980) — the binding failures: a name the model does not carry.

`d3tool audit` already checks the structural half of that list — `materials_num`,
`material0_triangles_num`, `vertexs_weights_num`, `weights_on_vertex`,
`bones_num` against the counts actually found, and "no triangles at all" per
part (see `d3tool/audit.py`), so this run of `.rdata` is a confirmation rather
than a to-do list.  Two items it does *not* cover yet and that the engine names
explicitly: a `.t` that exists but is empty (`has empty texture`), and a
`technique` that is not in `system\materials.s` — the first is cheap to add,
the second needs the materials file to compare against, which retail ships as
text.

## 6f. `index` decoded: the mod manifest, and what the loader checks

The storage transform is in the packer at **0x40a290**, and it is thirty
instructions: compare the first four bytes against `65 43 74 FF`, load the next
four as a seed, then step a 32-bit LCG and XOR its high byte into every byte
that follows:

```
[0:4]   65 43 74 FF                 magic
[4:8]   u32 little endian           seed
[8:]    text + NUL                  ^ ((state >> 16) & 0xFF) per byte

state = state * 0x34405 + 0x269EC9  (mod 2**32, stepped before each byte)
```

`imul ecx, ecx, 0x34405` / `add ecx, 0x269EC9` / `shr edx, 0x10` / `xor dl, bl`
is MSVC's `rand()`, so this is obfuscation, not encryption — the seed travels
in the file.  Run over the 4 738-byte `index` of `ruru.vdpack` it yields 4 730
bytes of text, and re-encoding that text with the same seed reproduces the
shipped payload **byte for byte**, which is the test that says the transform is
understood rather than approximated.

The text (`\n` line ends, quotes mandatory, `;` terminator, this order)::

```
Name = "ruru";
UUID = "6af75149-9223-4e52-b5cc-ccb9ac990071";
Type = "Mod";
File = "resources\\Characters\\Empire\\Inquisitor\\x.g", "<32 hex>", "<64 hex>";
...
```

`Name`/`UUID`/`Type` are the mod's identity, and one `File` line follows per
indexed resource: the path in archive spelling, the **MD5** of the payload,
then its **SHA-256**, lowercase.  In `ruru.vdpack` all **26** records verify
against the payloads they name — no exceptions — and the two entries that are
*not* listed are `index` itself and the root-level `russian.txt`.  So the
manifest covers the resource tree, not the archive.

Two questions the game answers differently, and both matter when planning a
pack:

* **A payload whose bytes no longer match its recorded digest is rejected
  outright.**  This was written as "probably fine" for one release, on the
  strength of the Angel pack (both `.g` payloads replaced, original `index`,
  battle loaded).  A crash dump of the shipping build (§6g) settled it the
  other way: the engine logs `Index error : str_not_valid_hash_for_file :
  <path>` and does not hand the file's data to the caller.  The digest is
  compared, not merely recorded.
* **A path with no `File` record is the failure.**  That is what the port did
  when it added entries, and the game showed no units — with `Unindexed file
  '%s'` (0x41a583) as the loader's own wording, and `Not found indexed file
  '%s'` (0x41a5c7) for the mirror case of a record whose file is gone.

Which means the "replace only, never add" rule that this toolkit enforced was
a limitation of *our* side, not the engine's: nothing in the archive format
forbids a new path, it just needs a `File` record.  `d3tool` writes those now —
`vdpack index <pack>` decodes and verifies a manifest, `vdpack pack --reindex`
and `vdpack pack --like <pack> --reindex` build one for the exact bytes that
land (keeping the mod's `Name`/`UUID`/`Type`), and `vdpack diff` grades a
rebuilt pack against the two questions above instead of guessing.

## 6g. The crash dump: the hash check is enforced, and it is why the unit dies

A minidump of the shipping build (`DisciplesIII.dmp`, 639 667 B) closes the
question §6f left open.  What it says:

| field | value |
|---|---|
| process | `D:\Disciples III Rebirth\DisciplesIII12.exe`, base 0x400000, 14 446 592 B — a *Rebirth* repack, not the retail `DisciplesIII.exe` of §6c |
| exception | `0xC0000005` access violation, **read** at `0xA6000CB3` |
| faulting | `DisciplesIII12.exe+0x208D44`, thread `0x1ee0` |
| registers | `eax` = the bad address, `ecx` = 0, `esi` = `0x14A70020`, `ebp` = 0 |
| modules | 89, and **no `okidifox.dll`, no StarForce** — this build carries no protector, so the dump answers loader questions and nothing about UPX |

The faulting code, from the 256 bytes of code the dump happens to carry:

```
0x608d1c  mov  ecx,[esp+0x5c]      ; an object the caller passed
0x608d20  mov  eax,[ecx+4]        ; a count
0x608d29  imul eax,eax,0x33       ; * 51
0x608d2c  lea  eax,[eax+esi+0x30] ; + base + 0x30
0x608d44  movzx ebx,byte ptr [eax+ecx]   ; ecx = 0 on the first pass
```

`[ecx+4]` holds `0x3F1A…` — a float bit pattern, not a count — so the scaled
offset wraps and the first byte read lands at `0xA6000CB3`.  The object was
never the structure this code expected, because the resource it was built from
had already been rejected.  Two log lines were live in memory at the time:

```
[EE] resources\characters\empire\inquisitor\character_empire_inquisitor.scene
   : resources\characters\empire\inquisitor\character_empire_inquisitor.g
   : Index error : str_not_valid_hash_for_file
   : resources\characters\empire\inquisitor\character_empire_inquisitor.g
...a_back_land_empire_02.g : 0 triangles in object: 'trees_02_2' with material: 1
```

and the mod list on the stack held both `mod:D3_test_faction6_mod` and
`mod:ruru`.

That message is not a mystery in the executable either.  `Index error : %s : %s`
is at 0x87835c; the identifier `str_not_valid_hash_for_file` at 0x88555c, pushed
at **0x49270d**; and the function it is the failure branch of compares the
48-byte digest of the payload against the manifest's (`0x4129e0` compares
sixteen bytes at a time and returns non-zero when they *differ*):

```
0x4926f8  call 0x4129e0     ; digests differ?
0x4926fd  test al, al
0x4926ff  jne  0x49270d     ; yes -> Index error, and no data returned
```

So the chain is: a pack whose `index` describes other bytes → the loader
computes MD5 + SHA-256, compares, logs `str_not_valid_hash_for_file`, and
**returns nothing for that file** → whatever asked for the asset builds its
state from the hole → the access violation.  A unit that "does not appear" and
a game that "just crashes on this unit" are the same event seen from two
places, and `vdpack pack --like <pack> --reindex` is the fix for both.

`tools/dmp_find.py` reads this back out of any dump the user produces
(`info`, `modules`, `exception`, `stack`, `strings`, `mem`), so the next
question — which function, on which asset — starts from evidence rather than
from a guess.

## 6. The `.vdpack` mod archive

A retail install keeps a unit as a loose folder; mods ship the whole tree as
**one file**, a `.vdpack`, which the game mounts by name.  Implemented in
`d3tool/vdpack.py` (`vdpack list|extract|pack|diff|manifest|validate`), layout
documented from the Heroes-VII → Disciples III porting corpus (see
`docs/PORT_ANALYSIS_COMPARE_RU.md`) — the bundled corpus ships no `.vdpack`,
so the reader is validated against synthetic archives and the port's own
writer, not against a retail pack.

```text
u32                       entry count
repeat count:
    u32                   name length, INCLUDING the NUL
    [name + NUL]          backslash separated, relative to the pack root
    u32 size              payload length
    u32 offset            absolute offset from byte 0
    u32 flags             engine flags; 0 in every observed pack
[payloads]                back to back, in index order, NO padding
```

Details that matter when writing one back:

* names are stored with `\` as the separator (that is what the port wrote and
  what the game resolves), so `release`/`pack` normalises `/` to `\`;
* nothing is aligned — the first payload starts exactly at the end of the
  index, and `size`/`offset` are the only way to find a file;
* payloads are the files byte-for-byte, so `pack` → `extract` reproduces the
  folder exactly (verified on `Neutrals/AirElemental`, 23 files);
* `flags` is preserved on read and defaults to 0 on write.

Validation on read (all reported as a parse error, never silently ignored): a
truncated index, an implausible entry count, an unterminated name, a payload
past the end of the file, and two entries sharing bytes.  On extraction the
stored name is re-validated, so an archive cannot write outside the target
folder (`..`, absolute paths and drive letters are refused).

**The `index` entry decides whether a pack is mounted**, and since §6f we know
what it is: the mod's manifest, one MD5 + SHA-256 record per resource.  The
three observations the port made at runtime map onto the loader's own three
messages, which is why they looked contradictory:

| the pack | the manifest says | loader | what was seen |
|---|---|---|---|
| payloads replaced, manifest untouched | hashes stale | nothing — a digest is not compared against the file list | mounted, rebuilt geometry on screen |
| six paths added, manifest untouched | path has no record | `Unindexed file '%s'` (0x41a583) | no units in game |
| a path dropped, manifest untouched | record's file is gone | `Not found indexed file '%s'` (0x41a5c7) | one attempt never appeared in the mods list; another still mounted — those two attempts differed in more than the dropped path, so read this row as the mechanism, not as a verdict |

So the rule was never "replace only" — it was "the entry set and the manifest
have to agree", and only one of the three ways to disagree was survivable.
`d3tool` can now write the manifest, so all three are fixable instead of
avoidable:

* `vdpack pack <folder> --like <pack> --reindex` — same paths and order, a
  manifest written for the payloads that actually landed (the mod keeps its
  `Name`/`UUID`/`Type`);
* `vdpack pack <folder> --reindex` — a fresh archive from a folder, manifest
  included: the way to ship a *new* unit, which was previously untestable;
* `vdpack index <pack>` — decode and check, so "does this pack agree with
  itself" is a command rather than a guess;
* `--keep-index <existing.vdpack>` copies a proven manifest over (correct only
  when the payloads are the ones it describes).

`--like` also rebuilds the archive in the proven pack's own entry order and
spelling, which is worth keeping for one reason: an unchanged bundle that the
game ignores anyway has to differ from the working one in payload bytes alone.
Any folder path the source pack does not carry is dropped with a warning;
`--drop <path>` leaves an entry out entirely.

Confirmed later on one real unit pack (the Empire Angel, 24 entries, the
original 4100-byte `index`, both `.g` payloads replaced in place): loading is
fine, the rebuilt geometry is what the battle shows, and **removing** a path —
a generated `*_lod.ac` — did not break the mount, unlike adding one.  That pack
also carried neither the unit's `.dds` diffuses nor its six icons, and both
still resolved from the vanilla tree, so a unit pack does not have to mirror
its folder.  What the engine will not fall back for is a reference its own
`.ac`/`.scene` makes into a folder the pack lacks: the load stops with a
missing-file dialog naming `<pack>.vdpack:resources\characters\...` — hence
the wrong-folder warning `vdpack validate` prints.

Repathing that `*_lod.ac` in game changed nothing: the dialog came back with
the same prefix, so the reference was never the thing the engine was opening.
What did keep coming back was the pack *containing a payload that differs from
its source*, and that is where the loop stopped being productive.  It is worth
recording what the container itself cannot be blamed for: a folder packed back
`--like` the working archive with every payload left alone reproduces that
archive **byte for byte** (md5 equal), so the writer, the header, the name
spelling, the flags and the `index` manifest are all carried exactly; only a
changed payload can make the engine complain.

The sword that never appeared was a payload problem of a different kind, and
the `.g` attribute block says so.  The pack ships the Empire Angel with a fifth
part appended (`empire_knight_sword`, lifted from
`Empire/Knight/character_empire_knight.g`: 124 triangles, 136 vertices, bound
1.0 to `Bip01 R Hand`), and the `.scene` already carries its `gobj` — so the
mesh, the node and the texture are all there, and the battle still shows no
sword.  The difference is one attribute: the appended block does not declare
`new_vertex_weights_format`.  Every skinned part in the corpus declares it
(`test_every_corpus_weapon_part_declares_its_weight_format`), and the engine
takes the vertex-weight stride from that declaration, not from
`weights_on_vertex` — so it reads the sword's 136 vertices five bytes short
apiece, the index block goes out of alignment and the part draws as nothing,
while every reader that derives the stride from the counts (this package
included) sees a perfectly good mesh.  The block is length-prefixed, so the
attribute can simply be inserted:

```bash
python3 -m d3tool gattr Empire/Angel/character_empire_angel.g \
    --part 4 --insert new_vertex_weights_format=1     # +36 bytes, nothing else
python3 -m d3tool lod Empire/Angel                     # the LOD mesh + scene too
python3 -m d3tool vdpack pack Empire/Angel -o angel.vdpack --like ruru.vdpack
```

`gattr` reads the block, rewrites the count and appends `(u32 len, "key\0")`
`(u32 len, "value\0")`; `--set` replaces a value in place (same length keeps
the payload's size, a different length shifts the rest of the file).  A part
appended by other tooling usually copied its neighbours' *data* and not their
*format* declaration — that is the shape to look for before blaming the
archive.

The exporter's own output was audited block by block on a re-export of that
unit, and the two models now agree.  Main mesh: 347 266 B, five parts, no count
disagreement; the sword block `empire_knight_sword` holds 136 vertices / 124
triangles, declares `new_vertex_weights_format` and `weights_on_vertex 1`,
carries the single bone `Bip01 R Hand` and the diffuse
`weapon_empire_knight_sword_diffuse.tga`.  LOD mesh: retail 161 869 B → 173 210
B, mesh count 4 → 5, the appended block spelled `lod_empire_knight_sword` and
parented to the LOD file's own `55867360`; its vertices, indices, normals, UVs,
weights and bone matrix are byte-identical to the main mesh's block, so both
draw the same sword, and a second `d3tool lod` run is a no-op.  The LOD scene
gained exactly 39 lines — one `gobj` on mesh slot 4 — with every other line
untouched, and the near `.ac` stayed byte-identical to retail.  The one item
`validate` still reports is the generated `*_lod.ac`, which retail never ships
and which the runtime experiments above say the engine never opens — it is left
alone rather than repathed.

`vdpack diff <pack-that-loads> <new-pack|folder>` answers the question a
modder actually ships on: *what did I change, and is it still the shape that
loads?*  It lists the paths added, the paths removed, the paths whose payload
differs, and whether the shared paths kept their order, then grades the result
against the single runtime-confirmed shape (same paths, different payloads):
`ok` when nothing but payloads moved, `warn` for a removal or a re-order, and
**added for any new path** — the case the port tried in game and saw no units
at all.  `--strict` exits 1 on that grade, which is the check worth putting in
front of the "pack and test" step; `--json` emits the same comparison for a
script.  A pack and the folder it was built from compare clean, separators
aside, so the tool doubles as "is this folder what the pack carries".

`vdpack plan <folder> [--like <pack-that-loads>]` turns that grade into an
instruction, because a folder on disk is shallow (`Empire/Inquisitor`) while the
path it lands on is not (`resources\\Characters\\Empire\\Inquisitor`), and a
modder cannot see which of the two shapes a file will take.  The plan lines the
folder up with an archive path — from the pack itself when one is given (the
entry carrying one of the folder's own files names the folder), otherwise from
the unit's own `.scene`, which spells the `resources\\…\\<base>.g` it binds — and
then, per file, says `replaces` or `new path`.  A `new path` that is a `.t` is
the interesting case: the pack's own textures in that folder are printed as
rename targets, because a `.g` attribute holds a bare texture name resolved
inside the unit's folder, so pointing the part at a name the `index` already
lists costs nothing and keeps the confirmed shape.  Files the pack carries and
the folder does not are listed as `stays vanilla` — dropping them from a bundle
is how the retail tree keeps resolving them.  The last line is the
`game\\resources\\…\\<file>` layout the engine's own mount reaches, which is the
only way to ship a path no pack can carry.  With no `--like` the plan prints
that layout alone and marks every file `loose`; `--strict` exits 1 on a
`new path`, matching `vdpack diff`.

`vdpack manifest <archive|folder>` writes the sha256 inventory of a bundle —
one `{path, bytes, sha256}` per file, in archive naming and case-insensitive
order, plus the reference report.  The document is identical for a folder and
for the archive packed from it, so packing can be proven rather than trusted
(the port kept a `manifest.json` per pack for exactly this) and two builds can
be diffed without unpacking them.

`vdpack validate <archive|folder>` re-checks what the exporter's
battle-readiness pass checks on a folder, against the payloads themselves: a
`.ac` state naming an `.a`/meshfile the pack does not contain, a `.g` material
whose `.t` is missing, duplicate part names.  Two exemptions, both learned
from retail data:

* `material0_lightmap` almost always names `cubemap_default.tga`, a texture
  the engine keeps globally (20 of 26 corpus parts) — a unit folder never
  ships it;
* a reference that points **outside** the packed tree (Blacknaga's `.ac`
  still names `resources\characters\neutrals\mermaid\…baseanims.a`, the
  template it was cloned from) is reported as a note, not a failure: the game
  mounts those folders separately.

A unit bundle is not only geometry and animation.  The port's archive carries,
per seven model kits: `.g`, `.a`, `.ac`, `.scene`, `.t`, and **`.char`** —
21 of those, described there as unit *profiles and stats*; the string inside a
`.char` payload is the profile ID the character browser lists, and patching it
alone produced a working second entry.  That reading is now confirmed against
the retail tree (see §4c): a profile is found by its `id`, three shipped files
have an `id` that differs from their file name, so the name is irrelevant.
`d3tool/char.py` parses the format, `vdpack validate` checks a packed
profile's includes and `characters\…` references like any other unit-local
reference, and `d3tool char set-id` performs exactly that one-string patch.
The port's archive also holds one entry named `index` (20 362 bytes) that
no reader decodes — here it is an ordinary file, so `extract` → `pack`
preserves it verbatim, but a pack assembled from loose folder content simply
has none.  §6d identifies it: a mod descriptor plus a **per-file hash
manifest** (MD5 and SHA-256) that the loader validates before mounting, which
makes an inherited `index` over changed payloads a defect rather than a
formality.  `vdpack diff` and `vdpack plan` say so explicitly now — a payload
count with the original `index` still in place reads as a warning naming the
loader's own messages (`has wrong index`, `not found indexed file`,
`unindexed file`), and `plan` prints which payloads differ from the ones the
pack's index describes.
