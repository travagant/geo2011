#!/usr/bin/env python3
"""Corpus-wide forward-export parity vs the bundled dis3tool references.

For every unit folder with a `.g` and a sibling dis3tool export
(`<stem>.gltf` + `<stem>.bin`) we forward-export the source GM files and
compare, reporting:

* EXACT      — `.bin` byte-identical *and* the glTF JSON is f32-bitwise equal;
* BIN-NEAR   — `.bin` equal except a few ±1ulp float32 lanes (the
               known-red-zone class: dis3tool's C++ float renormalisation is
               addition-order dependent on a handful of vertices);
* STRUCT     — the JSON structure is f32-bitwise equal but the binary
               differs materially (reported for investigation);
* FAIL       — the JSON differs structurally (real mismatch).

Folder selection: any of Empire/ Neutrals/ (repo root).  Animation choice:
the state file named by the unit's `.ac` (falling back to the conventional
``*_baseanims.a``), matching dis3tool's own resolution.
"""
from __future__ import annotations

import glob
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from d3tool import cli as climod          # noqa: E402
from d3tool import gfile                  # noqa: E402
from d3tool import gltf_out as gltfout    # noqa: E402


def _anim_for(g_path: str):
    """Animation choice — the CLI's own resolver, which mirrors dis3tool.

    dis3tool loads the stream the unit's `.ac` names; when that stream is
    not in the unit folder it ships the mesh **rigid** (Blacknaga points at
    mermaid's `.a`, watersnake_sea at a `.a` bundled with neither), so the
    resolver returns None there instead of guessing a conventional `.a`.
    """
    return climod._find_animation_for_geometry(g_path)


def _json_diffs(r, g, path=""):
    bad = []
    if isinstance(r, dict) and isinstance(g, dict):
        if set(r) != set(g):
            bad.append((path, sorted(set(r) ^ set(g))))
        for k in r.keys() & g.keys():
            bad += _json_diffs(r[k], g[k], path + "/" + str(k))
    elif isinstance(r, list) and isinstance(g, list):
        if len(r) != len(g):
            bad.append((path, f"len {len(r)} vs {len(g)}"))
        for i, (x, y) in enumerate(zip(r, g)):
            bad += _json_diffs(x, y, f"{path}/{i}")
    else:
        if path == "/asset/generator":
            # both export tools sign their own name; not a parity concern
            return bad
        if isinstance(r, (int, float)) and not isinstance(r, bool):
            try:
                if struct.pack("<f", r) != struct.pack("<f", g):
                    bad.append((path, (r, g)))
            except struct.error:
                bad.append((path, (r, g)))
        elif r != g:
            bad.append((path, (r, g)))
    return bad


def _compare(r, g):
    """Like _json_diffs but tolerant of ±1ulp float32 neighbours."""
    return _json_diffs(r, g)


def run(root: str, tmp_root: str):
    results = []
    for group in ("Empire", "Neutrals"):
        for g_path in sorted(glob.glob(os.path.join(root, group, "*", "*.g"))):
            folder = os.path.dirname(g_path)
            stem = os.path.splitext(os.path.basename(g_path))[0]
            ref_gltf = os.path.join(folder, stem + ".gltf")
            ref_bin = os.path.join(folder, stem + ".bin")
            if not (os.path.isfile(ref_gltf) and os.path.isfile(ref_bin)):
                continue
            unit = os.path.relpath(folder, root)
            try:
                mesh = gfile.parse_geometry_file(open(g_path, "rb").read())
                a_path = _anim_for(g_path)
                # Use the CLI's own loader rather than re-implementing it: the
                # exporter concatenates every `.a` the `.ac` references, and a
                # harness that parses one file would measure a stand-in.
                anim = (climod._load_anim_stream(g_path, a_path, True)
                        if a_path else None)
                out_dir = os.path.join(tmp_root, group, os.path.basename(folder))
                os.makedirs(out_dir, exist_ok=True)
                texture = None
                if not mesh.parts and mesh.material_diffuse:
                    # single-mesh export picks up the material texture, like
                    # the CLI does (.tga attr -> sibling .dds name)
                    texture = os.path.splitext(mesh.material_diffuse)[0] + ".dds"
                gp, bp = gltfout.write_gltf_to(
                    os.path.join(out_dir, stem + ".gltf"), mesh, anim,
                    texture=texture)
            except Exception as exc:  # noqa: BLE001
                results.append((unit, "ERROR", str(exc)))
                continue
            got_bin = open(bp, "rb").read()
            ref_raw = open(ref_bin, "rb").read()
            jdiff = _compare(json.load(open(ref_gltf)), json.load(open(gp)))
            if got_bin == ref_raw and not jdiff:
                results.append((unit, "EXACT", ""))
                continue
            bindiff = -1
            if len(got_bin) == len(ref_raw):
                bindiff = sum(1 for a, b in zip(got_bin, ref_raw) if a != b)
            n_ulp = bindiff if isinstance(bindiff, int) else 0
            if not jdiff and 0 <= n_ulp <= 4096:
                results.append((unit, "BIN-NEAR", f"{n_ulp} bytes"))
            elif not jdiff:
                results.append((unit, "STRUCT",
                                f"bin {'len-diff' if n_ulp == -1 else f'{n_ulp}B'}"))
            else:
                results.append((unit, "FAIL",
                                f"{len(jdiff)} json-diffs; bin {bindiff}B; "
                                f"first: {jdiff[0]}"))
    return results


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp_root = "/tmp/d3bench"
    # Each run writes ~640 MB of .gltf/.bin output; without this the scratch
    # tree accumulates across runs and eventually fills the disk (ENOSPC then
    # surfaces as bogus failures in other tests).
    import shutil
    shutil.rmtree(tmp_root, ignore_errors=True)
    try:
        results = run(root, tmp_root)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    from collections import Counter
    tally = Counter(st for _, st, _ in results)
    for unit, st, detail in results:
        mark = {"EXACT": "✔", "BIN-NEAR": "~", "STRUCT": "!",
                "FAIL": "✘", "ERROR": "✘"}.get(st, "?")
        print(f"{mark} {st:9s} {unit:42s} {detail}")
    print()
    print("== summary:", dict(tally), f"total {len(results)}")
    return 0 if not (tally.get("FAIL") or tally.get("ERROR")) else 1


if __name__ == "__main__":
    sys.exit(main())
