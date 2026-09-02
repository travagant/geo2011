#!/usr/bin/env python3
"""Corpus-wide import / export test over **every** bundled unit.

Where ``test_d3tool.py`` checks a handful of hand-picked units, this walks the
whole ``Empire/`` + ``Neutrals/`` tree and exercises both directions on every
asset that ships with the repository:

* **import** — every ``.g`` / ``.a`` / ``.ac`` / ``.gltf`` / ``.t`` / ``.dds``
  must parse;
* **forward export** — every ``.g`` (+ its ``.a``) must produce a glTF whose
  buffer sizes are self-consistent and which our own reader can load back;
* **reverse export** — every ``.gltf`` must produce ``.g`` / ``.scene`` /
  ``.ac`` (and ``.a`` when the source has an animation), all re-parseable;
* **round-trip** — ``.g`` and ``.a`` must re-serialise byte-for-byte, and
  ``.t`` -> ``.dds`` -> ``.t`` must be lossless.

The handful of assets that legitimately cannot be converted are listed in
``KNOWN`` with the reason, so a *new* refusal fails the run instead of being
swallowed.

Run it directly (``python3 tests/test_corpus.py``) or under pytest.
"""
from __future__ import annotations

import contextlib
import glob
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from d3tool import ac as acmod            # noqa: E402
from d3tool import anim as animmod        # noqa: E402
from d3tool import cli                    # noqa: E402
from d3tool import gfile                  # noqa: E402
from d3tool import gltf as gltfmod        # noqa: E402
from d3tool import texture as texmod      # noqa: E402

import _refgl                             # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUPS = ("Empire", "Neutrals")


def _assets(ext: str):
    """Every bundled file with ``ext``, sorted, as repo-relative paths."""
    out = []
    for group in GROUPS:
        out.extend(sorted(glob.glob(os.path.join(REPO, group, "**", "*" + ext),
                                    recursive=True)))
    return out


# --------------------------------------------------------------------------- #
#  Known, documented exceptions — a refusal outside this list is a failure.
# --------------------------------------------------------------------------- #
KNOWN = {
    # 602-byte node helpers: `materials_num 0`, four vertices, no index block.
    # A glTF for one would need a skin with zero joints, which is not valid.
    "no_gltf": {
        os.path.join("Empire", "Leader-Ranger",
                     "character_empire_leader-ranger.g"),
        os.path.join("Empire", "Leader-Thief",
                     "character_empire_leader-thief.g"),
    },
    # dis3tool exported these three as rigid meshes: no `skins`, no
    # `animations`, no WEIGHTS_0/JOINTS_0 — so there is no animation to
    # rebuild and the reverse export legitimately writes no `.a`.
    "no_rebuilt_a": {
        os.path.join("Neutrals", "Blacknaga", "character_neutrals_blacknaga.gltf"),
        os.path.join("Neutrals", "CityGuard", "character_neutrals_cityguard.gltf"),
        os.path.join("Neutrals", "WaterSnake",
                     "character_neutrals_watersnake_sea.gltf"),
    },
    # dis3tool wrote this .dds with a 24-bit RGB header over a 32-bit payload,
    # so its own header is self-contradictory and it cannot be trusted.  The
    # reverse export warns and passes the shipped `.t` through instead.
    "unparsable_dds": {
        os.path.join("Neutrals", "OrcKing", "weapon_neutrals_orcking_sword.dds"),
    },
}


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO)


# glTF component type -> byte size, and type -> component count
_COMP_SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


