"""Self-contained tests for the d3tool package (run with `python3 tests/`)."""
import base64
import copy
import dataclasses
import hashlib
import json
import glob
import os
import pathlib
import shutil
import tempfile
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import d3tool
from d3tool import anim as animmod
from d3tool import cli as climod
from d3tool import gfile, gltf, ac as acmod, gltf_out, scene as scenemod
from d3tool import parts as partsmod
from d3tool import audit as auditmod
from d3tool import texture as texmod
from d3tool import weapon as weaponmod

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_air_roundtrip():
    p = os.path.join(REPO, "Neutrals", "AirElemental",
                     "character_neutrals_airelemental.g")
    data = open(p, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    assert mesh.vertex_count == 3453
    assert mesh.tri_count == 5056
    assert len(mesh.bones) == 38

    attrs, _ = gfile.parse_attributes(data)
    out = gfile.write_geometry_file(mesh, attrs)
    assert out == data, "AirElemental .g must round-trip byte-for-byte"


def test_all_g_roundtrip():
    import glob
    for p in sorted(glob.glob(os.path.join(REPO, "Neutrals", "*", "character_*.g"))):
        data = open(p, "rb").read()
        mesh = gfile.parse_geometry_file(data)
        attrs, _ = gfile.parse_attributes(data)
        out = gfile.write_geometry_file(mesh, attrs)
        assert out == data, f"{os.path.basename(p)} must round-trip byte-for-byte"


def test_weapon_mesh_roundtrip():
    # The Zombie LOD is a compound file (weapon mesh + character LOD) whose
    # first mesh is a single-bone (w=1) weapon; it must round-trip exactly.
    p = os.path.join(REPO, "Neutrals", "Zombie",
                     "character_neutrals_zombie_lod.g")
    data = open(p, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    assert mesh.weights_on_vertex == 1
    attrs, _ = gfile.parse_attributes(data)
    assert gfile.write_geometry_file(mesh, attrs) == data


def test_gltf_to_g_matches_original():
    base = os.path.join(REPO, "Neutrals", "AirElemental",
                        "character_neutrals_airelemental")
    m = gltf.load_gltf(base + ".gltf", weights_on_vertex=2)
    sm = gltf.mesh_to_skinned(m, 2)

    attrs = {
        "dwNode": "0", "dwParent": "0", "name": sm.name,
        "groupname": "Scene Root", "materials_num": "1",
        "material0_diffuse": "character_neutrals_airelemental.tga",
        "new_vertex_weights_format": "1", "weights_on_vertex": "2",
    }
    g_bytes = gfile.write_geometry_file(sm, attrs)
    back = gfile.parse_geometry_file(g_bytes)
    orig = gfile.parse_geometry_file(open(base + ".g", "rb").read())

    assert back.vertex_count == orig.vertex_count
    assert back.tri_count == orig.tri_count
    assert back.indices == orig.indices
    assert [b.name for b in back.bones] == [b.name for b in orig.bones]
    # skeleton matrices (inverse bind) must match
    for x, y in zip(back.bones, orig.bones):
        assert all(abs(u - v) < 1e-5 for u, v in zip(x.matrix, y.matrix)), x.name


def test_gltf_detects_3_influence_wildboar():
    """Wildboar is written by dis3tool with 3 influence slots.  Reverse export
    must auto-detect that (rather than truncating to 2), otherwise the skin is
    corrupted and the Khronos validator reports ACCESSOR_WEIGHTS_NON_NORMALIZED.
    """
    base = os.path.join(REPO, "Neutrals", "Wildboar",
                        "character_neutrals_wildboar")
    m = gltf.load_gltf(base + ".gltf")  # default auto-detect
    assert m.weights_on_vertex == 3, m.weights_on_vertex
    sm = gltf.mesh_to_skinned(m)
    assert sm.weights_on_vertex == 3, sm.weights_on_vertex
    # a known 3-influence vertex: weight split across slots 0 and 2 must be
    # preserved, not collapsed onto a single (duplicate) joint.
    v = sm.vertices[162]
    assert abs(v.gltf_weights[0] - 0.7) < 1e-6
    assert abs(v.gltf_weights[2] - 0.3) < 1e-6
    # dedup-by-joint weight sum must still be 1.0 (no dropped influence)
    sums = {}
    for w, j in zip(v.gltf_weights, v.gltf_joints):
        sums[j] = sums.get(j, 0.0) + w
    assert abs(sum(sums.values()) - 1.0) < 1e-6


def test_ac_roundtrip():
    cfg = acmod.default_ac("mesh.g", "unit")
    text = acmod.write_ac(cfg)
    cfg2 = acmod.parse_ac(text)
    assert [s.name for s in cfg2.states] == ["Idle", "Attack", "Damage",
                                             "Death", "Run"]
    # frames are written 0-based inclusive (see `ac.default_ac`): a
    # 150-frame template is frames 0..149, never 0..150
    assert cfg2.states[0].frame0 == 0
    assert cfg2.states[0].frame1 == 149


def test_ac_bundled_roundtrip_semantic():
    """Every bundled `.ac` must round-trip with identical state semantics.

    `write_ac` emits the bodies without leading indentation, matching the
    original files.  A single `.ac` (Wolfsnow) contains a blank line inside a
    state body which the parser drops; that is purely cosmetic (all statements
    are `;`-terminated), so we compare the parsed state structure rather than
    bytes.
    """
    import glob
    def sig(cfg):
        return [(s.name, s.frame0, s.frame1, s.fps, s.priority, s.flags,
                 s.meshfile, list(s.links), list(s.events))
                for s in cfg.states]
    for p in sorted(glob.glob(os.path.join(REPO, "Neutrals", "*", "*.ac"))):
        data = open(p, "r", encoding="utf-8-sig", errors="replace").read()
        cfg1 = acmod.parse_ac(data)
        cfg2 = acmod.parse_ac(acmod.write_ac(cfg1))
        assert sig(cfg2) == sig(cfg1), os.path.basename(p)
    # byte-identity holds except for three purely-cosmetic originals: a blank
    # line inside a state body (Wolfsnow / Watersnake cast) and a stray
    # trailing space after a frame1 value (large orc ship).  Statements parse
    # identically (asserted semantically above).
    mismatch = [os.path.basename(p) for p in
                sorted(glob.glob(os.path.join(REPO, "Neutrals", "*", "*.ac")))
                if acmod.write_ac(acmod.parse_ac(
                    open(p, "r", encoding="utf-8-sig", errors="replace").read()
                )).rstrip() != open(p, "r", encoding="utf-8-sig",
                                    errors="replace").read().rstrip()]
    assert mismatch == ["character_large_orc_ship.ac",
                        "character_neutrals_watersnake_cast.ac",
                        "character_neutrals_wolfsnow.ac"], mismatch


def test_version():
    assert isinstance(d3tool.__version__, str) and d3tool.__version__


def test_cli_export_and_roundtrip():
    import subprocess
    import tempfile
    base = os.path.join(REPO, "Neutrals", "AirElemental",
                        "character_neutrals_airelemental")
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run(
            [sys.executable, "-m", "d3tool", "export", base + ".gltf",
             "-o", os.path.join(d, "re")],
            capture_output=True, text=True, cwd=REPO, check=False)
        assert r.returncode == 0, r.stderr
        re_dir = os.path.join(d, "re")
        assert os.path.exists(os.path.join(re_dir, "character_neutrals_airelemental.g"))
        assert os.path.exists(os.path.join(re_dir, "character_neutrals_airelemental.scene"))
        assert os.path.exists(os.path.join(re_dir, "character_neutrals_airelemental.ac"))
        assert os.path.exists(os.path.join(re_dir, "character_neutrals_airelemental_iadd.a"))
        # forward round-trip must validate clean
        fwd = os.path.join(d, "fwd", "u.gltf")
        r2 = subprocess.run(
            [sys.executable, "-m", "d3tool", "export-gl",
             os.path.join(re_dir, "character_neutrals_airelemental.g"),
             "-a", os.path.join(re_dir, "character_neutrals_airelemental_iadd.a"),
             "-o", fwd], capture_output=True, text=True, cwd=REPO,
            check=False)
        assert r2.returncode == 0, r2.stderr
        errors, warnings, info = gltf_out.validate_gltf(fwd)
        assert errors == 0, f"roundtrip glTF must be valid, got {errors}"


def test_a_parse():
    p = os.path.join(REPO, "Neutrals", "AirElemental",
                     "character_neutrals_airelemental_iadd.a")
    an = animmod.parse_anim(open(p, "rb").read())
    assert an.frame_count == 346
    assert an.bones[0].name == "Root"
    assert an.bones[0].parent == "Scene Root"


def test_anim_from_gltf_matches_original():
    base = os.path.join(REPO, "Neutrals", "AirElemental",
                        "character_neutrals_airelemental")
    m = gltf.load_gltf(base + ".gltf")
    an = animmod.parse_anim(open(base + "_iadd.a", "rb").read())
    rebuilt = gltf.animation_from_gltf(m)
    assert len(rebuilt.bones) == len(an.bones)
    assert rebuilt.bones[0].name == "Root"
    assert rebuilt.bones[0].parent == "Scene Root"
    assert rebuilt.bones[1].name == "Hips" and rebuilt.bones[1].parent == "Root"
    # frame values for a mid bone must reproduce the original exactly
    orig = an.bones[1].frames[0]
    mine = rebuilt.bones[1].frames[0]
    assert all(abs(v - b) < 1e-5 for v, b in zip(mine, orig)), (mine, orig)


def test_forward_export_matches_gltf():
    # .g/.a -> glTF must reproduce the dis3tool glTF geometry/skeleton.
    base = os.path.join(REPO, "Neutrals", "AirElemental",
                        "character_neutrals_airelemental")
    mesh = gfile.parse_geometry_file(open(base + ".g", "rb").read())
    an = animmod.parse_anim(open(base + "_iadd.a", "rb").read())
    binb, doc = gltf_out.write_gltf(mesh, an, "x")

    ref = json.load(open(base + ".gltf"))
    refbin = open(base + ".bin", "rb").read()

    def acc(g, b, idx):
        a = g["accessors"][idx]
        bv = g["bufferViews"][a["bufferView"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        n = a["count"]
        nc = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
        fmt = {5126: "f", 5121: "B", 5125: "I", 5123: "H"}[a["componentType"]]
        size = struct.calcsize(fmt)
        stride = bv.get("byteStride", nc * size)
        return [struct.unpack_from("<" + fmt * nc, b, off + i * stride)
                for i in range(n)]

    for label, idx in [("POSITION", 1), ("NORMAL", 2), ("TEXCOORD_0", 3),
                       ("WEIGHTS_0", 4), ("indices", 0),
                       ("inverseBindMatrices", 6)]:
        r = acc(ref, refbin, idx)
        m = acc(doc, binb, idx)
        assert len(r) == len(m), label
        if idx == 4:  # weights: allow tiny float diffs
            assert all(all(abs(a - b) < 1e-5 for a, b in zip(x, y))
                        for x, y in zip(r, m)), label
        else:
            assert all(all(abs(a - b) < 1e-5 for a, b in zip(x, y))
                        for x, y in zip(r, m)), label

    # Animation time base: one keyframe per `.a` frame on a 30 fps clock,
    # t[k] = float32(k * float32(1/30)) seconds — not a normalised 0..1
    # range.  The dis3tool reference uses exactly this clock (verified
    # byte-for-byte for every bundled unit), so our track must equal its
    # prefix; the reference is longer only because its source `.a` carried
    # 363 frames instead of 346.
    f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]  # noqa: E731
    step = f32(1.0 / 30.0)
    mine_t = [t[0] for t in acc(doc, binb, 7)]
    ref_t = [t[0] for t in acc(ref, refbin, 7)]
    assert len(mine_t) == an.frame_count
    assert mine_t == [f32(k * step) for k in range(len(mine_t))]
    assert mine_t == ref_t[: len(mine_t)]


def test_forward_export_weights_normalized():
    """Forward-exported glTF WEIGHTS_0 must sum to 1.0 per vertex (deduped by
    joint).  This guards the two bugs that used to trip the Khronos validator:
    3-slot skins being truncated to 2 (collapsing a real influence onto a
    duplicate joint) and float32 residuals leaving e.g. 0.9999995.
    """
    import tempfile
    base = os.path.join(REPO, "Neutrals", "Zombie",
                        "character_neutrals_zombie")
    mesh = gfile.parse_geometry_file(open(base + ".g", "rb").read())
    an = animmod.parse_anim(open(base + "_baseanims.a", "rb").read())

    with tempfile.TemporaryDirectory() as d:
        gp = os.path.join(d, "u.gltf")
        gltf_out.write_gltf_to(gp, mesh, an)
        doc = json.load(open(gp, "r", encoding="utf-8"))
        binb = open(os.path.join(d, "u.bin"), "rb").read()

        def acc(index, ncomp, fmt):
            a = doc["accessors"][index]
            bv = doc["bufferViews"][a["bufferView"]]
            off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
            stride = bv.get("byteStride", ncomp * struct.calcsize(fmt))
            return [struct.unpack_from("<" + fmt * ncomp, binb, off + i * stride)
                    for i in range(a["count"])]

        w = acc(doc["meshes"][0]["primitives"][0]["attributes"]["WEIGHTS_0"], 4, "f")
        j = acc(doc["meshes"][0]["primitives"][0]["attributes"]["JOINTS_0"], 4, "B")
        for i, (wi, ji) in enumerate(zip(w, j)):
            # dis3tool keeps a non-zero joint even in a zero-weight slot, so the
            # validator deduplicates by joint before summing.
            sums = {}
            for ww, jj in zip(wi, ji):
                sums[jj] = sums.get(jj, 0.0) + ww
            assert abs(sum(sums.values()) - 1.0) < 1e-5, (i, wi, ji)


def test_exported_gltf_is_structurally_valid():
    import tempfile
    base = os.path.join(REPO, "Neutrals", "AirElemental",
                        "character_neutrals_airelemental")
    mesh = gfile.parse_geometry_file(open(base + ".g", "rb").read())
    an = animmod.parse_anim(open(base + "_iadd.a", "rb").read())
    with tempfile.TemporaryDirectory() as d:
        gp = os.path.join(d, "u.gltf")
        gltf_out.write_gltf_to(gp, mesh, an)
        errors, warnings, info = gltf_out.validate_gltf(gp)
        assert errors == 0, f"exported glTF must be structurally clean, got {errors}"


def test_validate_gltf_hardened():
    """`validate_gltf` must not crash on malformed / truncated input."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        # valid file is clean
        base = os.path.join(REPO, "Neutrals", "AirElemental",
                            "character_neutrals_airelemental")
        mesh = gfile.parse_geometry_file(open(base + ".g", "rb").read())
        an = animmod.parse_anim(open(base + "_iadd.a", "rb").read())
        gp = os.path.join(d, "u.gltf")
        gltf_out.write_gltf_to(gp, mesh, an)
        assert gltf_out.validate_gltf(gp)[0] == 0

        # truncated .bin -> reports an error, does not raise struct.error
        with open(os.path.join(d, "u.bin"), "rb") as fh:
            good = fh.read()
        with open(os.path.join(d, "u.bin"), "wb") as fh:
            fh.write(good[:100])
        errors, _, _ = gltf_out.validate_gltf(gp)
        assert errors >= 1

        # incomplete doc (no asset/version) -> error
        bad = os.path.join(d, "bad.gltf")
        with open(bad, "w") as fh:
            fh.write("{}")
        assert gltf_out.validate_gltf(bad)[0] >= 1


def test_scene_generation():
    attrs = {"name": "neutrals_airelemental", "bones_num": "38",
             "vertexs_weights_num": "3453", "weights_on_vertex": "2",
             "material0_triangles_num": "5056"}
    text = scenemod.write_scene(
        "neutrals_airelemental", "character_neutrals_airelemental",
        "resources\\characters\\neutrals\\airelemental", attrs,
        gobj_name="neutrals_airelemental")
    assert 'child bones "character_neutrals_airelemental"' in text
    assert 'child gobj "neutrals_airelemental"' in text
    assert ("resources\\characters\\neutrals\\airelemental"
            "\\character_neutrals_airelemental.ac") in text
    assert ("resources\\characters\\neutrals\\airelemental"
            "\\character_neutrals_airelemental.g") in text
    assert 'guicamera' in text
    # no particle emitters in the generated (they are authoring data)
    assert 'child particles' not in text


def test_analyze_gltf():
    base = os.path.join(REPO, "Neutrals", "AirElemental",
                        "character_neutrals_airelemental")
    m = gltf.load_gltf(base + ".gltf")
    assert m.vertex_count == 3453
    assert m.tri_count == 5056
    assert len(m.bones) == 38
    assert len(m.frames) > 0


def _tex_pairs():
    """Yield (t_path, expected_dds_path) for every bundled character texture."""
    import glob
    for t in sorted(glob.glob(os.path.join(REPO, "Neutrals", "*", "*character*.t"))):
        dds = t[:-2] + ".dds"
        if os.path.exists(dds):
            yield t, dds


def test_texture_t_to_dds_matches_bundled():
    """`t_to_dds` must reproduce the bundled .dds byte-for-byte."""
    found = False
    for t, dds in _tex_pairs():
        found = True
        gen = texmod.t_to_dds(open(t, "rb").read())
        assert gen == open(dds, "rb").read(), os.path.basename(t)
    assert found, "no character .t/.dds pairs found"


def test_texture_roundtrip_all():
    """Every bundled .t -> .dds -> .t round-trips byte-identically."""
    import glob
    count = 0
    for t in sorted(glob.glob(os.path.join(REPO, "Neutrals", "*", "*.t"))):
        tb = open(t, "rb").read()
        dds = texmod.t_to_dds(tb)
        back = texmod.dds_to_t(dds, open(t, "rb").read(59))
        assert back == tb, os.path.basename(t)
        count += 1
    assert count >= 6, f"too few .t files found: {count}"


def test_texture_convert_file_both_directions():
    """`convert_file` handles .t->.dds and .dds->.t (payload preserved)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(REPO, "Neutrals", "AirElemental",
                         "character_neutrals_airelemental.t")
        # .t -> .dds must equal the bundled .dds
        dds = os.path.join(d, "out.dds")
        info = texmod.convert_file(t, dds)
        assert info.fourcc == b"DXT1"
        assert open(dds, "rb").read() == open(
            os.path.join(REPO, "Neutrals", "AirElemental",
                         "character_neutrals_airelemental.dds"), "rb").read()
        # .dds -> .t (no matching .t next to it -> default header) keeps payload
        back = os.path.join(d, "back.t")
        texmod.convert_file(dds, back)
        orig = open(t, "rb").read()
        rtd = open(back, "rb").read()
        assert orig[59:] == rtd[59:], "payload must be preserved"


def test_texture_payload_size():
    """`TextureInfo.payload_size` must equal the actual payload for every
    format, including the uncompressed 16-bit A1R5G5B5 form (2 bytes/pixel,
    not 4x4-block compressed)."""
    import glob
    count = 0
    for t in sorted(glob.glob(os.path.join(REPO, "Neutrals", "*", "*.t"))):
        info = texmod.parse_t(open(t, "rb").read())
        actual = len(info.payload)
        calc = info.payload_size()
        # One bundled texture (weapon_neutrals_zombie_diffuse, 256x512) stores a
        # partial 4-byte final mip; allow that single trailing-block gap there,
        # otherwise the computed mip chain must match the payload exactly.
        if os.path.basename(t) == "weapon_neutrals_zombie_diffuse.t":
            assert abs(calc - actual) <= 8, os.path.basename(t)
        else:
            assert calc == actual, f"{os.path.basename(t)}: {calc} != {actual}"
        count += 1
    assert count >= 6, f"too few .t files found: {count}"


def test_texture_find_diffuse():
    """`find_diffuse_texture` locates the .dds/.t next to a .g."""
    g = os.path.join(REPO, "Neutrals", "AirElemental",
                     "character_neutrals_airelemental.g")
    data = open(g, "rb").read()
    attrs, _ = gfile.parse_attributes(data)
    p = texmod.find_diffuse_texture(g, attrs)
    assert p and os.path.exists(p)
    assert p.endswith((".dds", ".t"))


def test_w1_multi_bone_main_mesh_layout():
    """`weights_on_vertex == 1` with `bones_num > 1` actually stores the full
    w=2 record (1.0 weight + two joint bytes); the parser must read the main
    mesh with the w=2 stride, like dis3tool's lod_empire_golem export, and
    the file must rebuild byte-for-byte."""
    p = os.path.join(REPO, "Empire", "Golem",
                     "character_empire_golem_lod.g")
    data = open(p, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    assert not mesh.raw, "golem_lod must parse structurally (no raw fallback)"
    assert mesh.weights_on_vertex == 2
    xs = [v.position[0] for v in mesh.vertices]
    assert -100.0 < min(xs) < max(xs) < 100.0, "positions must be sane floats"
    attrs, _ = gfile.parse_attributes(data)
    assert gfile.write_geometry_file(mesh, attrs) == data


def test_zombie_compound_weights_normalized():
    """Zombie is a compound container (body + weapon); the compound export
    must keep the implied-weight lane (2.65e-05) so that WEIGHTS_0 sums to
    exactly 1.0 per vertex — mirroring the reference dis3tool export."""
    import tempfile
    base = os.path.join(REPO, "Neutrals", "Zombie",
                        "character_neutrals_zombie")
    mesh = gfile.parse_geometry_file(open(base + ".g", "rb").read())
    an = animmod.parse_anim(open(base + "_baseanims.a", "rb").read())
    assert mesh.parts, "zombie must parse as a compound container"
    with tempfile.TemporaryDirectory() as d:
        gp = os.path.join(d, "u.gltf")
        gltf_out.write_gltf_to(gp, mesh, an)
        doc = json.load(open(gp, "r", encoding="utf-8"))
        binb = open(os.path.join(d, "u.bin"), "rb").read()
        pr = doc["meshes"][0]["primitives"][0]

        def acc(index, ncomp, fmt):
            a = doc["accessors"][index]
            bv = doc["bufferViews"][a["bufferView"]]
            off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
            stride = bv.get("byteStride", ncomp * struct.calcsize(fmt))
            return [struct.unpack_from("<" + fmt * ncomp, binb,
                                       off + i * stride)
                    for i in range(a["count"])]

        w = acc(pr["attributes"]["WEIGHTS_0"], 4, "f")
        j = acc(pr["attributes"]["JOINTS_0"], 4, "B")
        for wi, ji in zip(w, j):
            sums = {}
            for ww, jj in zip(wi, ji):
                sums[jj] = sums.get(jj, 0.0) + ww
            assert abs(sum(sums.values()) - 1.0) < 1e-5, (wi, ji)


def test_morph_only_animation_has_frames_input():
    """A morph-stream-only .a (no bone tracks, e.g. fatimp_lod.a) must still
    produce a valid `frames` sampler input — never a null-reference sampler."""
    import tempfile
    base = os.path.join(REPO, "Neutrals", "FatImp",
                        "character_neutrals_fatimp")
    mesh = gfile.parse_geometry_file(open(base + ".g", "rb").read())
    an = animmod.parse_anim(open(base + "_lod.a", "rb").read())
    with tempfile.TemporaryDirectory() as d:
        gp = os.path.join(d, "u.gltf")
        gltf_out.write_gltf_to(gp, mesh, an)
        doc = json.load(open(gp, "r", encoding="utf-8"))
        for anim in doc.get("animations", []):
            for smp in anim["samplers"]:
                assert smp["input"] is not None
                assert smp["output"] is not None
        errors, warnings, infos = gltf_out.validate_gltf(
            gp)
        assert errors == 0


def test_stub_g_is_a_headerless_node_helper():
    """The two bundled 602-byte Leader `.g` files are *header-less node
    helpers* (prelude first, no 120-byte header, no name strings) — valid
    files, not corruption.  They must parse as `form="stub"`, keep a
    lossless `.g` round-trip, and be refused by the glTF writer with an
    accurate "no triangles" message (a skin with zero joints is invalid)."""
    import tempfile
    for folder in ("Leader-Ranger", "Leader-Thief"):
        p = os.path.join(REPO, "Empire", folder,
                         f"character_empire_{folder.lower()}.g")
        data = open(p, "rb").read()
        mesh = gfile.parse_geometry_file(data)
        assert mesh.form == "stub", f"{folder}: expected the stub container form"
        assert mesh.name == "BaseMesh", f"{folder}: name from the `name` attr"
        assert len(mesh.vertices) == 4, f"{folder}: 4 helper vertices"
        # .g serialization stays lossless for the stub
        assert gfile.write_geometry_file(mesh, {}) == data
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "u.gltf")
            try:
                gltf_out.write_gltf_to(out, mesh, None)
            except ValueError as exc:
                assert "no triangles" in str(exc), str(exc)
            else:
                raise AssertionError(f"{folder}: expected ValueError for a stub")


def test_parse_attributes_reads_headerless_stub():
    """`parse_attributes` must read the stub's attribute block (it used to
    assume the 120-byte header + two name strings and report a valid file as
    corrupt)."""
    data = open(os.path.join(REPO, "Empire", "Leader-Ranger",
                             "character_empire_leader-ranger.g"),
                "rb").read()
    attrs, _ = gfile.parse_attributes(data)
    assert attrs["name"] == "BaseMesh"
    assert attrs["vertexs_weights_num"] == "4"
    assert attrs["materials_num"] == "0"


def test_parse_attributes_corrupt_bytes_raise_valueerror():
    """`parse_attributes` on garbage must raise a readable ValueError, not a
    struct.error with nonsense offsets."""
    for junk in (b"", b"\x00" * 16, os.urandom(256), b"\x03" + b"\xff" * 400):
        try:
            gfile.parse_attributes(junk)
        except ValueError as exc:
            assert "attribute block" in str(exc), str(exc)
        else:
            raise AssertionError("expected ValueError for a corrupt .g")


def test_reverse_export_preserves_ac_event_entries():
    """Regenerating the `.ac` from the template silently dropped every
    `event2` entry — the attack/damage/death sound aliases and the
    FxStrike/fxcast cues.  When the source `.ac` ships next to the glTF it
    must be reused verbatim, exactly like the `.scene`."""
    import tempfile
    from d3tool import cli
    src = os.path.join(REPO, "Neutrals", "AirElemental",
                       "character_neutrals_airelemental.ac")
    n_events = sum(1 for ln in open(src, encoding="utf-8")
                   if ln.strip().startswith("event2"))
    assert n_events > 0, "the reference .ac must carry event2 entries"
    with tempfile.TemporaryDirectory() as d:
        cli._export(os.path.join(REPO, "Neutrals", "AirElemental",
                                 "character_neutrals_airelemental.gltf"),
                    d, 0, anim=False, quiet=True)
        out = os.path.join(d, "character_neutrals_airelemental.ac")
        assert open(out, "rb").read() == open(src, "rb").read(), \
            "the source .ac must be reused byte-for-byte"
        kept = sum(1 for ln in open(out, encoding="utf-8")
                   if ln.strip().startswith("event2"))
        assert kept == n_events, f"{kept} of {n_events} event2 entries kept"


def test_validate_gltf_reports_errors_instead_of_raising():
    """`d3tool validate` inspects *untrusted* documents: a bad index or an
    unknown enum must be counted as an error, not escape as a traceback."""
    import json
    import tempfile
    bad_docs = [
        {"bufferView_out_of_range": {
            "asset": {"version": "2.0"},
            "buffers": [{"uri": "x.bin", "byteLength": 4}], "bufferViews": [],
            "accessors": [{"bufferView": 9, "count": 1, "type": "VEC3",
                           "componentType": 5126}]}},
        {"unknown_type": {
            "asset": {"version": "2.0"},
            "buffers": [{"uri": "x.bin", "byteLength": 4}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 4}],
            "accessors": [{"bufferView": 0, "count": 1, "type": "QUAT",
                           "componentType": 5126}]}},
        {"unknown_component_type": {
            "asset": {"version": "2.0"},
            "buffers": [{"uri": "x.bin", "byteLength": 4}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 4}],
            "accessors": [{"bufferView": 0, "count": 1, "type": "VEC3",
                           "componentType": 9999}]}},
        {"skin_without_joints": {
            "asset": {"version": "2.0"},
            "buffers": [{"uri": "x.bin", "byteLength": 4}], "bufferViews": [],
            "accessors": [], "skins": [{"inverseBindMatrices": 0}],
            "nodes": []}},
        {"sampler_index_out_of_range": {
            "asset": {"version": "2.0"},
            "buffers": [{"uri": "x.bin", "byteLength": 4}], "bufferViews": [],
            "accessors": [],
            "animations": [{"channels": [{"sampler": 0, "target": {
                "node": 0, "path": "rotation"}}],
                "samplers": [{"input": 5, "output": 6}]}], "nodes": []}},
        {"negative_mesh_index": {
            "asset": {"version": "2.0"},
            "buffers": [{"uri": "x.bin", "byteLength": 4}], "bufferViews": [],
            "accessors": [], "meshes": [], "nodes": [{"mesh": -1}]}},
        {"no_asset_version": {"buffers": []}},
    ]
    for case in bad_docs:
        label, doc = next(iter(case.items()))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.gltf")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            with open(os.path.join(d, "x.bin"), "wb") as fh:
                fh.write(b"\0" * 64)
            errors, _w, _i = gltf_out.validate_gltf(p)
            assert errors > 0, f"{label}: malformed glTF reported 0 errors"


def test_validate_gltf_reads_joints_with_their_real_component_type():
    """JOINTS_0 is componentType 5121 (unsigned byte) inside the interleaved
    view; decoding it as float32 turned real joint bytes [0,0,0,0] into
    [0,0,2,0] and defeated the dedup-by-joint weight rule."""
    import json
    import struct
    import tempfile
    from d3tool import cli
    base = os.path.join(REPO, "Neutrals", "AirElemental",
                        "character_neutrals_airelemental")
    with tempfile.TemporaryDirectory() as d:
        gt, bt = cli._export_gl(base + ".g", base + "_iadd.a",
                                os.path.join(d, "u.gltf"),
                                texture=None, quiet=True)
        doc = json.load(open(gt))
        buf = open(bt, "rb").read()
        prim = doc["meshes"][0]["primitives"][0]
        acc = doc["accessors"][prim["attributes"]["JOINTS_0"]]
        assert acc["componentType"] == 5121, "JOINTS_0 must stay UNSIGNED_BYTE"
        view = doc["bufferViews"][acc["bufferView"]]
        off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        raw = list(buf[off:off + 4])
        assert raw == [int(x) for x in raw], "joint bytes must be integral"
        # the same offset decoded as float32 (the old behaviour) differs
        as_f32 = [int(x) for x in struct.unpack_from("<4f", buf, off)]
        assert as_f32 != raw, "expected the float32 misread to be visible"
        assert gltf_out.validate_gltf(gt)[0] == 0


def test_forward_export_names_its_skin():
    """dis3tool names every skin (`skin0`, `skin1`, ...) in all 98 bundled
    references, and the compound writer already did; the single-mesh path
    omitted the key."""
    import json
    import tempfile
    from d3tool import cli
    base = os.path.join(REPO, "Neutrals", "AirElemental",
                        "character_neutrals_airelemental")
    with tempfile.TemporaryDirectory() as d:
        gt, _ = cli._export_gl(base + ".g", base + "_iadd.a",
                               os.path.join(d, "u.gltf"),
                               texture=None, quiet=True)
        assert json.load(open(gt))["skins"][0].get("name") == "skin0"


def test_public_api_is_re_exported():
    """`from d3tool import parse_anim` must work for every documented
    reader/writer, not just the geometry ones."""
    for name in ("parse_geometry_file", "write_geometry_file", "parse_attributes",
                 "load_gltf", "mesh_to_skinned", "animation_from_gltf",
                 "parse_anim", "write_anim", "build_anim",
                 "parse_ac", "write_ac", "default_ac", "detect_anim_files",
                 "write_scene", "parse_scene", "render_scene",
                 "count_particles", "parse_alias", "parse_alias_bytes",
                 "write_alias", "write_alias_bytes",
                 "write_gltf", "write_gltf_to", "validate_gltf",
                 "parse_t", "parse_dds", "t_to_dds", "dds_to_t", "convert_file",
                 "Bone", "Vertex", "SkinnedMesh", "MeshPart", "MorphTrack",
                 "pack_weights_joints",
                 "AnimFile", "BoneAnim", "AnimConfig", "State", "TextureInfo"):
        assert hasattr(d3tool, name), f"d3tool.{name} is not re-exported"
        assert name in d3tool.__all__, f"{name} missing from __all__"


def test_cubemap_t_produces_a_valid_cubemap_dds():
    """`build_dds_header` used to ignore `TextureInfo.faces`, so a cubemap
    `.t` became a 2D DDS header over six faces of payload — internally
    inconsistent, and no loader could reconcile it."""
    import struct
    p = os.path.join(REPO, "Empire", "Apprentice", "cubemap_default.t")
    data = open(p, "rb").read()
    info = texmod.parse_t(data, p)
    assert info.faces == 6, "the bundled cubemap must be detected as 6 faces"
    dds = texmod.t_to_dds(data, p)
    caps2 = struct.unpack_from("<I", dds, 112)[0]
    assert caps2 & 0x200, "dwCaps2 must carry DDSCAPS2_CUBEMAP"
    assert caps2 & 0xFC00 == 0xFC00, "all six face flags must be set"
    back = texmod.parse_dds(dds, p)
    assert back.faces == 6, "parse_dds must read the cubemap flag back"
    assert back.payload_size() == len(back.payload)
    assert texmod.dds_to_t(dds, info.t_header, p) == data, \
        "the cubemap round-trip must stay byte-identical"


def test_format_label_is_printable_for_every_gm_code():
    """An uncompressed DDS carries four NUL bytes in the fourCC slot, and
    `bool(b'\x00\x00\x00\x00')` is True — so testing fourCC for truthiness
    leaked NUL characters into the CLI output."""
    from d3tool import cli
    for code, label in ((3, "16-bit A1R5G5B5"), (1, "16-bit uncompressed"),
                        (2, "16-bit uncompressed"), (4, "32-bit A8R8G8B8"),
                        (5, "32-bit A8R8G8B8")):
        info = texmod.TextureInfo(width=4, height=4, mip_count=1,
                                  gm_format=code,
                                  fourcc=b"\x00\x00\x00\x00",
                                  r5g5b5=(code == 3))
        got = cli._format_label(info)
        assert got == label, f"code {code}: {got!r} != {label!r}"
        assert got.isprintable()


def test_weights_on_vertex_is_validated():
    """The GM vertex record only exists for 2/3/4 slots.  `--weights-on-vertex
    1` used to write a 40-byte-stride file that the reader re-interprets as the
    46-byte w=2 stride when bones_num > 1 — Wildboar came back with 0 vertices,
    and the CLI still exited 0."""
    import argparse
    from d3tool import cli  # noqa: F811 - local import keeps the test standalone
    for good in (0, 2, 3, 4):
        assert cli._weights_on_vertex(str(good)) == good
    for bad in ("1", "5", "99", "-1", "abc", ""):
        try:
            cli._weights_on_vertex(bad)
        except argparse.ArgumentTypeError:
            pass
        else:
            raise AssertionError(f"{bad!r} must be rejected")


def test_parse_anim_degrades_on_truncated_data():
    """`parse_anim` was the one reader with no defence against a truncated
    file: `str.index` escaped as a bare `ValueError: subsection not found`.
    It must degrade like `parse_geometry_file` and stay lossless."""
    data = open(os.path.join(REPO, "Neutrals", "AirElemental",
                             "character_neutrals_airelemental_iadd.a"),
                "rb").read()
    for cut in (b"", data[:4], data[:20], data[:len(data) // 2],
                data[:len(data) // 100], b"\xff" * 4096):
        anim = animmod.parse_anim(cut)
        assert animmod.write_anim(anim) == cut, \
            f"a {len(cut)}-byte input must round-trip verbatim"


def _copy_unit(unit: str, dst: str, gltf_ext: str = ".gltf") -> str:
    """Copy one corpus unit into *dst*, renaming the glTF to *gltf_ext*."""
    src = os.path.join(REPO, *unit.split("/"))
    os.makedirs(dst, exist_ok=True)
    for fn in os.listdir(src):
        p = os.path.join(src, fn)
        if os.path.isdir(p):
            shutil.copytree(p, os.path.join(dst, fn), dirs_exist_ok=True)
        else:
            shutil.copy(p, os.path.join(dst, fn))
    base = None
    for fn in os.listdir(dst):
        if fn.endswith(".gltf"):
            base = fn[: -len(".gltf")]
            if gltf_ext != ".gltf":
                os.replace(os.path.join(dst, fn),
                           os.path.join(dst, base + gltf_ext))
    return os.path.join(dst, base + gltf_ext)


def test_export_base_name_survives_a_non_gltf_extension():
    """`_export` used to chop a fixed 5 characters off the basename.  For
    `unit.glb` that produced `unit.g` AND made the sibling `.scene`/`.ac`
    lookups miss, silently dropping every particle emitter and every event2
    entry while still exiting 0."""
    from d3tool import cli
    with tempfile.TemporaryDirectory() as td:
        glb = _copy_unit("Empire/Rod-1", td, ".glb")
        assert glb.endswith(".glb")
        out = os.path.join(td, "out")
        cli._export(glb, out, 0, quiet=True)
        expect = "character_empire_rod-1"
        assert os.path.exists(os.path.join(out, expect + ".g")), \
            "the .g must keep the full unit base, not lose a character"
        assert not os.path.exists(os.path.join(out, "character_empire_rod.g")), \
            "a truncated name must not be written instead"
        scene = open(os.path.join(out, expect + ".scene"),
                     encoding="utf-8", errors="replace").read()
        assert scene.count("child particles") == 1, \
            "the source .scene must be reused, so particles survive"
        ac_txt = open(os.path.join(out, expect + ".ac"),
                      encoding="utf-8-sig", errors="replace").read()
        assert ac_txt.count("event2") == 6, "the source .ac must be reused"


def test_reverse_export_carries_the_alias_folder():
    """The reused `.ac` references `Aliases\\*.alias` by resource path, but
    nothing copied that folder, so exported units shipped with dangling
    event2 references (2065 across the 80 bundled `.ac` files that have any)."""
    from d3tool import cli
    src_dir = os.path.join(REPO, "Empire", "LivingArmor")
    want = sorted(os.listdir(os.path.join(src_dir, "Aliases")))
    assert len(want) == 9
    with tempfile.TemporaryDirectory() as td:
        gltf = _copy_unit("Empire/LivingArmor", td, ".gltf")
        out = os.path.join(td, "out")
        cli._export(gltf, out, 0, quiet=True)
        dst = os.path.join(out, "Aliases")
        assert os.path.isdir(dst), "the Aliases folder must be carried over"
        assert sorted(os.listdir(dst)) == want, \
            "alias file names must be preserved exactly"


def test_analyze_flags_an_unparsable_g_as_an_error():
    """`analyze` printed `(compound)` for *any* file whose parse fell back to
    the raw passthrough — including pure garbage — and exited 0.  `raw` is set
    by compound containers too, so it is not evidence of one; only `parts` is."""
    from d3tool import cli, gfile
    junk = b"\x01\x02\x03\x04" * 200
    m = gfile.parse_geometry_file(junk)
    assert m.parse_error, "a garbage .g must record why it did not parse"
    assert not m.parts, "and it must not look compound"
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, "garbage.g"), "wb").write(junk)
        shutil.copy(os.path.join(REPO, "Neutrals", "AirElemental",
                                 "character_neutrals_airelemental.g"),
                    os.path.join(td, "good.g"))
        assert cli._analyze_unit(td) == 1, "analyze must fail on corrupt input"


def test_bundle_continues_past_a_failing_unit():
    """One corrupt glTF used to abort the whole `_bundle` run: the units after
    it were never processed and no per-unit failure was reported."""
    from d3tool import cli
    with tempfile.TemporaryDirectory() as td:
        for unit in ("Neutrals/Wolf", "Neutrals/Troll"):
            _copy_unit(unit, td, ".gltf")
        names = sorted(f for f in os.listdir(td) if f.endswith(".gltf"))
        assert len(names) == 2
        bad = names[0]
        good = names[1][: -len(".gltf")]
        with open(os.path.join(td, bad), "w") as fh:
            fh.write('{"this is not": valid gltf')
        out = os.path.join(td, "out")
        assert cli._bundle(td, out, 0) == 1, \
            "a failing unit must still make the bundle fail"
        assert os.path.exists(os.path.join(out, good, good + ".g")), \
            f"{good} sorts after the broken unit and must still be processed"
        assert not os.path.exists(os.path.join(
            out, bad[: -len(".gltf")], bad[: -len(".gltf")] + ".g")), \
            "the broken unit must not emit a .g"


def test_validate_gltf_accepts_the_bundled_reference_files():
    """`validate_gltf` required morph-weight `output.count ==
    input.count * len(targets)`, but dis3tool writes `input.count ** 2` — and
    `_write_compound_gltf` replicates that for byte parity.  The validator
    therefore rejected 8 of the 98 bundled reference glTFs, i.e. ground truth.

    Since then two further reference quirks were identified as deliberate
    dis3tool output that the writers reproduce for parity, reported as
    warnings, not errors:

    * Rod-1      sampler 14 declares output=33 with 33 accessors (0..32)
    * WaterSnake 4 animation channels target nodes 47-50 with 47 nodes (x2
                 paths = 8 warnings)
    * Wildboar   1 animation channel targets node 37 with 37 nodes (x2 = 2)
    """
    from d3tool import gltf_out
    bad = []
    warned = {}
    for p in sorted(glob.glob(os.path.join(REPO, "*", "*", "*.gltf"))):
        errs, warns, _i = gltf_out.validate_gltf(p)
        if errs:
            bad.append((os.path.relpath(p, REPO), errs))
        if warns:
            warned[os.path.relpath(p, REPO)] = warns
    assert not bad, bad
    assert warned == {
        "Empire/Rod-1/character_empire_rod-1.gltf": 1,
        "Neutrals/WaterSnake/character_neutrals_watersnake.gltf": 8,
        "Neutrals/Wildboar/character_neutrals_wildboar.gltf": 2,
    }, warned


def test_validate_gltf_rejects_channels_targeting_missing_nodes():
    """Two bundled references animate nodes that do not exist, which the
    validator used to skip silently."""
    doc = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "n"}], "meshes": [], "accessors": [],
        "bufferViews": [], "buffers": [],
        "animations": [{"channels": [{"sampler": 0,
                                      "target": {"node": 3, "path": "rotation"}}],
                        "samplers": []}],
    }
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.gltf")
        open(p, "w").write(json.dumps(doc))
        assert gltf_out.validate_gltf(p)[0] >= 1, \
            "a channel aimed at a missing node must be an error"
        doc["nodes"].append({"name": "n1"})
        doc["nodes"].append({"name": "n2"})
        doc["nodes"].append({"name": "n3"})
        open(p, "w").write(json.dumps(doc))
        assert gltf_out.validate_gltf(p)[0] == 0, "node 3 must exist now"


def test_detect_anim_files_returns_every_ac_state():
    """`detect_anim_files` read all of the `.ac` states and then collapsed
    them to a hardcoded `{"Idle", "Run"}` pair, silently discarding the
    Attack/Damage/Death streams (Angel's `.ac` names five `.a` files)."""
    from d3tool import ac
    got = ac.detect_anim_files(os.path.join(REPO, "Empire", "Angel"),
                               "character_empire_angel")
    assert set(got) == {"Idle", "Attack", "Run", "Damage", "Death"}, got
    assert got["Idle"] == "character_empire_angel_idle.a"
    assert got["Attack"] == "character_empire_angel_attack.a"
    assert got["Death"] == "character_empire_angel_death.a"
    # a single-stream unit still resolves, and Idle/Run stay present
    one = ac.detect_anim_files(os.path.join(REPO, "Neutrals", "Wildboar"),
                               "character_neutrals_wildboar")
    assert "Idle" in one and "Run" in one


def test_forward_export_warns_when_the_animation_is_truncated():
    """dis3tool concatenates every `.a` the `.ac` references (Angel:
    64+84+28+32+55 = 263 frames, matching its reference glTF).  d3tool exports
    one stream, so the shortfall must be reported, not silently written."""
    import io
    import contextlib
    from d3tool import cli, anim as _anim
    total = sum(_anim.parse_anim(open(os.path.join(
        REPO, "Empire", "Angel", n), "rb").read()).frame_count
        for n in ("character_empire_angel_idle.a",
                  "character_empire_angel_attack.a",
                  "character_empire_angel_run.a",
                  "character_empire_angel_damage.a",
                  "character_empire_angel_death.a"))
    assert total == 263, "the bundled Angel streams must total 263 frames"
    a = _anim.parse_anim(open(os.path.join(
        REPO, "Empire", "Angel", "character_empire_angel_idle.a"), "rb").read())
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli._warn_if_animation_truncated(
            os.path.join(REPO, "Empire", "Angel", "character_empire_angel.g"),
            a, "character_empire_angel_idle.a")
    msg = buf.getvalue()
    assert "64 of 263" in msg, msg
    assert "5 animation files" in msg, msg


def test_import_refuses_an_unparsable_g():
    """`d3tool import` printed a zeroed JSON document with exit 0 for a file
    it could not parse at all, so a script could not tell a corrupt `.g` from
    a legitimately empty mesh.  Same rule as `analyze`."""
    import subprocess
    junk = b"\x01\x02\x03\x04" * 200
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "garbage.g")
        open(p, "wb").write(junk)
        r = subprocess.run([sys.executable, "-m", "d3tool", "dump", p],
                           capture_output=True, text=True, cwd=REPO,
                           check=False)
        assert r.returncode == 1, r.stdout
        assert "unparsed" in r.stdout + r.stderr
        good = os.path.join(REPO, "Neutrals", "Wildboar",
                            "character_neutrals_wildboar.g")
        r2 = subprocess.run([sys.executable, "-m", "d3tool", "dump", good],
                            capture_output=True, text=True, cwd=REPO,
                            check=False)
        assert r2.returncode == 0, r2.stderr
        assert json.loads(r2.stdout)["vertex_count"] > 0


def test_validate_gltf_checks_bufferview_fits_the_buffer():
    """The validator compared each accessor against its bufferView but never
    checked the view against the buffer, so a writer that accumulates
    byteOffsets by hand could point a view past the end of the .bin."""
    doc = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "n", "mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"bufferView": 0, "byteOffset": 0, "componentType": 5126,
                       "count": 3, "type": "VEC3"}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 1000}],
        "buffers": [{"uri": "b.bin", "byteLength": 1000}],
    }
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.gltf")
        b = os.path.join(td, "b.bin")
        open(b, "wb").write(b"\0" * 1000)

        def check():
            open(p, "w").write(json.dumps(doc))
            return gltf_out.validate_gltf(p)[0]

        assert check() == 0, "a consistent document must pass"
        doc["bufferViews"][0]["byteOffset"] = 500
        assert check() == 1, "a view running past the buffer must be an error"
        doc["buffers"][0]["byteLength"] = 2000
        open(b, "wb").write(b"\0" * 2000)
        assert check() == 0, "a genuinely larger buffer must pass again"


def _load_a(unit: str, name: str):
    return animmod.parse_anim(open(os.path.join(REPO, *unit.split("/"), name),
                                   "rb").read())


def test_concat_anims_joins_every_ac_referenced_stream():
    """dis3tool exports a unit whose `.ac` names several `.a` as ONE animation
    spanning all of them.  Verified on all 24 such bundled units: the reference
    frame count equals the sum (Angel 64+84+28+32+55 = 263)."""
    streams = [_load_a("Empire/Angel", f"character_empire_angel_{k}.a")
               for k in ("idle", "attack", "run", "damage", "death")]
    assert [a.frame_count for a in streams] == [64, 84, 28, 32, 55]
    cat = animmod.concat_anims(streams)
    assert cat.frame_count == 263
    assert len(cat.bones) == len(streams[0].bones)
    # every bone must be stretched to the total, or the writer would emit
    # ragged rotation/translation arrays
    assert set(len(b.frames) for b in cat.bones) == {263}
    # a lone stream must pass through untouched
    assert animmod.concat_anims([streams[0]]) is streams[0]
    assert animmod.concat_anims([]).frame_count == 0


def test_concat_anims_takes_morph_tracks_from_the_last_stream():
    """Cleric's reference has 25 morph targets — from `_run.a` (25 frames),
    not the 356 in `_iadd.a`.  Target #0 matches `_run.a` frame 0 bit-for-bit.
    Holds on all 8 morph-bearing bundled units."""
    cat = animmod.concat_anims([
        _load_a("Empire/Cleric", "character_empire_cleric_iadd.a"),
        _load_a("Empire/Cleric", "character_empire_cleric_run.a"),
    ])
    assert cat.frame_count == 381
    assert [t.frame_count for t in cat.morphs] == [25]
    assert cat.morphs[0].vertex_count == 264


def test_morph_weights_matrix_is_sized_by_total_frames():
    """The reference morph_weights bufferView for Cleric is 580644 bytes =
    381*381*4, and all 145161 cells match a 381x381 identity exactly, while the
    mesh carries only 25 morph targets."""
    from d3tool import cli
    with tempfile.TemporaryDirectory() as td:
        g = os.path.join(REPO, "Empire", "Cleric", "character_empire_cleric.g")
        out = os.path.join(td, "cleric.gltf")
        cli._export_gl(g, cli._find_animation_for_geometry(g), out,
                       texture=None, quiet=True)
        doc = json.load(open(out))
        acc = doc["accessors"]
        anim = doc["animations"][0]
        po = {c["sampler"]: c["target"]["path"] for c in anim["channels"]}
        wa = [acc[s["output"]] for i, s in enumerate(anim["samplers"])
              if po.get(i) == "weights"]
        assert wa, "the export must carry a weights sampler"
        assert wa[0]["count"] == 381 * 381, wa[0]["count"]
        n_tg = [len(m["primitives"][0].get("targets", []))
                for m in doc["meshes"]]
        assert 25 in n_tg, n_tg
        # and the bytes really are an identity matrix of that size
        buf = open(out[:-5] + ".bin", "rb").read()
        bv = doc["bufferViews"][wa[0]["bufferView"]]
        off = bv.get("byteOffset", 0) + wa[0].get("byteOffset", 0)
        vals = struct.unpack_from(f"<{381 * 381}f", buf, off)
        assert all(abs(vals[r * 381 + c] - (1.0 if r == c else 0.0)) < 1e-6
                   for r in range(0, 381, 37) for c in range(0, 381, 37))


def test_concat_anims_marks_the_primary_skeleton():
    """concat_anims records how many bones the *first* stream contributed.
    That boundary is what separates bones the reference animates from bones
    it only emits as nodes (AirElemental's LeftLeftHand/Tail02 come from the
    second, `_run.a`, stream)."""
    from d3tool import anim
    def bone(n, p=""):
        return anim.BoneAnim(n, p, 2)
    a = anim.AnimFile(); a.bones = [bone("Root"), bone("A", "Root")]
    b = anim.AnimFile()
    b.bones = [bone("Root"), bone("A", "Root"), bone("Late", "A")]
    j = anim.concat_anims([a, b])
    assert j.n_primary == 2, j.n_primary
    assert [x.name for x in j.bones] == ["Root", "A", "Late"]


def test_node_hierarchy_is_a_dfs_over_the_primary_skeleton():
    """The reference node order is a depth-first walk of the skeleton with
    children in .a record order. Verified against all 83 bundled reference
    exports: DFS matches 79, and the 4 misses are the two units with a
    later-stream bone, rigid Blacknaga, and duplicate-name WaterSnake."""
    from d3tool import gltf_out
    def b(n, p=""):
        x = anim_bone_stub(n, p)
        return x
    bones = [b("Root"), b("A", "Root"), b("A1", "A"), b("B", "Root")]
    children, parent, order, roots = gltf_out.node_hierarchy(bones, len(bones))
    assert order == ["Root", "A", "A1", "B"], order
    assert roots == ["Root"]
    assert children["Root"] == ["A", "B"]


def test_node_hierarchy_trails_bones_only_a_later_stream_carries():
    """Such a bone keeps its parent edge but is emitted as a trailing node.
    The AirElemental reference lists LeftLeftHand under LeftForeArm
    (children [7, 43]) yet places it at node 43 of 45, after the whole DFS
    tree, and targets it with no animation channel."""
    from d3tool import gltf_out
    primary = [anim_bone_stub("Root"), anim_bone_stub("Fore", "Root"),
               anim_bone_stub("Hand", "Fore")]
    late = anim_bone_stub("LateHand", "Fore")
    bones = primary + [late]
    children, parent, order, roots = gltf_out.node_hierarchy(bones, 3)
    assert order == ["Root", "Fore", "Hand", "LateHand"], order
    assert children["Fore"] == ["Hand", "LateHand"], children["Fore"]


def _export_like_the_harness(rel_g):
    """Forward-export a corpus unit the way tests/corpus_parity.run does."""
    g_path = os.path.join(REPO, rel_g)
    folder = os.path.dirname(g_path)
    stem = os.path.splitext(os.path.basename(g_path))[0]
    mesh = gfile.parse_geometry_file(open(g_path, "rb").read())
    a_path = climod._find_animation_for_geometry(g_path)
    an = (climod._load_anim_stream(g_path, a_path, True)
          if a_path else None)
    texture = None
    if not mesh.parts and mesh.material_diffuse:
        texture = os.path.splitext(mesh.material_diffuse)[0] + ".dds"
    binb, doc = gltf_out.write_gltf(mesh, an, stem, texture=texture)
    ref = json.load(open(os.path.join(folder, stem + ".gltf")))
    refbin = open(os.path.join(folder, stem + ".bin"), "rb").read()
    return binb, doc, ref, refbin


def test_rigid_export_when_the_animation_is_unresolvable():
    """Blacknaga's .ac points at mermaid's .a, which is not in its folder.

    The dis3tool reference ships the unit rigid: one mesh node, no skin, a
    primitive without WEIGHTS_0/JOINTS_0 and accessors stopping after
    TEXCOORD_0 — while the buffer keeps the skinned stride-52 vertex block
    and the mesh_bones IBM block, unreferenced but present.
    """
    binb, doc, ref, refbin = _export_like_the_harness(
        os.path.join("Neutrals", "Blacknaga",
                     "character_neutrals_blacknaga.g"))
    assert [n.get("name") for n in doc["nodes"]] == ref["nodes"][0].get(
        "name") or doc["nodes"] == ref["nodes"]
    assert len(doc["nodes"]) == 1 and "skin" not in doc["nodes"][0]
    assert "skins" not in doc and "animations" not in doc
    prim = doc["meshes"][0]["primitives"][0]
    assert set(prim["attributes"]) == {"POSITION", "NORMAL", "TEXCOORD_0"}
    assert len(doc["accessors"]) == 4 and len(ref["accessors"]) == 4
    bv_names = [bv["name"] for bv in doc["bufferViews"]]
    assert bv_names == [bv["name"] for bv in ref["bufferViews"]]
    # the bin keeps the full skinned layout — byte length parity included
    assert len(binb) == len(refbin)
    assert doc["bufferViews"][2]["byteLength"] == \
        ref["bufferViews"][2]["byteLength"] > 0


def test_find_animation_returns_none_when_the_ac_points_outside_the_folder():
    """dis3tool loads only what the unit's own .ac names; a stream missing
    from the unit folder means a rigid export, not a conventional-name
    guess (Blacknaga points at mermaid, watersnake_sea at a .a bundled
    with neither)."""
    def find(rel):
        return climod._find_animation_for_geometry(
            os.path.join(REPO, rel))
    assert find(os.path.join("Neutrals", "Blacknaga",
                             "character_neutrals_blacknaga.g")) is None
    assert find(os.path.join("Neutrals", "WaterSnake",
                             "character_neutrals_watersnake_sea.g")) is None
    assert find(os.path.join("Neutrals", "WaterSnake",
                             "character_neutrals_watersnake.g")) is not None
    assert find(os.path.join("Neutrals", "Wolf",
                             "character_neutrals_wolf.g")) is not None


def test_compound_writer_animates_only_the_primary_skeleton():
    """DarkServant's .ac concatenates `_iadd.a` and `_run.a`; Bone02 exists
    only in the latter.  The reference gives it a trailing node but no
    channel and no rot/tra storage: 69 of 70 bones are animated, and the
    morph_weights matrix stays sized by the *total* frame count."""
    binb, doc, ref, refbin = _export_like_the_harness(
        os.path.join("Neutrals", "DarkServant",
                     "character_neutrals_darkservant.g"))
    assert len(doc["accessors"]) == len(ref["accessors"]) == 204
    ra, ga = ref["animations"][0], doc["animations"][0]
    assert len(ga["channels"]) == len(ra["channels"]) == 140
    assert len(ga["samplers"]) == len(ra["samplers"]) == 140
    # bones_rotate / bones_translate cover 69 bones x 152 frames
    bv_rot = [bv for bv in doc["bufferViews"]
              if bv["name"] == "bones_rotate"][0]
    ref_rot = [bv for bv in ref["bufferViews"]
               if bv["name"] == "bones_rotate"][0]
    assert bv_rot["byteLength"] == ref_rot["byteLength"] == 69 * 152 * 16
    assert len(binb) == len(refbin)
    # and the weights channels close the animation, as in the reference
    assert [c["target"]["path"] for c in ga["channels"][-2:]] == \
        ["weights", "weights"]
    assert [c["target"]["path"] for c in ra["channels"][-2:]] == \
        ["weights", "weights"]


def test_duplicate_bone_channels_are_counted_positionally():
    """WaterSnake lists `null` five times: nodes exist for the first only,
    yet all 50 bones get channels, targets counted as node-slot + position —
    so the last four pairs dangle past the 47-node list and every unique
    bone after a duplicate aims one slot high.  The same rule puts
    Wildboar's last pair on node 37 of 37."""
    for rel in (os.path.join("Neutrals", "WaterSnake",
                             "character_neutrals_watersnake.g"),
                os.path.join("Neutrals", "Wildboar",
                             "character_neutrals_wildboar.g")):
        _binb, doc, ref, _refbin = _export_like_the_harness(rel)
        got = [c["target"]["node"]
               for c in doc["animations"][0]["channels"]]
        want = [c["target"]["node"]
                for c in ref["animations"][0]["channels"]]
        assert got == want, rel
        assert len(got) == len(want)


def test_attrless_part_keeps_real_positions_and_a_stray_sampler():
    """Rod-1's sword part carries no .g attributes at all.  The reference
    exports it at the morph-static stride but with real positions, reports
    zeroed POSITION min/max, and appends one stray sampler aimed at the
    accessor index just past the end that no channel references."""
    binb, doc, ref, refbin = _export_like_the_harness(
        os.path.join("Empire", "Rod-1", "character_empire_rod-1.g"))
    # ...the stray sampler: 15 samplers, 14 channels, output dangling
    ga = doc["animations"][0]
    assert len(ga["samplers"]) == 15 and len(ga["channels"]) == 14
    stray = ga["samplers"][-1]
    assert stray["output"] == len(doc["accessors"]) == 33
    assert stray["output"] not in [c["sampler"] for c in ga["channels"]]
    assert ref["animations"][0]["samplers"][-1] == stray
    # ...the sword vertex block carries the reference's real positions
    bv = [b for b in doc["bufferViews"]
          if b["name"] == "mesh_vertexes_empire_rod-1_sword"][0]
    off = bv["byteOffset"]
    assert binb[off:off + bv["byteLength"]] == \
        refbin[off:off + bv["byteLength"]]
    # ...yet the POSITION accessor keeps the zeroed min/max quirk
    sword_mesh = doc["meshes"][[m["name"] for m in doc["meshes"]]
                               .index("empire_rod-1_sword")]
    pos = doc["accessors"][sword_mesh["primitives"][0]["attributes"]
                           ["POSITION"]]
    assert pos["min"] == [0.0, 0.0, 0.0] and pos["max"] == [0.0, 0.0, 0.0]


def test_scene_lists_every_parentless_skeleton_node():
    """Scene nodes = sub-meshes + every node whose parent is not a bone, in
    node order.  DarkServant's ROOT_demons_thief_lod and Bone02 (parent
    `Scene Root`) therefore trail the skeleton root in its reference."""
    _binb, doc, ref, _refbin = _export_like_the_harness(
        os.path.join("Neutrals", "DarkServant",
                     "character_neutrals_darkservant.g"))
    assert doc["scenes"] == ref["scenes"]
    assert doc["scenes"][0]["nodes"] == [0, 1, 2, 3, 4, 72, 73]
    # the trailing nodes are exactly those two bones
    names = [doc["nodes"][i]["name"] for i in (72, 73)]
    assert names == ["ROOT_demons_thief_lod", "Bone02"]


def _corpus_files(ext):
    """Every Empire//Neutrals file with ``ext``, sorted (recursive)."""
    return (sorted(glob.glob(os.path.join(REPO, "Empire", "**", "*" + ext),
                              recursive=True))
            + sorted(glob.glob(os.path.join(REPO, "Neutrals", "**", "*" + ext),
                               recursive=True)))


def test_scene_parser_roundtrips_every_shipped_scene():
    """parse_scene / render_scene must be lossless on all 245 shipped
    `.scene` files -- Latin-1 bytes, mixed LF/CRLF line endings, keyless
    mesh lines and column-0 child headers included."""
    from d3tool import scene as scenemod
    files = _corpus_files(".scene")
    assert len(files) >= 244, len(files)
    kinds = {}
    for p in files:
        raw = open(p, "rb").read()
        doc = scenemod.parse_scene(raw.decode("latin-1"))
        assert scenemod.render_scene(doc).encode("latin-1") == raw, p
        for node in doc.root.walk():
            kinds[node.kind] = kinds.get(node.kind, 0) + 1
    # the shapes that actually occur in the corpus
    for kind in ("group", "bones", "gobj", "goclass", "particles"):
        assert kinds.get(kind), f"no {kind} nodes found in corpus scenes"


def test_scene_parser_extracts_air_elemental_structure():
    """The AirElemental scene: one bones child under Scene Root, nine
    particle emitters, the .ac referenced by the bones node."""
    from d3tool import scene as scenemod
    p = os.path.join(REPO, "Neutrals", "AirElemental",
                     "character_neutrals_airelemental.scene")
    doc = scenemod.parse_scene(open(p, encoding="latin-1").read())
    assert doc.settings.props["fov"] == "1.100000"
    assert doc.root.name == "Scene Root"
    bones = doc.find_all("bones")
    assert len(bones) == 1
    assert [f for f in bones[0].files()
            if f.endswith(".ac")] == [
        "resources\\characters\\neutrals\\airelemental\\"
        "character_neutrals_airelemental.ac"]
    assert len(doc.find_all("particles")) == 9
    assert scenemod.count_particles(doc) == 9


def test_scene_parser_rejects_broken_structure():
    from d3tool import scene as scenemod
    for text in ('group "A" \n\tfov 1;\n',          # no opening brace
                 'group "A" \n{\n\tfov 1;\n',        # never closed
                 'group "A" \n}\n',                    # close without open
                 'group "A" \n{\n}\n}\n'):           # extra close
        try:
            scenemod.parse_scene(text)
        except ValueError:
            continue
        raise AssertionError(f"parser accepted broken scene {text!r}")


def test_alias_parser_roundtrips_every_shipped_alias():
    """All 1300 `.alias` files re-emit byte-for-byte -- including the
    CP1251-encoded Craken file and the 87 empty (muted) blocks."""
    from d3tool import alias as aliasmod
    files = _corpus_files(".alias")
    assert len(files) == 1294, len(files)
    n_empty = 0
    encs = {}
    for p in files:
        raw = open(p, "rb").read()
        doc = aliasmod.parse_alias_bytes(raw)
        assert aliasmod.write_alias_bytes(doc) == raw, p
        encs[doc.encoding] = encs.get(doc.encoding, 0) + 1
        if not doc.sounds:
            n_empty += 1
    assert encs == {"utf-8": 1293, "cp1251": 1}, encs
    assert n_empty == 87, n_empty


def test_alias_parser_reads_entries_macros_and_cp1251():
    from d3tool import alias as aliasmod
    p = os.path.join(REPO, "Empire", "Acolyte", "Aliases", "attack00.alias")
    doc = aliasmod.parse_alias_bytes(open(p, "rb").read())
    assert doc.name == "Attack00"
    assert doc.sounds[0].use == 100 and doc.sounds[0].play == 100
    assert doc.sounds[0].flags == 3
    assert doc.sounds[0].path.startswith("$(Sounds)\\")
    # attack00.alias is one of the 236 files shipped without the comment
    # header; a headered sibling documents the format
    assert doc.preamble == ""
    doc2 = aliasmod.parse_alias_bytes(
        open(os.path.join(REPO, "Empire", "Acolyte", "Aliases",
                          "cloth.alias"), "rb").read())
    assert doc2.preamble.startswith("// alias configuration file")
    # Craken's Cyrillic-named CP1251 file round-trips through the same API
    import glob as _glob
    cyr = [q for q in _glob.glob(os.path.join(REPO, "Neutrals", "Craken",
                                              "Aliases", "*.alias"))
           if "ттт" in q]
    assert cyr
    raw = open(cyr[0], "rb").read()
    doc = aliasmod.parse_alias_bytes(raw)
    assert doc.encoding == "cp1251"
    assert aliasmod.write_alias_bytes(doc) == raw


def test_alias_parser_rejects_garbage():
    from d3tool import alias as aliasmod
    for text in ("", "sound 100, \"x.wav\", 1, 1;\n",
                 'alias "A" {\n\tsound 100, "x";\n}\n',
                 'alias "A" {\n'):
        try:
            aliasmod.parse_alias(text)
        except ValueError:
            continue
        raise AssertionError(f"parser accepted broken alias {text!r}")


def test_confirm_prompts_on_a_tty_even_without_colour():
    """`confirm` used to treat colour support as interactivity: under
    NO_COLOR it silently took the destructive default.  The prompt must
    appear whenever stdin is a TTY, coloured or not."""
    import io as _io
    from d3tool import ui

    class _FakeTTY:
        def __init__(self, data):
            self.buffer = _io.StringIO(data)

        def isatty(self):
            return True

        def readline(self):
            return self.buffer.readline()

    answers = {"y\n": True, "n\n": False, "\n": True}
    old_stdin = sys.stdin
    old_nocolor = os.environ.get("NO_COLOR")
    os.environ["NO_COLOR"] = "1"   # colour must not gate the prompt
    try:
        for data, expected in answers.items():
            sys.stdin = _FakeTTY(data)
            assert ui.confirm("ok?", default=True) is expected, data
            sys.stdin = _FakeTTY(data)
            assert ui.confirm("ok?", default=False) is (
                expected if data != "\n" else False), data
    finally:
        sys.stdin = old_stdin
        if old_nocolor is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = old_nocolor


def test_confirm_defaults_without_reading_on_piped_stdin():
    import io as _io
    from d3tool import ui
    # a non-TTY stdin (like StringIO with no isatty override) must return
    # the default WITHOUT consuming input -- batch scripts never hang
    sentinel = _io.StringIO("n\n")
    assert ui.confirm("ok?", default=True) is True
    assert sentinel.getvalue() == "n\n"  # untouched


def test_pack_weights_joints_matches_reference_counterexamples():
    """The exact dis3tool WEIGHTS_0/JOINTS_0 packing, pinned on the vertices
    that drove the derivation (byte-verified on all 292 569 skinned corpus
    vertices, 0 mismatches):

    * stored lanes stay **verbatim** — the complement of a float32-sum that
      already reaches 1.0 is noise and must not be re-rounded (Acolyte
      v35: 1-w0 rounds to a tie whose float32-nearest differs from the
      stored lane the reference keeps);
    * a positive complement merges **into the duplicate-bone lane** when
      the implied bone repeats (ImperialKnight cloak v85: bones (4,5,4),
      0.125+0.125 = 0.25 in lane 0), else it is appended (v18-class);
    * the complement is computed from the **double-precision** sum, not a
      float32 running sum (0.903+0.097 classes);
    * JOINTS_0 masks a lane only when its weight is **exactly** 0.0 — a
      2.98e-08 residue keeps its joint (the old 1e-4 threshold broke it).
    """
    from d3tool import model
    f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]  # noqa: E731

    # verbatim: total misses 1.0 by 2e-10, ref keeps both stored lanes
    w, j = model.pack_weights_joints(
        (0.2318541705608368, 0.7681458592414856), (7, 0, 0))
    assert w == (0.2318541705608368, 0.7681458592414856, 0.0, 0.0)
    assert j == (7, 0, 0, 0)

    # duplicate implied bone: complement merges into lane 0, joint stays 4
    w, j = model.pack_weights_joints((0.125, 0.75), (4, 5, 4))
    assert w == (0.25, 0.75, 0.0, 0.0)
    assert j == (4, 5, 0, 0)

    # joint-0 merge: the complement nudges lane 0 up one float32 step
    w, j = model.pack_weights_joints(
        (0.42888399958610535, 0.5711159706115723), (0, 1, 0))
    assert w == (0.42888402938842773, 0.5711159706115723, 0.0, 0.0)
    assert j == (0, 1, 0, 0)

    # appended complement: a 2.98e-08 residue keeps its (non-zero) joint
    w, j = model.pack_weights_joints(
        (0.7205365896224976, 0.27946338057518005), (1, 2, 3))
    assert w[2] == f32(1.0 - (0.7205365896224976 + 0.27946338057518005))
    assert 0.0 < w[2] < 1e-7
    assert j == (1, 2, 3, 0), "the residue lane must keep joint 3"

    # exact-zero stored lane: the joint is masked, the weight stays 0.0
    w, j = model.pack_weights_joints((0.0, 0.5, 0.5), (3, 8, 1, 0))
    assert w == (0.0, 0.5, 0.5, 0.0)
    assert j == (0, 8, 1, 0)

    # negative complement (stored lanes overshoot 1.0 by rounding): verbatim
    # f32(0.2)+f32(0.8) = 1.0000000149 -> c < 0 changes nothing
    w, j = model.pack_weights_joints((0.2, 0.8), (2, 5))
    assert w == (f32(0.2), f32(0.8), 0.0, 0.0)
    assert j == (2, 5, 0, 0)

    # no stored lanes: rigid vertex, full weight on its bone (or joint 0)
    assert model.pack_weights_joints((), ()) == \
        ((1.0, 0.0, 0.0, 0.0), (0, 0, 0, 0))
    assert model.pack_weights_joints((), (6,)) == \
        ((1.0, 0.0, 0.0, 0.0), (6, 0, 0, 0))


def test_concat_anims_fills_primary_slots_by_record_index():
    """Frame filling across concatenated streams is **positional**: the C++
    exporter walks the per-stream record arrays in parallel, so a stream
    record lands on the slot of the same index even when its name drifted
    (AirElemental `run.a` record 6 `LeftLeftHand` -> the `LeftHand` slot;
    DarkServant `run.a` record 68 `Bone02` -> `ROOT_demons_thief_lod`).
    Bones with brand-new names are appended after the primary skeleton."""
    from d3tool import anim

    def stream(bones, frame_count):
        a = anim.AnimFile()
        a.frame_count = frame_count
        a.bones = []
        for name, frames in bones:
            b = anim.BoneAnim(name, "", frame_count)
            b.frames = list(frames)
            a.bones.append(b)
        return a

    s1 = stream([("LeftHand", [(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)] * 2),
                 ("RightTail02", [(0.1,) * 7] * 2)], 2)
    s2 = stream([("LeftLeftHand", [(9.0,) * 7] * 3),
                 ("RightTail02", [(0.5,) * 7] * 3)], 3)
    cat = animmod.concat_anims([s1, s2])
    assert cat.frame_count == 5
    assert cat.n_primary == 2
    assert [b.name for b in cat.bones] == ["LeftHand", "RightTail02",
                                           "LeftLeftHand"]
    # slot 0 takes stream2's *record 0* frames for the second stretch,
    # despite the name drift
    assert cat.bones[0].frames[:2] == s1.bones[0].frames
    assert cat.bones[0].frames[2:] == s2.bones[0].frames
    # matching name and index agree
    assert cat.bones[1].frames[2:] == s2.bones[1].frames
    # the appended new-name bone keeps its own samples
    assert cat.bones[2].frames[2:] == s2.bones[0].frames


def test_forward_export_bytes_match_references_on_red_zone_units():
    """Byte parity against the bundled dis3tool references for the units that
    drove the last two rules: Werewolf (float32 weight packing),
    AirElemental and DarkServant (cross-stream record-index concat).
    tests/corpus_parity.py extends this to all 85 reference units."""
    import corpus_parity  # tests/ is on sys.path under pytest
    for rel in (os.path.join("Neutrals", "Werewolf",
                             "character_neutrals_werewolf.g"),
                os.path.join("Neutrals", "AirElemental",
                             "character_neutrals_airelemental.g"),
                os.path.join("Neutrals", "DarkServant",
                             "character_neutrals_darkservant.g")):
        binb, doc, ref, refbin = _export_like_the_harness(rel)
        unit = os.path.basename(rel)
        assert binb == refbin, f"{unit}: .bin must be byte-identical"
        diffs = corpus_parity._json_diffs(ref, doc)
        assert not diffs, f"{unit}: {diffs[:3]}"


def anim_bone_stub(name, parent=""):
    class _B:
        pass
    x = _B()
    x.name = name
    x.parent = parent
    return x



def test_every_corpus_submesh_hangs_off_one_parent_node():
    """The invariant the Angel's sword broke: one parent id per file.

    `dwParent` names the node a part hangs off.  All 376 sub-meshes of the
    retail corpus (141 compound files) share a single parent id per file; a
    part written with a placeholder parent names no node at all.
    """
    files = checked = 0
    for root in ("Empire", "Neutrals"):
        for path in sorted(glob.glob(os.path.join(REPO, root, "*", "*.g"))):
            mesh = gfile.parse_geometry_file(open(path, "rb").read())
            if mesh.parse_error:
                continue
            parts = gfile.iter_parts(mesh)
            if len(parts) < 2:
                continue
            files += 1
            parents = {p.attrs.get("dwParent") for p in parts}
            assert len(parents) == 1, (path, parents)
            nodes = [p.attrs.get("dwNode") for p in parts]
            assert len(set(nodes)) == len(nodes), (path, nodes)
            checked += len(parts)
    assert files > 100 and checked > 300, (files, checked)


def test_added_part_inherits_the_files_parent_node():
    """A part the export synthesized joins the file's node tree."""
    body = gfile.parse_geometry_file(open(os.path.join(
        REPO, "Empire", "Angel", "character_empire_angel.g"), "rb").read())
    parent = gfile.iter_parts(body)[0].attrs["dwParent"]
    added = copy.deepcopy(gfile.iter_parts(body)[-1])
    added.name = "empire_knight_sword"
    added.attr_items = [
        (k, "55867360" if k == "dwParent" else
         "empire_knight_sword" if k == "name" else v)
        for k, v in added.attr_items]
    added.attrs = dict(added.attr_items)
    body.parts.append(added)
    changes = gfile.normalize_part_identity(body, parent)
    assert changes == [f"empire_knight_sword: dwParent 55867360 -> {parent}"], changes
    assert added.attrs["dwParent"] == parent
    assert added.raw == b"", "a rewritten part must not keep its donor bytes"
    # a file whose parts already agree is left alone
    again = gfile.parse_geometry_file(open(os.path.join(
        REPO, "Empire", "Angel", "character_empire_angel.g"), "rb").read())
    assert gfile.normalize_part_identity(again, parent) == []


def test_no_corpus_part_declares_more_slots_than_bones():
    """The invariant the Angel's sword broke: a rigid part has no slots.

    A part stores one weight lane per bone it references.  The corpus never
    declares more influence slots than it has bones, and a single-bone part
    declares none at all (`weights_on_vertex 1`, the plain 40-byte record) —
    that is the shape of every retail weapon.  The export wrote the sword as
    `weights_on_vertex 2` with `bones_num 1`, a combination that occurs zero
    times in 376 sub-meshes, and the engine drew it without skinning it: the
    sword hung in mid-air at its stored coordinates.
    """
    single = checked = 0
    for root in ("Empire", "Neutrals"):
        for path in sorted(glob.glob(os.path.join(REPO, root, "*", "*.g"))):
            mesh = gfile.parse_geometry_file(open(path, "rb").read())
            if mesh.parse_error:
                continue
            for part in gfile.iter_parts(mesh):
                if part.morph:
                    continue
                w = part.weights_on_vertex
                bones = len(part.bones)
                checked += 1
                assert w <= max(bones, 1), (path, part.name, w, bones)
                if bones == 1:
                    assert w == 1, (path, part.name, w)
                    single += 1
    assert checked > 300 and single > 100, (checked, single)


def test_a_single_bone_part_is_written_without_influence_slots():
    """`weights_on_vertex 2` with one bone is rewritten to the rigid record."""
    from d3tool.model import Bone, MeshPart, Vertex
    v = Vertex((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0),
               0xFFFFFFFF, (1.0,), (0, 0))
    eye = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
           0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    bone = Bone("Bip01 R Hand", eye)
    part = MeshPart(
        name="empire_knight_sword", vertex_count=1, tri_count=1,
        vertices=[v, v, v], indices=[0, 1, 2], bones=[bone],
        weights_on_vertex=2,
        attr_items=[("dwNode", "1"), ("dwParent", "2"), ("name",
                    "empire_knight_sword"), ("groupname", "Scene Root"),
                    ("materials_num", "1"),
                    ("material0_diffuse", "x.tga"),
                    ("material0_triangles_num", "1"),
                    ("new_vertex_weights_format", "1"),
                    ("vertexs_weights_num", "3"),
                    ("weights_on_vertex", "2"), ("bones_num", "1")],
        attrs=None, vertex_magic=b"\x0b\x6e\xd5\x1c",
    )
    part.attrs = dict(part.attr_items)
    block = gfile._part_block(part)
    parsed = (gfile._parse_compound_parts(block)[0] or [None])[0]
    assert parsed is not None, block[:64]
    assert parsed.attrs["weights_on_vertex"] == "1", parsed.attrs
    assert parsed.weights_on_vertex == 1
    assert len(parsed.bones) == 1 and parsed.bones[0].name == "Bip01 R Hand"
    # the vertex stride is the rigid one: the data is 3 * 40 bytes behind the
    # attribute block, nothing else
    assert gfile.vertex_stride(1)[2] == 40


def test_lod_sync_appends_the_weapon_as_a_lod_part():
    """`d3tool lod` gives the LOD mesh a block shaped for its own file.

    The engine swaps to `unit_scene_lod` beyond the profile's draw distance,
    so a weapon added to the main `.g` has to exist in the LOD mesh too.  The
    block cannot be copied verbatim: every LOD block is spelled
    ``lod_<name>`` and hangs off *that file's* parent node — the ids are per
    file (the Angel's main mesh uses 53704704, its LOD mesh 55867360), and a
    part whose parent names no node is not part of the model's tree.
    """
    import shutil
    import tempfile
    from d3tool import cli as climod
    from d3tool import gfile as gmod

    angel = os.path.join(REPO, "Empire", "Angel", "character_empire_angel")
    knight = gmod.parse_geometry_file(open(os.path.join(
        REPO, "Empire", "Knight", "character_empire_knight.g"), "rb").read())
    sword = gmod.iter_parts(knight)[1]          # a rigid single-bone weapon
    assert sword.attrs["name"] == "empire_knight_shield"
    assert len(sword.bones) == 1 and sword.weights_on_vertex == 1
    assert sword.raw, "a compound part keeps its own bytes"

    with tempfile.TemporaryDirectory() as d:
        for ext in (".g", ".scene", "_lod.g", "_lod.scene"):
            shutil.copyfile(angel + ext,
                            os.path.join(d, "character_empire_angel" + ext))
        main_g = os.path.join(d, "character_empire_angel.g")
        grown = gmod.append_parts(open(main_g, "rb").read(), [sword])
        open(main_g, "wb").write(grown)

        lod_g = os.path.join(d, "character_empire_angel_lod.g")
        before = open(lod_g, "rb").read()
        before_parts = gmod.iter_parts(gmod.parse_geometry_file(before))
        parent = before_parts[0].attrs["dwParent"]

        assert climod._lod_sync(d, quiet=True) == 0
        after = open(lod_g, "rb").read()
        after_parts = gmod.iter_parts(gmod.parse_geometry_file(after))
        assert len(after_parts) == len(before_parts) + 1
        added = after_parts[-1]
        assert added.attrs["name"] == "lod_" + sword.attrs["name"]
        assert added.attrs["dwParent"] == parent, added.attrs["dwParent"]
        assert added.weights_on_vertex == 1 and len(added.bones) == 1
        assert gmod.mesh_count(after)[1] == len(after_parts)
        # nothing but the container count moves in the shipped bytes
        at, old = bytearray(after), bytearray(before)
        off = gmod.mesh_count(before)[0]
        at[off:off + 4] = old[off:off + 4] = b"\0\0\0\0"
        assert bytes(at[:len(old)]) == bytes(old)

        # a `.g` and a `.scene` agree block for block
        from d3tool import scene as scenemod
        text = open(os.path.join(d, "character_empire_angel_lod.scene"),
                    encoding="latin-1", newline="").read()
        gobjs = scenemod.parse_scene(text).find_all("gobj")
        assert len(gobjs) == len(after_parts)
        assert scenemod.node_mesh_index(gobjs[-1]) == len(after_parts) - 1

        # idempotent: the match ignores the `lod_` prefix
        assert climod._lod_sync(d, quiet=True) == 0
        assert open(lod_g, "rb").read() == after


def test_battle_check_reports_a_lod_that_lost_the_added_part():
    """A weapon the LOD mesh does not carry is invisible past the draw
    distance — the export says so instead of leaving it to the battlefield."""
    import shutil
    import tempfile
    from d3tool import cli as climod
    from d3tool import gfile as gmod

    angel = os.path.join(REPO, "Empire", "Angel", "character_empire_angel")
    knight = gmod.parse_geometry_file(open(os.path.join(
        REPO, "Empire", "Knight", "character_empire_knight.g"), "rb").read())
    sword = gmod.iter_parts(knight)[1]          # a rigid single-bone weapon
    with tempfile.TemporaryDirectory() as d:
        for ext in (".g", "_lod.g"):
            shutil.copyfile(angel + ext,
                            os.path.join(d, "character_empire_angel" + ext))
        main_g = os.path.join(d, "character_empire_angel.g")
        grown = gmod.append_parts(open(main_g, "rb").read(), [sword])
        open(main_g, "wb").write(grown)
        seen = []
        real = climod.ui.warn
        climod.ui.warn = lambda msg="": seen.append(str(msg))
        try:
            climod._battle_readiness_check(d, "character_empire_angel", False)
        finally:
            climod.ui.warn = real
        text = "\n".join(seen)
        assert "_lod.g has 4 mesh part(s)" in text, text


def test_battle_check_catches_a_rigid_export_and_a_lost_armature():
    """The Inquisitor once came back from Blender with all 3 699 body vertices
    bound to `RightShoulder` at weight 1 and a `.a` holding a single track
    named after the *object*: the unit folds into a heap around one bone and
    never animates, while nothing in the files looks structurally wrong.  Both
    shapes are reported now — a genuine single-bone weapon part is not."""
    import shutil
    import tempfile
    from d3tool import anim as animmod
    from d3tool import cli as climod
    from d3tool import gfile as gmod
    from d3tool import weights as wmod

    retail_g = open(os.path.join(REPO, "Empire", "Inquisitor",
                                 "character_empire_inquisitor.g"), "rb").read()
    parsed = gmod.parse_geometry_file(retail_g)
    assert wmod.skinning_problems(parsed) == []          # retail is clean
    assert len(wmod.summarise(parsed)[0].dominant) > 1

    # the body block, rewritten the way the broken export wrote it: every
    # vertex on bone slot 0 with weight 1
    attrs, _ = gmod.parse_attributes(retail_g)
    for vertex in parsed.vertices:
        vertex.stored_weights = (1.0,)
        vertex.bones = (0, 0)
    parsed.weights_on_vertex = 1
    rigid = gmod.write_geometry_file(parsed, attrs)

    ridden = wmod.skinning_problems(gmod.parse_geometry_file(rigid))
    assert len(ridden) == 1, ridden
    assert "'RightShoulder'" in ridden[0], ridden
    assert "rigid" in ridden[0], ridden
    # the sword block is rigid by design (one bone in its own table): it must
    # not be reported, or every exported weapon would look like a defect
    assert all("weapon_empire_inquisitor" not in p for p in ridden), ridden

    with tempfile.TemporaryDirectory() as d:
        base = "character_empire_inquisitor"
        for ext in (".scene", ".ac", "_lod.scene", "_lod.g"):
            shutil.copyfile(os.path.join(REPO, "Empire", "Inquisitor", base + ext),
                            os.path.join(d, base + ext))
        open(os.path.join(d, base + ".g"), "wb").write(rigid)
        # one track named after the object, 301 frames: what an export that
        # skipped the armature writes
        broken = animmod.build_anim([("empire_inquisitor", "Scene Root",
                                      [(0.0,) * 7] * 301)], 301)
        broken.raw = b""
        open(os.path.join(d, base + "_baseanims.a"), "wb").write(
            animmod.write_anim(broken))
        seen = []
        real = climod.ui.warn
        climod.ui.warn = lambda msg="": seen.append(str(msg))
        try:
            climod._battle_readiness_check(d, base, False)
        finally:
            climod.ui.warn = real
        text = "\n".join(seen)
        assert "the mesh is rigid" in text, text
        assert "1 bone track ('empire_inquisitor')" in text, text
        assert text.count("armature was not exported") == 1, text


def test_export_keeps_animation_only_bones():
    """A `.a` is addressed by index, so an animation-only bone the glTF has no
    channel for (a weapon trail carries no weights) shifts every bone behind
    it: the Inquisitor's trail went from `Trail1`/`Trail2` to
    `RightShoulder`/`RightArm` and the effect followed the wrong limbs.  The
    donor's record is the only correct source for such a bone."""
    from d3tool import anim as animmod
    from d3tool import gltf as gltfmod
    folder = os.path.join(REPO, "Empire", "Inquisitor")
    retail = animmod.parse_anim(open(os.path.join(
        folder, "character_empire_inquisitor_baseanims.a"), "rb").read())
    assert retail.bone_count == 27
    assert retail.bones[11].name == "Trail1"
    assert retail.bones[12].name == "Trail2"

    # what a Blender export produced: the same stream minus the two trail
    # tracks (no weights, so the glTF carried no channel for them)
    hurt = animmod.AnimFile(bone_count=25, frame_count=retail.frame_count,
                            header=retail.header,
                            bones=[b for i, b in enumerate(retail.bones)
                                   if i not in (11, 12)],
                            trailing=retail.trailing)
    assert hurt.bones[11].name == "RightShoulder"

    fixed = gltf._donor_full_bones(hurt, retail)
    assert len(fixed.bones) == 27
    assert [b.name for b in fixed.bones] == [b.name for b in retail.bones]
    assert fixed.bones[11].name == "Trail1"
    assert list(fixed.bones[11].frames) == list(retail.bones[11].frames)
    assert list(fixed.bones[12].frames) == list(retail.bones[12].frames)
    # every track the export did carry stays exactly as it was
    for name in ("Reference", "Hips", "Weapon1", "RightArm"):
        mine = next(b for b in fixed.bones if b.name == name)
        was = next(b for b in hurt.bones if b.name == name)
        assert list(mine.frames) == list(was.frames), name


def test_refresh_part_gobjs_restates_the_swapped_part():
    """A part the export REPLACED keeps the donor's `gobj`, which still names
    the replaced weapon's texture — that is how a pack ends up shipping a `.t`
    nothing references while the `.g` names one that is not there."""
    import copy
    from d3tool import gfile as gmod
    from d3tool import scene as scenemod

    folder = os.path.join(REPO, "Empire", "Inquisitor")
    text = open(os.path.join(folder, "character_empire_inquisitor.scene"),
                encoding="latin-1", newline="").read()
    parts = list(gmod.iter_parts(gmod.parse_geometry_file(open(os.path.join(
        folder, "character_empire_inquisitor.g"), "rb").read())))
    same, changed = scenemod.refresh_part_gobjs(text, parts)
    assert changed == [] and same == text          # retail agrees with itself

    knight = gmod.parse_geometry_file(open(os.path.join(
        REPO, "Empire", "Knight", "character_empire_knight.g"), "rb").read())
    sword = [p for p in gmod.iter_parts(knight) if p.name.endswith("sword")][0]
    blk = copy.copy(parts[-1])                     # the mace slot
    blk.vertices = list(sword.vertices)
    blk.indices = list(sword.indices)
    blk.vertex_count = len(sword.vertices)
    blk.tri_count = len(sword.indices) // 3
    blk.weights_on_vertex = 1
    blk.material_diffuse = "weapon_empire_knight_sword_diffuse.tga"
    blk.attrs = dict(blk.attrs,
                     material0_diffuse=blk.material_diffuse)
    out, changed = scenemod.refresh_part_gobjs(text, [parts[0], blk])
    assert changed == ["weapon_empire_inquisitor"], changed
    assert 'attr "material0_diffuse" "weapon_empire_knight_sword_diffuse.tga"' in out
    assert 'attr "material0_triangles_num" "124"' in out
    assert 'attr "vertexs_weights_num" "136"' in out
    # the body's block is untouched, and so is every other line
    assert out.count("material0_diffuse") == text.count("material0_diffuse")


def test_lod_sync_replaces_a_weapon_the_export_swapped():
    """The LOD keeps the weapon the unit shipped with unless the export's
    part is carried over: the part is already present by name, so nothing is
    appended and the battlefield draws the old weapon at distance."""
    import copy
    import shutil
    import tempfile
    from d3tool import cli as climod
    from d3tool import gfile as gmod

    folder = os.path.join(REPO, "Empire", "Inquisitor")
    retail = open(os.path.join(folder, "character_empire_inquisitor.g"),
                  "rb").read()
    parts = list(gmod.iter_parts(gmod.parse_geometry_file(retail)))
    prefix = retail[:len(retail) - sum(len(p.raw) for p in parts)]
    knight = gmod.parse_geometry_file(open(os.path.join(
        REPO, "Empire", "Knight", "character_empire_knight.g"), "rb").read())
    sword = [p for p in gmod.iter_parts(knight) if p.name.endswith("sword")][0]
    blk = copy.copy(parts[-1])
    blk.vertices = list(sword.vertices)
    blk.indices = list(sword.indices)
    items = [(k, "weapon_empire_knight_sword_diffuse.tga"
              if k == "material0_diffuse" else v)
             for k, v in blk.attr_items]
    blk.attr_items = items
    blk.attrs = dict(items)
    blk.raw = gmod._part_block(blk)

    with tempfile.TemporaryDirectory() as d:
        base = "character_empire_inquisitor"
        open(os.path.join(d, base + ".g"), "wb").write(
            prefix + parts[0].raw + blk.raw)
        for ext in ("_lod.g", "_lod.scene"):
            shutil.copyfile(os.path.join(folder, base + ext),
                            os.path.join(d, base + ext))
        before_lod = gmod.parse_geometry_file(open(os.path.join(
            d, base + "_lod.g"), "rb").read())
        before_parts = list(gmod.iter_parts(before_lod))
        assert before_parts[-1].attrs["material0_diffuse"] == \
            "weapon_empire_inquisitor.tga"              # the shipped mace

        assert climod._lod_sync(d, quiet=True) == 0
        after = list(gmod.iter_parts(gmod.parse_geometry_file(open(os.path.join(
            d, base + "_lod.g"), "rb").read())))
        assert len(after) == len(before_parts)           # replaced, not added
        weapon = after[-1]
        assert weapon.name == "lod_weapon_empire_inquisitor"
        assert len(weapon.vertices) == len(sword.vertices)
        assert weapon.attrs["material0_diffuse"] == \
            "weapon_empire_knight_sword_diffuse.tga"
        assert weapon.attrs["dwParent"] == before_parts[-1].attrs["dwParent"]
        text = open(os.path.join(d, base + "_lod.scene"),
                    encoding="latin-1", newline="").read()
        assert "weapon_empire_knight_sword_diffuse.tga" in text
        # idempotent
        assert climod._lod_sync(d, quiet=True) == 0
        assert open(os.path.join(d, base + "_lod.g"), "rb").read() == \
            open(os.path.join(d, base + "_lod.g"), "rb").read()


def test_lod_sync_swaps_a_replaced_part_that_keeps_the_old_material_name():
    """A sword dropped into the mace slot is exported under the *part's* own
    material name — `weapon_empire_inquisitor.tga`, exactly what the shipped
    mace carries — so comparing materials left the LOD drawing the mace while
    the main mesh drew the sword.  The LOD's own copy of a part is the same
    shape at lower detail, which is what the swap looks at now."""
    import copy
    import shutil
    import tempfile
    from d3tool import cli as climod
    from d3tool import gfile as gmod

    folder = os.path.join(REPO, "Empire", "Inquisitor")
    retail = open(os.path.join(folder, "character_empire_inquisitor.g"),
                  "rb").read()
    parts = list(gmod.iter_parts(gmod.parse_geometry_file(retail)))
    prefix = retail[:len(retail) - sum(len(p.raw) for p in parts)]
    knight = gmod.parse_geometry_file(open(os.path.join(
        REPO, "Empire", "Knight", "character_empire_knight.g"), "rb").read())
    sword = [p for p in gmod.iter_parts(knight) if p.name.endswith("sword")][0]
    blk = copy.copy(parts[-1])
    blk.vertices = list(sword.vertices)         # 0.26 long, the mace is 1.96
    blk.indices = list(sword.indices)
    blk.raw = gmod._part_block(blk)            # material name left untouched
    assert blk.attrs["material0_diffuse"] == "weapon_empire_inquisitor.tga"

    with tempfile.TemporaryDirectory() as d:
        base = "character_empire_inquisitor"
        open(os.path.join(d, base + ".g"), "wb").write(
            prefix + parts[0].raw + blk.raw)
        for ext in ("_lod.g", "_lod.scene"):
            shutil.copyfile(os.path.join(folder, base + ext),
                            os.path.join(d, base + ext))
        lod_path = os.path.join(d, base + "_lod.g")
        before = open(lod_path, "rb").read()
        before_parts = list(gmod.iter_parts(gmod.parse_geometry_file(before)))
        assert before_parts[-1].attrs["material0_diffuse"] == \
            "weapon_empire_inquisitor.tga"
        assert len(before_parts[-1].vertices) != len(sword.vertices)

        assert climod._lod_sync(d, quiet=True) == 0
        after = open(lod_path, "rb").read()
        after_parts = list(gmod.iter_parts(gmod.parse_geometry_file(after)))
        assert len(after_parts) == len(before_parts)        # replaced
        weapon = after_parts[-1]
        assert weapon.name == "lod_weapon_empire_inquisitor"
        assert len(weapon.vertices) == len(sword.vertices)
        # idempotent
        assert climod._lod_sync(d, quiet=True) == 0
        assert open(lod_path, "rb").read() == after

        # ... and the retailer's own LOD pair is left alone: same shape,
        # fewer vertices is the decimation, not a swap
        for ext in (".g", "_lod.g", "_lod.scene"):
            shutil.copyfile(os.path.join(folder, base + ext),
                            os.path.join(d, base + ext))
        retail_before = open(lod_path, "rb").read()
        assert climod._lod_sync(d, quiet=True) == 0
        assert open(lod_path, "rb").read() == retail_before


def test_export_reports_a_part_that_is_out_of_scale_with_the_donor():
    """A `.g` carries no transform, so a part scaled in Blender exports the
    way it looks: the Inquisitor's borrowed sword came out 1.9x the Knight's
    and half a hand off the grip while every byte of the file looked sane."""
    from d3tool import gfile as gmod
    from d3tool import parts as pmod

    folder = os.path.join(REPO, "Empire", "Inquisitor")
    retail = open(os.path.join(folder, "character_empire_inquisitor.g"),
                  "rb").read()
    donor = gmod.parse_geometry_file(retail)
    mine = gmod.parse_geometry_file(retail)
    assert pmod.donor_mismatches(mine, donor) == []

    weapon = [p for p in gmod.iter_parts(mine)
              if p.name.startswith("weapon")][0]
    for v in weapon.vertices:
        v.position = tuple(c * 1.8 for c in v.position)
    x, y, z = weapon.vertices[0].position
    weapon.vertices[0].position = (x, y, z + 0.6)  # and off the grip
    text = "\n".join(pmod.donor_mismatches(mine, donor))
    assert "1.80x the donor's" in text, text
    assert "Apply > Scale" in text, text
    assert "sits" in text and "along" in text, text


def _blender_animation_model(donor, Q, frames, name="Idle"):
    """A hand-built ``GltfModel`` shaped like a Blender re-save.

    ``Q`` holds the per-bone frame change: the glTF channel locals are built
    as ``L_b = Q_parent . L_o . inv(Q_j)`` from the donor's own records, which
    is exactly what Blender's glTF writer produces when its rest pose differs
    from the `.g` bind frame (Blender bakes the scene pose into the joints).
    """
    from d3tool import frame as framemod
    from d3tool.model import GltfModel

    names = [b.name for b in donor.bones]
    by_name = {b.name: b for b in donor.bones}
    ident = framemod.IDENTITY

    nodes = [{"name": "Scene Root", "children": []}]
    index = {}
    for nm in names:
        index[nm] = len(nodes)
        nodes.append({"name": nm, "children": []})
    for nm in names:
        parent = by_name[nm].parent
        holder = index.get(parent, 0)
        nodes[holder]["children"].append(index[nm])

    channels = {}
    for nm in names:
        parent = by_name[nm].parent
        Qj = Q.get(nm, ident)
        Qp = Q.get(parent, ident) if parent else ident
        qt, tt = [], []
        for f in frames:
            L = framemod.sample_matrix(by_name[nm].frames[f][:7])
            Lb = framemod.mm(Qp, framemod.mm(L, framemod.inv4(Qj)))
            q = framemod.mat_to_quat(Lb)
            qt.append(q)
            tt.append((Lb[0][3], Lb[1][3], Lb[2][3]))
        channels[index[nm]] = {"rotation": qt, "translation": tt}

    return GltfModel(
        mesh_name=name, nodes=nodes, frames=[float(i) for i in range(len(frames))],
        animation={"channels": [{"target": {"node": i}} for i in channels]},
        anim_channels=channels, blender_layout=True)


def test_blender_resave_converts_a_single_stream_animation():
    """A Blender re-save cannot keep the `.g`'s bind frame: it bakes the scene
    pose into the joints, so its channel TRS are local matrices in *Blender's*
    frame.  The mesh side always converted them back (``d3tool/frame.py``); the
    animation side only did for a unit whose `.ac` names several streams, so
    every single-stream unit — the Inquisitor among them — shipped
    Blender-frame rotations and contorted on the next import / in game while
    every byte of the file looked sane.
    """
    import math
    from d3tool import anim as animmod
    from d3tool import frame as framemod
    from d3tool import gltf as gltfmod

    donor = animmod.parse_anim(open(os.path.join(
        REPO, "Empire", "Inquisitor",
        "character_empire_inquisitor_baseanims.a"), "rb").read())
    # a different constant change of basis per bone, the way a baked rest pose
    # differs from the bind frame
    Q = {}
    for k, bone in enumerate(donor.bones):
        ang = 0.25 + 0.07 * k
        axis = ((k % 3) - 1.0, ((k + 1) % 3) - 1.0, ((k + 2) % 3) - 1.0)
        ln = math.sqrt(sum(c * c for c in axis)) or 1.0
        Q[bone.name] = framemod.quat_to_mat(
            (math.sin(ang / 2) * axis[0] / ln,
             math.sin(ang / 2) * axis[1] / ln,
             math.sin(ang / 2) * axis[2] / ln, math.cos(ang / 2)))

    frames = (0, 90, 240)
    m = _blender_animation_model(donor, Q, frames)
    built = gltf.animation_from_gltf(
        m, donor=donor, streams=[("character_empire_inquisitor_baseanims.a",
                                  donor)], out_name="Idle")
    assert [b.name for b in built.bones] == [b.name for b in donor.bones]
    by_name = {b.name: b for b in built.bones}
    worst = 0.0
    for k, f in enumerate(frames):
        for bone in donor.bones:
            got = by_name[bone.name].frames[k]
            ref = bone.frames[f]
            worst = max(worst, max(abs(a - b) for a, b in zip(got[:7], ref[:7])))
    assert worst < 1e-4, worst

    # a dis3tool glTF (its locals *are* the `.a` records) is copied verbatim:
    # the conversion must not touch the byte-exact round trip
    m.blender_layout = False
    plain = gltf.animation_from_gltf(
        m, donor=donor, streams=[("character_empire_inquisitor_baseanims.a",
                                  donor)], out_name="Idle")
    plain_by = {b.name: b for b in plain.bones}
    got = plain_by[donor.bones[3].name].frames[0]
    assert abs(got[0] - m.anim_channels[3 + 1]["rotation"][0][0]) < 1e-6


def test_texture_lookup_survives_a_doubled_extension():
    """A material names ``x.tga``; the folder ships ``x.dds.t``.

    That spelling is what an export wrote before the stem was cleaned, and a
    case-different name is what a pack authored on Windows ships.  Both have to
    resolve to the file that is actually there — otherwise the part imports
    untextured while the texture sits next to it.
    """
    tmp = tempfile.mkdtemp(prefix="d3tex_")
    try:
        g_path = os.path.join(tmp, "unit.g")
        open(g_path, "wb").write(b"")
        open(os.path.join(tmp, "Sword_Diffuse.DDS.T"), "wb").write(b"x")
        attrs = {"material0_diffuse": "sword_diffuse.tga"}
        found = texmod.find_diffuse_texture(g_path, attrs)
        assert found == os.path.join(tmp, "Sword_Diffuse.DDS.T"), found
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_texture_stem_strips_every_extension():
    assert texmod.clean_stem("weapon_empire_knight_sword.dds.png") == \
        "weapon_empire_knight_sword"
    assert texmod.clean_stem("character.t") == "character"
    assert texmod.clean_stem("character_empire_inquisitor.tga") == \
        "character_empire_inquisitor"
    assert texmod.clean_stem("no_extension") == "no_extension"


def test_export_carries_the_texture_its_g_names():
    """An export must ship every diffuse its own `.g` names.

    A unit re-textured from another unit's folder names a texture the export
    folder does not have; in game the retail copy hides that, but reading the
    export back (the add-on's own import, or anyone opening it) leaves the
    part untextured.  The donor folder has the file, so the export carries it.
    """
    tmp = tempfile.mkdtemp(prefix="d3carry_")
    try:
        base = "character_empire_inquisitor"
        shutil.copyfile(os.path.join(REPO, "Empire", "Inquisitor", base + ".g"),
                        os.path.join(tmp, base + ".g"))
        climod._carry_material_textures(tmp, base,
                                        os.path.join(REPO, "Empire",
                                                     "Inquisitor"), True)
        for name in ("character_empire_inquisitor.t",
                     "weapon_empire_inquisitor.t"):
            assert os.path.isfile(os.path.join(tmp, name)), name
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_blender_texture_name_does_not_double_the_extension():
    """A Blender image called ``x.dds`` must not become ``x.dds.tga``/``x.dds.t``.

    Blender keeps the imported file name, so a re-saved texture arrives as
    ``x.dds.png``; a naive stem left the ``.dds`` in place and the export wrote
    a material ``x.dds.tga`` plus a file ``x.dds.t`` — a pair nothing (the
    engine, our own importer, the add-on) can match to ``x``.
    """
    tmp = tempfile.mkdtemp(prefix="d3stem_")
    try:
        donor = os.path.join(tmp, "donor")
        os.makedirs(donor)
        for name in ("character_empire_inquisitor.g",
                     "character_empire_inquisitor_baseanims.a",
                     "character_empire_inquisitor.t",
                     "character_empire_inquisitor.ac"):
            shutil.copyfile(os.path.join(REPO, "Empire", "Inquisitor", name),
                            os.path.join(donor, name))
        fwd = os.path.join(tmp, "fwd")
        os.makedirs(fwd)
        gt, _bt = climod._export_gl(
            os.path.join(donor, "character_empire_inquisitor.g"),
            os.path.join(donor, "character_empire_inquisitor_baseanims.a"),
            os.path.join(fwd, "out.gltf"), None, quiet=True)
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000b49444154789c6300010000050001027d1c5b0000"
            "000049454e44ae426082")
        open(os.path.join(fwd, "blade_diffuse.dds.png"), "wb").write(png)
        doc = json.load(open(gt))
        doc["images"].append({"uri": "blade_diffuse.dds.png"})
        doc["textures"].append({"source": len(doc["images"]) - 1})
        mat = doc["meshes"][1]["primitives"][0]["material"]
        doc["materials"][mat]["pbrMetallicRoughness"]["baseColorTexture"] = {
            "index": len(doc["textures"]) - 1}
        json.dump(doc, open(gt, "w"))

        out = os.path.join(tmp, "back")
        climod._export(gt, out, 0, anim=True, quiet=True, donor_dir=donor)
        mesh = gfile.parse_geometry_file(
            open(os.path.join(out, "out.g"), "rb").read())
        part = gfile.iter_parts(mesh)[-1]
        assert part.attrs.get("material0_diffuse") == "blade_diffuse.tga", \
            part.attrs.get("material0_diffuse")
        assert os.path.isfile(os.path.join(out, "blade_diffuse.t"))
        assert not os.path.exists(os.path.join(out, "blade_diffuse.dds.t"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_retexture_part_rewrites_g_and_scene():
    """A part's material lives in three places and they must agree.

    ``tools/retexture_part.py`` rewrites the part's attribute block in the
    main and LOD `.g` and the matching `gobj` in both `.scene` files, and
    touches nothing else -- a whole-file rewrite of a `.g`/`.scene` would lose
    the scaffolding (attribute order, prelude, line endings) the game needs.
    """
    import importlib.util
    tmp = tempfile.mkdtemp(prefix="d3ret_")
    try:
        src = os.path.join(REPO, "Empire", "Inquisitor")
        for name in ("character_empire_inquisitor.g",
                     "character_empire_inquisitor_lod.g",
                     "character_empire_inquisitor.scene",
                     "character_empire_inquisitor_lod.scene"):
            shutil.copyfile(os.path.join(src, name), os.path.join(tmp, name))
        before_g = open(os.path.join(tmp, "character_empire_inquisitor.g"),
                        "rb").read()
        before_s = open(os.path.join(tmp, "character_empire_inquisitor.scene"),
                        "rb").read()

        spec = importlib.util.spec_from_file_location(
            "retexture_part", os.path.join(REPO, "tools", "retexture_part.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.main(["--folder", tmp, "--part", "weapon_empire_inquisitor",
                         "--diffuse", "blade.tga"]) == 0

        after_g = open(os.path.join(tmp, "character_empire_inquisitor.g"),
                       "rb").read()
        # only the name string changes length; every part must survive, with
        # the same vertex/triangle counts and the same order
        delta = len(before_g) - len(after_g)
        assert 1 <= delta <= 64, delta
        before_parts = gfile.iter_parts(gfile.parse_geometry_file(before_g))
        parts = gfile.iter_parts(gfile.parse_geometry_file(after_g))
        assert [(p.name, p.vertex_count, p.tri_count) for p in parts] == \
            [(p.name, p.vertex_count, p.tri_count) for p in before_parts]
        mats = {p.name: p.attrs.get("material0_diffuse") for p in parts}
        assert mats["weapon_empire_inquisitor"] == "blade.tga", mats
        assert mats["Standard_8"] == "character_empire_inquisitor.tga", mats
        lod = gfile.iter_parts(gfile.parse_geometry_file(
            open(os.path.join(tmp, "character_empire_inquisitor_lod.g"),
                 "rb").read()))
        assert {p.name: p.attrs.get("material0_diffuse")
                for p in lod}["lod_weapon_empire_inquisitor"] == "blade.tga"

        after_s = open(os.path.join(tmp, "character_empire_inquisitor.scene"),
                       "rb").read()
        assert after_s.count(b'"blade.tga"') == 1
        assert len(after_s.splitlines()) == len(before_s.splitlines())
        assert after_s.replace(b"blade.tga", b"weapon_empire_inquisitor.tga") \
            == before_s
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_blender_texture_name_property_reaches_the_g_and_the_scene():
    """``extras.d3unit_diffuse`` beats both the image and the donor part.

    A part can be re-textured from another unit's folder (a sword wearing the
    Knight's blade atlas) while its glTF material still names the donor's
    image.  The add-on stores the artist's name on the mesh object and passes
    it through as ``extras.d3unit_diffuse``; the export has to write it into
    the part's attribute block, into the LOD copy and into the `gobj` of both
    `.scene` files — the engine draws the scene's copy — and carry the `.t`.
    """
    tmp = tempfile.mkdtemp(prefix="d3diffuse_")
    try:
        donor = os.path.join(tmp, "donor")
        os.makedirs(donor)
        for name in ("character_empire_inquisitor.g",
                     "character_empire_inquisitor_baseanims.a",
                     "character_empire_inquisitor.t",
                     "character_empire_inquisitor.ac",
                     "character_empire_inquisitor.scene"):
            shutil.copyfile(os.path.join(REPO, "Empire", "Inquisitor", name),
                            os.path.join(donor, name))
        shutil.copyfile(
            os.path.join(REPO, "Empire", "Knight",
                         "weapon_empire_knight_sword_diffuse.t"),
            os.path.join(donor, "weapon_empire_knight_sword_diffuse.t"))
        fwd = os.path.join(tmp, "fwd")
        os.makedirs(fwd)
        gt, _bt = climod._export_gl(
            os.path.join(donor, "character_empire_inquisitor.g"),
            os.path.join(donor, "character_empire_inquisitor_baseanims.a"),
            os.path.join(fwd, "character_empire_inquisitor.gltf"), None,
            quiet=True)
        doc = json.load(open(gt))
        weapon = [m for m in doc["meshes"]
                  if "weapon" in (m.get("name") or "")][0]
        weapon.setdefault("extras", {})["d3unit_diffuse"] = \
            "weapon_empire_knight_sword_diffuse.tga"
        json.dump(doc, open(gt, "w"))

        out = os.path.join(tmp, "back")
        climod._export(gt, out, 0, anim=True, quiet=True, donor_dir=donor)
        parts = gfile.iter_parts(gfile.parse_geometry_file(
            open(os.path.join(out, "character_empire_inquisitor.g"),
                 "rb").read()))
        mats = {p.name: p.attrs.get("material0_diffuse") for p in parts}
        assert mats["weapon_empire_inquisitor"] == \
            "weapon_empire_knight_sword_diffuse.tga", mats
        assert mats["Standard_8"] == "character_empire_inquisitor.tga", mats
        scene = open(os.path.join(out, "character_empire_inquisitor.scene"),
                     "rb").read().decode("latin-1")
        assert "weapon_empire_knight_sword_diffuse.tga" in scene
        assert scene.count('"weapon_empire_inquisitor.tga"') == 0
        assert os.path.isfile(os.path.join(
            out, "weapon_empire_knight_sword_diffuse.t"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_find_texture_file_resolves_every_shipped_spelling():
    """A part names `<stem>.tga`; the folder may hold `<stem>.t` and friends.

    This is what the add-on's "Show in the Viewport" button and the export's
    texture report both lean on: after a re-texture the file on disk is
    whatever the artist had (`x.t`, `x.dds`, or the doubled `x.dds.t` our own
    exporter used to write), while the name in the `.g` stays `x.tga`.
    """
    tmp = tempfile.mkdtemp(prefix="d3texfind_")
    try:
        first = os.path.join(tmp, "unit")
        second = os.path.join(tmp, "elsewhere")
        os.makedirs(first)
        os.makedirs(second)
        # a .t preferred over the .dds next to it
        open(os.path.join(first, "a.t"), "wb").write(b"")
        open(os.path.join(first, "a.dds"), "wb").write(b"")
        assert texmod.find_texture_file(
            "a.tga", [first]).endswith("a.t")
        # a doubled extension, the name ours used to produce
        open(os.path.join(first, "b.dds.t"), "wb").write(b"")
        assert texmod.find_texture_file(
            "b.tga", [first]).endswith("b.dds.t")
        # a Blender re-save, and a different case on disk
        open(os.path.join(second, "c.dds.png"), "wb").write(b"")
        open(os.path.join(second, "D.t"), "wb").write(b"")
        assert texmod.find_texture_file(
            "c.tga", [first, second]).endswith("c.dds.png")
        assert texmod.find_texture_file(
            "d.tga", [first, second]).endswith("D.t")
        # folders are searched in order, and a miss stays a miss
        assert texmod.find_texture_file("missing.tga", [first, second]) is None
        assert texmod.find_texture_file("", [first]) is None
        assert texmod.find_texture_file("a.tga", ["/no/such/dir"]) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_texture_report_names_what_each_part_will_be_drawn_with():
    """The export reads its own `.g` back and says which texture each part wears.

    Re-texturing a part goes wrong silently: the name can come from the mesh's
    `d3unit_diffuse`, from the material image Blender exported, or from the
    donor part, and only the written file says which one won.  One row per
    part, with the file on disk when there is one.
    """
    tmp = tempfile.mkdtemp(prefix="d3texrep_")
    try:
        src = os.path.join(REPO, "Empire", "Inquisitor")
        for name in ("character_empire_inquisitor.g",
                     "character_empire_inquisitor_baseanims.a",
                     "character_empire_inquisitor.ac",
                     "character_empire_inquisitor.scene",
                     "character_empire_inquisitor.t"):
            shutil.copyfile(os.path.join(src, name),
                            os.path.join(tmp, name))
        g_path = os.path.join(tmp, "character_empire_inquisitor.g")
        rows = {part: (name, found) for part, name, found
                in partsmod.texture_report(g_path)}
        name, found = rows["Standard_8"]
        assert name == "character_empire_inquisitor.tga", rows
        assert os.path.basename(found) == "character_empire_inquisitor.t"
        # the weapon part names the other unit's atlas: absent until the .t is
        # dropped next to the unit, which is exactly the finding the report
        # has to surface instead of an untextured part in game
        name, found = rows["weapon_empire_inquisitor"]
        assert name == "weapon_empire_inquisitor.tga", rows
        assert found == "", rows
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_sword_takes_its_own_atlas_and_never_the_shield():
    """Both atlases sit in one folder; only the stem decides.

    The Knight ships two weapon textures — a 128x1024 blade strip (86 KiB) and
    a 512x2048 shield sheet (683 KiB) — and a re-textured part that grabs the
    wrong one looks *plausible* in Blender and wrong in game.  Resolution has to
    be stem-exact: no "close enough" match, no picking the biggest file, no
    shared prefix (`weapon_empire_knight_`).
    """
    tmp = tempfile.mkdtemp(prefix="d3swordshield_")
    try:
        src = os.path.join(REPO, "Empire", "Inquisitor")
        for name in ("character_empire_inquisitor.g",
                     "character_empire_inquisitor_baseanims.a",
                     "character_empire_inquisitor.ac",
                     "character_empire_inquisitor.scene",
                     "character_empire_inquisitor.t"):
            shutil.copyfile(os.path.join(src, name),
                            os.path.join(tmp, name))
        knight = os.path.join(REPO, "Empire", "Knight")
        sword_t = os.path.join(knight, "weapon_empire_knight_sword_diffuse.t")
        shield_t = os.path.join(knight, "weapon_empire_knight_shield_diffuse.t")
        assert os.path.getsize(shield_t) > 5 * os.path.getsize(sword_t)
        assert texmod.describe(sword_t) == "128x1024"
        assert texmod.describe(shield_t) == "512x2048"
        shutil.copyfile(sword_t,
                        os.path.join(tmp, "weapon_empire_knight_sword_diffuse.t"))
        shutil.copyfile(shield_t,
                        os.path.join(tmp, "weapon_empire_knight_shield_diffuse.t"))

        found = texmod.find_texture_file(
            "weapon_empire_knight_sword_diffuse.tga", [tmp])
        assert os.path.basename(found) == \
            "weapon_empire_knight_sword_diffuse.t", found
        found = texmod.find_texture_file(
            "weapon_empire_knight_shield_diffuse.tga", [tmp])
        assert os.path.basename(found) == \
            "weapon_empire_knight_shield_diffuse.t", found
        # a name with neither stem in the folder must stay unresolved rather
        # than fall back to the nearest weapon atlas
        assert texmod.find_texture_file(
            "weapon_empire_knight_mace_diffuse.tga", [tmp]) is None

        # the whole way through: name the inquisitor's sword part after the
        # blade atlas and export; the .t that lands in the folder is the 86 KiB
        # one, byte for byte, not the shield's
        fwd = os.path.join(tmp, "fwd")
        os.makedirs(fwd)
        gt, _bt = climod._export_gl(
            os.path.join(tmp, "character_empire_inquisitor.g"),
            os.path.join(tmp, "character_empire_inquisitor_baseanims.a"),
            os.path.join(fwd, "character_empire_inquisitor.gltf"), None,
            quiet=True)
        doc = json.load(open(gt))
        weapon = [m for m in doc["meshes"]
                  if "weapon" in (m.get("name") or "")][0]
        weapon.setdefault("extras", {})["d3unit_diffuse"] = \
            "weapon_empire_knight_sword_diffuse.tga"
        json.dump(doc, open(gt, "w"))
        out = os.path.join(tmp, "back")
        climod._export(gt, out, 0, anim=True, quiet=True, donor_dir=tmp)

        carried = os.path.join(out, "weapon_empire_knight_sword_diffuse.t")
        assert os.path.isfile(carried), os.listdir(out)
        assert open(carried, "rb").read() == open(sword_t, "rb").read(), \
            "the exported .t is not the blade atlas"
        assert texmod.describe(carried) == "128x1024"
        rows = {part: (name, found) for part, name, found
                in partsmod.texture_report(
                    os.path.join(out, "character_empire_inquisitor.g"), [out])}
        name, found = rows["weapon_empire_inquisitor"]
        assert name == "weapon_empire_knight_sword_diffuse.tga", rows
        assert os.path.basename(found) == \
            "weapon_empire_knight_sword_diffuse.t", rows
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_swapped_mesh_does_not_keep_the_donors_texture_name():
    """The name a part was imported with must not outlive the mesh it named.

    Graft a Knight's blade onto the Inquisitor's mace slot and the object still
    carries `d3unit_diffuse = weapon_empire_inquisitor.tga` — the name the
    add-on stamped at import.  Writing it back pins the sword to the mace's
    wooden atlas; the material the sword arrived with says otherwise, and
    *that* is the name to keep.  A name the artist typed (anything but the
    donor's) still wins, because it is the only way to texture a part that has
    no usable material of its own.
    """
    tmp = tempfile.mkdtemp(prefix="d3swapped_")
    try:
        donor = os.path.join(tmp, "donor")
        os.makedirs(donor)
        for name in ("character_empire_inquisitor.g",
                     "character_empire_inquisitor_baseanims.a",
                     "character_empire_inquisitor.ac",
                     "character_empire_inquisitor.scene",
                     "character_empire_inquisitor.t"):
            shutil.copyfile(os.path.join(REPO, "Empire", "Inquisitor", name),
                            os.path.join(donor, name))
        fwd = os.path.join(tmp, "fwd")
        os.makedirs(fwd)

        def swapped(extras):
            """The unit's glTF with the weapon wearing the Knight's blade."""
            gt, _bt = climod._export_gl(
                os.path.join(donor, "character_empire_inquisitor.g"),
                os.path.join(donor, "character_empire_inquisitor_baseanims.a"),
                os.path.join(fwd, "character_empire_inquisitor.gltf"), None,
                quiet=True)
            doc = json.load(open(gt))
            weapon = [m for m in doc["meshes"]
                      if "weapon" in (m.get("name") or "")][0]
            prim = weapon["primitives"][0]
            doc.setdefault("images", []).append(
                {"uri": "weapon_empire_knight_sword_diffuse.dds"})
            doc.setdefault("textures", []).append(
                {"source": len(doc["images"]) - 1})
            doc["materials"][prim["material"]]["pbrMetallicRoughness"] = {
                "baseColorTexture": {"index": len(doc["textures"]) - 1}}
            if extras:
                weapon.setdefault("extras", {})["d3unit_diffuse"] = extras
            json.dump(doc, open(gt, "w"))
            return gt

        # the inherited default yields to the material of the swapped mesh
        gt = swapped("weapon_empire_inquisitor.tga")
        out = os.path.join(tmp, "back1")
        climod._export(gt, out, 0, anim=True, quiet=True, donor_dir=donor)
        parts = {p.name: p.attrs.get("material0_diffuse")
                 for p in gfile.iter_parts(gfile.parse_geometry_file(
                     open(os.path.join(out, "character_empire_inquisitor.g"),
                          "rb").read()))}
        assert parts["weapon_empire_inquisitor"] == \
            "weapon_empire_knight_sword_diffuse.tga", parts

        # ...but a name the artist chose beats any material
        gt = swapped("weapon_empire_knight_shield_diffuse.tga")
        out = os.path.join(tmp, "back2")
        climod._export(gt, out, 0, anim=True, quiet=True, donor_dir=donor)
        parts = {p.name: p.attrs.get("material0_diffuse")
                 for p in gfile.iter_parts(gfile.parse_geometry_file(
                     open(os.path.join(out, "character_empire_inquisitor.g"),
                          "rb").read()))}
        assert parts["weapon_empire_inquisitor"] == \
            "weapon_empire_knight_shield_diffuse.tga", parts
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_parts_texture_is_written_from_the_attribute_items_not_the_dict():
    """``attrs`` only drives the counts; the block is emitted from ``items``.

    Setting ``part.material_diffuse`` — or even ``attrs["material0_diffuse"]``
    — changes nothing in the file, because `gfile._attr_block` writes the
    donor's ordered ``(key, value)`` list verbatim and only re-reads ``attrs``
    for the counts it may patch.  That is how a Knight's blade grafted onto the
    Inquisitor's mace slot kept naming the mace's atlas through every export:
    the rebuild fed the right name into the dataclass and the donor's string
    into the file.
    """
    from d3tool import gltf as gltfmod

    old = "weapon_empire_inquisitor.tga"
    want = "weapon_empire_knight_sword_diffuse.tga"
    items = [("name", "weapon_empire_inquisitor"),
             ("materials_num", "1"),
             ("material0_diffuse", old),
             ("vertexs_weights_num", "136")]
    attrs = dict(items)

    # the dataclass / the dict say the new name — the block does not
    naive = dict(attrs)
    naive["material0_diffuse"] = want
    block = gfile._attr_block(naive, list(items))
    assert want.encode() not in block, "attrs alone must not reach the file"
    # ...except for the file's own block, where the whole point is that a
    # renamed atlas reaches the file (`write_geometry_file` asks for it)
    root_block = gfile._attr_block(naive, list(items), patch_textures=True)
    assert want.encode() in root_block, \
        "a renamed atlas must reach the root block"
    assert old.encode() not in root_block

    # rewriting the items is what actually changes it, and it keeps the block
    # otherwise byte-identical
    patched_attrs, patched_items, written = gltf._patch_material(
        attrs, list(items), want, old)
    assert written == want, written
    # both routes end in the same bytes: the items carry the new name anyway
    block_default = gfile._attr_block(patched_attrs, patched_items)
    block_root = gfile._attr_block(patched_attrs, patched_items,
                                   patch_textures=True)
    assert block_default == block_root
    assert want.encode() in block_default
    assert old.encode() not in block_default
    assert len(patched_items) == len(items)

    # a part that already names that texture is left alone (the corpus round
    # trip is byte-exact, so an untouched part must not be rewritten)
    same_attrs, same_items, written = gltf._patch_material(
        attrs, list(items), old, old)
    assert written == "" and same_items == items


def test_the_compact_copy_of_a_stem_wins_and_its_format_is_reported():
    """One atlas, two files: 86 KiB as DXT1 and 683 KiB as raw RGBA.

    An artist who re-saved the Knight's blade atlas through Blender ends up with
    a ``x.dds.t`` eight times the size of the shipped ``x.t``; both hold the
    same picture, and "683 KB after export" reads like the wrong texture was
    picked.  Resolution prefers the block-compressed copy when a folder offers
    both, and the report says how the pixels are stored so the number stops
    being ambiguous.
    """
    tmp = tempfile.mkdtemp(prefix="d3compact_")
    try:
        # neither is the exact spelling, so the loose match has to choose
        small = os.path.join(tmp, "x.dds.t")
        big = os.path.join(tmp, "x.png.t")
        open(small, "wb").write(b"small")
        open(big, "wb").write(b"big")
        assert texmod.clean_stem(small) == texmod.clean_stem(big) == "x"
        real = texmod.is_compressed
        texmod.is_compressed = lambda p: os.path.basename(p) == "x.dds.t"
        try:
            assert texmod.find_texture_file("x.tga", [tmp]) == small
            texmod.is_compressed = lambda p: os.path.basename(p) == "x.png.t"
            assert texmod.find_texture_file("x.tga", [tmp]) == big
        finally:
            texmod.is_compressed = real

        # an exact `<stem>.t` beats every loose spelling, compressed or not
        exact = os.path.join(tmp, "x.t")
        open(exact, "wb").write(b"exact")
        assert texmod.find_texture_file("x.tga", [tmp]) == exact

        knight = os.path.join(REPO, "Empire", "Knight")
        sword = os.path.join(knight, "weapon_empire_knight_sword_diffuse.t")
        shield = os.path.join(knight, "weapon_empire_knight_shield_diffuse.t")
        assert texmod.describe(sword, True) == "128x1024 DXT1"
        assert texmod.describe(shield, True) == "512x2048 DXT1"
        assert texmod.describe(sword) == "128x1024"
        assert texmod.is_compressed(sword)
        assert texmod.describe("/no/such.file", True) == ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_part_with_two_materials_writes_both_atlases():
    """Body and weapon in one mesh: ``material0`` and ``material1`` of a block.

    The shipped units give every part a single material, so reading the first
    primitive was enough — until an artist merges the sword into the body mesh
    (or a unit that really has two ranges is re-textured).  Then the second
    primitive's atlas has to reach ``material1_diffuse``, both in the attribute
    dict and in the ordered item list the file is actually written from.
    """
    doc = {
        "materials": [
            {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
            {"pbrMetallicRoughness": {"baseColorTexture": {"index": 1}}},
        ],
        "textures": [{"source": 0}, {"source": 1}],
        "images": [{"uri": "character_empire_inquisitor.dds.png"},
                   {"uri": "weapon_empire_knight_sword_diffuse.dds.png"}],
    }
    mesh_json = {"primitives": [{"material": 0}, {"material": 1}]}
    slots = gltf._per_slot_textures(doc, mesh_json)
    assert slots == {0: "character_empire_inquisitor.tga",
                     1: "weapon_empire_knight_sword_diffuse.tga"}, slots

    items = [("name", "empire_inquisitor"), ("materials_num", "2"),
             ("material0_diffuse", "character_empire_inquisitor.tga"),
             ("material0_triangles_num", "10"),
             ("material1_diffuse", "weapon_empire_inquisitor.tga"),
             ("material1_triangles_num", "4")]
    attrs = dict(items)
    a, i, written = gltf._patch_material(
        attrs, items, "character_empire_inquisitor.tga", "", slots)
    got = dict(i)
    assert got["material0_diffuse"] == "character_empire_inquisitor.tga"
    assert got["material1_diffuse"] == "weapon_empire_knight_sword_diffuse.tga"
    assert a["material1_diffuse"] == "weapon_empire_knight_sword_diffuse.tga"
    assert written == ""            # material0 already said that
    assert [k for k, _ in i] == [k for k, _ in items]

    # a one-material donor part gets no invented material1 attributes: the
    # triangle ranges of two materials in a block of one would disagree
    single = [("material0_diffuse", "weapon_empire_inquisitor.tga"),
              ("material0_triangles_num", "250")]
    a2, i2, _w = gltf._patch_material(
        dict(single), single, "weapon_empire_knight_sword_diffuse.tga", "",
        {0: "weapon_empire_knight_sword_diffuse.tga"})
    assert dict(i2)["material0_diffuse"] == \
        "weapon_empire_knight_sword_diffuse.tga"
    assert "material1_diffuse" not in dict(i2)

    # ...and with no texture at all the donor's block is returned untouched
    a3, i3, w3 = gltf._patch_material(dict(single), single, "", "")
    assert i3 is single and a3 is not None and w3 == ""


def test_the_lod_follows_the_main_mesh_texture():
    """All four files must agree on the weapon's atlas — the LOD included.

    `unit_scene_lod` has a draw distance of 10 for nearly every unit, so the
    battlefield draws the LOD model most of the time; a main mesh that says
    "sword" while `_lod.g` and `_lod.scene` still say "mace" changes the
    weapon the moment the camera pulls back.  A `_lod.scene` also repeats the
    MAIN mesh's counts (every retail unit does), so restating it from the LOD
    mesh wrote numbers no shipped file has.
    """
    tmp = tempfile.mkdtemp(prefix="d3lodtex_")
    try:
        work = os.path.join(tmp, "unit")
        os.makedirs(work)
        for name in ("character_empire_inquisitor.g",
                     "character_empire_inquisitor_baseanims.a",
                     "character_empire_inquisitor.ac",
                     "character_empire_inquisitor.scene",
                     "character_empire_inquisitor.t",
                     "character_empire_inquisitor_lod.g",
                     "character_empire_inquisitor_lod.scene",
                     "weapon_empire_inquisitor.t"):
            shutil.copyfile(os.path.join(REPO, "Empire", "Inquisitor", name),
                            os.path.join(work, name))
        shutil.copyfile(
            os.path.join(REPO, "Empire", "Knight",
                         "weapon_empire_knight_sword_diffuse.t"),
            os.path.join(work, "weapon_empire_knight_sword_diffuse.t"))
        gl = os.path.join(tmp, "character_empire_inquisitor.gltf")
        climod._export_gl(os.path.join(work, "character_empire_inquisitor.g"),
                          os.path.join(work,
                                       "character_empire_inquisitor_baseanims.a"),
                          gl, None, quiet=True)
        doc = json.load(open(gl))
        for mesh in doc["meshes"]:
            if "weapon" in (mesh.get("name") or ""):
                mesh.setdefault("extras", {})["d3unit_diffuse"] = \
                    "weapon_empire_knight_sword_diffuse.tga"
        json.dump(doc, open(gl, "w"))
        out = os.path.join(tmp, "out")
        climod._export(gl, out, 0, anim=True, quiet=True, donor_dir=work)

        sword = "weapon_empire_knight_sword_diffuse.tga"
        for name in ("character_empire_inquisitor.g",
                     "character_empire_inquisitor_lod.g"):
            mesh = gfile.parse_geometry_file(
                open(os.path.join(out, name), "rb").read())
            parts = list(gfile.iter_parts(mesh))
            assert parts[1].attrs["material0_diffuse"] == sword, (name, parts[1].attrs)
        for name in ("character_empire_inquisitor.scene",
                     "character_empire_inquisitor_lod.scene"):
            text = open(os.path.join(out, name), encoding="latin-1").read()
            assert sword in text, name
            assert "weapon_empire_inquisitor.tga" not in text.split("weapon")[0], name

        # the LOD scene keeps the main mesh's numbers, as every retail unit does
        lod_scene = open(os.path.join(out,
                                      "character_empire_inquisitor_lod.scene"),
                         encoding="latin-1").read()
        assert 'attr "material0_triangles_num" "4664"' in lod_scene
        assert 'attr "vertexs_weights_num" "3693"' in lod_scene
        # ...and the audit agrees that the bundle is self-consistent
        _facts, findings = auditmod.audit(out)
        assert findings == [], findings
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_audit_reports_a_scene_that_describes_the_old_mesh():
    """A `.g` and a `.scene` that disagree both parse; only a reader notices.

    A scene left describing the mesh an export replaced is reported — as a
    note, not a finding.  The shipped game disagrees with itself this way
    everywhere (the Archer's body gobj says 3631 triangles where its block
    holds 4407, the Cleric's LOD gobj says 376 against 156) and loads fine, so
    these numbers cannot be what the loader reads; what it does read — a part
    that does not exist, a texture that is not in the folder — stays a
    finding.
    """
    tmp = tempfile.mkdtemp(prefix="d3audit_")
    try:
        work = os.path.join(tmp, "unit")
        os.makedirs(work)
        retail = os.path.join(REPO, "Empire", "Inquisitor")
        for name in os.listdir(retail):
            path = os.path.join(retail, name)
            if os.path.isfile(path) and name.endswith((".g", ".scene", ".t")):
                shutil.copyfile(path, os.path.join(work, name))
        # the retail bundle is consistent: no findings at all
        _facts, clean = auditmod.audit(work)
        assert clean == [], clean

        scene = os.path.join(work, "character_empire_inquisitor.scene")
        text = open(scene, encoding="latin-1").read()
        stale = text.replace('attr "material0_triangles_num" "250"',
                             'attr "material0_triangles_num" "537"', 1)
        assert stale != text
        open(scene, "w", encoding="latin-1").write(stale)
        facts, findings = auditmod.audit(work)
        notes = facts.get("notes") or []
        assert any("537" in n and "250" in n for n in notes), notes
        assert findings == [], findings

        # a gobj pointing past the end of the mesh is worse than a wrong count
        text2 = text.replace('"resources\\characters\\empire\\inquisitor\\'
                             'character_empire_inquisitor.g" 1',
                             '"resources\\characters\\empire\\inquisitor\\'
                             'character_empire_inquisitor.g" 7', 1)
        assert text2 != text
        open(scene, "w", encoding="latin-1").write(text2)
        _facts, findings = auditmod.audit(work)
        assert any("past the end" in f or "points at part" in f
                   for f in findings), findings
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_audit_reports_an_ac_that_names_a_folder_the_game_lacks():
    """A generated `*_lod.ac` invents a resource directory no install has.

    The engine does not fall back when an `.ac` names a path into a folder the
    game does not have — the load stops with a missing-file dialog naming the
    pack.  Deriving that path from a `*_lod` stem instead of the unit's own
    folder produces exactly that (`resources\\characters\\
    character_empire_angel_lod`, seen in a shipped mod pack), while every file
    it names sits in the unit folder.  The folder a bundle is *stored* in is no
    guide (an export lands in a temp or `out` directory), so the anchor is the
    resource folder the `.scene` itself names.
    """
    tmp = tempfile.mkdtemp(prefix="d3auditac_")
    try:
        work = os.path.join(tmp, "out")
        os.makedirs(work)
        retail = os.path.join(REPO, "Empire", "Inquisitor")
        for name in os.listdir(retail):
            path = os.path.join(retail, name)
            if os.path.isfile(path) and name.endswith((".g", ".scene", ".ac",
                                                       ".a", ".t")):
                shutil.copyfile(path, os.path.join(work, name))
        _facts, clean = auditmod.audit(work)
        assert clean == [], clean

        lod_ac = os.path.join(work, "character_empire_inquisitor_lod.ac")
        bad = ("// ANIMATION CONFIGURATION file\n"
               "version 0.2\n"
               'state "Idle"\n'
               "{\n"
               'file "resources\\characters\\'
               'character_empire_inquisitor_lod\\'
               'character_empire_inquisitor_baseanims.a";\n'
               "frame0 0;\n"
               "frame1 63;\n"
               "fps 30.000000;\n"
               "priority 256;\n"
               "flags 1;\n"
               'meshfile "resources\\characters\\'
               'character_empire_inquisitor_lod\\'
               'character_empire_inquisitor_lod.g";\n'
               "}\n")
        open(lod_ac, "w", encoding="latin-1").write(bad)
        _facts, findings = auditmod.audit(work)
        folder_hits = [f for f in findings if "no folder" in f]
        assert len(folder_hits) == 1, findings
        assert "character_empire_inquisitor_lod" in folder_hits[0]
        assert folder_hits[0].startswith("character_empire_inquisitor_lod.ac"), \
            folder_hits[0]

        # the same states pointed at the unit's real folder are not reported
        open(lod_ac, "w", encoding="latin-1").write(
            bad.replace("character_empire_inquisitor_lod\\",
                        "empire\\inquisitor\\"))
        _facts, findings = auditmod.audit(work)
        assert not [f for f in findings if "no folder" in f], findings
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_same_motion_ignores_only_the_round_trip_noise():
    """A rebuilt `.a` re-quantises every keyframe; that is not an edit.

    The export compares the motion and keeps the donor's own bytes when nothing
    moved — otherwise a re-export that changed no pose still writes a file that
    differs from the shipped one in the last bits of 300 frames x 27 bones.
    """
    raw = open(os.path.join(REPO, "Empire", "Inquisitor",
                            "character_empire_inquisitor_baseanims.a"), "rb").read()
    left = animmod.parse_anim(raw)
    right = animmod.parse_anim(raw)
    assert animmod.same_motion(left, right)
    # the noise his export carried: one float, last bit
    frames = list(right.bones[0].frames)
    frames[0] = tuple(x + 1e-6 for x in frames[0])
    right.bones[0].frames = frames
    assert animmod.same_motion(left, right)
    # a real pose change is not noise
    frames[0] = tuple(x + 0.25 for x in frames[0])
    right.bones[0].frames = frames
    assert not animmod.same_motion(left, right)
    # ...and neither is a different structure
    right = animmod.parse_anim(raw)
    right.bones[1].name = "NotABone"
    assert not animmod.same_motion(left, right)


def test_an_unedited_animation_is_written_back_byte_for_byte():
    """Export the Inquisitor without touching the rig: `.a` and `.ac` must not
    change at all.  A near-copy of the animation is one more file that differs
    from retail for no reason, and nothing in the `.g` needs it rewritten."""
    tmp = tempfile.mkdtemp(prefix="d3animsame_")
    try:
        work = os.path.join(tmp, "unit")
        os.makedirs(work)
        for name in ("character_empire_inquisitor.g",
                     "character_empire_inquisitor_baseanims.a",
                     "character_empire_inquisitor.ac",
                     "character_empire_inquisitor.scene",
                     "character_empire_inquisitor.t"):
            shutil.copyfile(os.path.join(REPO, "Empire", "Inquisitor", name),
                            os.path.join(work, name))
        gl = os.path.join(tmp, "character_empire_inquisitor.gltf")
        climod._export_gl(os.path.join(work, "character_empire_inquisitor.g"),
                          os.path.join(work,
                                       "character_empire_inquisitor_baseanims.a"),
                          gl, None, quiet=True)
        out = os.path.join(tmp, "out")
        climod._export(gl, out, 0, anim=True, quiet=True, donor_dir=work)
        for name in ("character_empire_inquisitor_baseanims.a",
                     "character_empire_inquisitor.ac"):
            retail = open(os.path.join(REPO, "Empire", "Inquisitor", name),
                          "rb").read()
            got = open(os.path.join(out, name), "rb").read()
            assert got == retail, name
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


def test_png_to_t_roundtrip():
    """A Blender re-saved .png must become a loadable native .t: DXT1 pixels,
    a full mip chain sized the GM way (each mip ``w*h*0.5`` bytes, no 4x4
    block padding), so a 128x1024 blade is 86 KiB and not 683."""
    import zlib
    from d3tool import texture as texmod

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    w, h = 8, 8
    raw = b"".join(b"\x00" + b"".join(
        bytes([(x * 32) % 256, (y * 32) % 256, 128, 255])
        for x in range(w)) for y in range(h))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    t = texmod.png_to_t(png)
    ti = texmod.parse_t(t)
    assert (ti.width, ti.height) == (w, h)
    assert ti.gm_format == 6 and ti.fourcc == b"DXT1", (ti.gm_format, ti.fourcc)
    assert ti.mip_count == 4                       # 8, 4, 2, 1
    # 32 + 8 + 2 + int(1*0.5) bytes: the container's own accounting
    assert len(ti.payload) == 32 + 8 + 2 + 0 == ti.payload_size()
    assert len(t) == 59 + 42

    # alpha that actually varies goes to DXT5 instead, so a cut-out atlas
    # keeps its transparency
    raw = b"".join(b"\x00" + b"".join(
        bytes([(x * 32) % 256, 64, 128, (y * 32) % 256])
        for x in range(w)) for y in range(h))
    png_a = (b"\x89PNG\r\n\x1a\n"
             + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
             + chunk(b"IDAT", zlib.compress(raw))
             + chunk(b"IEND", b""))
    ti_a = texmod.parse_t(texmod.png_to_t(png_a))
    assert ti_a.fourcc == b"DXT5" and ti_a.gm_format == 8, ti_a.fourcc


def _a_painted_tga(width=16, height=16, bpp=24, name="order_helmet.tga"):
    """A texture the way an artist hands one over: an uncompressed TGA.

    TGA is what the `.g` itself spells its material as, so it is the format a
    new atlas most often arrives in — and the one the engine cannot read.
    """
    px = bytearray()
    for y in range(height):
        for x in range(width):
            px += bytes(((x * 16) % 256, (y * 16) % 256, 96))
            if bpp == 32:
                px += b"\xff"
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0,
                         width, height, bpp, 0x20)
    return header + bytes(px)


def test_a_painted_texture_decodes_into_a_native_t():
    """A TGA/BMP the artist supplies must become a `.t` the engine can load.

    Neither format is readable by the GM engine, and a part whose atlas stayed
    a `.png`/`.tga` simply loads with no texture.  The export has to decode
    the pixels and block-compress them, with the container's own mip
    accounting.
    """
    w, h = 16, 16
    ti = texmod.parse_t(texmod.tga_to_t(_a_painted_tga(w, h)))
    assert (ti.width, ti.height) == (w, h)
    assert ti.fourcc == b"DXT1" and ti.gm_format == 6, ti.fourcc
    assert ti.mip_count == 5                     # 16, 8, 4, 2, 1
    assert len(ti.payload) == ti.payload_size()
    # the pixels arrive the right way round (BGRA in the file, RGBA out)
    w2, h2, rows = texmod.decode_tga(_a_painted_tga(w, h, bpp=32))
    assert (w2, h2) == (w, h)
    assert rows[0][:4] == bytes((96, 0, 0, 255)), rows[0][:4]
    # alpha that actually varies goes to DXT5, so a cut-out keeps its edges
    cut = bytearray()
    for y in range(8):
        for x in range(8):
            cut += bytes((64, 64, 64, (x * 32) % 256))
    cut_tga = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0,
                          8, 8, 32, 0x20) + bytes(cut)
    assert texmod.parse_t(texmod.tga_to_t(cut_tga)).fourcc == b"DXT5"

    # a bottom-up BMP is the same picture: both origins decode to the same `.t`
    def bmp(top_down):
        stride = ((24 * w + 31) // 32) * 4
        rows = []
        for y in range(h):
            row = bytearray()
            for x in range(w):
                row += bytes(((x * 16) % 256, (y * 16) % 256, 96))
            row += b"\0" * (stride - len(row))
            rows.append(bytes(row))
        body = b"".join(rows if top_down else list(reversed(rows)))
        info = struct.pack("<IiiHHIIiiII", 40, w, -h if top_down else h,
                           1, 24, 0, len(body), 2835, 2835, 0, 0)
        return (struct.pack("<2sIHHI", b"BM", 14 + 40 + len(body), 0, 0,
                            14 + 40) + info + body)

    assert texmod.bmp_to_t(bmp(True)) == texmod.bmp_to_t(bmp(False))
    assert texmod.bmp_to_t(bmp(True)) == texmod.tga_to_t(_a_painted_tga(w, h))
    # an image this tool cannot read says so instead of writing a broken `.t`
    try:
        texmod.image_to_t(b"\xff\xd8\xff\xe0" + b"0" * 64, "paint.jpg")
    except ValueError as exc:
        assert "JPEG" in str(exc), exc
    else:                                        # pragma: no cover
        raise AssertionError("a JPEG was accepted as a texture")


def test_a_texture_that_is_not_a_power_of_two_still_encodes():
    """An atlas painted at 300x150 must not kill the mip chain.

    The down-sampler walked the *input* width by two, so every odd-sized row
    wrote one pixel past the end of the level it was building.
    """
    ti = texmod.parse_t(texmod.tga_to_t(_a_painted_tga(15, 9)))
    assert (ti.width, ti.height) == (15, 9)
    assert len(ti.payload) == ti.payload_size()


def test_the_texture_add_step_writes_the_t_the_g_will_name():
    """``texture add``: a painted atlas in, the engine's ``.t`` out.

    This is the "add the texture too" half of modelling a new part: the file
    lands next to the unit under the stem the `.g`/`.scene` will carry as
    ``<stem>.tga``, in the only container the engine reads.
    """
    tmp = tempfile.mkdtemp(prefix="d3texadd_")
    try:
        src = os.path.join(tmp, "order_helmet_of_the_order.tga")
        open(src, "wb").write(_a_painted_tga(32, 32))
        out_dir = os.path.join(tmp, "unit")
        code = climod._texture_add(src, out_dir)
        assert code == 0
        written = os.path.join(out_dir, "order_helmet_of_the_order.t")
        assert os.path.isfile(written), sorted(os.listdir(out_dir))
        ti = texmod.parse_t(open(written, "rb").read())
        assert (ti.width, ti.height, ti.fourcc) == (32, 32, b"DXT1")
        # an explicit name wins, so a texture can be renamed to what the
        # material calls it
        code = climod._texture_add(src, out_dir, "helmet_neck")
        assert os.path.isfile(os.path.join(out_dir, "helmet_neck.t"))
        # a file that is not an image is refused, not written as garbage
        bad = os.path.join(tmp, "notes.txt")
        open(bad, "w").write("hello")
        assert climod._texture_add(bad, out_dir) == 1
        # a name the unit already ships is refused: the export prefers the
        # shipped .dds/.t by stem, so overwriting it would be undone by the
        # very next export and the painted atlas would vanish
        open(os.path.join(out_dir, "character_x.dds"), "wb").write(b"DDS ")
        assert climod._texture_add(src, out_dir, "character_x") == 1
        assert not os.path.isfile(os.path.join(out_dir, "character_x.t"))
        assert climod._texture_add(src, out_dir, "character_x", True) == 0
        assert os.path.isfile(os.path.join(out_dir, "character_x.t"))
        # adding the same texture twice is a no-op, not an error
        assert climod._texture_add(src, out_dir, "helmet_neck") == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_export_writes_the_atlas_a_new_part_names():
    """A glTF that names a texture only the artist has must still ship it.

    ``_export_textures`` is what turns the glTF's image URI into the `.t` the
    ``.g`` points at; it has to decode a painted TGA as readily as it converts
    a shipped DDS, or the new part exports without an atlas.
    """
    tmp = tempfile.mkdtemp(prefix="d3exptex_")
    try:
        gltf_path = os.path.join(tmp, "unit.gltf")
        open(os.path.join(tmp, "order_helmet.tga"), "wb").write(
            _a_painted_tga(8, 8))
        with open(gltf_path, "w", encoding="utf-8") as fh:
            json.dump({"images": [{"uri": "order_helmet.tga"}],
                       "materials": [], "textures": []}, fh)
        out = os.path.join(tmp, "out")
        os.makedirs(out)
        first = climod._export_textures(gltf_path, out, "unit", True)
        assert first == "order_helmet.t", first
        ti = texmod.parse_t(open(os.path.join(out, first), "rb").read())
        assert (ti.width, ti.height) == (8, 8)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_raw_texture_is_recompressed_and_a_packed_one_is_left_alone():
    """The 683 KiB problem: a `.t` holding raw A8R8G8B8 of an atlas the game
    ships as an 86 KiB DXT1.  Same pixels, eight times the size, and the file
    listing reads like the wrong texture."""
    from d3tool import texture as texmod

    w, h = 32, 32
    rows = [bytes(v for x in range(w)
                  for v in ((x * 8) % 256, (y * 8) % 256, 64, 255))
            for y in range(h)]
    raw = bytearray()
    cur, cw, ch = rows, w, h
    while cw >= 1 and ch >= 1:
        for row in cur:
            for x in range(cw):
                o = 4 * x
                raw += bytes((row[o + 2], row[o + 1], row[o], row[o + 3]))
        cur, cw, ch = texmod._downsample_rows(cur, cw, ch), cw >> 1, ch >> 1
    big = texmod.write_t(texmod.TextureInfo(
        width=w, height=h, mip_count=6, gm_format=4, payload=bytes(raw)))
    assert texmod.parse_t(big).fourcc is None          # raw RGBA, not DXT
    assert len(big) > 4000

    small = texmod.compress_t(big)
    assert small is not None and len(small) < len(big) // 3, (len(big), small and len(small))
    ti = texmod.parse_t(small)
    assert ti.fourcc == b"DXT1" and ti.gm_format == 6
    assert (ti.width, ti.height, ti.mip_count) == (w, h, 6)
    assert len(ti.payload) == ti.payload_size()
    # already compressed: nothing to do, and the file is returned untouched
    assert texmod.compress_t(small) is None
    assert texmod.compress_t(b"junk") is None

    # a flat block needs no search: one colour, every index on it
    block = texmod._bc1_block([(255, 0, 0, 255)] * 16)
    c0, c1, bits = struct.unpack("<HHI", block)
    assert c0 == c1 == texmod._rgb565(255, 0, 0) and bits == 0


def test_the_export_carries_a_compressed_texture_not_a_raw_one():
    """End to end: a folder that holds a raw-pixel atlas exports a DXT1 `.t`."""
    tmp = tempfile.mkdtemp(prefix="d3carry_")
    try:
        work = os.path.join(tmp, "unit")
        os.makedirs(work)
        for name in ("character_empire_inquisitor.g",
                     "character_empire_inquisitor_baseanims.a",
                     "character_empire_inquisitor.ac",
                     "character_empire_inquisitor.scene"):
            shutil.copyfile(os.path.join(REPO, "Empire", "Inquisitor", name),
                            os.path.join(work, name))
        # a raw-pixel stand-in for the unit's atlas: 64x64, DXT1 after export
        w = h = 64
        rows = [bytes(v for x in range(w)
                      for v in ((x * 4) % 256, (y * 4) % 256, 32, 255))
                for y in range(h)]
        payload = bytearray()
        cur, cw, ch = rows, w, h
        while cw >= 1 and ch >= 1:
            for row in cur:
                for x in range(cw):
                    o = 4 * x
                    payload += bytes((row[o + 2], row[o + 1], row[o], row[o + 3]))
            cur, cw, ch = texmod._downsample_rows(cur, cw, ch), cw >> 1, ch >> 1
        big = texmod.write_t(texmod.TextureInfo(
            width=w, height=h, mip_count=7, gm_format=4, payload=bytes(payload)))
        open(os.path.join(work, "character_empire_inquisitor.t"), "wb").write(big)

        g = os.path.join(work, "character_empire_inquisitor.g")
        gl = os.path.join(tmp, "x.gltf")
        climod._export_gl(g, os.path.join(work,
                                          "character_empire_inquisitor_baseanims.a"),
                          gl, None, quiet=True)
        out = os.path.join(tmp, "out")
        climod._export(gl, out, 0, anim=True, quiet=True, donor_dir=work)
        carried = os.path.join(out, "character_empire_inquisitor.t")
        assert os.path.isfile(carried)
        got = open(carried, "rb").read()
        assert len(got) < len(big) // 2, (len(big), len(got))
        assert texmod.describe(carried, True) == "64x64 DXT1"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_anim_track_without_parent_uses_zero_length():
    """The geo2011 writer stores a track's parent as ``[u32 len][name + NUL]``
    and writes no bytes at all when the parent is empty; a rebuilt stream must
    follow that grammar (and still parse back)."""
    frames = [(0.0, 0.0, 0.0, 1.0, 0.1, 0.2, 0.3)] * 3
    anim = animmod.build_anim([("Root", "", frames), ("Bip01", "Root", frames)], 3)
    raw = animmod.write_anim(anim)
    # first record: name_len == 5 ("Root\0"), parent_len == 0, frames == 3
    nl, pl, nf = struct.unpack_from("<3I", raw, 20)
    assert (nl, pl, nf) == (5, 0, 3), (nl, pl, nf)
    # the second record's header follows 16 + 5 + 3*28 bytes later
    o = 20 + 16 + 5 + 3 * 28
    assert struct.unpack_from("<3I", raw, o) == (6, 5, 3)
    back = animmod.parse_anim(raw)
    assert [(b.name, b.parent) for b in back.bones] == [("Root", ""), ("Bip01", "Root")]
    assert animmod.write_anim(back) == raw


def test_anim_build_takes_fps_and_track_scale():
    frames = [(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)] * 2
    anim = animmod.build_anim([("Root", "Scene Root", frames)], 2, fps=30, scale=0.0075)
    raw = animmod.write_anim(anim)
    assert struct.unpack_from("<5I", raw, 0)[4] == 30
    assert struct.unpack_from("<f", raw, 20 + 12)[0] == struct.unpack("<f", struct.pack("<f", 0.0075))[0]


def test_compound_part_keeps_tangent_stream():
    """A Max-exported part with the `tangent` property keeps an
    ``8 + vertex_count * 24`` byte stream after its bone table."""
    p = os.path.join(REPO, "Empire", "Hierophant",
                     "character_empire_hierophant.g")
    data = open(p, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    part = mesh.parts[4]
    tangent = b"TANGENT!" + bytes(range(24)) * len(part.vertices)
    patched = dict(part.attrs)
    patched["tangent"] = "1"
    block = gfile._part_block(gfile.MeshPart(
        name=part.name, vertex_count=part.vertex_count,
        tri_count=part.tri_count, vertices=part.vertices,
        indices=part.indices, bones=part.bones,
        weights_on_vertex=part.weights_on_vertex, morph=part.morph,
        material_diffuse=part.material_diffuse, lm_uv=part.lm_uv,
        lightmap=part.lightmap, vertex_magic=part.vertex_magic,
        attrs=patched,
        attr_items=[(k, patched[k]) for k, _v in part.attr_items]
        + [("tangent", "1")],
        part_prefix=part.part_prefix, part_tail=tangent))
    parts, consumed = gfile._parse_compound_parts(block)
    assert len(parts) == 1 and consumed == len(block)
    assert parts[0].part_tail == tangent, len(parts[0].part_tail)
    assert gfile._part_block(parts[0]) == block


# --------------------------------------------------------------------------- #
#  reverse .a parity (donor-assisted rebuild)
# --------------------------------------------------------------------------- #
def _original_a_streams(folder: str):
    """Parse every structurally-valid `.a` of a unit folder."""
    out = {}
    for p in sorted(glob.glob(os.path.join(folder, "*.a"))):
        a = animmod.parse_anim(open(p, "rb").read())
        if not a.raw:
            out[os.path.basename(p)] = a
    return out


def test_anim_record_preamble_is_name_length_pair():
    """Every original `.a` record preamble starts with the NUL-terminated
    string lengths [len(name)+1][len(parent)+1] and the per-record frame
    count; the header length field covers everything except the trailing
    block.  (The trailing time-step float varies per stream — 0.01 is the
    corpus mode, 0.02 matches the dis3tool reference exports a rebuild
    writes; adopted donor records keep their own value either way.)"""
    checked = 0
    for p in sorted(glob.glob(os.path.join(REPO, "*", "*", "*.a"))):
        a = animmod.parse_anim(open(p, "rb").read())
        if a.raw or not a.bones:
            continue
        for b in a.bones:
            a_len, p_len, nf = struct.unpack_from("<III", b.preamble)
            assert (a_len, p_len) == (len(b.name) + 1, len(b.parent) + 1), \
                f"{os.path.basename(p)}:{b.name} preamble {b.preamble!r}"
            assert nf == len(b.frames), \
                f"{os.path.basename(p)}:{b.name} preamble nf {nf}"
        header_len = struct.unpack_from("<I", a.header, 4)[0]
        body = len(animmod.write_anim(a)) - 8 - len(a.trailing)
        assert header_len == body, \
            f"{os.path.basename(p)} header len {header_len} != {body}"
        checked += len(a.bones)
    assert checked > 6000, f"corpus shrank: {checked} records"


def test_animation_from_gltf_matches_original_a():
    """The donor-assisted rebuild reproduces several original `.a` files
    byte-for-byte: a single-stream skeleton (rod-1), a morph-trailing
    reconstruction (orc) and a positional out-of-array recovery
    (wildboar)."""
    cases = (
        ("Empire", "Rod-1", "character_empire_rod-1_baseanims.a"),
        ("Neutrals", "Orc", "character_neutrals_orc_baseanims.a"),
        ("Neutrals", "Wildboar", "character_neutrals_wildboar.a"),
    )
    for group, unit, a_name in cases:
        folder = os.path.join(REPO, group, unit)
        stem = a_name[:-2].replace("_baseanims", "")
        m = gltf.load_gltf(os.path.join(folder, stem + ".gltf"))
        donor = animmod.parse_anim(open(os.path.join(folder, a_name),
                                        "rb").read())
        assert not donor.raw, f"{a_name} must parse structurally"
        rebuilt = gltf.animation_from_gltf(m, donor=donor)
        got = animmod.write_anim(rebuilt)
        want = open(os.path.join(folder, a_name), "rb").read()
        assert got == want, f"{a_name}: rebuilt {len(got)}B != {len(want)}B"


def test_reverse_export_splits_concatenated_angel_streams():
    """Angel's `.ac` names five `.a` files; dis3tool exported them as ONE
    concatenated glTF animation.  The reverse export must slice the
    Idle stream back out of it — byte-for-byte the original `_idle.a`."""
    folder = os.path.join(REPO, "Empire", "Angel")
    stem = "character_empire_angel"
    with tempfile.TemporaryDirectory() as d:
        gltf = os.path.join(folder, stem + ".gltf")
        if not os.path.isfile(gltf):
            # The Angel folder ships no dis3tool reference; build the same
            # concatenated animation the reference holds (every stream the
            # `.ac` names, in one glTF animation) and slice it back out.
            gltf = os.path.join(d, stem + ".gltf")
            assert climod._run(["import", os.path.join(folder, stem + ".g"),
                                "-o", gltf]) == 0, "import must succeed"
            assert os.path.isfile(os.path.join(d, stem + ".bin"))
        rev = os.path.join(d, "rev")
        # the donor folder is named explicitly: discovering it by walking up
        # from the temp dir depends on what else happens to live in /tmp
        climod._export(gltf, rev, 0, anim=True, quiet=True, donor_dir=folder)
        got = open(os.path.join(rev, stem + "_idle.a"), "rb").read()
    want = open(os.path.join(folder, stem + "_idle.a"), "rb").read()
    assert got == want, f"angel idle: {len(got)}B != {len(want)}B"


def test_donorless_reverse_rebuilds_valid_compound_g():
    """The Leader variant sets ship a reference glTF but no original `.g`.
    The donorless reverse must still produce a *structurally valid*
    compound `.g` (all parts recoverable, per-part materials carried) and
    the full glTF -> GM -> glTF cycle must close EXACTLY for a unit whose
    authoring data is fully expressible (Wolfsnow)."""
    folder = os.path.join(REPO, "Empire", "Leader-Archmage")
    stem = "character_empire_leader-archmage_set1"
    with tempfile.TemporaryDirectory() as d:
        climod._export(os.path.join(folder, stem + ".gltf"), d, 0,
                       anim=True, quiet=True)
        mesh = gfile.parse_geometry_file(
            open(os.path.join(d, stem + ".g"), "rb").read())
        assert not mesh.parse_error
        assert len(mesh.parts) == 8, f"expected 8 parts, got {len(mesh.parts)}"
        assert not mesh.trailing, "parts must consume the trailing block"
        for p in mesh.parts:
            assert p.attrs.get("material0_diffuse"), \
                f"{p.name}: donorless part must carry material0_diffuse"
        anim = climod._find_animation_for_geometry(
            os.path.join(d, stem + ".g"))
        gt, _bt = climod._export_gl(
            os.path.join(d, stem + ".g"), anim,
            os.path.join(d, "cycle.gltf"), texture=None, quiet=True)
        mine = json.load(open(gt))
    ref = json.load(open(os.path.join(folder, stem + ".gltf")))
    assert len(mine.get("materials", [])) == len(ref.get("materials", []))
    assert [i.get("uri") for i in mine.get("images", [])] == \
        [i.get("uri") for i in ref.get("images", [])]


def test_donorless_reverse_adopts_main_material_from_glTF():
    """Wolfsnow's texture name differs from the file base and no `.g`
    donor exists; the reverse must take `material0_diffuse` from the main
    primitive's glTF material so the cycle closes byte-for-byte."""
    folder = os.path.join(REPO, "Neutrals", "Wolfsnow")
    stem = "character_neutrals_wolfsnow"
    with tempfile.TemporaryDirectory() as d:
        climod._export(os.path.join(folder, stem + ".gltf"), d, 0,
                       anim=True, quiet=True)
        attrs, _ = gfile.parse_attributes(
            open(os.path.join(d, stem + ".g"), "rb").read())
        assert attrs["material0_diffuse"] == "character_neutral_wolfsnow.tga"
        gt, bt = climod._export_gl(
            os.path.join(d, stem + ".g"),
            climod._find_animation_for_geometry(
                os.path.join(d, stem + ".g")),
            os.path.join(d, "cycle", stem + ".gltf"),
            texture=None, quiet=True)
        bin_mine = open(bt, "rb").read()
        mine = json.load(open(gt))
    assert bin_mine == \
        open(os.path.join(folder, stem + ".bin"), "rb").read()
    ref = json.load(open(os.path.join(folder, stem + ".gltf")))
    assert mine["images"] == ref["images"]
    assert mine["materials"] == ref["materials"]


def test_import_auto_detects_animation_without_minus_a():
    """`d3tool import <g>` with no `-a` resolves the animation itself via
    the unit's `.ac` (concatenating every stream it names) and the output
    matches the dis3tool reference byte-for-byte (bin identical, JSON
    equal modulo the generator signature)."""
    import struct as _struct
    unit = os.path.join(REPO, "Empire", "Angel")
    stem = "character_empire_angel"
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, stem + ".gltf")
        r = climod._run(["import", os.path.join(unit, stem + ".g"),
                         "-o", out])
        assert r == 0, "import must succeed without -a"
        ref_bin = os.path.join(unit, stem + ".bin")
        ref_gltf = os.path.join(unit, stem + ".gltf")
        if not (os.path.isfile(ref_bin) and os.path.isfile(ref_gltf)):
            # This folder ships no dis3tool reference glTF.  The invariant
            # that matters is still checkable: auto-detection must find the
            # `.ac` streams and the reverse export must hand every stream
            # back byte-identically.
            assert os.path.getsize(os.path.join(d, stem + ".bin")) > 0
            rev = os.path.join(d, "rev")
            # explicit donor: guessing it by walking up from the temp dir
            # depends on unrelated contents of /tmp
            climod._export(out, rev, 0, anim=True, quiet=True, donor_dir=unit)
            for name in sorted(os.listdir(unit)):
                if not name.endswith(".a"):
                    continue
                got = open(os.path.join(rev, name), "rb").read()
                want = open(os.path.join(unit, name), "rb").read()
                assert got == want, f"{name}: {len(got)}B != {len(want)}B"
            return
        bin_ok = open(os.path.join(d, stem + ".bin"), "rb").read() == \
            open(ref_bin, "rb").read()
        assert bin_ok, "auto-animation export .bin must match the reference"

        def f32walk(r_, g_, path=""):
            bad = []
            if isinstance(r_, dict) and isinstance(g_, dict):
                for k in r_.keys() & g_.keys():
                    bad += f32walk(r_[k], g_[k], path + "/" + str(k))
            elif isinstance(r_, list) and isinstance(g_, list):
                for i, (x, y) in enumerate(zip(r_, g_)):
                    bad += f32walk(x, y, f"{path}/{i}")
            elif path == "/asset/generator":
                return bad
            elif isinstance(r_, (int, float)) and not isinstance(r_, bool):
                if _struct.pack("<f", r_) != _struct.pack("<f", g_):
                    bad.append(path)
            elif r_ != g_:
                bad.append(path)
            return bad

        ref = json.load(open(ref_gltf))
        mine = json.load(open(out))
        assert not f32walk(ref, mine), "JSON must be f32-equal to the reference"


def test_import_export_route_legacy_calls_by_extension():
    """`import` = GM → glTF, `export` = glTF → GM; a mismatched extension
    routes to the right command with a notice instead of failing."""
    unit = os.path.join(REPO, "Neutrals", "AirElemental")
    stem = "character_neutrals_airelemental"
    with tempfile.TemporaryDirectory() as d:
        # legacy/confused `import <file.gltf>` -> routes to export (GM out)
        out = os.path.join(d, "rev")
        assert climod._run(["import", os.path.join(unit, stem + ".gltf"),
                            "-o", out]) == 0
        assert os.path.isfile(os.path.join(out, stem + ".g"))
        # legacy/confused `export <file.g>` -> routes to import (glTF out)
        out2 = os.path.join(d, "fwd.gltf")
        assert climod._run(["export", os.path.join(unit, stem + ".g"),
                            "-o", out2]) == 0
        assert os.path.isfile(os.path.join(d, "fwd.bin"))

# --------------------------------------------------------------------------- #
#  .vdpack (the one-file form of a unit bundle)
# --------------------------------------------------------------------------- #

def test_vdpack_layout_is_the_documented_one():
    """The archive must be the byte layout the port's writer produced."""
    from d3tool import vdpack
    files = [("Empire\\Hierophant\\character_empire_hierophant.g", b"GEO"),
             ("Empire\\Hierophant\\t.t", b"XY")]
    blob = vdpack.build_vdpack(files)
    name0 = files[0][0].encode() + b"\0"
    name1 = files[1][0].encode() + b"\0"
    index_size = 4 + (4 + len(name0) + 12) + (4 + len(name1) + 12)
    want = struct.pack("<I", 2)
    want += struct.pack("<I", len(name0)) + name0
    want += struct.pack("<III", 3, index_size, 0)
    want += struct.pack("<I", len(name1)) + name1
    want += struct.pack("<III", 2, index_size + 3, 0)
    want += b"GEO" + b"XY"
    assert blob == want, "vdpack header/payload layout changed"
    pack = vdpack.parse_vdpack(blob)
    assert not pack.parse_error
    assert pack.names() == [n for n, _ in files]
    assert pack.index_size == index_size
    assert pack.payload(0) == b"GEO" and pack.payload("Empire/Hierophant/t.t") == b"XY"
    # rebuilding from what was parsed reproduces the archive exactly
    again = vdpack.build_vdpack([(e.name, pack.payload(e)) for e in pack.entries])
    assert again == blob


def test_vdpack_roundtrip_of_a_real_unit_folder():
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    files = vdpack.pack_folder(folder)
    assert len(files) >= 10, "the sample unit folder should be complete"
    blob = vdpack.build_vdpack(files)
    pack = vdpack.parse_vdpack(blob)
    assert not pack.parse_error, pack.parse_error
    assert len(pack) == len(files)
    assert pack.summary()["payload_bytes"] == sum(len(d) for _, d in files)
    with tempfile.TemporaryDirectory() as d:
        written = vdpack.extract_vdpack(pack, d)
        assert len(written) == len(files)
        for name, data in files:
            back = open(os.path.join(d, *name.split("/")), "rb").read()
            assert back == data, f"{name} changed in the pack round trip"


def test_vdpack_rejects_damaged_archives():
    from d3tool import vdpack
    good = vdpack.build_vdpack([("a.g", b"1234"), ("b/c.t", b"56")])
    assert not vdpack.parse_vdpack(good).parse_error
    # 1) too short, 2) entry count that cannot fit, 3) truncated index
    assert vdpack.parse_vdpack(b"\x01").parse_error
    assert vdpack.parse_vdpack(struct.pack("<I", 10 ** 6)).parse_error
    assert vdpack.parse_vdpack(good[:10]).parse_error
    # 4) a name that is not NUL terminated
    bad = bytearray(good)
    bad[4 + 4 + len("a.g")] = ord("a")     # overwrite the NUL of the first name
    assert "not NUL" in vdpack.parse_vdpack(bytes(bad)).parse_error
    # 5) a payload that runs past the end of the file
    bad = bytearray(good)
    struct.pack_into("<I", bad, 12, 999)   # first entry's size field
    assert vdpack.parse_vdpack(bytes(bad)).parse_error
    # 6) two entries pointing at the same bytes
    overlap = struct.pack("<I", 2)
    for name in ("a", "b"):
        raw = name.encode() + b"\0"
        overlap += struct.pack("<I", len(raw)) + raw
        overlap += struct.pack("<III", 2, 40, 0)   # both point at the same bytes
    overlap += b"\0" * 4
    err = vdpack.parse_vdpack(overlap).parse_error
    assert "overlap" in err, err


def test_vdpack_refuses_paths_that_escape_the_root():
    from d3tool import vdpack
    for name in ("..\\evil.g", "../evil.g", "\\abs.g", "/abs.g", "c:\\x.g", ""):
        try:
            vdpack.build_vdpack([(name, b"x")])
        except ValueError:
            pass
        else:
            raise AssertionError(f"{name!r} was accepted as an archive path")
    # an archive is untrusted input too: extracting must not write outside
    raw_name = b"..\\evil.g\x00"
    index_size = 4 + 4 + len(raw_name) + 12
    blob = struct.pack("<I", 1) + struct.pack("<I", len(raw_name)) + raw_name
    blob += struct.pack("<III", 4, index_size, 0) + b"evil"
    pack = vdpack.parse_vdpack(blob)
    with tempfile.TemporaryDirectory() as d:
        try:
            vdpack.extract_vdpack(pack, os.path.join(d, "out"))
        except ValueError:
            pass
        else:
            raise AssertionError("extraction followed a .. path")
        assert not os.path.exists(os.path.join(d, "evil.g"))


def test_vdpack_cli_pack_list_extract():
    import contextlib
    import io
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    stem = "character_neutrals_airelemental"
    with tempfile.TemporaryDirectory() as d, \
            contextlib.redirect_stdout(io.StringIO()):
        archive = os.path.join(d, "air.vdpack")
        assert climod._run(["vdpack", "pack", folder, "-o", archive]) == 0
        assert os.path.isfile(archive)
        assert climod._run(["vdpack", "list", archive]) == 0
        out = os.path.join(d, "unpacked")
        assert climod._run(["vdpack", "extract", archive, "-o", out]) == 0
        for name in (stem + ".g", stem + ".ac"):
            a = open(os.path.join(folder, name), "rb").read()
            b = open(os.path.join(out, name), "rb").read()
            assert a == b, f"{name} changed through the CLI pack/extract cycle"
        # extracting twice is refused unless forced
        assert climod._run(["vdpack", "extract", archive, "-o", out]) == 1
        assert climod._run(["vdpack", "extract", archive, "-o", out,
                            "--force"]) == 0
        # a damaged archive is reported, not half-extracted
        damaged = os.path.join(d, "damaged.vdpack")
        blob = bytearray(open(archive, "rb").read())
        blob[4:8] = struct.pack("<I", 1)      # name length of 1 -> not NUL'd
        open(damaged, "wb").write(bytes(blob))
        assert climod._run(["vdpack", "list", damaged]) == 1


def test_vdpack_diff_names_what_separates_two_packs():
    """The one thing a modder needs before shipping a pack: which paths it
    adds, which it drops, and which payloads it replaces — the three answers
    `pack_like` acts on, readable without unpacking either archive."""
    from d3tool import vdpack as vmod
    base = vmod.build_vdpack([
        ("index", b"opaque"),
        ("resources\\Unit\\a.g", b"old geometry"),
        ("resources\\Unit\\a.scene", b"scene"),
        ("resources\\Unit\\a.t", b"atlas"),
    ])
    other = vmod.build_vdpack([
        ("index", b"opaque"),
        ("resources\\Unit\\a.g", b"new geometry"),
        ("resources\\Unit\\a.scene", b"scene"),
        ("resources\\Unit\\b.t", b"atlas"),
    ])
    result = vmod.diff_entries(
        list(vmod.parse_vdpack(base).files().items()),
        list(vmod.parse_vdpack(other).files().items()))
    assert result["added"] == ["resources\\Unit\\b.t"]
    assert result["removed"] == ["resources\\Unit\\a.t"]
    assert result["changed"] == ["resources\\Unit\\a.g"]
    assert result["unchanged"] == 2
    assert result["reordered"] is False
    grades = [grade for grade, _ in vmod.shape_verdict(result)]
    assert "bad" in grades          # an added path is the unconfirmed shape
    assert "warn" in grades         # the removal, and it names each path


def test_vdpack_diff_grades_the_confirmed_shape_as_ok():
    """Same paths, different payloads, same order: the one shape with a
    runtime confirmation behind it, and the only one that may read `ok`."""
    from d3tool import vdpack as vmod
    entries = [("resources\\Unit\\a.g", b"x"), ("resources\\Unit\\a.t", b"y")]
    result = vmod.diff_entries(entries, [("resources\\Unit\\a.g", b"xx"),
                                         ("resources\\Unit\\a.t", b"y")])
    verdict = vmod.shape_verdict(result)
    assert verdict and verdict[0][0] == "ok"
    assert result["added"] == [] and result["removed"] == []
    assert result["reordered"] is False
    # a folder and the pack built from it compare clean, separators aside
    folder = [("resources/Unit/a.g", b"xx"), ("resources/Unit/a.t", b"y")]
    same = vmod.diff_entries(entries, folder)
    assert same["added"] == [] and same["removed"] == []


def test_vdpack_diff_cli_reports_and_exits():
    import contextlib
    import io
    from d3tool import vdpack as vmod
    good = vmod.build_vdpack([("index", b"i"), ("resources\\U\\a.g", b"1")])
    bad = vmod.build_vdpack([("index", b"i"), ("resources\\U\\a.g", b"2"),
                             ("resources\\U\\b.g", b"3")])
    with tempfile.TemporaryDirectory() as d:
        one = os.path.join(d, "one.vdpack")
        two = os.path.join(d, "two.vdpack")
        open(one, "wb").write(good)
        open(two, "wb").write(bad)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert climod._run(["vdpack", "diff", one, two]) == 0
        text = buf.getvalue()
        assert "resources\\U\\b.g" in text
        assert "added" in text
        with contextlib.redirect_stdout(io.StringIO()):
            assert climod._run(["vdpack", "diff", one, two, "--strict"]) == 1
            assert climod._run(["vdpack", "diff", one, one]) == 0


def test_vdpack_plan_separates_replacement_from_a_new_path():
    """What `plan` is for: a folder on disk says nothing about the archive
    path it lands on, so a modder can ship a file that replaces one the engine
    already reaches, or one at a path the pack's `index` never lists — and the
    second shape is the one that showed no units at all in game."""
    from d3tool import vdpack as vmod
    base = [("resources\\Characters\\Empire\\Unit\\a.g", b"old"),
            ("resources\\Characters\\Empire\\Unit\\a.scene", b"scene"),
            ("resources\\Characters\\Empire\\Unit\\a.t", b"atlas"),
            ("resources\\Characters\\Empire\\Unit\\Aliases\\x.alias", b"a")]
    folder = [("a.g", b"new"), ("a.scene", b"scene"), ("a.t", b"atlas"),
              ("b.t", b"atlas")]
    plan = vmod.plan_bundle(folder, base)
    assert plan["prefix"] == "resources\\Characters\\Empire\\Unit"
    assert [e["path"] for e in plan["replaced"]] == [
        "resources\\Characters\\Empire\\Unit\\a.g",
        "resources\\Characters\\Empire\\Unit\\a.scene",
        "resources\\Characters\\Empire\\Unit\\a.t"]
    assert [e["path"] for e in plan["added"]] == [
        "resources\\Characters\\Empire\\Unit\\b.t"]
    # the rename targets are the textures the pack already lists in that folder
    assert plan["added"][0]["alternatives"] == ["a"]
    assert plan["missing"] == ["resources\\Characters\\Empire\\Unit\\Aliases\\x.alias"]
    # a clean folder: nothing new, so the confirmed shape
    clean = vmod.plan_bundle(folder[:3], base)
    assert clean["added"] == [] and len(clean["replaced"]) == 3


def test_vdpack_plan_without_a_pack_is_the_loose_layout():
    """No `--like`: the plan can still line the folder up (its own `.scene`
    names the archive path) and print the loose tree, which is the only way to
    ship a path no pack can carry."""
    from d3tool import vdpack as vmod
    folder = [("a.g", b"g"), ("a.t", b"t"),
              ("a.scene", b'name "x"\nmesh "resources/Characters/Empire/Unit/a.g"\n')]
    plan = vmod.plan_bundle(folder)
    assert plan["prefix"] == "resources\\Characters\\Empire\\Unit"
    assert plan["has_base"] is False
    assert plan["added"] == []
    assert [e["kind"] for e in plan["entries"]] == ["loose"] * 3
    assert plan["entries"][0]["path"].startswith(
        "resources\\Characters\\Empire\\Unit\\")
    # no prefix anywhere: a bare listing, never a crash
    bare = vmod.plan_bundle([("a.g", b"g")])
    assert bare["prefix"] == "" and bare["entries"][0]["path"] == "a.g"


def test_vdpack_plan_cli_reports_and_exits():
    import contextlib
    import io
    from d3tool import vdpack as vmod
    base = vmod.build_vdpack([("index", b"i"),
                              ("resources\\U\\a.g", b"1"),
                              ("resources\\U\\a.t", b"2")])
    with tempfile.TemporaryDirectory() as d:
        archive = os.path.join(d, "base.vdpack")
        open(archive, "wb").write(base)
        folder = os.path.join(d, "Unit")
        os.makedirs(folder)
        for name in ("a.g", "a.t"):
            open(os.path.join(folder, name), "wb").write(b"payload")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert climod._run(["vdpack", "plan", folder, "--like", archive]) == 0
            assert climod._run(["vdpack", "plan", folder, "--like", archive,
                                "--strict"]) == 0
            assert climod._run(["vdpack", "plan", folder]) == 0
        text = buf.getvalue()
        assert "replaces  resources\\U\\a.g" in text
        assert "loose layout" in text
        # a file the pack cannot reach flips the verdict and --strict
        open(os.path.join(folder, "b.t"), "wb").write(b"payload")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert climod._run(["vdpack", "plan", folder, "--like", archive]) == 0
            assert climod._run(["vdpack", "plan", folder, "--like", archive,
                                "--strict"]) == 1
            assert climod._run(["vdpack", "plan", os.path.join(d, "nope")]) == 1
        text = buf.getvalue()
        assert "new path  resources\\U\\b.t" in text
        assert "a" in text              # the rename target it suggests
        # --prefix overrides the inference, --json prints the record
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert climod._run(["vdpack", "plan", folder, "--prefix",
                                "resources\\Other", "--json"]) == 0
        assert '"prefix": "resources\\\\Other"' in buf.getvalue()


# --------------------------------------------------------------------------- #
#  tools/dump_extract.py — reading a Windows minidump
# --------------------------------------------------------------------------- #

def _pe_image():
    """The smallest thing that is structurally a PE: header + one section."""
    img = bytearray(0x2000)
    img[0:2] = b"MZ"
    struct.pack_into("<I", img, 0x3C, 0x80)
    img[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", img, 0x84, 0x14C)          # machine i386
    struct.pack_into("<H", img, 0x86, 1)             # one section
    struct.pack_into("<H", img, 0x94, 0xE0)          # SizeOfOptionalHeader
    table = 0x80 + 24 + 0xE0
    img[table:table + 8] = b".text\x00\x00\x00"
    struct.pack_into("<I", img, table + 8, 0x1000)   # VirtualSize
    struct.pack_into("<I", img, table + 12, 0x1000)  # VirtualAddress
    struct.pack_into("<I", img, table + 16, 0x800)   # SizeOfRawData (on disk)
    struct.pack_into("<I", img, table + 20, 0x400)   # PointerToRawData
    img[0x1000:0x1010] = b"\x90" * 16
    return bytes(img)


def _minidump(modules, regions, exception=None, stack_pointer=None):
    """Assemble a minidump carrying exactly the streams the parser reads.

    ``modules`` is ``{name: (base, size, image)}``; ``regions`` the captured
    ranges, laid out the way a real dump lays them out (a table of start/size
    pairs, then the payloads back to back at ``BaseRva``).  Strings sit past
    every fixed-size structure, as in a dump written by the OS.
    """
    names = list(modules)
    strings = [struct.pack("<I", len(n.encode("utf-16-le"))) +
               n.encode("utf-16-le") for n in names]
    nstreams = 3 + (1 if exception else 0)
    system = struct.pack("<HH", 0, 0) + bytes(52)
    module_size = 4 + 108 * len(names)
    memory_size = 16 + 16 * len(regions)
    cursor = 32 + 12 * nstreams
    system_rva = cursor
    module_rva = cursor + len(system)
    memory_rva = module_rva + module_size
    payload_rva = memory_rva + memory_size
    payload_len = sum(len(data) for _start, data in regions)
    cursor = payload_rva + payload_len
    exception_rva = cursor if exception else None
    if exception:
        cursor += 168
    context = bytearray(0x2CC)
    if stack_pointer is not None:
        struct.pack_into("<I", context, 0xC4, stack_pointer)    # esp
    context_rva = cursor
    cursor += len(context)
    strings_rva = cursor

    records = b""
    for index, name in enumerate(names):
        base, size, _image = modules[name]
        name_rva = strings_rva + sum(len(b) for b in strings[:index])
        records += struct.pack("<QIIII", base, size, 0, 0, name_rva) \
            + bytes(52) + bytes(32)
    module_stream = struct.pack("<I", len(names)) + records

    entries = b""
    for start, data in regions:
        entries += struct.pack("<QQ", start, len(data))
    memory_stream = struct.pack("<QQ", len(regions), payload_rva) + entries

    exception_stream = b""
    if exception:
        exception_stream = (struct.pack("<II", 1, 0) +
                            struct.pack("<II", 0xC0000005, 0) +
                            struct.pack("<QQ", 0, exception) +
                            struct.pack("<II", 2, 0) +
                            struct.pack("<QQ", 0xFFFFFFFF, 0) + bytes(13 * 8) +
                            struct.pack("<II", len(context), context_rva))
    streams = [(7, system, system_rva), (4, module_stream, module_rva),
               (9, memory_stream, memory_rva)]
    if exception:
        streams.append((6, exception_stream, exception_rva))
    directory = b""
    for kind, data, rva in streams:
        directory += struct.pack("<III", kind, len(data), rva)
    body = (system + module_stream + memory_stream +
            b"".join(data for _start, data in regions) + exception_stream +
            bytes(context) + b"".join(strings))
    header = b"MDMP" + struct.pack("<IIIIIQ", 0xA793, nstreams, 32, 0, 0, 0)
    return header + directory + body


def test_dump_extract_reads_modules_and_rebuilds_a_pe(tmp_path=None):
    """`tools/dump_extract.py` is the way a protected engine becomes readable:
    the file on disk is encrypted, the process image is not, and the only fix a
    memory image needs is section pointers back to their virtual addresses."""
    import importlib.util
    import subprocess
    path = os.path.join(REPO, "tools", "dump_extract.py")
    spec = importlib.util.spec_from_file_location("dump_extract", path)
    dm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dm)

    pe = _pe_image()
    blob = _minidump({"C:\\game\\Toolkit.exe": (0x400000, 0x2000, pe),
                      "C:\\game\\ijaqemox.dll": (0x10000000, 0x1000, b"\xA1" * 256)},
                     [(0x400000, pe), (0x10000000, b"\xA1" * 256),
                      (0x12FF00, struct.pack("<III", 0x00401234, 0, 0x10000020))],
                     exception=0x10000010, stack_pointer=0x12FF00)
    with tempfile.TemporaryDirectory() as d:
        dump_path = os.path.join(d, "crash.dmp")
        open(dump_path, "wb").write(blob)
        dump = dm.read_dump(dump_path)
        assert [m.name.split("\\")[-1] for m in dump.modules] == [
            "Toolkit.exe", "ijaqemox.dll"]
        assert dump.modules[0].base == 0x400000
        image, got = dump.module_image(dump.modules[0])
        assert image[:2] == b"MZ" and got == 0x2000
        rebuilt, notes = dm.rebuild_pe(image)
        assert rebuilt[:2] == b"MZ" and notes
        assert struct.unpack_from("<I", rebuilt, 0x80 + 24 + 0xE0 + 20)[0] == 0x1000
        # a module the dump only partly captured says so instead of lying
        partial, got = dump.module_image(dump.modules[1])
        assert got == 256 and len(partial) == 0x1000
        report = dm.crash_report(dump)
        assert report["exception"] == 0xC0000005
        assert report["module"].endswith("ijaqemox.dll")
        assert report["offset"] == 0x10
        frames = [(f["module"].split("\\")[-1], f["offset"])
                  for f in report["stack"]]
        assert ("Toolkit.exe", 0x1234) in frames
        assert ("ijaqemox.dll", 0x20) in frames
        # ... and the CLI agrees with the library
        out = os.path.join(d, "out")
        result = subprocess.run([sys.executable, path, dump_path, "-o", out],
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert "crash: exception 0xc0000005" in result.stdout
        assert os.path.exists(os.path.join(out, "ijaqemox.dll.img"))
        assert os.path.exists(os.path.join(out, "Toolkit.exe.rebuilt"))
        # a file that is not a minidump is refused, not guessed at
        not_a_dump = os.path.join(d, "x.dmp")
        open(not_a_dump, "wb").write(b"\x01\x02\x03\x04" + bytes(28))
        result = subprocess.run([sys.executable, path, not_a_dump],
                                capture_output=True, text=True, check=False)
        assert result.returncode == 2
        assert "not a Windows minidump" in result.stderr
    # strings: what makes the loader's own names readable
    assert any("resources" in text for text in
               dm.printable_strings(b"\x00\x00resources\\characters\x01"))
    assert dm.entropy(b"\x00" * 100) == 0.0


def test_localdumps_reg_files_are_what_windows_expects():
    """The crash dump is the one artefact that makes a protected engine
    readable, and these two files are how a non-technical step produces it:
    double-click, crash, send the file.  What must not drift is the registry
    shape Windows actually reads — both views (a 32-bit game on 64-bit Windows
    looks in Wow6432Node), a full dump rather than a mini one, and an
    expandable string for the folder."""
    import re
    folder = os.path.join(REPO, "tools", "windows")
    enable = open(os.path.join(folder, "enable_localdumps.reg"), "rb").read()
    disable = open(os.path.join(folder, "disable_localdumps.reg"), "rb").read()
    assert enable.startswith(b"Windows Registry Editor Version 5.00\r\n")
    assert disable.startswith(b"Windows Registry Editor Version 5.00\r\n")
    assert enable.count(b"Windows Error Reporting\\LocalDumps]") == 2
    assert b"Wow6432Node" in enable
    assert enable.count(b'"DumpType"=dword:00000002') == 2   # full, not mini
    assert enable.count(b'"DumpCount"=dword:0000000a') == 2
    hexed = re.search(rb'"DumpFolder"=hex\(2\):([0-9a-f,]+)', enable).group(1)
    path = bytes.fromhex(hexed.decode().replace(",", "")).decode("utf-16-le")
    assert path.rstrip("\x00") == "%USERPROFILE%\\Desktop\\D3dumps"
    assert path.endswith("\x00")                             # NUL terminated
    assert not path[:-1].endswith("\x00")
    # the undo file removes exactly the keys the enable file creates
    assert disable.count(b"[-HKEY_LOCAL_MACHINE") == 2
    for key in (b"Windows Error Reporting\\LocalDumps]",):
        assert enable.count(key) == disable.count(key) == 2


def _tiny_pe(code, rdata):
    """A two-section PE32: one code section, one string section."""
    text_rva, text_raw, rdata_rva = 0x1000, 0x200, 0x2000
    rdata_raw = 0x200 + ((len(code) + 0x1FF) // 0x200) * 0x200
    image = bytearray(rdata_raw + ((len(rdata) + 0x1FF) // 0x200) * 0x200)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<HHIII", image, 0x84, 0x14C, 2, 0, 0, 0)
    struct.pack_into("<H", image, 0x94, 0xE0)          # SizeOfOptionalHeader
    struct.pack_into("<H", image, 0x96, 0x0102)
    base = 0x80 + 24
    struct.pack_into("<I", image, base + 28, 0x400000)          # ImageBase
    struct.pack_into("<II", image, base + 32, 0x1000, 0x200)
    struct.pack_into("<I", image, base + 56, 0x3000)            # SizeOfImage
    struct.pack_into("<I", image, base + 60, 0x200)
    table = base + 0xE0
    image[table:table + 8] = b".text\x00\x00\x00"
    struct.pack_into("<IIII", image, table + 8, len(code), text_rva,
                     len(code), text_raw)
    struct.pack_into("<I", image, table + 36, 0x60000020)
    image[table + 40:table + 48] = b".rdata\x00\x00"
    struct.pack_into("<IIII", image, table + 48, len(rdata), rdata_rva,
                     len(rdata), rdata_raw)
    struct.pack_into("<I", image, table + 76, 0x40000040)
    image[text_raw:text_raw + len(code)] = code
    image[rdata_raw:rdata_raw + len(rdata)] = rdata
    return bytes(image)


def test_pe_find_resolves_a_string_its_xref_and_the_disassembly():
    """`tools/pe_find.py` is how the retail loader's own answers are read: the
    game executable keeps its code and strings in clear, so a refusal message
    resolves to the address that logs it and then to the code around that
    address.  Exercised on a synthetic two-section PE because the retail binary
    deliberately stays out of git."""
    try:
        import capstone  # noqa: F401
        import pefile  # noqa: F401
    except ImportError:                                   # pragma: no cover
        return                      # optional dependencies of the bench
    import importlib.util
    path = os.path.join(REPO, "tools", "pe_find.py")
    spec = importlib.util.spec_from_file_location("pe_find", path)
    pf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pf)

    message_va = 0x400000 + 0x2000
    code = b"\x68" + struct.pack("<I", message_va) + b"\xC3" + b"\xCC" * 8
    with tempfile.TemporaryDirectory() as d:
        exe = os.path.join(d, "tiny.exe")
        open(exe, "wb").write(_tiny_pe(code, b"wrong index\x00"))
        binary = pf.Binary(exe)
        assert binary.base == 0x400000
        assert binary.code_sections() == [".text"]
        assert [text for _va, text in binary.strings()] == ["wrong index"]
        assert binary.string_at(message_va) == "wrong index"
        hits = binary.xrefs(message_va)
        assert len(hits) == 1 and hits[0]["kind"] == "push imm32"
        assert hits[0]["va"] == 0x400000 + 0x1001
        lines = binary.disasm(0x401000, 3)
        assert lines[0].startswith("0x00401000  push")
        assert '"wrong index"' in lines[0]           # the immediate is annotated
        assert binary.function_start(0x401002) <= 0x401002
        # an address outside every section is reported, not guessed at
        assert binary.section_of(0x900000) == (None, None)


def _tiny_minidump(path):
    """A minidump carrying one module, one memory range and one exception."""
    import struct
    module_name = "D:\\Game\\DisciplesIII12.exe"
    utf = module_name.encode("utf-16-le") + b"\x00\x00"

    header = 32
    directory = header
    streams = 3                                   # (type, payload bytes)
    body = b""
    # layout: [header][dir][module list][module name][memory][exception]
    dir_size = 12 * 3                      # three streams
    mod_rva = header + dir_size
    name_rva = mod_rva + 4 + 108
    mod_list = struct.pack("<I", 1) + struct.pack(
        "<QIIII", 0x400000, 0x2000, 0, 0, name_rva) + b"\x00" * (108 - 24)
    name = struct.pack("<I", len(utf)) + utf
    mem_rva = name_rva + len(name)
    code = b"\x55\x8b\xec\xc3" + b"\x90" * 12
    mem = struct.pack("<I", 1) + struct.pack("<QII", 0x400010, len(code), 0)
    code_rva = mem_rva + len(mem)
    mem = mem[:-4] + struct.pack("<I", code_rva)   # where its bytes live
    ctx = bytearray(0xcc)
    struct.pack_into("<I", ctx, 0xb8, 0x400011)   # eip inside the code
    struct.pack_into("<I", ctx, 0xc4, 0x100000)   # esp
    exc_rva = code_rva + len(code)
    exc = struct.pack("<II", 0x1ee0, 0) + struct.pack(
        "<IIQQII", 0xC0000005, 0, 0, 0x400011, 2, 0) + struct.pack(
        "<15Q", 0, 0xA6000CB3, *([0] * 13)) + struct.pack(
        "<II", len(ctx), 0)                        # context rva fixed below
    ctx_rva = exc_rva + len(exc)
    exc = exc[:-8] + struct.pack("<II", len(ctx), ctx_rva)
    directory_bytes = b"".join(struct.pack("<III", kind, size, rva)
                               for kind, size, rva in
                               ((4, len(mod_list), mod_rva),
                                (5, len(mem), mem_rva),
                                (6, len(exc), exc_rva)))
    head = struct.pack("<4sIIIIIQ", b"MDMP", 0, 3, directory, 0, 0, 0)
    blob = (head + directory_bytes + mod_list + name + mem + code + exc
            + bytes(ctx))
    open(path, "wb").write(blob)
    return blob


def test_dmp_find_reads_a_minidump_the_way_a_crash_is_read():
    """A crash is read through three things: which module and offset faulted,
    what the exception says was being touched, and what the engine had left in
    memory.  All three come out of the streams, so the parser is testable on a
    dump this test writes itself."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dmp_find", os.path.join(REPO, "tools", "dmp_find.py"))
    dmp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dmp)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "tiny.dmp")
        _tiny_minidump(path)
        dump = dmp.Minidump(path)
        assert [m[0] for m in dump.modules] == [0x400000]
        assert dump.modules[0][2] == "D:\\Game\\DisciplesIII12.exe"
        assert dump.module_at(0x400100)[0] == 0x400000
        assert dump.name_of(0x400011).startswith("D:\\Game\\DisciplesIII12.exe+")
        # the memory list maps a virtual address to the bytes carried for it
        assert dump.read(0x400010, 1) == b"\x55"
        assert dump.read(0x900000) is None          # not in this dump
        exc = dump.exception()
        assert exc["code"] == 0xC0000005
        assert dump.registers(exc["context"][1], exc["context"][0])["eip"] \
            == 0x400011
        # the reading a crash report actually needs
        assert dmp.EXCEPTIONS[exc["code"]] == "access violation"
        assert exc["params"][1] == 0xA6000CB3       # the address touched
        code, start = dump.window(0x400011)      # clipped to what is carried
        assert start == 0x400010 and code[:4] == b"\x55\x8b\xec\xc3"
        # strings() walks the dumped ranges only, never the file's own bytes
        assert all(addr >= 0x400010 for addr, _text in dump.strings(4))


def test_vdpack_index_is_a_decodable_manifest_the_loader_checks():
    """`index` is not opaque: it is a text manifest behind an eight byte
    header and an LCG keystream — `Name`/`UUID`/`Type`, one `File` line per
    resource with its MD5 and SHA-256.  The toolkit's deobfuscator at
    0x40a290 is the same three instructions, and every digest in a shipped
    pack matches the payload it names."""
    import hashlib
    from d3tool import vdpack as vmod
    files = [("index", b"placeholder"),
             ("resources\\Unit\\a.g", b"geometry"),
             ("resources\\Unit\\a.t", b"atlas"),
             ("russian.txt", b"russian")]
    text, blob = vmod.build_index(files, name="unit", uuid="u-1", type_="Mod")
    assert blob[:4] == vmod.INDEX_MAGIC == b"\x65\x43\x74\xff"
    assert vmod.U32.unpack_from(blob, 4)[0] == vmod.INDEX_SEED
    assert len(blob) == 8 + len(text.encode("utf-8")) + 1
    # the header is a magic and a seed; the body is the text XOR the keystream
    assert vmod.decode_index(blob) == text
    assert vmod.encode_index(text) == blob            # byte for byte
    doc = vmod.parse_index(text)
    assert (doc.name, doc.uuid, doc.type) == ("unit", "u-1", "Mod")
    assert [f.path for f in doc.files] == ["resources\\Unit\\a.g",
                                           "resources\\Unit\\a.t"]
    assert doc.files[0].md5 == hashlib.md5(b"geometry").hexdigest()
    assert doc.files[0].sha256 == hashlib.sha256(b"geometry").hexdigest()
    assert "russian.txt" not in text           # a root marker is not a resource
    assert 'File = "index"' not in text         # nor is the manifest itself

    # what the loader asks of a pack: is every listing still true?
    packed = [(n, blob if n == "index" else d) for n, d in files]
    report = vmod.index_report(packed)
    assert report["decoded"] and report["records"] == 2
    assert not (report["mismatched"] or report["unlisted"] or report["missing"])
    stale = [("index", blob), ("resources\\Unit\\a.g", b"edited"),
             ("resources\\Unit\\a.t", b"atlas")]
    report = vmod.index_report(stale)
    assert report["mismatched"] == ["resources\\Unit\\a.g"]
    assert report["missing"] == [] and report["unlisted"] == []
    # a path the manifest never saw is the case the port watched vanish
    added = stale + [("resources\\Unit\\b.g", b"new")]
    assert vmod.index_report(added)["unlisted"] == ["resources\\Unit\\b.g"]


def test_vdpack_reindex_lists_a_new_path_and_refreshes_a_stale_one():
    """Writing the manifest is what lifts the "replace only, never add" rule:
    a rebuilt archive can carry a path no shipped pack listed, with its own
    `File` record, and can carry edited bytes with correct digests."""
    from d3tool import vdpack as vmod
    base = [("index", vmod.build_index([("index", b""),
                                        ("resources\\U\\a.g", b"old")],
                                       name="m", uuid="id")[1]),
            ("resources\\U\\a.g", b"old"),
            ("resources\\U\\a.t", b"atlas")]
    # the shipped manifest still describes the shipped bytes
    assert vmod.index_report(base)["mismatched"] == []

    newer = [("index", base[0][1]),          # the pack's own manifest goes in
             ("resources\\U\\a.g", b"new"),
             ("resources\\U\\a.t", b"atlas"),
             ("resources\\U\\b.g", b"added")]
    rebuilt = vmod.reindex_pack(newer)
    assert rebuilt[0][0] == "index"            # where every pack keeps it
    report = vmod.index_report(rebuilt)
    assert report["decoded"], report["error"]
    assert report["records"] == 3             # the new path got a record
    assert report["unlisted"] == [] and report["mismatched"] == []
    doc = report["document"]
    assert (doc.name, doc.uuid) == ("m", "id")   # the mod keeps its identity
    assert "resources\\U\\b.g" in doc.paths
    # the archive it lands in reads back and still checks out
    back = vmod.parse_vdpack(vmod.build_vdpack(rebuilt))
    assert back.parse_error == "" and len(back) == 4   # index + 3
    assert vmod.index_report(list(back.files().items()))["mismatched"] == []


def test_vdpack_diff_reports_a_manifest_that_no_longer_fits():
    """A pack that replaces payloads while carrying the original manifest has
    a stale digest on every replaced payload — worth saying, and worth saying
    precisely: the one in-game confirmation (the Angel pack) replaced two
    `.g` payloads under the original `index` and still loaded, so it is a
    warning about the manifest, not a verdict on the pack.  A path with no
    record at all is the other question, and that one is the port's failure."""
    from d3tool import vdpack as vmod
    text, blob = vmod.build_index([("index", b""),
                                   ("resources\\U\\a.g", b"old")],
                                  name="m", uuid="id")
    base = [("index", blob), ("resources\\U\\a.g", b"old")]
    replaced = [("index", blob), ("resources\\U\\a.g", b"new")]
    result = vmod.diff_entries(base, replaced)
    assert result["index_present"] is True and result["index_changed"] is False
    assert result["index_decoded"] is True and result["index_records"] == 1
    assert result["index_mismatched"] == ["resources\\U\\a.g"]
    verdict = vmod.shape_verdict(result)
    grades = [grade for grade, _ in verdict]
    assert "bad" not in grades                 # stale digest is not the failure
    text_lines = " ".join(line for _g, line in verdict)
    assert "no longer match" in text_lines and "--reindex" in text_lines
    # a correctly reindexed replacement grades ok, manifest and all
    fixed = [("index", vmod.build_index([("index", b""),
                                         ("resources\\U\\a.g", b"new")],
                                        name="m", uuid="id")[1]),
             ("resources\\U\\a.g", b"new")]
    again = vmod.diff_entries(base, fixed)
    assert again["index_mismatched"] == [] and again["index_unlisted"] == []
    assert [g for g, _ in vmod.shape_verdict(again)] == ["ok"]
    # an added path with no record is the bad one
    added = fixed + [("resources\\U\\b.g", b"new")]
    bad = vmod.diff_entries(base, added)
    assert bad["index_unlisted"] == ["resources\\U\\b.g"]
    assert "bad" in [g for g, _ in vmod.shape_verdict(bad)]
    # and a manifest that decodes to nonsense is reported, not guessed at
    broken = [("index", b"not-a-manifest"), ("resources\\U\\a.g", b"new")]
    assert vmod.diff_entries(base, broken)["index_error"]
    assert "warn" in [g for g, _ in vmod.shape_verdict(
        vmod.diff_entries(base, broken))]


def test_vdpack_plan_notices_payloads_the_index_no_longer_describes():
    """`plan` lines a folder up against a pack; when the folder's bytes differ
    from the ones the pack's `index` was written for, that is the fact to say
    out loud, not the path count."""
    from d3tool import vdpack as vmod
    base = [("index", b"opaque"), ("resources\\U\\a.g", b"old"),
            ("resources\\U\\a.t", b"atlas")]
    same = vmod.plan_bundle([("a.g", b"old"), ("a.t", b"atlas")], base)
    assert same["index_present"] is True
    assert same["payloads_changed"] == []
    edited = vmod.plan_bundle([("a.g", b"new"), ("a.t", b"atlas")], base)
    assert edited["payloads_changed"] == ["resources\\U\\a.g"]
    assert edited["added"] == []
    # without an index there is nothing to go stale, even though the report
    # still says which payloads differ (the CLI gates the warning on this)
    plain = vmod.plan_bundle([("a.g", b"new")],
                             [("resources\\U\\a.g", b"old")])
    assert plain["index_present"] is False
    assert plain["payloads_changed"] == ["resources\\U\\a.g"]


def test_scene_gains_a_gobj_for_a_part_it_cannot_bind():
    """The engine binds the i-th mesh part to the i-th `gobj`, in order — the
    LOD scenes prove it, since their gobj names (`empire_angel_wing`) never
    match their part names (`lod_empire_angel_wing`) and the game draws them
    anyway.  A part past the last block is invisible, which is exactly the
    Angel's sword: five meshes, four gobjs."""
    import copy as copymod
    from d3tool import gfile as gmod
    from d3tool import scene as scenemod
    unit = os.path.join(REPO, "Empire", "Angel", "character_empire_angel")
    text = open(unit + ".scene", encoding="latin-1").read()
    mesh = gmod.parse_geometry_file(open(unit + ".g", "rb").read())
    parts = list(gmod.iter_parts(mesh))
    assert len(parts) == len(scenemod.parse_scene(text).find_all("gobj")) == 4

    extra = copymod.copy(parts[-1])
    extra.name = "test_extra_part"
    patched, added = scenemod.ensure_part_gobjs(text, parts + [extra])
    assert added == ["test_extra_part"], added
    doc = scenemod.parse_scene(patched)
    gobjs = doc.find_all("gobj")
    assert len(gobjs) == 5
    block = gobjs[-1]
    assert scenemod.node_attr(block, "name") == "test_extra_part"
    assert scenemod.node_attr(block, "material0_diffuse") == \
        extra.material_diffuse
    assert scenemod.node_attr(block, "material0_triangles_num") == \
        str(extra.tri_count)
    assert scenemod.node_attr(block, "vertexs_weights_num") == \
        str(extra.vertex_count)
    assert scenemod.node_attr(block, "weights_on_vertex") == \
        str(extra.weights_on_vertex)
    assert scenemod.node_attr(block, "bones_num") == str(len(extra.bones))
    # the block hangs inside the same node as the parts it joins
    assert block.props.get("bonesuid") == gobjs[0].props.get("bonesuid")
    for key, values in (("uid", [n.props.get("uid") for n in gobjs]),
                        ("dwNode", [scenemod.node_attr(n, "dwNode")
                                    for n in gobjs]),
                        ("slot", [scenemod.node_mesh_index(n)
                                  for n in gobjs])):
        assert len(set(values)) == len(values), (key, values)
    # and it is stable: a second pass changes nothing
    again, added2 = scenemod.ensure_part_gobjs(patched, parts + [extra])
    assert again == patched and added2 == []


def test_every_shipped_scene_already_binds_one_gobj_per_part():
    """The corpus sets the rule and must stay untouched by it: 159 of the 160
    scenes carry exactly one `gobj` per mesh part (the Cyclop carries a spare),
    so a correct unit comes back byte for byte."""
    from d3tool import gfile as gmod
    from d3tool import scene as scenemod
    checked = 0
    for group in ("Empire", "Neutrals"):
        for gpath in sorted(glob.glob(os.path.join(REPO, group, "*", "*.g"))):
            if gpath.endswith("_lod.g"):
                continue
            spath = gpath[:-2] + ".scene"
            if not os.path.isfile(spath):
                continue
            mesh = gmod.parse_geometry_file(open(gpath, "rb").read())
            text = open(spath, encoding="latin-1").read()
            patched, added = scenemod.ensure_part_gobjs(
                text, list(gmod.iter_parts(mesh)))
            assert patched == text, f"{spath} was rewritten"
            assert added == [], (spath, added)
            checked += 1
    assert checked > 50, checked


def test_container_mesh_count_follows_the_blocks():
    """A `.g` closes its node chain with the number of mesh blocks.

    The writer used to copy the donor prelude verbatim -- count included -- so
    every export that grew a compound model (the Angel with a weapon appended)
    declared one block fewer than the file carries.  Nothing in the parser
    notices (it walks the blocks), the whole retail corpus has the two numbers
    equal (247/247), and a reader that trusts the header simply never sees the
    last block: the mesh is in the file and out of the model.
    """
    from d3tool import gfile as gmod
    for path in ("Empire/Knight/character_empire_knight.g",
                 "Empire/Knight/character_empire_knight_lod.g",
                 "Empire/Angel/character_empire_angel.g",
                 "Empire/Angel/character_empire_angel_lod.g"):
        data = open(os.path.join(REPO, path), "rb").read()
        mesh = gmod.parse_geometry_file(data)
        offset, count = gmod.mesh_count(data)
        assert count == len(gmod.iter_parts(mesh)), path
        # a parsed `.g` round-trips, and the count field stays where it was
        rebuilt = gmod.write_geometry_file(mesh, mesh.attrs)
        assert rebuilt == data, path
        assert gmod.mesh_count(rebuilt) == (offset, count)

    # a file whose count was left one short is *not* detectable by the parser,
    # and the writer (which knows how many blocks it emitted) restores it
    stale = bytearray(open(os.path.join(
        REPO, "Empire/Knight/character_empire_knight.g"), "rb").read())
    offset, count = gmod.mesh_count(bytes(stale))
    stale[offset:offset + 4] = (count - 1).to_bytes(4, "little")
    mesh = gmod.parse_geometry_file(bytes(stale))
    assert len(gmod.iter_parts(mesh)) == count
    assert mesh.declared_mesh_count == count - 1
    assert gmod.mesh_count(bytes(stale))[1] == count - 1
    # the check an export prints, and the repair that rescues an old file
    findings = gmod.count_mismatches(mesh)
    assert len(findings) == 1 and "mesh block" in findings[0], findings
    repaired, fixes = gmod.repair_counts(bytes(stale))
    assert repaired == open(os.path.join(
        REPO, "Empire/Knight/character_empire_knight.g"), "rb").read()
    assert fixes == findings
    # and the writer knows how many blocks it emitted, whatever the donor said
    mesh.raw = b""
    fixed = gmod.write_geometry_file(mesh, mesh.attrs)
    assert gmod.mesh_count(fixed)[1] == count
    assert fixed == bytes(stale[:offset]) + count.to_bytes(4, "little") \
        + bytes(stale[offset + 4:])


def test_lod_mesh_takes_the_parts_its_main_mesh_gained():
    """The profile swaps to `unit_scene_lod` beyond a draw distance (10 for
    nearly every unit), and the LOD is a separate, lower-poly `.g`: a weapon
    has to be appended there too or the battlefield never shows it."""
    from d3tool import gfile as gmod
    from d3tool import scene as scenemod
    unit = os.path.join(REPO, "Empire", "Angel", "character_empire_angel")
    lod_bytes = open(unit + "_lod.g", "rb").read()
    lod = gmod.parse_geometry_file(lod_bytes)
    main = gmod.parse_geometry_file(open(unit + ".g", "rb").read())
    extra = list(gmod.iter_parts(main))[-1]     # raw bytes, name included

    grown = gmod.append_parts(lod_bytes, [extra])
    # nothing else moves -- except the container's mesh count, which is the
    # whole point: the engine reads exactly that many blocks
    assert len(grown) == len(lod_bytes) + len(extra.raw)
    prefix_offset, before = gmod.mesh_count(lod_bytes)
    after_offset, after = gmod.mesh_count(grown)
    assert (prefix_offset, after_offset) == (prefix_offset, prefix_offset)
    assert after == before + 1
    head, old_head = bytearray(grown[:len(lod_bytes)]), bytearray(lod_bytes)
    head[prefix_offset:prefix_offset + 4] = b"\0\0\0\0"
    old_head[prefix_offset:prefix_offset + 4] = b"\0\0\0\0"
    assert bytes(head) == bytes(old_head)
    regrown = gmod.parse_geometry_file(grown)
    assert [p.name for p in gmod.iter_parts(regrown)] == \
        [p.name for p in gmod.iter_parts(lod)] + [extra.name]
    assert gmod.count_mismatches(regrown) == []

    # the LOD scene spells its own parts `lod_<name>`, so only the count
    # decides how many blocks are missing
    text = open(unit + "_lod.scene", encoding="latin-1").read()
    lod_parts = list(gmod.iter_parts(regrown))
    patched, added = scenemod.ensure_part_gobjs(text, lod_parts)
    assert added == [extra.name], added
    gobjs = scenemod.parse_scene(patched).find_all("gobj")
    assert len(gobjs) == len(lod_parts)
    assert scenemod.node_mesh_index(gobjs[-1]) == len(lod_parts) - 1


def test_gobj_binding_follows_the_mesh_line_not_the_document_order():
    """The OrcKing's scene opens with its body, the Cleric's with its cloth.

    A `.scene` is free to list its `gobj`s in any order, and the retail
    corpus uses that freedom: zipping the scene's `gobj` list with the file's
    part list — what `refresh_part_gobjs` did — stated the body's atlas and
    counts on the weapon's `gobj` and the weapon's on the body's.  Those
    numbers are what the engine builds the vertex declaration from, so the
    swap is a load-time crash, not a cosmetic slip.
    """
    from d3tool import roundtrip

    unit = os.path.join(REPO, "Neutrals", "OrcKing",
                        "character_neutrals_orcking")
    text = open(unit + ".scene", encoding="latin-1").read()
    parts = list(gfile.iter_parts(gfile.parse_geometry_file(
        open(unit + ".g", "rb").read())))

    # nothing changed: the scene comes back byte for byte
    same, changed = scenemod.refresh_part_gobjs(text, parts, reference=parts)
    assert same == text and changed == []

    # the weapon part now wears the Knight's blade: only its own gobj moves
    sword = copy.copy(parts[2])
    sword.attrs = dict(sword.attrs, material0_diffuse="weapon_empire_knight_"
                                                "sword_diffuse.tga")
    touched_parts = list(parts)
    touched_parts[2] = sword
    fresh, changed = scenemod.refresh_part_gobjs(text, touched_parts,
                                                 reference=parts)
    assert changed == ["weapon_neutral_orcking"], changed
    doc = scenemod.parse_scene(fresh)
    by_name = {scenemod.node_attr(n, "name"): n
               for n in doc.find_all("gobj")}
    weapon = by_name["weapon_neutral_orcking"]
    body = by_name["neutrals_orcking"]
    assert scenemod.node_attr(weapon, "material0_diffuse") == \
        "weapon_empire_knight_sword_diffuse.tga"
    # ... and the body kept the numbers it shipped with
    assert scenemod.node_attr(body, "material0_triangles_num") == "4658"
    assert scenemod.node_attr(body, "bones_num") == "45"


def test_roundtrip_reexport_gives_the_unit_back_byte_for_byte():
    """`.g` -> glTF -> `.g` on an untouched unit: eight of eight files equal.

    This is the promise the add-on's Self-test makes to the user, measured on
    the format path so a regression is caught without Blender.
    """
    from d3tool import roundtrip

    folder = os.path.join(REPO, "Empire", "Inquisitor")
    tmp = tempfile.mkdtemp(prefix="d3rt_")
    try:
        stem = os.path.join(folder, "character_empire_inquisitor")
        gltf_path = os.path.join(tmp, "character_empire_inquisitor.gltf")
        out = os.path.join(tmp, "out")
        climod._export_gl(stem + ".g", climod._find_animation_for_geometry(
            stem + ".g"), gltf_path, None, quiet=True)
        climod._export(gltf_path, out, 0, anim=True, quiet=True,
                       donor_dir=folder)
        diffs = roundtrip.diff_folders(folder, out)
        bad = [d for d in diffs if not d.same and d.produced is not None]
        assert not bad, [d.summary() for d in bad]
        assert roundtrip.summary(diffs)["identical"] >= 7
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_first_difference_reports_where_and_ignores_a_prefix():
    from d3tool import roundtrip

    assert roundtrip.first_difference(b"abc", b"abc") is None
    assert roundtrip.first_difference(b"abc", b"abcd") is None
    assert roundtrip.first_difference(b"", b"") is None
    assert roundtrip.first_difference(b"abc", b"abd") == 2
    # a difference past the 4096-byte block the scan compares in
    left = bytes(9000)
    right = bytearray(left)
    right[8193] = 1
    assert roundtrip.first_difference(left, bytes(right)) == 8193


def test_diff_folders_does_not_accuse_a_file_the_export_never_wrote():
    from d3tool import roundtrip

    tmp = tempfile.mkdtemp(prefix="d3diff_")
    try:
        src = os.path.join(tmp, "donor")
        out = os.path.join(tmp, "out")
        os.makedirs(src)
        os.makedirs(out)
        open(os.path.join(src, "a.g"), "wb").write(b"aaaa")
        open(os.path.join(src, "b.t"), "wb").write(b"bbbb")
        open(os.path.join(out, "a.g"), "wb").write(b"aaaa")
        diffs = {d.name: d for d in roundtrip.diff_folders(src, out)}
        assert diffs["a.g"].same
        # the export wrote no `.t` at all: that is not a difference, whether
        # the folder lists it as a missing output or leaves it out
        assert "b.t" not in diffs or (diffs["b.t"].same
                                      and diffs["b.t"].produced is None)
        assert roundtrip.summary(list(diffs.values()))["differing"] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lod_sync_leaves_a_shorter_retail_lod_alone():
    """The Cleric's LOD model drops the hair on purpose.

    Appending "the parts the LOD lacks" on a re-export grew five retail LODs
    by a few kilobytes each and drew parts the artist never added there.  With
    the donor in hand only a part the export APPENDED is missing from the LOD.
    """
    tmp = tempfile.mkdtemp(prefix="d3lod_")
    try:
        src = os.path.join(REPO, "Empire", "Cleric")
        work = os.path.join(tmp, "unit")
        shutil.copytree(src, work)
        donor = list(gfile.iter_parts(gfile.parse_geometry_file(
            open(os.path.join(work, "character_empire_cleric.g"), "rb").read())))
        before = open(os.path.join(work, "character_empire_cleric_lod.g"),
                      "rb").read()
        climod._lod_sync(work, quiet=True, reference=donor)
        after = open(os.path.join(work, "character_empire_cleric_lod.g"),
                     "rb").read()
        assert after == before, "the LOD was rewritten on an untouched unit"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scenarios_nothing_touched_comes_back_identical():
    """The baseline the other three scenarios stand on."""
    import scenarios
    result = scenarios.scenario_unchanged(unit_dir=REPO_LIKE_UNIT_DIR())
    assert result["added"] == [] and result["rewritten"] == []


def test_scenarios_a_part_atlas_follows_the_material():
    import scenarios
    result = scenarios.scenario_retexture(unit_dir=REPO_LIKE_UNIT_DIR())
    assert result["texture"] > 0
    changed = [(n, a) for (n, a), (_b, b) in zip(result["after"],
                                                 result["before"]) if a != b]
    assert changed and changed[0][1] == result["now"], changed


def test_scenarios_a_body_atlas_reaches_the_lod_and_the_scenes():
    import scenarios
    result = scenarios.scenario_armour_retexture(unit_dir=REPO_LIKE_UNIT_DIR())
    assert result["texture"] > 0
    assert any(t == result["now"] for _n, t in result["scene"]), result["scene"]
    assert result["lod_after"] != result["lod_before"]


def test_scenarios_a_new_mesh_on_the_skeleton_is_drawn():
    import scenarios
    result = scenarios.scenario_new_part(unit_dir=REPO_LIKE_UNIT_DIR())
    assert result["bones"] == [scenarios.BONE], result["bones"]
    assert "helmet_empire_inquisitor" in result["forward"], result["forward"]


def test_a_lod_scene_with_duplicate_slots_still_gets_the_appended_gobj():
    """Priestess: two `gobj`s name the body (slots 0 0 1 2 3), four parts.

    Counting numbers positionally claimed every slot up to the `gobj` count,
    so a part the export *appended* (slot 4) was considered drawn and never
    got its `gobj` — the weapon stayed invisible on the battlefield while
    every file looked right.  A part the shipped scene simply never drew (the
    Goblin's and the Thug's scenes both leave one out) must still be left
    alone.
    """
    text = open(os.path.join(REPO, "Empire", "Priestess",
                             "character_empire_priestess_lod.scene"),
                encoding="latin-1", newline="").read()
    parts = list(gfile.iter_parts(gfile.parse_geometry_file(
        open(os.path.join(REPO, "Empire", "Priestess",
                          "character_empire_priestess_lod.g"),
             "rb").read())))
    # untouched: nothing is appended and nothing is added
    same, added = scenemod.ensure_part_gobjs(text, parts)
    assert same == text and added == [], added
    extra = copy.copy(parts[-1])
    extra.name = "lod_helmet_empire_inquisitor"
    fresh, added = scenemod.ensure_part_gobjs(text, parts + [extra],
                                              only=["lod_helmet_empire_inquisitor"])
    assert added == ["lod_helmet_empire_inquisitor"], added
    assert "lod_helmet_empire_inquisitor" in fresh
    # and the retail scenes with a part no `gobj` draws are untouched
    for unit, folder in (("goblin", "Goblin"), ("thug", "Thug")):
        scene = open(os.path.join(REPO, "Neutrals", folder,
                                  f"character_neutrals_{unit}_lod.scene"),
                     encoding="latin-1", newline="").read()
        mesh = list(gfile.iter_parts(gfile.parse_geometry_file(
            open(os.path.join(REPO, "Neutrals", folder,
                              f"character_neutrals_{unit}_lod.g"), "rb").read())))
        assert scenemod.ensure_part_gobjs(scene, mesh)[0] == scene, unit


def test_scenarios_a_new_mesh_brings_its_own_texture():
    """The new part's own atlas reaches every file the engine reads.

    Modelled with its own material and painted from a texture that exists
    nowhere in the game data, the part must end up with `<name>.t` in the
    folder, that name in its block of the `.g` *and* the `_lod.g`, the same
    name in both `.scene` `gobj`s, and the atlas handed back by the forward
    export.  Both placements are covered: the atlas next to the glTF and the
    atlas in the unit's folder.
    """
    import scenarios
    result = scenarios.scenario_new_part_texture(unit_dir=REPO_LIKE_UNIT_DIR())
    assert result["atlas"] == "order_helmet_of_the_order.tga", result
    assert result["bytes"] > 0


def REPO_LIKE_UNIT_DIR():
    return os.path.join(REPO, "Empire", "Inquisitor")


def test_the_export_reports_the_files_it_added_to_a_unit():
    """Nothing is written into a unit's folder that the unit did not have.

    The exporter's own report is the guarantee: every file it produced is
    either byte-identical to the unit's own, a rewrite of one of them, or —
    loudly — new.  `d3tool export` on a retail unit must come back with zero
    new files and zero rewrites when nothing was edited.
    """
    tmp = tempfile.mkdtemp(prefix="d3delta_")
    try:
        donor = os.path.join(tmp, "unit")
        out = os.path.join(tmp, "out")
        os.makedirs(donor)
        os.makedirs(out)
        open(os.path.join(donor, "a.g"), "wb").write(b"aaaa")
        open(os.path.join(donor, "a.t"), "wb").write(b"tttt")
        open(os.path.join(out, "a.g"), "wb").write(b"bbbb")     # rewritten
        open(os.path.join(out, "a.t"), "wb").write(b"tttt")     # identical
        open(os.path.join(out, "extra.t"), "wb").write(b"x")    # added
        open(os.path.join(out, "stale.scene"), "wb").write(b"s")  # was here
        delta = climod._export_delta(out, {"stale.scene"}, donor)
        assert delta["new"] == ["extra.t"], delta
        assert delta["rewritten"] == ["a.g"], delta
        assert delta["kept"] == ["a.t"], delta

        # end to end: a retail unit re-exported changes nothing and adds
        # nothing (the roundtrip harness asserts the bytes; this asserts the
        # report the user reads)
        folder = os.path.join(REPO, "Empire", "Inquisitor")
        stem = os.path.join(folder, "character_empire_inquisitor")
        gltf = os.path.join(tmp, "character_empire_inquisitor.gltf")
        exported = os.path.join(tmp, "exported")
        climod._export_gl(stem + ".g", climod._find_animation_for_geometry(
            stem + ".g"), gltf, None, quiet=True)
        before = set()
        climod._export(gltf, exported, 0, anim=True, quiet=True,
                       donor_dir=folder)
        delta = climod._export_delta(exported, before, folder)
        assert delta["new"] == [], delta["new"]
        assert delta["rewritten"] == [], delta["rewritten"]
        assert len(delta["kept"]) >= 7, delta["kept"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_repair_repaths_an_ac_to_the_folder_the_scene_names():
    """The crash-on-recruit of a shipped pack, in one file.

    `character_empire_angel_lod.ac` named
    ``resources\\characters\\character_empire_angel_lod\\…`` — a folder no
    install has — while every stream it names sits in
    ``resources\\characters\\empire\\angel``.  The engine does not fall back: the
    load stops.  `repair` rewrites the folder of every reference whose file is
    in the bundle, keeps the file's own ``\\r\\n`` endings, and leaves a
    reference to another folder alone.
    """
    tmp = tempfile.mkdtemp(prefix="d3acrepair_")
    try:
        work = os.path.join(tmp, "angel")
        shutil.copytree(os.path.join(REPO, "Empire", "Angel"), work)
        bad = "resources\\characters\\character_empire_angel_lod\\"
        ac = os.path.join(work, "character_empire_angel_lod.ac")
        states = "".join(
            f'state "{name}"\r\n{{\r\n'
            f'file "{bad}character_empire_angel_{stem}.a";\r\n'
            f'frame0 0;\r\nframe1 30;\r\n'
            f'meshfile "{bad}character_empire_angel_lod.g";\r\n}}\r\n'
            for name, stem in (("Idle", "idle"), ("Run", "run")))
        # one reference that belongs to no folder in this bundle
        states += ('state "Extra"\r\n{\r\n'
                   'file "resources\\characters\\shared\\common_idle.a";\r\n'
                   'frame0 0;\r\nframe1 10;\r\n}\r\n')
        with open(ac, "w", encoding="latin-1", newline="") as fh:
            fh.write(states)

        out = os.path.join(tmp, "fixed.ac")
        assert climod._repair_ac(ac, out, False) == 0
        with open(out, encoding="latin-1", newline="") as fh:
            fixed = fh.read()
        assert "character_empire_angel_lod\\character_empire_angel_lod.g" \
            not in fixed
        assert ('"resources\\characters\\empire\\angel\\'
                'character_empire_angel_lod.g"') in fixed
        # the foreign reference is not ours to touch
        assert '"resources\\characters\\shared\\common_idle.a"' in fixed
        # and the file keeps the terminators the game's files use
        assert fixed.count("\r\n") == states.count("\r\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_vdpack_pack_like_keeps_the_source_shape():
    """The only in-game-confirmed pack is *the same paths, different
    payloads*: entry order, name spelling, flags and the opaque `index` all
    come from the pack being patched, so nothing but the payload can move."""
    from d3tool import vdpack as vmod
    index_blob = bytes(range(256)) * 4          # stands in for the opaque one
    source = vmod.build_vdpack([
        ("index", index_blob),
        ("resources\\Characters\\Empire\\Angel\\b.scene", b"old scene"),
        ("resources\\Characters\\Empire\\Angel\\a.g", b"old geometry"),
    ])
    patched = vmod.parse_vdpack(source)
    rebuilt, extra = vmod.pack_like(patched, [
        ("resources/Characters/Empire/Angel/a.g", b"new geometry!"),
        ("russian.txt", b"x"),                  # a path the source lacks
    ])
    assert extra == ["russian.txt"]
    grown = vmod.parse_vdpack(rebuilt)
    assert grown.parse_error == ""
    assert [e.name for e in grown.entries] == [e.name for e in patched.entries]
    assert [e.flags for e in grown.entries] == [e.flags for e in patched.entries]
    # the payloads the folder does not carry come from the source, `index`
    # included, and the ones it does carry are the new bytes
    by_name = {e.name: grown.payload(e) for e in grown.entries}
    assert by_name["index"] == index_blob
    assert by_name["resources\\Characters\\Empire\\Angel\\b.scene"] == \
        b"old scene"
    assert by_name["resources\\Characters\\Empire\\Angel\\a.g"] == \
        b"new geometry!"


def test_vdpack_validate_finds_dangling_references():
    """A pack is mounted instead of the loose folders, so everything a unit
    names must be inside it — except what the engine keeps globally."""
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    files = dict(vdpack.pack_folder(folder))
    report = vdpack.reference_findings(files)
    assert not report, report.problems
    assert not report.notes, report.notes

    # dropping the unit texture must be caught through the `.g` material
    no_tex = {n: d for n, d in files.items() if not n.endswith(".t")}
    report = vdpack.reference_findings(no_tex)
    assert report.problems, "a pack without its textures was accepted"
    assert any("material0_diffuse" in p for p in report.problems), report.problems

    # a bare `.a` reference that is missing is a problem ...
    ac = ("file = 0\n"
          "meshfile = character_neutrals_airelemental.g\n"
          'state "Idle" { file "character_neutrals_airelemental_run.a" '
          "frame0 0 frame1 10 fps 30 }\n"
          'state "Run" { file "missing_stream.a" frame0 0 frame1 10 fps 30 }\n')
    bad = dict(files)
    bad["test.ac"] = ac.encode()
    report = vdpack.reference_findings(bad)
    assert any("missing_stream.a" in p for p in report.problems), report.problems
    # ... while a reference into another folder is a note: retail units name
    # their template's animation, which the game mounts separately
    bad["test.ac"] = ac.replace('"missing_stream.a"',
                                '"resources/characters/other/x.a"').encode()
    report = vdpack.reference_findings(bad)
    assert not report.problems, report.problems
    assert any("x.a" in n for n in report.notes), report.notes


def test_vdpack_validate_cli_reports_and_exits():
    import contextlib
    import io
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    stem = "character_neutrals_airelemental"
    with tempfile.TemporaryDirectory() as d, \
            contextlib.redirect_stdout(io.StringIO()) as buf:
        good = os.path.join(d, "good.vdpack")
        assert climod._run(["vdpack", "pack", folder, "-o", good]) == 0
        assert climod._run(["vdpack", "validate", good]) == 0
        # a folder works as a target too
        assert climod._run(["vdpack", "validate", folder]) == 0
        # packing an incomplete folder produces a pack that must be rejected
        partial = os.path.join(d, "partial")
        os.makedirs(partial)
        for name in ("character_neutrals_airelemental.g",):
            open(os.path.join(partial, name), "wb").write(
                open(os.path.join(folder, name), "rb").read())
        broken = os.path.join(d, "broken.vdpack")
        assert climod._run(["vdpack", "pack", partial, "-o", broken]) == 0
        assert climod._run(["vdpack", "validate", broken]) == 1
        out = buf.getvalue()
        assert "material0_diffuse" in out, out[-800:]
        # a damaged archive is refused before validation
        damaged = os.path.join(d, "damaged.vdpack")
        raw = bytearray(open(good, "rb").read())
        raw[4:8] = struct.pack("<I", 1)
        open(damaged, "wb").write(bytes(raw))
        assert climod._run(["vdpack", "validate", damaged]) == 1


def test_vdpack_warns_about_a_reference_in_the_wrong_folder():
    """A `.ac` written with the wrong resource root names a folder the pack
    does not have, while the model it wants sits in another one.  Nothing can
    resolve that — and it is how a generated `_lod.ac` shipped: the pack
    validated clean (`every reference resolves inside`) and the game asked for
    a file that was never there, so the two kinds of out-of-tree reference
    have to be told apart."""
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    root = "resources\\Characters\\Neutrals\\AirElemental\\"

    def as_pack():
        return {(root + name.replace("/", "\\")): data
                for name, data in vdpack.pack_folder(folder)}

    wrong = acmod.default_ac(
        "resources\\characters\\character_neutrals_airelemental_lod\\"
        "character_neutrals_airelemental.g",
        "resources\\characters\\character_neutrals_airelemental_lod\\"
        "character_neutrals_airelemental")
    files = as_pack()
    files[root + "character_neutrals_airelemental_lod.ac"] = \
        acmod.write_ac(wrong).encode()
    report = vdpack.reference_findings(files)
    assert not report.problems, report.problems
    assert report.warnings, "a wrong resource root was accepted"
    assert any("character_neutrals_airelemental.g" in w
               and "AirElemental" in w for w in report.warnings), \
        report.warnings

    # the same states pointing at the folder the pack really has are clean
    right = acmod.default_ac(
        "resources\\characters\\neutrals\\airelemental\\"
        "character_neutrals_airelemental.g",
        "resources\\characters\\neutrals\\airelemental\\"
        "character_neutrals_airelemental")
    files[root + "character_neutrals_airelemental_lod.ac"] = \
        acmod.write_ac(right).encode()
    report = vdpack.reference_findings(files)
    assert not report.warnings and not report.problems, \
        (report.warnings, report.problems)

    # a reference to a folder this pack does not carry at all stays a note:
    # retail units name their template's animation, which the game mounts
    other = acmod.default_ac("resources\\characters\\other\\x.g",
                             "resources\\characters\\other\\x")
    files[root + "character_neutrals_airelemental_lod.ac"] = \
        acmod.write_ac(other).encode()
    report = vdpack.reference_findings(files)
    assert not report.warnings, report.warnings
    assert any("x.g" in n for n in report.notes), report.notes


def test_vdpack_catches_a_weapon_trail_whose_bones_were_dropped():
    """A `.scene`'s weapon trail names its bones by **index**, so an animation
    stream that lost the animation-only `Trail*` bones silently repoints the
    trail at an arm — and a unit whose trail node is built from them can die
    on load.  Every one of the 54 trail-bearing retail scenes resolves its
    `boneid` values to `Trail1`…`TrailN` in the unit's own stream, which is
    what makes the check exact rather than a guess."""
    import struct as stmod
    from d3tool import anim as animmod
    from d3tool import vdpack
    folder = os.path.join(REPO, "Empire", "Inquisitor")
    files = {name: open(os.path.join(folder, name), "rb").read()
             for name in ("character_empire_inquisitor.scene",
                          "character_empire_inquisitor_lod.scene",
                          "character_empire_inquisitor.ac",
                          "character_empire_inquisitor_baseanims.a",
                          "character_empire_inquisitor.g")}
    assert "boneid 11" in files["character_empire_inquisitor.scene"].decode()

    report = vdpack.reference_findings(dict(files))
    assert not [p for p in report.problems if "weapon-trail" in p], \
        report.problems
    stream = animmod.parse_anim(files["character_empire_inquisitor_baseanims.a"])
    assert stream.bones[11].name == "Trail1"
    assert stream.bones[12].name == "Trail2"

    # a re-export keeps only the bones a *mesh* is weighted to: drop the two
    # trail tracks the way a Blender round-trip does and every index shifts
    stream.bones = [b for i, b in enumerate(stream.bones) if i not in (11, 12)]
    stream.raw = b""
    stream.bone_count = len(stream.bones)
    header = bytearray(stream.header)
    stmod.pack_into("<I", header, 8, len(stream.bones))
    stream.header = bytes(header)
    dropped = animmod.write_anim(stream)
    assert len(animmod.parse_anim(dropped).bones) == 25
    files["character_empire_inquisitor_baseanims.a"] = dropped
    report = vdpack.reference_findings(dict(files))
    trail = [p for p in report.problems if "weapon-trail" in p]
    assert len(trail) == 2, report.problems          # boneid 11 and 12
    assert "RightShoulder" in trail[0], trail
    assert "RightArm" in trail[1], trail
    # the reduced `_lod` streams of retail leave the index stale on purpose
    # (Defender of Faith, Dark Servant, Grand Inquisitor), so only the main
    # scene is judged
    assert not [p for p in trail if "_lod.scene" in p], trail


def test_vdpack_validate_is_strict_about_wrong_folders():
    """`--strict` turns the warning into a non-zero exit, so a build script can
    refuse to ship a pack whose paths were written with the wrong root."""
    import contextlib
    import io
    import shutil as shmod
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    wrong = acmod.default_ac(
        "resources\\characters\\character_neutrals_airelemental_lod\\"
        "character_neutrals_airelemental.g",
        "resources\\characters\\character_neutrals_airelemental_lod\\"
        "character_neutrals_airelemental")
    with tempfile.TemporaryDirectory() as d:
        # the bundle has to be namespaced for the folder to be judgeable: one
        # rooted at the unit folder mounts there and names its files by path
        bundle = os.path.join(d, "AirElemental")
        tree = os.path.join(bundle, "resources", "Characters", "Neutrals",
                            "AirElemental")
        shutil.copytree(folder, tree)
        open(os.path.join(tree, "character_neutrals_airelemental_lod.ac"), "w",
             encoding="utf-8").write(acmod.write_ac(wrong))
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            assert climod._run(["vdpack", "validate", bundle]) == 0
            out = buf.getvalue()
            assert "wrong folder" in out, out[-600:]
            assert climod._run(["vdpack", "validate", bundle,
                                "--strict"]) == 1


def test_vdpack_stored_names_carry_one_separator():
    """An archive name is one backslash per level; a doubled separator makes a
    path the engine cannot match against its own resource table."""
    from d3tool import vdpack
    blob = vdpack.build_vdpack([("resources/Characters/Empire/Angel/x.g",
                                 b"payload")])
    pack = vdpack.parse_vdpack(blob)
    assert not pack.parse_error, pack.parse_error
    assert [e.name for e in pack.entries] == \
        ["resources\\Characters\\Empire\\Angel\\x.g"], \
        [e.name for e in pack.entries]
    assert pack.files()["resources\\Characters\\Empire\\Angel\\x.g"] == \
        b"payload"


def test_vdpack_manifest_of_folder_and_archive_match():
    """A pack has to carry exactly what the folder has; the manifest is the
    proof (the Heroes-VII port kept one `manifest.json` per pack)."""
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    files = vdpack.pack_folder(folder)
    doc = vdpack.manifest(files, "AirElemental")
    assert doc["index"] is None, "a loose corpus folder carries no `index`"
    assert doc["files"] == len(files) >= 10
    assert doc["bytes"] == sum(len(d) for _, d in files)
    assert doc["source"] == "AirElemental"
    first = doc["entries"][0]
    assert set(first) == {"path", "bytes", "sha256"}
    # archive naming (backslashes) and a stable, case-insensitive order
    assert all("\\" in e["path"] or "/" not in e["path"] for e in doc["entries"])
    order = [e["path"].lower() for e in doc["entries"]]
    assert order == sorted(order)
    # every digest is the file's own
    for entry in doc["entries"]:
        data = dict((n.replace("/", "\\"), d) for n, d in files)[entry["path"]]
        assert entry["bytes"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    # the same document for the archive built from the folder
    pack = vdpack.parse_vdpack(vdpack.build_vdpack(files))
    assert not pack.parse_error
    back = vdpack.manifest(pack.files(), "AirElemental.vdpack")
    assert back["entries"] == doc["entries"]
    assert back["bytes"] == doc["bytes"]
    # ... and a manifest reports a bundle that would not load on its own
    no_tex = [(n, d) for n, d in files if not n.endswith(".t")]
    assert vdpack.manifest(no_tex)["references"]["problems"]


def test_vdpack_pack_writes_a_manifest_and_can_carry_one_instead():
    """`vdpack pack` writes the `index` manifest by default, because the
    loader compares the archive against it — `--no-reindex` turns that off and
    says so, and `--keep-index` carries a proven pack's manifest over instead."""
    import contextlib
    import io
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    with tempfile.TemporaryDirectory() as d, \
            contextlib.redirect_stdout(io.StringIO()) as buf:
        # the default: a manifest written for these bytes, and it verifies
        plain = os.path.join(d, "plain.vdpack")
        assert climod._run(["vdpack", "pack", folder, "-o", plain]) == 0
        assert "every payload matches its MD5 and SHA-256" in buf.getvalue()
        built = vdpack.read_vdpack(plain)
        report = vdpack.index_report(list(built.files().items()))
        assert report["decoded"], report["error"]
        assert report["records"] == len(vdpack.pack_folder(folder))
        assert not (report["mismatched"] or report["unlisted"]
                    or report["missing"])
        # ... and the archive it lives in reproduces byte for byte when the
        # manifest is recomputed, so the default costs nothing on a rebuild
        again = vdpack.build_vdpack(vdpack.reindex_pack(
            list(built.files().items())))
        assert again == open(plain, "rb").read()
        # opting out is allowed, and then it has to say so
        bare = os.path.join(d, "bare.vdpack")
        buf.seek(0)
        buf.truncate()
        assert climod._run(["vdpack", "pack", folder, "-o", bare,
                            "--no-reindex"]) == 0
        assert "no `index` entry" in buf.getvalue()
        assert vdpack.index_entry(
            list(vdpack.read_vdpack(bare).files().items())) is None
        # `--keep-index` carries one over verbatim (no rewrite unless asked)
        kept = os.path.join(d, "kept.vdpack")
        buf.seek(0)
        buf.truncate()
        assert climod._run(["vdpack", "pack", folder, "-o", kept,
                            "--keep-index", plain]) == 0
        carried = vdpack.index_entry(
            list(vdpack.read_vdpack(kept).files().items()))
        original = vdpack.index_entry(list(built.files().items()))
        assert carried is not None and carried[1] == original[1]
        assert "no `index` entry" not in buf.getvalue()
        # asking for it explicitly keeps the mod's identity and fixes hashes
        fixed = os.path.join(d, "fixed.vdpack")
        assert climod._run(["vdpack", "pack", folder, "-o", fixed,
                            "--keep-index", plain, "--reindex"]) == 0
        report = vdpack.index_report(
            list(vdpack.read_vdpack(fixed).files().items()))
        assert report["decoded"] and not report["mismatched"]
        assert report["document"].uuid == vdpack.parse_index(
            vdpack.decode_index(original[1])).uuid


def test_vdpack_manifest_cli_writes_json():
    import contextlib
    import io
    import json
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    with tempfile.TemporaryDirectory() as d, \
            contextlib.redirect_stdout(io.StringIO()) as buf:
        # stdout form
        assert climod._run(["vdpack", "manifest", folder]) == 0
        doc = json.loads(buf.getvalue())
        assert doc["files"] >= 10 and doc["entries"]
        assert not doc["references"]["problems"]
        # file form, from the archive this time
        archive = os.path.join(d, "air.vdpack")
        assert climod._run(["vdpack", "pack", folder, "-o", archive]) == 0
        out = os.path.join(d, "manifest.json")
        assert climod._run(["vdpack", "manifest", archive, "-o", out]) == 0
        written = json.load(open(out, encoding="utf-8"))
        # the archive carries an `index` manifest of its own (`vdpack pack`
        # writes one by default); the loose folder does not
        assert [e for e in written["entries"] if e["path"] != "index"] \
            == doc["entries"]
        assert written["source"] == "air.vdpack"
        # a damaged archive is refused, not half-reported
        damaged = os.path.join(d, "damaged.vdpack")
        raw = bytearray(open(archive, "rb").read())
        raw[4:8] = struct.pack("<I", 1)
        open(damaged, "wb").write(bytes(raw))
        assert climod._run(["vdpack", "manifest", damaged]) == 1
        # a bundle whose references dangle exits 1 but still writes the JSON
        partial = os.path.join(d, "partial")
        os.makedirs(partial)
        open(os.path.join(partial, "character_neutrals_airelemental.g"),
             "wb").write(open(os.path.join(
                 folder, "character_neutrals_airelemental.g"), "rb").read())
        broken = os.path.join(d, "broken.json")
        assert climod._run(["vdpack", "manifest", partial, "-o", broken]) == 1
        assert json.load(open(broken, encoding="utf-8"))["files"] == 1


# --------------------------------------------------------------------------- #
#  skin weights (what a .g carries, and whether an export kept them)
# --------------------------------------------------------------------------- #
ANGEL_G = os.path.join(REPO, "Empire", "Angel", "character_empire_angel.g")


def test_weights_summary_of_a_real_unit():
    from d3tool import weights
    mesh = gfile.parse_geometry_file(open(ANGEL_G, "rb").read())
    parts = weights.summarise(mesh)
    assert [p.name for p in parts] == ["Myzrael_alpha", "empire_angel_wing",
                                       "empire_angel", "empire_angel_cloth"]
    body = [p for p in parts if p.name == "empire_angel"][0]
    assert body.influences == 3
    assert body.max_influences <= body.influences <= 4
    assert body.unweighted == 0
    assert abs(body.min_sum - 1.0) < 1e-6 and abs(body.max_sum - 1.0) < 1e-6
    # the head and the spine carry most of the body
    top = sorted(body.dominant.items(), key=lambda kv: -kv[1])[:2]
    assert [n for n, _c in top] == ["Bip01 Head", "Bip01 Spine1"]


def test_weights_diff_reports_nothing_for_an_untouched_file():
    from d3tool import weights
    mesh = gfile.parse_geometry_file(open(ANGEL_G, "rb").read())
    diff = weights.diff_weights(mesh, mesh)
    assert diff.compared and not diff.changed
    assert diff.samples == []


def test_weights_diff_finds_a_swapped_pair():
    """Flipping one vertex's two lanes must show up as exactly one changed
    vertex whose dominant bone moved — this is the check a modder runs on the
    `.g` Blender just wrote, with the game's own file as the baseline."""
    import copy
    from d3tool import weights
    donor = gfile.parse_geometry_file(open(ANGEL_G, "rb").read())
    edited = copy.deepcopy(donor)
    edited.raw = b""          # a parsed donor is otherwise re-emitted verbatim
    part = [p for p in edited.parts if p.name == "empire_angel"][0]
    bone_names = [b.name for b in part.bones]
    was = now = None
    for vertex in part.vertices:
        stored = list(vertex.stored_weights)
        # two real lanes on two different bones, or the swap is a no-op
        if (len(stored) >= 2 and stored[0] > 0.3 and stored[1] > 0.05
                and vertex.bones[0] != vertex.bones[1]):
            was = bone_names[vertex.bones[0]]
            now = bone_names[vertex.bones[1]]
            # swap the weights only: swapping the bones with them would just
            # re-order the same (bone, weight) pairs
            vertex.stored_weights = tuple([stored[1], stored[0]]
                                          + stored[2:])
            break
    assert was is not None, "no vertex with two distinct lanes"
    diff = weights.diff_weights(donor, edited, limit=5)
    assert diff.changed == 1, diff.changed
    assert diff.moved_bone == 1
    change = diff.samples[0]
    assert change.part == "empire_angel"
    assert change.old_dominant == was and change.new_dominant == now
    assert "->" in weights.describe_change(change)
    # and an untouched copy is reported as identical
    assert not weights.diff_weights(donor, copy.deepcopy(donor)).changed


def test_weights_cli_reports_and_compares():
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        assert climod._run(["weights", ANGEL_G, "--json"]) == 0
    doc = json.loads(buf.getvalue())
    assert [p["name"] for p in doc["parts"]][0] == "Myzrael_alpha"
    assert doc["parts"][2]["weights_on_vertex"] == 3
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        assert climod._run(["weights", ANGEL_G, "--against", ANGEL_G,
                            "--json"]) == 0
    doc = json.loads(buf.getvalue())
    assert doc["diff"]["changed"] == 0 and doc["diff"]["compared"] > 4000


def test_writer_keeps_a_grown_part_header_in_step():
    """The engine reads the vertex/triangle pair in every sub-mesh header, so a
    rebuilt part has to carry the counts of the data behind it.  A parsed
    prefix is 28 bytes of header + the 56-byte scene block, and the writer used
    to patch only a bare 28-byte prefix — an edited mesh then shipped the
    donor's counts and crashed the game on load."""
    import copy
    mesh = gfile.parse_geometry_file(open(ANGEL_G, "rb").read())
    mesh.raw = b""
    part = mesh.parts[1]
    part.raw = b""
    part.part_tail = b""
    part.vertices.append(copy.deepcopy(part.vertices[-1]))
    grown = len(part.vertices) - 1
    part.indices.extend([part.indices[-3], part.indices[-2], grown])
    data = gfile.write_geometry_file(mesh, dict(mesh.attrs))
    again = gfile.parse_geometry_file(data)
    body = again.parts[1]
    header = struct.unpack_from("<7I", body.part_prefix, 0)
    assert header[3] == len(body.vertices) == 4166, header
    assert header[4] == len(body.indices) // 3 == 5063, header
    assert gfile.count_mismatches(again) == []


def test_repair_fixes_a_header_that_lies_about_its_counts():
    """A file an older export wrote is beyond the writer's reach, so
    `d3tool repair` recomputes the counts in place.  The fixture is the real
    one the Angel came back with: the header still says 4165/5062 while the
    data holds 4171/5050."""
    data = open(ANGEL_G, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    body = mesh.parts[1]
    stale = bytearray(data)
    at = gfile._locate_prefix(data, body.part_prefix)
    assert at > 0
    struct.pack_into("<I", stale, at + 12, len(body.vertices) - 6)
    struct.pack_into("<I", stale, at + 16, len(body.indices) // 3 + 12)
    broken = bytes(stale)
    findings = gfile.count_mismatches(gfile.parse_geometry_file(broken))
    assert len(findings) == 1 and "header says" in findings[0], findings
    fixed, fixes = gfile.repair_counts(broken)
    assert fixes == findings
    assert len(fixed) == len(broken), "repair must not move anything else"
    assert gfile.count_mismatches(gfile.parse_geometry_file(fixed)) == []
    # a clean file comes back untouched
    same, none = gfile.repair_counts(data)
    assert none == [] and same == data
    # and a shipped donor passes the check
    assert gfile.count_mismatches(gfile.parse_geometry_file(data)) == []


def test_repair_cli_reports_and_writes():
    import contextlib
    import io
    data = bytearray(open(ANGEL_G, "rb").read())
    mesh = gfile.parse_geometry_file(bytes(data))
    body = mesh.parts[1]
    at = gfile._locate_prefix(bytes(data), body.part_prefix)
    struct.pack_into("<I", data, at + 16, len(body.indices) // 3 + 12)
    td = tempfile.mkdtemp(prefix="d3tool_repair_")
    try:
        broken = os.path.join(td, "character_empire_angel.g")
        open(broken, "wb").write(bytes(data))
        # a report-only run writes nothing
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            assert climod._run(["repair", broken]) == 0
        assert "header says" in buf.getvalue()
        assert open(broken, "rb").read() == bytes(data)
        # --in-place fixes it and says so
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            assert climod._run(["repair", broken, "--in-place"]) == 0
        assert "fixed 1 header" in buf.getvalue()
        fixed = gfile.parse_geometry_file(open(broken, "rb").read())
        assert gfile.count_mismatches(fixed) == []
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            assert climod._run(["repair", broken]) == 0
        assert "agree" in buf.getvalue()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_resource_root_finds_the_unit_folder_for_a_lod_export():
    """A ``*_lod`` model has no ``.ac``/``.scene`` of its own, so the main stem
    is what names the resource folder.  Guessing from the stem wrote
    ``resources\\characters\\character_empire_angel_lod`` into a generated
    ``_lod.ac`` — a folder that does not exist, so the engine could never
    resolve the model or its animations."""
    angel = os.path.join(REPO, "Empire", "Angel")
    scratch = os.path.join(tempfile.gettempdir(), "no_scene_here.gltf")
    assert climod._resource_root(scratch, "character_empire_angel_lod",
                                 (angel,)) == "resources\\characters\\empire\\angel"
    # the near model resolves the same directory
    assert climod._resource_root(scratch, "character_empire_angel",
                                 (angel,)) == "resources\\characters\\empire\\angel"
    # a scene that names several meshes still picks the one being exported
    assert climod._resource_dir_from_file(
        os.path.join(angel, "character_empire_angel.scene"),
        "character_empire_angel_lod") == "resources\\characters\\empire\\angel"
    # with nothing to look at the historical guess stays (a new asset)
    assert climod._resource_root(scratch, "character_empire_angel_lod") == \
        "resources\\characters\\character_empire_angel_lod"


def test_lod_export_writes_a_config_that_resolves():
    """End to end: exporting the Angel's lod model must not emit an `.ac` that
    points into a folder of its own making."""
    angel = os.path.join(REPO, "Empire", "Angel")
    lod = os.path.join(angel, "character_empire_angel_lod.g")
    out = tempfile.mkdtemp(prefix="d3tool_lod_")
    try:
        gltf = os.path.join(out, "character_empire_angel_lod.gltf")
        climod._export_gl(lod, None, gltf, None, quiet=True)
        climod._export(gltf, out, 0, quiet=True, donor_dir=angel)
        text = open(os.path.join(out, "character_empire_angel_lod.ac"),
                    encoding="utf-8").read()
        assert "resources\\characters\\empire\\angel\\character_empire_angel_lod.g" \
            in text, text[:400]
        scene = open(os.path.join(out, "character_empire_angel_lod.scene"),
                     encoding="utf-8").read()
        assert "resources\\characters\\character_empire_angel_lod" not in scene
    finally:
        shutil.rmtree(out, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  .char unit profiles
# --------------------------------------------------------------------------- #
_CHAR_SAMPLE = """// Unit Profile
//
id\t\t"test_leader";
unit_scene \t"Resources\\\\Characters\\\\test\\\\leader\\\\character_test_leader.scene"
unit_scene_lod "resources\\characters\\test\\leader\\character_test_leader_lod.scene",10;
icon "resources\\characters\\test\\leader\\icon_leader.t";
leader_icon "resources\\characters\\test\\\\leader\\\\icon_leader_ring.t"
/*
atck_zone 10 10 {
    0 0 0 0
}
*/
set 1 {
    weapon "resources\\Characters\\test\\leader\\weapon\\leader_set1_weapon.scene";
    hands  "resources\\Characters\\test\\leader\\hands\\leader_set1_hands.scene";
}
#include "desc\\test_leader_desc.char";
#include "stats\\test_leader_stats.char";
"""


def test_char_parses_the_corpus_quirks():
    """Indented block slots, statements that lost their `;`, a `/* … */`
    region that must stay dead, a flag with no value and a draw distance
    trailing the lod path — all of them are in the shipped Profiles."""
    from d3tool import char
    prof = char.parse_char(_CHAR_SAMPLE.encode("utf-8"))
    assert prof.id == "test_leader"
    assert char.scene_basename(prof.unit_scene) == "character_test_leader.scene"
    # the draw distance after the lod path is a value, not a second path
    assert prof.statement("unit_scene_lod").values[1] == "10"
    assert dict(prof.resources())["unit_scene_lod"].endswith("_lod.scene")
    # a key that lost its `;` still yields its value
    assert char.normalize_reference(prof.get("leader_icon")).endswith(
        "icon_leader_ring.t")
    # the commented-out attack zone produced nothing
    assert prof.statement("atck_zone") is None
    # blocks keep their slots, indentation notwithstanding
    assert len(prof.blocks) == 1
    assert prof.slots(0) == {
        "weapon": "resources\\Characters\\test\\leader\\weapon\\leader_set1_weapon.scene",
        "hands": "resources\\Characters\\test\\leader\\hands\\leader_set1_hands.scene",
    }
    assert [char.scene_basename(p) for p in prof.includes] == [
        "test_leader_desc.char", "test_leader_stats.char"]


def test_char_round_trips_bytes_with_cp1251_and_bom():
    from d3tool import char
    for raw in (_CHAR_SAMPLE.encode("utf-8"),
                _CHAR_SAMPLE.replace("\n", "\r\n").encode("utf-8"),
                "\ufeff".encode("utf-8") + _CHAR_SAMPLE.encode("utf-8"),
                "// \u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435\n"
                "id \"x\";\n".encode("cp1251")):
        prof = char.parse_char(raw)
        assert char.encode_char(prof) == raw


def test_char_set_id_or_scene_rewrites_one_line_only():
    from d3tool import char
    prof = char.parse_char(_CHAR_SAMPLE.encode("utf-8"))
    before = char.write_char(prof).splitlines()
    assert prof.set_id("test_leader_v2")
    assert prof.set_value("unit_scene",
                          "resources\\characters\\test\\leader\\other.scene")
    after = char.write_char(prof).splitlines()
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(changed) == 2, changed
    # the key, its tabs and the terminator survive the patch
    assert changed[0][1] == 'id\t\t"test_leader_v2";'
    assert changed[1][1].startswith('unit_scene \t"resources\\characters')


def test_char_reference_index_maps_resources_onto_the_corpus():
    """`resources\\characters\\empire\\angel\\…` is `Empire/Angel/…` here, and
    the same file is written with one, two or four separators."""
    from d3tool import char
    profile = os.path.join(REPO, "Profiles", "empire_angel.char")
    assert os.path.exists(profile), "the shipped Profiles/ tree is missing"
    prof = char.parse_char(open(profile, "rb").read())
    index = char.ReferenceIndex(REPO)
    scene = index.resolve(prof.unit_scene)
    assert scene and scene.endswith(
        os.path.join("Empire", "Angel", "character_empire_angel.scene"))
    for spelling in (prof.unit_scene,
                     prof.unit_scene.replace("\\\\", "\\"),
                     prof.unit_scene.replace("\\", "/")):
        assert index.resolve(spelling) == scene
    report = char.check_profile(prof, index)
    # the fx/ scenes are not part of the donor corpus
    assert all("fx" in line for line in report.problems), report.problems


def test_char_profile_for_scene_finds_the_profile_of_a_unit_folder():
    from d3tool import char
    folder = os.path.join(REPO, "Profiles")
    hit = char.profile_for_scene(char.find_profiles(folder),
                                 os.path.join(REPO, "Empire", "Angel"))
    assert hit is not None
    path, prof = hit
    assert os.path.basename(path) == "empire_angel.char"
    assert prof.id == "empire_angel"


def test_char_cli_show_list_find_and_validate():
    import contextlib
    import io
    from d3tool import char
    folder = os.path.join(REPO, "Profiles")
    with tempfile.TemporaryDirectory() as d, \
            contextlib.redirect_stdout(io.StringIO()) as buf:
        # show
        assert climod._run(["char", "show",
                            os.path.join(folder, "empire_angel.char"),
                            "--json"]) == 0
        doc = json.loads(buf.getvalue())
        assert doc["id"] == "empire_angel"
        assert doc["scene_file"] == "character_empire_angel.scene"
        assert doc["blocks"] == []
        # list, with the repo as the resolution root
        buf.seek(0)
        buf.truncate()
        assert climod._run(["char", "list", "--profiles", folder,
                            "--root", REPO, "--json"]) == 0
        listed = json.loads(buf.getvalue())
        assert len(listed) == 214
        angel = [e for e in listed if e["id"] == "empire_angel"][0]
        assert angel["resolved"] == "ok"
        assert any(e["resolved"] == "-" for e in listed)   # absent factions
        # find, by unit folder
        buf.seek(0)
        buf.truncate()
        assert climod._run(["char", "find",
                            os.path.join(REPO, "Empire", "Angel"),
                            "--profiles", folder]) == 0
        assert "empire_angel" in buf.getvalue()
        # set-id writes a new file and leaves the original alone
        original = open(os.path.join(folder, "empire_angel.char"),
                        "rb").read()
        out = os.path.join(d, "renamed.char")
        buf.seek(0)
        buf.truncate()
        assert climod._run(["char", "set-id",
                            os.path.join(folder, "empire_angel.char"),
                            "empire_angel_mod", "-o", out]) == 0
        patched = open(out, "rb").read()
        assert open(os.path.join(folder, "empire_angel.char"),
                    "rb").read() == original
        assert patched != original
        assert char.parse_char(patched).id == "empire_angel_mod"
        assert char.parse_char(patched).unit_scene == \
            char.parse_char(original).unit_scene


def test_char_shipped_corpus_round_trips_and_ids_match():
    """Every one of the 874 shipped profiles must come back byte for byte,
    and the 214 that carry an `id` must be exactly the top-level ones."""
    from d3tool import char
    folder = os.path.join(REPO, "Profiles")
    if not os.path.isdir(folder):
        return
    files = char.find_profiles(folder)
    assert len(files) == 874
    top, mismatched = 0, []
    for path in files:
        raw = open(path, "rb").read()
        prof = char.parse_char(raw)
        assert char.encode_char(prof) == raw, path
        if not prof.id:
            continue
        top += 1
        if prof.id != os.path.splitext(os.path.basename(path))[0]:
            mismatched.append(os.path.basename(path))
    assert top == 214
    # the engine resolves a profile by its `id`, not by the file name, and
    # three shipped files prove it (one of them even has a space in its name)
    assert sorted(mismatched) == ["story_berthrezen.char",
                                  "story_myzrael_undead.char",
                                  "undead_templar .char"]


def test_vdpack_validates_a_packed_char_profile():
    """A `.char` names its includes, its scene and its icons: inside a pack
    those are unit-local, so a missing one is a finding, while the engine's
    global `fx/` tree is only a note."""
    from d3tool import char, vdpack
    prof = open(os.path.join(REPO, "Profiles", "elves_rod-1.char"),
                "rb").read()
    parsed = char.parse_char(prof)
    # a Profiles-only pack: the scenes and includes sit elsewhere -> notes
    report = vdpack.reference_findings({"Profiles/elves_rod-1.char": prof})
    assert report.problems == []
    assert len([n for n in report.notes if " includes " in n]) == 3
    # 8 unit-local paths: the scene, its lod, and the six icons
    assert len([n for n in report.notes if "->" in n]) == 8
    assert all("outside the packed tree" in n for n in report.notes)
    # with the includes and the scene inside, only the icons are left over
    files = {"Profiles/elves_rod-1.char": prof,
             "Profiles/characters/elves/rod-1/character_elves_rod-1.scene":
                 b'node "root" {}\n'}
    for inc in parsed.includes:
        files["Profiles/" + inc.replace("\\", "/")] = b'id "";\n'
    report = vdpack.reference_findings(files)
    assert report.problems == []
    assert not any(" includes " in n for n in report.notes)
    assert not any("-> character_elves_rod-1.scene" in n
                   for n in report.notes)
    assert len([n for n in report.notes if "icon" in n]) == 6
    # one icon inside the pack is one icon less to complain about
    files["Profiles/characters/elves/rod-1/icon_rod-1.t"] = b""
    report = vdpack.reference_findings(files)
    left = [n for n in report.notes if "icon" in n]
    assert len(left) == 5
    assert not any("-> icon_rod-1.t " in n for n in left)




# --------------------------------------------------------------------------- #
#  one attribute at a time: the part the engine will not draw without
# --------------------------------------------------------------------------- #

def test_gattr_inserts_an_attribute_and_keeps_the_part_readable():
    """A part that lost `new_vertex_weights_format` gets it back.

    The engine reads a part's vertex-weight block by the format the block
    declares, so a skinned part without that attribute is drawn as nothing
    while a reader that takes the stride from `weights_on_vertex` (this
    package included) sees a good mesh — exactly the Angel's appended sword,
    which never showed up in game.
    """
    path = os.path.join(REPO, "Empire", "Angel", "character_empire_angel.g")
    data = open(path, "rb").read()
    part = gfile.iter_parts(gfile.parse_geometry_file(data))[3]
    assert "port_marker" not in dict(part.attr_items)

    out = gfile.insert_part_attr(data, part, "port_marker", "7")
    assert len(out) == len(data) + len(gfile._attr_pair("port_marker", "7"))
    assert out.startswith(data[:len(data) // 2]) is False or True  # shifted

    again = gfile.iter_parts(gfile.parse_geometry_file(out))[3]
    assert again.attrs["port_marker"] == "7"
    assert again.vertex_count == part.vertex_count
    assert again.tri_count == part.tri_count
    assert [v.position for v in again.vertices] == [v.position for v in part.vertices]
    assert [b.name for b in again.bones] == [b.name for b in part.bones]

    # a value of the same length rewrites in place: the payload keeps its size
    same = gfile.replace_part_attr(out, again, "port_marker", "8")
    assert len(same) == len(out)
    assert gfile.iter_parts(
        gfile.parse_geometry_file(same))[3].attrs["port_marker"] == "8"

    # a value of a DIFFERENT length has to rewrite its own length prefix; when
    # it does not, every reader after the block takes the old width and the
    # parts stacked behind it disappear (`gattr --replace` once did exactly
    # that, on the weapon slot of a two-part unit)
    marker = gfile.iter_parts(gfile.parse_geometry_file(same))[3]
    longer = gfile.replace_part_attr(same, marker, "port_marker", "long-value")
    assert len(longer) != len(same)
    reparsed = list(gfile.iter_parts(gfile.parse_geometry_file(longer)))
    assert len(reparsed) == 4, [p.name for p in reparsed]
    assert reparsed[3].attrs["port_marker"] == "long-value"
    assert reparsed[3].vertex_count == part.vertex_count
    shorter = gfile.replace_part_attr(longer, reparsed[3], "port_marker", "1")
    reparsed = list(gfile.iter_parts(gfile.parse_geometry_file(shorter)))
    assert len(reparsed) == 4, [p.name for p in reparsed]
    assert reparsed[3].attrs["port_marker"] == "1"


def test_gattr_cli_reports_and_reproduces_the_patch():
    src = os.path.join(REPO, "Empire", "Angel", "character_empire_angel.g")
    out = os.path.join(tempfile.mkdtemp(prefix="gattr-"), "angel.g")
    rc = climod.main(["gattr", src, "--part", "3",
                      "--insert", "port_marker=1", "-o", str(out)])
    assert rc == 0
    patched = open(out, "rb").read()
    assert len(patched) > os.path.getsize(src)
    assert gfile.iter_parts(
        gfile.parse_geometry_file(patched))[3].attrs["port_marker"] == "1"
    # running it again is a no-op, not a second insert
    before = len(patched)
    rc = climod.main(["gattr", str(out), "--part", "3",
                      "--insert", "port_marker=1"])
    assert rc == 0
    assert os.path.getsize(out) == before


def test_every_corpus_weapon_part_declares_its_weight_format():
    """The invariant the Angel's sword broke: retail parts all declare it.

    A part that carries a weight block but no `new_vertex_weights_format` is
    read with the old stride by the engine, so it renders as nothing.  This
    test states the rule on the corpus (a weapon part is the smallest block
    that is still skinned to a single bone).
    """
    checked = 0
    for path in sorted(glob.glob(os.path.join(REPO, "Empire", "*", "*.g"))):
        if path.endswith("_lod.g"):
            continue
        mesh = gfile.parse_geometry_file(open(path, "rb").read())
        if mesh.parse_error:
            continue
        for part in gfile.iter_parts(mesh):
            if len(part.bones) != 1 or not part.vertex_count:
                continue
            if part.attrs.get("morph"):
                continue
            checked += 1
            assert part.attrs.get("new_vertex_weights_format") == "1", (
                f"{os.path.basename(path)}: {part.name!r} is skinned but "
                "does not declare new_vertex_weights_format")
    assert checked > 10, checked


def test_added_gobj_keeps_the_scene_line_endings_and_one_material():
    """An inserted `gobj` must not rewrite the file it lands in.

    Two things went wrong in the field: the `.scene` was read with universal
    newlines, so adding one block turned a CRLF payload into an LF one (every
    byte of it different from the source pack's), and the block was cloned
    from the LOD *body*, so the new sword part was handed the body's second
    material (`material1_diffuse` of the hair) that its own `.g` part does
    not have.
    """
    import copy as copymod
    from d3tool import gfile as gmod
    from d3tool import scene as scenemod
    unit = os.path.join(REPO, "Empire", "Angel", "character_empire_angel_lod")
    raw = open(unit + ".scene", "rb").read()
    lf = raw.decode("latin-1")
    crlf = lf.replace("\n", "\r\n")
    mesh = gmod.parse_geometry_file(open(unit + ".g", "rb").read())
    parts = list(gmod.iter_parts(mesh))
    extra = copymod.copy(parts[-1])
    extra.name = "test_weapon"

    # a CRLF file stays CRLF, line for line
    patched, added = scenemod.ensure_part_gobjs(crlf, parts + [extra])
    assert added == ["test_weapon"]
    lines = patched.split("\n")[:-1]
    assert lines and all(line.endswith("\r") for line in lines), \
        [line for line in lines if not line.endswith("\r")][:3]
    # ... and an LF file stays LF
    plain, _ = scenemod.ensure_part_gobjs(lf, parts + [extra])
    assert all(not line.endswith("\r") for line in plain.split("\n")[:-1])

    block = scenemod.parse_scene(patched).find_all("gobj")[-1]
    attrs = [line for line in block.items
             if isinstance(line, str) and 'attr "material' in line]
    assert any("material0_diffuse" in line for line in attrs), attrs
    assert not any("material1_" in line for line in attrs), attrs
    assert scenemod.node_attr(block, "materials_num") == "1"



def test_scene_node_block_is_the_meshs_own_bounding_volume():
    """The 56-byte record before a part's attributes is derived geometry.

    Fourteen floats: box centre, three zeros, box max, box min, the radius
    to the far corner and -1.  Measured over the corpus every non-morph part
    reproduces from its own vertices, which is what makes it safe to
    recompute for a part that was appended from another `.g` instead of
    keeping the donor's box -- the Angel's sword carried a record from a
    completely different model (centre 2.83 m up, radius 4.45), so the engine
    had nothing sensible to place or cull it by.
    """
    from d3tool import gfile as gmod
    checked = 0
    morph_checked = 0
    for rel in (os.path.join("Empire", "Angel", "character_empire_angel.g"),
                os.path.join("Empire", "Knight", "character_empire_knight.g"),
                os.path.join("Empire", "Paladin", "character_empire_paladin.g")):
        data = open(os.path.join(REPO, rel), "rb").read()
        for part in gmod.iter_parts(gmod.parse_geometry_file(data)):
            prefix = getattr(part, "part_prefix", b"") or b""
            if len(prefix) < 84:
                continue
            stored = prefix[28:84]
            got = gmod.scene_node_block(part, stored)
            gotf = struct.unpack("<14f", got)
            wantf = struct.unpack("<14f", stored)
            # box and radius are exact; the centre can differ in the last
            # bit (the corpus stores a 2e-07 residue where the box is
            # symmetric, this formula produces 0.0)
            if part.morph:
                # the centre of a morph part is its animated extent, which
                # the writer keeps from the donor; box and radius still match
                assert gotf[6:12] == wantf[6:12], (rel, part.name)
                assert abs(gotf[12] - wantf[12]) <= 1e-5 * max(1.0, wantf[12])
                morph_checked += 1
            else:
                assert gotf[3:12] == wantf[3:12], (rel, part.name, got.hex(" "), stored.hex(" "))
                assert abs(gotf[12] - wantf[12]) <= 1e-5 * max(1.0, wantf[12])
                assert all(abs(a - b) < 1e-5 for a, b in zip(gotf[:3], wantf[:3])), \
                    (rel, part.name, gotf[:3], wantf[:3])
                checked += 1
    assert checked >= 10, checked
    assert morph_checked >= 1, morph_checked


def test_a_part_without_a_donor_block_gets_its_own_box_and_weights_key():
    """Rebuilding a part re-derives the box and declares the weight format.

    A mesh appended from another unit used to keep the donor's bounding
    record and the donor's attribute keys.  Both are what the engine reads
    before it draws anything: without `new_vertex_weights_format` the weight
    block is read five bytes short apiece and the part is drawn as nothing,
    and with a box from another model the part has no volume of its own.
    """
    from d3tool import gfile as gmod
    kn = open(os.path.join(REPO, "Empire", "Knight",
                           "character_empire_knight.g"), "rb").read()
    donor = list(gmod.iter_parts(gmod.parse_geometry_file(kn)))[1]
    assert "new_vertex_weights_format" in donor.attrs

    block = gmod._part_block(donor)
    assert block[:4] == struct.pack("<I", 2)
    assert b"new_vertex_weights_format\x00" in block
    # a part nobody moved keeps the bounding record it came with, byte for
    # byte: recomputing it from (min+max)/2 lands a byte or two away from what
    # the file says, and the engine's own numbers are not derivable that way
    # (measured over the corpus: only 19 of 376 blocks agree to the bit)
    assert block[28:84] == donor.part_prefix[28:84]

    # ... but a part whose vertices were moved gets a box of its own
    moved = copy.copy(donor)
    moved.vertices = [dataclasses.replace(
        v, position=(v.position[0] + 3.0, v.position[1], v.position[2]))
        for v in donor.vertices]
    moved.raw = b""
    shifted = gmod._part_block(moved)
    assert shifted[28:84] != donor.part_prefix[28:84]
    assert shifted[28:84] == gmod.scene_node_block(moved, donor.part_prefix[28:84])

    # a part whose block never carried the key gets it back
    stripped = copy.copy(donor)
    stripped.raw = b""
    stripped.attr_items = [(k, v) for k, v in donor.attr_items
                           if k != "new_vertex_weights_format"]
    stripped.attrs = {k: v for k, v in donor.attrs.items()
                      if k != "new_vertex_weights_format"}
    fixed = gmod._part_block(stripped)
    assert b"new_vertex_weights_format\x00" in fixed
    # ... and it is ordered the way the corpus writes it: before the counts
    assert fixed.index(b"new_vertex_weights_format") < fixed.index(b"vertexs_weights_num")
    # a morph part keeps the donor centre (its box is the animated extent)
    morph = [pt for pt in gmod.iter_parts(gmod.parse_geometry_file(
        open(os.path.join(REPO, "Empire", "Paladin",
                          "character_empire_paladin.g"), "rb").read()))
        if pt.morph and len(getattr(pt, "part_prefix", b"") or b"") >= 84]
    if morph:
        pt = morph[0]
        assert gmod.scene_node_block(pt, pt.part_prefix[28:84])[:12] == pt.part_prefix[28:40]



def _dist(a, b):
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5


def _weapon_clouds(seed=7):
    """A reference weapon along +Y from its grip and a sword along -X."""
    import random
    rng = random.Random(seed)
    ref = [[rng.gauss(0, 0.02), t, rng.gauss(0, 0.02)]
           for t in [i / 50 for i in range(51)]]
    src = [[5.0 - t * 1.2, -2.0 + rng.gauss(0, 0.02), 3.0 + rng.gauss(0, 0.02)]
           for t in [i / 50 for i in range(51)]]
    return ref, [0.0, 0.0, 0.0], src, [5.0, -2.0, 3.0]


def test_weapon_axis_is_the_principal_component():
    """The long axis comes from the cloud, not from the bounding box."""
    d = weaponmod._unit([1.0, 2.0, -0.5])
    pts = [[t * d[0] + (0.01 if i % 2 else -0.01), t * d[1], t * d[2]]
           for i, t in enumerate([j / 40 for j in range(41)])]
    axis = weaponmod.main_axis(pts, [0.0, 0.0, 0.0])
    assert weaponmod.angle_between(axis, d) < 3.0, axis
    # ... and it points away from the grip, whichever end that is
    flipped = weaponmod.main_axis(pts, [pts[-1][0] * 1.01, pts[-1][1],
                                        pts[-1][2]])
    assert weaponmod.angle_between(flipped, [-x for x in d]) < 3.0


def test_weapon_alignment_puts_the_grip_on_the_grip():
    """The matrix is rigid: grip onto grip, axis onto axis, size unchanged."""
    ref, ref_grip, src, src_grip = _weapon_clouds()
    mat, info = weaponmod.align_matrix(ref, ref_grip, src, src_grip)
    assert info["axis_found"] is True
    got = weaponmod.place([src_grip], mat)[0]
    assert max(abs(got[i] - ref_grip[i]) for i in range(3)) < 1e-9
    moved = weaponmod.place(src, mat)
    assert weaponmod.angle_between(weaponmod.main_axis(moved, ref_grip),
                                   weaponmod.main_axis(ref, ref_grip)) < 2.0
    # every distance survives: no scale, no shear
    assert abs(_dist(src[0], src[-1]) - _dist(moved[0], moved[-1])) < 1e-9
    # the blade points the way the reference weapon points: forward along
    # its axis (the sword is longer than the mace, so it ends past the head)
    axis = weaponmod.main_axis(ref, ref_grip)
    rel = [moved[-1][i] - ref_grip[i] for i in range(3)]
    along = sum(rel[i] * axis[i] for i in range(3))
    across = _dist(rel, [along * axis[i] for i in range(3)])
    assert along > 1.1 and across < 0.05, (along, across)


def test_weapon_roll_spins_about_the_reference_axis_only():
    ref, ref_grip, src, src_grip = _weapon_clouds()
    mat, _ = weaponmod.align_matrix(ref, ref_grip, src, src_grip, roll_deg=90.0)
    got = weaponmod.place([src_grip], mat)[0]
    assert max(abs(got[i] - ref_grip[i]) for i in range(3)) < 1e-9
    moved = weaponmod.place(src, mat)
    assert weaponmod.angle_between(weaponmod.main_axis(moved, ref_grip),
                                   weaponmod.main_axis(ref, ref_grip)) < 2.0


def test_weapon_alignment_survives_a_mesh_without_an_axis():
    """A single-vertex "mesh" is only translated, never spun."""
    ref, ref_grip, src, src_grip = _weapon_clouds()
    mat, info = weaponmod.align_matrix(ref, ref_grip, [[1.0, 1.0, 1.0]],
                                       src_grip)
    assert info["axis_found"] is False
    got = weaponmod.place([[1.0, 1.0, 1.0]], mat)[0]
    want = [1.0 - src_grip[i] + ref_grip[i] for i in range(3)]
    assert max(abs(got[i] - want[i]) for i in range(3)) < 1e-9



def _retail_part(rel_path, wanted):
    mesh = gfile.parse_geometry_file(
        open(os.path.join(REPO, *rel_path.split("/")), "rb").read())
    for pt in gfile.iter_parts(mesh):
        if wanted in pt.name:
            return pt
    raise AssertionError(f"no part {wanted!r} in {rel_path}")


def test_weapon_axis_of_a_retail_weapon_is_its_long_axis():
    """On shipped geometry the principal axis is the barrel, not the hilt."""
    for path, name in (("Empire/Hierophant/character_empire_hierophant.g",
                        "weapon_empire_hierophant"),
                       ("Empire/Paladin/character_empire_paladin.g",
                        "paladin_sword"),
                       ("Neutrals/OrcKing/character_neutrals_orcking.g",
                        "weapon_neutral_orcking")):
        pt = _retail_part(path, name)
        pts = [list(v.position) for v in pt.vertices]
        size = weaponmod.bbox_size(pts)
        longest = max(range(3), key=lambda i: size[i])
        axis = weaponmod.main_axis(pts, [0.0, 0.0, 0.0])
        assert abs(axis[longest]) > 0.95, (name, size, axis)


def test_weapon_placement_of_retail_parts_is_rigid():
    """Flail and sword from the corpus: grip onto grip, rigid, axis aligned."""
    flail = _retail_part("Empire/Inquisitor/character_empire_inquisitor.g",
                         "weapon_empire_inquisitor")
    sword = _retail_part("Empire/Knight/character_empire_knight.g",
                         "empire_knight_sword")
    ref = [list(v.position) for v in flail.vertices]
    src = [list(v.position) for v in sword.vertices]
    # the flail's origin is its grip (it spans x -0.07 .. 1.90 from it); the
    # sword's handle end is where an artist would put the origin
    ref_grip = [0.0, 0.0, 0.0]
    src_grip = min(src, key=lambda p: sum(c * c for c in p))[:]
    mat, info = weaponmod.align_matrix(ref, ref_grip, src, src_grip)
    assert info["axis_found"] is True
    moved = weaponmod.place(src, mat)
    grip = weaponmod.place([src_grip], mat)[0]
    assert max(abs(grip[i] - ref_grip[i]) for i in range(3)) < 1e-9
    assert weaponmod.angle_between(weaponmod.main_axis(moved, ref_grip),
                                   weaponmod.main_axis(ref, ref_grip)) < 2.0
    assert abs(_dist(src[0], src[-1]) - _dist(moved[0], moved[-1])) < 1e-9
    assert len(moved) == len(src) == 136



def test_weapon_flip_turns_the_blade_round():
    """``Flip`` reverses the axis about the grip — a mid-handle origin needs it."""
    ref, ref_grip, src, src_grip = _weapon_clouds()
    mat, info = weaponmod.align_matrix(ref, ref_grip, src, src_grip,
                                       flip=True)
    assert info.get("flipped") is True
    moved = weaponmod.place(src, mat)
    assert weaponmod.angle_between(weaponmod.main_axis(moved, ref_grip),
                                   weaponmod.main_axis(ref, ref_grip)) > 178.0
    grip = weaponmod.place([src_grip], mat)[0]
    assert max(abs(grip[i] - ref_grip[i]) for i in range(3)) < 1e-9
    assert abs(_dist(src[0], src[-1]) - _dist(moved[0], moved[-1])) < 1e-9
    # ... and roll + flip compose without moving the grip either
    mat2, _ = weaponmod.align_matrix(ref, ref_grip, src, src_grip,
                                     roll_deg=45.0, flip=True)
    grip2 = weaponmod.place([src_grip], mat2)[0]
    assert max(abs(grip2[i] - ref_grip[i]) for i in range(3)) < 1e-9


def test_vdpack_scan_names_the_pack_the_loader_rejects():
    """`vdpack scan` answers what `run.log` cannot: *which* installed pack
    carries the payload the loader answers `str_not_valid_hash_for_file` for —
    the log names the file, never the archive it came from."""
    import contextlib
    import io
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    with tempfile.TemporaryDirectory() as d, \
            contextlib.redirect_stdout(io.StringIO()) as buf:
        good = vdpack.read_vdpack(os.path.join(d, "good.vdpack")) \
            if False else None
        assert climod._run(["vdpack", "pack", folder,
                            "-o", os.path.join(d, "good.vdpack")]) == 0
        files = vdpack.pack_folder(folder)
        files = vdpack.reindex_pack(files)
        # the same archive with one payload changed and the old manifest kept:
        # a mod rebuilt without `--reindex`, the crash this tool explains
        stale = [(n, d + b"\x00") if n.lower().endswith(".g") else (n, d)
                 for n, d in files]
        with open(os.path.join(d, "stale.vdpack"), "wb") as fh:
            fh.write(vdpack.build_vdpack(stale))
        rows = {row["file"]: row for row in vdpack.scan_packs([d])}
        assert set(rows) == {"good.vdpack", "stale.vdpack"}
        assert rows["good.vdpack"]["state"] == "ok"
        assert rows["stale.vdpack"]["state"] == "stale"
        assert len(rows["stale.vdpack"]["mismatched"]) >= 1
        assert all(path.lower().endswith(".g")
                   for path in rows["stale.vdpack"]["mismatched"])
        findings = vdpack.scan_findings(list(rows.values()))
        text = "\n".join(level + ": " + line for level, line in findings)
        assert "ok: good.vdpack" in text
        assert "payload(s) differ from their recorded MD5/SHA-256" in text
        assert "str_not_valid_hash_for_file" in text
        assert "vdpack pack --like stale.vdpack --reindex" in text
        # the CLI exits 1 under --strict only while something is rejected
        buf.seek(0)
        buf.truncate()
        assert climod._run(["vdpack", "scan", d, "--strict"]) == 1
        assert "rejected file(s)" in buf.getvalue()
        assert climod._run(["vdpack", "scan",
                            os.path.join(d, "good.vdpack")]) == 0


def test_vdpack_scan_knows_where_the_loader_looks_for_mods():
    """A mod is mounted from `$(UserFolder)\\Mods`; anywhere else the game
    answers `mod '<name>' not found` and the pack never loads at all."""
    import contextlib
    import io
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    with tempfile.TemporaryDirectory() as d, \
            contextlib.redirect_stdout(io.StringIO()):
        game = os.path.join(d, "game")
        mods = os.path.join(game, "Mods")
        os.makedirs(mods)
        assert climod._run(["vdpack", "pack", folder, "-o",
                            os.path.join(game, "loose.vdpack")]) == 0
        assert climod._run(["vdpack", "pack", folder, "-o",
                            os.path.join(mods, "AirElemental.vdpack")]) == 0
        rows = {row["file"]: row for row in vdpack.scan_packs([game])}
        # the folder's `Mods` subfolder is scanned too: that is what matters
        assert set(rows) == {"loose.vdpack", "AirElemental.vdpack"}
        assert rows["loose.vdpack"]["mod"] is False
        assert rows["AirElemental.vdpack"]["mod"] is True
        text = "\n".join(line for _level, line in
                         vdpack.scan_findings(list(rows.values())))
        assert "loose.vdpack: a mod pack outside" in text
        assert "AirElemental.vdpack: a mod pack outside" not in text
        # the manifest's own `Name` is reported when it is not the file name
        # the game matches `sModifications` against
        mismatch = os.path.join(d, "RenamedPack.vdpack")
        files = vdpack.pack_folder(folder)
        text_index, blob = vdpack.build_index(files, name="AirElemental")
        with open(mismatch, "wb") as fh:
            fh.write(vdpack.build_vdpack(files + [("index", blob)]))
        row = vdpack.scan_pack(mismatch)
        assert row["index_name"] == "AirElemental"
        assert row["state"] == "ok"
        lines = "\n".join(line for _level, line in
                          vdpack.scan_findings([row]))
        assert "'AirElemental' but the game names it after the file" in lines
        assert "mod:RenamedPack" in lines


def test_vdpack_scan_reports_two_mods_carrying_one_path():
    """`intersects other mods` (0x8785d8) is the third way a pack silently
    never loads: two mods ship the same resource and only one is mounted."""
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    files = vdpack.reindex_pack(vdpack.pack_folder(folder))
    other = [(n, data + b"!") if n.lower().endswith(".scene") else (n, data)
             for n, data in files]
    other = vdpack.reindex_pack(other)
    with tempfile.TemporaryDirectory() as d:
        mods = os.path.join(d, "Mods")
        os.makedirs(mods)
        for name, bundle in (("first", files), ("second", other)):
            with open(os.path.join(mods, name + ".vdpack"), "wb") as fh:
                fh.write(vdpack.build_vdpack(bundle))
        rows = vdpack.scan_packs([mods])
        assert [row["state"] for row in rows] == ["ok", "ok"]
        findings = vdpack.scan_findings(rows)
        clash = [line for level, line in findings
                 if level == "warn" and "intersects other mods" in line]
        assert clash, findings
        assert "first.vdpack and second.vdpack carry" in clash[0]
        assert "index" not in clash[0]
        # and the JSON the CLI prints is the same answer, machine readable
        import contextlib
        import io
        import json
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            assert climod._run(["vdpack", "scan", mods, "--json"]) == 0
        doc = json.loads(buf.getvalue())
        assert len(doc["packs"]) == 2
        assert {row["file"] for row in doc["packs"]} == {"first.vdpack",
                                                         "second.vdpack"}
        assert any("intersects other mods" in f["text"]
                   for f in doc["findings"])


def test_vdpack_scan_crosses_a_run_log_against_the_packs():
    """`run.log` says `Index error : str_not_valid_hash_for_file : <path>` and
    never the archive; the scan knows the archive — crossed, the two name the
    pack to rebuild, and say when no pack is to blame at all."""
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    files = vdpack.reindex_pack(vdpack.pack_folder(folder))
    stale = [(n, data + b"\x00") if n.lower().endswith(".g") else (n, data)
             for n, data in files]
    rejected = [n for n, _ in stale if n.lower().endswith(".g")][0]
    with tempfile.TemporaryDirectory() as d:
        good = os.path.join(d, "good.vdpack")
        bad = os.path.join(d, "bad.vdpack")
        with open(good, "wb") as fh:
            fh.write(vdpack.build_vdpack(files))
        with open(bad, "wb") as fh:
            fh.write(vdpack.build_vdpack(stale))
        log_path = os.path.join(d, "run.log")
        # a Windows log: CRLF, no translation on the way out
        with open(log_path, "w", encoding="utf-8", newline="") as fh:
            fh.write("[  ] Game version: 1.03\r\n")
            fh.write("[WW] resources0.vdpack:resources\\x.effect(9) : odd\r\n")
            fh.write(f"[EE] scene : Index error : str_not_valid_hash_for_file"
                     f" : {rejected}\r\n")
            fh.write("[EE] scene : Index error : str_not_valid_hash_for_file"
                     " : resources\\nowhere\\ghost.g\r\n")
            fh.write("[EE] mod 'ruru' not found\r\n")
        log = vdpack.read_run_log(log_path)
        assert log["lines"] == 5
        assert log["rejected"] == [rejected, "resources\\nowhere\\ghost.g"]
        assert log["missing_mods"] == ["ruru"]
        assert log["packs"] == ["resources0.vdpack"]
        findings = vdpack.log_findings(vdpack.scan_packs([d]), log)
        text = "\n".join(level + ": " + line for level, line in findings)
        # the path with a stale carrier: name it and say how to fix it
        assert f"run.log rejects {rejected}" in text
        assert "bad.vdpack (stale)" in text
        assert "vdpack pack --like bad.vdpack --reindex" in text
        # the path nobody carries: not a pack problem at all
        assert "no scanned pack carries it" in text
        # a mod the log cannot find, and no archive answers to
        assert "mod 'ruru' not found` and no scanned archive" in text
        # ... and when the pack *is* there, the message says where it lives
        named = os.path.join(d, "Mods")
        os.makedirs(named)
        with open(os.path.join(named, "ruru.vdpack"), "wb") as fh:
            fh.write(vdpack.build_vdpack(files))
        rows = vdpack.scan_packs([d])
        text = "\n".join(line for _lvl, line in
                         vdpack.log_findings(rows, log))
        assert "the scan found a pack for it at" in text


def test_vdpack_pack_names_the_mod_after_its_file():
    """The loader resolves `sModifications=mod:<name>` to `Mods\\<name>.vdpack`,
    so a manifest built from scratch has to call itself what the file is
    called — otherwise the archive and the game disagree about its name."""
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "AirElemental.vdpack")
        assert climod._run(["vdpack", "pack", folder, "-o", out]) == 0
        doc = vdpack.index_document(list(vdpack.read_vdpack(out).files().items()))
        assert doc.name == "AirElemental"
        assert doc.type == "Mod"
        named = os.path.join(d, "something-else.vdpack")
        assert climod._run(["vdpack", "pack", folder, "-o", named,
                            "--name", "Ruru"]) == 0
        doc = vdpack.index_document(
            list(vdpack.read_vdpack(named).files().items()))
        assert doc.name == "Ruru"
        # rebuilding an archive that already carries an identity keeps it
        files = list(vdpack.read_vdpack(named).files().items())
        again = vdpack.index_document(vdpack.reindex_pack(files, name="Ignored"))
        assert again.name == "Ruru"


def test_bridge_finds_exports_and_builds_the_right_commands():
    """The AstraForge bridge only builds command lines and reads reports — so
    the parts that can be wrong without a GPU or Blender are testable here:
    which export it picks, what it tells Blender, and what it gates on."""
    import json
    sys.path.insert(0, os.path.join(REPO, "bridge"))
    import astra_d3
    from d3tool import vdpack

    with tempfile.TemporaryDirectory() as d:
        assets = os.path.join(d, "data", "library", "assets")
        old = os.path.join(assets, "11111111-1111-1111-1111-111111111111",
                           "versions", "v1", "old.glb")
        new = os.path.join(assets, "22222222-2222-2222-2222-222222222222",
                           "exports", "knight.glb")
        for path in (old, new):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"glTF")
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        rows = astra_d3.discover_glbs(assets)
        assert [os.path.basename(r["path"]) for r in rows] == ["knight.glb",
                                                                "old.glb"]
        assert rows[0]["asset"] == "22222222-2222-2222-2222-222222222222"
        assert rows[0]["is_export"] and not rows[1]["is_export"]
        assert astra_d3.newest_glb(assets)["path"] == new
        assert astra_d3.discover_glbs(os.path.join(d, "nope")) == []

        donor = os.path.join(d, "inquisitor")
        os.makedirs(donor)
        cfg = astra_d3.Config({
            "studio": os.path.join(d, "studio"),
            "blender": os.path.join(d, "blender.exe"),
            "assets": assets,
            "donor": donor,
            "mods": os.path.join(d, "Mods"),
            "mod_name": "AstraInq",
            "reference_pack": os.path.join(d, "ruru.vdpack"),
            "d3tool_repo": REPO,
            "python": "py",
        })
        cmd = astra_d3.blender_command(cfg, new, "character_empire_inquisitor")
        assert cmd[0].endswith("blender.exe") and cmd[1] == "-b"
        assert "--factory-startup" in cmd and "--python" in cmd
        assert cmd[cmd.index("--python") + 1].endswith("build_d3.py")
        tail = cmd[cmd.index("--") + 1:]
        assert tail[0] == new
        assert tail[1].endswith(os.path.join("inquisitor",
                                             "character_empire_inquisitor.g"))
        assert tail[2].endswith(".build.json")
        assert tail[tail.index("--donor") + 1] == donor
        assert tail[tail.index("--weights") + 1] == "AUTO"
        assert tail[tail.index("--d3tool") + 1] == REPO
        assert "--no-anim" not in tail
        cfg["anim"] = False
        assert "--no-anim" in astra_d3.blender_command(cfg, new, "u")
        cfg["anim"] = True

        pack = astra_d3.pack_command(cfg)
        assert pack[:3] == ["py", "-m", "d3tool"]
        assert "pack" in pack and "--name" in pack
        assert pack[pack.index("--name") + 1] == "AstraInq"
        assert pack[pack.index("--like") + 1] == cfg["reference_pack"]
        assert pack[pack.index("-o") + 1].endswith("AstraInq.vdpack")
        scan = astra_d3.scan_command(cfg)
        assert scan[-1] == "--json" and "scan" in scan
        # a missing report is not an exception: the caller reports the failure
        assert astra_d3.read_build_report(os.path.join(d, "nothing.json")) == {}

        # the gate reads `vdpack scan --json`, so build one for real
        folder = os.path.join(REPO, "Neutrals", "AirElemental")
        files = vdpack.reindex_pack(vdpack.pack_folder(folder))
        good = os.path.join(d, "good.vdpack")
        with open(good, "wb") as fh:
            fh.write(vdpack.build_vdpack(files))
        stale = os.path.join(d, "stale.vdpack")
        with open(stale, "wb") as fh:
            fh.write(vdpack.build_vdpack(
                [(n, b + b"\x00") if n.lower().endswith(".g") else (n, b)
                 for n, b in files]))
        clean = {"packs": [vdpack.scan_pack(good)], "findings": []}
        assert astra_d3.scan_verdict(clean)[0] is True
        rows = [vdpack.scan_pack(good), vdpack.scan_pack(stale)]
        findings = vdpack.scan_findings(rows)
        payload = {"packs": rows,
                   "findings": [{"level": lvl, "text": txt}
                                for lvl, txt in findings]}
        ok, messages = astra_d3.scan_verdict(json.dumps(payload))
        assert ok is False
        assert any("str_not_valid_hash_for_file" in m for m in messages)
        assert astra_d3.scan_verdict("not json")[0] is False
        assert astra_d3.scan_verdict({})[0] is False


def test_studio_link_is_a_token_gated_local_executor():
    """The link is the only part of this repository that runs on the user's
    machine while a remote session drives it, so what is pinned down here is
    the contract: no token, no answer; nothing outside the allowed folders;
    long work returns a job id instead of blocking the request."""
    import importlib
    import json
    import pathlib
    import threading
    import urllib.error
    import urllib.parse
    import urllib.request
    from http.server import ThreadingHTTPServer

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    link_mod = importlib.import_module("studio_link")
    astra_d3 = link_mod.astra_d3

    with tempfile.TemporaryDirectory() as d:
        state = pathlib.Path(d, "state")
        state.mkdir()
        link_mod.LINK_DIR = state
        link_mod.TOKEN_FILE = state / "token.txt"
        link_mod.JOBS_DIR = state / "jobs"
        link_mod.JOBS_DIR.mkdir()

        studio = pathlib.Path(d, "AstraForge")
        (studio / "data" / "library" / "assets").mkdir(parents=True)
        donor = pathlib.Path(d, "inquisitor")
        donor.mkdir()
        (donor / "character_x.g").write_bytes(b"GM")
        cfg = astra_d3.Config({"studio": str(studio), "donor": str(donor),
                               "mods": str(pathlib.Path(d, "Mods")),
                               "assets": str(studio / "data" / "library" / "assets")})
        cfg["d3tool_repo"] = REPO
        cfg.resolve()
        link = link_mod.Link(cfg, 0, "http://127.0.0.1:9")   # nothing listens there
        assert len(link.token) >= 16

        link_mod.Handler.link = link
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), link_mod.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        def call(op, **params):
            query = urllib.parse.urlencode({"token": link.token, "op": op, **params})
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/cmd?{query}", timeout=30) as resp:
                    return resp.status, json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode())

        try:
            query = urllib.parse.urlencode({"token": "wrong", "op": "hello"})
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/cmd?{query}",
                                       timeout=10)
                raise AssertionError("a wrong token must not be answered")
            except urllib.error.HTTPError as exc:
                assert exc.code == 403

            status, body = call("hello")
            assert status == 200 and body["ok"]
            assert body["result"]["studio_api"]["status"] == 0     # studio is down
            assert body["result"]["port"] == 0

            status, body = call("read", path=os.path.join(REPO, "README.md"),
                                max="80")
            assert body["ok"] and "geo2011" in body["result"]["text"]

            for escape in ("/etc/passwd", str(pathlib.Path(d, "outside.txt"))):
                status, body = call("read", path=escape)
                assert status == 400 and "outside the folders" in body["error"]

            target = str(studio / "note.txt")
            status, body = call("write", path=target,
                                b64=base64.b64encode(b"hello").decode())
            assert body["ok"] and pathlib.Path(target).read_text() == "hello"

            status, body = call("d3tool", args="vdpack --help")
            assert body["ok"] and body["result"]["exit"] == 0

            status, body = call("build")            # no GLB anywhere: a job fails
            assert body["ok"] and body["result"]["job"]
            job_id = body["result"]["job"]
            for _ in range(50):
                status, body = call("job", id=job_id)
                if body["result"]["status"] != "running":
                    break
                time.sleep(0.1)
            assert body["result"]["status"] == "failed"
            assert "no GLB" in body["result"]["result"]["reason"]

            status, body = call("nonsense")
            assert status == 400 and "unknown op" in body["error"]
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_link_up_reads_the_tunnel_address_and_proves_it():
    """The launcher is the one piece that has to work on a machine we cannot
    see, so its two fallible steps — reading cloudflared's address out of its
    own log, and proving that address answers before it is handed over — are
    pinned here. The address is printed in a box, and a quick tunnel prints it
    long before it is routable, so a regex on a single line or a bare
    'the process started' is not enough."""
    import importlib
    import pathlib
    import threading
    import time
    from http.server import ThreadingHTTPServer

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    link_up = importlib.import_module("link_up")
    astra_d3 = link_up.astra_d3
    studio_link = link_up.studio_link

    with tempfile.TemporaryDirectory() as d:
        state = pathlib.Path(d, "state")
        state.mkdir()
        studio_link.LINK_DIR = state
        studio_link.TOKEN_FILE = state / "token.txt"
        studio_link.JOBS_DIR = state / "jobs"
        studio_link.JOBS_DIR.mkdir()

        # 1. an address in cloudflared's box art, and the newest one wins
        log = pathlib.Path(d, "tunnel.log")
        log.write_text(
            "INF Requesting new quick Tunnel on trycloudflare.com...\n"
            "+--------------------------------------------------------------------+\n"
            "|  https://plain-owl-marks-the-spot.trycloudflare.com                 |\n"
            "+--------------------------------------------------------------------+\n"
            "INF Registered tunnel connection connIndex=0 location=waw02\n",
            encoding="utf-8")
        assert link_up.url_from_log(log) == \
            "https://plain-owl-marks-the-spot.trycloudflare.com"
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("INF retry: https://second-name-here.trycloudflare.com\n")
        assert link_up.url_from_log(log).endswith("second-name-here.trycloudflare.com")
        assert link_up.url_from_log(pathlib.Path(d, "nothing.log")) == ""

        # the ssh fallback prints its address in prose, not in a box
        ssh_log = pathlib.Path(d, "tunnel-ssh.log")
        ssh_log.write_text("** your connection id is 8f2c, please mention it\n"
                           "abcd1234.lhr.life tunneled with tls termination, "
                           "https://abcd1234.lhr.life\n", encoding="utf-8")
        assert link_up.url_from_log(ssh_log) == "https://abcd1234.lhr.life"

        # 2. a port nothing holds, and one that is already taken is skipped
        assert link_up.free_port((3299, 3300)) in (3299, 3300)

        cfg = astra_d3.Config({"studio": str(pathlib.Path(d, "AstraForge"))})
        cfg.resolve()
        exe = pathlib.Path(d, "AstraForge", "runtime", "cloudflared", "cloudflared.exe")
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ")
        assert link_up.find_cloudflared(cfg["studio"]) == str(exe)

        # 3. probe: yes against a real link server, no against a dead port
        link = studio_link.Link(cfg, 0, "http://127.0.0.1:9")
        studio_link.Handler.link = link
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), studio_link.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            assert link_up.probe(f"http://127.0.0.1:{port}", link.token,
                                 tries=1, gap=0.1) == "ok"
            dead = link_up.probe("http://127.0.0.1:9", link.token, tries=1, gap=0.1)
            assert dead.startswith("not reachable"), dead
            wrong = link_up.probe(f"http://127.0.0.1:{port}", "wrong-token",
                                  tries=1, gap=0.1)
            assert wrong != "ok", wrong
            # a 530/1033 from Cloudflare means "connector not attached yet" and
            # is retried; anything else stops at once. Both keep the body,
            # because 530 and "bad token" mean very different things.
            detail = link_up.probe_detail(f"http://127.0.0.1:{port}", "wrong-token",
                                          tries=2, gap=0.05)
            assert detail["ok"] is False and detail["status"] == 403
            assert "bad token" in detail["body"]
            good = link_up.probe_detail(f"http://127.0.0.1:{port}", link.token,
                                        tries=1, gap=0.1)
            assert good["ok"] is True and good["status"] == 200
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_cuda_check_names_the_wheel_that_lacks_pascal():
    """The studio's "no kernel image is available" has exactly two causes —
    torch built without sm_61, or Hunyuan3D's precompiled rasterizer — and the
    difference is where it dies. The report has to say which, from the probe,
    because the machine it runs on cannot be reached from here."""
    import importlib

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    cuda = importlib.import_module("cuda_check")

    pascal_broken = {
        "python": "3.11.9", "torch": "2.8.0+cu128", "cuda_build": "12.8",
        "available": True, "device": "NVIDIA GeForce GTX 1080 Ti",
        "capability": "6.1",
        "arch": ["sm_70", "sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"],
        "smoke": "RuntimeError: CUDA error: no kernel image is available for "
                 "execution on the device",
        "mod_custom_rasterizer": "ModuleNotFoundError",
    }
    level, text = cuda.verdict(pascal_broken)
    assert level == "bad"
    assert "sm_61" in text and "cu126" in text
    assert any("[БЕДА]" in line for line in cuda.report_lines("x", pascal_broken))

    working = dict(pascal_broken, torch="2.8.0+cu126", cuda_build="12.6",
                   arch=["sm_50", "sm_60", "sm_70", "sm_75", "sm_80", "sm_86",
                         "sm_90"], smoke="ok")
    assert cuda.verdict(working)[0] == "ok"
    # a 6.1 card is fine with sm_60 kernels: same major, lower minor
    assert cuda.compatible_arch(["sm_50", "sm_60"], "6.1")
    assert not cuda.compatible_arch(["sm_75", "sm_90"], "6.1")
    assert not cuda.compatible_arch(["sm_120"], "6.1")
    assert not cuda.compatible_arch(["bogus"], "6.1")

    # the rasterizer case: torch is fine, the extension is what breaks
    texture_only = dict(working, mod_custom_rasterizer="ok",
                        mod_differentiable_renderer="ImportError")
    assert cuda.verdict(texture_only)[0] == "ok"
    assert "differentiable_renderer=ImportError" in "\n".join(
        cuda.report_lines("x", texture_only))

    assert cuda.verdict({"error": "No module named 'torch'"})[0] == "info"
    assert cuda.verdict({"error": "boom"})[0] == "info"
    assert cuda.verdict(dict(working, available=False))[0] == "warn"
    # sm_61 missing but the kernel ran anyway (PTX/JIT): a warning, not a failure
    jit = dict(working, arch=["sm_75", "sm_90"], smoke="ok")
    assert cuda.verdict(jit)[0] == "warn"

    # discovery: every venv the studio may run, and the rasterizer's sources
    with tempfile.TemporaryDirectory() as d:
        studio = os.path.join(d, "AstraForge")
        made = [
            os.path.join(studio, "runtime", "comfy", ".venv", "Scripts", "python.exe"),
            os.path.join(studio, "runtime", "python", "python.exe"),
            os.path.join(studio, "runtime", "hunyuan", "custom_rasterizer", "setup.py"),
        ]
        for path in made:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").close()
        hits = cuda.find_pythons(studio)
        assert len(hits) == 2, hits
        assert all(hit.endswith("python.exe") for hit in hits)
        sources = cuda.find_rasterizer_sources(studio)
        assert len(sources) == 1 and sources[0].endswith("setup.py")
        assert cuda.find_rasterizer_sources(os.path.join(d, "nope")) == []
        # a probe of a missing interpreter is reported, not raised
        assert cuda.probe(os.path.join(d, "nope.exe")).get("error")


def test_bridge_finds_the_studio_wherever_it_lives():
    """The studio on this user's machine is C:\\AstraForge, while the bridge was
    unzipped into D:\\AstraForge\\bridge — a folder with the right name and none
    of the studio inside. Every derived path (assets, Blender, the ComfyUI
    interpreter) hangs off that one answer, so it cannot be the name."""
    import importlib

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    astra_d3 = importlib.import_module("astra_d3")

    with tempfile.TemporaryDirectory() as d:
        decoy = pathlib.Path(d, "D", "AstraForge", "bridge")
        decoy.mkdir(parents=True)
        real = pathlib.Path(d, "C", "AstraForge", "data", "models")
        real.mkdir(parents=True)

        assert astra_d3.guess_studio((str(decoy.parent), str(real.parents[1]))) == \
            str(pathlib.Path(d, "C", "AstraForge"))
        assert astra_d3.guess_studio((str(decoy.parent),)) == ""
        assert astra_d3.looks_like_studio(real.parents[1]) is True
        assert astra_d3.looks_like_studio(decoy.parent) is False

        # configured at the decoy, but the guess wins because the decoy is not a studio
        cfg = astra_d3.Config({"studio": str(decoy.parent)})
        astra_d3.STUDIO_CANDIDATES = (str(real.parents[1]),)
        try:
            cfg.resolve()
            assert cfg["studio"] == str(pathlib.Path(d, "C", "AstraForge"))
            assert cfg["assets"].replace("\\", "/").endswith(
                "AstraForge/data/library/assets")
        finally:
            astra_d3.STUDIO_CANDIDATES = astra_d3.STUDIO_CANDIDATES

        # nothing found anywhere: the configured value is kept rather than lost
        cfg2 = astra_d3.Config({"studio": str(decoy.parent)})
        astra_d3.STUDIO_CANDIDATES = (str(pathlib.Path(d, "nope")),)
        try:
            cfg2.resolve()
            assert cfg2["studio"] == str(decoy.parent)
        finally:
            astra_d3.STUDIO_CANDIDATES = astra_d3.STUDIO_CANDIDATES


if __name__ == "__main__":
    # Keep this at the very end: `main()` collects every module-level
    # `test_*` function, so tests defined below an earlier guard would never
    # run.
    sys.exit(main())


def test_cuda_fix_uses_the_venv_the_studio_actually_runs():
    """A "no kernel image" fix that installs into the wrong interpreter, or
    through a pip that is not there, changes nothing and looks successful.

    Facts this encodes, all from the studio's own sources:
    * the studio runs Hunyuan3D in ``<app>/runtime/ComfyUI/.venv``
      (server/storage.mjs: ``comfyDir``, server/runtime.mjs spawns it);
    * that venv is made by ``uv venv`` and has **no pip**, so the fix has to go
      through the studio's ``runtime\\uv\\uv.exe``;
    * ``installer/Install.ps1`` gives every machine with a GPU the cu128 index,
      which is why a 1080 Ti has no Pascal in torch at all.
    """
    import importlib

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    cuda = importlib.import_module("cuda_check")

    with tempfile.TemporaryDirectory() as d:
        studio = os.path.join(d, "AstraForge")
        venv = os.path.join(studio, "runtime", "ComfyUI", ".venv", "Scripts", "python.exe")
        os.makedirs(os.path.dirname(venv), exist_ok=True)
        open(venv, "wb").close()
        uv = os.path.join(studio, "runtime", "uv", "uv.exe")
        os.makedirs(os.path.dirname(uv), exist_ok=True)
        open(uv, "wb").close()
        # a second ComfyUI somewhere else, pointed at from data/config.json
        other = os.path.join(d, "ComfyUI_elsewhere")
        other_py = os.path.join(other, ".venv", "Scripts", "python.exe")
        os.makedirs(os.path.dirname(other_py), exist_ok=True)
        open(other_py, "wb").close()

        assert cuda.studio_comfy_dir(studio) == os.path.join(studio, "runtime", "ComfyUI")
        found = cuda.find_pythons(studio)
        assert found and found[0] == venv, found          # the studio's own venv first
        assert cuda.find_uv(studio) == uv

        os.makedirs(os.path.join(studio, "data"), exist_ok=True)
        with open(os.path.join(studio, "data", "config.json"), "w", encoding="utf-8") as f:
            json.dump({"comfyDir": other}, f)
        assert cuda.studio_comfy_dir(studio) == other     # comfyDir wins, as in storage.mjs
        assert cuda.find_pythons(studio)[0] == other_py

    # uv, not pip: the venv has no pip at all
    command = cuda.install_command("PY", "UV", ["torch"])
    assert command[:4] == ["UV", "pip", "install", "--python"]
    assert "--index-url" in command and cuda.TORCH_INDEX_PASCAL in command
    pip_only = cuda.install_command("PY", "", ["torch"])
    assert pip_only[:3] == ["PY", "-m", "pip"]

    # the fallback order matters: newest cu126 first, then the known-good set
    labels = [label for label, _ in cuda.TORCH_ATTEMPTS]
    assert "torch==2.8.0" in cuda.TORCH_ATTEMPTS[2][1]
    assert len(labels) >= 2

    # the studio's ports, and a fix that refuses to run while they answer
    assert cuda.ENGINE_PORTS == (3210, 8190, 8191, 8192)
    assert cuda.port_open(9, timeout=0.05) is False
    real_port_open = cuda.port_open
    try:
        cuda.port_open = lambda port, **kw: port == 8190
        assert cuda.stop_studio(wait=1) is False          # 8190 still listening
        cuda.port_open = lambda port, **kw: False
        assert cuda.stop_studio() is True                 # nothing runs, nothing to stop
    finally:
        cuda.port_open = real_port_open


def test_cuda_fix_is_a_double_click_that_cannot_land_in_the_wrong_place():
    """The launchers are ASCII + CRLF (cp866 console), never pass a guessed
    --studio, and hand the fix its --yes so a double-click runs to the end."""
    for name, args in (("CUDA-check.cmd", " check "),
                       ("CUDA-fix-torch.cmd", " fix-torch --yes ")):
        raw = open(os.path.join(REPO, "bridge", name), "rb").read()
        raw.decode("ascii")                                  # raises if not ASCII
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
        text = raw.decode("ascii")
        assert "--studio" not in text                        # the shape decides, not a path
        assert args in text, name
        assert "AstraForge" in text and "pause" in text
        assert "cuda_check.py" in text


def test_cuda_fix_tries_cu126_then_the_pinned_set_and_stops_when_a_kernel_runs():
    """Installing the newest cu126 build is a guess until a kernel actually runs
    on the card. The fix must try, verify, and only then report success — and it
    must not leave a half-installed venv behind while still saying "ok"."""
    import argparse
    import importlib

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    cuda = importlib.import_module("cuda_check")

    broken = {"python": "3.12.11", "torch": "2.11.0+cu128", "cuda_build": "12.8",
              "available": True, "device": "NVIDIA GeForce GTX 1080 Ti",
              "capability": "6.1", "arch": ["sm_75", "sm_90", "sm_120"],
              "smoke": "RuntimeError: CUDA error: no kernel image is available "
                       "for execution on the device"}
    working = dict(broken, torch="2.8.0+cu126", cuda_build="12.6",
                   arch=["sm_50", "sm_60", "sm_70", "sm_75", "sm_80", "sm_90"],
                   smoke="ok")

    with tempfile.TemporaryDirectory() as d:
        studio = os.path.join(d, "AstraForge")
        venv = os.path.join(studio, "runtime", "ComfyUI", ".venv", "Scripts", "python.exe")
        os.makedirs(os.path.dirname(venv), exist_ok=True)
        open(venv, "wb").close()
        uv = os.path.join(studio, "runtime", "uv", "uv.exe")
        os.makedirs(os.path.dirname(uv), exist_ok=True)
        open(uv, "wb").close()
        report = os.path.join(d, "cuda-fix-report.txt")

        calls = []

        # "no kernel image" survives every attempt until the pinned set lands
        def fake_probe(python, timeout=180):
            return working if any("torch==2.8.0" in c for c in calls) else broken

        real = cuda.probe, cuda.subprocess.call, cuda.stop_studio
        try:
            cuda.probe = fake_probe
            cuda.subprocess.call = lambda cmd, **kw: (calls.append(cmd), 0)[1]
            cuda.stop_studio = lambda wait=45: True
            args = argparse.Namespace(studio=studio, python="", yes=True, all=False,
                                      report=report, keep_engine=False, arch="6.1")
            assert cuda.cmd_fix_torch(args) == 0
        finally:
            cuda.probe, cuda.subprocess.call, cuda.stop_studio = real

        assert len(calls) >= 2, calls
        assert calls[0][:5] == [uv, "pip", "install", "--python", venv]
        assert "torch" in calls[0] and "torch==2.8.0" not in calls[0]
        assert "torch==2.8.0" in calls[-1]          # fallen back to the known-good set
        text = open(report, encoding="utf-8").read()
        assert "fixed: " + venv in text and "failed: none" in text


def test_link_survives_a_leftover_instance_on_the_same_port():
    """`local: not reachable (HTTP 403)` is what a second copy of the bridge sees.

    On Windows `HTTPServer` binds with `SO_REUSEADDR`, which *succeeds* on a port
    another process is already listening on (the older listener keeps the
    traffic). So a fresh run talks to the stale server of another unzipped copy,
    whose `.link/token.txt` holds a different token: 403, verdict `local-fail`,
    and nothing ever gets a tunnel. The fix has to detect the owner and move on.
    """
    import importlib

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    import threading

    link_up = importlib.import_module("link_up")
    studio_link = importlib.import_module("studio_link")

    stale = studio_link.Link({"studio": "."}, 0, "http://127.0.0.1:3210")
    stale.token = "A-TOKEN-FROM-AN-OLDER-UNZIPPED-COPY"

    class StaleHandler(studio_link.Handler):   # its own token, like the other copy
        link = stale

    old = studio_link.StudioLinkServer(("127.0.0.1", 0), StaleHandler)
    port = old.server_address[1]
    threading.Thread(target=old.serve_forever, daemon=True).start()
    try:
        assert link_up.port_busy(port) is True
        # a connect test sees it; a single busy candidate is refused, not hijacked
        try:
            link_up.free_port((port,))
        except link_up.Abort as exc:
            assert "занят" in str(exc)
        else:                                     # pragma: no cover - must not pass
            raise AssertionError("free_port handed out a busy port")

        mine = studio_link.Link({"studio": "."}, 0, "http://127.0.0.1:3210")
        studio_link.Handler.link = mine
        httpd, chosen, tried = link_up.bind_link_server((port, port + 1, port + 2),
                                                       mine.token)
        try:
            assert chosen != port, tried
            assert any(str(port) in line and "занят" in line for line in tried), tried

            # and the wrong-token answer is now recognisably "someone else's server"
            outcome = link_up.probe_detail(f"http://127.0.0.1:{port}", mine.token,
                                           tries=1, gap=0.05)
            assert outcome["ok"] is False and outcome["status"] == 403
            assert outcome["ours"] is False
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        old.shutdown()
        old.server_close()


def test_link_probe_uses_ping_so_a_thin_config_cannot_throw_the_tunnel_away():
    """The probe must not depend on the studio, the config or the asset folders.

    `hello` reads the config; when a key is missing it answers 400, and a probe
    that treated 400 as "not our server" discarded a perfectly working tunnel.
    `ping` answers without touching anything, and `ours` still distinguishes our
    server from Cloudflare and from another copy.
    """
    import importlib

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    import threading

    link_up = importlib.import_module("link_up")
    studio_link = importlib.import_module("studio_link")

    thin = studio_link.Link({"studio": "."}, 0, "http://127.0.0.1:9")
    studio_link.Handler.link = thin
    httpd = studio_link.StudioLinkServer(("127.0.0.1", 0), studio_link.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        ping = link_up.probe_detail(base, thin.token, tries=1, gap=0.05)
        assert ping["ok"] is True and ping["op"] == "ping" and ping["ours"] is True
        assert link_up.probe(base, thin.token, tries=1, gap=0.05) == "ok"

        # an op that fails on the server side: our server (token matched), and
        # that must not be mistaken for a dead tunnel
        broken = link_up.probe_detail(base, thin.token, tries=1, gap=0.05, op="read")
        assert broken["ours"] is True
        assert broken["ok"] is False and "наш сервер" in broken["detail"]
        # `hello` itself survives a config without keys — a thin config is not 400
        hello = link_up.probe_detail(base, thin.token, tries=1, gap=0.05, op="hello")
        assert hello["ok"] is True and hello["ours"] is True

        wrong = link_up.probe_detail(base, "nope", tries=1, gap=0.05)
        assert wrong["ours"] is False and wrong["status"] == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_link_says_which_build_it_is_and_where_it_runs():
    """"I did not see where the fix is" is almost always an old copy on disk.

    Two unzipped bridges, one double-clicked, and the build number is the only
    way to tell them apart — so it is printed in the banner, stored in the
    report, and answered by `op=ping` / `op=hello` before anything else.
    """
    import importlib

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    studio_link = importlib.import_module("studio_link")
    link_up = importlib.import_module("link_up")

    marker = pathlib.Path(REPO, "bridge", "BUILD.txt").read_text(encoding="utf-8").strip()
    assert marker.isdigit(), marker
    assert studio_link.build_marker() == marker

    ping = studio_link.op_ping(studio_link.Link({"studio": "."}, 0, "http://127.0.0.1:9"), {})
    assert ping["build"] == marker
    assert ping["bridge"].replace("\\", "/").endswith("bridge")

    # the banner in run() prints the marker, and a bridge living inside the
    # studio folder is called out instead of being used silently
    source = pathlib.Path(REPO, "bridge", "link_up.py").read_text(encoding="utf-8")
    assert "сборк" in source and "build_marker()" in source
    assert "ЭТА КОПИЯ ЛЕЖИТ ВНУТРИ ПАПКИ СТУДИИ" in source
    assert "BUILD.txt" in pathlib.Path(REPO, "bridge", "studio_link.py").read_text(
        encoding="utf-8")


def test_link_has_more_than_one_way_out():
    """trycloudflare answers 530 often enough that one transport is not a plan.

    ssh ships with Windows but is not always on PATH; pinggy answers on 443,
    which survives networks that drop ssh's port 22; localhost.run is the plain
    fallback. cloudflared is tried over QUIC and then HTTP/2.
    """
    import importlib

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    link_up = importlib.import_module("link_up")

    names = [name for name, _ in link_up.transports("cloudflared.exe", 3211)]
    assert "cloudflared QUIC" in names and "cloudflared HTTP/2" in names
    assert any("pinggy" in name for name in names), names
    assert any("localhost.run" in name for name in names), names

    # Windows' own ssh, even when PATH has none
    for candidate in link_up.SSH_CANDIDATES:
        assert candidate.lower().endswith("openssh\\ssh.exe")

    # pinggy is asked for an assigned port (0) over 443; localhost.run for 80
    seen = []

    def fake_spawn(cmd, log_path):
        seen.append((cmd, log_path.name))
        raise OSError("stop before launching")

    real = link_up._spawn
    try:
        link_up._spawn = fake_spawn
        for name, starter in link_up.transports("", 3211):
            try:
                starter()
            except OSError:
                pass
    finally:
        link_up._spawn = real
    logs = dict((name, cmd) for cmd, name in seen)
    assert "tunnel-pinggy.log" in logs
    assert "0:127.0.0.1:3211" in logs["tunnel-pinggy.log"]
    assert "443" in logs["tunnel-pinggy.log"]
    assert "80:127.0.0.1:3211" in logs["tunnel-ssh.log"]


def test_a_folder_with_one_stray_data_is_not_the_studio():
    """An older build of this bridge ran with a wrong studio path and created
    `D:\\AstraForge\\data` by itself — after that the decoy looked real, and every
    derived path (assets, Blender, the ComfyUI interpreter) pointed at the wrong
    disk. One empty marker folder must not be enough."""
    import importlib

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    astra_d3 = importlib.import_module("astra_d3")

    with tempfile.TemporaryDirectory() as d:
        decoy = pathlib.Path(d, "AstraForge")
        (decoy / "data").mkdir(parents=True)          # what an old run leaves behind
        (decoy / "bridge").mkdir()
        assert astra_d3.looks_like_studio(decoy) is False

        real = pathlib.Path(d, "C", "AstraForge")
        (real / "runtime" / "ComfyUI").mkdir(parents=True)
        astra_d3.STUDIO_CANDIDATES = (str(decoy), str(real))
        try:
            # the decoy is skipped even though it owns a `data` folder
            assert astra_d3.guess_studio() == str(real)
            # and a second empty marker does not change that order either
            (decoy / "runtime").mkdir()
            assert astra_d3.looks_like_studio(decoy) is True
        finally:
            astra_d3.STUDIO_CANDIDATES = astra_d3.STUDIO_CANDIDATES


def test_tunnel_address_never_comes_from_a_previous_run():
    """`url_from_log` reads the newest URL in the log — which is the *old* one
    until the new cloudflared prints its own.

    A log left by a previous attempt therefore made the probe knock on a dead
    hostname: Cloudflare answers 1033 ("tunnel not found") or 530, every
    transport looks broken, and the real tunnel was fine all along. The log is
    truncated before the client starts.
    """
    import importlib
    import io

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    link_up = importlib.import_module("link_up")

    seen = {}

    class FakeProc:
        stdout = io.BytesIO(b"")

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        log = link_up.LINK_DIR / "tunnel-quic.log"
        seen["empty_before_spawn"] = log.read_text(encoding="utf-8") == ""
        seen["cmd"] = cmd
        return FakeProc()

    def fake_wait(proc, log_path, seconds=45):
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("INF |  https://fresh-name-here.trycloudflare.com  |\n")
        return link_up.url_from_log(log_path)

    link_up.LINK_DIR.mkdir(parents=True, exist_ok=True)
    log = link_up.LINK_DIR / "tunnel-quic.log"
    log.write_text("INF |  https://stale-from-yesterday.trycloudflare.com  |\n",
                   encoding="utf-8")

    real_popen, real_wait = link_up.subprocess.Popen, link_up._wait_for_url
    try:
        link_up.subprocess.Popen = fake_popen
        link_up._wait_for_url = fake_wait
        proc, url, log_path = link_up.start_cloudflared("cloudflared.exe", 3211, "quic")
    finally:
        link_up.subprocess.Popen, link_up._wait_for_url = real_popen, real_wait

    assert seen["empty_before_spawn"] is True
    assert url.endswith("fresh-name-here.trycloudflare.com"), url
    assert "stale-from-yesterday" not in url
    assert "--protocol" in seen["cmd"] and "quic" in seen["cmd"]
    log.write_text("", encoding="utf-8")


def test_link_preview_shows_a_page_but_never_the_token():
    """A browser opening the port must see something readable, and the JSON API
    must stay token-only.

    The page exists for a live preview of this port (a plain 403 JSON in an
    iframe says nothing). It must not carry the token or anything about the PC:
    anyone with the preview link would otherwise be able to drive the studio.
    """
    import importlib
    import threading
    import urllib.error
    import urllib.request

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    studio_link = importlib.import_module("studio_link")

    link = studio_link.Link({"studio": "."},
                            int(os.environ.get("D3_PREVIEW_PORT", "3298")),
                            "http://127.0.0.1:3210")
    studio_link.Handler.link = link
    httpd = studio_link.StudioLinkServer(("127.0.0.1", 0), studio_link.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            page = resp.read().decode("utf-8")
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/html")
        assert "studio link" in page
        assert studio_link.build_marker() in page
        assert link.token not in page                     # never the token
        assert str(link.port) in page                     # but its own port is fine

        # the API itself keeps refusing: with and without a query, `/cmd` needs a token
        for path in ("/cmd", "/cmd?op=ping", "/ping"):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)
            except urllib.error.HTTPError as exc:
                assert exc.code == 403, path
            else:                                          # pragma: no cover
                raise AssertionError(f"{path} answered without a token")

        # with the token, "/" is the JSON API again
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/?token={link.token}&op=ping", timeout=5) as resp:
            answer = json.loads(resp.read())
        assert answer["ok"] is True and answer["result"]["build"] == studio_link.build_marker()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_link_moves_big_files_in_chunks_and_reads_logs():
    """"Full control" fails at the first real artefact: `read` caps at 400 KB and
    `write` at 256 KB, while a unit GLB is tens of megabytes. So the link needs a
    chunked transfer — and it must fight corruption, not hide it: the first chunk
    truncates, every later one has to continue exactly at the current size.
    """
    import hashlib
    import importlib
    import random as rnd
    import threading
    import urllib.parse
    import urllib.request

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    studio_link = importlib.import_module("studio_link")

    with tempfile.TemporaryDirectory() as d:
        link = studio_link.Link({"studio": d}, 3297, "http://127.0.0.1:3210")
        studio_link.Handler.link = link
        httpd = studio_link.StudioLinkServer(("127.0.0.1", 0), studio_link.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        def call(**params):
            query = urllib.parse.urlencode({"token": link.token, **params})
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/cmd?{query}",
                                        timeout=20) as resp:
                answer = json.loads(resp.read())
            assert answer["ok"] is True, answer
            return answer["result"]

        # bigger than one answer and than one chunk, like a real artefact
        blob = bytes(rnd.randrange(256) for _ in range(64 * 1024))

        # the chunk size is set by the request line, not by taste: a GET query
        # string has to stay under Cloudflare's ~16 KB, so base64(+params) does
        assert studio_link.MAX_CHUNK * 4 // 3 + 2048 <= 16_384, studio_link.MAX_CHUNK
        assert studio_link.CHUNK <= studio_link.MAX_CHUNK
        target = os.path.join(d, "unit.glb")
        offset = 0
        while offset < len(blob):
            piece = blob[offset:offset + studio_link.CHUNK]
            result = call(op="put", path=target, offset=offset, total=len(blob),
                          b64=__import__("base64").b64encode(piece).decode())
            assert result["size"] == offset + len(piece)
            offset = result["next"]
        assert result["eof"] is True
        with open(target, "rb") as fh:
            assert fh.read() == blob

        # reading it back, chunk by chunk, gives the same bytes
        back, offset = b"", 0
        while True:
            chunk = call(op="get", path=target, offset=offset, sum="1")
            back += __import__("base64").b64decode(chunk["b64"])
            assert chunk["sha256_chunk"] == hashlib.sha256(
                __import__("base64").b64decode(chunk["b64"])).hexdigest()
            if chunk["eof"]:
                break
            offset = chunk["next"]
        assert back == blob

        # a lost or repeated chunk is an error, not a silently corrupt file
        try:
            call(op="put", path=target, offset=10, b64="AAAA")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert "does not match the file size" in json.loads(exc.read())["error"]
        else:                                              # pragma: no cover
            raise AssertionError("a chunk at the wrong offset was accepted")

        # logs: the tail of a file, without pulling megabytes through JSON
        log = os.path.join(d, "comfy.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("\n".join(f"line {i}" for i in range(5000)))
        tail = call(op="tail", path=log, lines=3)
        assert tail["lines"] == ["line 4997", "line 4998", "line 4999"]
        assert "line 4999" in tail["text"] and tail["size"] > 0

        # and the CUDA diagnosis can be run through the link, not by hand
        assert "cuda" in studio_link.OPS
        httpd.shutdown()
        httpd.server_close()


def test_link_leaves_one_file_to_drag_into_the_chat():
    """The hand-off, not the tunnel, is what keeps failing: the address lives in a
    window and the token in another place, and a chat needs both. So the announce
    step writes `SEND-ME.txt` — one line, ready to paste, plus the build and the
    folder — and a user can simply drag that file in."""
    import importlib

    sys.path.insert(0, os.path.join(REPO, "bridge"))
    link_up = importlib.import_module("link_up")
    studio_link = importlib.import_module("studio_link")

    link_up.LINK_DIR.mkdir(parents=True, exist_ok=True)
    verdicts = []

    def fake_finish(code, verdict):
        verdicts.append((code, verdict))
        return code

    import builtins

    real_input, real_print = builtins.input, builtins.print
    real_copy = link_up.copy_to_clipboard
    try:
        builtins.input = lambda *a, **kw: (_ for _ in ()).throw(EOFError())
        builtins.print = lambda *a, **kw: None
        link_up.copy_to_clipboard = lambda text: True
        link_up._announce("https://abc-def.trycloudflare.com", "SECRET-TOKEN-0123456789",
                          "cloudflared QUIC", [], fake_finish, None)
    finally:
        builtins.input, builtins.print = real_input, real_print
        link_up.copy_to_clipboard = real_copy

    text = (link_up.LINK_DIR / "SEND-ME.txt").read_text(encoding="utf-8")
    first = text.splitlines()[0]
    assert first == "link https://abc-def.trycloudflare.com SECRET-TOKEN-0123456789"
    assert "build: " + studio_link.build_marker() in text
    assert "verdict: ok" in text
    assert verdicts == [(0, "ok")]
    (link_up.LINK_DIR / "SEND-ME.txt").unlink()
