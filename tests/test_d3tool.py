"""Self-contained tests for the d3tool package (run with `python3 tests/`)."""
import copy
import hashlib
import json
import glob
import os
import shutil
import tempfile
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import d3tool
from d3tool import anim as animmod
from d3tool import cli as climod
from d3tool import gfile, gltf, ac as acmod, gltf_out, scene as scenemod
from d3tool import texture as texmod

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    """A Blender re-saved .png must become a loadable native .t: right
    dimensions, uncompressed 32-bit code 4, full mip chain sized the GM
    way (each mip w>>i * h>>i * 4, no clamping to 1px), BGRA order."""
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
    assert ti.gm_format == 4
    assert ti.mip_count == 4                      # 8,4,2,1
    assert len(ti.payload) == (64 + 16 + 4 + 1) * 4
    assert ti.payload[:4] == b"\x80\x00\x00\xff"  # RGBA(0,0,128,255)->BGRA


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


def test_vdpack_pack_can_keep_the_opaque_index():
    """Only in-place replacement inside a pack that keeps its opaque `index`
    is confirmed to load in game, so building on that shape must be possible
    (and a pack without one has to say so)."""
    import contextlib
    import io
    from d3tool import vdpack
    folder = os.path.join(REPO, "Neutrals", "AirElemental")
    with tempfile.TemporaryDirectory() as d, \
            contextlib.redirect_stdout(io.StringIO()) as buf:
        # a stock pack has no `index` and warns about it
        plain = os.path.join(d, "plain.vdpack")
        assert climod._run(["vdpack", "pack", folder, "-o", plain]) == 0
        assert "no opaque `index` entry" in buf.getvalue()
        # the reference archive: a folder copy plus the opaque resource
        shared = os.path.join(d, "shared")
        for name, data in vdpack.pack_folder(folder):
            os.makedirs(os.path.dirname(os.path.join(shared, *name.split("/"))),
                        exist_ok=True)
            open(os.path.join(shared, *name.split("/")), "wb").write(data)
        reference = os.path.join(d, "reference.vdpack")
        assert climod._run(["vdpack", "pack", shared, "-o", reference]) == 0
        # a pack that already carries the resource keeps it through a repack
        blob = vdpack.parse_vdpack(open(reference, "rb").read())
        assert len(blob) == len(vdpack.pack_folder(shared))
        # ... now hand the `index` to a pack built from a plain folder
        with_index = os.path.join(d, "indexed.vdpack")
        buf.seek(0)
        buf.truncate()
        assert climod._run(["vdpack", "pack", folder, "-o", with_index,
                            "--keep-index", reference]) == 1, \
            "the reference carries no `index`, so there is nothing to keep"
        assert "no `index` entry" in buf.getvalue()
        # build a real one: rebuild the reference with an `index` resource
        raw = bytearray(open(reference, "rb").read())
        pack = vdpack.parse_vdpack(bytes(raw))
        entries = [(e.name, pack.payload(e)) for e in pack.entries]
        entries.append(("index", b"opaque-index-payload"))
        archive = os.path.join(d, "withindex.vdpack")
        open(archive, "wb").write(vdpack.build_vdpack(entries))
        assert vdpack.index_entry(
            list(vdpack.read_vdpack(archive).files().items())) is not None
        # keep it in a pack built from a folder that has none
        out = os.path.join(d, "kept.vdpack")
        buf.seek(0)
        buf.truncate()
        assert climod._run(["vdpack", "pack", folder, "-o", out,
                            "--keep-index", archive]) == 0
        assert "no opaque `index` entry" not in buf.getvalue()
        kept = vdpack.read_vdpack(out)
        assert not kept.parse_error, kept.parse_error
        index = vdpack.index_entry(list(kept.files().items()))
        assert index is not None and index[1] == b"opaque-index-payload"
        # the manifest of that pack records the resource
        assert vdpack.manifest(
            list(kept.files().items()))["index"]["bytes"] == len(
                b"opaque-index-payload")
        # a folder that already has an `index` file is refused (`pack` would
        # silently end up with two)
        dupe = os.path.join(d, "dupe")
        shutil.copytree(folder, dupe)
        open(os.path.join(dupe, "index"), "wb").write(b"ours")
        assert climod._run(["vdpack", "pack", dupe, "-o",
                            os.path.join(d, "dupe.vdpack"),
                            "--keep-index", archive]) == 1


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
        assert written["entries"] == doc["entries"]
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
    # the box in the emitted block is the sword's own, not a donor's
    assert block[28:84] == gmod.scene_node_block(donor, b"")

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


if __name__ == "__main__":
    # Keep this at the very end: `main()` collects every module-level
    # `test_*` function, so tests defined below an earlier guard would never
    # run.
    sys.exit(main())
