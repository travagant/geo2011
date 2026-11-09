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

Every pack also carries one resource named ``index``: the mod's manifest, a
short text file of per-file MD5 and SHA-256 digests (see :func:`parse_index`),
wrapped in eight bytes and XOR-obfuscated with a 32-bit LCG keystream (see
:func:`decode_index`).  Same-path payload replacement inside such a pack is
confirmed working in game; a pack that *added* resources did not show them,
exactly because the loader validates the file list against this manifest
before it mounts anything.  :func:`build_index` therefore writes a manifest
for any file set, and `vdpack pack --reindex` / `vdpack pack --like ... 
--reindex` put a correct one into a rebuilt archive.
"""
from __future__ import annotations

import hashlib
import os
import re
import struct
from dataclasses import dataclass, field
from uuid import uuid4
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


def pack_like(source: "Vdpack", files, drop=(),
              reindex: bool = False) -> Tuple[bytes, List[str]]:
    """Rebuild a pack with `source`'s entry order, names, flags and `index`.

    The only in-game-confirmed shape is *the same paths, different payloads*,
    and the archive is a flat list whose order nothing here wants to disturb:
    a rebuild that reuses it is byte-comparable with the pack it replaces.  So a patch pack must not be re-sorted: this walks the source
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

    ``reindex`` replaces the carried-over manifest with one written for the
    payloads that actually landed, keeping the mod's ``Name``/``UUID``/``Type``
    — this is the mode to ship in, since the loader checks every payload
    against the manifest before it mounts anything.

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
    if reindex:
        # Same paths, same order, different payloads is only half the shape:
        # the manifest has to agree with the new bytes, or the loader answers
        # `has wrong index` before it mounts the archive.
        flags = {_norm_name(n).lower(): f for n, _, f in entries}
        entries = [(n, b, flags.get(_norm_name(n).lower(), 0))
                   for n, b in reindex_pack([(n, b) for n, b, _ in entries])]
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
#  planning a build
# --------------------------------------------------------------------------- #

def infer_prefix(base, folder_names: Sequence[str]) -> Optional[str]:
    """Which archive folder a loose unit folder belongs to.

    A mod's folder is shallow (``Empire/Inquisitor``) while the archive path is
    deep (``resources\\Characters\\Empire\\Inquisitor``), so a plan has to line
    the two up before it can say anything about "new paths".  The pack is the
    authority: the entry that carries one of the folder's own files names the
    folder, and a file name match outranks a bare ``.g`` stem match.
    """
    wanted = {n.replace("/", "\\").lower() for n in folder_names}
    stems = {os.path.splitext(n)[0].lower() for n in folder_names
             if n.lower().endswith(".g")}
    scores: Dict[str, int] = {}
    for name, _ in _pairs(base):
        parts = _norm_name(name).split("\\")
        if len(parts) < 2:
            continue
        leaf = parts[-1].lower()
        parent = "\\".join(parts[:-1])
        if leaf in wanted:
            scores[parent] = scores.get(parent, 0) + 3
        elif leaf.endswith(".g") and os.path.splitext(leaf)[0] in stems:
            scores[parent] = scores.get(parent, 0) + 1
    if not scores:
        return None
    return max(scores.items(), key=lambda kv: (kv[1], len(kv[0])))[0]


def _scene_prefix(files: Sequence[Tuple[str, bytes]]) -> Optional[str]:
    """The archive folder a unit's own `.scene` says its mesh lives in."""
    import re
    for name, data in files:
        if not name.lower().endswith(".scene"):
            continue
        text = data.decode("latin-1", "replace")
        hit = re.search(r'"([^"]*[\\/][^"]*\.g)"', text, re.IGNORECASE)
        if hit:
            return "\\".join(hit.group(1).replace("/", "\\").split("\\")[:-1])
    return None