@contextlib.contextmanager
def _quiet():
    """Swallow the CLI's banner/table output while a test drives it."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


# --------------------------------------------------------------------------- #
#  import
# --------------------------------------------------------------------------- #
def test_import_every_asset():
    """Every bundled asset of every format parses."""
    problems = []

    for p in _assets(".g"):
        mesh = gfile.parse_geometry_file(open(p, "rb").read())
        if not mesh.raw:
            if len(mesh.vertices) != mesh.vertex_count:
                problems.append(f"{_rel(p)}: vertex_count mismatch")
            if len(mesh.indices) != mesh.tri_count * 3:
                problems.append(f"{_rel(p)}: index count mismatch")
            for v in mesh.vertices:
                if mesh.bones and any(b >= len(mesh.bones) for b in v.bones):
                    problems.append(f"{_rel(p)}: joint index out of range")
                    break

    for p in _assets(".a"):
        if not animmod.parse_anim(open(p, "rb").read()).bones and \
                not animmod.parse_anim(open(p, "rb").read()).morphs:
            problems.append(f"{_rel(p)}: parsed with neither bones nor morphs")

    for p in _assets(".ac"):
        with open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
            if not acmod.parse_ac(fh.read()).states:
                problems.append(f"{_rel(p)}: no states parsed")

    for p in _assets(".gltf"):
        if not gltfmod.load_gltf(p).vertices:
            problems.append(f"{_rel(p)}: glTF loaded with no vertices")

    for p in _assets(".t"):
        info = texmod.parse_t(open(p, "rb").read(), p)
        if info.payload_size() != len(info.payload):
            problems.append(
                f"{_rel(p)}: payload {len(info.payload)} != "
                f"{info.payload_size()} expected from the header")

    for p in _assets(".dds"):
        if _rel(p) in KNOWN["unparsable_dds"]:
            continue
        texmod.parse_dds(open(p, "rb").read(), p)

    assert not problems, "\n".join(problems)


# --------------------------------------------------------------------------- #
#  round-trip
# --------------------------------------------------------------------------- #
def test_g_roundtrip_is_bytewise_lossless():
    """parse -> write reproduces every `.g` exactly (structured or raw)."""
    bad = []
    structured = 0
    for p in _assets(".g"):
        data = open(p, "rb").read()
        mesh = gfile.parse_geometry_file(data)
        structured += not mesh.raw
        attrs = {}
        if not mesh.raw:
            attrs, _ = gfile.parse_attributes(data)
        if gfile.write_geometry_file(mesh, attrs) != data:
            bad.append(_rel(p))
    assert not bad, f"{len(bad)} .g files do not round-trip: {bad[:5]}"
    # The structured (non-passthrough) share must not silently regress: the
    # compound containers used to need the raw passthrough; since the writer
    # reproduces their parts (donated prefix/attrs/tail) the whole corpus
    # parses structurally.
    total = len(_assets(".g"))
    assert structured == total, \
        f"{structured} of {total} .g parse structurally, expected {total}"


def test_a_roundtrip_is_bytewise_lossless():
    bad = []
    for p in _assets(".a"):
        data = open(p, "rb").read()
        if animmod.write_anim(animmod.parse_anim(data)) != data:
            bad.append(_rel(p))
    assert not bad, f"{len(bad)} .a files do not round-trip: {bad[:5]}"


def test_texture_roundtrip_is_lossless():
    bad = []
    for p in _assets(".t"):
        data = open(p, "rb").read()
        info = texmod.parse_t(data, p)
        if texmod.dds_to_t(texmod.t_to_dds(data, p), info.t_header, p) != data:
            bad.append(_rel(p))
    assert not bad, f"{len(bad)} .t files do not round-trip: {bad[:5]}"


# --------------------------------------------------------------------------- #
#  forward export: .g -> glTF
# --------------------------------------------------------------------------- #
def test_forward_export_every_g():
    """Every `.g` forward-exports to a self-consistent, re-readable glTF."""
    problems = []
    exported = 0
    with tempfile.TemporaryDirectory() as tmp:
        for p in _assets(".g"):
            rel = _rel(p)
            out = os.path.join(tmp, rel[:-2] + ".gltf")
            animation = cli._find_animation_for_geometry(p)
            try:
                gt, bt = cli._export_gl(p, animation, out, texture=None,
                                        quiet=True)
            except ValueError as exc:
                if rel in KNOWN["no_gltf"]:
                    continue
                problems.append(f"{rel}: refused — {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{rel}: {type(exc).__name__}: {exc}")
                continue
            exported += 1
            doc = json.load(open(gt))
            size = os.path.getsize(bt)
            for buf in doc.get("buffers", []):
                if buf.get("byteLength") != size:
                    problems.append(
                        f"{rel}: buffer byteLength {buf.get('byteLength')} "
                        f"!= {size} on disk")
            views = doc.get("bufferViews", [])
            for i, acc in enumerate(doc.get("accessors", [])):
                if not acc["count"]:
                    continue
                view = views[acc["bufferView"]]
                need = (_COMP_SIZE[acc["componentType"]]
                        * _NCOMP[acc["type"]] * acc["count"])
                end = acc.get("byteOffset", 0) + need
                if end > view["byteLength"]:
                    problems.append(
                        f"{rel}: accessor {i} needs {end} bytes, its "
                        f"bufferView holds {view['byteLength']}")
            # our own reader must be able to load what we wrote
            try:
                gltfmod.load_gltf(gt)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{rel}: exported glTF not re-readable — "
                                f"{type(exc).__name__}: {exc}")
    assert not problems, "\n".join(problems[:20])
    assert exported == len(_assets(".g")) - len(KNOWN["no_gltf"]), \
        f"only {exported} of {len(_assets('.g'))} .g files exported"


# --------------------------------------------------------------------------- #
#  reverse export: glTF -> GM
# --------------------------------------------------------------------------- #
def test_reverse_export_every_gltf():
    """Every bundled `.gltf` reverse-exports, and the output re-parses."""
    problems = []
    with_a = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, p in enumerate(_assets(".gltf")):
            rel = _rel(p)
            out_dir = os.path.join(tmp, str(i))
            try:
                cli._export(p, out_dir, 0, anim=True, quiet=True)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{rel}: {type(exc).__name__}: {exc}")
                continue
            base = os.path.splitext(os.path.basename(p))[0]
            for ext in (".g", ".scene", ".ac"):
                if not os.path.exists(os.path.join(out_dir, base + ext)):
                    problems.append(f"{rel}: {ext} was not produced")
            rebuilt = [f for f in os.listdir(out_dir) if f.endswith(".a")]
            if rebuilt:
                with_a += 1
            elif rel not in KNOWN["no_rebuilt_a"]:
                problems.append(f"{rel}: no .a was rebuilt")
            # everything we wrote must be readable again
            gp = os.path.join(out_dir, base + ".g")
            if os.path.exists(gp):
                gfile.parse_geometry_file(open(gp, "rb").read())
            for a in rebuilt:
                animmod.parse_anim(open(os.path.join(out_dir, a), "rb").read())
            acp = os.path.join(out_dir, base + ".ac")
            if os.path.exists(acp):
                with open(acp, "r", encoding="utf-8") as fh:
                    cfg = acmod.parse_ac(fh.read())
                if not cfg.states:
                    problems.append(f"{rel}: written .ac has no states")
                # the editor lists a unit's animation records straight from
                # the `.ac`: a reused config must carry EXACTLY the shipped
                # record set (ships: Idle+Run, walls: no Attack, DragonRed:
                # +Attack_2), a generated one the canonical five
                src_ac = os.path.join(os.path.dirname(p),
                                      os.path.basename(acp))
                names = tuple(s.name for s in cfg.states)
                if os.path.isfile(src_ac):
                    if open(acp, "rb").read() != open(src_ac, "rb").read():
                        problems.append(f"{rel}: .ac not byte-identical")
                elif names != ("Idle", "Attack", "Damage", "Death", "Run"):
                    problems.append(f"{rel}: generated .ac records {names}, "
                                    f"expected the canonical five")
    assert not problems, "\n".join(problems[:20])
    # the reverse export must not silently drop animations any more
    assert with_a == len(_assets(".gltf")) - len(KNOWN["no_rebuilt_a"]), \
        f"only {with_a} glTFs produced a rebuilt .a"


def test_reverse_export_picks_the_units_own_animation():
    """`detect_anim_files` must be driven by the unit, not by whichever `.a`
    happens to sort last in a folder holding several units (Neutrals/Ship
    holds four).  The resolved file must either be named by the unit's own
    `.ac`, or belong to the same unit's stem — never to a sibling unit."""
    wrong = []
    for p in _assets(".g"):
        folder = os.path.dirname(p)
        stem = os.path.splitext(os.path.basename(p))[0]
        main_stem = stem[:-4] if stem.lower().endswith("_lod") else stem
        expected = set()
        ac_path = os.path.join(folder, stem + ".ac")
        if os.path.isfile(ac_path):
            with open(ac_path, "r", encoding="utf-8-sig",
                      errors="replace") as fh:
                expected = {os.path.basename(s.file.replace("\\", "/"))
                            for s in acmod.parse_ac(fh.read()).states
                            if s.file}
            expected = {n for n in expected
                        if os.path.isfile(os.path.join(folder, n))}
        got = acmod.detect_anim_files(folder, stem)["Idle"]
        if got in expected or got.startswith(main_stem):
            continue
        if not expected:
            # the base owns nothing of its own: adopting a *sibling* `.ac`'s
            # resolvable streams is the documented fallback (a re-saved
            # `angel_edit.gltf` inside the Angel folder, leader set
            # variants) — require exactly that provenance
            claimed = os.path.isfile(os.path.join(folder, got)) and any(
                got == acmod.detect_anim_files(folder, other)["Idle"]
                for other in
                (os.path.splitext(os.path.basename(p))[0]
                 for p in glob.glob(os.path.join(folder, "*.ac")))
                if other != stem)
            if claimed:
                continue
        wrong.append(f"{_rel(p)}: resolved {got}, expected one of "
                     f"{sorted(expected) or [main_stem + '*']}")
    assert not wrong, "\n".join(wrong[:20])


