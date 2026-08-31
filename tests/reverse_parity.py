#!/usr/bin/env python3
"""Corpus-wide reverse-export parity vs the shipped originals.

For every unit folder with a dis3tool reference export (`<stem>.gltf` +
`<stem>.bin`) and the original GM files we run the CLI's own reverse
export (glTF -> GM) and compare every produced file against the original
with the same relative name:

* `.g`        — geometry (donor-assisted rebuild);
* `.a`        — the animation stream the unit's `.ac` names (donated
                scaffolding, concat-slice, positional recovery);
* `.scene` / `.ac` — reused verbatim, so they must match;
* `.t`        — textures converted from the reference `.dds`;
* `Aliases/`  — sound/FX alias files copied through.

The reverse output is a subset of the unit folder by construction, so
byte-identity everywhere is the only acceptable outcome.
"""
from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from d3tool import cli as climod       # noqa: E402


def _diff_bytes(a: bytes, b: bytes):
    if a == b:
        return 0
    if len(a) != len(b):
        return -1
    return sum(1 for x, y in zip(a, b) if x != y)


def run(root: str, tmp_root: str):
    results = []
    for group in ("Empire", "Neutrals"):
        for ref_gltf in sorted(glob.glob(os.path.join(root, group, "*",
                                                      "*.gltf"))):
            folder = os.path.dirname(ref_gltf)
            stem = os.path.splitext(os.path.basename(ref_gltf))[0]
            if not (os.path.isfile(os.path.join(folder, stem + ".bin"))
                    and os.path.isfile(os.path.join(folder, stem + ".g"))):
                continue
            unit = os.path.relpath(folder, root)
            out_dir = os.path.join(tmp_root, group, os.path.basename(folder))
            try:
                climod._export(ref_gltf, out_dir, 0, anim=True, quiet=True)
            except Exception as exc:  # noqa: BLE001
                results.append((unit, "ERROR", f"{type(exc).__name__}: {exc}"))
                continue
            bad = []
            for current, _dirs, files in os.walk(out_dir):
                rel = os.path.relpath(current, out_dir)
                for fn in files:
                    mine = os.path.join(current, fn)
                    orig = os.path.join(folder, rel, fn)
                    if not os.path.isfile(orig):
                        bad.append((os.path.join(rel, fn), "no original"))
                        continue
                    d = _diff_bytes(open(mine, "rb").read(),
                                    open(orig, "rb").read())
                    if d:
                        name = os.path.relpath(mine, out_dir)
                        bad.append((name, "len-diff" if d == -1
                                    else f"{d}B"))
            if not bad:
                results.append((unit, "EXACT", ""))
            else:
                results.append((unit, "DIFF",
                                "; ".join(f"{n} {det}" for n, det
                                          in bad[:4])))
    return results


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp_root = "/tmp/d3revbench"
    import shutil
    shutil.rmtree(tmp_root, ignore_errors=True)
    try:
        results = run(root, tmp_root)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    from collections import Counter
    tally = Counter(st for _, st, _ in results)
    for unit, st, detail in results:
        mark = {"EXACT": "✔", "DIFF": "!", "ERROR": "✘"}.get(st, "?")
        print(f"{mark} {st:9s} {unit:42s} {detail}")
    print()
    print("== summary:", dict(tally), f"total {len(results)}")
    return 0 if not (tally.get("DIFF") or tally.get("ERROR")) else 1


if __name__ == "__main__":
    sys.exit(main())
