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
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import ac as acmod
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


def _mesh_ref(node) -> str:
    """The `.g` a `gobj` line names, as a basename."""
    for item in node.items:
        if isinstance(item, str) and item.strip().startswith('"'):
            first = item.split('"')[1] if '"' in item else ""
            return os.path.basename(first.replace("\\", "/"))
    return ""


def _int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _scene_resource_folder(folder: str, names: List[str]) -> str:
    """The unit's own resource folder, taken from the main `.scene`.

    A bundle may be assembled anywhere on disk (a temp folder called ``out``),
    so the directory it sits in says nothing about the path the engine will
    ask for.  The `.scene` does: it names its geometry as
    ``resources\\\\characters\\\\empire\\\\inquisitor\\\\….g``, and the segment
    before the file name is the folder every companion file has to live in.
    """
    scenes = [n for n in names if n.lower().endswith(".scene")
              and not n.lower().endswith("_lod.scene")]
    for name in scenes or [n for n in names if n.lower().endswith(".scene")]:
        try:
            with open(os.path.join(folder, name), encoding="latin-1") as fh:
                text = fh.read()
        except OSError:
            continue
        for match in re.finditer(r'"([^"\r\n]*?\.g)"', text, re.IGNORECASE):
            chunks = [c for c in match.group(1).replace("/", "\\").split("\\")
                      if c]
            if len(chunks) >= 2:
                return chunks[-2].lower()
    return os.path.basename(os.path.abspath(folder)).lower()


