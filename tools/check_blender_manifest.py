#!/usr/bin/env python3
"""Check a Blender extension zip (or a loose manifest) with Blender's own rules.

    python3 tools/check_blender_manifest.py release/d3unit-addon/d3unit-1.1.51.zip
    python3 tools/check_blender_manifest.py blender/io_scene_d3unit/blender_manifest.toml

Exit 0 = the manifest would install in Blender 4.2+, 1 = Blender would reject it.

Why this exists: `release/build_addon.py` builds the extension zip by hand
(Blender is not in CI), so nothing on our side proves the manifest is
acceptable.  Blender's validator lives in `scripts/addons_core/bl_pkg/cli/
blender_ext.py` and happens to be pure stdlib — no `bpy` import anywhere in it
— so it runs here unchanged.  A vendored copy sits in `tools/vendor/`; refresh
it from a Blender checkout with `--refresh`.

`strict=True` is not optional.  Blender's `build` and `validate` commands both
pass `strict=True` (blender_ext.py, `_validate_directory` and `build`), and
only the strict pass rejects a `tagline` that ends with punctuation or an
unknown key under `[permissions]`.  A lenient check here would wave through
manifests that Blender later refuses to build.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VENDOR = os.path.join(HERE, "vendor", "blender_ext.py")
UPSTREAM = ("https://raw.githubusercontent.com/blender/blender/main/"
            "scripts/addons_core/bl_pkg/cli/blender_ext.py")


def load_validator():
    """Import the vendored validator without touching the checkout's sys.path."""
    if not os.path.isfile(VENDOR):
        raise SystemExit(f"missing {VENDOR} — run with --refresh")
    sys.path.insert(0, os.path.dirname(VENDOR))
    import blender_ext  # noqa: PLC0415  (deliberately late, vendored module)
    return blender_ext


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?",
                    help="extension .zip, or a blender_manifest.toml")
    ap.add_argument("--refresh", action="store_true",
                    help="re-download the validator from the Blender repo")
    args = ap.parse_args(argv)

    if args.refresh:
        import urllib.request
        os.makedirs(os.path.dirname(VENDOR), exist_ok=True)
        with urllib.request.urlopen(UPSTREAM) as fh:
            data = fh.read()
        with open(VENDOR, "wb") as fh:
            fh.write(data)
        print(f"refreshed {VENDOR} ({len(data)} bytes)")
        if not args.path:
            return 0

    if not args.path:
        ap.error("path is required unless --refresh is used")

    path = args.path
    if not os.path.exists(path):
        raise SystemExit(f"no such file: {path}")

    be = load_validator()
    if path.lower().endswith(".zip"):
        result = be.pkg_manifest_from_archive_and_validate(path, strict=True)
    else:
        with open(path, "rb") as fh:
            tomllib_data = fh.read()
        import tomllib
        try:
            data = tomllib.loads(tomllib_data.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: TOML does not parse: {exc}")
            return 1
        # a loose manifest is validated by the same strict pass Blender's
        # `build` runs on the manifest before it packs anything
        result = be.pkg_manifest_from_dict_and_validate_all_errros(
            data, from_repo=False, strict=True)

    if isinstance(result, str):
        print("FAIL: Blender would reject this manifest")
        for line in result.splitlines():
            print("  " + line)
        return 1
    if isinstance(result, list):
        print("FAIL: Blender would reject this manifest")
        for line in result:
            print("  " + line)
        return 1

    m = result
    print(f"PASS: {path}")
    print(f"  id={m.id} version={m.version} type={m.type}")
    print(f"  tagline={m.tagline!r} ({len(m.tagline)} chars)")
    print(f"  license={m.license}")
    print(f"  blender_version_min={m.blender_version_min}")
    print(f"  tags={m.tags} permissions={m.permissions}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
