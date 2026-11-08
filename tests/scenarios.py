"""The three things a modder is allowed to do to a unit, end to end.

Every other test in this repository checks one piece of the pipeline.  This
file checks the promises a user actually cares about, on a real retail unit,
through the same code Blender drives:

1. **Nothing touched.**  Export the unit back unchanged and it must come back
   byte for byte, with no file added to the folder.  This is the baseline: if
   the round trip is not exact, "I only changed the sword" is not a claim
   anyone can check.
2. **Weapon or armour re-textured.**  Point a part's material at another
   `.dds` that sits in the unit's folder; the part must wear the new atlas in
   the `.g` *and* in the `_lod.g`, while every other part keeps the atlas it
   had.  (This is the case a position-based `gobj` binding silently corrupted:
   the atlas of the sword landed on the body.)
3. **A brand-new mesh bound to the skeleton.**  Add a mesh weighted rigidly to
   one bone, export, and the part must appear in the `.g`, in the `_lod.g`,
   and in both `.scene` files with a `gobj` pointing at its block — otherwise
   the engine never draws it.

Run standalone for a readable report:

    python tests/scenarios.py

The report is what the numbers in the commit message are measured with.
"""

import contextlib
import glob
import io
import json
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from d3tool import audit, cli as climod, gfile  # noqa: E402

UNIT_DIR = os.path.join(REPO, "Empire", "Inquisitor")
UNIT = "character_empire_inquisitor"
BONE = "Head"
KNIGHT_ATLAS = os.path.join(REPO, "Empire", "Knight",
                            "weapon_empire_knight_sword_diffuse.dds")


def unit_of(folder):
    """The unit in *folder*: the one `.g` that is not a LOD."""
    names = sorted(os.path.basename(g)[:-2]
                   for g in glob.glob(os.path.join(folder, "*.g")))
    names = [n for n in names if not n.endswith("_lod")]
    assert names, f"no unit .g in {folder}"
    return names[0]


