"""The ``.char`` unit profile (a unit's identity, resources and statistics).

The retail game keeps one text profile per unit under ``Profiles/``; the
profile's ``id`` is what the character browser lists, and its ``unit_scene``
points at the ``.scene`` of the unit folder this toolkit's ``.g`` reader
already understands.  The game resolves the profile *by its ``id``*, not by
the file name — `story_myzrael_undead.char` holds ``empire_myzrael_undead``
and `undead_templar .char` (trailing space in the name) holds
``undead_templar`` — which is exactly why the Heroes-VII port could add a
browser entry by patching that one string.

Grammar (line oriented, C-style comments, ``;`` terminated)::

    // Unit Profile
    id                       "demons_beast";
    unit_scene              "resources\\\\characters\\\\demons\\\\beast\\\\…scene";
    unit_scene_lod          "…_lod.scene",10;      // extra number after it
    shadowtexdim            512,512;               // comma separated values
    atck_zone;                                     // a key with no value
    set 1 {                                        // a block
        weapon "…set1_weapon.scene";
    }
    #include                 "desc\\\\demons_beast_desc.char";

Quirks the parser has to survive, all of them present in the shipped corpus:
a statement may lose its trailing ``;`` (`leader_icon` in `elves_rod-1.char`,
`unit_scene` in every leader profile), keys come with mixed case
(`Control`, `Discount_Mod`), values may sit on either side of the terminator
(`#include "x";`), and ``/* … */`` comments span lines (`elves_rod-1.char`
keeps a whole commented-out attack zone that way).  Five of the 874 shipped
profiles also carry cp1251 bytes in a comment or a name, so decoding is
UTF-8 first and cp1251 second, and the original codec is remembered.

This module is deliberately **line preserving**: :func:`parse_char` keeps the
raw lines and records where each statement sits, and :func:`write_char`
returns them unchanged, so an untouched profile round-trips byte for byte and
:func:`set_value` can patch one string without reformatting the rest.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

#: profile keys whose value is a game resource path
RESOURCE_KEYS: Tuple[str, ...] = (
    "unit_scene", "unit_scene_lod", "icon", "subicon", "layout_icon",
    "capital_icon", "dialog_icon", "leader_icon", "fxscene", "fxcast",
    "fxscene_ko", "fxlevelup", "names",
)

#: keys a `set N { … }` block carries (one scene per equipment slot)
SET_SLOT_KEYS: Tuple[str, ...] = (
    "weapon", "feet", "body", "head", "legs", "arms", "hands",
)

_INCLUDE_RE = re.compile(r'^#include\b\s*"?([^";]*)"?')
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\b")
_BLOCK_OPEN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\b([^{]*)\{")


@dataclass
class CharStatement:
    """One ``key value…;`` line (or an ``#include``), with its source span."""

    key: str                     # original spelling, "" for a directive
    values: List[str] = field(default_factory=list)   # unquoted, split
    quoted: List[bool] = field(default_factory=list)  # value was a "string"
    line: int = 0                # 0-based line index in `CharProfile.lines`
    block: int = -1              # index into `CharProfile.blocks`, -1 = top
    directive: str = ""          # "#include" when the line is a directive

    @property
    def value(self) -> str:
        """First value, or ``""`` (a flag such as ``atck_zone;``)."""
        return self.values[0] if self.values else ""


@dataclass
class CharBlock:
    """A ``set N { … }`` block: header line, closing line, slot statements."""

    name: str                    # "set"
    argument: str                # "1", "2", …
    open_line: int
    close_line: int = -1


@dataclass
class CharProfile:
    """A parsed profile: raw lines plus the statements they contain."""

    lines: List[str] = field(default_factory=list)
    statements: List[CharStatement] = field(default_factory=list)
    blocks: List[CharBlock] = field(default_factory=list)
    encoding: str = "utf-8"
    bom: bool = False
    newline: str = "\n"
    trailing_newline: bool = True
    path: str = ""
    parse_error: str = ""

    # -- lookups -------------------------------------------------------- #
    def get_all(self, key: str) -> List[str]:
        """Every value of *key* (case-insensitive), in file order."""
        low = key.lower()
        out: List[str] = []
        for st in self.statements:
            if st.key.lower() == low:
                out.extend(st.values)
        return out

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """First value of *key* (case-insensitive)."""
        values = self.get_all(key)
        return values[0] if values else default

    def statement(self, key: str) -> Optional[CharStatement]:
        low = key.lower()
        for st in self.statements:
            if st.key.lower() == low:
                return st
        return None

    @property
    def id(self) -> Optional[str]:
        """The value the character browser lists (``id``)."""
        return self.get("id")

    @property
    def unit_scene(self) -> Optional[str]:
        return self.get("unit_scene")

    @property
    def unit_scene_lod(self) -> Optional[str]:
        return self.get("unit_scene_lod")

    @property
    def includes(self) -> List[str]:
        """Every ``#include`` path, in file order."""
        return [st.value for st in self.statements if st.directive]

    def slots(self, block: int) -> Dict[str, str]:
        """The equipment scenes of one ``set N { … }`` block."""
        return {st.key.lower(): st.value for st in self.statements
                if st.block == block}

    def keys(self) -> List[str]:
        seen: List[str] = []
        for st in self.statements:
            low = st.key.lower()
            if st.key and low not in seen:
                seen.append(low)
        return seen

    def resources(self) -> List[Tuple[str, str]]:
        """``(key, path)`` for every resource reference, blocks included.

        Only the *first* value of a statement is a path: `unit_scene_lod`
        carries a draw distance after its path (``"…_lod.scene",10``).
        """
        out: List[Tuple[str, str]] = []
        for st in self.statements:
            if st.directive:
                out.append(("#include", st.value))
                continue
            key = st.key.lower()
            if (key in RESOURCE_KEYS or key in SET_SLOT_KEYS) and st.value:
                out.append((key, st.value))
        return out

    # -- editing -------------------------------------------------------- #
    def set_value(self, key: str, value: str,
                  occurrence: int = 0) -> bool:
        """Put *value* into the first (or *occurrence*-th) *key* statement.

        The line keeps its key spelling, spacing and trailing text — only the
        content between the quotes changes (a value that was not quoted gains
        quotes).  Returns ``False`` when the key is absent.
        """
        hits = [st for st in self.statements if st.key.lower() == key.lower()]
        if len(hits) <= occurrence:
            return False
        st = hits[occurrence]
        line = self.lines[st.line]
        code = _mask_comments(line)
        m = _KEY_RE.match(code)
        if not m:
            return False
        start = m.end()
        # replace the first value token (quoted or bare) after the key
        rest = code[start:]
        tok = _VALUE_RE.search(rest)
        if not tok:
            line = line[:start] + ' "' + value + '";'
        else:
            # group 1 is the value itself; group 0 also holds the spacing
            a, b = start + tok.start(1), start + tok.end(1)
            if line[a:a + 1] == '"' and line[b - 1:b] == '"':
                line = line[:a + 1] + value + line[b - 1:]
            elif line[a:a + 1] == '"':
                line = line[:a + 1] + value + line[a + 1:]
            else:
                line = line[:a] + '"' + value + '"' + line[b:]
        self.lines[st.line] = line
        st.values[0] = value
        st.quoted = [True] + list(st.quoted[1:])
        return True

    def set_id(self, new_id: str) -> bool:
        """Rename the profile (the port's way of adding a browser entry)."""
        return self.set_value("id", new_id)


# `quoted string` or a bare run of non-separators, at the start of a value
_VALUE_RE = re.compile(r'\s*("(?:[^"\\]|\\.)*"|[^",;\s]+)')


def _mask_comments(text: str) -> str:
    """Blank out ``//`` and ``/* … */`` comments, keeping the offsets."""
    out = list(text)
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if text.startswith("//", i):
            j = i
            while j < n and text[j] not in "\r\n":
                out[j] = " "
                j += 1
            i = j
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            stop = n if end < 0 else end + 2
            for j in range(i, stop):
                if text[j] not in "\r\n":
                    out[j] = " "
            i = stop
            continue
        i += 1
    return "".join(out)


def _split_values(text: str) -> Tuple[List[str], List[bool]]:
    """Split a statement tail into values, honouring quotes and commas."""
    values: List[str] = []
    quoted: List[bool] = []
    current = ""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            if current.strip():
                values.append(current.strip())
                quoted.append(False)
                current = ""
            i += 1
            buf = []
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    buf.append(text[i:i + 2])
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                buf.append(text[i])
                i += 1
            # values stay exactly as the file spells them (the corpus mixes
            # `resources\\characters` and `resources\characters`); anything that
            # must compare or resolve a path goes through normalize_reference
            values.append("".join(buf))
            quoted.append(True)
            continue
        if ch in ",;":
            if current.strip():
                values.append(current.strip())
                quoted.append(False)
            current = ""
            i += 1
            continue
        if ch.isspace():
            if current.strip():
                values.append(current.strip())
                quoted.append(False)
            current = ""
            i += 1
            continue
        current += ch
        i += 1
    if current.strip():
        values.append(current.strip())
        quoted.append(False)
    return values, quoted


def parse_char(data) -> CharProfile:
    """Parse a ``.char`` profile from bytes (or already decoded text)."""
    prof = CharProfile()
    if isinstance(data, bytes):
        for enc in ("utf-8", "cp1251"):
            try:
                text = data.decode(enc)
                prof.encoding = enc
                break
            except UnicodeDecodeError:
                continue
        else:                                    # pragma: no cover - latin-1
            text = data.decode("latin-1")
            prof.encoding = "latin-1"
    else:
        text = data
    if text.startswith("\ufeff"):
        # a byte-order mark belongs to the file, not to the first line: keep
        # it out of the parse and put it back on the way out
        prof.bom = True
        text = text[1:]
    prof.newline = "\r\n" if "\r\n" in text else "\n"
    prof.trailing_newline = text.endswith(("\n", "\r"))
    prof.lines = text.splitlines()
    code = _mask_comments(text).splitlines()
    if len(code) < len(prof.lines):              # pragma: no cover - defensive
        code += [""] * (len(prof.lines) - len(code))

    current_block = -1
    for index, line in enumerate(code):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("}"):
            if current_block >= 0:
                prof.blocks[current_block].close_line = index
                current_block = -1
            continue
        m = _INCLUDE_RE.match(stripped)
        if m:
            values, quoted = _split_values(m.group(1).strip())
            prof.statements.append(CharStatement(
                key="#include", values=values, quoted=quoted, line=index,
                block=current_block, directive="#include"))
            continue
        m = _BLOCK_OPEN_RE.match(stripped)
        if m:
            prof.blocks.append(CharBlock(
                name=m.group(1), argument=m.group(2).strip(), open_line=index))
            current_block = len(prof.blocks) - 1
            continue
        m = _KEY_RE.match(stripped)
        if not m:
            continue                 # a comment fragment or a stray line
        key = m.group(1)
        # `stripped` lost the indentation, so shift the match back onto the
        # raw line before slicing the value tail
        lead = len(line) - len(line.lstrip())
        # the statement runs to the first `;` on this line, or to its end
        semi = line.find(";", lead + m.end())
        tail_end = len(line) if semi < 0 else semi
        tail = line[lead + m.end():tail_end]
        values, quoted = _split_values(tail)
        st = CharStatement(key=key, values=values, quoted=quoted, line=index,
                           block=current_block)
        # a statement that lost its `;` is normal in this corpus (212 of the
        # 214 root profiles lose one) and nothing needs repairing: the line
        # ended, which is all the game needs
        prof.statements.append(st)
    if current_block >= 0:
        prof.blocks[current_block].close_line = len(prof.lines) - 1
    return prof


def write_char(profile: CharProfile) -> str:
    """Render a profile back to text (byte-identical when untouched)."""
    text = profile.newline.join(profile.lines)
    if profile.trailing_newline and profile.lines:
        text += profile.newline
    return text


def encode_char(profile: CharProfile) -> bytes:
    """``write_char`` in the profile's own encoding (BOM included)."""
    text = write_char(profile)
    if profile.bom:
        text = "\ufeff" + text
    return text.encode(profile.encoding)


# --------------------------------------------------------------------------- #
#  links to the unit folders
# --------------------------------------------------------------------------- #
def normalize_reference(reference: str) -> str:
    """One spelling for a reference: lower case, single separators.

    The corpus writes the same path three ways — `resources\\characters…`,
    `resources\characters…` and `resources/characters…` — and Windows, which
    is what the engine runs on, treats repeated separators as one.  Every
    comparison therefore goes through this function, while the file keeps its
    own text.
    """
    sep = chr(92)
    text = reference.strip().strip('"')
    while sep * 2 in text:
        text = text.replace(sep * 2, sep)
    return text.replace("/", sep).lower()


def scene_basename(reference: str) -> str:
    """The file name a resource reference ends with (``\\``/``/`` agnostic)."""
    return normalize_reference(reference).rsplit(chr(92), 1)[-1]


def resource_path(reference: str, root: str) -> str:
    """Map a ``resources\\…`` reference onto *root* (the game's data dir)."""
    path = normalize_reference(reference)
    parts = [p for p in path.split("\\") if p not in ("", ".")]
    if parts and parts[0].lower() == "resources":
        parts = parts[1:]
    return os.path.join(root, *parts)


def find_profiles(folder: str, recursive: bool = True) -> List[str]:
    """Every ``.char`` under *folder*, sorted."""
    found: List[str] = []
    if recursive:
        for cur, dirs, names in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(names):
                if name.lower().endswith(".char"):
                    found.append(os.path.join(cur, name))
    else:
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".char"):
                found.append(os.path.join(folder, name))
    return found


