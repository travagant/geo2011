"""The ``.vdpack`` resource container (a Disciples III mod shipped as one file).

A retail Disciples III install keeps its units as loose folders
(``Empire/Hierophant/character_empire_hierophant.g`` next to its ``.a``,
``.ac``, ``.scene`` and ``.t``).  Mods are distributed as a single
``.vdpack`` file instead: the whole folder tree in one archive, which is what
the game mounts by name.

Layout (little-endian)::

    u32                       entry count
    repeat count:
        u32                   name length, *including* the NUL
        [name + NUL]          backslash separated, relative to the pack root
        u32 size              payload length
        u32 offset            absolute offset of the payload from byte 0
        u32 flags             engine flags, 0 in every observed pack
    [payloads]                back to back, in index order, no padding

Nothing in the header is aligned and the payloads are ordinary files
byte-for-byte, so a pack is fully reconstructible: :func:`build_vdpack`
writes the exact layout above and :func:`parse_vdpack` validates it
(truncated index, unterminated name, payload out of bounds, overlapping
payloads) instead of trusting the offsets.

The format is documented and implemented from the Heroes-VII → Disciples III
porting corpus (``docs/PORT_ANALYSIS_COMPARE_RU.md``); the toolkit's own
corpus ships no ``.vdpack``, so this reader is validated against synthetic
archives and against the *layout* the port used, not against a retail pack.

One thing the port could not explain, and this module deliberately does not
pretend to either: every pack it saw contains one resource named ``index``
(20 362 bytes in its reference archive) that no tool has decoded.
Same-path payload replacement inside such a pack is confirmed working in
game; a pack that *added* resources did not show them.  :func:`index_entry`
finds that resource so `vdpack pack --keep-index` can carry it over, and
:func:`manifest` records whether a bundle has one.
"""
from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

U32 = struct.Struct("<I")
ENTRY_TAIL = struct.Struct("<III")

#: Encodings tried for an entry name, in order.  Retail packs are ASCII, the
#: Russian localisation uses cp1251, and a port may write UTF-8.
NAME_ENCODINGS = ("utf-8", "cp1251")


def _decode_name(raw: bytes) -> Tuple[str, str]:
    for enc in NAME_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


@dataclass
class VdpackEntry:
    """One file inside a pack."""

    name: str                 # backslash separated, as stored
    size: int
    offset: int
    flags: int = 0
    encoding: str = "utf-8"   # how the stored name decoded

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def path(self) -> str:
        """The name as a relative OS path (``/`` separated)."""
        return self.name.replace("\\", "/")


@dataclass
class Vdpack:
    """A parsed archive: the index plus the raw bytes it indexes."""

    entries: List[VdpackEntry] = field(default_factory=list)
    data: bytes = b""
    index_size: int = 0       # where the first payload starts (= header end)
    parse_error: str = ""

    def __len__(self) -> int:
        return len(self.entries)

    def names(self) -> List[str]:
        return [e.name for e in self.entries]

    def find(self, name: str) -> Optional[VdpackEntry]:
        wanted = name.replace("\\", "/").lower()
        for e in self.entries:
            if e.path.lower() == wanted:
                return e
        return None

    def payload(self, entry) -> bytes:
        """The bytes of ``entry`` (a :class:`VdpackEntry`, index or name)."""
        if isinstance(entry, str):
            found = self.find(entry)
            if found is None:
                raise KeyError(entry)
            entry = found
        elif isinstance(entry, int):
            entry = self.entries[entry]
        return self.data[entry.offset:entry.end]

    def files(self) -> Dict[str, bytes]:
        return {e.name: self.payload(e) for e in self.entries}

    def summary(self) -> Dict[str, object]:
        counts: Dict[str, int] = {}
        for e in self.entries:
            ext = os.path.splitext(e.path)[1].lower() or "<none>"
            counts[ext] = counts.get(ext, 0) + 1
        return {
            "bytes": len(self.data),
            "entries": len(self.entries),
            "payload_bytes": sum(e.size for e in self.entries),
            "index_bytes": self.index_size,
            "flags": sorted({e.flags for e in self.entries}),
            "types": dict(sorted(counts.items())),
            "parse_error": self.parse_error,
        }


# --------------------------------------------------------------------------- #
#  reading
# --------------------------------------------------------------------------- #

