"""Reader / writer for the Disciples 3 sound-alias file (`.alias`).

An `.alias` binds one event name (the first argument of an `.ac` ``event2``
line) to one or more sound entries::

    // alias configuration file
    //  ...

    alias "Attack00" {
    \tsound 100, "$(Sounds)\\clothes\\cloth\\cloth_02_03.wav", 100, 3;
    }

* the optional ``//`` comment header documents the format and is preserved
  verbatim (236 of the 1300 bundled files ship without one);
* each ``sound`` entry is ``<use chance>, "<file>", <play chance>, <flags>``
  with the path possibly carrying a ``$(Sounds)``-style macro prefix;
* 87 of the bundled files have an **empty** block — the event exists but is
  muted — and they must round-trip as empty, not be dropped;
* every bundled entry re-renders byte-for-byte from the parsed values, so
  ``parse_alias`` / ``write_alias`` are safe for reverse-export round trips.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

_SOUND_RE = re.compile(
    r'^\tsound (\d+), "([^"]*)", (\d+), ([0-9]+|0[xX][0-9a-fA-F]+);\s*$')
_ALIAS_OPEN_RE = re.compile(r'^alias "([^"]*)" \{$')


@dataclass
class SoundRef:
    """One ``sound <use>, "<file>", <play>, <flags>;`` entry."""

    use: int            # chance the entry is picked when the event fires
    path: str           # wav path, may start with a $(Macro) prefix
    play: int           # chance the picked entry is actually played
    flags: int          # bit field: bit0 enabled, bit1 sync to anim speed


@dataclass
class AliasDoc:
    """A parsed `.alias`: one event name plus its sound entries."""

    name: str
    sounds: List[SoundRef] = field(default_factory=list)
    # verbatim text before the `alias` block (the // comment header);
    # re-emitted as-is so a round trip keeps the file self-documenting
    preamble: str = ""
    # codec the source bytes were decoded with ("utf-8" for everything in the
    # corpus except Craken's CP1251 `ттт.alias`); write_alias_bytes needs it
    # to re-encode losslessly
    encoding: str = "utf-8"

    def paths(self) -> List[str]:
        """The sound file paths, in entry order."""
        return [s.path for s in self.sounds]


def parse_alias(text: str) -> AliasDoc:
    """Parse an `.alias` document.

    Raises ``ValueError`` on malformed input (no ``alias`` block, a sound
    line that does not match the canonical layout, unclosed block).
    """
    lines = text.split("\n")
    open_idx = next((i for i, ln in enumerate(lines)
                     if _ALIAS_OPEN_RE.match(ln)), None)
    if open_idx is None:
        raise ValueError("no 'alias \"<name>\" {' block found")
    opened = _ALIAS_OPEN_RE.match(lines[open_idx])
    assert opened is not None  # narrow for the type checker
    name = opened.group(1)
    doc = AliasDoc(name=name, preamble="\n".join(lines[:open_idx]))
    closed = False
    for lineno, line in enumerate(lines[open_idx + 1:], open_idx + 2):
        if line == "}":
            closed = True
            break
        m = _SOUND_RE.match(line)
        if m is None:
            raise ValueError(f"line {lineno}: malformed sound entry "
                             f"{line.strip()!r}")
        doc.sounds.append(SoundRef(use=int(m.group(1)), path=m.group(2),
                                   play=int(m.group(3)),
                                   flags=int(m.group(4), 0)))
    if not closed:
        raise ValueError(f"unterminated alias block {name!r}")
    return doc


def parse_alias_bytes(data: bytes) -> AliasDoc:
    """Decode `.alias` bytes and parse them, remembering the codec.

    The bundled corpus is ASCII/UTF-8 except one CP1251 file
    (Craken's `ттт.alias`, Cyrillic event name and header), so decoding
    tries UTF-8 first and falls back to CP1251, then Latin-1.
    """
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        doc = parse_alias(text)
        doc.encoding = enc
        return doc
    raise ValueError("not decodable as utf-8, cp1251 or latin-1")


def write_alias(doc: AliasDoc) -> str:
    """Render an :class:`AliasDoc` back to `.alias` text."""
    out = (doc.preamble + "\n") if doc.preamble else ""
    out += f'alias "{doc.name}" {{\n'
    for s in doc.sounds:
        out += f'\tsound {s.use}, "{s.path}", {s.play}, {s.flags};\n'
    out += "}\n"
    return out


def write_alias_bytes(doc: AliasDoc) -> bytes:
    """Render and encode with the codec :func:`parse_alias_bytes` picked."""
    return write_alias(doc).encode(doc.encoding)
