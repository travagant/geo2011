"""Command line interface for the d3tool reverse-engineering toolkit."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from . import ac as acmod
from . import anim as animmod
from . import gfile
from . import gltf as gltfmod
from . import gltf_out as gltfout
from . import scene as scenemod


def _analyze_unit(path: str) -> None:
    """Print a human-readable analysis of one unit folder."""
    print(f"== {path} ==")
    for g in sorted(glob.glob(os.path.join(path, "*.g"))):
        try:
            data = open(g, "rb").read()
            mesh = gfile.parse_geometry_file(data)
            flag = " (compound/raw)" if mesh.raw else ""
            print(f"  .g  {os.path.basename(g)}: verts={mesh.vertex_count} "
                  f"tris={mesh.tri_count} bones={len(mesh.bones)} "
                  f"w={mesh.weights_on_vertex} diffuse={mesh.material_diffuse}{flag}")
        except Exception as exc:  # noqa: BLE001
            print(f"  .g  {os.path.basename(g)}: parse error {exc}")
    for ac in sorted(glob.glob(os.path.join(path, "*.ac"))):
        try:
            cfg = acmod.parse_ac(open(ac, "r", encoding="utf-8", errors="replace").read())
            print(f"  .ac {os.path.basename(ac)}: states="
                  + ", ".join(f"{s.name}({s.frame0}-{s.frame1})" for s in cfg.states))
        except Exception as exc:  # noqa: BLE001
            print(f"  .ac {os.path.basename(ac)}: parse error {exc}")
    for a in sorted(glob.glob(os.path.join(path, "*.a"))):
        try:
            an = animmod.parse_anim(open(a, "rb").read())
            print(f"  .a  {os.path.basename(a)}: bones={len(an.bones)} "
                  f"frames={an.frame_count} "
                  f"first={an.bones[0].name if an.bones else '-'}")
        except Exception as exc:  # noqa: BLE001
            print(f"  .a  {os.path.basename(a)}: parse error {exc}")
    for gltf in sorted(glob.glob(os.path.join(path, "*.gltf"))):
        try:
            m = gltfmod.load_gltf(gltf)
            print(f"  gltf {os.path.basename(gltf)}: verts={m.vertex_count} "
                  f"tris={m.tri_count} bones={len(m.bones)} frames={len(m.frames)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  gltf {os.path.basename(gltf)}: parse error {exc}")


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


def _export(gltf_path: str, out_dir: str, weights_on_vertex: int, anim: bool = True) -> None:
    """Reverse export: glTF -> GM geometry (.g), scene, animation config and .a."""
    m = gltfmod.load_gltf(gltf_path, weights_on_vertex=weights_on_vertex)
    sm = gltfmod.mesh_to_skinned(m, weights_on_vertex=weights_on_vertex)
    base = os.path.basename(gltf_path)[: -len(".gltf")]
    os.makedirs(out_dir, exist_ok=True)

    # figure out the resource root (folder path with backslashes)
    rel = gltf_path.replace("\\", "/")
    parts = rel.split("/")
    if "Neutrals" in parts:
        res_parts = ["resources", "characters", "neutrals"] + [parts[-2]]
        res = "\\".join(res_parts)
        res = res.replace("\\\\", "\\")
    else:
        res = "resources\\characters\\" + base

    attrs = _default_attrs(sm, base, res)
    # geometry file
    sm.geometry_file = base
    g_bytes = gfile.write_geometry_file(sm, attrs)
    out_g = os.path.join(out_dir, base + ".g")
    with open(out_g, "wb") as fh:
        fh.write(g_bytes)

    # scene
    scene_text = scenemod.write_scene(sm.name, base, res, attrs)
    with open(os.path.join(out_dir, base + ".scene"), "w", encoding="utf-8") as fh:
        fh.write(scene_text)

    # ac
    anim_files = acmod.detect_anim_files(os.path.dirname(gltf_path), base)
    cfg = acmod.default_ac(f'{res}\\{base}.g', f'{res}\\{base}', anim_files)
    with open(os.path.join(out_dir, base + ".ac"), "w", encoding="utf-8") as fh:
        fh.write(acmod.write_ac(cfg))

    # .a animation binary rebuilt from the glTF animation channels
    out_a = None
    if anim:
        try:
            animfile = gltfmod.animation_from_gltf(m)
            if animfile.bones:
                a_bytes = animmod.write_anim(animfile)
                out_a = os.path.join(out_dir, base + "_iadd.a")
                with open(out_a, "wb") as fh:
                    fh.write(a_bytes)
                print(f"wrote {out_a} ({len(a_bytes)} bytes, {len(animfile.bones)} bones)")
        except Exception as exc:  # noqa: BLE001
            print(f"  .a rebuild skipped: {exc}")

    print(f"wrote {out_g} ({len(g_bytes)} bytes)")
    print(f"wrote {os.path.join(out_dir, base + '.scene')}")
    print(f"wrote {os.path.join(out_dir, base + '.ac')}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="d3tool",
        description="Disciples 3 GM geometry / dis3tool glTF reverse tooling",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_analyze = sub.add_parser("analyze", help="analyze a unit folder")
    p_analyze.add_argument("path")

    p_export = sub.add_parser("export", help="glTF -> GM (.g/.scene/.ac/.a)")
    p_export.add_argument("gltf")
    p_export.add_argument("-o", "--out", default=".")
    p_export.add_argument("--weights-on-vertex", type=int, default=2)
    p_export.add_argument("--no-anim", action="store_true",
                          help="do not rebuild the .a animation file")

    p_g2gl = sub.add_parser("export-gl", help="GM .g/.a -> glTF (.gltf/.bin)")
    p_g2gl.add_argument("g")
    p_g2gl.add_argument("-a", "--anim", default=None,
                        help="optional .a animation file")
    p_g2gl.add_argument("-o", "--out", default=None,
                        help="output glTF path (default <base>.gltf)")
    p_g2gl.add_argument("-t", "--texture", default=None)

    p_validate = sub.add_parser("validate", help="structural glTF self-check")
    p_validate.add_argument("gltf")

    p_import = sub.add_parser("import", help="GM .g -> glTF (debug)")
    p_import.add_argument("gfile")
    p_import.add_argument("-o", "--out", default=None)

    args = parser.parse_args(argv)
    if args.cmd == "analyze":
        _analyze_unit(args.path)
    elif args.cmd == "export":
        _export(args.gltf, args.out, args.weights_on_vertex,
                anim=not args.no_anim)
    elif args.cmd == "export-gl":
        data = open(args.g, "rb").read()
        mesh = gfile.parse_geometry_file(data)
        anim = None
        if args.anim:
            anim = animmod.parse_anim(open(args.anim, "rb").read())
        out = args.out or (args.g[: -len(".g")] if args.g.endswith(".g")
                           else args.g + ".gltf")
        gt, bt = gltfout.write_gltf_to(out, mesh, anim, texture=args.texture)
        print(f"wrote {gt}")
        print(f"wrote {bt}")
    elif args.cmd == "validate":
        errors, warnings, infos = gltfout.validate_gltf(args.gltf)
        print(json.dumps({
            "errors": errors, "warnings": warnings, "info": infos,
        }, indent=2))
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
