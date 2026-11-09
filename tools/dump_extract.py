#!/usr/bin/env python3
"""Turn a Windows minidump into files a reader can actually analyse.

    python3 tools/dump_extract.py game.dmp -o out/
    python3 tools/dump_extract.py game.dmp --list
    python3 tools/dump_extract.py game.dmp --module ijaqemox --strings

Why this exists: the retail engines this toolkit talks to are wrapped in a
protector that leaves a 4.9 MB encrypted `.text` and an unbacked multi-megabyte
`.data` hole in the file on disk (see `docs/FORMATS.md` §6b).  The image exists
in clear only inside a running process, so the file on disk can never be read
statically — but the process' own memory can, and a minidump is the one artefact
that carries it without a debugger, an unpacker or a modified executable.

What comes out:

* one image per module, byte-for-byte as the process had it, written as
  ``<name>.img``;
* for a PE image, ``<name>.rebuilt`` with every section's ``PointerToRawData``
  set to its ``VirtualAddress`` — the one fix a memory image needs before a
  disassembler or `pefile` can walk it, because in memory the sections are laid
  out at their virtual addresses and the header is the only thing that still
  describes a file;
* a crash report: the exception code and address, which module owns that
  address, and the return addresses found by scanning the faulting thread's
  stack and resolving them to module+offset — enough to say *where* a unit
  kills the game without any symbols;
* ``--strings`` prints the printable runs of one module, which is how the
  loader's own names (`index`, `resources\`, `vdpack`) surface.

The minidump is parsed per the documented structures (``MINIDUMP_HEADER``,
streams 3/4/5/6/7/9) and nothing is guessed: a stream that is absent is
reported as absent, and a module whose pages the dump did not capture is
reported as partial with the byte count that survived.
"""
from __future__ import annotations

import argparse
import collections
import math
import os
import struct
import sys

SIGNATURE = b"MDMP"

STREAM_THREAD_LIST = 3
STREAM_MODULE_LIST = 4
STREAM_MEMORY_LIST = 5
STREAM_EXCEPTION = 6
STREAM_SYSTEM_INFO = 7
STREAM_MEMORY64_LIST = 9
STREAM_MEMORY_INFO_LIST = 16

_ARCH = {0: "x86", 5: "arm", 6: "ia64", 9: "amd64", 12: "arm64"}

#: x86 ``CONTEXT``: the sixteen register slots after the debug registers and
#: the floating point save area.  ``amd64`` keeps a different set.
_CONTEXT_X86 = {"edi": 0x9C, "esi": 0xA0, "ebx": 0xA4, "edx": 0xA8,
                "ecx": 0xAC, "eax": 0xB0, "ebp": 0xB4, "eip": 0xB8,
                "eflags": 0xC0, "esp": 0xC4}
_CONTEXT_X64 = {"rax": 0x78, "rcx": 0x80, "rdx": 0x88, "rbx": 0x90,
                "rsp": 0x98, "rbp": 0xA0, "rsi": 0xA8, "rdi": 0xB0,
                "r8": 0xB8, "r9": 0xC0, "r10": 0xC8, "r11": 0xD0,
                "r12": 0xD8, "r13": 0xE0, "r14": 0xE8, "r15": 0xF0,
                "rip": 0xF8}


class DumpError(Exception):
    """The file is not a minidump this parser can read."""


class Module:
    __slots__ = ("name", "base", "size", "timestamp", "checksum")

    def __init__(self, name, base, size, timestamp=0, checksum=0):
        self.name = name
        self.base = base
        self.size = size
        self.timestamp = timestamp
        self.checksum = checksum

    def __repr__(self):                                   # pragma: no cover
        return f"<Module {self.name} {self.base:#x}+{self.size:#x}>"

    @property
    def end(self):
        return self.base + self.size


