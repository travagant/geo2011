# -*- coding: utf-8 -*-
"""Structural audit of a unit folder — what the engine reads, cross-checked.

A unit is a bundle of files that *describe each other*: the `.scene` names the
`.g` and indexes a part inside it, the `gobj` repeats that part's triangle,
vertex, weight and bone counts as attributes, the `.ac` names the animation
stream and the mesh, and every `materialN_diffuse` has to find its `.t` next to
the unit.  Nothing in the individual files is self-validating: a `.g` with the
right counts and a `.scene` with the *old* counts both parse, and the engine
only finds out when it loads the pair.

That is the class of fault behind "the unit crashes the game when it upgrades":
the export rewrote the mesh, and one of the four files describing it still
carries the numbers of the mesh it replaced.  `audit` reads the bundle the way
the loader does and reports the disagreements; `compare` puts a rebuilt bundle
beside the retail one so the changes are not guesswork.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import gfile, scene as scenemod


@dataclass
class PartFacts:
    """One mesh block of a `.g`, with the numbers the `.scene` repeats."""

    index: int
    name: str
    verts: int
    tris: int
    bones: int
    weights_on_vertex: int
    materials: Dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        mats = ", ".join(f"{k}={v}" for k, v in sorted(self.materials.items()))
        return (f"{self.name}: {self.tris} tri, {self.verts} v, "
                f"{self.bones} bone(s), wob {self.weights_on_vertex}"
                + (f", {mats}" if mats else ""))


@dataclass
class GobjFacts:
    """One `gobj` of a `.scene`: which part it draws and what it claims."""

    scene: str
    name: str
    g_base: str
    index: Optional[int]
    attrs: Dict[str, str] = field(default_factory=dict)


def _material_keys(attrs: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in (attrs or {}).items()
            if k.lower().endswith("_diffuse") and v}


def g_parts(path: str) -> List[PartFacts]:
    """Every block of a `.g` in file order — the index the `.scene` uses."""
    data = open(path, "rb").read()
    mesh = gfile.parse_geometry_file(data)
    out: List[PartFacts] = []
    for index, part in enumerate([mesh] + list(mesh.parts or [])):
        out.append(PartFacts(
            index=index,
            name=part.name,
            verts=len(part.vertices or ()),
            tris=len(part.indices or ()) // 3,
            bones=len(part.bones or ()),
            weights_on_vertex=int(part.weights_on_vertex or 0),
            materials=_material_keys(part.attrs or {}),
        ))
    return out


def scene_gobjs(path: str) -> List[GobjFacts]:
    """Every `gobj` of a `.scene`, with the file and part index it points at."""
    with open(path, encoding="latin-1", newline="") as fh:
        text = fh.read()
    doc = scenemod.parse_scene(text)
    out: List[GobjFacts] = []
    for node in doc.find_all("gobj"):
        ref = ""
        for item in node.items:
            if isinstance(item, str) and '"' in item and item.strip().startswith('"'):
                ref = item
                break
        g_base = ""
        if ref:
            first = ref.split('"')[1] if '"' in ref else ""
            g_base = os.path.basename(first.replace("\\", "/"))
        attrs: Dict[str, str] = {}
        for key in ("name", "materials_num", "material0_diffuse",
                    "material0_triangles_num", "vertexs_weights_num",
                    "weights_on_vertex", "bones_num"):
            value = scenemod.node_attr(node, key)
            if value is not None:
                attrs[key] = value
        out.append(GobjFacts(
            scene=os.path.basename(path),
            name=node.name,
            g_base=g_base,
            index=scenemod.node_mesh_index(node),
            attrs=attrs,
        ))
    return out


def _int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def audit(folder: str) -> Tuple[Dict[str, List], List[str]]:
    """Read a unit bundle and report what disagrees with what.

    Returns ``(facts, findings)``; ``facts`` holds the parsed parts per ``.g``
    and gobjs per ``.scene`` so a caller can print them or diff two folders.
    """
    findings: List[str] = []
    facts: Dict[str, List] = {"parts": [], "gobjs": [], "files": []}
    if not os.path.isdir(folder):
        return facts, [f"no such folder: {folder}"]
    names = sorted(os.listdir(folder))
    facts["files"] = names
    g_files = [n for n in names if n.lower().endswith(".g")]
    parts_by_g: Dict[str, List[PartFacts]] = {}
    for name in g_files:
        try:
            parts = g_parts(os.path.join(folder, name))
        except Exception as exc:  # noqa: BLE001 - a broken .g is the finding
            findings.append(f"{name}: does not parse ({exc})")
            continue
        parts_by_g[name] = parts
        facts["parts"].append((name, parts))
        tri_total = sum(p.tris for p in parts)
        if tri_total == 0:
            findings.append(f"{name}: no triangles at all")
        seen: Dict[str, int] = {}
        for p in parts:
            seen[p.name] = seen.get(p.name, 0) + 1
        for dup, count in seen.items():
            if count > 1:
                findings.append(f"{name}: part name {dup!r} appears {count} times"
                                f" — the engine keeps one of them")
        # every material has to find its atlas next to the unit
        for p in parts:
            for key, mat in p.materials.items():
                want = os.path.splitext(mat)[0] + ".t"
                if not os.path.isfile(os.path.join(folder, want)):
                    findings.append(f"{name} part {p.name}: {key} names {mat}, "
                                    f"but {want} is not in the folder")

    for name in [n for n in names if n.lower().endswith(".scene")]:
        try:
            gobjs = scene_gobjs(os.path.join(folder, name))
        except Exception as exc:  # noqa: BLE001
            findings.append(f"{name}: does not parse ({exc})")
            continue
        facts["gobjs"].append((name, gobjs))
        for gobj in gobjs:
            parts = parts_by_g.get(gobj.g_base)
            if parts is None:
                if gobj.g_base:
                    findings.append(
                        f"{name}: gobj {gobj.name} draws {gobj.g_base}, which is "
                        f"not in the folder")
                continue
            if gobj.index is None:
                findings.append(f"{name}: gobj {gobj.name} has no part index")
                continue
            if gobj.index >= len(parts):
                findings.append(
                    f"{name}: gobj {gobj.name} points at part #{gobj.index} of "
                    f"{gobj.g_base}, which has {len(parts)} block(s) — the "
                    f"engine reads past the end of the mesh")
                continue
            part = parts[gobj.index]
            # A `_lod.scene` repeats the numbers of the MAIN mesh, not of the
            # LOD `.g` it draws — the retail Inquisitor's LOD gobj says 4664
            # triangles while `_lod.g` holds 1716, and every shipped unit does
            # this.  Compare a LOD gobj against the file it is describing or
            # the check reports the corpus itself as broken.
            counted = part
            if name.lower().endswith("_lod.scene") and gobj.g_base:
                main_file = gobj.g_base[:-6] + ".g"
                main_parts = parts_by_g.get(main_file)
                if main_parts and gobj.index < len(main_parts):
                    counted = main_parts[gobj.index]
            # the gobj repeats the part's numbers; one stale copy is enough to
            # desynchronise the loader (buffer sizes come from here)
            checks = (
                ("material0_triangles_num", part.tris, "triangles"),
                ("vertexs_weights_num", part.verts, "vertices"),
                ("bones_num", part.bones, "bones"),
                ("weights_on_vertex", part.weights_on_vertex, "weight slots"),
            )
            for key, expected, what in checks:
                want = expected
                if key in ("material0_triangles_num", "vertexs_weights_num",
                           "bones_num", "weights_on_vertex"):
                    want = getattr(counted, {
                        "material0_triangles_num": "tris",
                        "vertexs_weights_num": "verts",
                        "bones_num": "bones",
                        "weights_on_vertex": "weights_on_vertex"}[key])
                have = _int(gobj.attrs.get(key))
                if have is None or not want:
                    continue
                if have != want:
                    findings.append(
                        f"{name}: gobj {gobj.name} says {key} {have}, part "
                        f"#{gobj.index} of {gobj.g_base} has {want} {what}")
            attr_name = gobj.attrs.get("name")
            # index 0 is the root block, whose own name is the geometry's
            # (`Standard_8`) while the gobj is named after the unit — retail
            # does this everywhere, so only a *part* name mismatch counts
            if gobj.index and attr_name and attr_name != _strip_lod(part.name) \
                    and attr_name != part.name:
                findings.append(
                    f"{name}: gobj {gobj.name} is named {attr_name!r}, the part "
                    f"it draws is {part.name!r}")
            mat = gobj.attrs.get("material0_diffuse")
            own = part.materials.get("material0_diffuse")
            if mat and own and os.path.splitext(mat)[0].lower() != \
                    os.path.splitext(own)[0].lower():
                findings.append(
                    f"{name}: gobj {gobj.name} draws {mat}, but part "
                    f"#{gobj.index} of {gobj.g_base} names {own} — the scene "
                    f"and the mesh disagree on the atlas")

    # the LOD is the model the battlefield actually draws (`unit_scene_lod`)
    for main, parts in parts_by_g.items():
        if main[:-2].endswith("_lod"):
            continue
        lod = main[:-2] + "_lod.g"
        if lod not in parts_by_g:
            continue
        main_names = [p.name for p in parts]
        lod_names = [_strip_lod(p.name) for p in parts_by_g[lod]]
        for name in main_names:
            if name not in lod_names:
                findings.append(
                    f"{lod}: no part for {name!r} — the battle camera swaps to "
                    f"the LOD at distance and that part is not drawn "
                    f"(run `d3tool lod`)")
        for part in parts:
            if not part.materials:
                continue
            twin = next((p for p in parts_by_g[lod]
                         if _strip_lod(p.name) == part.name.strip()), None)
            if twin is None or not twin.materials:
                continue
            mine = part.materials.get("material0_diffuse")
            theirs = twin.materials.get("material0_diffuse")
            if mine and theirs and os.path.splitext(mine)[0].lower() != \
                    os.path.splitext(theirs)[0].lower():
                findings.append(
                    f"{lod}: part {twin.name} wears {theirs}, the main mesh's "
                    f"{part.name} wears {mine} — the weapon changes atlas "
                    f"when the camera pulls back")
    return facts, list(dict.fromkeys(findings))


def _strip_lod(name: str) -> str:
    name = name or ""
    return name[4:] if name.startswith("lod_") else name


def compare(a: str, b: str) -> List[str]:
    """Structural differences between two bundles of the same unit.

    ``compare(retail_folder, rebuilt_folder)`` answers "what did the rebuild
    change" without diffing binary files: parts added, dropped, re-textured,
    and the `.scene` attributes that no longer agree with them.
    """
    fa, _ = audit(a)
    fb, _ = audit(b)
    lines: List[str] = []
    pa = {name: parts for name, parts in fa["parts"]}
    pb = {name: parts for name, parts in fb["parts"]}
    for name in sorted(set(pa) | set(pb)):
        left, right = pa.get(name), pb.get(name)
        if left is None or right is None:
            lines.append(f"{name}: only in {'b' if left is None else 'a'}")
            continue
        na = [_strip_lod(p.name) for p in left]
        nb = [_strip_lod(p.name) for p in right]
        for gone in na:
            if gone not in nb:
                lines.append(f"{name}: part {gone!r} dropped")
        for new in nb:
            if new not in na:
                lines.append(f"{name}: part {new!r} added")
        for lp, rp in zip(left, right):
            if lp.summary() != rp.summary():
                lines.append(f"{name}: part {lp.name} changed\n"
                             f"    was: {lp.summary()}\n"
                             f"    now: {rp.summary()}")
    ga = {name: g for name, g in fa["gobjs"]}
    gb = {name: g for name, g in fb["gobjs"]}
    for name in sorted(set(ga) & set(gb)):
        for la, rb in zip(ga[name], gb[name]):
            if la.attrs == rb.attrs:
                continue
            diffs = [f"{k}: {la.attrs.get(k)!r} -> {rb.attrs.get(k)!r}"
                     for k in sorted(set(la.attrs) | set(rb.attrs))
                     if la.attrs.get(k) != rb.attrs.get(k)]
            lines.append(f"{name}: gobj {la.name} attributes changed"
                         + ("\n    " + "\n    ".join(diffs) if diffs else ""))
    only_a = sorted(set(fa["files"]) - set(fb["files"]))
    only_b = sorted(set(fb["files"]) - set(fa["files"]))
    if only_a:
        lines.append("files only in the first bundle: " + ", ".join(only_a))
    if only_b:
        lines.append("files only in the second bundle: " + ", ".join(only_b))
    return lines
