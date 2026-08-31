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
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from d3tool import ac as acmod            # noqa: E402
from d3tool import anim as animmod        # noqa: E402
from d3tool import cli                    # noqa: E402
from d3tool import gfile                  # noqa: E402
from d3tool import gltf as gltfmod        # noqa: E402
from d3tool import texture as texmod      # noqa: E402

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
                    if not acmod.parse_ac(fh.read()).states:
                        problems.append(f"{rel}: written .ac has no states")
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
        wrong.append(f"{_rel(p)}: resolved {got}, expected one of "
                     f"{sorted(expected) or [main_stem + '*']}")
    assert not wrong, "\n".join(wrong[:20])


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