def plan_bundle(folder, base=None, prefix: Optional[str] = None) -> dict:
    """How to ship `folder` so the engine actually sees it.

    ``base`` is the pack the game already loads (or a folder in the same
    shape).  The plan answers the three questions a modder ships on:

    * which files **replace** a path the pack already carries — the only shape
      with a runtime confirmation behind it;
    * which files are **new paths**, i.e. the shape the port tried in game and
      saw no units at all, with the names already available in that folder as
      the rename targets;
    * which of the pack's own files the folder does **not** carry — those stay
      whatever the vanilla tree has, which is fine and worth seeing.

    With no ``base`` it still plans the loose install the engine's own mount
    understands (``resources\\...\\<file>``), which is the way to ship a file
    that no pack shape can add.
    """
    pairs = _pairs(folder)
    names = [n.replace("/", "\\") for n, _ in pairs]
    base_pairs = _pairs(base) if base is not None else []
    where = prefix or (infer_prefix(base_pairs, names) if base_pairs
                       else None) or _scene_prefix(pairs) or ""
    where = where.strip("\\")
    existing = {_norm_name(n).lower() for n, _ in base_pairs}
    base_bytes = {_norm_name(n).lower(): d for n, d in base_pairs}
    textures: Dict[str, List[str]] = {}     # archive folder -> texture stems
    for name, _ in base_pairs:
        norm = _norm_name(name)
        parts = norm.split("\\")
        if parts[-1].lower().endswith(".t"):
            textures.setdefault("\\".join(parts[:-1]).lower(), []).append(
                os.path.splitext(parts[-1])[0])
    entries = []
    for name, data in pairs:
        leaf = name.replace("/", "\\").split("\\")[-1]
        path = f"{where}\\{leaf}" if where else leaf
        if not base_pairs:
            kind = "loose"          # nothing to compare against: a bare layout
        else:
            kind = ("replaces" if path.lower() in existing else "new")
        entries.append({"path": path, "file": leaf, "bytes": len(data),
                        "kind": kind})
    carried = {e["path"].lower() for e in entries}
    missing = [n for n, _ in base_pairs
               if where and _norm_name(n).lower().startswith(where.lower() + "\\")
               and _norm_name(n).lower() not in carried]
    added = [e for e in entries if e["kind"] == "new"]
    here = sorted(textures.get(where.lower(), []))
    for entry in added:
        entry["alternatives"] = []
        if entry["file"].lower().endswith(".t"):
            # a `.g` attribute holds a bare texture name, resolved inside the
            # unit's own folder first: these are the names a rename can take,
            # and every one of them is a path the pack's `index` already lists.
            entry["alternatives"] = here[:4]
    by_leaf = {n.replace("/", "\\").split("\\")[-1].lower(): d
               for n, d in pairs}
    stale = [e["path"] for e in entries
             if e["kind"] == "replaces"
             and base_bytes.get(e["path"].lower()) is not None
             and base_bytes[e["path"].lower()] != by_leaf.get(e["file"].lower())]
    return {"prefix": where, "entries": entries, "added": added,
            "replaced": [e for e in entries if e["kind"] == "replaces"],
            "missing": sorted(missing), "has_base": bool(base_pairs),
            "index_present": any(_norm_name(n).lower() == "index"
                                 for n, _ in base_pairs),
            "payloads_changed": sorted(stale)}


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


