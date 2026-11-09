"""Blender side of the AstraForge -> Disciples III bridge (run by `astra_d3.py`).

Blender is started headless::

    blender -b --factory-startup --python bridge/build_d3.py -- \
        <glb> <out.g> <report.json> [--donor DIR] [--weights AUTO|2|3|4]
        [--no-anim] [--keep-temp] [--addon DIR] [--d3tool DIR]

It imports the GLB the studio exported, makes the scene the shape the add-on
expects (triangulated, transforms applied, one object per part), and calls the
same `export_unit` the "Export Disciples III Unit" button calls — so a
headless run and a hand export write the same bytes.

Everything is reported through a JSON file rather than stdout: Blender prints
a lot of its own, and the bridge reads `<report>.json` to know what happened.
"""

import json
import os
import sys
import traceback

import bpy  # noqa: F401  (only importable inside Blender)


def parse(argv):
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(args) < 3:
        raise SystemExit("usage: build_d3.py -- <glb> <out.g> <report.json> "
                         "[--donor DIR] [--weights AUTO] [--no-anim] "
                         "[--keep-temp] [--addon DIR] [--d3tool DIR]")
    opts = {"glb": args[0], "out": args[1], "report": args[2],
            "donor": "", "weights": "AUTO", "anim": True, "keep_temp": False,
            "addon": "", "d3tool": ""}
    rest = args[3:]
    index = 0
    while index < len(rest):
        flag = rest[index]
        if flag == "--no-anim":
            opts["anim"] = False
            index += 1
        elif flag == "--keep-temp":
            opts["keep_temp"] = True
            index += 1
        elif flag in ("--donor", "--weights", "--addon", "--d3tool"):
            if index + 1 >= len(rest):
                raise SystemExit(f"{flag} needs a value")
            opts[flag[2:]] = rest[index + 1]
            index += 2
        else:
            raise SystemExit(f"unknown option {flag}")
    return opts


def enable_addon(package_dir):
    """Make `io_scene_d3unit` importable and registered, from a folder or a zip."""
    import addon_utils

    name = "io_scene_d3unit"
    if package_dir:
        package_dir = os.path.abspath(package_dir)
        if package_dir.lower().endswith(".zip"):
            bpy.ops.preferences.addon_install(filepath=package_dir)
        else:
            parent = os.path.dirname(package_dir)
            if parent not in sys.path:
                sys.path.insert(0, parent)
            name = os.path.basename(package_dir)
    addon_utils.enable(name, default_set=True, persistent=True)
    return name


def set_d3tool(name, repo):
    """Point the add-on's preferences at the toolkit the export shells out to."""
    if not repo:
        return
    addon = bpy.context.preferences.addons.get(name)
    if addon is None:
        return
    addon.preferences.d3tool_path = os.path.abspath(repo)


def import_glb(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    # glTF is Y-up and comes with whatever scale the studio used; the add-on
    # reads world-space geometry, so bake the object matrices into the meshes
    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH":
            continue
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        if not obj.data.loop_triangles and any(len(p.vertices) > 3
                                              for p in obj.data.polygons):
            mod = obj.modifiers.new(name="d3bridge_tri", type="TRIANGULATE")
            bpy.ops.object.modifier_apply(modifier=mod.name)
        obj.select_set(False)


def mesh_stats():
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    bones = sum(len(o.data.bones) for o in bpy.context.scene.objects
                if o.type == "ARMATURE")
    return {
        "objects": len(meshes),
        "vertices": sum(len(o.data.vertices) for o in meshes),
        "polygons": sum(len(o.data.polygons) for o in meshes),
        "bones": bones,
        "materials": len({m.name for o in meshes for m in o.data.materials if m}),
        "images": [img.name for img in bpy.data.images if img.name != "Render Result"],
    }


def main():
    opts = parse(sys.argv)
    report = {"glb": opts["glb"], "out": opts["out"], "ok": False,
              "log": [], "warnings": [], "stats": {}, "error": ""}

    def note(text):
        report["log"].append(text)
        print(f"[d3bridge] {text}")

    try:
        note(f"blender {bpy.app.version_string}")
        name = enable_addon(opts["addon"])
        note(f"add-on {name}")
        set_d3tool(name, opts["d3tool"])
        import_glb(opts["glb"])
        report["stats"] = mesh_stats()
        note(f"imported: {report['stats']['objects']} mesh(es), "
             f"{report['stats']['vertices']} vertices, "
             f"{report['stats']['bones']} bone(s)")
        if not report["stats"]["vertices"]:
            raise RuntimeError("the GLB has no mesh data")

        from io_scene_d3unit import exporter  # noqa: PLC0415 - after enabling

        weights = 0 if str(opts["weights"]).upper() == "AUTO" else int(opts["weights"])
        result = exporter.export_unit(
            bpy.context,
            opts["out"],
            use_anim=bool(opts["anim"]),
            weights=weights,
            donor_dir=opts["donor"],
            quiet=True,
            keep_temp=bool(opts["keep_temp"]),
        )
        report["warnings"] = list(result.get("warnings") or [])
        report["stats"].update(result.get("stats") or {})
        report["files"] = list(result.get("files") or [])
        report["ok"] = os.path.isfile(opts["out"])
        note(f"wrote {result.get('base', os.path.basename(opts['out']))}.g")
    except Exception as exc:  # noqa: BLE001 - the report is the error channel
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        print(report["traceback"])

    os.makedirs(os.path.dirname(opts["report"]) or ".", exist_ok=True)
    with open(opts["report"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
