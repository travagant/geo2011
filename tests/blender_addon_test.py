#!/usr/bin/env python3
"""Headless Blender test suite for the Disciples III add-on.

Run it with any Blender (3.0+), or with the ``bpy`` module::

    blender --background --python tests/blender_addon_test.py
    python -m bpy --background --python tests/blender_addon_test.py   # if bpy is installed

Every scenario builds a real scene, drives the *operators* the user clicks
(``d3unit.import_unit`` / ``d3unit.export_unit``), and then checks the files
that came out of it with the same readers the game tooling uses.  The
last group is the important one: a unit whose armature has *more* meshes than
the original ``.g`` (a new weapon, a prop parented to a bone, a part split
off with "Separate by Material") must export without an error and the extra
geometry must reach the game file — not vanish silently.
"""
from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import traceback

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "blender"))

import io_scene_d3unit as addon                      # noqa: E402

d3 = addon.bridge.load(force=True)
gfile = addon.bridge.module("gfile")
animmod = addon.bridge.module("anim")
texmod = addon.bridge.module("texture")
frm = addon.bridge.module("frame")

ANGEL = os.path.join(REPO, "Empire", "Angel", "character_empire_angel.g")
ANGEL_LOD = os.path.join(REPO, "Empire", "Angel",
                         "character_empire_angel_lod.g")
RIGID = os.path.join(REPO, "Neutrals", "Blacknaga",
                     "character_neutrals_blacknaga.g")
MORPH = os.path.join(REPO, "Empire", "Hierophant",
                     "character_empire_hierophant.g")
TMP = tempfile.mkdtemp(prefix="d3unit_tests_")

RESULTS = []


# --------------------------------------------------------------------------- #
#  harness
# --------------------------------------------------------------------------- #
def check(name, fn):
    reset()
    try:
        fn()
    except Exception:  # noqa: BLE001
        RESULTS.append((name, False, traceback.format_exc(limit=6)))
        print(f"FAIL {name}")
    else:
        RESULTS.append((name, True, ""))
        print(f"PASS {name}")


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for block in list(bpy.data.texts):
        if block.name.startswith("character"):
            bpy.data.texts.remove(block)


def live_tri_count(mesh):
    """Triangles the engine will actually rasterise (skips degenerates)."""
    n = 0
    for t in range(0, len(mesh.indices) - 2, 3):
        i0, i1, i2 = mesh.indices[t:t + 3]
        if len({i0, i1, i2}) == 3:
            n += 1
    return n


def assert_(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "assertion failed")


def eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}: {a!r} != {b!r}")


def near(a, b, tol, msg=""):
    if abs(a - b) > tol:
        raise AssertionError(f"{msg}: {a!r} vs {b!r} (tol {tol})")


def import_unit(path=ANGEL):
    out = bpy.ops.d3unit.import_unit(filepath=path)
    assert_(out == {"FINISHED"}, f"import returned {out}")
    root = addon.unitprops.find_unit_root(bpy.context)
    assert_(root is not None, "no unit root after import")
    return root


def export_unit(path):
    out = bpy.ops.d3unit.export_unit(filepath=path)
    assert_(out == {"FINISHED"}, f"export returned {out}")
    return path


def out_dir(tag):
    d = os.path.join(TMP, tag)
    os.makedirs(d, exist_ok=True)
    return d


def read_g(path):
    return gfile.parse_geometry_file(open(path, "rb").read())


def all_blocks(mesh):
    return [mesh] + list(mesh.parts)


def part_names(mesh):
    return [p.name for p in mesh.parts]


def bone_head(mesh, bone_name):
    """Bind-pose head of a `.g` bone (the descriptor is the inverse bind)."""
    for b in mesh.bones + [b for p in mesh.parts for b in p.bones]:
        if b.name == bone_name:
            m = frm.cmaj16(list(b.matrix))
            inv = frm.inv4(m)
            return (inv[0][3], inv[1][3], inv[2][3])
    return None


def make_png(path, size=8, colour=(180, 40, 40, 255)):
    """A tiny real PNG, like a user's own texture."""
    import struct
    import zlib
    raw = b"".join(bytes([0]) + bytes(colour) * size for _ in range(size))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n"
                 + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6,
                                              0, 0, 0))
                 + chunk(b"IDAT", zlib.compress(raw))
                 + chunk(b"IEND", b""))


