#!/usr/bin/env python3
"""Build a standalone `d3tool` executable with PyInstaller.

Run this on your own machine (Windows / Linux / macOS) with any Python 3.8+:

    python release/build_exe.py          # or: release\build_exe.bat on Windows

It installs PyInstaller automatically if missing and produces a single
self-contained binary at `release/d3tool-dist-exe/d3tool[.exe]` that needs no
Python on the target machine.

Note: this cannot run in the repo's CI sandbox (no shared libpython there);
use python.org's Python install on Windows, where libpython is bundled.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "d3tool"
DIST = os.path.join(ROOT, "release", "d3tool-dist-exe")

# PyInstaller entry stub — mirrors python -m d3tool.
STUB = '''\
import sys
from d3tool.cli import _run

if __name__ == "__main__":
    sys.exit(_run())
'''


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("PyInstaller not found -- installing it ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def build() -> str:
    _ensure_pyinstaller()

    stage = os.path.join(ROOT, "build_stage_exe")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    stub = os.path.join(stage, "d3tool_entry.py")
    with open(stub, "w", encoding="utf-8") as f:
        f.write(STUB)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",           # single file, unpacks to temp at startup
        "--console",           # CLI app: keep the terminal attached
        "--clean",
        "--name", NAME,
        "--paths", ROOT,       # let PyInstaller see the d3tool package
        "--distpath", DIST,
        "--workpath", os.path.join(stage, "work"),
        "--specpath", stage,
        stub,
    ]
    print(" ".join(cmd))
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(
            "\nPyInstaller failed (exit %s).  If the error above says\n"
            "  'Python library not found' / libpython, your Python install\n"
            "  lacks the shared library: use the python.org installer, or\n"
            "  fall back to the portable zipapp:  bash release/build.sh\n" % exc.returncode)
        raise SystemExit(exc.returncode) from exc
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    exe = os.path.join(DIST, NAME + (".exe" if os.name == "nt" else ""))
    if not os.path.exists(exe):
        raise SystemExit("build finished but %s was not produced" % exe)
    return exe


def main() -> int:
    exe = build()
    print("\nbuilt: %s  (%.1f MB)" % (exe, os.path.getsize(exe) / 1e6))
    # smoke test: the binary must answer --version on its own
    out = subprocess.check_output([exe, "--version"]).decode().strip()
    print("smoke test: %s --version -> %s" % (os.path.basename(exe), out))
    print("""
usage:
  {exe} --help
  {exe} analyze <unit-folder>
  {exe} export <unit>.gltf -o out
  {exe} export-gl <unit>.g -a <unit>.a -o out/unit.gltf
  {exe} bundle <unit-folder> -o out
  {exe} texture convert <unit>.t -o out/tex.dds
""".format(exe=exe))
    return 0


if __name__ == "__main__":
    sys.exit(main())
