# -*- coding: utf-8 -*-
"""Locate and import the :mod:`d3tool` package from inside Blender.

The add-on is a thin shell around ``d3tool`` — the pure-Python library that
owns the actual Disciples 3 (GM engine) format work, verified byte-for-byte
against the shipped dis3tool corpus.  Keeping the format code out of the
add-on means the Blender bridge can only ever be wrong about *scene
plumbing*, never about the bytes that go into the game.

Four places are searched, in order:

1. the path set in the add-on preferences (a folder that *contains* a
   ``d3tool`` package, or the package folder itself),
2. ``<add-on>/vendor/`` — where ``release/build_addon.py`` puts the packaged
   copy, so an installed .zip works with nothing else on the machine,
3. the repository checkout this file lives in (``../../d3tool``), which is
   how it runs straight from the git tree,
4. an already importable ``d3tool`` (``pip install d3tool``).
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Optional

_d3tool = None
_origin: str = ""


def _package_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def candidate_roots(pref_path: str = "") -> list:
    """Every folder that may contain the ``d3tool`` package, best first."""
    roots = []
    if pref_path:
        pref_path = os.path.expanduser(pref_path)
        # accept both "…/d3tool" and "…/repo" in the preference field
        if os.path.basename(pref_path.rstrip("/\\")) == "d3tool":
            pref_path = os.path.dirname(pref_path)
        roots.append(pref_path)
    here = _package_dir()
    roots.append(os.path.join(here, "vendor"))
    # repo checkout: blender/io_scene_d3unit -> blender -> <repo>
    roots.append(os.path.dirname(os.path.dirname(here)))
    return roots


def _import_from(root: str):
    """Import ``d3tool`` from ``root`` (which must contain the package)."""
    pkg = os.path.join(root, "d3tool")
    if not os.path.isfile(os.path.join(pkg, "__init__.py")):
        return None
    root = os.path.abspath(root)
    inserted = root not in sys.path
    if inserted:
        sys.path.insert(0, root)
    try:
        # A different copy may already be loaded: drop it so the path we
        # just added wins (editing d3tool and re-running the add-on should
        # pick the edit up instead of a stale module).
        for name in [n for n in sys.modules if n == "d3tool"
                     or n.startswith("d3tool.")]:
            mod = sys.modules.get(name)
            mod_file = getattr(mod, "__file__", "") or ""
            if not mod_file.startswith(os.path.join(root, "d3tool")):
                del sys.modules[name]
        return importlib.import_module("d3tool")
    except Exception:  # noqa: BLE001 - a broken copy must not kill Blender
        if inserted:
            try:
                sys.path.remove(root)
            except ValueError:
                pass
        return None


def load(pref_path: str = "", force: bool = False):
    """Return the ``d3tool`` module, or ``None`` when it cannot be found."""
    global _d3tool, _origin
    if _d3tool is not None and not force:
        return _d3tool
    for root in candidate_roots(pref_path):
        if not root or not os.path.isdir(root):
            continue
        mod = _import_from(root)
        if mod is not None:
            _d3tool = mod
            _origin = getattr(mod, "__file__", "") or root
            return mod
    try:
        _d3tool = importlib.import_module("d3tool")
        _origin = getattr(_d3tool, "__file__", "") or "installed"
        return _d3tool
    except Exception:  # noqa: BLE001
        _d3tool = None
        _origin = ""
        return None


def loaded_from() -> str:
    return _origin


def version_hint() -> str:
    """A short "where did the library come from" line for the UI/log."""
    if _d3tool is None:
        return "d3tool not loaded"
    ver = getattr(_d3tool, "__version__", "?")
    return f"d3tool {ver} ({_origin})"


def module(name: str):
    """Import ``d3tool.<name>`` from the copy :func:`load` selected.

    ``d3tool/__init__`` deliberately does not import the CLI (argparse and
    friends are not wanted in every consumer), so the add-on pulls the
    submodules it needs through here instead of ``import d3tool.cli``, which
    would resolve against whatever copy happens to be first on ``sys.path``.
    """
    if load() is None:
        raise RuntimeError("d3tool is not available")
    return importlib.import_module("d3tool." + name)


def require(pref_path: str = ""):
    """Load ``d3tool`` or raise a readable error for the operator report."""
    mod = load(pref_path)
    if mod is None:
        raise RuntimeError(
            "d3tool was not found.  Point 'd3tool path' in the add-on "
            "preferences at the folder containing the d3tool package (or "
            "install it with 'pip install d3tool').")
    return mod
