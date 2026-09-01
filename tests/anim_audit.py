#!/usr/bin/env python3
"""Animation-only audit over the whole corpus, both directions.

1. FORWARD (GM -> glTF): every bundled ``.g`` goes through the CLI's own
   import path (auto-detected animation).  Checks:
     * the export succeeds;
     * an animation is present exactly when the unit's authoritative
       ``.ac`` (the ``.g``'s own stem first, then the de-lodded main one)
       names resolvable ``.a`` streams — or the documented fallbacks
       (conventional names / a claimed sole ``.a``);
     * the exported animation covers *every* stream that ``.ac`` names
       (frame count == sum of the streams' frame counts);
     * no truncation warning fires.

2. REVERSE (glTF -> GM): every bundled reference glTF (all 98, including
   the 13 without a sibling ``.g`` that ``reverse_parity`` skips) goes
   through the CLI's own export path.  Checks:
     * the export succeeds;
     * an ``.a`` is written whenever the glTF carries an animation;
     * the rebuilt ``.a`` is byte-identical to a shipped original of the
       same name whenever one exists next to the glTF.

3. ANIM ROUND-TRIP: ref glTF -> export -> (.g/.a) -> import -> glTF,
   comparing only the animation (channel structure and the sampler
   accessor bytes) between the reference and the round-tripped file.
   The handful of dis3tool references that are themselves invalid glTF
   (Rod-1's out-of-range sampler output, WaterSnake's/Wildboar's
   dangling channel targets) are compared on their valid part only.
"""
from __future__ import annotations

import contextlib
import glob
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from d3tool import ac as acmod       # noqa: E402
from d3tool import anim as animmod   # noqa: E402
from d3tool import cli as climod     # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUPS = ("Empire", "Neutrals")


def _assets(ext):
    out = []
    for g in GROUPS:
        out.extend(sorted(glob.glob(os.path.join(REPO, g, "**", "*" + ext),
                                    recursive=True)))
    return out


def _capture(fn, *a, **kw):
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        try:
            fn(*a, **kw)
            exc = None
        except Exception as e:  # noqa: BLE001
            exc = e
    return exc, buf.getvalue() + err.getvalue()


def _authoritative_streams(folder, stem):
    """The streams the authoritative `.ac` for this `.g` names + that exist.

    Mirrors `_find_animation_for_geometry`: the `.g`'s own stem first (lod
    configs included), then the de-lodded main stem.
    """
    main_stem = stem[:-4] if stem.lower().endswith("_lod") else stem
    for st in dict.fromkeys((stem, main_stem)):
        p = os.path.join(folder, st + ".ac")
        if not os.path.isfile(p):
            continue
        cfg = acmod.parse_ac(open(p, "r", encoding="utf-8-sig",
                                  errors="replace").read())
        named = [(s.name, os.path.basename(
            s.file.replace("\\", "/").rsplit("/", 1)[-1]))
            for s in cfg.states if s.file]
        if not named:
            continue
        existing = {n for _s, n in named
                    if os.path.isfile(os.path.join(folder, n))}
        if existing:
            return existing
        # names streams, none shipped here (Blacknaga -> mermaid): rigid
        return set()
    return None  # no .ac at all