def add_cube(name, loc=(0.3, 0.3, 1.0), size=0.25, material=None):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    ob = bpy.context.object
    ob.name = name
    if material is not None:
        ob.data.materials.append(material)
    return ob


def _parent_with_operator(ob, arm, kind, bone=None):
    """Parent exactly like the UI does (keeps the object where it is)."""
    for other in bpy.context.scene.objects:
        other.select_set(False)
    ob.select_set(True)
    if bone is not None:
        arm.data.bones.active = bone
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type=kind, keep_transform=True)
    return ob


def skin_to(ob, arm, bone):
    vg = ob.vertex_groups.new(name=bone.name)
    vg.add(list(range(len(ob.data.vertices))), 1.0, "REPLACE")
    mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    _parent_with_operator(ob, arm, "ARMATURE")
    # parent_set(ARMATURE) may remove/replace the modifier; make sure ours is
    # the one pointing at this armature
    if not any(m.type == "ARMATURE" and m.object == arm for m in ob.modifiers):
        mod = ob.modifiers.new("Armature", "ARMATURE")
        mod.object = arm
    return ob


def parent_to_bone(ob, arm, bone):
    return _parent_with_operator(ob, arm, "BONE", bone)


def hand_bone(arm):
    for b in arm.data.bones:
        if "R Hand" in b.name:
            return b
    return arm.data.bones[0]


def rest_world_vertices(ob):
    mw = ob.matrix_world
    return [tuple(mw @ v.co) for v in ob.data.vertices]


def centroid(points):
    n = len(points) or 1
    return tuple(sum(p[i] for p in points) / n for i in range(3))


# --------------------------------------------------------------------------- #
#  import
# --------------------------------------------------------------------------- #
def test_import_structure():
    arm = import_unit()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    eq(len(meshes), 4, "mesh parts of the Angel")
    eq(arm.type, "ARMATURE", "armature type")
    for ob in meshes:
        assert_(any(m.type == "ARMATURE" and m.object is arm
                    for m in ob.modifiers), f"{ob.name} is not skinned")
        assert_(len(ob.data.polygons) > 0, f"{ob.name} has no faces")
        assert_(len(ob.data.uv_layers) >= 1, f"{ob.name} lost its UVs")
    info = addon.unitprops.get_info(arm)
    eq(info.get("base"), "character_empire_angel", "unit base")
    eq(len(info.get("anims") or []), 5, "animation streams")
    assert_(os.path.isfile(info.get("g", "")), "source .g recorded")


def test_import_animation_and_fps():
    arm = import_unit()
    eq(bpy.context.scene.render.fps, 30, "scene fps")
    action = arm.animation_data.action
    start, end = action.frame_range
    # Angel's Idle+Attack+Run+Damage+Death = 64+84+28+32+55 frames
    eq(round(end - start) + 1, 263, "concatenated frame count")
    kinds = {fc.data_path.split(".")[-1] for fc in action.fcurves}
    assert_(kinds & {"rotation_quaternion", "rotation_euler"}, "no rotation")
    assert_("location" in kinds, "no translation")
    interp = {kp.interpolation for fc in action.fcurves
              for kp in fc.keyframe_points}
    eq(interp, {"LINEAR"}, "key interpolation")


def test_import_textures_packed():
    import_unit()
    imgs = [i for i in bpy.data.images if i.source == "FILE"]
    eq(len(imgs), 2, "texture count")
    for img in imgs:
        assert_(img.packed_file is not None,
                f"{img.name} was not packed (the temp folder is gone)")


def test_import_is_idempotent_per_scene():
    import_unit()
    before = len(bpy.data.objects)
    reset()
    arm = import_unit()
    eq(len([o for o in bpy.context.scene.objects if o.type == "MESH"]), 4,
       "second import in a clean scene")
    eq(len(addon.unitprops.get_info(arm)["anims"]), 5, "metadata intact")


