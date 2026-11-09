#!/usr/bin/env python3
"""Small static-analysis bench for the retail Disciples III binaries.

    python3 tools/pe_find.py DisciplesIII.exe strings --grep 'wrong index'
    python3 tools/pe_find.py DisciplesIII.exe xrefs 0x8785f8
    python3 tools/pe_find.py DisciplesIII.exe disasm 0x5b1234 --count 60
    python3 tools/pe_find.py DisciplesIII.exe around 0x5b1234 --count 40

Why it exists: `DisciplesIII.exe` ships with its code and strings in clear
(entropy 6.54 in `.text`, 5.78 in `.rdata` — see `docs/FORMATS.md` §6b), so the
loader's own answers are reachable without a debugger.  What is *not* reachable
by eye is the linkage: which of the 24 277 strings a function actually uses,
and what the code around that use does.  This resolves a string to its address,
an address to the `push`/`mov` sites that reference it (the binary is a PE32
with absolute addressing, so a `.rdata` address appears in `.text` as a 4-byte
immediate), and an address to a disassembly with every immediate annotated with
the string or function it points at.

Only the pure-Python `pefile` and `capstone` packages are needed; both are
optional for the rest of the repository, and the tool says so instead of
failing with an ImportError deep in a traceback.
"""
from __future__ import annotations

import argparse
import re
import struct
import sys


def _need(module, hint):
    try:
        return __import__(module)
    except ImportError:
        raise SystemExit(f"this tool needs `{module}`: {hint}")