# --------------------------------------------------------------------------- #
def forward(tmp):
    print("=" * 78)
    print("1. FORWARD import sweep (every .g, auto-detected animation)")
    print("=" * 78)
    problems = []
    animated = rigid = 0
    used_a = {}
    for g in _assets(".g"):
        rel = os.path.relpath(g, REPO)
        folder = os.path.dirname(g)
        stem = os.path.splitext(os.path.basename(g))[0]
        anim_path = climod._find_animation_for_geometry(g)
        out = os.path.join(tmp, "fwd", rel + ".gltf")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        exc, log = _capture(climod._export_gl, g, anim_path, out, None,
                            quiet=True)
        if exc is not None and "node-helper" not in str(exc):
            problems.append((rel, "EXPORT FAIL", f"{type(exc).__name__}: {exc}"))
            continue
        if exc is not None:
            continue  # documented node-helper refusal
        j = json.load(open(out))
        anims = j.get("animations", [])
        expected = _authoritative_streams(folder, stem)
        total_frames = 0
        for f in expected or set():
            p = os.path.join(folder, f)
            total_frames += animmod.parse_anim(open(p, "rb").read()).frame_count
            used_a.setdefault(os.path.normpath(p), []).append(rel)
        if anim_path and anim_path not in (
                os.path.join(folder, f) for f in (expected or set())):
            used_a.setdefault(os.path.normpath(anim_path), []).append(rel)
        if anims:
            animated += 1
            accs = j["accessors"]
            frames = max(accs[s["input"]]["count"]
                         for a in anims for s in a["samplers"])
            if expected:  # None -> fallback territory, checked below
                if frames != total_frames:
                    problems.append((rel, "FRAMES",
                                     f"exported {frames} frames, .ac total "
                                     f"{total_frames} ({sorted(expected)})"))
            elif "covers" not in log:
                pass  # conventional/claimed fallback: any length is fine
        else:
            rigid += 1
            if expected:
                problems.append((rel, "MISSING ANIM",
                                 f".ac names {sorted(expected)} but glTF "
                                 f"has no animation"))
        if "covers" in log:
            problems.append((rel, "TRUNCWARN", log.strip().splitlines()[-1]))
    print(f"animated={animated} rigid={rigid} "
          f"(of {len(_assets('.g'))} .g; 2 node helpers refuse by design)")
    orphans = [a for a in _assets(".a")
               if os.path.normpath(a) not in used_a]
    print(f".a files consumed by some import: "
          f"{len(_assets('.a')) - len(orphans)}/{len(_assets('.a'))}")
    for o in orphans:
        print(f"  ORPHAN .a (never imported): {os.path.relpath(o, REPO)}")
    return problems


# --------------------------------------------------------------------------- #
def reverse(tmp):
    print("=" * 78)
    print("2. REVERSE export sweep (every reference glTF, all 98)")
    print("=" * 78)
    problems = []
    a_exact = a_noorig = 0
    for gt in _assets(".gltf"):
        rel = os.path.relpath(gt, REPO)
        folder = os.path.dirname(gt)
        stem = os.path.splitext(os.path.basename(gt))[0]
        out_dir = os.path.join(tmp, "rev", os.path.dirname(rel), stem)
        os.makedirs(out_dir, exist_ok=True)
        exc, _log = _capture(climod._export, gt, out_dir, 0, anim=True,
                             quiet=True)
        if exc is not None:
            problems.append((rel, "EXPORT FAIL", f"{type(exc).__name__}: {exc}"))
            continue
        j = json.load(open(gt))
        has_anim_gltf = bool(j.get("animations"))
        produced = sorted(glob.glob(os.path.join(out_dir, "**", "*.a"),
                                    recursive=True))
        if has_anim_gltf and not produced:
            problems.append((rel, "NO .a WRITTEN",
                             "glTF has animation but no .a produced"))
            continue
        if not has_anim_gltf and produced:
            problems.append((rel, "UNEXPECTED .a", "rigid glTF produced .a"))
            continue
        for a in produced:
            fn = os.path.basename(a)
            orig = os.path.join(folder, fn)
            if os.path.isfile(orig):
                if open(a, "rb").read() == open(orig, "rb").read():
                    a_exact += 1
                else:
                    problems.append((rel, ".a DIFF", fn))
            else:
                # no same-named original (leader sets): byte-equality against
                # any sibling .a the data verifies against is checked in the
                # round-trip step; here require write->parse->write stability
                a_noorig += 1
                data = open(a, "rb").read()
                if animmod.write_anim(animmod.parse_anim(data)) != data:
                    problems.append((rel, ".a UNSTABLE", fn))
    print(f".a byte-exact vs same-named original: {a_exact}, "
          f"without same-named original: {a_noorig}")
    return problems


