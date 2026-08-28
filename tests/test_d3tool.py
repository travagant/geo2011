"""Self-contained tests for the d3tool package (run with `python3 tests/`)."""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import d3tool
from d3tool import anim as animmod
from d3tool import gfile, gltf, ac as acmod, gltf_out

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


def test_ac_roundtrip():
    cfg = acmod.default_ac("mesh.g", "unit")
    text = acmod.write_ac(cfg)
    cfg2 = acmod.parse_ac(text)
    assert [s.name for s in cfg2.states] == ["Idle", "Attack", "Damage",
                                             "Death", "Run"]
    assert cfg2.states[0].frame1 == 150


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


def test_analyze_gltf():
    base = os.path.join(REPO, "Neutrals", "AirElemental",
                        "character_neutrals_airelemental")
    m = gltf.load_gltf(base + ".gltf")
    assert m.vertex_count == 3453
    assert m.tri_count == 5056
    assert len(m.bones) == 38
    assert len(m.frames) > 0


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
