"""Self-contained tests for the d3tool package (run with `python3 tests/`)."""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import d3tool
from d3tool import anim as animmod
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
    ab = [x[0] for x in zip(back.bones, orig.bones)]
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
    assert cfg2.states[0].frame1 == 150


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
    import d3tool
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
            capture_output=True, text=True, cwd=REPO)
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
             "-o", fwd], capture_output=True, text=True, cwd=REPO)
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


def test_stub_g_gives_raw_passthrough_and_clean_export_error():
    """The two bundled loader stubs (602-byte placeholder .g files) fall back
    to raw passthrough: .g serialization stays lossless, and glTF export
    must refuse with a readable error, not crash inside `min()`."""
    import tempfile
    p = os.path.join(REPO, "Empire", "Leader-Ranger",
                     "character_empire_leader-ranger.g")
    data = open(p, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    assert mesh.raw, "leader stub must fall back to raw passthrough"
    # .g serialization stays lossless for the stub
    assert gfile.write_geometry_file(mesh, {}) == data
    # glTF export refuses with a readable error
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "u.gltf")
        try:
            gltf_out.write_gltf_to(out, mesh, None)
        except ValueError as exc:
            assert "empty mesh" in str(exc)
        else:
            raise AssertionError("expected ValueError for a stub mesh")


def test_parse_attributes_corrupt_bytes_raise_valueerror():
    """`parse_attributes` on garbage must raise a readable ValueError, not a
    struct.error with nonsense offsets."""
    data = open(os.path.join(REPO, "Empire", "Leader-Ranger",
                             "character_empire_leader-ranger.g"),
                "rb").read()
    try:
        gfile.parse_attributes(data)
    except ValueError as exc:
        assert "attribute block" in str(exc)
    else:
        raise AssertionError("expected ValueError for a corrupt .g stub")


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


if __name__ == "__main__":
    sys.exit(main())
