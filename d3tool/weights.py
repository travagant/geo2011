"""Read a unit's skin weights without opening Blender.

The `.g` stores, per vertex, ``weights_on_vertex`` influence slots: the first
``w-1`` weights as float32 and the last one implied (``1 - sum``), next to
``w`` single-byte bone indices into the part's own bone table.  That is
compact but hard to eyeball, so this module turns it into answers to the two
questions a modder actually has:

* *what does this file carry?* — :func:`summarise` reports the influence width
  of every part, how the weights are distributed over the bones, and how many
  vertices are bound to each dominant bone;
* *did my export change what I painted?* — :func:`diff_weights` compares two
  files (the donor the game ships and the one Blender wrote) and counts the
  vertices whose dominant bone or whose weight magnitudes moved, with a
  per-vertex listing for the first few.

Vertices are matched by position when the two files disagree about their
number (Blender re-splits a vertex at a UV seam), else by index.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

#: weights are stored as bytes, so 1/255 is the finest difference the format has
QUANTUM = 1.0 / 255.0


@dataclass
class PartWeights:
    """What one mesh block of a `.g` carries, weight-wise."""

    name: str
    vertex_count: int = 0
    influences: int = 0                 # weights_on_vertex (2..4, 0 = morph)
    bone_count: int = 0
    max_influences: int = 0             # non-zero lanes seen on one vertex
    min_sum: float = 1.0
    max_sum: float = 1.0
    dominant: Dict[str, int] = field(default_factory=dict)   # bone -> vertices
    unweighted: int = 0                 # vertices with every lane at 0

    @property
    def bones_used(self) -> int:
        return len(self.dominant)


def _blocks(mesh) -> List:
    """The root mesh followed by every part of a compound container."""
    return [mesh] + list(getattr(mesh, "parts", []) or [])


def _bone_names(block, mesh) -> List[str]:
    bones = list(getattr(block, "bones", []) or [])
    if not bones:
        bones = list(getattr(mesh, "bones", []) or [])
    return [b.name for b in bones]


def _vertex_weights(vertex) -> Tuple[List[float], List[int]]:
    weights = list(vertex.influence_weights())
    bones = list(vertex.bones)
    return weights, bones


def dominant_bone(block, mesh, vertex) -> Optional[str]:
    """The bone the vertex follows most (``None`` when it carries no weight)."""
    weights, bones = _vertex_weights(vertex)
    names = _bone_names(block, mesh)
    best, best_w = None, 0.0
    for w, index in zip(weights, bones):
        if w > best_w and 0 <= index < len(names):
            best, best_w = names[index], w
    return best


def summarise(mesh) -> List[PartWeights]:
    """One :class:`PartWeights` per block of a parsed `.g`."""
    out: List[PartWeights] = []
    for block in _blocks(mesh):
        info = PartWeights(name=getattr(block, "name", "") or "",
                           vertex_count=len(block.vertices),
                           influences=int(getattr(block,
                                                 "weights_on_vertex", 0) or 0),
                           bone_count=len(getattr(block, "bones", []) or []))
        sums: List[float] = []
        for vertex in block.vertices:
            weights, _bones = _vertex_weights(vertex)
            live = [w for w in weights if w > 0.0]
            info.max_influences = max(info.max_influences, len(live))
            total = sum(weights)
            sums.append(total)
            if total <= 1e-6:
                info.unweighted += 1
                continue
            name = dominant_bone(block, mesh, vertex)
            if name is not None:
                info.dominant[name] = info.dominant.get(name, 0) + 1
        if sums:
            info.min_sum = min(sums)
            info.max_sum = max(sums)
        out.append(info)
    return out


@dataclass
class WeightChange:
    """One vertex whose weights differ between two files."""

    part: str
    index: int
    position: Tuple[float, float, float]
    was: List[Tuple[str, float]] = field(default_factory=list)
    now: List[Tuple[str, float]] = field(default_factory=list)
    old_dominant: Optional[str] = None
    new_dominant: Optional[str] = None

    @property
    def moved_bone(self) -> bool:
        return self.old_dominant != self.new_dominant


@dataclass
class WeightDiff:
    """The answer to "did my export keep the weights I painted?"."""

    parts: List[str] = field(default_factory=list)
    compared: int = 0
    changed: int = 0
    moved_bone: int = 0
    unmatched: int = 0                  # vertices with no counterpart
    samples: List[WeightChange] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.changed)


def _influences(block, mesh, vertex) -> List[Tuple[str, float]]:
    """``[(bone, weight)]`` strongest first, zero lanes dropped."""
    weights, bones = _vertex_weights(vertex)
    names = _bone_names(block, mesh)
    # a lane below half a byte step is the format's own noise: the donor
    # routinely carries `1 - sum` residues there, and counting them would
    # report a change on every vertex of an untouched round trip
    pairs = [(names[i] if 0 <= i < len(names) else f"#{i}", w)
             for w, i in zip(weights, bones) if w > QUANTUM / 2]
    pairs.sort(key=lambda p: -p[1])
    return pairs


def _key(position: Sequence[float]) -> Tuple[float, float, float]:
    return (round(float(position[0]), 4), round(float(position[1]), 4),
            round(float(position[2]), 4))


def diff_weights(old, new, limit: int = 10,
                 tolerance: float = QUANTUM / 2) -> WeightDiff:
    """Compare the weights of two parsed `.g` files (donor vs export).

    A vertex counts as *changed* when its dominant bone is another one or when
    any of its weights moved by more than half a byte step (the format cannot
    express anything finer).  Vertices are matched by index when the blocks
    have the same vertex count, else by position.
    """
    result = WeightDiff()
    old_blocks = {getattr(b, "name", ""): b for b in _blocks(old)}
    for block in _blocks(new):
        name = getattr(block, "name", "") or ""
        source = old_blocks.get(name)
        if source is None:
            result.notes.append(f"{name}: no part of that name in the first "
                                f"file — {len(block.vertices)} vertices "
                                f"cannot be compared")
            continue
        result.parts.append(name)
        by_index = len(source.vertices) == len(block.vertices)
        if not by_index:
            result.notes.append(
                f"{name}: {len(source.vertices)} vs {len(block.vertices)} "
                f"vertices, matched by position")
        index_of: Dict[Tuple[float, float, float], int] = {}
        for i, vertex in enumerate(source.vertices):
            index_of.setdefault(_key(vertex.position), i)
        for i, vertex in enumerate(block.vertices):
            j = i if by_index else index_of.get(_key(vertex.position), -1)
            if j < 0 or j >= len(source.vertices):
                result.unmatched += 1
                continue
            result.compared += 1
            was = _influences(source, old, source.vertices[j])
            now = _influences(block, new, vertex)
            old_dom = was[0][0] if was else None
            new_dom = now[0][0] if now else None
            if not _same_weights(was, now, tolerance):
                result.changed += 1
                if old_dom != new_dom:
                    result.moved_bone += 1
                if len(result.samples) < limit:
                    result.samples.append(WeightChange(
                        part=name, index=i, position=tuple(vertex.position),
                        was=was, now=now, old_dominant=old_dom,
                        new_dominant=new_dom))
    return result


def _same_weights(was: List[Tuple[str, float]],
                  now: List[Tuple[str, float]], tolerance: float) -> bool:
    if [n for n, _w in was] != [n for n, _w in now]:
        return False
    for (_n1, w1), (_n2, w2) in zip(was, now):
        if abs(w1 - w2) > tolerance:
            return False
    return True


def describe_change(change: WeightChange) -> str:
    """A one-line, human-readable account of a vertex whose weights moved."""
    def fmt(pairs):
        return " ".join(f"{name}={w:.3f}" for name, w in pairs[:4]) or "-"
    moved = ""
    if change.moved_bone:
        moved = f"  [{change.old_dominant} -> {change.new_dominant}]"
    return (f"{change.part}[{change.index}] "
            f"({change.position[0]:.3f},{change.position[1]:.3f},"
            f"{change.position[2]:.3f})  {fmt(change.was)}   =>   "
            f"{fmt(change.now)}{moved}")


def weight_report(mesh) -> List[Tuple[str, str, str, str]]:
    """``(part, influences, dominant bones, weight sum)`` rows for a table."""
    rows: List[Tuple[str, str, str, str]] = []
    for info in summarise(mesh):
        top = sorted(info.dominant.items(), key=lambda kv: -kv[1])[:3]
        bones = ", ".join(f"{name} {count}" for name, count in top) or "-"
        if len(info.dominant) > len(top):
            bones += f", +{len(info.dominant) - len(top)} more"
        if info.unweighted:
            bones += f", {info.unweighted} unweighted"
        rows.append((info.name, f"{info.influences} ({info.max_influences} used)",
                     f"{info.bones_used} of {info.bone_count}: {bones}",
                     f"{info.min_sum:.3f}..{info.max_sum:.3f}"))
    return rows


def skinning_problems(mesh) -> List[str]:
    """What a `.g` would do in game if its weights are as written.

    Two shapes are always wrong, and both come out of a Blender round-trip
    that lost the skinning rather than out of the original asset:

    * a block that declares several bones but every vertex follows **one** of
      them at full weight.  The mesh is rigid — the engine glues all of it to
      that single bone, so the unit folds into a heap as soon as it animates
      (the Inquisitor came back with all 3 699 vertices on `RightShoulder`).
      The add-on's glTF export says ``exported rigid (bound to the nearest
      bone)`` when the mesh has no vertex groups;
    * a block whose vertices carry **no** weight while the file has a
      skeleton: nothing attaches it, so the part is drawn at its stored
      coordinates or not at all.  A weapon part that lost its `Weapon1` bone
      is exactly this.

    A legitimately rigid part (a weapon, a single-bone prop) has one bone in
    its own table and is not reported.
    """
    problems: List[str] = []
    for info in summarise(mesh):
        if not info.vertex_count or info.influences <= 0:
            continue                      # morph part: no skinning at all
        if info.bone_count <= 1:
            continue                      # rigid by design
        if info.unweighted == info.vertex_count:
            problems.append(
                f"part {info.name}: none of its {info.vertex_count} vertices "
                f"carries a weight although the file has {info.bone_count} "
                f"bones — the mesh is not attached to the skeleton")
            continue
        if info.bones_used <= 1:
            bone = next(iter(info.dominant), "?")
            problems.append(
                f"part {info.name}: all {info.vertex_count} vertices follow "
                f"the single bone '{bone}' of the {info.bone_count} in its "
                f"table — the mesh is rigid, the export lost its skin "
                f"weights (re-import the unit, check the armature modifier "
                f"and the vertex groups)")
    return problems


def is_plausible(weights: Sequence[float]) -> bool:
    """Every vertex must sum to ~1 — the engine divides by the sum."""
    return bool(weights) and abs(math.fsum(weights) - 1.0) <= 2 * QUANTUM
