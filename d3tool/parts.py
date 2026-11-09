"""Compare the mesh parts an export wrote with the donor the game ships.

A `.g` says nothing about the scale or placement of the objects it came from:
the engine binds every part to a bone and draws it where the vertices are.  So
an object whose Blender transform was never applied — the classic
``Object > Apply > Scale`` a modeller forgets after scaling a borrowed weapon
— exports *exactly as it looks in the viewport*, happily, and only the
battlefield reveals that the sword is 1.9x too long or floats a hand's width
off the grip.

Nothing in the exported file looks broken, so :func:`donor_mismatches` catches
it by comparing every part against the donor part of the same name: size per
axis, where its bounding box sits, and how many bones it is bound to.  The
caller decides what to do with the findings; :func:`d3tool.cli._export` turns
them into warnings.
"""
from __future__ import annotations

import os

from typing import Dict, List, Optional, Sequence, Tuple

#: a part may differ this much from its donor part on any axis before the
#: export says so (a decimated copy, a re-split vertex, a rebuilt UV seam)
SIZE_TOL = 0.15

#: ... or its bounding box may sit this far away (in model units, the
#: Inquisitor is ~1.7 tall) before the export points at it
OFFSET_TOL = 0.25


def _points(part) -> List[Sequence[float]]:
    return [v.position for v in getattr(part, "vertices", ()) or ()]


def bbox(part) -> Tuple[List[float], List[float]]:
    """``(min, max)`` of a part's vertices, in model space."""
    pts = _points(part)
    if not pts:
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    return ([min(p[i] for p in pts) for i in range(3)],
            [max(p[i] for p in pts) for i in range(3)])


def dimensions(part) -> List[float]:
    lo, hi = bbox(part)
    return [hi[i] - lo[i] for i in range(3)]


def centre(part) -> List[float]:
    lo, hi = bbox(part)
    return [(lo[i] + hi[i]) / 2.0 for i in range(3)]


def _stem(name: str) -> str:
    """Part names differ between a model and its LOD (`lod_` prefix)."""
    return name[4:] if name.startswith("lod_") else name


def _pair_parts(mesh, donor) -> List[Tuple[object, object]]:
    """Match the export's parts to the donor's, by name then by order."""
    from . import gfile  # local: avoids a cycle at import time

    mine = list(gfile.iter_parts(mesh))
    theirs = list(gfile.iter_parts(donor))
    by_name: Dict[str, object] = {}
    for part in theirs:
        by_name[_stem(getattr(part, "name", "") or "")] = part
    pairs = []
    for index, part in enumerate(mine):
        key = _stem(getattr(part, "name", "") or "")
        other = by_name.get(key)
        if other is None and index < len(theirs):
            other = theirs[index]
        pairs.append((part, other))
    return pairs


def donor_mismatches(mesh, donor, size_tol: float = SIZE_TOL,
                     offset_tol: float = OFFSET_TOL) -> List[str]:
    """What an export changed about a part's *size* and *placement*.

    ``mesh`` is what the export wrote, ``donor`` the original `.g` it was
    authored from.  Returns one readable line per finding, empty when every
    part matches its donor.
    """
    if donor is None:
        return []
    findings: List[str] = []
    for part, other in _pair_parts(mesh, donor):
        if other is None:
            continue
        name = getattr(part, "name", "") or "?"
        mine_dims, their_dims = dimensions(part), dimensions(other)
        if max(mine_dims) <= 0 or max(their_dims) <= 0:
            continue
        worst = max(range(3), key=lambda i: abs(mine_dims[i] - their_dims[i]))
        ratio = (mine_dims[worst] / their_dims[worst]
                 if their_dims[worst] else 1.0)
        if abs(ratio - 1.0) > size_tol:
            axis = "xyz"[worst]
            findings.append(
                f"part '{name}' is {ratio:.2f}x the donor's along {axis} "
                f"({mine_dims[worst]:.2f} vs {their_dims[worst]:.2f}) — a .g "
                f"stores raw mesh data, so the engine draws it at that size "
                f"(Object > Apply > Scale bakes a non-applied object scale)")
        mine_c, their_c = centre(part), centre(other)
        shift = max(range(3), key=lambda i: abs(mine_c[i] - their_c[i]))
        if abs(mine_c[shift] - their_c[shift]) > offset_tol:
            axis = "xyz"[shift]
            findings.append(
                f"part '{name}' sits {mine_c[shift] - their_c[shift]:+.2f} "
                f"along {axis} from where the donor's does — it will not line "
                f"up with the hand/body it is bound to")
        mine_b, their_b = len(getattr(part, "bones", ()) or ()), \
            len(getattr(other, "bones", ()) or ())
        if mine_b != their_b:
            findings.append(
                f"part '{name}' is bound to {mine_b} bone(s), the donor's to "
                f"{their_b} — the engine deforms it differently")
    return findings


# --------------------------------------------------------------------------- #
#  the texture every part will be drawn with
# --------------------------------------------------------------------------- #
def texture_report(g_path: str,
                   folders: Sequence[str] = ()) -> List[Tuple[str, str, str]]:
    """``(part, material0_diffuse, file on disk or "")`` for every part of a ``.g``.

    Reading the *written* files back is the only honest answer to "did my
    texture name reach the game?": the name lives in the attribute block of the
    ``.g``, can come from the mesh's own ``d3unit_diffuse``, from the material
    image Blender exported, or from the donor part, and the three are
    indistinguishable from the Blender side.  ``folders`` are searched, after
    the ``.g``'s own folder, for a file with that name
    (:func:`d3tool.texture.find_texture_file`).
    """
    from . import gfile, texture

    with open(g_path, "rb") as fh:
        mesh = gfile.parse_geometry_file(fh.read())
    search = [os.path.dirname(os.path.abspath(g_path))]
    search += [f for f in folders or () if f]
    rows = []
    for part in gfile.iter_parts(mesh):
        name = (getattr(part, "attrs", {}) or {}).get("material0_diffuse") or ""
        found = texture.find_texture_file(name, search) if name else None
        rows.append((getattr(part, "name", "") or "?", name, found or ""))
    return rows