def profile_for_scene(profiles: Iterable[str],
                      scene: str) -> Optional[Tuple[str, CharProfile]]:
    """The top-level profile that points at *scene* (a path or a file name).

    Only profiles carrying an ``id`` are candidates: the ``desc``/``stats``/
    ``sound`` fragments included by them have neither an ``id`` nor a scene.
    *scene* may also be a unit folder, in which case its own scene is picked
    (the one not marked as the low level of detail).
    """
    if os.path.isdir(scene):
        scenes = [os.path.join(scene, n) for n in sorted(os.listdir(scene))
                  if n.lower().endswith(".scene")]
        scenes.sort(key=lambda p: ("_lod" in os.path.basename(p).lower(),
                                   len(p)))
        if not scenes:
            return None
        scene = scenes[0]
    want = normalize_reference(scene).rsplit("\\", 1)[-1]
    for path in profiles:
        try:
            prof = parse_char(open(path, "rb").read())
        except OSError:                          # pragma: no cover
            continue
        if not prof.id:
            continue
        for key in ("unit_scene", "unit_scene_lod"):
            ref = prof.get(key)
            if ref and scene_basename(ref).lower() == want:
                return path, prof
    return None


class ReferenceIndex:
    """Resolve the ``resources\\…`` references of profiles against a tree.

    Two layouts have to work: the game's own ``<data>/resources/characters/…``
    and this repository's donor corpus, where the same unit sits at
    ``geo2011/<faction>/<unit>/`` with the ``resources`` prefix dropped.  The
    reference is therefore tried verbatim, then with ``resources`` removed,
    then by file name anywhere under the root.
    """

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self._by_name: Dict[str, List[str]] = {}
        self._scanned = False

    def _scan(self) -> None:
        if self._scanned:
            return
        self._scanned = True
        for cur, dirs, names in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in names:
                self._by_name.setdefault(name.lower(), []).append(
                    os.path.join(cur, name))

    def candidates(self, reference: str) -> List[str]:
        """Plausible absolute paths for *reference*, best guess first."""
        norm = normalize_reference(reference)
        parts = [p for p in norm.split(chr(92)) if p not in ("", ".")]
        if parts and parts[0] == "resources":
            parts = parts[1:]
        out: List[str] = []
        if parts:
            out.append(os.path.join(self.root, *parts))
            if parts[0] in ("characters", "textures", "fx"):
                out.append(os.path.join(self.root, *parts[1:]))
        self._scan()
        base = parts[-1] if parts else norm
        out.extend(self._by_name.get(base.lower(), []))
        seen: List[str] = []
        for path in out:
            if path not in seen:
                seen.append(path)
        return seen

    def resolve(self, reference: str) -> Optional[str]:
        """The first existing candidate, or ``None``."""
        for path in self.candidates(reference):
            if os.path.exists(path):
                return path
        return None

    def __call__(self, reference: str) -> bool:
        return self.resolve(reference) is not None


@dataclass
class ProfileReport:
    """What a profile names that is not there (``problems``) or elsewhere."""

    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.problems)


def check_profile(prof: CharProfile, exists) -> ProfileReport:
    """Check every resource reference of *prof* with *exists(path) -> bool*.

    ``exists`` receives the reference as written, so a caller can resolve it
    against a folder, an archive or nothing at all.  A reference that is not
    ``resources\\…``-relative is reported as a note: the engine mounts those
    (``$(Sounds)``, ``$(Texts)``) from its own trees.
    """
    report = ProfileReport()
    name = os.path.basename(prof.path or "profile")
    if not prof.id:
        report.problems.append(f"{name}: no `id` — the browser cannot list it")
    for key, ref in prof.resources():
        if not ref:
            continue
        if ref.startswith("$("):
            report.notes.append(f"{name}: {key} uses an engine macro ({ref})")
            continue
        if not exists(ref):
            report.problems.append(
                f"{name}: {key} -> {normalize_reference(ref)} is missing")
    return report
