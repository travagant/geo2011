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

A declared `w = 1` on a mesh that references **several** bones actually stores
the full two-influence record (46 bytes) — the attribute keeps `1`, readers
derive the width from `w` + `bones_num` (independently confirmed on the
Heroes VII port corpus, see `docs/PORT_ANALYSIS_COMPARE_RU.md`).

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
  (original → glTF), `validate`, `import`, `texture`, `vdpack`, `char`.

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

## 6. The `.vdpack` mod archive

A retail install keeps a unit as a loose folder; mods ship the whole tree as
**one file**, a `.vdpack`, which the game mounts by name.  Implemented in
`d3tool/vdpack.py` (`vdpack list|extract|pack`), layout documented from the
Heroes-VII → Disciples III porting corpus (see
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

**The opaque `index` entry decides whether a pack is mounted.**  Every pack
the port examined carries one resource named exactly `index` (20 362 bytes in
`NewUnit.vdpack`) that nobody has decoded.  What the port established at
runtime: replacing a payload *inside* such a pack works; adding six entries to
it (index untouched) did **not** show the new units in game.  So the writer
here does not claim otherwise — `vdpack pack` prints a warning when the bundle
has no `index`, and `--keep-index <existing.vdpack>` copies the resource from
a proven pack into the new one (same paths, new payloads: the shape that is
confirmed to load).  `vdpack manifest` records `index: {bytes, sha256}` (or
`null`), so a manifest says which of the two you are looking at.

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
The port's archive also holds one opaque entry named
`index` (20 362 bytes) that no tool has decoded — here it is an ordinary file,
so `extract` → `pack` preserves it verbatim, but a pack assembled from loose
folder content simply has none (unverified in game).  Keep that in mind before
promising a runtime-clean mod archive.
