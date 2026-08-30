"""Command line interface for the d3tool reverse-engineering toolkit.

Provides a friendly, colour-aware interface on top of the format readers and
writers:

* ``analyze <folder>``  — inspect a unit folder (.g/.a/.ac/.scene/.gltf).
* ``export <gltf>``     — reverse-export a dis3tool glTF back to the original
  Disciples 3 files (.g / .scene / .ac / .a).
* ``export-gl <g>``     — forward-export `.g`/(`.a`) into a viewable glTF.
* ``export-all <folder>`` — recursively export every `.g` into glTF.
* ``bundle <folder>``   — run the full pipeline over a unit folder (both ways).
* ``validate <gltf>``   — structural self-check of a glTF.
* ``import <g>``        — dump the parsed `.g` as JSON.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import struct
import sys
import textwrap
import traceback

from . import __version__
from . import ac as acmod
from . import anim as animmod
from . import gfile
from . import gltf as gltfmod
from . import gltf_out as gltfout
from . import scene as scenemod
from . import texture as texmod
from . import ui as ui


# --------------------------------------------------------------------------- #
#  analyze
# --------------------------------------------------------------------------- #
def _analyze_unit(path: str) -> int:
    """Print a human-readable analysis of one unit folder.

    Returns 0 on success, 1 when nothing usable was found or a file failed to
    parse (so the exit code can be used by scripts).
    """
    ui.banner()
    ui.section(f"Analysis  {path}")

    g_files = sorted(glob.glob(os.path.join(path, "*.g")))
    ac_files = sorted(glob.glob(os.path.join(path, "*.ac")))
    a_files = sorted(glob.glob(os.path.join(path, "*.a")))
    gltf_files = sorted(glob.glob(os.path.join(path, "*.gltf")))
    if not (g_files or ac_files or a_files or gltf_files):
        ui.fail(f"no Disciples 3 assets found in {path}")
        return 1
    had_error = 0

    g_rows, a_rows, ac_rows, gltf_rows = [], [], [], []

    for g in g_files:
        try:
            data = open(g, "rb").read()
            mesh = gfile.parse_geometry_file(data)
            flag = " (compound)" if mesh.raw else ""
            g_rows.append((os.path.basename(g),
                           str(mesh.vertex_count),
                           str(mesh.tri_count),
                           str(len(mesh.bones)),
                           f"w={mesh.weights_on_vertex}",
                           mesh.material_diffuse + flag))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(g)}: parse error {exc}")
            had_error = 1

    for a in a_files:
        try:
            an = animmod.parse_anim(open(a, "rb").read())
            a_rows.append((os.path.basename(a),
                           str(len(an.bones)),
                           str(an.frame_count),
                           an.bones[0].name if an.bones else "-"))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(a)}: parse error {exc}")
            had_error = 1

    for ac in ac_files:
        try:
            cfg = acmod.parse_ac(open(ac, "r", encoding="utf-8",
                                      errors="replace").read())
            for st in cfg.states:
                ac_rows.append((os.path.basename(ac), st.name,
                                f"{st.frame0}–{st.frame1}", st.file))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(ac)}: parse error {exc}")
            had_error = 1

    for gltf in gltf_files:
        try:
            m = gltfmod.load_gltf(gltf)
            gltf_rows.append((os.path.basename(gltf),
                              str(m.vertex_count),
                              str(m.tri_count),
                              str(len(m.bones)),
                              str(len(m.frames))))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(gltf)}: parse error {exc}")
            had_error = 1

    if g_rows:
        ui.table(g_rows, headers=[".g geometry", "verts", "tris", "bones",
                                  "slots", "diffuse"])
    if a_rows:
        ui.table(a_rows, headers=[".a animation", "bones", "frames", "root"])
    if ac_rows:
        ui.table(ac_rows, headers=[".ac state", "state", "range", "file"])
    if gltf_rows:
        ui.table(gltf_rows, headers=[".gltf export", "verts", "tris", "bones",
                                     "frames"])
    return had_error


# --------------------------------------------------------------------------- #
#  reverse export: glTF -> original D3
# --------------------------------------------------------------------------- #
def _default_attrs(mesh, geometry_base: str, res: str) -> dict:
    return {
        "dwNode": "375048704",
        "dwParent": "55867360",
        "name": mesh.name,
        "groupname": "Scene Root",
        "materials_num": "1",
        "material0_diffuse": f"{geometry_base}.tga",
        "material0_triangles_num": str(mesh.tri_count),
        "new_vertex_weights_format": "1",
        "vertexs_weights_num": str(mesh.vertex_count),
        "weights_on_vertex": str(mesh.weights_on_vertex),
        "bones_num": str(len(mesh.bones)),
    }


def _resource_root(gltf_path: str, base: str) -> str:
    """Derive the game resource directory (backslash path) for a glTF.

    The engine resolves character assets via a lowercase resource directory
    (e.g. ``resources\\characters\\neutrals\\airelemental``).  That name does
    *not* always match the on-disk unit folder (``Wildboar`` -> ``werewolf``),
    so we prefer the directory referenced by the sibling ``.scene`` across the
    actual geometry files (``<base>.g`` / ``<base>.ac``); only then fall back
    to a lowercase guess.
    """
    import re
    gltf_dir = os.path.dirname(os.path.abspath(gltf_path))
    for ext in (".scene", ".ac"):
        p = os.path.join(gltf_dir, base + ext)
        if not os.path.exists(p):
            continue
        try:
            text = open(p, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        # Prefer the resource path that references the actual geometry, not
        # e.g. an Alias/particle resource living elsewhere.
        best = None
        for m in re.finditer(r'resources[^"]*', text, re.IGNORECASE):
            cand = m.group(0).strip()
            if not cand.lower().endswith(('.g', '.ac', '.a')):
                continue
            if base.lower() in cand.lower():
                best = cand
                break
        if best is None:
            for m in re.finditer(r'resources[^"]*', text, re.IGNORECASE):
                cand = m.group(0).strip()
                if cand.lower().endswith(('.g', '.ac', '.a')):
                    best = cand
                    break
        if best:
            d = best.rsplit('\\', 1)[0].replace('/', '\\')
            if 'characters' in d.lower():
                return d
    rel = gltf_path.replace("\\", "/")
    parts = rel.split("/")
    if "Neutrals" in parts and len(parts) >= 2:
        res = "\\".join(["resources", "characters", "neutrals", parts[-2].lower()])
    else:
        res = f"resources\\characters\\{base.lower()}"
    return res.replace("/", "\\")

def _export_textures(gltf_path: str, out_dir: str, base: str,
                     quiet: bool) -> Optional[str]:
    """Emit the GM ``.t`` textures referenced by a glTF's images.

    dis3tool's glTF references its diffuse texture as a ``.dds``.  The GM
    engine instead wants the native ``.t`` container, so if the referenced
    ``.dds`` exists on disk (next to the glTF) we convert it to a ``.t`` and
    write it into the output directory.

    Returns the basename of the first emitted texture (the diffuse), or
    ``None`` if none was written, so the caller can point ``material0_diffuse``
    at the actual file.
    """
    try:
        with open(gltf_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    gltf_dir = os.path.dirname(os.path.abspath(gltf_path))
    images = doc.get("images") or []
    first = None
    for img in images:
        uri = img.get("uri")
        if not uri or uri.startswith("data:"):
            continue
        src = os.path.join(gltf_dir, os.path.basename(uri))
        if not os.path.exists(src) or not src.lower().endswith(".dds"):
            continue
        stem = os.path.splitext(os.path.basename(src))[0]
        out_t = os.path.join(out_dir, stem + ".t")
        with open(src, "rb") as fh:
            data = fh.read()
        # If the source asset ships a native .t next to the .dds, reuse its
        # header so the emitted .t round-trips byte-identically (the @24/@52
        # flags are not stored in a .dds and cannot be reconstructed).
        src_t = os.path.join(gltf_dir, stem + ".t")
        orig_header = None
        if os.path.exists(src_t):
            with open(src_t, "rb") as fh:
                orig_header = fh.read()[:59]
        with open(out_t, "wb") as fh:
            fh.write(texmod.dds_to_t(data, orig_header, os.path.basename(src)))
        if not quiet:
            ui.wrote(out_t, "converted from .dds")
        if first is None:
            first = os.path.basename(out_t)
    return first


def _export(gltf_path: str, out_dir: str, weights_on_vertex: int,
            anim: bool = True, quiet: bool = False) -> None:
    """Reverse export: glTF -> GM geometry (.g), scene, animation config, .a."""
    if not quiet:
        ui.section("Reverse export  glTF → Disciples 3")

    m = gltfmod.load_gltf(gltf_path, weights_on_vertex=weights_on_vertex)
    sm = gltfmod.mesh_to_skinned(m, weights_on_vertex=weights_on_vertex)
    base = os.path.basename(gltf_path)[: -len(".gltf")]
    os.makedirs(out_dir, exist_ok=True)
    res = _resource_root(gltf_path, base)
    attrs = _default_attrs(sm, base, res)
    sm.geometry_file = base

    # textures (glTF .dds -> native .t), and point the .g at the emitted file
    emitted = _export_textures(gltf_path, out_dir, base, quiet)
    if emitted:
        attrs["material0_diffuse"] = emitted
    else:
        # No texture was emitted; fall back to the unit base if a .t/.dds
        # happened to be written next to the output (or keep the .tga name).
        for cand in (base + ".t", base + ".dds"):
            if os.path.exists(os.path.join(out_dir, cand)):
                attrs["material0_diffuse"] = cand
                break

    # geometry
    g_bytes = gfile.write_geometry_file(sm, attrs)
    out_g = os.path.join(out_dir, base + ".g")
    with open(out_g, "wb") as fh:
        fh.write(g_bytes)
    if not quiet:
        ui.wrote(out_g, f"{len(g_bytes)} bytes")

    # scene (reuse the original if present to keep particle emitters)
    out_scene = os.path.join(out_dir, base + ".scene")
    src_scene = os.path.join(os.path.dirname(gltf_path), base + ".scene")
    if os.path.exists(src_scene):
        with open(src_scene, "r", encoding="utf-8") as fh:
            scene_text = fh.read()
        with open(out_scene, "w", encoding="utf-8") as fh:
            fh.write(scene_text)
        if not quiet:
            ui.wrote(out_scene, "reused (particle emitters preserved)")
    else:
        scene_text = scenemod.write_scene(sm.name, base, res, attrs,
                                          gobj_name=sm.name)
        with open(out_scene, "w", encoding="utf-8") as fh:
            fh.write(scene_text)
        if not quiet:
            ui.wrote(out_scene, "generated (no particles)")

    # animation config
    anim_files = acmod.detect_anim_files(os.path.dirname(gltf_path), base)
    cfg = acmod.default_ac(f'{res}\\{base}.g', f'{res}\\{base}', anim_files)
    with open(os.path.join(out_dir, base + ".ac"), "w", encoding="utf-8") as fh:
        fh.write(acmod.write_ac(cfg))
    if not quiet:
        ui.wrote(os.path.join(out_dir, base + ".ac"))

    # animation binary
    if anim:
        try:
            animfile = gltfmod.animation_from_gltf(m)
            if animfile.bones:
                a_bytes = animmod.write_anim(animfile)
                # Name the rebuilt .a exactly as the .ac references it (the
                # Idle/combined file from detect_anim_files), so the engine can
                # resolve the path.  Typically <base>_iadd.a but some assets
                # use <base>.a or <base>_baseanims.a.
                idle_name = anim_files.get("Idle") or (base + "_iadd.a")
                out_a = os.path.join(out_dir, os.path.basename(idle_name))
                with open(out_a, "wb") as fh:
                    fh.write(a_bytes)
                if not quiet:
                    ui.wrote(out_a, f"{len(a_bytes)} bytes, "
                                    f"{len(animfile.bones)} bones")
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                ui.skipped(base + " <anim>", str(exc))
    if not quiet:
        print("")


# --------------------------------------------------------------------------- #
#  forward export: original D3 -> glTF
# --------------------------------------------------------------------------- #
def _resolve_texture(g_path: str, attrs, texture: Optional[str],
                     out_dir: str, quiet: bool) -> Optional[str]:
    """Resolve a texture to hand to the forward-export glTF writer.

    Returns the image *uri* (a bare filename) to reference in the glTF, or
    ``None`` if no texture could be used.  If the source texture is a ``.t``
    container it is converted to a ``.dds`` next to the glTF (matching what
    dis3tool references); the URI is then the ``.dds`` basename so the file
    resolves relative to the output glTF.
    """
    src = None
    if texture:
        # explicit -t may be a path, or just a bare name to copy alongside
        cand = os.path.join(os.path.dirname(g_path), texture) \
            if not os.path.isabs(texture) else texture
        if os.path.exists(cand):
            src = cand
        elif os.path.exists(texture):
            src = texture
        else:
            # treat as a filename to reference as-is (no file yet)
            return texture
    else:
        src = texmod.find_diffuse_texture(g_path, attrs)

    if not src:
        return None

    # Emit the texture as a .dds (converting a .t source) so it sits next to
    # the glTF output and the URI resolves.
    base_src = os.path.splitext(os.path.basename(src))[0]
    out_dds = os.path.join(out_dir, base_src + ".dds")
    if src.lower().endswith(".t"):
        with open(src, "rb") as fh:
            data = fh.read()
        with open(out_dds, "wb") as fh:
            fh.write(texmod.t_to_dds(data, os.path.basename(src)))
        if not quiet:
            ui.wrote(out_dds, "converted from .t")
    elif src.lower().endswith(".dds"):
        # copy the .dds so the glTF can be rooted in any output directory
        import shutil
        with open(src, "rb") as fh:
            data = fh.read()
        with open(out_dds, "wb") as fh:
            fh.write(data)
        if not quiet:
            ui.wrote(out_dds, "texture")
    else:
        # .tga or other: reference the basename but do not mangle it
        return os.path.basename(src)

    return os.path.basename(out_dds)


def _export_gl(g_path: str, anim_path: Optional[str], out: Optional[str],
               texture: Optional[str], quiet: bool = False) -> None:
    if not quiet:
        ui.section("Forward export  Disciples 3 → glTF")
    data = open(g_path, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    anim = None
    if anim_path:
        anim = animmod.parse_anim(open(anim_path, "rb").read())
    if not out:
        out = (g_path[: -len(".g")] if g_path.endswith(".g") else g_path + ".gltf")
    attrs, _ = gfile.parse_attributes(data)
    out_dir = os.path.dirname(os.path.abspath(out))
    os.makedirs(out_dir, exist_ok=True)
    uri = _resolve_texture(g_path, attrs, texture, out_dir, quiet)
    gt, bt = gltfout.write_gltf_to(out, mesh, anim, texture=uri)
    if not quiet:
        ui.wrote(gt)
        ui.wrote(bt)
    return gt, bt


# --------------------------------------------------------------------------- #
#  recursive forward export
# --------------------------------------------------------------------------- #
def _find_animation_for_geometry(g_path: str) -> Optional[str]:
    """Find the most likely animation for a geometry file.

    Prefer the animation referenced by the sibling animation config.  This is
    important for names such as ``*_iadd.a`` and ``*_baseanims.a``.  LOD files
    normally share the main model's config.  If no config gives an answer, use
    conventional names, then a sole animation in the directory.
    """
    folder = os.path.dirname(os.path.abspath(g_path))
    stem = os.path.splitext(os.path.basename(g_path))[0]
    main_stem = stem[:-4] if stem.lower().endswith("_lod") else stem

    for ac_stem in dict.fromkeys((stem, main_stem)):
        ac_path = os.path.join(folder, ac_stem + ".ac")
        if not os.path.isfile(ac_path):
            continue
        try:
            cfg = acmod.parse_ac(open(ac_path, "r", encoding="utf-8-sig",
                                      errors="replace").read())
            for state in cfg.states:
                if state.file:
                    candidate = os.path.join(
                        folder, state.file.replace("\\", "/").rsplit("/", 1)[-1])
                    if os.path.isfile(candidate):
                        return candidate
        except OSError:
            pass

    for base in dict.fromkeys((stem, main_stem)):
        for suffix in (".a", "_iadd.a", "_baseanims.a"):
            candidate = os.path.join(folder, base + suffix)
            if os.path.isfile(candidate):
                return candidate

    animations = sorted(glob.glob(os.path.join(folder, "*.a")))
    return animations[0] if len(animations) == 1 else None


def _export_all(folder: str, out_dir: str, use_anim: bool = True) -> int:
    """Recursively convert every `.g` below *folder* to glTF."""
    root = os.path.abspath(folder)
    if not os.path.isdir(root):
        ui.section("Recursive export  Disciples 3 → glTF")
        ui.fail(f"no such folder: {folder}")
        return 1

    g_files = []
    for current, dirs, files in os.walk(root):
        # Do not accidentally consume a previous export when output is inside
        # the source tree.
        dirs[:] = [d for d in dirs
                   if os.path.abspath(os.path.join(current, d)) !=
                   os.path.abspath(out_dir)]
        g_files.extend(os.path.join(current, f) for f in files
                       if f.lower().endswith(".g"))
    g_files.sort()

    ui.section("Recursive export  Disciples 3 → glTF")
    if not g_files:
        ui.fail(f"no .g files found in {folder}")
        return 1

    succeeded = failed = 0
    for g_path in g_files:
        relative = os.path.relpath(g_path, root)
        target = os.path.join(out_dir, os.path.splitext(relative)[0] + ".gltf")
        animation = _find_animation_for_geometry(g_path) if use_anim else None
        try:
            _export_gl(g_path, animation, target, texture=None, quiet=True)
            detail = os.path.relpath(target, os.getcwd())
            if animation:
                detail += f"  + {os.path.basename(animation)}"
            ui.ok(detail)
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{relative}: {exc}")
            failed += 1

    print("")
    ui.info(f"exported {succeeded}/{len(g_files)} models to {out_dir}")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
#  bundle: full pipeline over a unit folder
# --------------------------------------------------------------------------- #
def _bundle(folder: str, out_dir: Optional[str], weights_on_vertex: int) -> None:
    gltf_files = sorted(glob.glob(os.path.join(folder, "*.gltf")))
    if not gltf_files:
        ui.fail(f"no .gltf found in {folder}")
        return

    ui.section(f"Bundle {folder}")
    for gltf in gltf_files:
        ui.info(f"processing {os.path.basename(gltf)}")
        d = os.path.join(out_dir, os.path.splitext(os.path.basename(gltf))[0])
        os.makedirs(d, exist_ok=True)
        _export(gltf, d, weights_on_vertex, quiet=True)
        # forward round-trip back
        base = os.path.splitext(os.path.basename(gltf))[0]
        g_path = os.path.join(d, base + ".g")
        a_path = os.path.join(d, base + "_iadd.a")
        fwd_dir = os.path.join(d, "gltf")
        if os.path.exists(g_path):
            try:
                _export_gl(g_path,
                           a_path if os.path.exists(a_path) else None,
                           os.path.join(fwd_dir, "roundtrip.gltf"),
                           texture=None, quiet=True)
                ui.ok(f"{base} → glTF round-trip wrote gltf/roundtrip.gltf")
            except Exception as exc:  # noqa: BLE001
                ui.fail(f"{base}: forward export failed — {exc}")
        else:
            ui.fail(f"{base}: .g was not produced")

    print("")
    ui.info(f"bundle written to {out_dir}")


# --------------------------------------------------------------------------- #
#  texture conversion
# --------------------------------------------------------------------------- #
def _texture_convert(src: str, dst: str, quiet: bool = False) -> None:
    """``d3tool texture convert <in> -o <out>`` — .t <-> .dds."""
    if not quiet:
        ui.section("Texture convert")
    info = texmod.convert_file(src, dst)
    if not quiet:
        ui.wrote(dst, f"{info.width}x{info.height} "
                      f"{'16-bit A1R5G5B5' if info.r5g5b5 else info.fourcc.decode()}"
                      f" mips={info.mip_count}")


def _texture_info(path: str) -> None:
    """``d3tool texture info <file>`` — describe a .t or .dds texture."""
    ui.section(f"Texture  {path}")
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] == b"DDS ":
        info = texmod.parse_dds(data, os.path.basename(path))
    else:
        info = texmod.parse_t(data, os.path.basename(path))
    fmt = "16-bit A1R5G5B5" if info.r5g5b5 else info.fourcc.decode()
    ui.table([
        ("format", fmt),
        ("width", str(info.width)),
        ("height", str(info.height)),
        ("mipmap levels", str(info.mip_count)),
        ("payload", f"{len(info.payload)} bytes"),
    ], headers=["field", "value"])


# --------------------------------------------------------------------------- #
#  argparse
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="d3tool",
        description="Disciples 3 / dis3tool reverse-engineering toolkit — "
                    "glTF & the original GM (.g/.a/.scene/.ac) formats.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              d3tool analyze Neutrals/AirElemental
              d3tool export Neutrals/AirElemental/character_neutrals_airelemental.gltf -o out
              d3tool export-gl Neutrals/AirElemental/character_neutrals_airelemental.g -a .../iadd.a -o out/unit.gltf
              d3tool export-all Neutrals -o gltf
              d3tool bundle Neutrals/AirElemental -o bundle
              d3tool validate out/unit.gltf
        """),
    )
    parser.add_argument("--version", action="version",
                        version=f"d3tool {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_analyze = sub.add_parser("analyze", help="inspect a unit folder")
    p_analyze.add_argument("path")

    p_export = sub.add_parser("export", help="glTF → GM (.g/.scene/.ac/.a)")
    p_export.add_argument("gltf")
    p_export.add_argument("-o", "--out", default=".")
    p_export.add_argument("--weights-on-vertex", type=int, default=0,
                          help="influence slots to write (2/3/4; 0=auto-detect)")
    p_export.add_argument("--no-anim", action="store_true",
                          help="do not rebuild the .a animation file")

    p_g2gl = sub.add_parser("export-gl", help="GM .g/.a → glTF (.gltf/.bin)")
    p_g2gl.add_argument("g")
    p_g2gl.add_argument("-a", "--anim", default=None,
                        help="optional .a animation file")
    p_g2gl.add_argument("-o", "--out", default=None,
                        help="output glTF path (default <base>.gltf)")
    p_g2gl.add_argument("-t", "--texture", default=None)

    p_all = sub.add_parser(
        "export-all", help="recursively convert every .g file to glTF")
    p_all.add_argument("folder", help="folder to scan recursively")
    p_all.add_argument("-o", "--out", default="gltf",
                       help="output folder (default: gltf)")
    p_all.add_argument("--no-anim", action="store_true",
                       help="export models without auto-detected .a animations")

    p_bundle = sub.add_parser(
        "bundle", help="run the full glTF↔GM pipeline over a unit folder")
    p_bundle.add_argument("folder")
    p_bundle.add_argument("-o", "--out", default="bundle")
    p_bundle.add_argument("--weights-on-vertex", type=int, default=0)

    p_validate = sub.add_parser("validate", help="structural glTF self-check")
    p_validate.add_argument("gltf")

    p_import = sub.add_parser("import", help="dump a parsed .g as JSON")
    p_import.add_argument("gfile")

    p_texture = sub.add_parser(
        "texture", help="inspect / convert .t (GM) <-> .dds textures")
    p_texture_sub = p_texture.add_subparsers(dest="tex_cmd", required=True)
    p_tc = p_texture_sub.add_parser(
        "convert", help="convert a .t to .dds (or .dds to .t)")
    p_tc.add_argument("src")
    p_tc.add_argument("-o", "--out", help="output path (decides direction)")
    p_ti = p_texture_sub.add_parser(
        "info", help="describe a .t / .dds texture")
    p_ti.add_argument("file")

    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "analyze":
        return _analyze_unit(args.path)
    elif args.cmd == "export":
        _export(args.gltf, args.out, args.weights_on_vertex,
                anim=not args.no_anim)
    elif args.cmd == "export-gl":
        _export_gl(args.g, args.anim, args.out, args.texture)
    elif args.cmd == "export-all":
        return _export_all(args.folder, args.out, use_anim=not args.no_anim)
    elif args.cmd == "bundle":
        _bundle(args.folder, args.out, args.weights_on_vertex)
    elif args.cmd == "validate":
        if not os.path.exists(args.gltf):
            ui.section("glTF structure check")
            ui.fail(f"no such file: {args.gltf}")
            return 1
        try:
            errors, warnings, infos = gltfout.validate_gltf(args.gltf)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            ui.section("glTF structure check")
            ui.fail(f"could not parse {args.gltf}: {exc}")
            return 1
        ui.section("glTF structure check")
        ui.table([("errors", str(errors)), ("warnings", str(warnings)),
                  ("accessors", str(infos))])
        return 1 if errors else 0
    elif args.cmd == "import":
        data = open(args.gfile, "rb").read()
        mesh = gfile.parse_geometry_file(data)
        print(json.dumps({
            "name": mesh.name,
            "geometry_file": mesh.geometry_file,
            "vertex_count": mesh.vertex_count,
            "tri_count": mesh.tri_count,
            "bones": [b.name for b in mesh.bones],
        }, indent=2))
    elif args.cmd == "texture":
        if args.tex_cmd == "convert":
            if not args.out:
                ui.fail("texture convert requires -o/--out")
                return 1
            _texture_convert(args.src, args.out)
        elif args.tex_cmd == "info":
            _texture_info(args.file)
    return 0


def _run(argv=None) -> int:
    """`main` with the argument dispatch wrapped so any user-facing error is
    reported cleanly rather than as a raw Python traceback."""
    try:
        return main(argv)
    except (ValueError, json.JSONDecodeError, OSError, struct.error) as exc:
        ui.section("error")
        ui.fail(str(exc))
        if os.environ.get("D3TOOL_DEBUG"):
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(_run())
