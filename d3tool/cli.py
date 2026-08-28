"""Command line interface for the d3tool reverse-engineering toolkit.

Provides a friendly, colour-aware interface on top of the format readers and
writers:

* ``analyze <folder>``  — inspect a unit folder (.g/.a/.ac/.scene/.gltf).
* ``export <gltf>``     — reverse-export a dis3tool glTF back to the original
  Disciples 3 files (.g / .scene / .ac / .a).
* ``export-gl <g>``     — forward-export `.g`/(`.a`) into a viewable glTF.
* ``bundle <folder>``   — run the full pipeline over a unit folder (both ways).
* ``validate <gltf>``   — structural self-check of a glTF.
* ``import <g>``        — dump the parsed `.g` as JSON.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import textwrap

from . import __version__
from . import ac as acmod
from . import anim as animmod
from . import gfile
from . import gltf as gltfmod
from . import gltf_out as gltfout
from . import scene as scenemod
from . import ui as ui


# --------------------------------------------------------------------------- #
#  analyze
# --------------------------------------------------------------------------- #
def _analyze_unit(path: str) -> None:
    """Print a human-readable analysis of one unit folder."""
    ui.banner()
    ui.section(f"Analysis  {path}")

    g_files = sorted(glob.glob(os.path.join(path, "*.g")))
    ac_files = sorted(glob.glob(os.path.join(path, "*.ac")))
    a_files = sorted(glob.glob(os.path.join(path, "*.a")))
    gltf_files = sorted(glob.glob(os.path.join(path, "*.gltf")))
    if not (g_files or ac_files or a_files or gltf_files):
        ui.fail(f"no Disciples 3 assets found in {path}")
        return

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

    for a in a_files:
        try:
            an = animmod.parse_anim(open(a, "rb").read())
            a_rows.append((os.path.basename(a),
                           str(len(an.bones)),
                           str(an.frame_count),
                           an.bones[0].name if an.bones else "-"))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(a)}: parse error {exc}")

    for ac in ac_files:
        try:
            cfg = acmod.parse_ac(open(ac, "r", encoding="utf-8",
                                      errors="replace").read())
            for st in cfg.states:
                ac_rows.append((os.path.basename(ac), st.name,
                                f"{st.frame0}–{st.frame1}", st.file))
        except Exception as exc:  # noqa: BLE001
            ui.fail(f"{os.path.basename(ac)}: parse error {exc}")

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
    """Derive the game resource directory (backslash path) for a glTF."""
    rel = gltf_path.replace("\\", "/")
    parts = rel.split("/")
    if "Neutrals" in parts and len(parts) >= 2:
        res = "\\".join(["resources", "characters", "neutrals", parts[-2]])
    else:
        res = f"resources\\characters\\{base}"
    return res.replace("\\\\", "\\")


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
                out_a = os.path.join(out_dir, base + "_iadd.a")
                with open(out_a, "wb") as fh:
                    fh.write(a_bytes)
                if not quiet:
                    ui.wrote(out_a, f"{len(a_bytes)} bytes, "
                                    f"{len(animfile.bones)} bones")
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                ui.skipped(base + "_iadd.a", str(exc))
    if not quiet:
        print("")


# --------------------------------------------------------------------------- #
#  forward export: original D3 -> glTF
# --------------------------------------------------------------------------- #
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
    gt, bt = gltfout.write_gltf_to(out, mesh, anim, texture=texture)
    if not quiet:
        ui.wrote(gt)
        ui.wrote(bt)
    return gt, bt


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

    p_bundle = sub.add_parser(
        "bundle", help="run the full glTF↔GM pipeline over a unit folder")
    p_bundle.add_argument("folder")
    p_bundle.add_argument("-o", "--out", default="bundle")
    p_bundle.add_argument("--weights-on-vertex", type=int, default=0)

    p_validate = sub.add_parser("validate", help="structural glTF self-check")
    p_validate.add_argument("gltf")

    p_import = sub.add_parser("import", help="dump a parsed .g as JSON")
    p_import.add_argument("gfile")

    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "analyze":
        _analyze_unit(args.path)
    elif args.cmd == "export":
        _export(args.gltf, args.out, args.weights_on_vertex,
                anim=not args.no_anim)
    elif args.cmd == "export-gl":
        _export_gl(args.g, args.anim, args.out, args.texture)
    elif args.cmd == "bundle":
        _bundle(args.folder, args.out, args.weights_on_vertex)
    elif args.cmd == "validate":
        errors, warnings, infos = gltfout.validate_gltf(args.gltf)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
