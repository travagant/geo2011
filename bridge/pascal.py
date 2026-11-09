"""A GTX 1080 Ti is compute 6.1, and AstraForge 0.6.12 decides that such a card
is not worth supporting.

Two lines in the studio are responsible, and both are about the same thing:

* `installer\\Install.ps1`, the GPU probe — a card below compute 7.5 is demoted
  (`$s.gpuOld = $s.gpu; $s.gpu = $null`), so the installer concludes there is no
  NVIDIA GPU at all and installs a CPU-only PyTorch;
* `$torchIndex = if ($hasGpu) { '.../whl/cu128' } else { '.../whl/cpu' }` — and
  `cu128` is built for `sm_70 … sm_120`. Pascal is `sm_61`, so even when the card
  is used, every kernel call dies with "no kernel image is available" while
  `torch.cuda.is_available()` keeps answering `True`.

`cu126` is the last wheel line that still carries `sm_50 … sm_120`, so the fix is
to stop treating "old" as "absent" and let the index follow the card:

* keep the card, mark it Pascal, choose `cu126` for both venvs (the studio's
  ComfyUI and the generation engine), and skip the pre-downloaded `cu128` wheel
  file that the engine would otherwise install;
* reinstall torch when `sm_61` is missing from `torch.cuda.get_arch_list()` and
  not merely when `import torch` fails — on Pascal the import always succeeds,
  which is why a reinstall used to undo the repair;
* leave what genuinely cannot work alone: Pixal3D already gates itself on
  compute 8.0 (`installer\\pixal3d\\pixal3d.ps1`), flash-attn has no Pascal build.

Every patch is idempotent, named in the report, and keeps a `.bak`. A patch that
does not find its anchor is reported as skipped instead of mangling the file:
the installer differs between 0.3.0 and 0.6.12, and both are handled here.

Installing the wheel itself is not repeated here — `cuda_check.py fix-torch`
already does that (shutdown, cu126, fallbacks, a real matmul on the card).
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

CU126 = "https://download.pytorch.org/whl/cu126"
CU128 = "https://download.pytorch.org/whl/cu128"
CPU = "https://download.pytorch.org/whl/cpu"

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
    broken one. "No GPU at all" is a different question, answered by `$hasGpu`.
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
#  how each piece of PowerShell asks the driver what the card can do
# --------------------------------------------------------------------------- #
CC_SNIPPET = """\
  $cc = 0.0
  try {
    $cap = (& nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null | Select-Object -First 1)
    if ("$cap".Trim() -match '^(\\d+\\.\\d+)$') { $cc = [double]::Parse($Matches[1], [Globalization.CultureInfo]::InvariantCulture) }
  } catch {}
"""

# --- installer\Install.ps1 -------------------------------------------------- #
# 1. the index itself (identical line in 0.3.0 and 0.6.12)
A_INDEX = ("  $torchIndex = if ($hasGpu) { 'https://download.pytorch.org/whl/cu128' } "
           "else { 'https://download.pytorch.org/whl/cpu' }")
R_INDEX = r"""  # Pascal (GTX 10xx, compute 6.1) has no kernels in cu128: torch.cuda works,
  # yet every kernel call dies with "no kernel image is available". cu126 is the
  # last line compiled for sm_50..sm_120, so the index follows the card.
