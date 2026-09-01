#!/usr/bin/env python3
"""Reproduce the Blender round-trip: angel -> (weights painted) -> Blender
glTF -> d3tool export -> game.  Checks the invariants the battle loader
needs, for several user-workflow scenarios.
"""
import contextlib
import copy
import glob
import io
import json
import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from d3tool import ac as acmod        # noqa: E402
from d3tool import cli as climod      # noqa: E402
from d3tool import gfile              # noqa: E402
from d3tool import scene as scenemod  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "Empire", "Angel")


def blenderize(src_gltf, out_gltf, paint=False, anim_frames=None):
    """Dis3tool reference glTF -> what Blender's exporter tends to write."""
    bin_name = os.path.basename(os.path.splitext(out_gltf)[0]) + ".bin"
    shutil.copy(os.path.splitext(src_gltf)[0] + ".bin",
                os.path.join(os.path.dirname(out_gltf), bin_name))
    j = json.load(open(src_gltf))
    data = bytearray(open(os.path.splitext(src_gltf)[0] + ".bin", "rb").read())
    j["asset"]["generator"] = "Khronos glTF Blender I/O 4.2"

    # -- extra Armature root node wrapping the skeleton root (Bip01) --
    arm_idx = len(j["nodes"])
    j["nodes"].append({"name": "Armature", "children": []})
    scene_nodes = j["scenes"][0]["nodes"]
    new_scene = []
    for n in scene_nodes:
        nd = j["nodes"][n]
        if nd.get("name") == "Bip01":
            j["nodes"][arm_idx]["children"].append(n)
            if "matrix" not in nd:
                # Blender keeps the rest transform on the bone itself
                pass
        else:
            new_scene.append(n)
    new_scene.append(arm_idx)
    j["scenes"][0]["nodes"] = new_scene

    # -- textures: Blender references a PNG it wrote, not the .dds --
    import struct as _st
    import zlib as _zl
    for img in j.get("images", []):
        uri = img.get("uri", "")
        if uri:
            img["uri"] = os.path.splitext(uri)[0] + ".png"
            png_path = os.path.join(os.path.dirname(out_gltf), img["uri"])
            if not os.path.exists(png_path):
                # a small synthetic RGBA PNG, like Blender would write
                w = h = 4
                raw = b"".join(b"\x00" + bytes(w * 4) for _y in range(h))
                def _ck(t, d):
                    c = _st.pack(">I", len(d)) + t + d
                    return c + _st.pack(">I", _zl.crc32(t + d) & 0xffffffff)
                png_path = png_path
                with open(png_path, "wb") as fh:
                    fh.write(b"\x89PNG\r\n\x1a\n"
                             + _ck(b"IHDR", _st.pack(">IIBBBBB", w, h, 8, 6,
                                                     0, 0, 0))
                             + _ck(b"IDAT", _zl.compress(raw))
                             + _ck(b"IEND", b""))

    # -- weights: Blender sorts the 4 lanes descending, renormalizes in f32;
    #    "paint" also re-weights 1 in 20 vertices (the user's edit) --
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
            n = wacc["count"]
            for v in range(n):
                ws = list(struct.unpack_from("<4f", data, woff + 16 * v))
                js = list(struct.unpack_from("<4B", data, joff + 4 * v))
                if paint and v % 20 == 0 and ws[1] > 0:
                    # move a third of lane0's weight onto lane1
                    d = ws[0] * 0.33
                    ws[0] -= d
                    ws[1] += d
                pairs = sorted(zip(ws, js), key=lambda p: -p[0])
                ws, js = zip(*pairs)
                s = sum(ws) or 1.0
                ws = tuple(struct.unpack("<4f",
                                         struct.pack("<4f", *(w / s for w in ws)))
                           ) if paint else tuple(ws)
                struct.pack_into("<4f", data, woff + 16 * v, *ws)
                struct.pack_into("<4B", data, joff + 4 * v, *js)

    # -- optionally shorten the animation (user trimmed the end) --
    if anim_frames is not None:
        an = j["animations"][0]
        inp = an["samplers"][0]["input"]
        cnt = j["accessors"][inp]["count"]
        keep = anim_frames
        # keep the first `keep` samples of every sampler
        for s in j["samplers"] if False else []:
            pass
        for s in an["samplers"]:
            for key in ("input", "output"):
                ai = s[key]
                acc = j["accessors"][ai]
                bv = j["bufferViews"][acc["bufferView"]]
                off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
                comp = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[acc["type"]]
                if key == "input":
                    acc["count"] = keep
                else:
                    acc["count"] = keep * (2 if False else 1)
        # (channels unchanged; output accessors of rotation/translation keep
        #  their original per-frame layout, just truncated conceptually)

    j["buffers"][0]["uri"] = bin_name
    json.dump(j, open(out_gltf, "w"))
    open(os.path.join(os.path.dirname(out_gltf), bin_name), "wb").write(bytes(data))


