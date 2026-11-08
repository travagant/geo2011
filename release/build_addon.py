#!/usr/bin/env python3
"""Build an installable Blender add-on zip for the Disciples III unit I/O.

    python3 release/build_addon.py

Writes ``release/d3unit-addon/io_scene_d3unit.zip`` (the add-on package plus
a vendored copy of ``d3tool``), and an unpacked folder next to it for
inspection.  Install the zip in Blender with *Edit > Preferences > Add-ons >
Install…* — no Python packages, no checkout, nothing else on the machine.

The add-on is a thin shell around ``d3tool``: the format code decides every
byte that reaches the game, so the zip always carries the library it was
built from (``<add-on>/vendor/d3tool``, the second place ``bridge.py``
looks).  A user who edits ``d3tool`` can still point the preference at a
checkout and that copy wins.
"""
from __future__ import annotations

import argparse
import ast
import os
import shutil
import sys
import tempfile
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_SRC = os.path.join(REPO, "blender", "io_scene_d3unit")
TOOL_SRC = os.path.join(REPO, "d3tool")
DIST = os.path.join(REPO, "release", "d3unit-addon")

#: Everything the add-on needs at run time.  `__pycache__` and any editor
#: droppings are filtered out below.
ADDON_FILES = (
    "__init__.py",
    "bridge.py",
    "exporter.py",
    "importer.py",
    "preferences.py",
    "unitprops.py",
    "weapon.py",
)

SKIP_DIRS = {"__pycache__", ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIX = (".pyc", ".pyo", ".swp", "~")


def addon_version() -> str:
    """Read ``bl_info["version"]`` without importing bpy."""
    path = os.path.join(ADDON_SRC, "__init__.py")
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if getattr(target, "id", "") == "bl_info":
                info = ast.literal_eval(node.value)
                return "%d.%d.%d" % tuple(info.get("version", (0, 0, 0)))
    return "0.0.0"


def copy_tool(dst: str) -> int:
    """Copy the d3tool package (sources only); returns the file count."""
    n = 0
    for cur, dirs, files in os.walk(TOOL_SRC):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(cur, TOOL_SRC)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for name in files:
            if name.endswith(SKIP_SUFFIX):
                continue
            shutil.copy2(os.path.join(cur, name), os.path.join(target, name))
            n += 1
    return n


def write_readme(path: str, version: str) -> None:
    open(path, "w", encoding="utf-8").write(
        "Disciples III Unit - Blender add-on %s\n"
        "=====================================\n\n"
        "Install: Edit > Preferences > Add-ons > Install... and pick this\n"
        "zip.  Enable 'Disciples III Unit (GM .g/.a/.scene/.ac/.t)'.\n\n"
        "Import: File > Import > Disciples III Unit, pick the unit's .g.\n"
        "Export: File > Export > Disciples III Unit, pick the .g to write.\n\n"
        "The zip vendors the d3tool library it was built from; the add-on\n"
        "preferences can point at another copy (Check d3tool reports which\n"
        "one is loaded).\n\n"
        "Source: https://github.com/travagant/geo2011\n" % version)


def build(out_dir: str = DIST, zip_only: bool = False) -> str:
    version = addon_version()
    os.makedirs(out_dir, exist_ok=True)
    stage_root = tempfile.mkdtemp(prefix="d3unit_addon_")
    stage = os.path.join(stage_root, "io_scene_d3unit")
    try:
        os.makedirs(stage)
        for name in ADDON_FILES:
            shutil.copy2(os.path.join(ADDON_SRC, name),
                         os.path.join(stage, name))
        n_tool = copy_tool(os.path.join(stage, "vendor", "d3tool"))
        write_readme(os.path.join(stage, "README.txt"), version)
        missing = [n for n in ADDON_FILES
                   if not os.path.isfile(os.path.join(stage, n))]
        if missing:
            raise SystemExit("add-on source missing: " + ", ".join(missing))

        zip_path = os.path.join(out_dir, "io_scene_d3unit.zip")
        if os.path.exists(zip_path):
            os.remove(zip_path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for cur, dirs, files in os.walk(stage):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for name in sorted(files):
                    if name.endswith(SKIP_SUFFIX):
                        continue
                    full = os.path.join(cur, name)
                    rel = os.path.relpath(full, os.path.dirname(stage))
                    zf.write(full, rel)
        if not zip_only:
            plain = os.path.join(out_dir, "io_scene_d3unit")
            shutil.rmtree(plain, ignore_errors=True)
            shutil.copytree(stage, plain)
        size = os.path.getsize(zip_path)
        print(f"add-on {version}: {zip_path}  ({size // 1024} KiB, "
              f"{len(ADDON_FILES)} modules + {n_tool} d3tool files)")
        return zip_path
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=DIST,
                    help=f"output folder (default {DIST})")
    ap.add_argument("--zip-only", action="store_true",
                    help="don't keep the unpacked copy next to the zip")
    args = ap.parse_args(argv)
    build(args.out, args.zip_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