# --------------------------------------------------------------------------- #
#  animation completeness (import and export)
# --------------------------------------------------------------------------- #
def test_lod_import_concatenates_the_lod_config_streams():
    """`<mesh>_lod.g` must animate with the streams its OWN `<mesh>_lod.ac`
    names, all of them: dis3tool concatenates every `.ac` stream and the
    lod configs are no exception (cleric_lod: iadd_lod 356 + run_lod 25 =
    381 frames — resolving the set from the main `.ac` handed back the lod
    Idle stream alone)."""
    unit = os.path.join(REPO, "Empire", "Cleric")
    g = os.path.join(unit, "character_empire_cleric_lod.g")
    anim = cli._find_animation_for_geometry(g)
    assert anim and anim.endswith("character_empire_cleric_iadd_lod.a")
    with tempfile.TemporaryDirectory() as tmp, _quiet():
        out = os.path.join(tmp, "lod.gltf")
        cli._export_gl(g, anim, out, None, quiet=True)
        j = json.load(open(out))
        frames = max(j["accessors"][s["input"]]["count"]
                     for a in j["animations"] for s in a["samplers"])
    assert frames == 381, f"expected 381 (356+25), got {frames}"


def test_sole_animation_fallback_needs_a_claim():
    """The sole `.a` in a multi-mesh folder may animate only the `.g` that a
    sibling `.ac` claims it for: Wolfsnow's config names `wolf.g` (animated,
    also for its `_lod` variant), while the Cyclop's rock projectile — one
    bone, claimed by nobody — stays rigid instead of borrowing the Cyclop's
    39-bone skeleton."""
    rock = os.path.join(REPO, "Neutrals", "Cyclop",
                        "character_neutrals_cyclop_rock.g")
    assert cli._find_animation_for_geometry(rock) is None
    folder = os.path.join(REPO, "Neutrals", "Wolfsnow")
    for stem in ("character_neutrals_wolf", "character_neutrals_wolf_lod"):
        got = cli._find_animation_for_geometry(
            os.path.join(folder, stem + ".g"))
        assert got and got.endswith("character_neutrals_wolfsnow.a"), got