def parse_vdpack(data: bytes) -> Vdpack:
    """Parse an archive.  Structural damage lands in ``parse_error``."""
    pack = Vdpack(data=data)
    if len(data) < 4:
        pack.parse_error = "shorter than the 4-byte entry count"
        return pack
    count = U32.unpack_from(data, 0)[0]
    pos = 4
    # An entry cannot be shorter than an empty name + the 16-byte tail, so a
    # count that does not fit the file is rejected before allocating anything.
    if count > (len(data) - 4) // (4 + 1 + ENTRY_TAIL.size) + 1:
        pack.parse_error = (f"entry count {count} does not fit in "
                            f"{len(data)} bytes")
        return pack
    for index in range(count):
        if pos + 4 > len(data):
            pack.parse_error = f"truncated header for entry {index}"
            return pack
        name_len = U32.unpack_from(data, pos)[0]
        pos += 4
        if not name_len:
            pack.parse_error = f"entry {index} has an empty name"
            return pack
        if pos + name_len + ENTRY_TAIL.size > len(data):
            pack.parse_error = f"entry {index} name/header runs past the file"
            return pack
        raw_name = data[pos:pos + name_len]
        pos += name_len
        if not raw_name.endswith(b"\0"):
            pack.parse_error = f"entry {index} name is not NUL terminated"
            return pack
        name, encoding = _decode_name(raw_name[:-1])
        size, offset, flags = ENTRY_TAIL.unpack_from(data, pos)
        pos += ENTRY_TAIL.size
        if offset + size > len(data):
            pack.parse_error = (f"payload of {name!r} runs past the file "
                                f"({offset}+{size} > {len(data)})")
            return pack
        pack.entries.append(VdpackEntry(name, size, offset, flags, encoding))
    pack.index_size = pos
    spans = sorted((e.offset, e.end, e.name) for e in pack.entries)
    previous_end = pos
    for start, end, name in spans:
        if start < previous_end:
            pack.parse_error = (f"payload of {name!r} overlaps the previous "
                                f"one (or the index)")
            pack.entries = [e for e in pack.entries if e.name != name]
            return pack
        previous_end = end
    return pack


def read_vdpack(path: str) -> Vdpack:
    return parse_vdpack(open(path, "rb").read())


# --------------------------------------------------------------------------- #
#  writing
# --------------------------------------------------------------------------- #

