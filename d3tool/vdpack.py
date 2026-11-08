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


def pack_like(source: "Vdpack", files, drop=()) -> Tuple[bytes, List[str]]:
    """Rebuild a pack with `source`'s entry order, names, flags and `index`.

    The only in-game-confirmed shape is *the same paths, different payloads*,
    and the archive is a flat list whose order the opaque `index` may well
    depend on.  So a patch pack must not be re-sorted: this walks the source
    archive entry by entry, takes each payload from ``files`` (matched
    case-insensitively on the backslash path), keeps the source's spelling and
    flags, and falls back to the source payload for anything the folder does
    not carry (the `index` resource itself, most of all).

    ``drop`` removes entries from the result (paths as they appear in the
    source archive, matched case-insensitively): a pack that ships a file the
    engine will request and cannot resolve — the Angel's generated
    ``*_lod.ac``, whose own ``meshfile`` names a folder no retail install has —
    stops the load with a missing-file dialog, while *removing* a path is the
    direction the port confirmed as survivable.

    Returns the archive bytes and the list of folder paths that had no place
    in the archive (paths the source does not carry), since *adding* paths is
    exactly the unconfirmed case.
    """
    dropped = {str(name).replace("/", "\\").strip("\\").lower()
               for name in drop}
    pairs = list(files.items()) if isinstance(files, dict) else list(files)
    folder = {name.replace("/", "\\").strip("\\").lower(): data
              for name, data in pairs}
    entries: List[Tuple[str, bytes, int]] = []
    used = set()
    for entry in source.entries:
        key = entry.name.replace("/", "\\").strip("\\").lower()
        if key in dropped:
            used.add(key)
            continue
        blob = folder.get(key)
        if blob is None:
            blob = source.payload(entry)
        else:
            used.add(key)
        entries.append((entry.name, blob, entry.flags))
    extra = sorted(k for k in folder if k not in used and k != INDEX_ENTRY)
    return build_vdpack([(n, b) for n, b, _ in entries],
                        flags=[f for _, _, f in entries]), extra


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

    ``warnings`` are the awkward middle: the reference names a file the pack
    *does* carry, but in a folder the pack does not have.  Nothing resolves it
    — retail never does this, since an out-of-tree reference points at a file
    the pack does not ship at all — and it is the exact signature of a
    generated `.ac`/`.scene` written with the wrong resource root
    (``resources\\\\characters\\\\<object name>\\\\…`` instead of the unit's own
    folder).  The pack loads in a viewer and fails in the game, so it is worth
    a loud line even though the toolkit cannot prove the case is fatal.
    """

    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

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

    Two more things are confirmed on the Angel unit pack (24 entries, original
    4100-byte `index`, both `.g` payloads replaced in place and one path — a
    generated `*_lod.ac` — dropped): the battle loads and the rebuilt geometry
    is the geometry on screen, so removing a path is survivable where adding
    one is not; and a unit pack does not have to mirror the unit folder, since
    the `.dds` diffuses and the six icons it did not carry still resolved from
    the mounted vanilla tree.  What the engine does *not* fall back for is a
    path its own `.ac`/`.scene` names into a folder the pack lacks: that is a
    missing-file dialog naming ``<pack>.vdpack:resources\characters\...``,
    which is what `reference_findings` warns about.
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
        "references": {"problems": report.problems, "notes": report.notes,
                       "warnings": report.warnings},
    }


def _check_weapon_trace(base: str, txt: str, files: Dict[str, bytes],
                        findings: List[str]) -> None:
    """Does a `.scene` weapon trail still name the unit's `Trail*` bones?

    A unit that draws a weapon trail carries a `VDGoWeaponTrace` node whose
    `boneid` lines are **indices into the unit's animation stream**, not
    names.  All 54 such scenes of the retail corpus resolve them to
    `Trail1`…`TrailN` — the bones a weapon's tip trail is built from, each
    parented to the weapon bone.  An animation stream that lost those bones
    (a re-exported `.a` that only holds the bones a *mesh* is weighted to,
    which is what a Blender round-trip does to animation-only bones) leaves
    the same indices pointing at whatever bone now sits there — an arm — and
    the trail follows the wrong thing or the unit's load dies on it.

    A unit names several streams and only the full one carries every bone
    (a `_run.a` holds a reduced skeleton, and retail's own Grand Inquisitor
    would flag on those), so a unit is only reported when **no** stream it
    ships resolves the ids to trail bones.  A `.ac` naming an out-of-tree
    animation says nothing at all — the game resolves it from the normal
    mount.

    The `_lod.scene` of a unit is skipped: its `.ac` names only the reduced
    `…_lod.a` streams, which carry no trail bones even in retail
    (Defender of Faith, Dark Servant and Grand Inquisitor all leave their
    trail `boneid` pointing at a leg or past the end of the stream), so the
    index is already stale there by design and the game loads those fine.
    """
    import re

    from . import ac as acmod
    from . import anim as animmod

    stem = os.path.splitext(base)[0].lower()
    if stem.endswith("_lod"):
        return
    ids = [int(v) for v in re.findall(r"boneid\s+(\d+)", txt)]
    if not ids:
        return
    by_base: Dict[str, bytes] = {}
    for name, blob in files.items():
        by_base.setdefault(
            os.path.basename(name.replace("\\", "/")).lower(), blob)
    streams: List[str] = []
    for mm in re.finditer(r'file\s+"([^"]+)"', txt):
        ref = mm.group(1).replace("\\", "/")
        if not ref.lower().endswith(".ac"):
            continue
        blob = by_base.get(os.path.basename(ref).lower())
        if blob is None:
            continue
        try:
            cfg = acmod.parse_ac(blob.decode("utf-8-sig", "replace"))
        except Exception:  # noqa: BLE001  — the `.ac` branch reports its own
            continue
        for st in cfg.states:
            if not st.file:
                continue
            want = os.path.basename(st.file.replace("\\", "/"))
            if want.lower() in by_base and want not in streams:
                streams.append(want)

    best = None          # (bone count, stream, complaint per id)
    for stream in streams:
        try:
            af = animmod.parse_anim(by_base[stream.lower()])
        except Exception:  # noqa: BLE001
            continue
        if not af.bones:
            continue
        complaints: List[str] = []
        for i in ids:
            if i >= len(af.bones):
                complaints.append(f"boneid {i} is past the end of {stream} "
                                  f"({len(af.bones)} bones)")
                continue
            bone = af.bones[i].name
            if not bone.lower().startswith("trail"):
                complaints.append(f"boneid {i} lands on bone '{bone}' in "
                                  f"{stream}")
        if not complaints:
            return                      # a stream that still has them: fine
        if best is None or len(af.bones) > best[0]:
            best = (len(af.bones), stream, complaints)
    if best is None:
        return
    for line in best[2]:
        findings.append(
            f"{base}: weapon-trail {line} — every retail scene's trail boneid "
            f"points at a Trail bone, so this unit's animation streams have "
            f"lost them (the trail follows an arm, or the unit crashes)")


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

    home: Dict[str, str] = {}
    for name in files:
        path = name.replace("\\", "/")
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        home.setdefault(os.path.basename(path).lower(), folder)

    def wrong_folder(name: str) -> str:
        """The pack folder holding ``name`` when the reference got it wrong.

        Empty when the reference is bare, points at a folder the pack has, or
        names a file the pack does not carry at all (the legitimate
        out-of-tree case).
        """
        path = name.replace("\\", "/")
        folder = path.rsplit("/", 1)[0].lower() if "/" in path else ""
        holder = home.get(os.path.basename(path).lower())
        if holder is None or not folder or folder == holder.lower():
            return ""
        # a file the pack holds at its own root says nothing: a folder-shaped
        # bundle (`vdpack pack MyUnit/`) mounts at the unit folder, and retail
        # `.ac`/`.scene` files name their files by full path there
        return holder

    def out_of_tree(head: str, ref: str) -> None:
        """Record a reference into a folder the pack does not have.

        Three cases: the pack does not carry that file at all (an ordinary
        out-of-tree reference, a note), it carries it at its own root (a
        folder-shaped bundle, which mounts at the unit folder and may name its
        files by full path — silent), or it carries it somewhere deeper (a path
        built on the wrong resource root — a warning).
        """
        path = ref.replace("\\", "/")
        holder = home.get(os.path.basename(path).lower())
        if holder is None:
            report.notes.append(head + " is outside the packed tree")
        elif holder:
            report.warnings.append(
                f"{head}: the reference's folder is not in the pack, but the "
                f"pack holds that file in {holder} — the path is wrong")

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
                    if not ref:
                        continue
                    line = (f"{base}: state {st.name} -> {what} "
                            f"{os.path.basename(ref.replace(chr(92), '/'))}")
                    if outside(ref):
                        # a path into a folder the pack does not have: ordinary
                        # out-of-tree content, or a path built on the wrong
                        # resource root — which is what a generated `.ac` with
                        # the object name as its folder looks like
                        out_of_tree(line, ref)
                    elif not present(ref):
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
                if outside(mm.group(1)):
                    out_of_tree(f"{base}: references {ref}", mm.group(1))
                elif not present(ref):
                    findings.append(f"{base}: references {ref}, which is not "
                                    f"in the pack")
            _check_weapon_trace(base, txt, files, findings)
        elif low.endswith(".char"):
            from . import char as charmod
            prof = charmod.parse_char(blob)
            prof.path = base
            # the `desc`/`stats`/`sound` fragments a unit profile includes are
            # its own; the scenes, icons and fx it names may live elsewhere
            for ref in prof.includes:
                want = os.path.basename(ref.replace("\\", "/"))
                if outside(ref):
                    out_of_tree(f"{base}: includes {want}", ref)
                elif not present(want):
                    findings.append(f"{base}: includes {want}, which is "
                                    f"not in the pack")
            for key, ref in prof.resources():
                if key == "#include" or ref.startswith("$("):
                    continue
                norm = charmod.normalize_reference(ref)
                if norm.startswith("resources" + chr(92)):
                    norm = norm.split(chr(92), 1)[1]
                if not norm.startswith("characters" + chr(92)):
                    continue          # fx/… and other engine-global trees
                want = os.path.basename(norm.replace("\\", "/"))
                if outside(ref):
                    out_of_tree(f"{base}: {key} -> {want}", ref)
                    continue
                if present(want):
                    continue
                else:
                    findings.append(f"{base}: {key} -> {want} is not in the "
                                    f"pack")
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
    report.warnings = list(dict.fromkeys(report.warnings))
    return report
