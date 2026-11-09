#!/usr/bin/env python3
"""Read a Windows minidump: what crashed, in which module, over what data.

    **This is a tool for a dump of your own running game.**  A minidump is a
    memory image of a process you started, so reading it is the same act as
    attaching a debugger to your own session: nothing is bypassed, no file is
    patched, and no protection is touched.  For this repository that distinction
    matters — `docs/FORMATS.md` §6b/§6c — so this tool only ever parses a file
    the user produced.

    python3 tools/dmp_find.py DisciplesIII.dmp info
    python3 tools/dmp_find.py DisciplesIII.dmp modules --grep 'Disciples|okido'
    python3 tools/dmp_find.py DisciplesIII.dmp exception
    python3 tools/dmp_find.py DisciplesIII.dmp stack --strings
    python3 tools/dmp_find.py DisciplesIII.dmp strings --grep 'Index error'

Why it exists: the retail binaries are readable on disk except for the packer
module, but a *crash* is only visible in the process — and the useful part of a
crash is rarely the exception code.  It is which module and offset faulted (so
the disassembly of the shipped exe applies), which engine strings were live on
the stack (so the failing asset can be named), and what the engine logged before
it died (`Index error : str_not_valid_hash_for_file : <path>` is a whole
diagnosis in one line, and it is only in memory).

Only the standard library is used; `exception` additionally disassembles the
code window around the fault when `capstone` is installed.
"""
from __future__ import annotations

import argparse
import re
import struct
import sys

HEADER = struct.Struct("<4sIIIIIQ")          # sig, ver, nstreams, dirrva, ..
MEMDESC = struct.Struct("<QII")              # start, size, rva
MODULE = struct.Struct("<QIIII")             # base, size, csum, ts, namerva

STREAMS = {3: "ThreadList", 4: "ModuleList", 5: "MemoryList", 6: "Exception",
           7: "SystemInfo", 9: "Memory64List", 12: "MiscInfo",
           15: "MemoryInfoList", 16: "ThreadInfoList", 17: "HandleData",
           18: "UnloadedModuleList", 19: "MiscInfoList"}
EXCEPTIONS = {0xC0000005: "access violation", 0xC0000094: "integer divide by zero",
              0xC00000FD: "stack overflow", 0xC0000374: "heap corruption",
              0xC0000409: "stack buffer overrun / fast fail",
              0x80000003: "breakpoint", 0xC000001D: "illegal instruction"}


