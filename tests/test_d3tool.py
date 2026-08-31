"""Self-contained tests for the d3tool package (run with `python3 tests/`)."""
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
        r = subprocess.run([sys.executable, "-m", "d3tool", "import", p],
                           capture_output=True, text=True, cwd=REPO,
                           check=False)
        assert r.returncode == 1, r.stdout
        assert "unparsed" in r.stdout + r.stderr
        good = os.path.join(REPO, "Neutrals", "Wildboar",
                            "character_neutrals_wildboar.g")
        r2 = subprocess.run([sys.executable, "-m", "d3tool", "import", good],
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
        climod._export(os.path.join(folder, stem + ".gltf"), d, 0,
                       anim=True, quiet=True)
        got = open(os.path.join(d, stem + "_idle.a"), "rb").read()
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
