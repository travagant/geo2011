#!/usr/bin/env python3
"""Why does the studio say "no kernel image is available"? Find out, then fix.

The message

    CUDA error: no kernel image is available for execution on the device

means one thing only: the CUDA binary that was called has no compiled code for
this GPU's compute capability (GTX 1080 Ti = Pascal = sm_61).  The card is fine;
a wheel somewhere was built without Pascal.  There are exactly two wheels that
can be at fault, and they fail at different moments:

* **torch itself.**  PyTorch's ``cu128`` wheels ship ``sm_70 … sm_120`` and no
  Pascal (``cu126`` is the last line that still carries ``sm_50/sm_60``).  It
  shows up on the very first tensor on the GPU — so a *shape* fails at once.
  Curiously ``torch.cuda.is_available()`` still says True, which is why the
  studio starts and looks healthy.

* **Hunyuan3D's rasterizer.**  ``custom_rasterizer_kernel`` ships as a
  precompiled wheel built for Ampere and newer, so on Pascal the model is made
  and the *texture* step dies inside ``PyInit_custom_rasterizer_kernel``.
  Fixing it means compiling that extension locally with
  ``TORCH_CUDA_ARCH_LIST=6.1``, which needs the CUDA toolkit and MSVC.

Where this torch comes from is not a mystery either — it is the studio's own
installer.  ``installer/Install.ps1`` (``InstallComfyDeps``) picks the wheel
index by one question, "is there a GPU":

    $torchIndex = if ($hasGpu) { 'https://download.pytorch.org/whl/cu128' } else { '.../cpu' }
    uv pip install --python <venv> torch torchvision torchaudio --index-url $torchIndex

so every machine with any video card — a 1080 Ti included — gets ``cu128``, the
one line without Pascal.  The studio's ComfyUI lives in
``<app>/runtime/ComfyUI/.venv`` (``server/storage.mjs``: ``comfyDir``), and the
``import torch`` check before that line means a reinstall will *not* happen on
its own: once the venv holds a working cu126 torch, the installer leaves it be.

Two traps when fixing it from outside:

* the venv is made by ``uv venv``, which **ships no pip** — ``python -m pip``
  fails there with "No module named pip", so the studio's own
  ``runtime\\uv\\uv.exe`` must be used (``uv pip install --python <exe>``);
* "newest torch from cu126" is not guaranteed to be the same version the studio
  had, so every attempt is verified by running a real kernel before it counts.

So: ``check`` reports, per interpreter, what torch is installed, whether it
really runs a kernel on this GPU, and whether the rasterizer is importable;
``fix-torch`` reinstalls torch/torchvision/torchaudio from the cu126 index
(through uv), tries the known-good 2.8.0 set if the newest one still has no
Pascal, and verifies each attempt; ``fix-rasterizer`` rebuilds the rasterizer
for sm_61 if its sources are on disk.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

STUDIO_DEFAULT = r"D:\AstraForge"
STUDIO_CANDIDATES = (r"C:\AstraForge", r"D:\AstraForge", r"E:\AstraForge",
                     r"C:\Program Files\AstraForge", r"D:\Program Files\AstraForge")
TORCH_INDEX_PASCAL = "https://download.pytorch.org/whl/cu126"
RASTERIZER_MODULES = ("custom_rasterizer", "custom_rasterizer_kernel",
                      "differentiable_renderer", "mesh_processor")
# the studio's own ComfyUI (server/storage.mjs: comfyDir, else <app>/runtime/ComfyUI)
COMFY_REL = ("runtime", "ComfyUI")
# ports the studio and its engines listen on (server/index.mjs, runtime.mjs)
ENGINE_PORTS = (3210, 8190, 8191, 8192)
# packages to try, in order; each entry is (what the user sees, requirement list).
# cu126 is the last wheel line that still carries Pascal (sm_50/sm_60), and a
# 1080 Ti (6.1) runs sm_60 kernels — same major, lower minor.
TORCH_ATTEMPTS = (
    ("новейшая сборка из cu126", ("torch", "torchvision", "torchaudio")),
    ("новейшая сборка из cu126, без torchaudio", ("torch", "torchvision")),
    ("проверенный набор torch 2.8.0 + cu126",
     ("torch==2.8.0", "torchvision==0.23.0", "torchaudio==2.8.0")),
    ("проверенный набор torch 2.8.0 без torchaudio",
     ("torch==2.8.0", "torchvision==0.23.0")),
)

PROBE = r'''
import json, sys
out = {"python": sys.version.split()[0]}
try:
    import torch
    out["torch"] = torch.__version__
    out["cuda_build"] = torch.version.cuda or ""
    out["available"] = bool(torch.cuda.is_available())
    if out["available"]:
        out["device"] = torch.cuda.get_device_name(0)
        major, minor = torch.cuda.get_device_capability(0)
        out["capability"] = "%d.%d" % (major, minor)
        out["arch"] = list(torch.cuda.get_arch_list())
        try:
            probe = torch.zeros(8, device="cuda")
            (probe + 1).sum().item()
            # a second, different kernel: "no kernel image" can hide behind PTX
            eye = torch.eye(16, device="cuda")
            (eye @ eye).sum().item()
            out["smoke"] = "ok"
        except Exception as exc:
            out["smoke"] = "%s: %s" % (type(exc).__name__, exc)
except Exception as exc:
    out["error"] = "%s: %s" % (type(exc).__name__, exc)
for name in __MODULES__:
    try:
        __import__(name)
        out["mod_" + name] = "ok"
    except Exception as exc:
        out["mod_" + name] = type(exc).__name__
print(json.dumps(out))
'''
PROBE = PROBE.replace("__MODULES__", repr(list(RASTERIZER_MODULES)))


# --------------------------------------------------------------------------- #
#  discovery and reading
# --------------------------------------------------------------------------- #
def guess_studio() -> str:
    """The installed studio: it has ``data`` or ``runtime`` next to itself."""
    for candidate in STUDIO_CANDIDATES:
        path = Path(candidate)
        if path.is_dir() and any((path / name).is_dir() for name in ("data", "runtime")):
            return str(path)
    return STUDIO_DEFAULT


def studio_comfy_dir(studio: str) -> str:
    """The ComfyUI the studio itself starts.

    ``server/storage.mjs``: ``ASTRA_COMFY_DIR`` or ``<app>/runtime/ComfyUI``; the
    installer saves the answer as ``comfyDir`` in ``data/config.json``, which wins
    when the user pointed the studio at a ComfyUI somewhere else.  This is the
    only interpreter that matters — the studio runs Hunyuan3D in it (port 8190).
    """
    config = Path(studio) / "data" / "config.json"
    if config.is_file():
        try:
            value = json.loads(config.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, OSError):
            value = None
        pointed = value.get("comfyDir") if isinstance(value, dict) else None
        if pointed:
            return str(pointed)
    return str(Path(studio).joinpath(*COMFY_REL))


def find_pythons(studio: str, extra=()) -> list:
    """Every interpreter the studio might be running: venvs and embeds.

    The studio's ComfyUI venv comes first — it is the one that runs Hunyuan3D.
    """
    root = Path(studio)
    found = []

    def add(path):
        text = str(path)
        if text not in found and Path(text).is_file():
            found.append(text)

    add(Path(studio_comfy_dir(studio)) / ".venv" / "Scripts" / "python.exe")
    # glob's ** does not descend into dotted folders, and the studio's venvs are
    # exactly that (.venv), so those are spelled out
    patterns = ("runtime/**/Scripts/python.exe", "runtime/**/python.exe",
                "runtime/**/python_embeded/python.exe",
                "runtime/**/.venv/Scripts/python.exe",
                "runtime/.venv/Scripts/python.exe",
                "runtime/**/venv/Scripts/python.exe")
    for pattern in patterns:
        for hit in glob.glob(str(root / pattern), recursive=True):
            add(hit)
    for candidate in extra:
        if candidate:
            add(candidate)
    return found


def find_uv(studio: str) -> str:
    """The ``uv`` the studio installed — its venvs have no pip at all.

    ``uv venv`` does not put pip inside, so ``python -m pip`` answers "No module
    named pip" in ``runtime\\ComfyUI\\.venv``.  ``uv pip install --python <exe>``
    works without it.
    """
    for candidate in (Path(studio) / "runtime" / "uv" / "uv.exe",
                      Path(studio) / "runtime" / "uv" / "uv"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("uv") or ""


def has_pip(python: str) -> bool:
    try:
        done = subprocess.run([python, "-c", "import pip"], capture_output=True,
                              timeout=60)
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def port_open(port: int, host="127.0.0.1", timeout=0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def stop_studio(wait=45) -> bool:
    """Stop the studio and its ComfyUI, or torch cannot be replaced.

    Windows keeps a loaded DLL locked, so ``pip install torch`` into a venv whose
    python is running fails with "Access is denied" on ``torch_cuda.dll``.  The
    studio stops its own ComfyUI on ``POST /api/shutdown`` (server/index.mjs:
    ``shutdown = () => { stopOwnedRuntime(); … }``), so that is what we call.
    """
    if not any(port_open(port) for port in ENGINE_PORTS):
        print("    студия и её движок не запущены — менять файлы можно")
        return True
    print("    окно студии придётся закрыть: Windows не даст заменить torch.dll, "
          "пока ComfyUI держит его в памяти")
    if port_open(3210):
        try:
            request = urllib.request.Request("http://127.0.0.1:3210/api/shutdown",
                                             data=b"{}", method="POST",
                                             headers={"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=3).read()
            print("    студии отправлен /api/shutdown")
        except OSError as exc:
            print(f"    /api/shutdown не ответил ({type(exc).__name__}) — закрой окно сам")
    deadline = time.time() + wait
    while time.time() < deadline:
        if not any(port_open(port) for port in ENGINE_PORTS):
            print("    студия и движок остановлены")
            return True
        time.sleep(1.5)
    left = [port for port in ENGINE_PORTS if port_open(port)]
    print("    [!] всё ещё слушают порты: " + ", ".join(str(port) for port in left))
    print("    закрой окно студии (и отдельный ComfyUI, если он запущен не ею)")
    print("    и запусти этот файл снова — иначе установка упадёт на занятом файле")
    return False


def install_command(python: str, uv: str, requirements, index: str = TORCH_INDEX_PASCAL) -> list:
    """``uv pip install --python <exe>`` when uv is around, otherwise pip."""
    if uv:
        return [uv, "pip", "install", "--python", python, "--index-url", index,
                "--upgrade", *requirements]
    return [python, "-m", "pip", "install", "--upgrade", "--force-reinstall",
            "--no-cache-dir", "--index-url", index, *requirements]


def probe(python: str, timeout=180) -> dict:
    """Ask one interpreter what its torch can actually do."""
    try:
        proc = subprocess.run([python, "-c", PROBE], capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"error": "interpreter not found"}
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {"error": (proc.stderr or proc.stdout or "no output").strip()[-400:]}


def compatible_arch(arch, capability: str) -> bool:
    """Is any ``sm_XY`` in ``arch`` able to run on this ``capability``?

    Within one major version a cubin built for a lower minor runs on a higher
    one, which is why a 1080 Ti (6.1) is fine with PyTorch's ``sm_60`` kernels
    even though the list never says ``sm_61``.
    """
    try:
        major, minor = (int(part) for part in capability.split(".")[:2])
    except (ValueError, AttributeError):
        return False
    for tag in arch or []:
        text = str(tag).replace("sm_", "")
        if not text.isdigit() or len(text) != 2:
            continue
        if int(text[0]) == major and int(text[1]) <= minor:
            return True
    return False


def verdict(info: dict) -> tuple:
    """``(level, text)``: ``ok`` | ``bad`` | ``warn`` | ``info``."""
    if info.get("error"):
        if "No module named 'torch'" in str(info["error"]):
            return "info", "torch не установлен в этом окружении"
        return "info", "torch не запускается: " + str(info["error"])[:120]
    if "torch" not in info:
        return "info", "torch не установлен в этом окружении"
    if not info.get("available"):
        return "warn", "CUDA недоступна (драйвер? torch собран под CPU?)"
    arch = info.get("arch") or []
    smoke = str(info.get("smoke") or "")
    capability = info.get("capability") or ""
    pascal = f"sm_{capability.replace('.', '')}" if capability else ""
    if smoke and smoke != "ok":
        if "no kernel image" in smoke:
            return "bad", (f"в torch нет кода для {pascal}: arch = "
                           f"{', '.join(arch) or '—'} — сборка cu128 без Pascal, "
                           "нужен cu126")
        return "bad", "первый же вызов CUDA падает: " + smoke.splitlines()[0][:160]
    if pascal and not compatible_arch(arch, capability):
        return "warn", (f"в arch list нет ничего для {pascal} "
                        f"({', '.join(arch) or '—'}), но вычисления прошли — "
                        "похоже, сработал PTX/JIT; такие окружения хрупкие")
    return "ok", f"torch работает на {info.get('device')} ({pascal})"


def report_lines(python: str, info: dict) -> list:
    level, text = verdict(info)
    mark = {"ok": "[OK]  ", "bad": "[БЕДА]", "warn": "[!]   ", "info": "[i]   "}[level]
    lines = [f"{mark} {python}",
             f"        python {info.get('python', '?')}  torch {info.get('torch', '—')}"
             f"  (сборка CUDA {info.get('cuda_build') or '—'})"]
    if info.get("device"):
        lines.append(f"        GPU: {info['device']}  ({info.get('capability')}, "
                     f"sm {info.get('capability')})")
    if info.get("arch"):
        lines.append("        arch list: " + " ".join(info["arch"]))
    lines.append(f"        вердикт: {text}")
    modules = {k[4:]: v for k, v in info.items() if k.startswith("mod_")}
    if modules:
        lines.append("        расширения: " + ", ".join(
            f"{name}={'ok' if state == 'ok' else state}" for name, state in modules.items()))
    return lines


def find_rasterizer_sources(studio: str) -> list:
    """Directories that hold the rasterizer's setup.py, if it was shipped with sources."""
    root = Path(studio)
    found = []
    for pattern in ("runtime/**/custom_rasterizer/setup.py",
                    "runtime/**/hy3dgen/texgen/custom_rasterizer/setup.py",
                    "data/**/custom_rasterizer/setup.py"):
        for hit in glob.glob(str(root / pattern), recursive=True):
            if hit not in found:
                found.append(hit)
    return found