def _safe_name(name: str) -> str:
    """Normalise a stored name: relative, backslash separated, no traversal."""
    name = name.replace("\\", "/").strip()
    if not name or name.startswith("/") or ":" in name.split("/")[0]:
        raise ValueError(f"not a relative archive path: {name!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"archive path escapes the pack root: {name!r}")
    if not parts:
        raise ValueError(f"not a relative archive path: {name!r}")
    return "\\".join(parts)


def build_vdpack(files: Sequence[Tuple[str, bytes]],
                 flags=None) -> bytes:
    """Pack ``(name, bytes)`` pairs into an archive.

    ``files`` keeps its order in the payload area, so passing a sorted list
    reproduces the layout the Disciples III port wrote byte for byte;
    ``flags`` is either one value for every entry or one value per entry.
    """
    if not files:
        raise ValueError("nothing to pack")
    entries: List[Tuple[str, bytes, int]] = []
    seen = set()
    for i, (name, blob) in enumerate(files):
        stored = _safe_name(name)
        key = stored.lower()
        if key in seen:
            raise ValueError(f"duplicate archive path: {stored}")
        seen.add(key)
        if isinstance(flags, (list, tuple)):
            flag = int(flags[i])
        else:
            flag = int(flags or 0)
        entries.append((stored, bytes(blob), flag))
    index_size = 4 + sum(4 + len(n.encode("utf-8")) + 1 + ENTRY_TAIL.size
                         for n, _, _ in entries)
    index = bytearray(U32.pack(len(entries)))
    payload = bytearray()
    offset = index_size
    for name, blob, flag in entries:
        encoded = name.encode("utf-8") + b"\0"
        index += U32.pack(len(encoded)) + encoded
        index += ENTRY_TAIL.pack(len(blob), offset, flag)
        payload += blob
        offset += len(blob)
    return bytes(index) + bytes(payload)


def pack_folder(root: str) -> List[Tuple[str, bytes]]:
    """Every file under ``root`` as sorted ``(name, bytes)`` pairs."""
    files: List[Tuple[str, bytes]] = []
    for cur, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            full = os.path.join(cur, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            files.append((rel, open(full, "rb").read()))
    files.sort(key=lambda item: item[0].lower())
    return files


# --------------------------------------------------------------------------- #
#  extracting
# --------------------------------------------------------------------------- #

def extract_vdpack(pack: Vdpack, dest: str,
                   overwrite: bool = False) -> List[str]:
    """Write every entry under ``dest``; returns the written paths.

    Names are re-validated here, which matters because an archive is
    untrusted input: ``..`` or an absolute path in the index must never
    escape ``dest``.
    """
    written: List[str] = []
    for entry in pack.entries:
        rel = _safe_name(entry.name).replace("\\", "/")
        target = os.path.join(dest, *rel.split("/"))
        if os.path.exists(target) and not overwrite:
            raise FileExistsError(target)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        blob = pack.payload(entry)
        with open(target, "wb") as fh:
            fh.write(blob)
        written.append(target)
    return written


# --------------------------------------------------------------------------- #
#  self-containment
# --------------------------------------------------------------------------- #

#: Textures the engine keeps globally, so a unit folder (and therefore a
#: pack) never ships them: `cubemap_default.tga` is the reflection cube every
#: character material names in `material0_lightmap`.
GLOBAL_TEXTURES = ("cubemap_default",)


def _is_global_texture(name: str) -> bool:
    stem = os.path.splitext(os.path.basename(name.replace("\\", "/")))[0]
    return stem.lower() in GLOBAL_TEXTURES


@dataclass
class ReferenceReport:
    """What a pack's internal references look like.

    ``problems`` are references the pack must satisfy itself (a bare file
    name, or a path inside a folder the pack actually has).  ``notes`` are
    references that point outside the packed tree — retail content does this
    all the time (a cloned unit's `.ac` still names its template's animation
    in another folder), and the game resolves those from the normal mount.
    """

    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.problems)


INDEX_ENTRY = "index"


def index_entry(files) -> Optional[Tuple[str, bytes]]:
    """The opaque ``index`` entry a retail-style pack carries, if any.

    Every pack the Heroes-VII port saw contains one resource named exactly
    ``index`` (20 362 bytes in `NewUnit.vdpack`) whose format nobody has
    decoded.  It is not a directory of the archive: same-path payload
    replacement works with it untouched, while a pack that *added* paths
    (and kept the old `index`) did not show its units in game.  So the only
    game-confirmed way to ship new content is a pack that keeps the original
    `index` — which is why `vdpack pack --keep-index` exists.
    """
    pairs = files.items() if isinstance(files, dict) else files
    for name, data in pairs:
        if name.replace("/", "\\").strip("\\").lower() == INDEX_ENTRY:
            return name, data
    return None


def manifest(files, source: str = "") -> dict:
    """``files`` is either ``{name: bytes}`` or ``[(name, bytes), ...]``."""
    """A reproducible inventory of a bundle: one sha256 per file.

    A `.vdpack` is what a mod ships, so there has to be a way to prove that
    the archive carries exactly what the working folder has — and to compare
    two builds without unpacking them.  The manifest has the same shape for
    a folder and for the archive built from it, so
    ``vdpack manifest my_unit`` and
    ``vdpack manifest my_unit.vdpack`` diff to nothing (the workflow the
    Heroes-VII port used, keeping a `manifest.json` next to each pack).

    Paths are written in archive form (backslash separated) for both
    sources, entries are sorted case-insensitively, and the reference check
    runs along so a manifest doubles as the "would it load?" report.
    """
    pairs = list(files.items()) if isinstance(files, dict) else list(files)
    pairs.sort(key=lambda kv: kv[0].lower())
    entries = [{"path": name.replace("/", "\\"),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}
               for name, data in pairs]
    report = reference_findings(dict(pairs))
    index = index_entry(pairs)
    return {
        "source": source,
        "files": len(entries),
        "bytes": sum(entry["bytes"] for entry in entries),
        "index": ({"bytes": len(index[1]),
                   "sha256": hashlib.sha256(index[1]).hexdigest()}
                  if index else None),
        "entries": entries,
        "references": {"problems": report.problems, "notes": report.notes},
    }


def reference_findings(files: Dict[str, bytes]) -> ReferenceReport:
    """What a pack would fail to load in game: dangling internal references.

    A mod archive is mounted *instead of* the loose folders, so every file a
    `.ac` state, a `.scene` node or a `.g` material names has to be inside the
    same pack.  The checks are the ones the exporter's battle-readiness pass
    uses on a folder, run here against the in-memory payloads so a pack can be
    validated without unpacking it.

    Names are compared by basename and case-insensitively: the engine
    resolves a reference relative to the mounted root, exactly like a loose
    unit folder.
    """
    import re

    from . import ac as acmod
    from . import gfile
    from . import scene as scenemod

    report = ReferenceReport()
    findings = report.problems
    lower = {name.replace("/", "\\").lower(): name for name in files}
    lower.update({os.path.basename(k.replace("\\", "/")).lower(): v
                  for k, v in lower.items()})
    dirs = set()
    for name in files:
        low = name.replace("\\", "/").lower()
        while "/" in low:
            low = low.rsplit("/", 1)[0]
            dirs.add(low)

    def present(name: str) -> bool:
        base = os.path.basename(name.replace("\\", "/")).lower()
        return base in lower

    def outside(name: str) -> bool:
        """True when the reference lives in a folder this pack does not have."""
        path = name.replace("\\", "/")
        folder = path.rsplit("/", 1)[0].lower() if "/" in path else ""
        return bool(folder) and folder not in dirs

    for name, blob in sorted(files.items()):
        base = os.path.basename(name.replace("\\", "/"))
        low = base.lower()
        if low.endswith(".ac"):
            try:
                cfg = acmod.parse_ac(blob.decode("utf-8-sig", "replace"))
            except Exception as exc:  # noqa: BLE001
                findings.append(f"{base}: does not parse ({exc})")
                continue
            for st in cfg.states:
                for ref, what in ((st.file, "file"), (st.meshfile, "meshfile")):
                    if not ref or present(ref):
                        continue
                    line = (f"{base}: state {st.name} -> {what} "
                            f"{os.path.basename(ref.replace(chr(92), '/'))}")
                    if outside(ref):
                        report.notes.append(line + " is outside the packed tree")
                    else:
                        findings.append(line + " is not in the pack")
        elif low.endswith(".scene"):
            txt = blob.decode("latin-1")
            try:
                scenemod.parse_scene(txt)
            except Exception as exc:  # noqa: BLE001
                findings.append(f"{base}: does not parse ({exc})")
                continue
            for mm in re.finditer(r'file\s+"([^"]+)"', txt):
                raw = mm.group(1).replace("\\", "/")
                ref = os.path.basename(raw)
                # A `.scene` may point at a particle texture from the game's
                # shared `fx/` folders — those are mounted globally and are
                # not part of a unit pack.  Only the unit-local kinds must be
                # inside it.
                if os.path.splitext(ref)[1].lower() not in (
                        ".g", ".a", ".ac", ".alias", ".scene", ".char"):
                    continue
                if not ref or present(ref):
                    continue
                if outside(mm.group(1)):
                    report.notes.append(f"{base}: references {ref} outside "
                                        f"the packed tree")
                else:
                    findings.append(f"{base}: references {ref}, which is not "
                                    f"in the pack")
        elif low.endswith(".g"):
            try:
                mesh = gfile.parse_geometry_file(blob)
            except Exception as exc:  # noqa: BLE001
                findings.append(f"{base}: does not parse ({exc})")
                continue
            if mesh.parse_error:
                findings.append(f"{base}: does not re-parse cleanly "
                                f"({mesh.parse_error})")
            for prt in [mesh] + list(mesh.parts):
                attrs = prt.attrs or {}
                for key in ("material0_diffuse", "material0_lightmap",
                            "material0_bump", "material0_detail"):
                    tex = attrs.get(key, "")
                    if not tex or _is_global_texture(tex):
                        continue
                    want = os.path.splitext(os.path.basename(
                        tex.replace("\\", "/")))[0] + ".t"
                    if not present(want):
                        findings.append(
                            f"{base}: part {prt.name} {key} '{tex}' has no "
                            f"{want} in the pack")
            names = [p.name for p in mesh.parts]
            if len(set(names)) != len(names):
                dupes = sorted({n for n in names if names.count(n) > 1})
                findings.append(f"{base}: duplicate part names "
                                f"({', '.join(dupes)})")
    # one line per distinct problem: a `.scene` repeating a reference twenty
    # times must not bury the other findings
    report.problems = list(dict.fromkeys(report.problems))
    report.notes = list(dict.fromkeys(report.notes))
    return report