class Minidump:
    """A minidump, with the three views this repository needs."""

    def __init__(self, path: str):
        self.data = open(path, "rb").read()
        if len(self.data) < HEADER.size or self.data[:4] != b"MDMP":
            raise SystemExit(f"{path}: not a minidump (no MDMP signature)")
        (_sig, self.version, nstreams, dirrva, _sum,
         self.timestamp, self.flags) = HEADER.unpack_from(self.data, 0)
        self.streams = {}
        for i in range(nstreams):
            kind, size, rva = struct.unpack_from("<III", self.data,
                                                 dirrva + 12 * i)
            if size:
                self.streams[kind] = (rva, size)
        self.modules = self._modules()
        self.regions = self._regions()

    # -- streams --------------------------------------------------------- #
    def _modules(self):
        if 4 not in self.streams:
            return []
        rva, _size = self.streams[4]
        count = struct.unpack_from("<I", self.data, rva)[0]
        out = []
        for i in range(count):
            base, size, _csum, _ts, namerva = MODULE.unpack_from(
                self.data, rva + 4 + i * 108)
            length = struct.unpack_from("<I", self.data, namerva)[0]
            name = self.data[namerva + 4:namerva + 4 + length].decode(
                "utf-16-le", "replace").rstrip("\x00")
            out.append((base, size, name))
        return out

    def _regions(self):
        out = []
        if 5 in self.streams:                    # MemoryListStream
            rva, _size = self.streams[5]
            count = struct.unpack_from("<I", self.data, rva)[0]
            for i in range(count):
                start, size, data_rva = MEMDESC.unpack_from(
                    self.data, rva + 4 + i * 16)
                out.append((start, size, data_rva))
        if 9 in self.streams:                    # Memory64ListStream
            rva, _size = self.streams[9]
            count, base_rva = struct.unpack_from("<QQ", self.data, rva)
            for i in range(count):
                start, size = struct.unpack_from("<QQ", self.data,
                                                 rva + 16 + i * 16)
                out.append((start, size, base_rva))
                base_rva += size
        out.sort()
        return out

    # -- lookups --------------------------------------------------------- #
    def read(self, address: int, length: int = 4):
        """Bytes at a virtual address, or ``None`` if the dump lacks them."""
        for start, size, data_rva in self.regions:
            if start <= address < start + size:
                offset = address - start
                return self.data[data_rva + offset:
                                 data_rva + min(size, offset + length)]
        return None

    def u32(self, address: int):
        raw = self.read(address, 4)
        return struct.unpack("<I", raw)[0] if raw and len(raw) == 4 else None

    def cstring(self, address: int, limit: int = 200):
        raw = self.read(address, limit) or b""
        end = raw.find(b"\x00")
        return raw[:end if end >= 0 else len(raw)]

    def module_at(self, address: int):
        for base, size, name in self.modules:
            if base <= address < base + size:
                return base, size, name
        return None

    def name_of(self, address: int) -> str:
        hit = self.module_at(address)
        if hit is None:
            return "?"
        base, _size, name = hit
        return f"{name}+{address - base:#x} ({name.rsplit(chr(92), 1)[-1]}" \
               f" rva {address - base:#x})"

    # -- the streams a crash is read from -------------------------------- #
    def exception(self):
        if 6 not in self.streams:
            return None
        rva, _size = self.streams[6]
        tid, _align = struct.unpack_from("<II", self.data, rva)
        code, flags, _rec, address = struct.unpack_from("<IIQQ", self.data,
                                                        rva + 8)
        nparam = struct.unpack_from("<I", self.data, rva + 32)[0]
        params = struct.unpack_from("<15Q", self.data, rva + 40)[:nparam]
        ctx_size, ctx_rva = struct.unpack_from("<II", self.data,
                                               rva + 40 + 8 * 15)
        return {"tid": tid, "code": code, "flags": flags, "address": address,
                "params": params, "context": (ctx_size, ctx_rva)}

    def registers(self, ctx_rva: int, ctx_size: int):
        """The x86 CONTEXT fields this tool reports (Eip is at 0xb8)."""
        if ctx_size < 0xcc:
            return {}
        names = (("edi", 0x9c), ("esi", 0xa0), ("ebx", 0xa4), ("edx", 0xa8),
                 ("ecx", 0xac), ("eax", 0xb0), ("ebp", 0xb4), ("eip", 0xb8),
                 ("eflags", 0xc0), ("esp", 0xc4))
        return {name: struct.unpack_from("<I", self.data, ctx_rva + off)[0]
                for name, off in names}

    def thread_stacks(self):
        """``[(tid, stack_start, stack_size, eip, esp)]`` from ThreadListStream."""
        out = []
        if 3 not in self.streams:
            return out
        rva, _size = self.streams[3]
        count = struct.unpack_from("<I", self.data, rva)[0]
        for i in range(count):
            offset = rva + 4 + i * 48
            tid = struct.unpack_from("<I", self.data, offset)[0]
            start, stk_size = struct.unpack_from("<QQ", self.data, offset + 24)
            ctx_size, ctx_rva = struct.unpack_from("<II", self.data,
                                                   offset + 40)
            regs = self.registers(ctx_rva, ctx_size)
            out.append((tid, start, stk_size, regs.get("eip"),
                        regs.get("esp")))
        return out

    def strings(self, minimum: int = 5):
        """``[(address, text)]`` for every printable run in dumped memory."""
        out = []
        for start, size, data_rva in self.regions:
            blob = self.data[data_rva:data_rva + size]
            for match in re.finditer(rb"[ -~]{%d,}" % minimum, blob):
                out.append((start + match.start(),
                            match.group().decode("latin-1")))
        return out

    def window(self, address: int, before: int = 0x80,
               after: int = 0x80):
        """``(bytes, start)`` — the code around ``address`` the dump carries.

        Clipped to the range that holds the address, because a minidump
        usually carries a page or two around the fault rather than the whole
        image: asking for 128 bytes either side of 0x400011 in a dump that
        holds 0x400010..0x400020 returns those sixteen, and the caller prints
        the anchor it was given instead of pretending to have more.
        """
        for start, size, data_rva in self.regions:
            if start <= address < start + size:
                low = max(start, address - before)
                high = min(start + size, address + after)
                return (self.data[data_rva + (low - start):
                                  data_rva + (high - start)], low)
        return b"", None