def test_reverse_export_writes_every_ac_stream():
    """The reused `.ac` references `iadd.a` AND `run.a`: the reverse export
    must write each named stream — sliced back out of the concatenated glTF
    animation at the donor frame-count boundaries — or the produced unit
    would ship with a dangling Run reference.  Each file byte-identical."""
    gt = os.path.join(REPO, "Empire", "Cleric",
                      "character_empire_cleric.gltf")
    folder = os.path.dirname(gt)
    with tempfile.TemporaryDirectory() as tmp, _quiet():
        cli._export(gt, tmp, 0, anim=True, quiet=True)
        for fn in ("character_empire_cleric_iadd.a",
                   "character_empire_cleric_run.a"):
            mine = os.path.join(tmp, fn)
            assert os.path.isfile(mine), f"{fn} was not written"
            assert (open(mine, "rb").read()
                    == open(os.path.join(folder, fn), "rb").read()), fn


def test_leader_set_animation_adopts_sibling_donor():
    """A leader set variant (set1/2/3) carries no `.ac` of its own, yet its
    glTF animation IS the folder's baseanims stream — the rebuild must come
    out byte-identical to it (donor scaffolding over verified data), named
    as the folder's `.ac` references it (baseanims.a) so the engine can
    actually resolve the generated set config."""
    gt = os.path.join(REPO, "Empire", "Leader-Archmage",
                      "character_empire_leader-archmage_set1.gltf")
    folder = os.path.dirname(gt)
    with tempfile.TemporaryDirectory() as tmp, _quiet():
        cli._export(gt, tmp, 0, anim=True, quiet=True)
        a = os.path.join(tmp, "character_empire_leader-archmage_baseanims.a")
        donor = os.path.join(
            folder, "character_empire_leader-archmage_baseanims.a")
        assert os.path.isfile(a), sorted(os.listdir(tmp))
        assert open(a, "rb").read() == open(donor, "rb").read()
        # the generated .ac must reference a file that was actually written
        cfg = acmod.parse_ac(open(os.path.join(
            tmp, "character_empire_leader-archmage_set1.ac"),
            encoding="utf-8-sig", errors="replace").read())
        for st in cfg.states:
            fn = os.path.basename(st.file.replace("\\", "/"))
            assert os.path.isfile(os.path.join(tmp, fn)), \
                f"{st.name} -> {fn} was not written"