class Dump:
    """A parsed minidump: modules, the captured memory, and the crash."""

    def __init__(self, path):
        self.path = path
        self.size = 0
        self.arch = "unknown"
        self.modules = []
        self.regions = []              # (start, file offset, size)
        self.blob = b""
        self.threads = []              # (tid, esp/ebp dict)
        self.exception = None          # dict
        self.streams = {}
        self.notes = []

    # -- memory ---------------------------------------------------------- #
    def region_bytes(self, start, size):
        """The captured bytes of ``[start, start+size)``, gaps left as zero.

        A minidump is a *set* of captured ranges, so a module is usually a few
        contiguous chunks with holes where no page was interesting enough to
        write down.  Zero-filling the holes keeps every offset inside the image
        meaning what the loader meant it to mean.
        """
        out = bytearray(size)
        got = 0
        for rstart, off, rsize in self.regions:
            lo = max(start, rstart)
            hi = min(start + size, rstart + rsize)
            if lo >= hi:
                continue
            dst = lo - start
            src = off + (lo - rstart)
            chunk = self.blob[src:src + (hi - lo)]
            out[dst:dst + len(chunk)] = chunk
            got += len(chunk)
        return bytes(out), got

    def module_image(self, module):
        return self.region_bytes(module.base, module.size)

    def owner(self, address):
        """Which module an address belongs to, and how far into it."""
        for module in self.modules:
            if module.base <= address < module.end:
                return module, address - module.base
        return None, None

    def summary(self):
        return {"path": self.path, "arch": self.arch, "modules": len(self.modules),
                "regions": len(self.regions), "threads": len(self.threads),
                "has_exception": self.exception is not None,
                "streams": sorted(self.streams), "notes": list(self.notes)}


def _u32(blob, off):
    return struct.unpack_from("<I", blob, off)[0]


def _u64(blob, off):
    return struct.unpack_from("<Q", blob, off)[0]


def _name_at(blob, rva):
    """A ``MINIDUMP_STRING``: a byte length followed by UTF-16LE text."""
    if not rva or rva + 4 > len(blob):
        return ""
    length = _u32(blob, rva)
    raw = blob[rva + 4:rva + 4 + length]
    return raw.decode("utf-16-le", "replace").rstrip("\x00")


def read_dump(path):
    """Parse ``path`` into a :class:`Dump`.  Raises :class:`DumpError`."""
    dump = Dump(path)
    blob = open(path, "rb").read()
    dump.blob = blob
    dump.size = len(blob)
    if len(blob) < 32 or blob[:4] != SIGNATURE:
        raise DumpError(f"{os.path.basename(path)} does not start with MDMP — "
                        f"not a Windows minidump (a user-dump prefix, a page "
                        f"file fragment or a compressed crash archive?)")
    version, count, directory = (_u32(blob, 4), _u32(blob, 8), _u32(blob, 12))
    for index in range(count):
        off = directory + index * 12
        if off + 12 > len(blob):
            dump.notes.append(f"stream directory truncated at {index}")
            break
        stream_type, data_size, data_rva = struct.unpack_from("<III", blob, off)
        dump.streams[stream_type] = (data_size, data_rva)

    if STREAM_SYSTEM_INFO in dump.streams:
        _, rva = dump.streams[STREAM_SYSTEM_INFO]
        if rva + 2 <= len(blob):
            dump.arch = _ARCH.get(struct.unpack_from("<H", blob, rva)[0],
                                  "unknown")

    # -- modules --------------------------------------------------------- #
    if STREAM_MODULE_LIST in dump.streams:
        _, rva = dump.streams[STREAM_MODULE_LIST]
        number = _u32(blob, rva)
        for index in range(number):
            off = rva + 4 + index * 108
            if off + 108 > len(blob):
                dump.notes.append(f"module list truncated at {index}")
                break
            base = _u64(blob, off)
            size = _u32(blob, off + 8)
            checksum = _u32(blob, off + 12)
            timestamp = _u32(blob, off + 16)
            name_rva = _u32(blob, off + 20)
            dump.modules.append(
                Module(_name_at(blob, name_rva), base, size, timestamp, checksum))
    dump.modules.sort(key=lambda m: m.base)

    # -- memory ---------------------------------------------------------- #
    if STREAM_MEMORY64_LIST in dump.streams:
        _, rva = dump.streams[STREAM_MEMORY64_LIST]
        number = _u64(blob, rva)
        cursor = _u64(blob, rva + 8)
        for index in range(number):
            off = rva + 16 + index * 16
            if off + 16 > len(blob):
                dump.notes.append(f"memory64 list truncated at {index}")
                break
            start = _u64(blob, off)
            length = _u64(blob, off + 8)
            if cursor + length > len(blob):
                dump.notes.append(
                    f"memory64 range {index} claims {length} bytes at "
                    f"{cursor:#x} but the file ends at {len(blob):#x}")
                length = max(0, len(blob) - cursor)
            dump.regions.append((start, cursor, length))
            cursor += length
    elif STREAM_MEMORY_LIST in dump.streams:
        _, rva = dump.streams[STREAM_MEMORY_LIST]
        number = _u32(blob, rva)
        for index in range(number):
            off = rva + 4 + index * 16
            if off + 16 > len(blob):
                break
            start = _u64(blob, off)
            length, data_rva = struct.unpack_from("<II", blob, off + 8)
            dump.regions.append((start, data_rva, length))
    dump.regions.sort(key=lambda r: r[0])

    # -- threads --------------------------------------------------------- #
    contexts = {}
    if STREAM_THREAD_LIST in dump.streams:
        _, rva = dump.streams[STREAM_THREAD_LIST]
        number = _u32(blob, rva)
        for index in range(number):
            off = rva + 4 + index * 48
            if off + 48 > len(blob):
                break
            tid = _u32(blob, off)
            context_size, context_rva = struct.unpack_from("<II", blob, off + 40)
            contexts[tid] = _read_context(blob, context_rva, context_size, dump)
            dump.threads.append((tid, contexts[tid]))

    # -- the crash ------------------------------------------------------- #
    if STREAM_EXCEPTION in dump.streams:
        _, rva = dump.streams[STREAM_EXCEPTION]
        if rva + 168 <= len(blob):
            tid = _u32(blob, rva)
            code, flags = struct.unpack_from("<II", blob, rva + 8)
            address = _u64(blob, rva + 24)
            number = _u32(blob, rva + 32)
            information = [_u64(blob, rva + 40 + i * 8)
                           for i in range(min(number, 15))]
            context_size, context_rva = struct.unpack_from("<II", blob, rva + 160)
            dump.exception = {
                "thread": tid, "code": code, "flags": flags,
                "address": address, "information": information,
                "context": contexts.get(tid) or _read_context(
                    blob, context_rva, context_size, dump),
            }
    return dump