def index_entry(files) -> Optional[Tuple[str, bytes]]:
    r"""The ``index`` entry a retail-style pack carries, if any.

    Every pack the Heroes-VII port saw contains one resource named exactly
    ``index`` (20 362 bytes in `NewUnit.vdpack`, 4 100 in the Angel pack,
    4 738 in `ruru.vdpack`) — the mod's manifest, decoded by
    :func:`decode_index`.  It is not a directory of the archive: same-path
    payload replacement works with it untouched, while a pack that *added*
    paths (and kept the old `index`) did not show its units in game, because
    the loader checks the archive against this manifest first.  Carrying it
    over is still the safe move for a pure replacement pack
    (`vdpack pack --keep-index`), and :func:`build_index` is the way to get a
    correct manifest for a file set that differs.

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


# --------------------------------------------------------------------------- #
#  the `index` resource: the mod manifest
# --------------------------------------------------------------------------- #
#
# `index` is a text manifest, stored obfuscated.  On disk:
#
#     [0:4]   65 43 74 FF              magic
#     [4:8]   u32 little endian        seed for the keystream
#     [8:]    text + NUL, XOR-obfuscated byte by byte
#
# and the keystream is the high byte of a 32-bit LCG seeded with those four
# bytes, stepped *before* each byte is used:
#
#     state = state * 0x34405 + 0x269EC9   (mod 2**32)
#     plain = cipher ^ ((state >> 16) & 0xFF)
#
# That is the whole of `0x40a290` in the retail toolkit (magic compare, seed
# load, the multiply/add, the `shr edx,0x10` and the `xor dl,bl`), which runs
# over a resource that starts exactly with these bytes; the text it yields
# hashes every payload with MD5 and SHA-256 and matches all of them.  The
# LCG is MSVC's `rand()`, so this is obfuscation, not encryption: the seed is
# in the file.
#
# The text is (`\n` line ends, quotes mandatory, `;` terminator, fields in
# this order)::
#
#     Name = "ruru";
#     UUID = "6af75149-9223-4e52-b5cc-ccb9ac990071";
#     Type = "Mod";
#     File = "resources\\Characters\\...\\x.g", "<md5 hex>", "<sha256 hex>";
#     ...
#
# `Name`/`UUID`/`Type` are the mod's identity (`MOD_INDEX_NAME`,
# `MOD_INDEX_UUID`, `MOD_INDEX_TYPE` in the toolkit, ids 0x32..0x35), and one
# `File` line follows per indexed path, lowercase hex.  Paths are indexed with
# backslashes, `index` itself is not listed, and neither is a root-level
# marker such as `russian.txt` — the manifest covers the resource tree.

INDEX_ENTRY = "index"
INDEX_MAGIC = b"\x65\x43\x74\xff"
INDEX_SEED = 0xC6931EDD      # the seed every observed pack carries
INDEX_MUL = 0x34405
INDEX_ADD = 0x269EC9

_INDEX_HEAD = re.compile(r'^(Name|UUID|Type) = "([^"]*)";$')
_INDEX_FILE = re.compile(r'^File = "([^"]+)", "([0-9a-f]{32})", '
                         r'"([0-9a-f]{64})";$')


def _index_keystream(seed: int, count: int) -> bytes:
    """The LCG keystream, one byte per call *after* stepping the state."""
    state = seed & 0xFFFFFFFF
    out = bytearray(count)
    for i in range(count):
        state = (state * INDEX_MUL + INDEX_ADD) & 0xFFFFFFFF
        out[i] = (state >> 16) & 0xFF
    return bytes(out)


def decode_index(blob: bytes) -> str:
    """Obfuscated `index` payload -> the manifest text (no trailing NUL).

    Raises ``ValueError`` if the eight-byte header is not there, so a caller
    can tell "not a manifest" from "manifest with odd contents" — the first
    is what a hand-written pack looks like, the second is a bug.
    """
    if len(blob) < 8:
        raise ValueError(f"`index` too short for a header ({len(blob)} bytes)")
    if blob[:4] != INDEX_MAGIC:
        raise ValueError("`index` does not start with the 65 43 74 FF header "
                         f"(got {blob[:4].hex()})")
    seed = U32.unpack_from(blob, 4)[0]
    body = blob[8:]
    plain = bytes(b ^ k for b, k in zip(body, _index_keystream(seed, len(body))))
    plain = plain.rstrip(b"\x00")
    for encoding in ("utf-8", "cp1251", "latin-1"):
        try:
            return plain.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("`index` decoded to bytes no encoding accepts")  # pragma:


def encode_index(text: str, seed: int = INDEX_SEED) -> bytes:
    """Manifest text -> an obfuscated `index` payload the loader will read."""
    body = text.encode("utf-8") + b"\x00"
    obfuscated = bytes(b ^ k for b, k in
                       zip(body, _index_keystream(seed, len(body))))
    return INDEX_MAGIC + U32.pack(seed & 0xFFFFFFFF) + obfuscated


@dataclass
class IndexFile:
    """One `File` line: a path and the two digests the loader checks."""
    path: str
    md5: str
    sha256: str


@dataclass
class IndexDocument:
    """A parsed manifest."""
    name: str = ""
    uuid: str = ""
    type: str = ""
    files: List[IndexFile] = field(default_factory=list)
    text: str = ""

    @property
    def paths(self) -> List[str]:
        return [f.path for f in self.files]


def parse_index(text: str) -> IndexDocument:
    """Manifest text -> :class:`IndexDocument`; unknown lines are an error.

    Being strict is the point: a manifest this reader half-understands is a
    manifest whose hashes cannot be trusted, and the whole resource exists to
    be checked.
    """
    doc = IndexDocument(text=text)
    for number, line in enumerate(text.split("\n"), start=1):
        line = line.strip("\r")
        if not line:
            continue
        head = _INDEX_HEAD.match(line)
        if head:
            key, value = head.group(1), head.group(2)
            if key == "Name":
                doc.name = value
            elif key == "UUID":
                doc.uuid = value
            else:
                doc.type = value
            continue
        record = _INDEX_FILE.match(line)
        if record:
            doc.files.append(IndexFile(*record.groups()))
            continue
        raise ValueError(f"`index` line {number} is not a field: {line!r}")
    return doc


def _digests(blob: bytes) -> Tuple[str, str]:
    return hashlib.md5(blob).hexdigest(), hashlib.sha256(blob).hexdigest()


def _indexed(name: str) -> bool:
    """Does a path belong in the manifest?

    `index` cannot hash itself, and a root-level marker (`russian.txt` in
    every observed pack) is not part of the resource tree the loader walks, so
    neither is listed.
    """
    norm = _norm_name(name).lower()
    return norm != INDEX_ENTRY and "\\" in norm


def build_index(files, name: Optional[str] = None,
                uuid: Optional[str] = None, type_: str = "Mod",
                seed: int = INDEX_SEED,
                keep: Optional["IndexDocument"] = None) -> Tuple[str, bytes]:
    """Manifest text and payload for ``files``, hashes included.

    ``keep`` is the manifest an archive already carries: its ``Name``,
    ``UUID`` and ``Type`` win over the defaults, so rebuilding a mod's pack
    keeps the mod's identity.  Without it the identity is taken from the
    arguments and a fresh UUID is generated — the loader only needs the field
    to be there and to be unique per mod.
    """
    if keep is not None:
        name = keep.name or name
        uuid = keep.uuid or uuid
        type_ = keep.type or type_
    if not name:
        name = "mod"
    if not uuid:
        uuid = str(uuid4())
    if not type_:
        type_ = "Mod"
    lines = [f'Name = "{name}";', f'UUID = "{uuid}";', f'Type = "{type_}";']
    for path, blob in _pairs(files):
        if not _indexed(path):
            continue
        md5, sha256 = _digests(blob)
        lines.append(f'File = "{_norm_name(path)}", "{md5}", "{sha256}";')
    text = "\n".join(lines) + "\n"
    return text, encode_index(text, seed)


def index_document(files) -> Optional[IndexDocument]:
    """The manifest ``files`` carries, parsed — ``None`` if there is none.

    A manifest that cannot be decoded or parsed raises, because that is a
    finding in itself and the caller (``vdpack index``, ``vdpack diff``) is
    supposed to report it rather than to fall back to silence.
    """
    found = index_entry(files)
    if found is None:
        return None
    return parse_index(decode_index(found[1]))


def index_report(files) -> Dict[str, object]:
    """Check a bundle's manifest against the bundle itself.

    Answers the three questions that decide whether the loader accepts a pack:
    which listed paths hold different bytes than the manifest says, which
    payloads the manifest does not mention at all, and which listed paths the
    pack no longer carries.
    """
    pairs = _pairs(files)
    by_name = {_norm_name(n).lower(): d for n, d in pairs}
    report: Dict[str, object] = {
        "present": index_entry(pairs) is not None,
        "decoded": False, "document": None, "records": 0,
        "mismatched": [], "unlisted": [], "missing": [], "error": "",
    }
    try:
        doc = index_document(pairs)
    except ValueError as exc:
        report["error"] = str(exc)
        return report
    if doc is None:
        return report
    report["decoded"] = True
    report["document"] = doc
    report["records"] = len(doc.files)
    listed = set()
    for record in doc.files:
        key = record.path.lower()
        listed.add(key)
        blob = by_name.get(key)
        if blob is None:
            report["missing"].append(record.path)
            continue
        md5, sha256 = _digests(blob)
        if md5 != record.md5 or sha256 != record.sha256:
            report["mismatched"].append(record.path)
    report["unlisted"] = sorted(n for n, _ in pairs
                                if _indexed(n) and _norm_name(n).lower()
                                not in listed)
    return report


def reindex_pack(files, seed: int = INDEX_SEED) -> List[Tuple[str, bytes]]:
    """``files`` with a manifest written for those exact bytes.

    The archive's own identity (``Name``/``UUID``/``Type``) is taken from the
    manifest it already carries when there is one, so rebuilding a mod's pack
    keeps the mod's name; otherwise a fresh one is generated.  The `index`
    entry keeps its position when the archive has one and goes to the front
    when it does not, which is where every observed pack keeps it.
    """
    pairs = _pairs(files)
    keep = None
    try:
        keep = index_document(pairs)
    except ValueError:
        keep = None
    _text, blob = build_index(pairs, keep=keep, seed=seed)
    out: List[Tuple[str, bytes]] = []
    placed = False
    for name, data in pairs:
        if _norm_name(name).lower() == INDEX_ENTRY:
            out.append((name, blob))
            placed = True
        else:
            out.append((name, data))
    if not placed:
        out.insert(0, (INDEX_ENTRY, blob))
    return out


def _pairs(files) -> List[Tuple[str, bytes]]:
    return list(files.items()) if isinstance(files, dict) else list(files)


def _norm_name(name: str) -> str:
    return name.replace("/", "\\").strip("\\")


def diff_entries(base, other) -> Dict[str, object]:
    """What separates two bundles of the same unit, entry by entry.

    ``base`` is normally the pack the game already loads and ``other`` the one
    a mod is about to ship, so the answer reads as "what did I change": the
    paths only in one of them, the paths whose payload differs, and whether
    the *order* of the shared paths moved.

    The order matters for reproducibility: ``pack_like`` rebuilds an archive
    in the source's order so a rebuild with unchanged payloads is byte for
    byte the source, and a diff that reports ``reordered`` is telling the
    modder the same thing `vdpack pack --like` enforces.  (The ``index``
    manifest itself is per path, so it does not care — see `index_report`.)

    Names are compared in archive form (backslash separated, no leading
    separator), so a folder and the pack built from it compare clean.
    """
    left = _pairs(base)
    right = _pairs(other)
    lmap = {_norm_name(n): d for n, d in left}
    rmap = {_norm_name(n): d for n, d in right}
    added = sorted(set(rmap) - set(lmap))
    removed = sorted(set(lmap) - set(rmap))
    changed = sorted(n for n in set(lmap) & set(rmap) if lmap[n] != rmap[n])
    order_left = [_norm_name(n) for n, _ in left if _norm_name(n) in rmap]
    order_right = [_norm_name(n) for n, _ in right if _norm_name(n) in lmap]
    index_left = lmap.get("index")
    index_right = rmap.get("index")
    manifest = index_report(right)
    return {
        "base": len(left),
        "other": len(right),
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": len(order_left) - len(changed),
        "reordered": order_left != order_right,
        "index_present": index_right is not None,
        "index_changed": (index_left is not None and index_right is not None
                          and index_left != index_right),
        "index_decoded": manifest["decoded"],
        "index_records": manifest["records"],
        "index_mismatched": manifest["mismatched"],
        "index_unlisted": manifest["unlisted"],
        "index_missing": manifest["missing"],
        "index_error": manifest["error"],
        "bytes": (sum(len(d) for _, d in left), sum(len(d) for _, d in right)),
    }


def shape_verdict(result: Dict[str, object]) -> List[Tuple[str, str]]:
    """Turn a :func:`diff_entries` result into the "will it load" reading.

    Three grades, all of them measured against the one pack shape with a
    runtime confirmation behind it (the Heroes-VII port, see
    ``docs/PORT_ANALYSIS_COMPARE_RU.md`` §4): **same paths, different
    payloads**.

    * ``ok``   — the archive adds nothing and drops nothing;
    * ``warn`` — a path went away (the Angel pack dropped a generated
      ``*_lod.ac`` and its battle still loaded, so removal is survivable) or
      the shared paths changed places (the `index` may be order-dependent);
    * ``bad``  — a path was added.  The port tried exactly that (six new
      entries, original `index`) and the game showed no units at all.

    A ``bad`` grade is not a claim that the pack cannot work; it is a claim
    that it is not the shape anything has been confirmed to load in.
    """
    lines: List[Tuple[str, str]] = []
    added = result.get("added") or []
    removed = result.get("removed") or []
    if added:
        lines.append((
            "bad",
            f"{len(added)} path(s) added — the only shape confirmed to load "
            f"in game is the same paths with different payloads. An added "
            f"path needs its own `File` record in `index`; "
            f"`vdpack pack --like <base> --reindex` writes one, and until it "
            f"has one the loader reports `Unindexed file '%s'` and the path "
            f"stays invisible. Renaming the file after one the pack already "
            f"carries (`texture add --name`) avoids the question."))
        for name in added:
            lines.append(("bad", f"  + {name}"))
    if removed:
        lines.append((
            "warn",
            f"{len(removed)} path(s) removed — survivable (the Angel pack "
            f"dropped a path and its battle loaded), but every reference "
            f"that names one of them now ends in a missing-file dialog."))
        for name in removed:
            lines.append(("warn", f"  - {name}"))
    if result.get("reordered"):
        lines.append((
            "warn",
            "the shared paths changed order — rebuild with "
            "`vdpack pack <folder> --like <this pack>` so the entry order and "
            "the `index` stay on the confirmed shape"))
    changed = result.get("changed") or []
    error = str(result.get("index_error") or "")
    mismatched = result.get("index_mismatched") or []
    unlisted = result.get("index_unlisted") or []
    missing = result.get("index_missing") or []

    # `index` is the mod's manifest: the file list and a MD5 + SHA-256 per
    # resource.  Two different questions live in it, and the game answered
    # them differently, so they are graded differently:
    #
    # * a payload whose bytes no longer match the recorded digest — the Angel
    #   pack replaced both of its `.g` payloads under the original `index` and
    #   its battle loaded, so a stale digest is *not* known to block anything;
    # * a path with no record at all — that is the `Unindexed file` the port
    #   hit when it added entries, and the pack showed no units.
    if result.get("index_present") and error:
        lines.append((
            "warn",
            f"the `index` entry does not decode ({error}), so the file list "
            f"and the per-file digests it carries cannot be checked at all. "
            f"`vdpack pack --like <base> --reindex` writes a readable one."))
    if mismatched:
        lines.append((
            "warn",
            f"{len(mismatched)} payload(s) no longer match the MD5/SHA-256 "
            f"`index` records for them. The one pack with a runtime "
            f"confirmation replaced both of its `.g` payloads under the "
            f"original manifest and its battle loaded, so a stale digest is "
            f"not known to block the mount — but the manifest then describes "
            f"bytes that are not in the archive. `vdpack pack --like <base> "
            f"--reindex` makes both true again."))
        for name in mismatched[:8]:
            lines.append(("warn", f"  ~ {name}"))
        if len(mismatched) > 8:
            lines.append(("warn", f"  ... and {len(mismatched) - 8} more"))
    if unlisted:
        lines.append((
            "bad",
            f"{len(unlisted)} path(s) have no `File` record in `index`. This "
            f"is the failure the port saw in game — a pack that added paths "
            f"under a manifest that did not list them showed no unit at all — "
            f"and `Unindexed file '%s'` is the loader's wording for it. "
            f"`vdpack pack --like <base> --reindex` writes the records."))
        for name in unlisted[:8]:
            lines.append(("bad", f"  + {name}"))
        if len(unlisted) > 8:
            lines.append(("bad", f"  ... and {len(unlisted) - 8} more"))
    if missing:
        lines.append((
            "warn",
            f"{len(missing)} path(s) are listed in `index` but absent from the "
            f"archive — `Not found indexed file '%s'` is the loader's answer "
            f"to that, so drop the records or carry the files."))
        for name in missing[:8]:
            lines.append(("warn", f"  - {name}"))
    if (not added and not removed and not result.get("reordered")
            and not unlisted):
        if changed and result.get("index_present") and not (error or mismatched):
            lines.append((
                "ok",
                f"same {result.get('other', 0)} path(s) in the same order, "
                f"{len(changed)} payload(s) replaced, and the `index` "
                f"manifest describes those exact bytes — the confirmed shape"))
        else:
            lines.append((
                "ok",
                f"same {result.get('other', 0)} path(s) in the same order, "
                f"{len(changed)} payload(s) replaced — the confirmed shape"))
    return lines


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
