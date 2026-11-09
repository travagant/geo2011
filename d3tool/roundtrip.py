# -*- coding: utf-8 -*-
"""Compare a re-exported unit with the one it was exported from.

The add-on's promise is "import a unit, touch nothing, export it, get the same
files back".  Every claim about that promise is a claim about *bytes*, so this
module answers one question — which files differ, and where — and nothing else.

Two callers use it:

* ``d3tool roundtrip <unit folder>`` runs the format-only path (``.g`` -> glTF
  -> ``.g``) in plain Python, so a regression is caught by CI;
* the add-on's *Self-test* operator runs the real path (``.g`` -> Blender ->
  ``.g``) inside Blender, where the glTF importer/exporter of the user's own
  Blender version is what stands between the two.

A file that differs is only half the story: the interesting part is *where*.
A `.a` that differs from byte 8528 on is float noise from a re-quantised
keyframe; a `.g` that differs at offset 240 is a changed count in the prelude.
:func:`first_difference` reports that offset so a report can be read without
the files.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class FileDiff:
    """One file of the bundle, seen from both sides."""

    name: str
    expected: Optional[int]      # bytes in the source folder (None: only here)
    produced: Optional[int]      # bytes the re-export wrote
    same: bool
    first_diff: Optional[int] = None
    note: str = ""

    def summary(self) -> str:
        if self.expected is None:
            return f"{self.name}: only in the export ({self.produced} B)"
        if self.produced is None:
            return f"{self.name}: missing from the export ({self.expected} B)"
        if self.same:
            return f"{self.name}: identical ({self.expected} B)"
        delta = self.produced - self.expected
        where = f"@ {self.first_diff}" if self.first_diff is not None else ""
        tail = f" — {self.note}" if self.note else ""
        return (f"{self.name}: differs ({delta:+d} B, first byte {where}){tail}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def first_difference(left: bytes, right: bytes) -> Optional[int]:
    """Offset of the first differing byte, or ``None`` when one is a prefix.

    ``None`` also covers the "the export wrote a shorter file" case: a file
    that stops early has no differing byte, and a report that calls the offset
    of its end a difference reads like corruption rather than truncation.
    """
    limit = min(len(left), len(right))
    step = 4096
    for start in range(0, limit, step):
        end = min(start + step, limit)
        if left[start:end] != right[start:end]:
            for i in range(start, end):
                if left[i] != right[i]:
                    return i
    return None


def diff_files(name: str, expected: Optional[bytes],
               produced: Optional[bytes]) -> FileDiff:
    if expected is not None and produced is not None and expected == produced:
        return FileDiff(name, len(expected), len(produced), True)
    where = None
    if expected is not None and produced is not None:
        where = first_difference(expected, produced)
    return FileDiff(name, None if expected is None else len(expected),
                    None if produced is None else len(produced),
                    False, where)


def diff_folders(source: str, produced: str,
                 only: Optional[List[str]] = None) -> List[FileDiff]:
    """Every file of ``produced`` against the same name in ``source``.

    Files the export did not write (an `.a` stream the export leaves alone, the
    icons) are not accused of being missing: the unit folder carries the whole
    bundle while an export writes the files it owns.  A file the export *did*
    write and ``source`` does not have is reported — that is how a pack ends up
    with a path the game cannot resolve.
    """
    names = sorted(os.listdir(produced), key=str.lower)
    if only:
        wanted = {n.lower() for n in only}
        names = [n for n in names if n.lower() in wanted]
    out: List[FileDiff] = []
    for name in names:
        path = os.path.join(produced, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            got = fh.read()
        ref = os.path.join(source, name)
        want = None
        if os.path.isfile(ref):
            with open(ref, "rb") as fh:
                want = fh.read()
        out.append(diff_files(name, want, got))
    return out


def summary(diffs: List[FileDiff]) -> Dict[str, int]:
    return {
        "files": len(diffs),
        "identical": sum(1 for d in diffs if d.same),
        "differing": sum(1 for d in diffs if not d.same),
    }


def report_lines(diffs: List[FileDiff]) -> List[str]:
    lines = [d.summary() for d in diffs]
    counts = summary(diffs)
    lines.append(f"{counts['identical']}/{counts['files']} file(s) identical")
    return lines