def _read_context(blob, rva, size, dump):
    if not rva or rva + size > len(blob):
        return {}
    table = _CONTEXT_X64 if dump.arch == "amd64" else _CONTEXT_X86
    width = 8 if dump.arch == "amd64" else 4
    out = {}
    for name, offset in table.items():
        if offset + width <= size:
            out[name] = (_u64(blob, rva + offset) if width == 8
                         else _u32(blob, rva + offset))
    return out


# --------------------------------------------------------------------------- #
#  turning a memory image back into something a reader can open
# --------------------------------------------------------------------------- #

def rebuild_pe(image):
    """Lay a memory image out the way a file on disk is laid out.

    In memory a section sits at its ``VirtualAddress``; on disk it sits at its
    ``PointerToRawData``.  A dumped image is the former, so the only fix a
    disassembler needs is to point every section at its virtual address and let
    ``SizeOfRawData`` cover the virtual size.  Returns ``(bytes, notes)``, and
    the input untouched when it does not carry a PE header.
    """
    notes = []
    if len(image) < 0x40 or image[:2] != b"MZ":
        return image, ["no MZ header at the module base — the dump did not "
                       "capture the image header page"]
    pe_off = _u32(image, 0x3C)
    if pe_off + 24 > len(image) or image[pe_off:pe_off + 4] != b"PE\x00\x00":
        return image, ["no PE signature at e_lfanew — header page truncated"]
    out = bytearray(image)
    number = struct.unpack_from("<H", out, pe_off + 6)[0]
    optional = struct.unpack_from("<H", out, pe_off + 20)[0]
    table = pe_off + 24 + optional
    for index in range(number):
        off = table + index * 40
        if off + 40 > len(out):
            notes.append(f"section table truncated at {index}")
            break
        name = bytes(out[off:off + 8]).rstrip(b"\x00").decode("latin-1")
        virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
            "<IIII", out, off + 8)
        want = virtual_size or raw_size
        if want and want > raw_size:
            notes.append(f"{name}: raw size {raw_size} -> {want}")
        struct.pack_into("<I", out, off + 16, want)
        struct.pack_into("<I", out, off + 20, virtual_address)
        if raw_pointer != virtual_address:
            notes.append(f"{name}: raw pointer {raw_pointer:#x} -> "
                         f"{virtual_address:#x}")
    return bytes(out), notes


def entropy(data):
    if not data:
        return 0.0
    counts = collections.Counter(data)
    return -sum(v / len(data) * math.log2(v / len(data))
                for v in counts.values())


def printable_strings(data, minimum=6, limit=400):
    out, run = [], bytearray()
    for byte in data:
        if 32 <= byte < 127 or byte in (9,):
            run.append(byte)
        else:
            if len(run) >= minimum:
                out.append(run.decode("latin-1"))
                if len(out) >= limit:
                    return out
            run.clear()
    if len(run) >= minimum:
        out.append(run.decode("latin-1"))
    return out