# --------------------------------------------------------------------------- #
#  commands
# --------------------------------------------------------------------------- #
def cmd_check(args) -> int:
    studio = args.studio
    comfy = studio_comfy_dir(studio)
    print(f"студия: {studio}")
    print(f"её ComfyUI (там и живёт Hunyuan3D): {comfy}")
    if not Path(comfy).is_dir():
        print("  [!] этой папки нет — студия берёт ComfyUI из другого места "
              "(ASTRA_COMFY_DIR или поле comfyDir в data\\config.json)")
    uv = find_uv(studio)
    print("uv: " + (uv or "не найден (переустановка пойдёт через pip)"))
    print()

    targets = [args.python] if args.python else find_pythons(studio, extra=())
    if not targets:
        print(f"не нашёл ни одного python.exe под {studio}")
        print("укажи вручную: --python \"<путь>\\python.exe\"")
        return 2
    print(f"смотрю {len(targets)} интерпретатор(ов)\n")
    lines, worst = [], "ok"
    for python in targets:
        info = probe(python)
        block = report_lines(python, info)
        print("\n".join(block))
        print()
        lines.extend(block + [""])
        level = verdict(info)[0]
        if level == "bad":
            worst = "bad"
        elif level == "warn" and worst == "ok":
            worst = "warn"

    sources = find_rasterizer_sources(studio)
    if sources:
        print("исходники custom_rasterizer: " + ", ".join(sources))
        lines.append("rasterizer sources: " + ", ".join(sources))
    else:
        print("исходники custom_rasterizer на диске не найдены "
              "(значит стоит готовый wheel — его придётся заменить сборкой под sm_61)")
    nvcc = shutil.which("nvcc")
    print("nvcc: " + (nvcc or "не найден (для пересборки rasterizer нужен CUDA Toolkit 12.x + MSVC)"))
    lines.append(f"nvcc: {nvcc or 'missing'}")

    lines.insert(0, f"studio: {studio}\ncomfy: {comfy}\nuv: {uv or 'missing'}\n")
    if worst == "bad":
        print("\n[БЕДА] — двойной клик по CUDA-fix-torch.cmd: он поставит torch "
              "cu126 (последняя сборка с Pascal) и проверит результат.")
        print("Откуда взялся cu128: установщик студии "
              "(installer/Install.ps1) выбирает индекс по одному признаку — "
              "«есть ли видеокарта», и любой карте даёт cu128.")
    report = Path(args.report) if args.report else Path.cwd() / "cuda-report.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nотчёт: {report}")
    return 1 if worst == "bad" else 0