# --------------------------------------------------------------------------- #
#  commands
# --------------------------------------------------------------------------- #

def _info(dump: Minidump) -> int:
    print(f"minidump  version {dump.version:#x}  streams {len(dump.streams)}  "
          f"modules {len(dump.modules)}  ranges {len(dump.regions)}")
    if 7 in dump.streams:
        rva, _size = dump.streams[7]
        arch, _lvl, _rev, ncpu, _pt, major, minor, build, plat = \
            struct.unpack_from("<HHHBBIIII", dump.data, rva)
        label = {0: "x86", 9: "x64", 5: "arm"}.get(arch, str(arch))
        print(f"system    {label}, {ncpu} cpu(s), Windows {major}.{minor} "
              f"build {build}, platform {plat}")
    exc = dump.exception()
    if exc:
        print(f"exception {exc['code']:#010x} "
              f"({EXCEPTIONS.get(exc['code'], 'unknown')}) "
              f"at {exc['address']:#010x}  thread {exc['tid']:#x}")
        regs = dump.registers(exc["context"][1], exc["context"][0])
        if regs:
            print(f"faulting  {dump.name_of(regs['eip'])}  "
                  f"eip {regs['eip']:#010x} esp {regs['esp']:#010x}")
    for kind in (21, 22):
        if kind in dump.streams:
            print(f"stream    {kind} ({STREAMS.get(kind, 'vendor')}, "
                  f"{dump.streams[kind][1]} bytes)")
    return 0


def _modules(dump: Minidump, pattern) -> int:
    for base, size, name in dump.modules:
        if pattern and not re.search(pattern, name, re.I):
            continue
        print(f"  {base:#010x} {size:9d}  {name}")
    return 0


def _exception(dump: Minidump) -> int:
    exc = dump.exception()
    if not exc:
        print("no exception stream: this is not a crash dump (or it was "
              "written from a live, healthy process)")
        return 1
    print(f"thread    {exc['tid']:#x}")
    print(f"code      {exc['code']:#010x} "
          f"({EXCEPTIONS.get(exc['code'], 'unknown')})")
    params = exc["params"]
    if exc["code"] == 0xC0000005 and params:
        access = "write" if params[0] else "read"
        print(f"access    {access} at {params[1]:#x}"
              + ("   (null pointer)" if params[1] == 0 else ""))
    regs = dump.registers(exc["context"][1], exc["context"][0])
    if not regs:
        return 0
    for name in ("eip", "esp", "ebp", "eax", "ebx", "ecx", "edx", "esi", "edi"):
        print(f"  {name:3s} {regs[name]:#010x}")
    code, start = dump.window(regs["eip"])
    if code:
        try:
            import capstone
        except ImportError:
            print(f"  code   {len(code)} byte(s) at {start:#x} "
                  f"(install capstone to disassemble)")
            return 0
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        print(f"  code   around eip {regs['eip']:#x}:")
        for ins in md.disasm(code, start):
            mark = "  <<< fault" if ins.address == regs["eip"] else ""
            print(f"    {ins.address:#010x}  {ins.mnemonic:7s} "
                  f"{ins.op_str}{mark}")
    return 0