# --------------------------------------------------------------------------- #
def _anim_blob(gltf_path):
    """Canonical description of a glTF's animation data (structure + bytes)."""
    j = json.load(open(gltf_path))
    binp = os.path.splitext(gltf_path)[0] + ".bin"
    data = open(binp, "rb").read() if os.path.isfile(binp) else b""
    bvs = j.get("bufferViews", [])

    def acc_bytes(i):
        if not isinstance(i, int) or not (0 <= i < len(j["accessors"])):
            return None  # invalid reference (Rod-1's stray sampler)
        a = j["accessors"][i]
        n = a["count"] * 4 * (3 if a["type"] == "vec3" else
                              4 if a["type"] == "vec4" else 1)
        if "bufferView" not in a:
            return None
        bv = bvs[a["bufferView"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        return data[off:off + n]

    anims = []
    for a in j.get("animations", []):
        chans = sorted((c["target"].get("node"), c["target"]["path"],
                        c["sampler"]) for c in a["channels"])
        samps = [(acc_bytes(s["input"]), acc_bytes(s["output"]))
                 for s in a["samplers"]]
        anims.append((chans, samps))
    return anims


def roundtrip(tmp):
    print("=" * 78)
    print("3. ANIMATION round-trip: ref glTF -> GM -> glTF (animation only)")
    print("=" * 78)
    problems = []
    exact = 0
    for gt in _assets(".gltf"):
        rel = os.path.relpath(gt, REPO)
        rev_dir = os.path.join(tmp, "rt", os.path.dirname(rel),
                               os.path.basename(gt))
        os.makedirs(rev_dir, exist_ok=True)
        exc, _ = _capture(climod._export, gt, rev_dir, 0, anim=True,
                          quiet=True)
        if exc is not None:
            problems.append((rel, "REVERSE FAIL", str(exc)))
            continue
        j = json.load(open(gt))
        if not j.get("animations"):
            continue  # rigid refs have nothing to round-trip
        gs = sorted(glob.glob(os.path.join(rev_dir, "*.g")))
        aas = sorted(glob.glob(os.path.join(rev_dir, "*.a")))
        if not aas:
            problems.append((rel, "NO .a", "cannot round-trip animation"))
            continue
        out = os.path.join(rev_dir, "rt.gltf")
        # the real CLI import path: auto-detected animation (the reused or
        # generated `.ac` in rev_dir names the streams), concatenating all
        # of them in `.ac` order like dis3tool did for the reference
        a_path = climod._find_animation_for_geometry(gs[0]) or aas[0]
        exc, _ = _capture(climod._export_gl, gs[0], a_path, out, None,
                          quiet=True)
        if exc is not None:
            problems.append((rel, "IMPORT FAIL", str(exc)))
            continue
        ref, got = _anim_blob(gt), _anim_blob(out)
        if ref == got:
            exact += 1
        else:
            det = []
            if len(ref) != len(got):
                det.append(f"anim count {len(ref)}!={len(got)}")
            else:
                for (rc, rs), (gc, gs2) in zip(ref, got):
                    if rc != gc:
                        det.append(f"channels {len(rc)}!={len(gc)}")
                        break
                    for i, ((ri, ro), (gi, go)) in enumerate(zip(rs, gs2)):
                        if ri != gi:
                            det.append(f"sampler{i} input")
                            break
                        if ro != go:
                            det.append(f"sampler{i} output")
                            break
                    break
            problems.append((rel, "ANIM DIFF", ",".join(det) or "data"))
    print(f"animation round-trip exact: {exact} "
          f"of {sum(1 for gt in _assets('.gltf') if json.load(open(gt)).get('animations'))}"
          " animated refs")
    return problems


def main():
    tmp = tempfile.mkdtemp(prefix="anim_audit_")
    try:
        allp = []
        for step in (forward, reverse, roundtrip):
            p = step(tmp)
            for x in p:
                print(f"  !! {x[0]:44s} {x[1]:14s} {x[2]}")
            allp += p
            print()
        print("=" * 78)
        print(f"TOTAL PROBLEMS: {len(allp)}")
        return 1 if allp else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
