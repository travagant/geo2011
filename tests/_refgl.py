"""Test fixture: the dis3tool-reference glTF for the Angel unit.

The reference glTF (``d3tool import`` output) is a generated artifact and
is not committed, so every test that needs it regenerates it on the fly
into a process-wide temp dir on first use.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from d3tool import cli as climod  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "Empire", "Angel")

_CACHE = {}


def ensure_angel_ref() -> str:
    """Return a folder holding ``character_empire_angel.gltf`` + ``.bin``.

    Generates it once per process via the forward export (the exact bytes
    ``d3tool import`` produces for the unit, donors and all).
    """
    if "dir" in _CACHE:
        return _CACHE["dir"]
    tmp = tempfile.mkdtemp(prefix="d3tool_ref_")
    # the forward export writes <base>.gltf/.bin next to its inputs when
    # no output folder is given; point it at the temp dir instead
    g = os.path.join(SRC, "character_empire_angel.g")
    anim = os.path.join(SRC, "character_empire_angel_idle.a")
    climod._export_gl(g, anim, os.path.join(tmp, "character_empire_angel.gltf"), None, quiet=True)
    gt = os.path.join(tmp, "character_empire_angel.gltf")
    assert os.path.isfile(gt) and os.path.isfile(os.path.splitext(gt)[0] + ".bin"), \
        "reference glTF generation failed"
    _CACHE["dir"] = tmp
    return tmp


def copy_ref_into(work_dir: str, name: str = "character_empire_angel") -> str:
    """Copy the reference glTF + .bin into ``work_dir`` as ``name``."""
    src = os.path.join(ensure_angel_ref(), "character_empire_angel.gltf")
    dst = os.path.join(work_dir, name + ".gltf")
    shutil.copy(src, dst)
    shutil.copy(os.path.splitext(src)[0] + ".bin",
                os.path.splitext(dst)[0] + ".bin")
    return dst