# --------------------------------------------------------------------------- #
#  plain round trip
# --------------------------------------------------------------------------- #
def test_roundtrip_writes_identical_animation():
    import_unit()
    out = out_dir("plain")
    export_unit(os.path.join(out, "character_empire_angel.g"))
    src = os.path.join(REPO, "Empire", "Angel")
    for name in ("character_empire_angel_idle.a",
                 "character_empire_angel_attack.a",
                 "character_empire_angel_run.a",
                 "character_empire_angel_damage.a",
                 "character_empire_angel_death.a",
                 "character_empire_angel.ac",
                 "character_empire_angel.scene",
                 "character_empire_angel.t"):
        a = open(os.path.join(src, name), "rb").read()
        b = open(os.path.join(out, name), "rb").read()
        eq(a, b, f"{name} must survive an untouched round trip")


def test_roundtrip_geometry_survives():
    import_unit()
    out = out_dir("geom")
    g_path = os.path.join(out, "character_empire_angel.g")
    export_unit(g_path)
    src = read_g(ANGEL)
    dst = read_g(g_path)
    eq(len(dst.parts), len(src.parts), "part count")
    eq(dst.weights_on_vertex, src.weights_on_vertex, "root influence slots")
    eq([b.name for b in dst.bones], [b.name for b in src.bones],
       "root bone table")
    eq(live_tri_count(dst), live_tri_count(src),
       "live triangle count (Blender drops the donor's degenerate faces)")
    for a, b in zip(src.parts, dst.parts):
        eq(a.name, b.name, "part name")
        eq(len(a.bones), len(b.bones), f"{a.name} bone table size")
        assert_(abs(a.vertex_count - b.vertex_count) <= max(
            8, a.vertex_count // 50),
            f"{a.name} vertex count drifted: {a.vertex_count} -> "
            f"{b.vertex_count}")
        eq(live_tri_count(b), live_tri_count(a),
           f"{a.name} live triangle count")
    # the engine only sees positions/weights: every vertex must land on the
    # original geometry (Blender re-splits a handful at UV seams)
    for a, b in zip(src.parts, dst.parts):
        worst = 0.0
        for v in b.vertices[::max(1, len(b.vertices) // 200)]:
            best = min((max(abs(v.position[i] - d.position[i])
                            for i in range(3)) for d in a.vertices),
                       default=0.0)
            worst = max(worst, best)
        assert_(worst < 1e-4,
                f"{a.name}: a vertex moved {worst:.2e} from the original")


def test_export_into_other_folder_uses_the_import_donors():
    arm = import_unit()
    info = addon.unitprops.get_info(arm)
    elsewhere = out_dir("elsewhere")   # nothing but the exported files here
    export_unit(os.path.join(elsewhere, "character_empire_angel.g"))
    for name in ("character_empire_angel.ac", "character_empire_angel.scene"):
        eq(open(os.path.join(elsewhere, name), "rb").read(),
           open(os.path.join(info["dir"], name), "rb").read(),
           f"{name} donated from the imported unit folder")
    assert_(os.path.isfile(os.path.join(elsewhere, "Aliases", "attack00.alias")),
            "sound aliases came along")


def test_export_without_import_metadata():
    """A .blend that only ever saw a dis3tool glTF must still export."""
    bpy.ops.import_scene.gltf(filepath=os.path.join(
        REPO, "Blender", "character_empire_angel.gltf"))
    out = out_dir("nometa")
    export_unit(os.path.join(out, "character_empire_angel.g"))
    mesh = read_g(os.path.join(out, "character_empire_angel.g"))
    assert_([p.name for p in mesh.parts], "compound parts written")
    assert_(os.path.isfile(os.path.join(out, "character_empire_angel.t")),
            "texture emitted")


# --------------------------------------------------------------------------- #
#  the extra-mesh workflows (the point of this suite)
# --------------------------------------------------------------------------- #
def test_extra_skinned_mesh_becomes_a_part():
    arm = import_unit()
    hand = hand_bone(arm)
    cube = skin_to(add_cube("SwordSkinned"), arm, hand)
    verts = len(cube.data.vertices)
    out = out_dir("extra_skinned")
    g_path = os.path.join(out, "character_empire_angel.g")
    export_unit(g_path)
    mesh = read_g(g_path)
    eq(part_names(mesh), ["empire_angel_wing", "empire_angel",
                          "empire_angel_cloth", "SwordSkinned"],
       "the extra mesh is a part, in order")
    part = mesh.parts[-1]
    # the glTF splits a cube into 24 vertices (one per face corner); every
    # one of them must survive, none may be dropped
    assert_(part.vertex_count >= verts,
            f"cube lost vertices: {part.vertex_count} < {verts}")
    eq([b.name for b in part.bones], [hand.name], "bound to the hand bone")
    eq(part.weights_on_vertex, 2, "influence slots")
    assert_(all(max(v.bones) == 0 for v in part.vertices),
            "every vertex indexes the part's single bone")


def _rest_centroid(ob):
    """World-space centroid of the bind cage (rest pose, no deformation)."""
    mw = ob.matrix_world
    pts = [mw @ v.co for v in ob.data.vertices]
    return centroid(pts)


def test_extra_rigid_mesh_follows_its_bone():
    arm = import_unit()
    hand = hand_bone(arm)
    cone = parent_to_bone(add_cube("SwordRigid", loc=(0.4, 0.2, 1.2)),
                          arm, hand)
    bpy.context.view_layer.update()
    want = _rest_centroid(cone)
    out = out_dir("extra_rigid")
    g_path = os.path.join(out, "character_empire_angel.g")
    export_unit(g_path)
    mesh = read_g(g_path)
    eq(part_names(mesh)[-1], "SwordRigid", "rigid mesh became a part")
    part = mesh.parts[-1]
    eq([b.name for b in part.bones], [hand.name],
       "rigid part bound to the bone it hangs from")
    eq(part.weights_on_vertex, 2, "rigid part influence slots")
    assert_(all(v.stored_weights == (1.0,) and set(v.bones) == {0}
                for v in part.vertices), "every vertex fully on that bone")
    # the real question: does it come back where the artist put it?
    reset()
    import_unit(g_path)
    bpy.context.view_layer.update()
    back = bpy.data.objects["SwordRigid"]
    got = _rest_centroid(back)
    for i, axis in enumerate("xyz"):
        near(got[i], want[i], 0.01,
             f"rigid prop moved along {axis} (got {got}, want {want})")


def test_extra_mesh_with_its_own_texture():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm = import_unit()
    hand = hand_bone(arm)
    png = os.path.join(TMP, "user_sword.png")
    make_png(png)
    img = bpy.data.images.load(png)
    mat = bpy.data.materials.new("user_sword_mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    cube = skin_to(add_cube("UserSword", material=mat), arm, hand)
    out = out_dir("extra_tex")
    g_path = os.path.join(out, "character_empire_angel.g")
    export_unit(g_path)
    mesh = read_g(g_path)
    part = mesh.parts[-1]
    eq(part.name, "UserSword", "part name")
    diffuse = part.attrs.get("material0_diffuse", "")
    assert_(diffuse.endswith(".tga"), f"diffuse attribute: {diffuse}")
    emitted = os.path.join(out, os.path.splitext(diffuse)[0] + ".t")
    assert_(os.path.isfile(emitted),
            f"{os.path.basename(emitted)} must exist for the new part")
    info = texmod.parse_t(open(emitted, "rb").read())
    assert_(info.width == 8 and info.height == 8,
            f"the user's 8x8 PNG became a {info.width}x{info.height} .t")


def test_extra_mesh_with_awkward_name():
    arm = import_unit()
    hand = hand_bone(arm)
    cube = add_cube("Клинок")           # non-Latin-1 object name
    skin_to(cube, arm, hand)
    add_cube("Клинок_2")
    skin_to(bpy.context.object, arm, hand)
    out = out_dir("extra_name")
    g_path = os.path.join(out, "character_empire_angel.g")
    export_unit(g_path)                 # must not raise
    mesh = read_g(g_path)
    names = part_names(mesh)
    eq(len(names), 5, "both meshes exported")
    eq(len(set(names)), len(names), "part names stay unique")
    for n in names:
        n.encode("latin1")              # every name is encodable


def test_hidden_mesh_is_skipped():
    arm = import_unit()
    hand = hand_bone(arm)
    skin_to(add_cube("HiddenSword"), arm, hand).hide_render = True
    skin_to(add_cube("VisibleSword"), arm, hand)
    out = out_dir("hidden")
    g_path = os.path.join(out, "character_empire_angel.g")
    export_unit(g_path)
    names = part_names(read_g(g_path))
    assert_("VisibleSword" in names, "visible mesh exported")
    assert_("HiddenSword" not in names, "hidden mesh skipped")


def test_extra_mesh_with_two_materials():
    arm = import_unit()
    hand = hand_bone(arm)
    cube = add_cube("TwoMatSword")
    cube.data.materials.append(bpy.data.materials[0]
                               if bpy.data.materials else None)
    cube.data.materials.append(bpy.data.materials.new("Second"))
    skin_to(cube, arm, hand)
    out = out_dir("two_mat")
    g_path = os.path.join(out, "character_empire_angel.g")
    export_unit(g_path)                 # warns, but must succeed
    assert_("TwoMatSword" in part_names(read_g(g_path)), "part written")


def test_extra_mesh_sharing_the_body_texture():
    arm = import_unit()
    body = bpy.data.objects["empire_angel"]
    mat = body.material_slots[0].material
    cube = skin_to(add_cube("Cape", material=mat), arm, hand_bone(arm))
    out = out_dir("shared_tex")
    export_unit(os.path.join(out, "character_empire_angel.g"))
    part = read_g(os.path.join(out, "character_empire_angel.g")).parts[-1]
    eq(part.attrs["material0_diffuse"], "character_empire_angel.tga",
       "reuses the body atlas")


def test_exported_extra_part_can_be_imported_back():
    arm = import_unit()
    hand = hand_bone(arm)
    hand_name = hand.name
    skin_to(add_cube("RoundTripSword"), arm, hand)
    out = out_dir("reimport")
    export_unit(os.path.join(out, "character_empire_angel.g"))
    reset()
    arm2 = import_unit(os.path.join(out, "character_empire_angel.g"))
    names = {o.name for o in bpy.context.scene.objects if o.type == "MESH"}
    assert_("RoundTripSword" in names, f"re-imported objects: {sorted(names)}")
    ob = bpy.data.objects["RoundTripSword"]
    assert_(any(m.type == "ARMATURE" and m.object is arm2
                for m in ob.modifiers), "the new part keeps its skin")
    groups = {g.name for g in ob.vertex_groups}
    assert_(hand_name in groups, f"vertex groups: {groups}")


# --------------------------------------------------------------------------- #
#  awkward assets
# --------------------------------------------------------------------------- #
def test_rigid_unit_roundtrip():
    if not os.path.isfile(RIGID):
        raise AssertionError("rigid sample missing")
    root = import_unit(RIGID)
    info = addon.unitprops.get_info(root) or {}
    # the Blacknaga `.g` carries no part table at all (the geometry sits in the
    # main block), yet the unit ships two animation streams — the metadata must
    # say so instead of guessing "no skeleton, no animation"
    eq(len(info.get("anims") or []), 2, "Blacknaga ships two animation streams")
    assert_(root.type in {"MESH", "ARMATURE"}, "rigid unit imported")
    out = out_dir("rigid")
    g_path = os.path.join(out, os.path.basename(RIGID))
    export_unit(g_path)
    a, b = read_g(RIGID), read_g(g_path)
    eq(b.parse_error, None, "re-read fails")
    # a Blender re-save splits vertices at the normal/UV seams (Blacknaga:
    # 3448 -> 3452), so the vertex COUNT may grow; the geometry must not.  The
    # live triangle count (degenerate-free) is the invariant.
    assert_(b.vertex_count >= a.vertex_count,
            f"vertex count shrank: {b.vertex_count} < {a.vertex_count}")
    eq(live_tri_count(b), live_tri_count(a), "live triangle count")
    eq(b.tri_count, a.tri_count, "triangle count")
    eq(len(b.parts), len(a.parts), "part count")


def test_morph_unit_roundtrip():
    if not os.path.isfile(MORPH):
        raise AssertionError("morph sample missing")
    import_unit(MORPH)
    out = out_dir("morph")
    g_path = os.path.join(out, os.path.basename(MORPH))
    export_unit(g_path)
    a = read_g(MORPH)
    b = read_g(g_path)
    eq(b.name, a.name, "container name")
    eq(len(b.parts), len(a.parts), "part count")
    assert_(b.vertex_count > 0, "geometry written")


def _dominant_signature(ob):
    """(dominant bone name, weight rounded to 1/100) per vertex."""
    names = {g.index: g.name for g in ob.vertex_groups}
    out = []
    for v in ob.data.vertices:
        inf = sorted([g for g in v.groups if g.weight > 1e-6],
                     key=lambda g: -g.weight)
        out.append((names[inf[0].group], round(inf[0].weight, 2))
                   if inf else (None, 0.0))
    return out


def _file_signatures(part):
    """The same signatures read straight out of a `.g` part."""
    bone_names = [b.name for b in part.bones]
    out = []
    for v in part.vertices:
        w = v.influence_weights()
        if not w:
            out.append((None, 0.0))
            continue
        top = max(range(len(w)), key=lambda i: w[i])
        name = bone_names[v.bones[top]] if top < len(v.bones) else None
        out.append((name, round(w[top], 2)))
    return out


def test_edited_weights_reach_the_file():
    """Repainting weights is the whole point of the round trip: the export
    must follow the Blender data, not replay the donor.

    The dominant influence of every vertex is collected as a multiset of
    ``(bone, weight)`` signatures; a score of vertices whose signature is
    unique gets its two weight lanes swapped, and the file must then carry
    exactly the swapped multiset — no more, no less.  Every vertex also has
    to keep at most 4 influences summing to 1.0."""
    import collections

    arm = import_unit(ANGEL)
    body = max([o for o in bpy.context.scene.objects if o.type == "MESH"],
               key=lambda o: len(o.data.vertices))
    body_name = body.name
    names = {g.index: g.name for g in body.vertex_groups}

    def clear_of_rounding(w):
        return abs(w * 100 - round(w * 100)) < 0.4

    before = collections.Counter(_dominant_signature(body))
    swapped = {}
    for v in body.data.vertices:
        inf = sorted([g for g in v.groups if g.weight > 1e-6],
                     key=lambda g: -g.weight)
        # exactly two influences and a wide gap: no third lane, and 8-bit
        # quantisation cannot flip the order
        if len(inf) != 2 or inf[0].weight - inf[1].weight < 0.3:
            continue
        # the larger lane moves onto the second bone: (A, w) -> (B, w)
        here = (names[inf[0].group], round(inf[0].weight, 2))
        there = (names[inf[1].group], round(inf[0].weight, 2))
        # unique signatures, so the multiset difference is unambiguous
        if before[here] != 1 or before[there] != 0:
            continue
        if not (clear_of_rounding(inf[0].weight)
                and clear_of_rounding(inf[1].weight)):
            continue
        # read both weights first: a VertexGroupElement is a live proxy, so
        # the second `.add` would otherwise see the value the first one wrote
        wa, wb = inf[0].weight, inf[1].weight
        body.vertex_groups[inf[0].group].add([v.index], wb, "REPLACE")
        body.vertex_groups[inf[1].group].add([v.index], wa, "REPLACE")
        swapped[v.index] = (here, there)
        if len(swapped) >= 12:
            break
    assert_(len(swapped) >= 6,
            f"only {len(swapped)} usable two-influence vertices")

    out = out_dir("weights")
    g_path = os.path.join(out, os.path.basename(ANGEL))
    export_unit(g_path)
    dst = read_g(g_path)
    assert_(dst.parse_error is None,
            f"exported .g does not parse: {dst.parse_error}")
    for blk in all_blocks(dst):
        for v in blk.vertices:
            w = v.influence_weights()
            assert_(len(w) <= 4,
                    f"{blk.name}: {len(w)} influences on one vertex")
            near(sum(w), 1.0, 1e-6, f"{blk.name}: weights must sum to 1")

    want = collections.Counter(before)
    for was, now in swapped.values():
        want[was] -= 1
        want[now] += 1
    part = [p for p in dst.parts if p.name == body_name][0]
    got = collections.Counter(_file_signatures(part))
    missing = {k: n for k, n in (want - got).items() if n}
    extra = {k: n for k, n in (got - want).items() if n}
    eq(missing, {}, "signatures the swap should have added")
    # the export may split a vertex at a UV/normal seam, which adds copies
    # (`got` is then larger than the Blender mesh) — but a signature the swap
    # took away must not survive anywhere
    removed = {was for was, _now in swapped.values()}
    survivors = {k: n for k, n in extra.items() if k in removed}
    eq(survivors, {}, "the old dominant influence survived the round trip")


def test_edited_mesh_reaches_the_file():
    """Moving vertices and deleting faces must show up in the `.g`: it is
    rebuilt from the Blender mesh, not copied from the donor.  Ten vertices
    are nudged by 0.01/0.02/0.03 and four faces deleted; the file must lose
    exactly those faces (its triangle count is computed from the Blender
    polygons) and gain exactly ten vertices that sit ~0.03 away from the
    donor's own geometry."""
    import bmesh

    arm = import_unit(ANGEL)
    body = max([o for o in bpy.context.scene.objects if o.type == "MESH"],
               key=lambda o: len(o.data.vertices))
    body_name = body.name
    me = body.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    moved = {}
    for v in bm.verts[:10]:
        v.co.x += 0.01
        v.co.y += 0.02
        v.co.z += 0.03
        moved[v.index] = tuple(v.co)
    for f in list(bm.faces[:4]):
        bm.faces.remove(f)
    bm.to_mesh(me)
    bm.free()
    me.update()
    edited_tris = sum(len(p.vertices) - 2 for p in me.polygons)

    out = out_dir("mesh")
    g_path = os.path.join(out, os.path.basename(ANGEL))
    export_unit(g_path)
    dst = read_g(g_path)
    assert_(dst.parse_error is None,
            f"exported .g does not parse: {dst.parse_error}")
    part = [p for p in dst.parts if p.name == body_name][0]
    eq(live_tri_count(part), edited_tris, "triangles after deleting 4 faces")
    # the header every sub-mesh carries must advertise those counts: the
    # engine's loader believes it, and a stale pair desynchronises the vertex
    # and index blocks behind it (that is a crash on load, not a visual bug)
    header = struct.unpack_from("<7I", part.part_prefix, 0)
    eq(header[3], len(part.vertices), "header vertices")
    eq(header[4], len(part.indices) // 3, "header triangles")
    eq(gfile.count_mismatches(dst), [], "no stale container counts")

    # the .g lives in the game's own space, so the nudged vertices are
    # compared with the donor's geometry
    donor = [p for p in read_g(ANGEL).parts if p.name == body_name][0]
    donor_pos = [v.position for v in donor.vertices]

    def distance(p, pts):
        return min(max(abs(p[i] - q[i]) for i in range(3)) for q in pts)

    far = [v.position for v in part.vertices
           if distance(v.position, donor_pos) > 1e-3]
    eq(len(far), len(moved), "vertices displaced from the donor geometry")
    for p in far:
        d = distance(p, donor_pos)
        assert_(0.01 <= d <= 0.06,
                f"a nudged vertex sits {d:.3f} from the donor geometry")


def test_second_unit_in_the_scene_is_left_alone():
    import_unit(ANGEL)
    first = addon.unitprops.find_armature(bpy.context)
    import_unit(RIGID)
    # both units live in the scene now; the second one is rigid (no
    # armature at all), so its root is a mesh
    second = addon.unitprops.find_unit_root(bpy.context)
    assert_(second is not None and second is not first,
            "the second unit did not become its own root")
    owners = {ob.name: ob.get(addon.unitprops.OWNER_PROP)
              for ob in bpy.context.scene.objects if ob.type == "MESH"}
    assert_(len({o for o in owners.values() if o}) == 2,
            f"the two units must be told apart: {owners}")
    bpy.context.view_layer.objects.active = first
    first.select_set(True)
    # ...and a foreign mesh skinned to the Angel's hand, slipped in between
    # the two units, must land in the Angel and never in the Blacknaga
    hand = hand_bone(first)
    skin_to(add_cube("StolenSword"), first, hand)
    out = out_dir("two_units")
    g_path = os.path.join(out, "character_empire_angel.g")
    export_unit(g_path)
    mesh = read_g(g_path)
    eq(len(mesh.parts), 4, "the Angel (3 parts) plus the extra mesh")
    eq(part_names(mesh)[-1], "StolenSword", "the foreign mesh went to the Angel")

    # now export the other unit out of the very same scene
    for ob in bpy.context.scene.objects:
        ob.select_set(False)
    bpy.context.view_layer.objects.active = second
    second.select_set(True)
    g2 = os.path.join(out, "character_neutrals_blacknaga.g")
    export_unit(g2)
    other = read_g(g2)
    eq(len(other.parts), 0, "Blacknaga is a single-block .g, not a compound")
    eq(other.parse_error, None, "the Blacknaga file still parses")
    assert_(other.vertex_count >= 3448,
            f"Blacknaga lost vertices: {other.vertex_count}")
    assert_("StolenSword" not in part_names(other),
            "the foreign mesh leaked into the other unit")
    eq(len(other.bones), 38,
       "Blacknaga keeps its own 38-bone table (not the Angel's 30)")


# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #
def test_lod_import_says_it_is_the_far_model():
    """The unit ships two meshes out of one folder and the game draws the
    non-lod one up close, so editing the lod copy changes nothing in battle.
    Importing it has to say so — that mistake already cost a full report
    ("my weights never reached the game") that was in fact a correct export
    of a model the camera only switches to at range."""
    root = import_unit(ANGEL_LOD)
    info = addon.unitprops.get_info(root) or {}
    warnings = info.get("warnings") or []
    assert_(any("far/LOD model" in w for w in warnings), warnings)
    assert_(any("character_empire_angel.g" in w for w in warnings), warnings)
    # the near model is the unit itself: nothing to warn about
    reset()
    root = import_unit(ANGEL)
    info = addon.unitprops.get_info(root) or {}
    assert_(not any("far/LOD" in w for w in (info.get("warnings") or [])),
            info.get("warnings"))


TESTS = [
    ("import builds armature, meshes and metadata", test_import_structure),
    ("import restores 30 fps animation", test_import_animation_and_fps),
    ("import packs the textures", test_import_textures_packed),
    ("import is repeatable", test_import_is_idempotent_per_scene),
    ("untouched round trip keeps .a/.ac/.scene/.t", 
     test_roundtrip_writes_identical_animation),
    ("round trip keeps the geometry", test_roundtrip_geometry_survives),
    ("export elsewhere still uses the import donors",
     test_export_into_other_folder_uses_the_import_donors),
    ("export works without import metadata",
     test_export_without_import_metadata),
    ("extra skinned mesh becomes a .g part",
     test_extra_skinned_mesh_becomes_a_part),
    ("extra rigid mesh follows its bone",
     test_extra_rigid_mesh_follows_its_bone),
    ("extra mesh brings its own texture",
     test_extra_mesh_with_its_own_texture),
    ("awkward (non Latin-1) object names export",
     test_extra_mesh_with_awkward_name),
    ("hidden meshes are skipped", test_hidden_mesh_is_skipped),
    ("multi-material mesh exports its first slot",
     test_extra_mesh_with_two_materials),
    ("extra mesh can share the body atlas",
     test_extra_mesh_sharing_the_body_texture),
    ("exported extra part re-imports as a skinned object",
     test_exported_extra_part_can_be_imported_back),
    ("rigid unit round trip", test_rigid_unit_roundtrip),
    ("morph unit round trip", test_morph_unit_roundtrip),
    ("other units in the scene keep out of the export",
     test_second_unit_in_the_scene_is_left_alone),
    ("edited weights reach the file", test_edited_weights_reach_the_file),
    ("edited mesh reaches the file", test_edited_mesh_reaches_the_file),
    ("a lod import says it is the far model",
     test_lod_import_says_it_is_the_far_model),
]


def main(argv=None):
    only = None
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    addon.register()
    print(f"[addon-test] {addon.bridge.version_hint()}; Blender "
          f"{bpy.app.version_string}")
    for name, fn in TESTS:
        if only and only.lower() not in name.lower():
            continue
        check(name, fn)
    shutil.rmtree(TMP, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 70)
    for name, ok, err in RESULTS:
        if not ok:
            print(f"FAIL {name}\n{err}")
    print(f"TOTAL: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    code = main()
    print("RESULT", "FAILED" if code else "OK")
    sys.exit(code)