def test_explicit_stream_import_keeps_ac_order():
    """`-a run.a` selects the *unit*, not the layout: the concatenated
    export must keep `.ac` order (the Idle stretch first, morph targets from
    the last stream), byte-identical to the auto-detected import."""
    g = os.path.join(REPO, "Empire", "Cleric", "character_empire_cleric.g")
    run = os.path.join(os.path.dirname(g), "character_empire_cleric_run.a")
    with tempfile.TemporaryDirectory() as tmp, _quiet():
        auto = os.path.join(tmp, "auto.gltf")
        explicit = os.path.join(tmp, "explicit.gltf")
        cli._export_gl(g, cli._find_animation_for_geometry(g),
                       auto, None, quiet=True)
        cli._export_gl(g, run, explicit, None, quiet=True)
        ja, jb = json.load(open(auto)), json.load(open(explicit))
        for j in (ja, jb):        # the buffer uri is the output file's own
            j["buffers"][0]["uri"] = ""
        assert ja == jb
        assert (open(auto[:-5] + ".bin", "rb").read()
                == open(explicit[:-5] + ".bin", "rb").read())



# --------------------------------------------------------------------------- #
#  Blender-authored glTF (re-saved textures, renamed stem, painted weights)
# --------------------------------------------------------------------------- #
def _blenderize(src_gltf: str, out_gltf: str, paint: bool = False):
    """Dis3tool reference glTF -> what Blender's exporter tends to write:
    an extra Armature node, textures re-saved as .png, the 4 weight lanes
    sorted descending, and (optionally) user-painted weights."""
    bin_name = os.path.basename(os.path.splitext(out_gltf)[0]) + ".bin"
    src_bin = os.path.splitext(src_gltf)[0] + ".bin"
    dst_bin = os.path.join(os.path.dirname(out_gltf), bin_name)
    if os.path.abspath(src_bin) != os.path.abspath(dst_bin):
        shutil.copy(src_bin, dst_bin)
    j = json.load(open(src_gltf))
    data = bytearray(open(os.path.splitext(src_gltf)[0] + ".bin", "rb").read())
    j["asset"]["generator"] = "Khronos glTF Blender I/O 4.2"
    arm = len(j["nodes"])
    j["nodes"].append({"name": "Armature", "children": []})
    keep = []
    for n in j["scenes"][0]["nodes"]:
        if j["nodes"][n].get("name") == "Bip01":
            j["nodes"][arm]["children"].append(n)
        else:
            keep.append(n)
    j["scenes"][0]["nodes"] = keep + [arm]
    for img in j.get("images", []):
        uri = img.get("uri", "")
        if not uri:
            continue
        img["uri"] = os.path.splitext(uri)[0] + ".png"
        png_path = os.path.join(os.path.dirname(out_gltf), img["uri"])
        if not os.path.exists(png_path):
            w = h = 4
            raw = b"".join(b"\x00" + bytes(w * 4) for _y in range(h))

            def _ck(t, d):
                c = struct.pack(">I", len(d)) + t + d
                return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

            with open(png_path, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n"
                         + _ck(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6,
                                                    0, 0, 0))
                         + _ck(b"IDAT", zlib.compress(raw))
                         + _ck(b"IEND", b""))
    for mesh in j["meshes"]:
        for prim in mesh["primitives"]:
            if "WEIGHTS_0" not in prim["attributes"]:
                continue
            wacc = j["accessors"][prim["attributes"]["WEIGHTS_0"]]
            jacc = j["accessors"][prim["attributes"]["JOINTS_0"]]
            wbv = j["bufferViews"][wacc["bufferView"]]
            jbv = j["bufferViews"][jacc["bufferView"]]
            woff = wbv.get("byteOffset", 0) + wacc.get("byteOffset", 0)
            joff = jbv.get("byteOffset", 0) + jacc.get("byteOffset", 0)
            # interleaved vertex buffers (byteStride) must be touched at
            # the accessor's own stride, or the paint smears across the
            # neighbouring attributes
            wstride = wbv.get("byteStride") or 16
            jstride = jbv.get("byteStride") or 4
            for v in range(wacc["count"]):
                ws = list(struct.unpack_from("<4f", data, woff + wstride * v))
                js = list(struct.unpack_from("<4B", data, joff + jstride * v))
                if paint and v % 20 == 0 and ws[1] > 0:
                    d = ws[0] * 0.33
                    ws[0], ws[1] = ws[0] - d, ws[1] + d
                ws, js = zip(*sorted(zip(ws, js), key=lambda q: -q[0]))
                struct.pack_into("<4f", data, woff + wstride * v, *ws)
                struct.pack_into("<4B", data, joff + jstride * v, *js)
    j["buffers"][0]["uri"] = bin_name
    json.dump(j, open(out_gltf, "w"))
    with open(os.path.join(os.path.dirname(out_gltf), bin_name), "wb") as fh:
        fh.write(bytes(data))