def battle_check(out_dir, label):
    """The invariants the engine needs when the unit walks into battle."""
    bad = []
    base = None
    gs = glob.glob(os.path.join(out_dir, "*.g"))
    acs = glob.glob(os.path.join(out_dir, "*.ac"))
    scs = glob.glob(os.path.join(out_dir, "*.scene"))
    if not gs:
        return [f"{label}: no .g produced"]
    base = os.path.splitext(os.path.basename(gs[0]))[0]

    # .g parses
    m = gfile.parse_geometry_file(open(gs[0], "rb").read())
    if getattr(m, "parse_error", None):
        bad.append(f"{label}: .g parse_error: {m.parse_error}")
    # .ac states: file exists + frame range within that file's frames
    for ac in acs:
        cfg = acmod.parse_ac(open(ac, encoding="utf-8-sig",
                                  errors="replace").read())
        for st in cfg.states:
            if not st.file:
                continue
            fn = os.path.basename(st.file.replace("\\", "/").rsplit("/", 1)[-1])
            p = os.path.join(out_dir, fn)
            if not os.path.isfile(p):
                bad.append(f"{label}: .ac state {st.name} -> {fn} MISSING")
                continue
            if fn.endswith(".a"):
                from d3tool import anim as animmod
                a = animmod.parse_anim(open(p, "rb").read())
                if st.frame1 > a.frame_count:
                    bad.append(f"{label}: {st.name} frames {st.frame0}.."
                               f"{st.frame1} exceed {fn} "
                               f"({a.frame_count} frames)")
            if st.meshfile:
                mf = os.path.basename(st.meshfile.replace("\\", "/")
                                      .rsplit("/", 1)[-1])
                if not os.path.isfile(os.path.join(out_dir, mf)):
                    bad.append(f"{label}: {st.name} meshfile {mf} MISSING")
    # .scene parses and its file refs exist
    for sc in scs:
        txt = open(sc, "rb").read().decode("latin-1")
        try:
            scenemod.parse_scene(txt)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{label}: .scene does not parse: {exc}")
        import re
        for mm in re.finditer(r'file\s+"([^"]+)"', txt):
            fn = os.path.basename(mm.group(1).replace("\\", "/"))
            if not os.path.isfile(os.path.join(out_dir, fn)):
                bad.append(f"{label}: .scene ref {fn} MISSING")
    # texture: material0_diffuse must resolve to a .t next to the .g
    attrs, _ = gfile.parse_attributes(open(gs[0], "rb").read())
    mat = attrs.get("material0_diffuse", "")
    want = os.path.splitext(mat)[0] + ".t"
    if not os.path.isfile(os.path.join(out_dir, want)):
        ts = [f for f in os.listdir(out_dir) if f.endswith(".t")]
        bad.append(f"{label}: material0_diffuse '{mat}' -> no {want} "
                   f"(folder has {ts[:3] or 'no .t at all'})")
    return bad


def run(label, make_src, with_donors, name="character_empire_angel_edit"):
    tmp = tempfile.mkdtemp(prefix="angel_")
    if with_donors:
        work = os.path.join(tmp, "Empire", "Angel")
        shutil.copytree(SRC, work)
    else:
        work = os.path.join(tmp, "bare")
        os.makedirs(work)
    gt = os.path.join(work, name + ".gltf")
    make_src(gt)
    out = os.path.join(tmp, "out")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            climod._export(gt, out, 0, anim=True, quiet=False)
        except Exception as exc:  # noqa: BLE001
            print(f"== {label}\n   EXPORT CRASHED: {type(exc).__name__}: {exc}")
            return
    bad = battle_check(out, label)
    print(f"== {label}")
    if bad:
        for b in bad[:8]:
            print("   !!", b)
    else:
        print("   all battle-load invariants OK")
    warn = [l for l in buf.getvalue().splitlines()
            if "!" in l or "warn" in l.lower() or "skip" in l.lower()]
    for w in warn[:6]:
        print("   log>", w.strip()[:130])
    shutil.rmtree(tmp, ignore_errors=True)
    return out


if __name__ == "__main__":
    REF = os.path.join(SRC, "character_empire_angel.gltf")

    def copy_ref(gt):
        shutil.copy(REF, gt)
        shutil.copy(os.path.splitext(REF)[0] + ".bin",
                    os.path.splitext(gt)[0] + ".bin")

    # A: user overwrote the unit's own glTF (same stem, donors around)
    run("A same-stem reference (sanity)", copy_ref, True,
        name="character_empire_angel")
    run("A1 Blender-style, same stem, in unit folder",
        lambda gt: blenderize(REF, gt), True, name="character_empire_angel")
    run("B Blender-style, same stem, bare folder (no donors)",
        lambda gt: blenderize(REF, gt), False, name="character_empire_angel")
    run("C Blender-style + painted weights, in unit folder",
        lambda gt: blenderize(REF, gt, paint=True), True,
        name="character_empire_angel")
    run("D Blender-style, animation trimmed to 250 frames",
        lambda gt: blenderize(REF, gt, anim_frames=250), True,
        name="character_empire_angel")
    run("E renamed stem (angel_edit.gltf) in unit folder",
        lambda gt: blenderize(REF, gt), True, name="angel_edit")