class Binary:
    """A PE image with the two address maps and a string table."""

    def __init__(self, path):
        pefile = _need("pefile", "pip install pefile")
        self.path = path
        self.pe = pefile.PE(path, fast_load=True)
        self.data = open(path, "rb").read()
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.sections = [(s.Name.rstrip(b"\x00").decode("latin-1"),
                          s.VirtualAddress, s.Misc_VirtualSize,
                          s.PointerToRawData, s.SizeOfRawData)
                         for s in self.pe.sections]
        self._strings = None

    # -- address maps ---------------------------------------------------- #
    def section_of(self, va):
        rva = va - self.base
        for name, virtual, vsize, raw, rsize in self.sections:
            if virtual <= rva < virtual + max(vsize, rsize):
                return name, raw + (rva - virtual)
        return None, None

    def code_sections(self):
        """Sections that hold code, by characteristic bit 0x20000000."""
        out = []
        for section in self.pe.sections:
            if section.Characteristics & 0x20000000:
                out.append(section.Name.rstrip(b"\x00").decode("latin-1"))
        return out

    # -- strings --------------------------------------------------------- #
    def strings(self, minimum=5):
        if self._strings is not None and minimum >= 5:
            return self._strings
        found = []
        for match in re.finditer(rb"[ -~]{%d,}" % minimum, self.data):
            name, offset = self.section_of_off(match.start())
            if offset is None:
                continue
            found.append((self.base + offset, match.group().decode("latin-1")))
        if minimum >= 5:
            self._strings = found
        return found

    def section_of_off(self, file_offset):
        for name, virtual, vsize, raw, rsize in self.sections:
            if raw <= file_offset < raw + rsize:
                return name, virtual + (file_offset - raw)
        return None, None

    def string_at(self, va):
        name, offset = self.section_of(va)
        if offset is None:
            return ""
        end = self.data.find(b"\x00", offset)
        raw = self.data[offset:end if end >= 0 else offset + 64]
        return raw.decode("latin-1") if all(32 <= b < 127 for b in raw) else ""

    # -- references ------------------------------------------------------ #
    def xrefs(self, va, width=4):
        """Where ``va`` appears as a 32-bit immediate inside a code section.

        Absolute addressing means `push 0x8785f8` and `mov eax, 0x8785f8` both
        leave the address in the instruction stream, so scanning the code
        sections for the little-endian dword finds every site.  Each hit is
        reported with the opcode byte that precedes it, which is what tells a
        `push` (0x68) from a `mov` (0xB8..0xBF).
        """
        needle = struct.pack("<I", va & 0xFFFFFFFF)
        hits = []
        for name, virtual, vsize, raw, rsize in self.sections:
            if name not in self.code_sections():
                continue
            start = raw
            end = raw + max(vsize, rsize)
            position = self.data.find(needle, start, end)
            while position >= 0:
                opcode = self.data[position - 1] if position else None
                hits.append({
                    "va": self.base + virtual + (position - raw),
                    "opcode": opcode,
                    "kind": ("push imm32" if opcode == 0x68 else
                             "mov r32, imm32" if opcode is not None and
                             0xB8 <= opcode <= 0xBF else "other"),
                })
                position = self.data.find(needle, position + 1, end)
        return hits

    # -- disassembly ----------------------------------------------------- #
    def disasm(self, va, count=40, length=None):
        capstone = _need("capstone", "pip install capstone")
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        md.detail = False
        name, offset = self.section_of(va)
        if offset is None:
            return [f"{va:#x}: not inside any section"]
        blob = self.data[offset:offset + (length or 16 * count)]
        lines = []
        for insn in md.disasm(blob, va):
            annotation = self._annotate(insn)
            lines.append(f"{insn.address:#010x}  {insn.mnemonic:<8s} "
                         f"{insn.op_str}{annotation}")
            if len(lines) >= count:
                break
        return lines

    def _annotate(self, insn):
        """" ; \"the string\"" when an operand is an address of a string."""
        out = []
        for token in re.findall(r"0x[0-9a-f]+", insn.op_str):
            try:
                va = int(token, 16)
            except ValueError:
                continue
            text = self.string_at(va)
            if len(text) >= 4:
                out.append(f'"{text[:60]}"')
                continue
            name, offset = self.section_of(va)
            if offset is not None:
                out.append(f"{name}+{va - (self.base + [s[1] for s in self.sections if s[0] == name][0]):#x}")
        return ("  ; " + " | ".join(out)) if out else ""

    def function_start(self, va, limit=0x4000):
        """Walk back over the alignment and the prologue to a plausible start.

        Not a real CFG: the usual `push ebp / mov ebp, esp` or a run of `int3`
        padding immediately before the address is what identifies the function
        head, and that is enough to read a routine by hand.
        """
        _, offset = self.section_of(va)
        if offset is None:
            return va
        start = offset
        for back in range(1, min(limit, offset)):
            position = offset - back
            if self.data[position] == 0xCC and self.data[position - 1] == 0xCC:
                name, roff = self.section_of_off(position + 1)
                return self.base + roff
            if (self.data[position:position + 3] == b"\x55\x8b\xec" and
                    self.data[position - 1] in (0xCC, 0xC3, 0x90)):
                name, roff = self.section_of_off(position)
                return self.base + roff
        return va


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("binary")
    parser.add_argument("command", choices=("strings", "xrefs", "disasm",
                                            "around", "sections", "info"))
    parser.add_argument("address", nargs="?", help="hex address or a decimal")
    parser.add_argument("--grep", help="regular expression for `strings`")
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--min", type=int, default=5, help="minimum string length")
    parser.add_argument("--context", type=int, default=12,
                        help="lines before the address for `around`")
    args = parser.parse_args(argv)
    binary = Binary(args.binary)

    if args.command == "info":
        print(f"{binary.path}: base {binary.base:#x}, "
              f"{len(binary.sections)} sections, {len(binary.strings(args.min))} "
              f"strings >= {args.min}")
        for name, virtual, vsize, raw, rsize in binary.sections:
            print(f"  {name:10s} VA {binary.base + virtual:#010x} "
                  f"vsize {vsize:9d} raw {rsize:9d} @{raw:#010x}")
        return 0
    if args.command == "sections":
        print("\n".join(binary.code_sections()))
        return 0
    if args.command == "strings":
        pattern = re.compile(args.grep, re.I) if args.grep else None
        for va, text in binary.strings(args.min):
            if pattern is None or pattern.search(text):
                print(f"{va:#010x}  {text}")
        return 0
    if not args.address:
        parser.error("this command needs an address")
    va = int(args.address, 16) if args.address.lower().startswith("0x") \
        else int(args.address)
    if args.command == "xrefs":
        hits = binary.xrefs(va)
        text = binary.string_at(va)
        print(f"{va:#010x}  {text!r}  ({len(hits)} reference(s))")
        for hit in hits:
            print(f"  {hit['va']:#010x}  {hit['kind']}")
        return 0
    if args.command == "disasm":
        print("\n".join(binary.disasm(va, args.count)))
        return 0
    start = binary.function_start(va)
    print(f"function head guess: {start:#x}  (from {va:#x})\n")
    print("\n".join(binary.disasm(start, args.count)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