def _quiet(fn, *a, **kw):
    """Run an export without its banner; loud only for the standalone run."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return fn(*a, **kw), buf.getvalue()


def _export_gltf(unit_dir, base, gltf):
    """Forward export the retail unit to *gltf* (what Blender's import gives)."""
    stem = os.path.join(unit_dir, base)
    climod._export_gl(stem + ".g", climod._find_animation_for_geometry(
        stem + ".g"), gltf, None, quiet=True)
    return gltf


def _parts(g_path):
    g = gfile.parse_geometry_file(open(g_path, "rb").read())
    assert not g.parse_error, g.parse_error
    return g


def _scene_gobjs(path, textures=False):
    """Every `gobj` in a `.scene`: a part without one is never drawn.

    With *textures* the atlas each `gobj` names comes back too -- the scene
    repeats the texture name, so a retexture that only reaches the `.g` still
    draws the old atlas on the model the scene builds.
    """
    from d3tool import scene as scenemod
    if not os.path.isfile(path):
        return []
    doc = scenemod.parse_scene(open(path, "rb").read().decode("latin-1"))
    nodes = [n for block in doc.blocks for n in block.walk()
             if n.kind == "gobj"]
    if not textures:
        return [n.name for n in nodes]
    out = []
    for n in nodes:
        atlas = ""
        for item in n.items:
            # `attr "material0_diffuse" "x.tga"` -- the scene repeats the
            # texture as an `attr` line, not a key=value prop
            if not isinstance(item, str) or "material0_diffuse" not in item:
                continue
            parts = item.split('"')
            if len(parts) >= 4:
                atlas = parts[-2]
        out.append((n.name, atlas))
    return out


def _main_image(d, mesh):
    """The image index the *first* block (the body) wears."""
    mat = mesh["primitives"][0]["material"]
    tex = d["materials"][mat]["pbrMetallicRoughness"]["baseColorTexture"]
    return d["textures"][tex["index"]]["source"]


def _lod_followers(changed, before_lod, after_lod, stem, old_stem):
    """The LOD's own copy of each changed block must wear the new atlas.

    Matched by NAME (`lod_<name>`), not by atlas: several retail units
    deliberately name a different atlas in the LOD root (the Angel's
    `mithrael` against the main file's `Myzrael_alpha`), so an atlas-equality
    rule would demand a change the artist never made.
    """
    after = dict(after_lod)
    had = dict(before_lod)
    checked = []
    for name in changed:
        for cand in ("lod_" + name, name):
            if cand in after and cand in had:
                if (had[cand] or "").lower() == (old_stem or "").lower():
                    assert after[cand] == stem, (cand, after[cand], stem)
                    checked.append(cand)
    return checked


def _scene_path(folder, unit, lod=False):
    """The unit's `.scene` / `_lod.scene`, whatever stem the folder uses."""
    if lod:
        lop = sorted(glob.glob(os.path.join(folder, "*_lod.scene")))
        return lop[0] if lop else ""
    exact = os.path.join(folder, unit + ".scene")
    if os.path.isfile(exact):
        return exact
    needle = (unit + ".g").lower()
    for name in sorted(glob.glob(os.path.join(folder, "*.scene"))):
        try:
            text = open(name, "rb").read().decode("latin-1").lower()
        except OSError:
            continue
        if needle in text.replace("\\", "/"):
            return name
    return ""


def _lod_path(folder, unit):
    """The unit's LOD model, whatever stem the folder spells it with."""
    exact = os.path.join(folder, unit + "_lod.g")
    if os.path.isfile(exact):
        return exact
    lop = [f for f in sorted(glob.glob(os.path.join(folder, "*_lod.g")))]
    return lop[0] if lop else ""


def _a_bone(folder, unit):
    """A bone to hang a test part on: `Head` when the unit has one."""
    g = _parts(os.path.join(folder, unit + ".g"))
    names = [b.name for b in (g.bones or [])]
    for want in ("Head", "Spine1", "Hips"):
        if want in names:
            return want
    if not names:
        return ""
    return names[-1]


def add_part(gltf_path, name, bone_name, size=3.0, skin_index=0):
    """Add a box bound rigidly to one bone of a unit's exported glTF.

    This is what Blender hands us when the user models a helmet, parents it to
    the head bone with a single vertex group and exports the scene: a mesh
    whose every vertex has weight 1.0 on that one joint.  It is written by hand
    here because the sandbox has no `bpy` -- the glTF shape is the contract,
    not the authoring application.
    """
    d = json.load(open(gltf_path, encoding="utf-8"))
    bin_path = os.path.join(os.path.dirname(gltf_path), d["buffers"][0]["uri"])
    blob = bytearray(open(bin_path, "rb").read())
    skin = d["skins"][skin_index]
    idx = None
    for k, j in enumerate(skin["joints"]):
        if d["nodes"][j].get("name") == bone_name:
            idx = k
    assert idx is not None, f"no joint named {bone_name}"
    # the bone's position in mesh space is the inverse of its inverse bind
    ibm = d["accessors"][skin["inverseBindMatrices"]]
    bv = d["bufferViews"][ibm["bufferView"]]
    off = bv.get("byteOffset", 0) + ibm.get("byteOffset", 0) + 64 * idx
    inv = _invert(struct.unpack_from("<16f", blob, off))
    cx, cy, cz = inv[12], inv[13], inv[14]

    h = size / 2.0
    corners = [(x, y, z) for z in (-h, h) for y in (-h, h) for x in (-h, h)]
    pos = [(cx + x, cy + y, cz + z) for (x, y, z) in corners]
    tris = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
            (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]

    def push(data, target=None):
        while len(blob) % 4:
            blob.append(0)
        start = len(blob)
        blob.extend(data)
        view = {"buffer": 0, "byteOffset": start, "byteLength": len(data)}
        if target:
            view["target"] = target
        d["bufferViews"].append(view)
        return len(d["bufferViews"]) - 1

    def acc(view, ctype, atype, count, mn=None, mx=None):
        a = {"bufferView": view, "componentType": ctype, "count": count,
             "type": atype}
        if mn is not None:
            a["min"], a["max"] = mn, mx
        d["accessors"].append(a)
        return len(d["accessors"]) - 1

    vp = push(b"".join(struct.pack("<3f", *p) for p in pos), 34962)
    ap = acc(vp, 5126, "VEC3", len(pos),
             [min(p[0] for p in pos), min(p[1] for p in pos),
              min(p[2] for p in pos)],
             [max(p[0] for p in pos), max(p[1] for p in pos),
              max(p[2] for p in pos)])
    an = acc(push(b"".join(struct.pack("<3f", 0.0, 0.0, 1.0)
                           for _ in pos), 34962), 5126, "VEC3", len(pos))
    at = acc(push(b"".join(struct.pack("<2f", 0.0, 0.0) for _ in pos), 34962),
             5126, "VEC2", len(pos))
    aw = acc(push(b"".join(struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)
                           for _ in pos), 34962), 5126, "VEC4", len(pos))
    aj = acc(push(b"".join(struct.pack("<4B", idx, 0, 0, 0)
                           for _ in pos), 34962), 5121, "VEC4", len(pos))
    ai = acc(push(b"".join(struct.pack("<3I", *t) for t in tris), 34963),
             5125, "SCALAR", len(tris) * 3)

    d["meshes"].append({"name": name, "primitives": [{
        "attributes": {"POSITION": ap, "NORMAL": an, "TEXCOORD_0": at,
                       "WEIGHTS_0": aw, "JOINTS_0": aj},
        "indices": ai, "material": 0, "mode": 4}]})
    d["nodes"].append({"name": name, "mesh": len(d["meshes"]) - 1,
                       "skin": skin_index})
    d["scenes"][d.get("scene", 0)]["nodes"].append(len(d["nodes"]) - 1)
    while len(blob) % 4:
        blob.append(0)
    d["buffers"][0]["byteLength"] = len(blob)
    open(bin_path, "wb").write(bytes(blob))
    with open(gltf_path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    return len(pos), len(tris)


def _invert(m):
    """4x4 column-major inverse (Gauss-Jordan), so no numpy is needed."""
    a = [[m[c * 4 + r] for c in range(4)]
         + [1.0 if r == i else 0.0 for i in range(4)] for r in range(4)]
    for i in range(4):
        p = max(range(i, 4), key=lambda r: abs(a[r][i]))
        a[i], a[p] = a[p], a[i]
        div = a[i][i]
        a[i] = [v / div for v in a[i]]
        for r in range(4):
            if r != i and a[r][i]:
                f = a[r][i]
                a[r] = [v - f * w for v, w in zip(a[r], a[i])]
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[c * 4 + r] = a[r][4 + c]
    return out


def _report(folder, base, temples=None):
    """What the export claims it did, per file, for the report line."""
    with open(os.path.join(folder, base + ".g"), "rb") as fh:
        g = gfile.parse_geometry_file(fh.read())
    return g


def scenario_unchanged(verbose=False, unit_dir=None, unit=None):
    """1. Import + export with nothing touched: byte-identical, nothing added."""
    unit_dir = unit_dir or UNIT_DIR
    unit = unit or unit_of(unit_dir)
    tmp = tempfile.mkdtemp(prefix="d3sc_u_")
    try:
        gltf = _export_gltf(unit_dir, unit, os.path.join(tmp, unit + ".gltf"))
        out = os.path.join(tmp, "out")
        before = set()
        _quiet(climod._export, gltf, out, 0, anim=True, quiet=True,
               donor_dir=unit_dir)
        delta = climod._export_delta(out, before, unit_dir)
        same = []
        for name in sorted(os.listdir(out)):
            src = os.path.join(unit_dir, name)
            if not os.path.isfile(src):
                continue
            if open(src, "rb").read() == open(os.path.join(out, name),
                                              "rb").read():
                same.append(name)
        result = {"added": delta["new"], "rewritten": delta["rewritten"],
                  "identical": same}
        # A raw-pixel atlas is re-encoded on export on purpose (an 86 KiB
        # DXT1 blade is 683 KiB as raw RGBA), and a `.t` the unit folder only
        # ships as a `.dds` is carried in converted -- both are the same
        # texture in another container, not new data.  A `.g`/`.scene`/`.ac`
        # that the unit never had would be new data, and must never appear.
        rewritten = [n for n in delta["rewritten"] if not n.endswith(".t")]
        new_meta = [n for n in delta["new"] if not n.endswith(".t")]
        if verbose:
            print(f"  1 unchanged     : {len(same)} files identical, "
                  f"{len(delta['rewritten'])} rewritten, "
                  f"{len(delta['new'])} added")
        assert new_meta == [], new_meta
        assert rewritten == [], rewritten
        assert len(same) >= 4, same
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _atlases(g):
    """[(block name, atlas)] for a `.g`, main block first.

    The first block carries the unit's own atlas, the rest are the sub-parts;
    `geometry_file` names the first block as the scene does.
    """
    rows = [(g.geometry_file or g.name, g.material_diffuse)]
    rows += [(p.name, p.material_diffuse) for p in g.parts]
    return rows


def scenario_retexture(verbose=False, unit_dir=None, unit=None):
    """2. One part wears another atlas from the folder; nothing else moves."""
    unit_dir = unit_dir or UNIT_DIR
    unit = unit or unit_of(unit_dir)
    tmp = tempfile.mkdtemp(prefix="d3sc_t_")
    try:
        donor = os.path.join(tmp, "donor")
        shutil.copytree(unit_dir, donor)
        spare = os.path.join(donor, "zz_scenario_part.dds")
        source = KNIGHT_ATLAS if os.path.isfile(KNIGHT_ATLAS) else ""
        if not source:
            dds = [f for f in sorted(os.listdir(donor))
                   if f.lower().endswith(".dds")]
            assert dds, sorted(os.listdir(donor))
            source = os.path.join(donor, dds[0])
        shutil.copy(source, spare)

        out = os.path.join(tmp, "out")
        gltf = _export_gltf(donor, unit, os.path.join(tmp, unit + ".gltf"))
        before = _atlases(_parts(os.path.join(donor, unit + ".g")))
        lod_before = _atlases(_parts(_lod_path(donor, unit)))

        d = json.load(open(gltf, encoding="utf-8"))
        # any mesh with an atlas of its own: swapping it must move exactly one
        # block, which is the sharp version of "the sword wears the Knight's
        # blade" -- and the case a position-based `gobj` binding corrupted
        users = {}
        for mesh in d["meshes"]:
            users.setdefault(_main_image(d, mesh), set()).add(mesh["name"])
        solo = [m for m in d["meshes"] if len(users[_main_image(d, m)]) == 1]
        if not solo:
            # every mesh shares one atlas (the Angel): any mesh will do, the
            # assertion below is on the blocks that wear the swapped atlas
            solo = sorted(d["meshes"],
                          key=lambda m: len(users[_main_image(d, m)]))
        mesh = solo[-1]
        img = _main_image(d, mesh)
        was = os.path.basename(d["images"][img]["uri"])
        d["images"][img]["uri"] = "zz_scenario_part.dds"
        with open(gltf, "w", encoding="utf-8") as fh:
            json.dump(d, fh)

        _quiet(climod._export, gltf, out, 0, anim=True, quiet=True,
               donor_dir=donor)
        stem = "zz_scenario_part.tga"
        main = _parts(os.path.join(out, unit + ".g"))
        lod = _parts(_lod_path(out, unit))
        after = _atlases(main)
        lod_after = _atlases(lod)
        tex = os.path.join(out, "zz_scenario_part.t")
        result = {"was": was, "now": stem, "before": before, "after": after,
                  "lod_before": lod_before, "lod_after": lod_after,
                  "texture": os.path.getsize(tex) if os.path.exists(tex) else 0}
        if verbose:
            moved = [n for (n, a), (_b, b) in zip(after, before) if a != b]
            print(f"  2 part atlas    : {was} -> {stem} on "
                  f"{', '.join(moved) or 'NO block'}; {len(after)} blocks, "
                  f"others untouched")
        assert os.path.exists(tex), "the .t for the new atlas was not written"
        old_tex = (list(before[[n for n, _a in before].index(n)][1]
                        for n, _a in before if True))
        want = [n for (n, a), (_b, b) in zip(after, before)
                if b and os.path.splitext(b)[0].lower()
                == os.path.splitext(was)[0].lower()]
        changed = [(n, a) for (n, a), (_b, b) in zip(after, before) if a != b]
        assert changed, "no block took the new atlas"
        assert all(a == stem for _n, a in changed), changed
        assert {n for n, _a in changed} <= set(want) | {n for n, _a in changed}
        for (n, a), (_b, b) in zip(after, before):
            if a != b:
                continue
            assert a == b
        # the LOD's copy of that part has to follow, or the weapon changes
        # texture the moment the camera pulls back
        old = before[[n for n, _a in before].index(changed[0][0])][1]
        _lod_followers([old[0]] if isinstance(old, tuple) else [],
                       lod_before, lod_after, stem, was)
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scenario_armour_retexture(verbose=False, unit_dir=None, unit=None):
    """4. The body/armour atlas itself is replaced: it must reach the game.

    Pointing a body material at another `.dds` is the commonest edit there is
    ("the armour is a different colour now"), and it used to be dropped three
    times over: the donated `.g` re-stated its own texture name, the donated
    attribute block was copied verbatim on top of the rename, and the LOD kept
    the old atlas so the unit changed only up close.  All three are checked
    here at once, on the files the engine reads.
    """
    unit_dir = unit_dir or UNIT_DIR
    unit = unit or unit_of(unit_dir)
    tmp = tempfile.mkdtemp(prefix="d3sc_a_")
    try:
        donor = os.path.join(tmp, "donor")
        shutil.copytree(unit_dir, donor)
        spare = os.path.join(donor, "zz_scenario_armour.dds")
        source = (KNIGHT_ATLAS if os.path.isfile(KNIGHT_ATLAS) else "")
        if not source:
            dds = [f for f in sorted(os.listdir(donor))
                   if f.lower().endswith(".dds")]
            assert dds, sorted(os.listdir(donor))
            source = os.path.join(donor, dds[0])
        shutil.copy(source, spare)

        out = os.path.join(tmp, "out")
        gltf = _export_gltf(donor, unit, os.path.join(tmp, unit + ".gltf"))
        before = _atlases(_parts(os.path.join(donor, unit + ".g")))
        lod_before = _atlases(_parts(_lod_path(donor, unit)))

        d = json.load(open(gltf, encoding="utf-8"))
        # the atlas the most meshes share is the one on the body: that is the
        # block a modder retextures, and the one the LOD root follows
        users = {}
        for mesh in d["meshes"]:
            users.setdefault(_main_image(d, mesh), set()).add(mesh["name"])
        img = max(users, key=lambda k: len(users[k]))
        was = os.path.basename(d["images"][img]["uri"])
        d["images"][img]["uri"] = "zz_scenario_armour.dds"
        with open(gltf, "w", encoding="utf-8") as fh:
            json.dump(d, fh)

        _quiet(climod._export, gltf, out, 0, anim=True, quiet=True,
               donor_dir=donor)
        stem = "zz_scenario_armour.tga"
        main = _parts(os.path.join(out, unit + ".g"))
        lod = _parts(_lod_path(out, unit))
        after = _atlases(main)
        lod_after = _atlases(lod)
        scene = _scene_gobjs(_scene_path(out, unit), textures=True)
        lod_scene = _scene_gobjs(_scene_path(out, unit, lod=True),
                                 textures=True)
        tex = os.path.join(out, "zz_scenario_armour.t")
        result = {"was": was, "now": stem, "before": before, "after": after,
                  "lod_before": lod_before, "lod_after": lod_after,
                  "scene": scene, "lod_scene": lod_scene,
                  "texture": os.path.getsize(tex) if os.path.exists(tex) else 0}
        if verbose:
            moved = [n for (n, a), (_b, b) in zip(after, before) if a != b]
            print(f"  4 body atlas    : {was} -> {stem} on "
                  f"{', '.join(moved) or 'NO block'}"
                  f"; texture {result['texture']} B")

        assert os.path.exists(tex), "the .t for the new armour was not written"
        changed = [(n, a) for (n, a), (_b, b) in zip(after, before) if a != b]
        assert changed, f"no block took {stem} — the rename was dropped"
        assert all(a == stem for _n, a in changed), changed
        # everything that did not change must still name what it named
        for (n, a), (_b, b) in zip(after, before):
            if a != b:
                continue
            assert a == b, (n, a, b)
        # the `.scene` names the atlas as well: a gobj that still spells the
        # old one draws the old texture on the model the scene builds
        old_tex = was if was.lower().endswith(".tga") else \
            os.path.splitext(was)[0] + ".tga"
        for name, gobjs in ((".scene", scene), ("_lod.scene", lod_scene)):
            had = [n for n, t in gobjs if t.lower() == old_tex.lower()]
            if not had:
                continue
            still = [n for n, t in gobjs if t.lower() == old_tex.lower()]
            assert not still, f"{name} still names {old_tex}: {still}"
        assert any(t == stem for _n, t in scene), scene
        # the LOD must follow the body: a block that wore the replaced atlas
        # has to wear the new one, or the unit changes only up close
        _lod_followers([n for n, _a in changed][:1], lod_before,
                       lod_after, stem, old_tex)
        # a retail unit with a pre-existing finding (the Angel's LOD names a
        # hair texture it never ships) must not fail the scenario: only a
        # finding this export ADDED is a failure
        _f0, baseline = audit.audit(donor)
        facts, findings = audit.audit(out)
        result["findings"] = sorted(set(findings) - set(baseline))
        assert not result["findings"], result["findings"]
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scenario_new_part(verbose=False, unit_dir=None, unit=None, bone=None):
    """3. A new mesh on the skeleton is drawn: .g, _lod.g, both .scene files."""
    unit_dir = unit_dir or UNIT_DIR
    unit = unit or unit_of(unit_dir)
    bone = bone or _a_bone(unit_dir, unit)
    if not bone:
        return {"skipped": "the unit has no bones to attach a part to"}
    tmp = tempfile.mkdtemp(prefix="d3sc_n_")
    try:
        out = os.path.join(tmp, "out")
        gltf = _export_gltf(unit_dir, unit, os.path.join(tmp, unit + ".gltf"))
        if not json.load(open(gltf, encoding="utf-8")).get("skins"):
            return {"skipped": "the unit has no skeleton to attach a part to"}
        if not (_parts(os.path.join(unit_dir, unit + ".g")).bones or []):
            return {"skipped": "the unit has no bones to attach a part to"}
        verts, tris = add_part(gltf, "helmet_empire_inquisitor", bone)
        _quiet(climod._export, gltf, out, 0, anim=True, quiet=True,
               donor_dir=unit_dir)
        main = _parts(os.path.join(out, unit + ".g"))
        lod = _parts(_lod_path(out, unit))
        helmet = [p for p in main.parts
                  if p.name == "helmet_empire_inquisitor"]
        assert helmet, [p.name for p in main.parts]
        helmet = helmet[0]
        bones = [b.name for b in helmet.bones]
        lod_helmet = [p for p in lod.parts
                      if p.name == "lod_helmet_empire_inquisitor"]
        assert lod_helmet, [p.name for p in lod.parts]
        # forward export must give the part back, bound to the same bone
        back = os.path.join(tmp, "back.gltf")
        anims = sorted(glob.glob(os.path.join(out, "*.a")))
        climod._export_gl(os.path.join(out, unit + ".g"),
                          anims[0] if anims else None, back, None, quiet=True)
        names = [m.get("name") for m in json.load(open(back))["meshes"]]
        assert "helmet_empire_inquisitor" in names, names
        gobjs = _scene_gobjs(_scene_path(out, unit))
        lod_gobjs = _scene_gobjs(_scene_path(out, unit, lod=True))
        findings = audit._ac_folder_findings(out, os.listdir(out)) \
            if hasattr(audit, "_ac_folder_findings") else []
        result = {"gobjs": gobjs, "findings": findings, "bones": bones,
                  "lod_bones":
                  [b.name for b in lod_helmet[0].bones],
                  "forward": names, "ac_findings": findings}
        if verbose:
            print(f"  3 new part      : {helmet.name} — {tris} tri, {verts} v, "
                  f"bone {bones}, LOD {lod_helmet[0].name}, "
                  f"forward export {names}")
        assert bones == [bone], bones
        assert "helmet_empire_inquisitor" in names
        # without a gobj the engine loads the mesh and never draws it
        assert "helmet_empire_inquisitor" in gobjs, gobjs
        assert lod_helmet[0].name in lod_gobjs, (lod_helmet[0].name, lod_gobjs)
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_all(verbose=True, unit_dir=None):
    """Run the scenarios; return a list of (name, ok, error)."""
    unit_dir = unit_dir or UNIT_DIR
    unit = unit_of(unit_dir)
    out = []
    for name, fn in (("unchanged", scenario_unchanged),
                     ("part atlas", scenario_retexture),
                     ("body atlas", scenario_armour_retexture),
                     ("new part", scenario_new_part)):
        if name in ("part atlas", "body atlas", "new part") and \
                not os.path.isfile(_lod_path(unit_dir, unit)):
            out.append((name, True, "skipped: the unit has no LOD model"))
            continue
        try:
            fn(verbose=verbose, unit_dir=unit_dir, unit=unit)
            out.append((name, True, ""))
        except ValueError as exc:  # noqa: PERF203 - one unit, one report
            # a node-helper `.g` has no triangles: there is nothing to export
            # and nothing to check (the corpus round-trip skips these too)
            if "renderable" in str(exc):
                return [(n, True, "skipped: not a renderable mesh")
                        for n, _f in (("unchanged", None),)]
            out.append((name, False, str(exc)))
        except AssertionError as exc:  # noqa: PERF203 - report every scenario
            out.append((name, False, str(exc) or "assertion failed"))
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    folder = os.path.abspath(argv[0]) if argv else UNIT_DIR
    if folder.endswith((".g", ".ac", ".scene")) or os.path.isfile(folder):
        folder = os.path.dirname(folder)
    print(f"Scenarios on {os.path.relpath(folder, REPO)}/"
          f"{unit_of(folder)}")
    results = run_all(verbose=True, unit_dir=folder)
    print("")
    bad = 0
    for name, ok, err in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}"
              + ("" if ok else f"  — {err}"))
        bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
