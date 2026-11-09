"""Why Windows refused memory: «Файл подкачки слишком мал… (os error 1455)».

Windows does not hand out RAM directly: every allocation is charged against the
*commit limit*, which is RAM plus the page file. A model is committed before it is
read from disk, so a 16 GB machine with a 2 GB page file runs out at 18 GB while
Task Manager still shows several GB of RAM free — and the error says «page file»,
not «RAM».

The studio's own engine plans with the same number (`engine/astra_engine/device.py`,
ram_room()): a new model may take min(free RAM, commit free), and it wants the model
plus 3 GB for the system to stay free. «Ракурсы» with MV-Adapter ask for SDXL and the
adapter together — 10.5 GB in RAM before the card sees anything.

This script prints the numbers and what to free: run it, and it tells you whether
«Ракурсы» can start now, and if not, how much page file to add.

Usage:
    python bridge/ram_check.py                  # verdict for «Ракурсы» (MV-Adapter)
    python bridge/ram_check.py --need 14000     # your own figure, MB of RAM a task loads
    python bridge/ram_check.py --json           # the same numbers for a script
    python bridge/ram_check.py --no-ollama      # skip the «ollama ps» call

Exit codes: 0 — the task fits as things are; 1 — it does not (advice is printed);
2 — not Windows, nothing to measure.
"""
import json
import os
import shutil
import subprocess
import sys

MB = 1024 * 1024
GB = 1024 * MB

# What a task loads into RAM, in MiB, counted from the files themselves: the model
# manager asks for the sum of the weight sizes (engine/plugins/mvadapter/nodes.py
# passes `ram=stat.st_size + astat.st_size`).
#   sd_xl_base_1.0.safetensors        6 979 975 392 B = 6657 MiB (6.98 GB on Hugging Face)
#   mvadapter_i2mv_sdxl.safetensors   3 602 537 816 B = 3436 MiB (installer/mvadapter/manifest.json)
NEED_MB = {
    'views': 10093,      # MV-Adapter «Ракурсы»: SDXL + the adapter, together
    'sdxl': 6657,        # one SDXL checkpoint
    'mvadapter': 3436,   # the adapter on its own
}
# The engine keeps this much for the system on top of the model (models.py: ram + 3 GB).
RESERVE_MB = 3072


def gib(n_bytes):
    """'10.5 ГБ' — one decimal, the way Task Manager shows it."""
    return '%.1f ГБ' % (float(n_bytes) / GB)


def memory_status(reader=None):
    """{'total','free','commit_limit','commit_free'} in bytes, or None off Windows.

    commit_limit is RAM + page file, commit_free is what Windows still allows to be
    committed — the number that runs out in «os error 1455».
    """
    if reader is not None:
        return reader()
    if not hasattr(__import__('ctypes'), 'windll'):
        return None
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    except Exception:
        return None
    if not ok:
        return None
    return {'total': int(st.ullTotalPhys), 'free': int(st.ullAvailPhys),
            'commit_limit': int(st.ullTotalPageFile), 'commit_free': int(st.ullAvailPageFile)}


def pagefile_bytes(status):
    """How much of the commit limit is the page file (may be 0: Windows managed, or
    none at all — a machine without a page file is exactly where 1455 starts)."""
    return max(0, int(status.get('commit_limit', 0)) - int(status.get('total', 0)))