def cmd_fix_torch(args) -> int:
    studio = args.studio
    targets = [args.python] if args.python else find_pythons(studio)
    if not targets:
        print(f"не нашёл python.exe под студией ({studio}) — укажи --python")
        return 2
    uv = find_uv(studio)
    log = [f"studio: {studio}", f"uv: {uv or 'missing'}",
           f"python: {', '.join(targets)}", ""]
    print("Переустановка torch на сборку cu126 — последнюю, где есть Pascal (sm_61).")
    print("Скачается ~2.5–3 ГБ, ставится ~7 ГБ. Прерывать нельзя: окно должно дойти до конца.")
    print("После каждого шага фикс сам запускает ядро на GPU и проверяет результат.\n")

    # 1. decide what needs fixing before touching anything (or stopping anything)
    broken = []
    for python in targets:
        before = probe(python)
        level, text = verdict(before)
        print(f"--- {python}\n    {text}")
        log.extend([f"--- {python}", f"situation: {text}"])
        if level in ("bad", "warn"):
            broken.append(python)
        elif level == "ok":
            print("    уже работает, не трогаю (--all — переустановить всё равно)")
            if args.all:
                broken.append(python)
        else:
            print("    torch не установлен — пропускаю (это не окружение генерации)")
    if not broken:
        print("\nНечего исправлять: во всех окружениях ядро запускается.")
        report = Path(args.report) if args.report else Path.cwd() / "cuda-report.txt"
        report.write_text("\n".join(log + ["", "fixed: none", "failed: none"]), encoding="utf-8")
        print(f"отчёт: {report}")
        return 0

    # 2. free the DLLs Windows will not let us overwrite
    print("\nФайлы torch нельзя заменить, пока их держит запущенный движок.")
    if not args.keep_engine and not stop_studio():
        report = Path(args.report) if args.report else Path.cwd() / "cuda-report.txt"
        report.write_text("\n".join(log + ["", "aborted: engine still running"]),
                          encoding="utf-8")
        print(f"\nотчёт: {report}")
        return 2
    print()

    fixed, failed = [], []
    for python in broken:
        level, text = verdict(probe(python))
        print(f"\n--- {python}\n    сейчас: {text}")
        log.extend([f"--- {python}", f"situation: {text}"])
        if not args.yes:
            answer = input("    переустановить torch в этом окружении? [y/N] ").strip().lower()
            if answer not in ("y", "yes", "д", "да"):
                print("    пропущено")
                continue
        tool = uv
        if not tool and not has_pip(python):
            print("    в этом окружении нет pip (его создал uv) и uv не найден —")
            print("    включу pip через ensurepip")
            subprocess.call([python, "-m", "ensurepip", "--upgrade"])
        done = False
        for label, requirements in TORCH_ATTEMPTS:
            command = install_command(python, tool, requirements)
            print(f"\n    попытка: {label}")
            print("    $ " + " ".join(command))
            log.append("command: " + " ".join(command))
            code = subprocess.call(command)
            if code != 0:
                print(f"    команда вышла с кодом {code} — пробую следующий вариант")
                log.append(f"exit: {code}")
                continue
            after = probe(python)
            level, text = verdict(after)
            print("    после: " + text)
            log.append(f"after ({label}): {text}")
            if level == "ok":
                done = True
                break
            if "no kernel image" not in (after.get("smoke") or ""):
                print("    ядро уже не жалуется на отсутствие кода — другой сбой, "
                      "разбираем отдельно")
                break
        if done:
            fixed.append(python)
        else:
            failed.append(python)
            print("    [!] не вышло. Пришли cuda-report.txt — по нему видно, что "
                  "поставилось и что осталось")
    report = Path(args.report) if args.report else Path.cwd() / "cuda-report.txt"
    log.append("")
    log.append("fixed: " + (", ".join(fixed) or "none"))
    log.append("failed: " + (", ".join(failed) or "none"))
    report.write_text("\n".join(log), encoding="utf-8")
    print(f"\nотчёт: {report}")
    if failed:
        return 1
    if fixed:
        print("\nГотово. Теперь закрой студию (или «Связи → остановить движок») "
              "и запусти заново — уже запущенный ComfyUI держит старый torch в памяти.")
        print("Не запускай заново «Install AstraForge.cmd» с очисткой ComfyUI: он "
              "поставит cu128 обратно. Обычный запуск студии фикс не отменяет.")
    return 0


