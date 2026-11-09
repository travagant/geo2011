"""Studio-side memory fixes for AstraForge 0.6.12 — applied to the studio, not to the bridge.

The bridge (D:\\geo2011) talks to the studio; this package changes the studio itself. Four
edits, all of them about the same failure: Windows refusing memory with «os error 1455:
the paging file is too small» while RAM still looks free.

1. `engine/astra_engine/device.py` — the page-file message now carries the numbers (RAM,
   page file, commit left) instead of only «close ComfyUI and play fewer games».
2. `engine/astra_engine/engine.py` — `keep_ram()` no longer decides by total RAM alone.
   On a 32 GB PC it kept SDXL + MV-Adapter (10 GB) in memory after every job, so the next
   heavy task started halfway to the commit limit. Now the free commit has to allow it.
3. `engine/astra_engine/server.py` — `/system_stats` reports `commit_free` / `commit_limit`,
   so the studio can ask before a job instead of after it.
4. `server/views.mjs` — «Ракурсы» check the free commit before handing the job over and
   answer 409 with the shortfall, instead of failing inside the engine a second later.
5. `server/runtime.mjs` — `ASTRA_COMFY_LOWVRAM=0|auto` chooses ComfyUI's memory mode
   without editing the file (default unchanged: `--lowvram --reserve-vram 1`).

Every patch is idempotent: the replacement carries a marker, and a second run reports
«уже исправлено». The first change to a file keeps a `.bak` copy next to it, and
`--revert` restores every file from that copy. A patch whose anchor is missing is
reported as skipped — the files differ between studio versions, and mangling them is
worse than doing nothing.

Usage:
    python update/memory_patch.py --studio D:\\AstraForge          # apply
    python update/memory_patch.py --studio D:\\AstraForge --check  # show, change nothing
    python update/memory_patch.py --studio D:\\AstraForge --revert # restore the .bak files

Exit codes: 0 everything applied, 1 some patches skipped (another studio version),
2 the studio was not found or nothing could be read.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STUDIO_MARKERS = ("server/views.mjs", "engine/astra_engine/device.py")


class Patch:
    """One named fix: a marker that proves it is already in place, and anchor→replacement."""

    def __init__(self, label: str, marker: str, anchor: str, replacement: str):
        self.label, self.marker = label, marker
        self.anchor, self.replacement = anchor, replacement

    def apply(self, text: str) -> tuple[str, str]:
        if self.marker in text:
            return text, "уже исправлено"
        if self.anchor not in text:
            return text, "пропущено (нет такой строки — другая версия студии)"
        return text.replace(self.anchor, self.replacement, 1), "исправлено"


COMMIT_REPORT = '''def commit_report():
    """Numbers behind the page-file message: RAM, page file and what is left of commit.

    «os error 1455» names the page file while Task Manager still shows RAM free: Windows
    charges every allocation against RAM + page file. The figures belong in the message,
    otherwise the advice is «close something» with no way to check it.
    """
    r = ram()
    total, limit, free = r.get('total', 0), r.get('commit_limit', 0), r.get('commit_free', 0)
    if not total:
        return ''
    page = max(0, limit - total)
    return (' Сейчас: RAM %.1f ГБ, файл подкачки %.1f ГБ, свободно %.1f ГБ памяти;'
            ' для задач с моделями в RAM файл подкачки нужен не меньше 16 ГБ.'
            % (total / 1024 ** 3, page / 1024 ** 3, free / 1024 ** 3))


'''

REQUIRE_RAM = '''  /** «os error 1455» arrives from the engine after the job started: ask for the memory first. */
  const RAM_NEED_MB = 10093 + 3072;   // SDXL + MV-Adapter + the 3 GB the engine keeps for the system
  async function requireRam(url, provider) {
    if (provider !== 'mvadapter' || !url) return;
    let sys = null;
    try {
      const res = await fetch(url + '/system_stats', { signal: AbortSignal.timeout(2500) });
      if (res.ok) sys = (await res.json())?.system || null;
    } catch { return; }                 // no answer: the job will report its own error
    const freeMb = Math.floor((sys?.commit_free ?? sys?.ram_free ?? 0) / 1048576);
    // commit_free is the honest figure; ComfyUI only reports free RAM, and free RAM
    // alone is a weak signal — refuse only when it is small outright.
    const needMb = sys?.commit_free ? RAM_NEED_MB : 6144;
    if (!sys || !freeMb || freeMb >= needMb) return;
    throw httpError('Мало свободной памяти для ракурсов: нужно ' + Math.ceil(RAM_NEED_MB / 1024) +
      ' ГБ, свободно ' + Math.floor(freeMb / 1024) + ' ГБ. Выгрузите модель из Ollama (ollama stop) и повторите;' +
      ' если повторяется — увеличьте файл подкачки: Параметры → Система → О системе → Дополнительные' +
      ' параметры системы → Быстродействие → Дополнительно → Виртуальная память.', 409);
  }

'''

MEMORY_ARGS = ''' // 11 GB (GTX 1080 Ti) keeps SDXL next to the desktop: --lowvram then pushes every weight
 // through RAM on each step, which is where «os error 1455» peaks come from. The default is
 // unchanged; ASTRA_COMFY_LOWVRAM=0 (normalvram, reserve 0.5) or =auto (lowvram, 0.5) changes it.
 const memoryArgs=()=>{
   const mode=(process.env.ASTRA_COMFY_LOWVRAM||'').trim();
   if(mode==='0')return ['--normalvram','--reserve-vram','0.5'];
   if(mode==='auto')return ['--lowvram','--reserve-vram','0.5'];
   return ['--lowvram','--reserve-vram','1'];
 };
'''

PATCHES = {
    "engine/astra_engine/device.py": (
        Patch(
            "commit limit читается из Windows",
            "'commit_limit': int(st.ullTotalPageFile)",
            "return {'total': int(st.ullTotalPhys), 'free': int(st.ullAvailPhys), "
            "'commit_free': int(st.ullAvailPageFile)}",
            "return {'total': int(st.ullTotalPhys), 'free': int(st.ullAvailPhys), "
            "'commit_free': int(st.ullAvailPageFile),\n                    "
            "'commit_limit': int(st.ullTotalPageFile)}",
        ),
        Patch(
            "подсказка про подкачку печатает цифры",
            "def commit_report():",
            "def pagefile_hint(text):",
            COMMIT_REPORT + "def pagefile_hint(text):",
        ),
        Patch(
            "цифры дописываются к тексту ошибки 1455",
            "компьютер.' + commit_report())",
            "'Виртуальная память) и перезагрузите компьютер.')",
            "'Виртуальная память) и перезагрузите компьютер.' + commit_report())",
        ),
    ),
    "engine/astra_engine/engine.py": (
        Patch(
            "keep_ram учитывает свободный commit, а не только объём RAM",
            "devmod.ram_room() >= 12 * 1024 ** 3",
            "return devmod.ram()['total'] >= 32 * 1024 ** 3",
            "# 32 GB alone is not enough: SDXL and MV-Adapter hold 10 GB while Ollama adds 7,\n"
            "        # and the next job then starts with «os error 1455».\n"
            "        return devmod.ram()['total'] >= 32 * 1024 ** 3 and devmod.ram_room() >= 12 * 1024 ** 3",
        ),
    ),
    "engine/astra_engine/server.py": (
        Patch(
            "движок отдаёт свободный commit в /system_stats",
            "'commit_free': ram.get('commit_free')",
            "'ram_free': ram['free'], 'comfyui_version': None,",
            "'ram_free': ram['free'], 'commit_free': ram.get('commit_free'), "
            "'commit_limit': ram.get('commit_limit'), 'comfyui_version': None,",
        ),
    ),
    "server/views.mjs": (
        Patch(
            "ракурсы проверяют память до старта",
            "async function requireRam(url, provider)",
            "  async function startGenerate(id, input) {",
            REQUIRE_RAM + "  async function startGenerate(id, input) {",
        ),
        Patch(
            "проверка вызывается перед генерацией",
            "await requireRam(gen.url, opts.provider);",
            "        await runner.views_generate({",
            "        await requireRam(gen.url, opts.provider);\n        await runner.views_generate({",
        ),
    ),
    "server/runtime.mjs": (
        Patch(
            "режим памяти ComfyUI выбирается переменной",
            "const memoryArgs=()=>{",
            " const args=[path.join(comfy,'main.py')",
            MEMORY_ARGS + " const args=[path.join(comfy,'main.py')",
        ),
        Patch(
            "аргументы lowvram берутся из memoryArgs()",
            "'ComfyUI-GGUF',...memoryArgs()];",
            "'ComfyUI-GGUF','--lowvram','--reserve-vram','1'];",
            "'ComfyUI-GGUF',...memoryArgs()];",
        ),
    ),
}


# --------------------------------------------------------------------------- #
#  files
# --------------------------------------------------------------------------- #
def read_text(path: Path) -> tuple[str, bool, bool]:
    """(text with \\n, was CRLF, had BOM) — the file is written back exactly as it was."""
    raw = path.read_bytes()
    return raw.decode("utf-8-sig").replace("\r\n", "\n"), b"\r\n" in raw, raw.startswith(b"\xef\xbb\xbf")


def write_text(path: Path, text: str, crlf: bool, bom: bool) -> None:
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))


def patch_file(path: Path, patches, say=print, dry=False) -> tuple[int, int, bool]:
    """Apply patches to one file. Returns (applied, skipped, changed). Never writes in dry mode."""
    try:
        text, crlf, bom = read_text(path)
    except OSError as exc:
        say(f"  {path.name}: не читается ({exc})")
        return 0, len(patches), False
    changed, applied, skipped = False, 0, 0
    for patch in patches:
        new_text, verdict = patch.apply(text)
        say(f"  · {patch.label}: {verdict}")
        if verdict == "исправлено":
            changed, applied = True, applied + 1
        elif verdict.startswith("пропущено"):
            skipped += 1
        else:
            applied += 1
        text = new_text
    if changed and not dry:
        backup = path.with_name(path.name + ".bak")
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
            say(f"  копия: {backup.name}")
        write_text(path, text, crlf, bom)
    return applied, skipped, changed


def patch_studio(studio: str, say=print, dry=False) -> int:
    root = Path(studio)
    if not root.is_dir():
        say(f"студия не найдена: {studio}")
        return 2
    say(f"студия : {root}")
    version = studio_version(root)
    if version:
        say(f"версия : {version}" + ("" if version.startswith("0.6") else "   (правки писались под 0.6.12)"))
    total_applied, total_skipped, changed_files, touched = 0, 0, 0, 0
    for relative, patches in PATCHES.items():
        path = root / relative
        if not path.is_file():
            say(f"{relative}: нет такого файла — пропускаю")
            total_skipped += len(patches)
            continue
        touched += 1
        say(f"{relative}")
        applied, skipped, changed = patch_file(path, patches, say, dry=dry)
        total_applied += applied
        total_skipped += skipped
        changed_files += 1 if changed else 0
    if not touched:
        say("ни один файл студии не найден — это не папка AstraForge")
        return 2
    say("")
    if dry:
        say(f"проверка: применится {total_applied}, пропустится {total_skipped} (ничего не изменено)")
        return 0 if total_applied else 1
    if not changed_files and not total_skipped:
        say("всё уже исправлено, менять нечего")
        return 0
    say(f"итого: исправлено {total_applied} (изменено файлов {changed_files}), пропущено {total_skipped}")
    if total_skipped:
        say("пропущенные — не ошибка: в этой версии студии строки выглядят иначе.")
        say("Пришли этот отчёт, и я добавлю якоря под твою версию.")
    return 0 if not total_skipped else 1


def revert_studio(studio: str, say=print) -> int:
    root = Path(studio)
    if not root.is_dir():
        say(f"студия не найдена: {studio}")
        return 2
    restored = 0
    for relative in PATCHES:
        path = root / relative
        backup = path.with_name(path.name + ".bak")
        if not backup.is_file():
            continue
        path.write_bytes(backup.read_bytes())
        backup.unlink()
        say(f"  восстановлен: {relative}")
        restored += 1
    if not restored:
        say("нечего откатывать: копий .bak нет (правка не применялась или уже откатана)")
        return 1
    say(f"итого: восстановлено файлов {restored}")
    return 0


def studio_version(root: Path) -> str:
    try:
        return str(json.loads((root / "package.json").read_text(encoding="utf-8-sig")).get("version", ""))
    except (OSError, ValueError):
        return ""


def find_studio(start: str = "") -> str:
    """The studio folder: the one given, else the usual drives."""
    for base in (start, r"D:\AstraForge", r"C:\AstraForge", r"E:\AstraForge"):
        if not base:
            continue
        path = Path(base)
        if all((path / m).is_file() for m in STUDIO_MARKERS):
            return str(path)
    return ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Правки памяти для студии AstraForge 0.6.12")
    parser.add_argument("--studio", default="", help="папка студии (иначе ищется на C:/D:/E:)")
    parser.add_argument("--check", action="store_true", help="только показать, ничего не менять")
    parser.add_argument("--revert", action="store_true", help="вернуть файлы из .bak")
    args = parser.parse_args(argv)

    studio = args.studio or find_studio()
    if not studio:
        print("Студия не найдена. Укажите папку: --studio D:\\AstraForge")
        return 2
    print()
    if args.revert:
        return revert_studio(studio)
    code = patch_studio(studio, dry=args.check)
    if not args.check and code in (0, 1):
        print()
        print("Готово. Перезапустите студию: движок читает настройки при старте.")
    return code


if __name__ == "__main__":
    sys.exit(main())