def verdict(status, need_mb, reserve_mb=RESERVE_MB):
    """Whether a task that loads need_mb fits, and what to change when it does not.

    {'ok','need','have','short','pagefile','pagefile_after','advice':[...]}
    need and have are MB; short is MB that must come from somewhere.
    """
    need = int(need_mb) + int(reserve_mb)
    have = int(status.get('commit_free', 0)) // MB
    pagefile = pagefile_bytes(status) // MB
    short = max(0, need - have)
    out = {'ok': short == 0, 'need': need, 'have': have, 'short': short,
           'pagefile': pagefile, 'pagefile_after': pagefile, 'advice': []}
    if out['ok']:
        return out
    # To free MB by page file alone the file has to grow by exactly the shortfall;
    # round up to whole gigabytes so the number is something a person can type.
    want = pagefile + short
    out['pagefile_after'] = int((want + 1023) // 1024 * 1024)
    out['advice'].append(
        'подкачка: сейчас %s, поставьте минимум %s (файл на диске, где есть место)'
        % (gib(pagefile * MB), gib(out['pagefile_after'] * MB)))
    out['advice'].append(
        'или закройте то, что держит память: Ollama с загруженной моделью, ComfyUI, браузер')
    out['advice'].append(
        'после смены подкачки Windows попросит перезагрузку — без неё размер не меняется')
    return out


def disks(paths=None):
    """[{'path','free'}] for the drives that matter: the system one and where the studio is."""
    paths = paths or ['C:\\', 'D:\\', 'E:\\']
    out = []
    for p in paths:
        try:
            if os.path.exists(p):
                out.append({'path': p, 'free': shutil.disk_usage(p).free})
        except OSError:
            continue
    return out


def ollama_loaded(runner=None):
    """[{'name','size_mb','processor','until'}] from «ollama ps» — models kept in RAM.

    An empty list means nothing is loaded, or Ollama is not installed: both are fine here.
    """
    runner = runner or subprocess.run
    try:
        res = runner(['ollama', 'ps'], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    if getattr(res, 'returncode', 1) != 0:
        return []
    out = []
    for line in (getattr(res, 'stdout', '') or '').splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0].upper() == 'NAME':
            continue
        size_mb = None
        size_at = None
        for i, token in enumerate(parts):
            if token.upper() in ('GB', 'MB') and i:
                try:
                    value = float(parts[i - 1])
                except ValueError:
                    continue
                size_mb = int(value * (1024 if token.upper() == 'GB' else 1))
                size_at = i - 1
                break
        out.append({'name': parts[0], 'size_mb': size_mb,
                    'processor': ' '.join(parts[size_at + 2:size_at + 3]) if size_at is not None else '',
                    'until': ' '.join(parts[size_at + 3:]) if size_at is not None else ''})
    return out


def report(status, need_mb, need_label, loaded, drives, pagefile_hint=True):
    """The text a person reads. Returns (text, ok)."""
    lines = []
    v = verdict(status, need_mb)
    total, free = status.get('total', 0), status.get('free', 0)
    lines.append('Память (Windows считает commit = RAM + файл подкачки)')
    lines.append('  RAM              %s, свободно %s' % (gib(total), gib(free)))
    lines.append('  Commit limit     %s = %s RAM + %s подкачки'
                 % (gib(status.get('commit_limit', 0)), gib(total), gib(pagefile_bytes(status))))
    lines.append('  Commit свободно  %s' % gib(status.get('commit_free', 0)))
    if drives:
        lines.append('')
        lines.append('Диски')
        for d in drives:
            lines.append('  %-3s %s свободно' % (d['path'], gib(d['free'])))
    if loaded:
        lines.append('')
        lines.append('Ollama держит в памяти')
        for m in loaded:
            size = gib(m['size_mb'] * MB) if m['size_mb'] else '?'
            lines.append('  %-24s %s   ->  ollama stop %s' % (m['name'], size, m['name']))
    if total >= 31 * GB:
        lines.append('')
        lines.append('Движок с 32 ГБ RAM держит веса в RAM между задачами (keep_ram),')
        lines.append('то есть 10 ГБ остаются занятыми и после ракурсов. На время тяжёлых')
        lines.append('сессий: setx ASTRA_ENGINE_KEEP_RAM 0  — и перезапустить студию.')
    lines.append('')
    lines.append('%s: %s в RAM, плюс %s системе.' % (need_label, gib(need_mb * MB), gib(RESERVE_MB * MB)))
    lines.append('  нужно свободного commit: %s, есть %s%s'
                 % (gib(v['need'] * MB), gib(v['have'] * MB),
                    '' if v['ok'] else ' — не хватает %s' % gib(v['short'] * MB)))
    lines.append('')
    if v['ok']:
        lines.append('ВЕРДИКТ: проходит. Если 1455 всё равно вылезает — память занимает что-то')
        lines.append('ещё (3D-модели движка в RAM, браузер): перезапустите студию перед ракурсами.')
        return '\n'.join(lines), True
    lines.append('ВЕРДИКТ: не хватает памяти на запуск, это и есть os error 1455.')
    lines.append('Windows предлагает «включите автоматический объём подкачки» — это верно,')
    lines.append('но только если на диске есть куда расти. По шагам:')
    for i, a in enumerate(v['advice'], 1):
        lines.append('  %d. %s' % (i, a))
    if pagefile_hint:
        room = max([d['free'] for d in drives] or [0])
        if drives and room < 16 * GB:
            lines.append('  ! свободного места на дисках меньше 16 ГБ: автоподкачка не вырастет,')
            lines.append('    освободите место или перенесите файл подкачки на другой диск')
    return '\n'.join(lines), False


def free_ollama(runner=None, loaded=None):
    """«ollama stop» every model that sits in memory. Returns what was stopped.

    Ollama keeps a model for five minutes after the last answer, so closing the window
    changes nothing — the GB are still taken.
    """
    runner = runner or subprocess.run
    loaded = ollama_loaded(runner) if loaded is None else loaded
    stopped = []
    for m in loaded:
        try:
            res = runner(['ollama', 'stop', m['name']], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if getattr(res, 'returncode', 0) == 0:
            stopped.append(m['name'])
    return stopped


def pagefile_script(mode, status, need_mb, drives, size_mb=None):
    """PowerShell that changes the page file, as one -EncodedCommand line.

    Encoded (UTF-16LE base64) so the command survives cmd -> powershell -> elevated
    powershell without a single quote of escaping, and Cyrillic paths never touch the
    console code page. mode: 'auto' (let Windows size it) or 'fixed' (explicit size on
    the disk with the most room).
    """
    if mode == 'fixed':
        drive = max(drives, key=lambda d: d['free'])['path'] if drives else 'C:\\'
        target = int(size_mb) if size_mb else max(8192, verdict(status, need_mb)['pagefile_after'])
        target = int((target + 1023) // 1024 * 1024)
        plan = ('$cs = Get-CimInstance Win32_ComputerSystem\n'
                "if ($cs.AutomaticManagedPagefile) { Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile = $false} }\n"
                "$pf = Get-CimInstance Win32_PageFileSetting | Where-Object { $_.Name -like '%s*' } | Select-Object -First 1\n"
                "if ($pf) { Set-CimInstance -InputObject $pf -Property @{InitialSize = %d; MaximumSize = %d} }\n"
                "else { New-CimInstance -ClassName Win32_PageFileSetting -Property @{Name = '%spagefile.sys'; InitialSize = %d; MaximumSize = %d} | Out-Null }\n"
                % (drive, target, target, drive, target, target))
    else:
        plan = ('$cs = Get-CimInstance Win32_ComputerSystem\n'
                "if (-not $cs.AutomaticManagedPagefile) { Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile = $true} }\n")
    tail = ("Write-Host ''\n"
            "Get-CimInstance Win32_PageFileSetting | Format-List Name, InitialSize, MaximumSize\n"
            "Get-CimInstance Win32_PageFileUsage | Format-List Name, AllocatedBaseSize, PeakUsage\n"
            "Write-Host 'Restart Windows: the new page file size applies after a reboot.'\n"
            "Read-Host 'Enter to close'\n")
    return plan + tail


def encoded_command(script):
    """base64 of the PowerShell script in UTF-16LE, the encoding -EncodedCommand expects."""
    import base64
    return base64.b64encode(script.encode('utf-16-le')).decode('ascii')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    need_label, need_mb = 'Ракурсы (MV-Adapter)', NEED_MB['views']
    as_json, ask_ollama = False, True
    action, size_mb = None, None
    while argv:
        a = argv.pop(0)
        if a == '--need' and argv:
            need_mb = int(float(argv.pop(0)))
            need_label = 'Задача'
        elif a == '--kind' and argv:
            kind = argv.pop(0)
            if kind not in NEED_MB:
                print('Не знаю такой задачи: %s (есть: %s)' % (kind, ', '.join(NEED_MB)))
                return 2
            need_mb, need_label = NEED_MB[kind], kind
        elif a == '--json':
            as_json = True
        elif a == '--no-ollama':
            ask_ollama = False
        elif a == '--free-ollama':
            action = 'free'
        elif a in ('--pagefile-auto', '--pagefile-fixed'):
            action = a.split('-')[-1]
            if action == 'fixed' and argv and argv[0].lstrip('-').isdigit():
                action, size_mb = 'fixed', int(argv.pop(0))
        elif a in ('-h', '--help'):
            print(__doc__)
            return 0
        else:
            print('Не понимаю аргумент: %s (см. --help)' % a)
            return 2
    status = memory_status()
    if status is None:
        print('Это не Windows: commit limit здесь не при чём.')
        return 2
    loaded = ollama_loaded() if ask_ollama else []
    drives = disks()
    if action == 'free':
        stopped = free_ollama(loaded=loaded)
        if not stopped:
            print('Ollama ничего не держит (или не установлен) — освобождать нечего.')
            return 0
        for name in stopped:
            print('выгружено: %s' % name)
        print('')
        text, ok = report(status, need_mb, need_label, [], drives)
        print(text)
        return 0 if ok else 1
    if action in ('auto', 'fixed'):
        if not drives:
            print('Не вижу ни одного диска — нечего настраивать.')
            return 2
        script = pagefile_script(action, status, need_mb, drives, size_mb)
        if action == 'fixed':
            drive = max(drives, key=lambda d: d['free'])
            target = int(size_mb) if size_mb else max(8192, verdict(status, need_mb)['pagefile_after'])
            if drive['free'] < (target + 2048) * MB:
                print('На %s свободно %s, а файл подкачки возьмёт %s — влезет в притык.'
                      % (drive['path'], gib(drive['free']), gib(target * MB)), file=sys.stderr)
        print(encoded_command(script))
        return 0
    text, ok = report(status, need_mb, need_label, loaded, drives)
    if as_json:
        v = verdict(status, need_mb)
        print(json.dumps({'status': status, 'verdict': v, 'ollama': loaded, 'disks': drives,
                          'ok': ok}, ensure_ascii=False, indent=1))
    else:
        print(text)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
