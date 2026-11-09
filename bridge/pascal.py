"""GTX 1080 Ti is compute 6.1, and the studio's installer hands it a wheel that
has no code for this card.

The single line responsible, `installer\\Install.ps1` -> `InstallComfyDeps`:

    $torchIndex = if ($hasGpu) { 'https://download.pytorch.org/whl/cu128' } else { '.../cpu' }

`cu128` carries `sm_70 … sm_120`. On Pascal every kernel call then dies with
"no kernel image is available for execution on the device" while
`torch.cuda.is_available()` keeps answering `True` — the card is seen, the
kernels are absent.

Two consequences, two jobs, both here:

* the index must follow the *card*, not the fact that a card exists
  (`torch_index_for`), and
* a reinstall must be able to repair a Pascal machine, so the "is torch
  installed" test has to ask about `sm_61` and not only about `import torch`
  (`patch_installer`, idempotent, keeps a `.bak`).

Installing the wheel itself is not repeated here: `cuda_check.py fix-torch`
already does that (shutdown, cu126, fallbacks, a real matmul on the card). This
module makes the installer agree with it, so the next reinstall does not undo it.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CU126 = "https://download.pytorch.org/whl/cu126"
CU128 = "https://download.pytorch.org/whl/cu128"
CPU = "https://download.pytorch.org/whl/cpu"

# Names are the fallback: `--query-gpu=compute_cap` needs a fresh driver.
NAME_TO_CC = (
    (re.compile(r"GTX\s+(10|16)\d0|TITAN\s+X|Quadro\s+(P|M)\d|Tesla\s+(P|M)\d"), 6.1),
    (re.compile(r"TITAN\s+V|V100"), 7.0),
    (re.compile(r"RTX\s+20\d0|Quadro\s+RTX"), 7.5),
    (re.compile(r"A100|RTX\s+30\d0"), 8.0),
    (re.compile(r"RTX\s+40\d0|L40|H100|A40"), 8.9),
    (re.compile(r"RTX\s+50\d0|B100|B200"), 12.0),
)


def torch_index_for(cc: float) -> str:
    """The wheel index a card of this compute capability needs.

    Below 7.5 is Pascal (6.x) and Volta (7.0): cu128 starts at `sm_75`, cu126 is
    the last line that still carries `sm_50 … sm_120`. ``0`` means "could not
    tell", and the older index is then the safe answer — it contains kernels for
    every architecture, so an unknown card gets a working build instead of a
    broken one. The only card that must never land here is `cpu`, and that is
    decided by `$hasGpu`, not by this function.
    """
    if cc <= 0:
        return CU126
    return CU126 if cc < 7.5 else CU128


def cc_from_name(name: str) -> float:
    for pattern, cc in NAME_TO_CC:
        if pattern.search(name or ""):
            return cc
    return 0.0


def gpu_info(smi: str = "nvidia-smi") -> dict:
    """``{"name", "cc", "source", "vram_mb"}`` — empty values if no driver."""
    info = {"name": "", "cc": 0.0, "source": "", "vram_mb": 0}
    try:
        raw = subprocess.run([smi, "--query-gpu=name,compute_cap,memory.total",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raw = ""
    if not raw:
        return info
    parts = [part.strip() for part in raw.splitlines()[0].split(",")]
    info["name"] = parts[0] if parts else ""
    for part in parts[1:]:
        if re.fullmatch(r"\d+\.\d+", part or ""):
            info["cc"], info["source"] = float(part), "nvidia-smi compute_cap"
        elif re.fullmatch(r"\d+", part or ""):
            info["vram_mb"] = int(part)
    if not info["cc"] and info["name"]:
        guessed = cc_from_name(info["name"])
        if guessed:
            info["cc"] = guessed
            info["source"] = "по имени карты (драйвер не отдал compute_cap)"
    return info


# --------------------------------------------------------------------------- #
#  the installer
# --------------------------------------------------------------------------- #
ANCHOR = ("  $torchIndex = if ($hasGpu) { 'https://download.pytorch.org/whl/cu128' } "
          "else { 'https://download.pytorch.org/whl/cpu' }")
REPLACEMENT = r"""  # Pascal (GTX 10xx, compute 6.1) has no kernels in cu128: torch.cuda works,
  # yet every kernel call dies with "no kernel image is available". cu126 is the
  # last line compiled for sm_50..sm_120, so the index follows the card.
  $cc = 0.0
  try {
    $cap = (& nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null | Select-Object -First 1)
    if ($cap -and ($cap -match '(\d+(\.\d+)?)')) { $cc = [double]$Matches[1] }
  } catch {}
  if ($cc -eq 0) {
    try {
      $nm = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
      if ($nm -match 'GTX\s+(10|16)\d0|TITAN\s+X|Quadro\s+(P|M)\d') { $cc = 6.1 }
    } catch {}
  }
  $torchIndex = if (!$hasGpu) { 'https://download.pytorch.org/whl/cpu' }
                elseif ($cc -gt 0 -and $cc -lt 7.5) { 'https://download.pytorch.org/whl/cu126' }
                else { 'https://download.pytorch.org/whl/cu128' }"""

# Second half of the same bug: torch is installed only when `import torch` fails,
# so an existing cu128 install is never revisited. On Pascal the import succeeds
# and every kernel still dies — the exact state a reinstall has to be able to fix.
ANCHOR_TORCH = """  Native { & $py -c "import torch" 2>$null | Out-Null }
  if ($LASTEXITCODE -ne 0) { Run $uv @('pip', 'install', '--python', $py, 'torch', 'torchvision', 'torchaudio', '--index-url', $torchIndex) 'PyTorch' }"""
REPLACEMENT_TORCH = r"""  Native { & $py -c "import torch" 2>$null | Out-Null }
  $torchOk = ($LASTEXITCODE -eq 0)
  if ($torchOk -and $cc -gt 0 -and $cc -lt 7.5) {
    # An import proves nothing on Pascal: ask the build whether sm_61 is inside.
    $sm = (& $py -c "import torch;print('sm_61' in torch.cuda.get_arch_list())" 2>$null)
    if ("$sm".Trim() -ne 'True') { $torchOk = $false }
  }
  if (!$torchOk) { Run $uv @('pip', 'install', '--python', $py, 'torch', 'torchvision', 'torchaudio', '--index-url', $torchIndex, '--upgrade') 'PyTorch' }"""

PATCHES = ((ANCHOR, REPLACEMENT), (ANCHOR_TORCH, REPLACEMENT_TORCH))
MARKER = "$cc -lt 7.5"          # proof that this patch has been applied


def find_installer(start: str = "") -> str:
    for base in (start, r"D:\AstraForge", r"C:\AstraForge"):
        if not base:
            continue
        path = Path(base, "installer", "Install.ps1")
        if path.is_file():
            return str(path)
    return ""


def patch_installer(path, say=print) -> int:
    """Pick torch by compute capability. Idempotent, keeps a ``.bak``."""
    target = Path(path)
    say(f"файл   : {target}")
    try:
        raw = target.read_bytes()
    except OSError as exc:
        say(f"не читается: {exc}")
        return 2
    crlf = b"\r\n" in raw
    bom = raw.startswith(b"\xef\xbb\xbf")   # PowerShell 5.1 needs it for Cyrillic
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    if MARKER in text:
        say("уже исправлен — второй раз ничего не меняю")
        return 0
    if ANCHOR not in text:
        say("не нашла строку выбора индекса — это не 0.3.0 или файл уже правили")
        say("искала: " + ANCHOR.strip())
        return 2
    backup = target.with_suffix(target.suffix + ".bak")
    if not backup.exists():
        backup.write_bytes(raw)
        say(f"копия  : {backup.name}")
    done = 0
    for anchor, replacement in PATCHES:
        if anchor in text:
            text = text.replace(anchor, replacement, 1)
            done += 1
    if crlf:
        text = text.replace("\n", "\r\n")
    target.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    say(f"исправлено: {done} из {len(PATCHES)} — индекс выбирается по compute "
        "capability (Pascal → cu126); наличие sm_61 проверяется, а не только import")
    if done < len(PATCHES):
        say("второе место не нашлось: torch останется ставиться только при неудачном import")
    return 0


def report_card(say=print) -> dict:
    card = gpu_info()
    say(f"карта  : {card['name'] or '(nvidia-smi не ответил)'}  "
        f"cc {card['cc'] or '?'}  ({card['source'] or 'нет данных'})")
    say(f"индекс : {torch_index_for(card['cc'])}")
    return card


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GTX 1080 Ti (Pascal, sm_61): "
                                                 "правка установщика AstraForge")
    parser.add_argument("--studio", default="", help="папка студии")
    parser.add_argument("--patch-installer", default="", metavar="Install.ps1")
    parser.add_argument("--card", action="store_true", help="только показать карту")
    parser.add_argument("--auto", action="store_true",
                        help="найти студию, исправить установщик, показать карту")
    args = parser.parse_args(argv)

    if args.patch_installer:
        return patch_installer(args.patch_installer)
    if args.card:
        report_card()
        return 0
    if not args.auto:
        parser.print_help()
        return 0

    installer = find_installer(args.studio)
    code = 0
    if installer:
        code = patch_installer(installer)
    else:
        print("установщик не найден (искала <студия>\\installer\\Install.ps1) — "
              "пропускаю, на работу студии это не влияет")
    print()
    report_card()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