def _ac_folder_findings(folder: str, names: List[str]) -> List[str]:
    """`.ac` paths that send the engine into a folder the game does not have.

    An `.ac` names the animation stream and the mesh by *resource* path, and
    the engine does not fall back when that path points nowhere: the load
    stops with a missing-file dialog naming the pack.  The usual way to
    produce one is to derive the resource directory from a `*_lod` stem
    instead of the unit's own folder, which invents
    ``resources\\characters\\character_empire_angel_lod`` — a directory no
    retail install has.  A path is only reported when the file it names is
    sitting in *this* folder, so a unit that legitimately shares another
    folder's streams is not accused.
    """
    findings: List[str] = []
    base = _scene_resource_folder(folder, names)
    for name in [n for n in names if n.lower().endswith(".ac")]:
        try:
            with open(os.path.join(folder, name), encoding="latin-1") as fh:
                config = acmod.parse_ac(fh.read())
        except Exception as exc:  # noqa: BLE001 - an unreadable .ac is a finding
            findings.append(f"{name}: does not parse ({exc})")
            continue
        seen = set()
        for state in config.states:
            for ref in (state.file, state.meshfile):
                if not ref:
                    continue
                chunks = [c for c in ref.replace("/", "\\").split("\\") if c]
                if len(chunks) < 2:
                    continue
                where = chunks[-2].lower()
                target = chunks[-1]
                if where == base or (name, where) in seen:
                    continue
                if not os.path.isfile(os.path.join(folder, target)):
                    continue
                seen.add((name, where))
                findings.append(
                    f"{name}: state {state.name} names {ref}, but the game "
                    f"has no folder {where!r} — {target} is in this folder")
    return findings


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

    findings.extend(_ac_folder_findings(folder, names))

    for name in [n for n in names if n.lower().endswith(".scene")]:
        try:
            gobjs = scene_gobjs(os.path.join(folder, name))
        except Exception as exc:  # noqa: BLE001
            findings.append(f"{name}: does not parse ({exc})")
            continue
        facts["gobjs"].append((name, gobjs))
        # Which part does each `gobj` really draw?  Its mesh line names a slot
        # and for about half the corpus that *is* the part's position, but the
        # rest number their scenes in their own order (the OrcKing's opens with
        # the body, the Cleric's with the cloth), so comparing the declared
        # number to the file libelled 31 shipped units.  Resolve the binding by
        # agreement first (name, atlas, counts) and keep the declared number as
        # the tie-break; a `gobj` nothing explains is then a real finding.
        bound: Dict[str, int] = {}
        try:
            nodes = scenemod.parse_scene(
                open(os.path.join(folder, name), encoding="latin-1").read()
            ).find_all("gobj")
        except Exception:  # noqa: BLE001
            nodes = []
        for path in [n for n in parts_by_g if n.endswith(".g")]:
            mine = [n for n in nodes if _mesh_ref(n) == path]
            if not mine:
                continue
            # Two conventions live in the corpus: a LOD scene usually repeats
            # the MAIN mesh's numbers (the Inquisitor's LOD gobj says 4664
            # triangles where `_lod.g` holds 1716) and sometimes its own (the
            # OrcKing's LOD shield gobj says 156, which is the LOD block's
            # count).  Bind against both lists and keep the one that explains
            # more of the scene.
            best = None
            for cand in ([parts_by_g.get(path[:-6] + ".g")] +
                         [None] if name.lower().endswith("_lod.scene")
                         else [None]):
                gparts = cand or parts_by_g[path]
                if len(gparts) != len(mine):
                    continue
                binding = scenemod.bind_gobjs(mine, gparts)
                score = sum(scenemod._fact_diffs(n, gparts[i])
                            for n, i in zip(mine, binding) if i >= 0)
                if best is None or score > best[0]:
                    best = (score, binding, gparts)
            if best is None:
                continue
            bound[name + "|parts"] = best[2]
            for node, idx in zip(mine, best[1]):
                bound[(name, id(node))] = idx
                bound[(name, scenemod.node_attr(node, "name") or "")] = idx
        for gobj in gobjs:
            parts = parts_by_g.get(gobj.g_base)
            if parts is None:
                if gobj.g_base:
                    findings.append(
                        f"{name}: gobj {gobj.name} draws {gobj.g_base}, which is "
                        f"not in the folder")
                continue
            # the number the engine reads off the mesh line must name a block
            # that exists, whatever the numbers in the attribute block say
            if gobj.index is not None and gobj.index >= len(parts):
                findings.append(
                    f"{name}: gobj {gobj.name} points at part #{gobj.index} of "
                    f"{gobj.g_base}, which has {len(parts)} block(s) — the "
                    f"engine reads past the end of the mesh")
                continue
            drawn = bound.get((name, gobj.name), -1)
            if drawn < 0:
                drawn = bound.get((name, gobj.attrs.get("name") or ""), -1)
            if drawn < 0:
                if gobj.index is None:
                    findings.append(
                        f"{name}: gobj {gobj.name} has no part index")
                    continue
                if gobj.index >= len(parts):
                    findings.append(
                        f"{name}: gobj {gobj.name} points at part "
                        f"#{gobj.index} of {gobj.g_base}, which has "
                        f"{len(parts)} block(s) — the engine reads past the "
                        f"end of the mesh")
                    continue
                drawn = gobj.index
            if not 0 <= drawn < len(parts):
                # the binding chose the MAIN mesh's part list (a LOD scene);
                # the part itself lives in the file the node draws
                drawn = gobj.index if gobj.index is not None else 0
            if not 0 <= drawn < len(parts):
                continue
            part = parts[drawn]
            # A `_lod.scene` repeats the numbers of the MAIN mesh, not of the
            # LOD `.g` it draws — the retail Inquisitor's LOD gobj says 4664
            # triangles while `_lod.g` holds 1716, and every shipped unit does
            # this.  Compare a LOD gobj against the file it is describing or
            # the check reports the corpus itself as broken.
            chosen = bound.get(name + "|parts")
            counted = chosen[drawn] if chosen and drawn < len(chosen) else part
            counted_file = gobj.g_base
            if chosen is not None and chosen is not parts:
                counted_file = (gobj.g_base[:-6] + ".g"
                                if name.lower().endswith("_lod.scene")
                                else gobj.g_base)
            # The `gobj` repeats the part's numbers, and a stale copy used to
            # look like a crash waiting to happen.  It is not: the shipped
            # units disagree with their own meshes everywhere (the Archer's
            # body gobj says 3631 triangles where the block holds 4407, the
            # Cleric's LOD gobj says 376 where the LOD block holds 156, the
            # Archer's bow gobj names `weapon_empire_archer_bow_complete.tga`
            # while the mesh names `..._bow_diffuse.tga`), and the game loads
            # all of them — so these numbers cannot be what the loader reads.
            # They are reported as notes: worth knowing when a pack behaves
            # oddly, never a reason to call a shipped unit broken.
            def off(have: int, want: int) -> bool:
                return abs(have - want) > max(2, int(0.05 * want))

            for key, expected, what in (
                    ("material0_triangles_num", counted.tris, "triangles"),
                    ("vertexs_weights_num", counted.verts, "vertices"),
                    ("bones_num", counted.bones, "bones")):
                have = _int(gobj.attrs.get(key))
                if have is None or not expected:
                    continue
                if off(have, expected):
                    facts.setdefault("notes", []).append(
                        f"{name}: gobj {gobj.name} says {key} {have}, part "
                        f"#{drawn} of {counted_file} has {expected} {what}")
            have_w = _int(gobj.attrs.get("weights_on_vertex"))
            if have_w is not None and counted.weights_on_vertex \
                    and have_w != counted.weights_on_vertex:
                facts.setdefault("notes", []).append(
                    f"{name}: gobj {gobj.name} says weights_on_vertex "
                    f"{have_w}, part #{drawn} of {counted_file} writes "
                    f"{counted.weights_on_vertex}")
            mat = gobj.attrs.get("material0_diffuse")
            own = counted.materials.get("material0_diffuse")
            if mat and own and os.path.splitext(mat)[0].lower() != \
                    os.path.splitext(own)[0].lower():
                facts.setdefault("notes", []).append(
                    f"{name}: gobj {gobj.name} draws {mat}, part #{drawn} of "
                    f"{counted_file} names {own}")

    # the LOD is the model the battlefield actually draws (`unit_scene_lod`)
    for main, parts in parts_by_g.items():
        if main[:-2].endswith("_lod"):
            continue
        lod = main[:-2] + "_lod.g"
        if lod not in parts_by_g:
            continue
        main_names = [p.name for p in parts]
        lod_names = [_strip_lod(p.name) for p in parts_by_g[lod]]
        # A LOD model legitimately holds fewer parts than the main mesh (the
        # Cleric's LOD drops the hair: it is not worth drawing at distance),
        # so a part is only "missing" when the LOD has no counterpart at all —
        # neither by name nor by position with the same bone count.
        lod_parts = parts_by_g[lod]
        for name in main_names:
            main_idx = [p.name for p in parts].index(name)
            twin = (main_idx < len(lod_parts)
                    and lod_parts[main_idx].bones == parts[main_idx].bones)
            if name not in lod_names and not twin:
                facts.setdefault("notes", []).append(
                    f"{lod}: no part for {name!r} — the LOD model (what the "
                    f"battle camera draws at distance) has fewer parts than "
                    f"the main mesh, as it usually does")
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