def cmd_fix_rasterizer(args) -> int:
    sources = find_rasterizer_sources(args.studio)
    if not sources:
        print("Исходников custom_rasterizer нет — его поставили готовым wheel'ом.")
        print("Важно: у студии его и не должно быть. Она ставит Hunyuan3D из своего")
        print("ComfyUI и берёт только геометрию (AstraForgeMesh, Marching Cubes);")
        print("растеризатор нужен внешнему ComfyUI-Hunyuan3DWrapper для текстуры.")
        print("Если падает именно текстура во внешнем ComfyUI — нужны исходники")
        print("hy3dgen/texgen/custom_rasterizer из Hunyuan3D-2 и CUDA Toolkit 12.x + MSVC.")
        return 2
    nvcc = shutil.which("nvcc")
    if not nvcc:
        print("nvcc не найден: нужен CUDA Toolkit 12.x (12.1/12.4/12.6) и MSVC Build Tools.")
        print("Без них пересборку не сделать — сообщи, соберём пошагово.")
        return 2
    python = args.python
    if not python:
        print("укажи интерпретатор: --python \"<venv>\\python.exe\"")
        return 2
    env = dict(os.environ)
    env["TORCH_CUDA_ARCH_LIST"] = args.arch
    env["MAX_JOBS"] = "2"
    for source in sources:
        folder = str(Path(source).parent)
        print(f"\n--- сборка {folder} под {args.arch}")
        code = subprocess.call([python, "setup.py", "build_ext", "--inplace"],
                               cwd=folder, env=env)
        print(f"    код {code}")
        if code != 0:
            return code
    print("\nСобрано. Проверь импортом: python -c \"import custom_rasterizer\"")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_text in (("check", "что установлено и почему падает"),
                            ("fix-torch", "поставить torch cu126 (с Pascal)"),
                            ("fix-rasterizer", "пересобрать rasterizer под sm_61")):
        part = sub.add_parser(name, help=help_text)
        # empty means "look for it": the studio is on C: on this machine, on D:
        # on the next one, and a folder can carry the right name without the
        # studio inside it (guess_studio checks for data/ or runtime/)
        part.add_argument("--studio", default="")
        part.add_argument("--python", default="", help="конкретный интерпретатор")
        part.add_argument("--yes", action="store_true", help="без вопросов")
        part.add_argument("--all", action="store_true", help="трогать и рабочие окружения")
        part.add_argument("--arch", default="6.1", help="TORCH_CUDA_ARCH_LIST")
        part.add_argument("--report", default="", help="куда писать cuda-report.txt")
        part.add_argument("--keep-engine", action="store_true",
                          dest="keep_engine",
                          help="не останавливать студию (файлы torch могут быть заняты)")
    args = parser.parse_args(argv)
    if not args.studio:
        args.studio = guess_studio()
    return {"check": cmd_check, "fix-torch": cmd_fix_torch,
            "fix-rasterizer": cmd_fix_rasterizer}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