def crash_report(dump, stack_dwords=256):
    """Where the process died, in the terms a modder needs.

    The exception address names the module that faulted; the faulting thread's
    stack holds the return addresses of everything that called into it, and
    resolving those to ``module+offset`` is a backtrace without symbols.
    """
    exception = dump.exception
    if not exception:
        return {"exception": None,
                "note": "the dump carries no exception stream — take the dump "
                        "while the process is stopped at the failure, or send "
                        "one written at the crash"}
    address = exception["address"]
    module, offset = dump.owner(address)
    context = exception.get("context") or {}
    report = {
        "exception": exception["code"],
        "address": address,
        "module": module.name if module else "",
        "offset": offset or 0,
        "thread": exception["thread"],
        "context": dict(context),
        "stack": [],
    }
    stack_pointer = context.get("esp") or context.get("rsp")
    if stack_pointer:
        width = 8 if dump.arch == "amd64" else 4
        raw, got = dump.region_bytes(stack_pointer, stack_dwords * width)
        for index in range(0, len(raw) - width + 1, width):
            value = (int.from_bytes(raw[index:index + width], "little"))
            owner, into = dump.owner(value)
            if owner is not None:
                report["stack"].append({"value": value, "module": owner.name,
                                        "offset": into})
        report["stack_bytes"] = got
    return report


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #

def _safe_name(name):
    base = os.path.basename(name.replace("\\", "/"))
    return base or "module"


def extract(dump, outdir, only=None, want_strings=False):
    os.makedirs(outdir, exist_ok=True)
    written = []
    for module in dump.modules:
        if only and only.lower() not in module.name.lower():
            continue
        image, got = dump.module_image(module)
        if not got:
            written.append((module, None, 0, ["no pages captured for this "
                                              "module in this dump"]))
            continue
        notes = []
        if got < module.size:
            notes.append(f"partial: {got} of {module.size} bytes captured")
        name = _safe_name(module.name)
        target = os.path.join(outdir, name + ".img")
        open(target, "wb").write(image)
        written.append((module, target, got, notes))
        rebuilt, pe_notes = rebuild_pe(image)
        if rebuilt is not image:
            rebuilt_path = os.path.join(outdir, name + ".rebuilt")
            open(rebuilt_path, "wb").write(rebuilt)
            written.append((module, rebuilt_path, len(rebuilt), pe_notes))
        if want_strings:
            body = image
            strings = printable_strings(body)
            strings_path = os.path.join(outdir, name + ".strings.txt")
            with open(strings_path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(strings) + "\n")
            written.append((module, strings_path, len(strings), []))
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="extract decrypted module images and the crash location "
                    "from a Windows minidump")
    parser.add_argument("dump", nargs="?", help="the .dmp / .mdmp file")
    parser.add_argument("-o", "--out", help="write module images here")
    parser.add_argument("--list", action="store_true",
                        help="modules and captured ranges only")
    parser.add_argument("--module", help="only this module (substring)")
    parser.add_argument("--strings", action="store_true",
                        help="also write the printable strings of each module")
    parser.add_argument("--crash", action="store_true",
                        help="print the crash report only")
    args = parser.parse_args(argv)
    if not args.dump:
        parser.error("give a dump file")
    try:
        dump = read_dump(args.dump)
    except DumpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{os.path.basename(dump.path)}  {dump.size} bytes  "
          f"{dump.arch or 'unknown arch'}  {len(dump.modules)} module(s), "
          f"{len(dump.regions)} captured range(s)")
    for module in dump.modules:
        image, got = dump.module_image(module)
        flag = "" if got >= module.size else f"  partial {got}/{module.size}"
        print(f"  {module.base:#010x}  {module.size:9d}  "
              f"{entropy(image[:min(len(image), 1 << 20)]):5.2f}  "
              f"{module.name}{flag}")
    if dump.notes:
        for note in dump.notes:
            print(f"  note: {note}")
    # the crash is why the dump was taken, so it always leads
    report = crash_report(dump)
    if report["exception"] is None:
        print(f"  {report['note']}")
    else:
        where = (f"{report['module']}+{report['offset']:#x}"
                 if report["module"] else "an unmapped address")
        print(f"\ncrash: exception {report['exception']:#010x} at "
              f"{report['address']:#010x} in {where}  (thread "
              f"{report['thread']})")
        for frame in report["stack"][:12]:
            print(f"  stack  {frame['value']:#010x}  {frame['module']}"
                  f"+{frame['offset']:#x}")
    if args.out:
        written = extract(dump, args.out, only=args.module,
                          want_strings=args.strings)
        if not written:
            print("  nothing captured for the modules you asked for")
        for module, target, size, notes in written:
            if target is None:
                print(f"  {module.name}: {notes[0]}")
                continue
            print(f"  wrote {os.path.relpath(target, args.out)}  {size} bytes")
            for note in notes:
                print(f"    {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