def test_blender_resaved_gltf_exports_battle_ready():
    """The documented user flow - import a unit, paint weights in Blender,
    re-save the glTF (textures become .png, an Armature node appears,
    weight lanes re-sort) - must export a unit the battle loader can read:
    every `.ac` state's file present with its frame range inside the
    stream, the meshfile resolvable, and a `.t` for the diffuse material.
    The donor animation files come out byte-identical."""
    for paint in (False, True):
        with tempfile.TemporaryDirectory() as tmp, _quiet():
            work = os.path.join(tmp, "unit")
            shutil.copytree(_refgl.SRC, work)
            src = _refgl.copy_ref_into(work)
            gt = os.path.join(work, "character_empire_angel.gltf")
            _blenderize(src, gt, paint=paint)
            out = os.path.join(tmp, "out")
            cli._export(gt, out, 0, anim=True, quiet=True)
            cfg = acmod.parse_ac(open(
                os.path.join(out, "character_empire_angel.ac"),
                encoding="utf-8-sig", errors="replace").read())
            for st in cfg.states:
                fn = os.path.basename(st.file.replace("\\", "/"))
                p = os.path.join(out, fn)
                assert os.path.isfile(p), f"{st.name} -> {fn} missing"
                if fn.endswith(".a"):
                    fc = animmod.parse_anim(open(p, "rb").read()).frame_count
                    assert st.frame1 <= fc, \
                        f"{st.name}: {st.frame1} > {fn} ({fc})"
                mf = os.path.basename(st.meshfile.replace("\\", "/"))
                assert os.path.isfile(os.path.join(out, mf))
            attrs, _ = gfile.parse_attributes(
                open(os.path.join(out, "character_empire_angel.g"),
                     "rb").read())
            want = os.path.splitext(attrs["material0_diffuse"])[0] + ".t"
            assert os.path.isfile(os.path.join(out, want)), want
            # the donor .a files are the animation source of truth here
            for fn in ("character_empire_angel_idle.a",
                       "character_empire_angel_attack.a"):
                assert (open(os.path.join(out, fn), "rb").read()
                        == open(os.path.join(work, fn), "rb").read()), fn