def _stack(dump: Minidump, args) -> int:
    stacks = dump.thread_stacks()
    if not stacks:
        print("no thread list in this dump")
        return 1
    tid, start, size, eip, esp = stacks[0]
    for row in stacks:
        if args.thread is not None and row[0] == args.thread:
            tid, start, size, eip, esp = row
    exc = dump.exception()
    if args.thread is None and exc and exc["tid"] in [r[0] for r in stacks]:
        tid, start, size, eip, esp = [r for r in stacks if r[0] == exc["tid"]][0]
    print(f"thread {tid:#x}  stack {start:#010x}+{size}  "
          f"eip {eip and hex(eip)}  esp {esp and hex(esp)}")
    if esp is None:
        return 1
    raw = dump.read(esp, size if size <= (1 << 22) else (1 << 22)) or b""
    for offset in range(0, max(0, len(raw) - 3), 4):
        value = struct.unpack_from("<I", raw, offset)[0]
        hit = dump.module_at(value)
        text = dump.cstring(value, 120) if dump.read(value) else b""
        if args.strings and text and len(text) >= 5 \
                and all(32 <= byte < 127 for byte in text):
            print(f"  +{offset:#06x}  string {text.decode('latin-1')}")
        elif hit and re.search(r"\.(exe|dll)$", hit[2], re.I | 0):
            print(f"  +{offset:#06x}  code  {dump.name_of(value)}")
    return 0


def _strings(dump: Minidump, pattern, minimum: int) -> int:
    for address, text in dump.strings(minimum):
        if pattern and not re.search(pattern, text, re.I):
            continue
        print(f"  {address:#010x}  {text}")
    return 0


def _mem(dump: Minidump, address: int, length: int, text: bool) -> int:
    raw = dump.read(address, length)
    if raw is None:
        print(f"{address:#x} is not in this dump "
              f"(it carries {len(dump.regions)} ranges)")
        return 1
    if text:
        for match in re.finditer(rb"[ -~]{4,}", raw):
            print(f"  +{match.start():#05x}  {match.group().decode('latin-1')}")
        return 0
    for offset in range(0, len(raw), 16):
        chunk = raw[offset:offset + 16]
        hexed = " ".join(f"{byte:02x}" for byte in chunk)
        shown = "".join(chr(byte) if 32 <= byte < 127 else "."
                        for byte in chunk)
        print(f"  {address + offset:#010x}  {hexed:<47s} {shown}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="read a minidump of a game you are running: modules, the "
                    "exception, the live stack, and the engine's own log lines")
    parser.add_argument("dump")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="header, system, exception summary")
    mod = sub.add_parser("modules", help="loaded modules and their bases")
    mod.add_argument("--grep", default=None)
    sub.add_parser("exception", help="the fault, with code around eip")
    stk = sub.add_parser("stack", help="what the crashing thread carried")
    stk.add_argument("--thread", type=lambda s: int(s, 0), default=None)
    stk.add_argument("--strings", action="store_true",
                     help="print printable strings instead of code addresses")
    stx = sub.add_parser("strings", help="every printable run in the dump")
    stx.add_argument("--grep", default=None)
    stx.add_argument("--min", type=int, default=5)
    mem = sub.add_parser("mem", help="hexdump an address the dump carries")
    mem.add_argument("address", type=lambda s: int(s, 0))
    mem.add_argument("--len", dest="length", type=lambda s: int(s, 0),
                     default=0x100)
    mem.add_argument("--text", action="store_true")
    args = parser.parse_args(argv)

    dump = Minidump(args.dump)
    if args.cmd == "info":
        return _info(dump)
    if args.cmd == "modules":
        return _modules(dump, args.grep)
    if args.cmd == "exception":
        return _exception(dump)
    if args.cmd == "stack":
        return _stack(dump, args)
    if args.cmd == "strings":
        return _strings(dump, args.grep, args.min)
    return _mem(dump, args.address, args.length, args.text)


if __name__ == "__main__":
    sys.exit(main())
