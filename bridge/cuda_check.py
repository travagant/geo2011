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

So: ``check`` reports, per interpreter, what torch is installed, whether it
really runs a kernel on this GPU, and whether the rasterizer is importable;
``fix-torch`` reinstalls torch/torchvision/torchaudio from the cu126 index;
``fix-rasterizer`` rebuilds the rasterizer for sm_61 if its sources are on disk.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

STUDIO_DEFAULT = r"D:\AstraForge"
TORCH_INDEX_PASCAL = "https://download.pytorch.org/whl/cu126"
RASTERIZER_MODULES = ("custom_rasterizer", "custom_rasterizer_kernel",
                      "differentiable_renderer", "mesh_processor")

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
def find_pythons(studio: str, extra=()) -> list:
    """Every interpreter the studio might be running: venvs and embeds."""
    root = Path(studio)
    found = []
    # glob's ** does not descend into dotted folders, and the studio's venvs are
    # exactly that (.venv), so those are spelled out
    patterns = ("runtime/**/Scripts/python.exe", "runtime/**/python.exe",
                "runtime/**/python_embeded/python.exe",
                "runtime/**/.venv/Scripts/python.exe",
                "runtime/.venv/Scripts/python.exe",
                "runtime/**/venv/Scripts/python.exe")
    for pattern in patterns:
        for hit in glob.glob(str(root / pattern), recursive=True):
            if hit not in found:
                found.append(hit)
    for candidate in extra:
        if candidate and candidate not in found:
            found.append(candidate)
    # keep the ones that answer, in a stable order
    return sorted(found)


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
                           f"{', '.join(arch) or '—'} — нужен cu126")
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
    targets = [args.python] if args.python else find_pythons(args.studio, extra=())
    if not targets:
        print(f"не нашёл ни одного python.exe под {args.studio}")
        print("укажи вручную: --python \"<путь>\\python.exe\"")
        return 2
    print(f"смотрю {len(targets)} интерпретатор(ов) под {args.studio}\n")
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

    sources = find_rasterizer_sources(args.studio)
    if sources:
        print("исходники custom_rasterizer: " + ", ".join(sources))
        lines.append("rasterizer sources: " + ", ".join(sources))
    else:
        print("исходники custom_rasterizer на диске не найдены "
              "(значит стоит готовый wheel — его придётся заменить сборкой под sm_61)")
    nvcc = shutil.which("nvcc")
    print("nvcc: " + (nvcc or "не найден (для пересборки rasterizer нужен CUDA Toolkit 12.x + MSVC)"))
    lines.append(f"nvcc: {nvcc or 'missing'}")

    report = Path(args.report) if args.report else Path.cwd() / "cuda-report.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nотчёт: {report}")
    return 1 if worst == "bad" else 0


def cmd_fix_torch(args) -> int:
    targets = [args.python] if args.python else find_pythons(args.studio)
    if not targets:
        print("не нашёл python.exe под студией — укажи --python")
        return 2
    print("Переустановка torch на сборку cu126 — последнюю, где есть Pascal (sm_61).")
    print("Скачается ~2.5–3 ГБ, поставится ~7 ГБ. Прерывать нельзя.\n")
    for python in targets:
        before = probe(python)
        level, text = verdict(before)
        print(f"\n--- {python}\n    сейчас: {text}")
        if level == "info":
            print("    torch не установлен — пропускаю (это не окружение генерации)")
            continue
        if level == "ok" and "--all" not in sys.argv:
            print("    уже работает, ничего не меняю (--all — переустановить всё равно)")
            continue
        if not args.yes:
            answer = input("    переустановить torch в этом окружении? [y/N] ").strip().lower()
            if answer not in ("y", "yes", "д", "да"):
                print("    пропущено")
                continue
        command = [python, "-m", "pip", "install", "--upgrade", "--force-reinstall",
                   "--no-cache-dir", "torch", "torchvision", "torchaudio",
                   "--index-url", TORCH_INDEX_PASCAL]
        print("    $ " + " ".join(command))
        code = subprocess.call(command)
        if code != 0:
            print(f"    pip вышел с кодом {code} — окружение могло остаться без torch.")
            print("    повтори запуск; если снова ошибка, пришли cuda-report.txt")
            return code
        after = probe(python)
        level, text = verdict(after)
        print("    после: " + text)
        if level != "ok":
            print("    не помогло — пришли этот вывод, будем смотреть rasterizer")
            return 1
    print("\nГотово. Перезапусти студию и попробуй генерацию снова.")
    return 0


def cmd_fix_rasterizer(args) -> int:
    sources = find_rasterizer_sources(args.studio)
    if not sources:
        print("Исходников custom_rasterizer нет — его поставили готовым wheel'ом.")
        print("Нужно скачать исходники Hunyuan3D-2 (hy3dgen/texgen/custom_rasterizer)")
        print("или взять их из установленного пакета, и только потом собирать.")
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
        part.add_argument("--studio", default=STUDIO_DEFAULT)
        part.add_argument("--python", default="", help="конкретный интерпретатор")
        part.add_argument("--yes", action="store_true", help="без вопросов")
        part.add_argument("--all", action="store_true", help="трогать и рабочие окружения")
        part.add_argument("--arch", default="6.1", help="TORCH_CUDA_ARCH_LIST")
        part.add_argument("--report", default="", help="куда писать cuda-report.txt")
    args = parser.parse_args(argv)
    return {"check": cmd_check, "fix-torch": cmd_fix_torch,
            "fix-rasterizer": cmd_fix_rasterizer}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