def test_renamed_gltf_exports_battle_ready():
    """`angel_edit.gltf` saved inside the unit folder: no `.ac` of its own,
    so a config must be GENERATED - referencing files that were actually
    written (the folder's baseanims streams), with frame ranges inside
    them, plus the textures next to the glTF."""
    with tempfile.TemporaryDirectory() as tmp, _quiet():
        work = os.path.join(tmp, "unit")
        shutil.copytree(_refgl.SRC, work)
        src = _refgl.copy_ref_into(work)
        gt = os.path.join(work, "angel_edit.gltf")
        _blenderize(src, gt)
        out = os.path.join(tmp, "out")
        cli._export(gt, out, 0, anim=True, quiet=True)
        cfg = acmod.parse_ac(open(os.path.join(out, "angel_edit.ac"),
                                  encoding="utf-8-sig",
                                  errors="replace").read())
        for st in cfg.states:
            fn = os.path.basename(st.file.replace("\\", "/"))
            p = os.path.join(out, fn)
            assert os.path.isfile(p), f"{st.name} -> {fn} missing"
            fc = animmod.parse_anim(open(p, "rb").read()).frame_count
            assert st.frame1 <= fc, f"{st.name}: {st.frame1} > {fc}"
        assert os.path.isfile(os.path.join(out, "angel_edit.g"))
        assert any(f.endswith(".t") for f in os.listdir(out)), \
            "no texture written for a bare-stem export"


# --------------------------------------------------------------------------- #
#  CLI surface
# --------------------------------------------------------------------------- #
def test_cli_commands_exit_cleanly():
    """Every subcommand succeeds on a healthy unit and fails on a bad path."""
    unit = os.path.join(REPO, "Neutrals", "AirElemental")
    stem = os.path.join(unit, "character_neutrals_airelemental")
    with tempfile.TemporaryDirectory() as tmp, _quiet():
        for argv, want in (
            (["analyze", unit], 0),
            (["import", stem + ".g",
              "-o", os.path.join(tmp, "fwd", "u.gltf")], 0),
            (["import", stem + ".g", "-a", stem + "_iadd.a",
              "-o", os.path.join(tmp, "fwd2", "u.gltf")], 0),
            (["export", stem + ".gltf", "-o", os.path.join(tmp, "rev")], 0),
            (["validate", os.path.join(tmp, "fwd", "u.gltf")], 0),
            (["dump", stem + ".g"], 0),
            (["bundle", unit, "-o", os.path.join(tmp, "b")], 0),
            (["texture", "info", stem + ".t"], 0),
            (["texture", "convert", stem + ".t",
              "-o", os.path.join(tmp, "t.dds")], 0),
            # failures must surface in the exit code, not be swallowed
            (["analyze", os.path.join(tmp, "nope")], 1),
            (["validate", os.path.join(tmp, "nope.gltf")], 1),
            (["export-all", os.path.join(tmp, "nope"), "-o", tmp], 1),
            (["bundle", tmp, "-o", tmp], 1),
        ):
            got = cli._run(argv)
            assert got == want, f"d3tool {' '.join(argv[:1])} -> {got}, want {want}"


def test_cli_texture_commands_handle_every_gm_format_code():
    """`texture info` / `convert` must not dereference a missing fourCC —
    20 bundled `.t` files are uncompressed and carry none."""
    seen = set()
    with tempfile.TemporaryDirectory() as tmp, _quiet():
        for p in _assets(".t"):
            info = texmod.parse_t(open(p, "rb").read(), p)
            seen.add(info.gm_format)
            if cli._run(["texture", "info", p]) != 0:
                raise AssertionError(f"texture info failed on {_rel(p)}")
            dst = os.path.join(tmp, f"{info.gm_format}_{os.path.basename(p)}.dds")
            if cli._run(["texture", "convert", p, "-o", dst]) != 0:
                raise AssertionError(f"texture convert failed on {_rel(p)}")
    assert {1, 2, 3, 4, 5, 6, 7, 8} <= seen, f"untested GM codes: {sorted(seen)}"


if __name__ == "__main__":
    failures = 0
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