""" + CC_SNIPPET + r"""  $torchIndex = 'https://download.pytorch.org/whl/cu128'
  if (!$hasGpu) { $torchIndex = 'https://download.pytorch.org/whl/cpu' }
  elseif ($cc -gt 0 -and $cc -lt 7.5) { $torchIndex = 'https://download.pytorch.org/whl/cu126' }"""

# 2. "torch is installed" must mean "it has kernels for this card"
A_TORCH_OLD = """  Native { & $py -c "import torch" 2>$null | Out-Null }
  if ($LASTEXITCODE -ne 0) { Run $uv @('pip', 'install', '--python', $py, 'torch', 'torchvision', 'torchaudio', '--index-url', $torchIndex) 'PyTorch' }"""
R_TORCH_OLD = r"""  Native { & $py -c "import torch" 2>$null | Out-Null }
  $torchOk = ($LASTEXITCODE -eq 0)
  if ($torchOk -and $cc -gt 0 -and $cc -lt 7.5) {
    # an import proves nothing on Pascal: ask the build whether sm_61 is inside
    $sm = (& $py -c "import torch;print('sm_61' in torch.cuda.get_arch_list())" 2>$null)
    if ("$sm".Trim() -ne 'True') { $torchOk = $false }
  }
  if (!$torchOk) {
    if ($torchIndex -match 'cu126') {
      # pinned and forced: `torch==2.8.0` is satisfied by `2.8.0+cu128`, so a
      # plain install would leave the broken wheel where it is
      Run $uv @('pip', 'install', '--python', $py, 'torch==2.8.0', 'torchvision==0.23.0', 'torchaudio==2.8.0', '--index-url', $torchIndex, '--reinstall-package', 'torch', '--reinstall-package', 'torchvision', '--reinstall-package', 'torchaudio') 'PyTorch 2.8.0 cu126 (Pascal)'
    } else { Run $uv @('pip', 'install', '--python', $py, 'torch', 'torchvision', 'torchaudio', '--index-url', $torchIndex) 'PyTorch' }
  }"""

A_TORCH_NEW = """  Native { & $py -c "import torch" 2>$null | Out-Null }
  if ($LASTEXITCODE -ne 0) {
"""
R_TORCH_NEW = r"""  Native { & $py -c "import torch" 2>$null | Out-Null }
  $torchOk = ($LASTEXITCODE -eq 0)
  if ($torchOk -and $cc -gt 0 -and $cc -lt 7.5) {
    # an import proves nothing on Pascal: ask the build whether sm_61 is inside
    $sm = (& $py -c "import torch;print('sm_61' in torch.cuda.get_arch_list())" 2>$null)
    if ("$sm".Trim() -ne 'True') { $torchOk = $false }
  }
  if (!$torchOk) {
"""

# 3. the pre-downloaded wheel file is cu128 — useless on Pascal
A_TW = "    $tw = if ($hasGpu) { EngineTorchFile } else { $null }"
R_TW = """    # the engine file is a cu128 wheel: on Pascal it is exactly the broken one
    $tw = if ($hasGpu -and $torchIndex -notmatch 'cu126') { EngineTorchFile } else { $null }"""

# 4. the index fallback: the pins and the force that Pascal needs, or uv keeps
#    the resident cu128 (PEP 440 considers `torch==2.8.0` satisfied by `2.8.0+cu128`)
A_FALLBACK = "    if (!$done) { Uvx @('pip', 'install', '--python', $py, 'torch', 'torchvision', 'torchaudio', '--index-url', $torchIndex)"
R_FALLBACK = r"""    if (!$done -and $torchIndex -match 'cu126') { Uvx @('pip', 'install', '--python', $py, 'torch==2.8.0', 'torchvision==0.23.0', 'torchaudio==2.8.0', '--index-url', $torchIndex, '--reinstall-package', 'torch', '--reinstall-package', 'torchvision', '--reinstall-package', 'torchaudio') 'PyTorch 2.8.0 cu126 для Pascal (GTX 10xx)' }
    elseif (!$done) { Uvx @('pip', 'install', '--python', $py, 'torch', 'torchvision', 'torchaudio', '--index-url', $torchIndex)"""

# 5. the probe demotes the card; it should mark it instead
A_PROBE = ("        if ($s.cc -and ([double]::Parse($s.cc, [Globalization.CultureInfo]::InvariantCulture) "
           "-lt 7.5)) { $s.gpuOld = $s.gpu; $s.gpu = $null; $s.vram = 0 }")
R_PROBE = r"""        # Pascal (6.x) и Volta (7.0) не имеют ядер в cu128, но имеют в cu126: карту не
        # выбрасываем (иначе ставится torch для процессора), а помечаем для индекса.
        if ($s.cc -and ([double]::Parse($s.cc, [Globalization.CultureInfo]::InvariantCulture) -lt 7.5)) {
          $s.gpuPascal = $s.gpu
          Say ('  [!]     ' + $s.gpu + ' (compute ' + $s.cc + '): Pascal/Volta — PyTorch будет cu126, генерация пойдёт; Pixal3D и flash-attn этой карте недоступны') 'Yellow'
        }"""

# --- installer\engine\engine.ps1 -------------------------------------------- #
A_ENGINE = """    if ($LASTEXITCODE -ne 0) {
      if ($hasGpu) {
        $whl = EngineTorchFile -Download"""
R_ENGINE = r"""    if ($LASTEXITCODE -ne 0) {
      # the engine wheel in the manifest is cu128 (sm_75+): on Pascal it installs
      # and then dies with "no kernel image", so such a card gets cu126 instead
      $engCc = 0.0
      try {
        $cap = (& nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null | Select-Object -First 1)
        if ("$cap".Trim() -match '^(\d+\.\d+)$') { $engCc = [double]::Parse($Matches[1], [Globalization.CultureInfo]::InvariantCulture) }
      } catch {}
      if ($hasGpu -and $engCc -gt 0 -and $engCc -lt 7.5) {
        Uvx @('pip', 'install', '--python', $py, 'torch==2.8.0', 'torchvision==0.23.0', 'torchaudio==2.8.0', '--index-url', 'https://download.pytorch.org/whl/cu126', '--reinstall-package', 'torch', '--reinstall-package', 'torchvision', '--reinstall-package', 'torchaudio') 'PyTorch 2.8.0 cu126 для Pascal (GTX 10xx)'
      } elseif ($hasGpu) {
        $whl = EngineTorchFile -Download"""


class Patch:
    """One named fix; several spellings because the installer changed versions."""

    def __init__(self, label, marker, *variants):
        self.label, self.marker = label, marker
        self.variants = variants      # (anchor, replacement)

    def apply(self, text):
        """Second run must change nothing: the replacement carries its own mark."""
        if self.marker in text:
            return text, "уже исправлено"
        for anchor, replacement in self.variants:
            if anchor in text:
                return text.replace(anchor, replacement, 1), "исправлено"
        return text, "пропущено (нет такой строки — другая версия установщика)"


PATCHES = {
    "installer/Install.ps1": (
        Patch("индекс PyTorch выбирается по карте",
              "elseif ($cc -gt 0 -and $cc -lt 7.5) { $torchIndex", (A_INDEX, R_INDEX)),
        Patch("torch переставляется, если в сборке нет sm_61",
              "$torchOk = ($LASTEXITCODE -eq 0)",
              (A_TORCH_OLD, R_TORCH_OLD), (A_TORCH_NEW, R_TORCH_NEW)),
        Patch("файл движка (cu128) не берётся на Pascal",
              "$torchIndex -notmatch 'cu126'", (A_TW, R_TW)),
        Patch("на Pascal ставится закреплённый набор cu126",
              "PyTorch 2.8.0 cu126 для Pascal", (A_FALLBACK, R_FALLBACK)),
        Patch("видеокарта не объявляется неподходящей",
              "$s.gpuPascal = $s.gpu", (A_PROBE, R_PROBE)),
    ),
    "installer/engine/engine.ps1": (
        Patch("движок ставится из cu126 на Pascal",
              "$engCc -gt 0 -and $engCc -lt 7.5", (A_ENGINE, R_ENGINE)),
    ),
}


def patch_file(path, patches, say=print) -> tuple:
    """Apply patches to one PowerShell file. Returns (applied, skipped)."""
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        say(f"  {target.name}: не читается ({exc})")
        return 0, len(patches)
    crlf = b"\r\n" in raw
    bom = raw.startswith(b"\xef\xbb\xbf")   # PowerShell 5.1 needs it for Cyrillic
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")

    changed, applied, skipped = False, 0, 0
    for patch in patches:
        new_text, verdict = patch.apply(text)
        if verdict == "исправлено":
            changed = True
            applied += 1
        elif verdict.startswith("пропущено"):
            skipped += 1
        else:
            applied += 1
        say(f"  · {patch.label}: {verdict}")
        text = new_text

    if changed:
        backup = target.with_suffix(target.suffix + ".bak")
        if not backup.exists():
            backup.write_bytes(raw)
            say(f"  копия: {backup.name}")
        if crlf:
            text = text.replace("\n", "\r\n")
        target.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return applied, skipped


def patch_studio(studio: str, say=print) -> int:
    """Patch every installer file that exists under the studio."""
    root = Path(studio)
    if not root.is_dir():
        say(f"студия не найдена: {studio}")
        return 2
    total_applied, total_skipped, touched = 0, 0, 0
    for relative, patches in PATCHES.items():
        path = root / relative
        if not path.is_file():
            say(f"{relative}: нет такого файла — пропускаю")
            continue
        touched += 1
        say(f"{relative}")
        applied, skipped = patch_file(path, patches, say)
        total_applied += applied
        total_skipped += skipped
    if not touched:
        say("ни одного файла установщика не нашлось — правка не применена")
        return 2
    say(f"\nитого: исправлено {total_applied}, пропущено {total_skipped}")
    if total_skipped:
        say("пропущенные — не ошибка: другая версия установщика. "
            "torch всё равно поставит CUDA-fix-torch.cmd")
    return 0


def find_studio(start: str = "") -> str:
    for base in (start, r"D:\AstraForge", r"C:\AstraForge"):
        if not base:
            continue
        path = Path(base)
        if (path / "installer" / "Install.ps1").is_file():
            return str(path)
    return ""


def report_card(say=print) -> dict:
    card = gpu_info()
    say(f"карта  : {card['name'] or '(nvidia-smi не ответил)'}  "
        f"cc {card['cc'] or '?'}  ({card['source'] or 'нет данных'})")
    say(f"индекс : {torch_index_for(card['cc'])}")
    return card


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="GTX 1080 Ti (Pascal, sm_61): правка установщика AstraForge")
    parser.add_argument("--studio", default="", help="папка студии")
    parser.add_argument("--patch-installer", default="", metavar="Install.ps1",
                        help="исправить один файл (без engine.ps1)")
    parser.add_argument("--card", action="store_true", help="только показать карту")
    parser.add_argument("--auto", action="store_true",
                        help="найти студию, исправить установщик, показать карту")
    args = parser.parse_args(argv)

    if args.patch_installer:
        applied, skipped = patch_file(args.patch_installer,
                                      PATCHES["installer/Install.ps1"])
        return 0 if applied or not skipped else 2
    if args.card:
        report_card()
        return 0
    if not args.auto:
        parser.print_help()
        return 0

    studio = find_studio(args.studio)
    if not studio:
        print("студия не найдена: искала installer\\Install.ps1 в "
              f"{args.studio or 'папке вызова'}, D:\\AstraForge, C:\\AstraForge")
        report_card()
        return 2
    code = patch_studio(studio)
    print()
    report_card()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
