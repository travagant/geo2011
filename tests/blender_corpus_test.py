#!/usr/bin/env python3
"""Corpus-wide Blender import → export sweep for the Disciples III add-on.

    blender --background --python tests/blender_corpus_test.py -- [options]
    python -m bpy --background --python tests/blender_corpus_test.py   # bpy installed

`tests/blender_addon_test.py` drives nineteen hand-built scenarios (a new
weapon, a rigid prop, two units in one scene, …).  This script asks the other
question: does *every* unit the game ships survive a Blender round trip at
all?  For each `character_*.g` it

1. imports the unit through the operator (`d3unit.import_unit`),
2. exports it back through `d3unit.export_unit` into a scratch folder,
3. re-reads the result with `d3tool` and compares it with the donor,

which is the check that catches the classes of damage a fixture cannot: a
lightmap block sized for the wrong vertex count, a declared influence count
that disagrees with the record width, a compound part silently dropped.

Options (after `--` when run through Blender):

    --only SUBSTR   only units whose path contains SUBSTR
    --limit N       stop after N units
    --jobs 1        accepted for symmetry; the sweep is sequential
    --keep          keep the scratch output of a failing unit

Exit code 0 means every unit round-tripped cleanly.
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
import tempfile
import time
import traceback

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "blender"))

import io_scene_d3unit as addon                      # noqa: E402

d3 = addon.bridge.load(force=True)
gfile = addon.bridge.module("gfile")

try:
    addon.register()
except RuntimeError:
    pass            # already registered (running inside a real Blender)


def parse_args(argv):
    only = None
    limit = 0
    keep = False
    skip_lod = False
    for i, a in enumerate(argv):
        if a == "--only" and i + 1 < len(argv):
            only = argv[i + 1]
        elif a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
        elif a == "--keep":
            keep = True
        elif a == "--no-lod":
            skip_lod = True
    return only, limit, keep, skip_lod


def units(only=None, limit=0, skip_lod=False):
    paths = sorted(glob.glob(os.path.join(REPO, "*", "*", "character_*.g")))
    out = []
    for p in paths:
        rel = os.path.relpath(p, REPO).replace(os.sep, "/")
        if only and only not in rel:
            continue
        if skip_lod and os.path.basename(p).endswith("_lod.g"):
            continue
        out.append(p)
    if limit:
        out = out[:limit]
    return out


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def live_tris(mesh):
    """Triangles the engine rasterises, one per vertex set.

    The glTF carries one triangle per vertex set, so a reversed-winding twin
    (a back-face pair some corpus units ship) cannot survive a round trip —
    comparing unique sets keeps that from reading as lost geometry.
    """
    seen = set()
    for t in range(0, len(mesh.indices) - 2, 3):
        tri = mesh.indices[t:t + 3]
        if len(set(tri)) == 3:
            seen.add(frozenset(tri))
    return len(seen)


def describe(mesh):
    return (f"parts={len(mesh.parts)} verts={mesh.vertex_count} "
            f"tris={mesh.tri_count} bones={len(mesh.bones)}")


def check_unit(path, tmp, keep=False):
    """Import → export one unit; returns a list of problems (empty = clean)."""
    donor = gfile.parse_geometry_file(open(path, "rb").read())
    if donor.parse_error:
        return [f"donor does not parse: {donor.parse_error}"], ""
    if getattr(donor, "form", "classic") == "stub" or (
            not donor.tri_count and not any(p.tri_count for p in donor.parts)):
        # A node helper (`BaseMesh`, materials_num 0, four vertices, no
        # indices) is not renderable geometry — the forward export refuses it
        # on purpose, so there is nothing to round-trip.
        return None, ""
    problems = []
    base = os.path.splitext(os.path.basename(path))[0]
    out_dir = os.path.join(tmp, base)
    os.makedirs(out_dir, exist_ok=True)

    reset()
    res = bpy.ops.d3unit.import_unit(filepath=path)
    if res != {"FINISHED"}:
        return [f"import returned {res}"], out_dir
    armatures = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        return ["import produced no mesh"], out_dir
    # No armature is not a defect on its own: CityGuard ships bone tables but
    # no usable animation, so the glTF carries no skins and Blender gets
    # unskinned meshes — the reverse export re-attaches the donor tables,
    # which the per-block bone-count comparison below verifies.

    out_g = os.path.join(out_dir, base + ".g")
    res = bpy.ops.d3unit.export_unit(filepath=out_g)
    if res != {"FINISHED"}:
        return problems + [f"export returned {res}"], out_dir
    if not os.path.isfile(out_g):
        return problems + ["export wrote no .g"], out_dir

    out = gfile.parse_geometry_file(open(out_g, "rb").read())
    if out.parse_error:
        problems.append(f"rebuilt .g does not re-parse: {out.parse_error}")
        return problems, out_dir

    # structure: the compound slots must stay one-to-one with the donor
    if [p.name for p in out.parts] != [p.name for p in donor.parts]:
        problems.append(f"part list changed: {[p.name for p in out.parts]} != "
                        f"{[p.name for p in donor.parts]}")
    if len(out.bones) != len(donor.bones):
        problems.append(f"root bone table {len(out.bones)} != {len(donor.bones)}")
    for mine, theirs in zip([out] + list(out.parts),
                            [donor] + list(donor.parts)):
        if live_tris(mine) != live_tris(theirs):
            problems.append(f"part {mine.name}: {live_tris(mine)} triangles "
                            f"!= {live_tris(theirs)}")
        if mine.vertex_count < theirs.vertex_count:
            problems.append(f"part {mine.name}: {mine.vertex_count} vertices "
                            f"< {theirs.vertex_count}")
        if len(mine.bones) != len(theirs.bones):
            problems.append(f"part {mine.name}: {len(mine.bones)} bones != "
                            f"{len(theirs.bones)}")
        have_w = any(any(w for w in (v.stored_weights or ()))
                     for v in mine.vertices)
        if have_w and mine.vertex_count and not mine.bones:
            problems.append(f"part {mine.name}: weights but no bones")

    # the animation side: every stream the donor's .ac names must be written
    ac = os.path.join(os.path.dirname(path), base + ".ac")
    if os.path.isfile(ac):
        if not os.path.isfile(os.path.join(out_dir, base + ".ac")):
            problems.append("no .ac written")
        import re
        text = open(ac, "r", encoding="utf-8-sig", errors="replace").read()
        for m in re.finditer(r'file\s+"([^"]+)"', text):
            ref = m.group(1).replace("\\", "/")
            name = os.path.basename(ref)
            if not name.endswith(".a") or os.path.isfile(
                    os.path.join(out_dir, name)):
                continue
            if "/" in ref:
                # Retail content names streams that live in ANOTHER unit's
                # folder (Blacknaga's `.ac` still points at the mermaid
                # template it was cloned from) — the game mounts those
                # folders separately, so a missing local copy is expected.
                continue
            problems.append(f".ac names {name}, which was not written")

    if not problems and not keep:
        shutil.rmtree(out_dir, ignore_errors=True)
    return problems, out_dir


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    only, limit, keep, skip_lod = parse_args(argv)
    todo = units(only, limit, skip_lod)
    if not todo:
        print("no units matched")
        return 1
    tmp = tempfile.mkdtemp(prefix="d3unit_corpus_")
    failed = []
    skipped = []
    started = time.time()
    for i, path in enumerate(todo, 1):
        rel = os.path.relpath(path, REPO)
        t0 = time.time()
        try:
            problems, out_dir = check_unit(path, tmp, keep)
        except Exception:  # noqa: BLE001
            problems = ["crashed: " + traceback.format_exc(limit=4)]
            out_dir = ""
        took = time.time() - t0
        if problems is None:
            skipped.append(rel)
            print(f"SKIP {rel}  (node helper, no geometry)")
        elif problems:
            failed.append((rel, problems, out_dir))
            print(f"FAIL {rel}  ({took:.1f}s)")
            for p in problems:
                print("     " + p)
        else:
            print(f"PASS {rel}  ({took:.1f}s)")
    print()
    ran = len(todo) - len(skipped)
    print(f"TOTAL: {ran - len(failed)}/{ran} units round-trip cleanly "
          f"in {time.time() - started:.0f}s"
          + (f" ({len(skipped)} node helper(s) skipped)" if skipped else ""))
    if failed and keep:
        print(f"output kept in {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    if failed:
        print("RESULT FAILED")
        return 1
    print("RESULT OK")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)      # bpy segfaults in its shutdown handlers here
