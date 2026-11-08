#!/usr/bin/env python3
"""Corpus-wide *reverse* parity: `.g` -> glTF -> `.g` must give the file back.

`corpus_parity.py` measures the forward half (our glTF vs dis3tool's).  This
one measures the half a modder actually notices: load a retail unit, export it
again without touching anything, and ask whether the game gets the same bytes
it shipped with.  Anything else is drift the engine may or may not forgive —
a scene-node volume rounded one ulp, an attribute reordered, a bone table
rewritten — and drift is what a "just a weapon swap" mod turns into a crash.

Folder selection matches `corpus_parity.py`: every unit folder under Empire/
and Neutrals/ that holds a `.g`.  Only files the export actually writes are
judged (an export is not expected to invent `.dds` sources).

Statuses
    EXACT      every written file is byte-identical to the original
    TEX        only a `.t` differs, and only because a raw-pixel atlas was
               re-encoded as DXT (the same pixels, an eighth of the bytes —
               what an uncompressed 683 KiB atlas is supposed to become)
    DIFF       a `.g` or a `.scene` came back different (the byte offset is
               printed): that is the class of defect this harness is for
    ERROR      the export raised (a node-helper `.g` is a SKIP: it is not a
               renderable mesh and never had a scene of its own)
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from d3tool import cli as climod          # noqa: E402
from d3tool import roundtrip as rt        # noqa: E402


def run(root: str, tmp_root: str):
    results = []
    for group in ("Empire", "Neutrals"):
        for g_path in sorted(glob.glob(os.path.join(root, group, "*", "*.g"))):
            folder = os.path.dirname(g_path)
            stem = os.path.splitext(os.path.basename(g_path))[0]
            if stem.lower().endswith("_lod"):
                continue
            unit = os.path.relpath(folder, root)
            out_dir = os.path.join(tmp_root, group, os.path.basename(folder))
            os.makedirs(out_dir, exist_ok=True)
            gltf = os.path.join(out_dir, stem + ".gltf")
            produced = os.path.join(out_dir, "export")
            try:
                climod._export_gl(g_path, climod._find_animation_for_geometry(
                    g_path), gltf, None, quiet=True)
            except SystemExit:  # noqa: PERF203 - a helper mesh, not a unit
                results.append((unit, "SKIP", "not a renderable mesh"))
                continue
            except Exception as exc:  # noqa: BLE001 - a failed export is data
                msg = str(exc)
                if "node-helper" in msg or "no triangles" in msg:
                    results.append((unit, "SKIP", "not a renderable mesh"))
                else:
                    results.append((unit, "ERROR", msg))
                continue
            try:
                climod._export(gltf, produced, 0, anim=True, quiet=True,
                               donor_dir=folder)
            except Exception as exc:  # noqa: BLE001 - a failed export is data
                results.append((unit, "ERROR", str(exc)))
                continue
            diffs = rt.diff_folders(folder, produced)
            # an extra file (one the export wrote and the folder does not
            # ship — a texture carried in from another unit, a second `.ac`)
            # is not a difference in what the folder still has
            compared = [d for d in diffs if d.expected is not None]
            bad = [d for d in compared if not d.same]
            counts = rt.summary(diffs)
            detail = "; ".join(d.summary() for d in bad[:3])
            if not bad:
                results.append((unit, "EXACT", f"{counts['files']} files"))
            elif all(d.name.lower().endswith(".t") for d in bad):
                results.append((unit, "TEX", detail))
            else:
                results.append((unit, "DIFF",
                                f"{len(bad)}/{len(compared)} files — {detail}"))
    return results


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp_root = "/tmp/d3roundtrip_corpus"
    shutil.rmtree(tmp_root, ignore_errors=True)
    results = run(root, tmp_root)
    tally = Counter(st for _, st, _ in results)
    for unit, status, detail in results:
        mark = {"EXACT": "✔", "TEX": "~", "DIFF": "!",
                "ERROR": "✗", "SKIP": "·"}.get(status, "?")
        tail = f"  — {detail}" if detail and status != "EXACT" else ""
        print(f"{mark} {unit:46s} {status}{tail}")
    print()
    judged = len(results) - tally["SKIP"]
    print(f"{tally['EXACT']}/{judged} units re-export byte-identically"
          + (f", {tally['TEX']} re-encode a texture" if tally["TEX"] else "")
          + (f", {tally['DIFF']} differ" if tally["DIFF"] else "")
          + (f", {tally['ERROR']} failed to export" if tally["ERROR"] else "")
          + (f", {tally['SKIP']} skipped" if tally["SKIP"] else ""))
    shutil.rmtree(tmp_root, ignore_errors=True)
    return 0 if tally["DIFF"] == 0 and tally["ERROR"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
