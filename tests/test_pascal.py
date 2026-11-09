"""A GTX 1080 Ti is compute 6.1, and AstraForge 0.6.12 treats such a card as absent.

Two things must therefore be pinned: which wheel a card needs, and that the
installer keeps choosing it — on a fresh install, on a reinstall, and in both
venvs (the studio's ComfyUI and the generation engine). The installer changed
between 0.3.0 and 0.6.12, so both spellings are exercised here.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

import pascal  # noqa: E402


def brace_depth(path):
    """Crude brace/paren balance, skipping comments and quoted strings."""
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    depth = {"{": 0, "(": 0}
    pairs = {"}": "{", ")": "("}
    i, comment = 0, False
    while i < len(text):
        char = text[i]
        if char == "\n":
            comment = False
        elif comment:
            pass
        elif char == "#":
            comment = True
        elif char in "'\"":
            quote = char
            i += 1
            while i < len(text):
                if text[i] == "`":
                    i += 1
                elif text[i] == quote:
                    if text[i + 1:i + 2] == quote:
                        i += 1
                    else:
                        break
                i += 1
        elif char in depth:
            depth[char] += 1
        elif char in pairs:
            depth[pairs[char]] -= 1
        i += 1
    return depth


# --------------------------------------------------------------------------- #
#  which wheel a card needs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cc,expected", [
    (6.0, pascal.CU126),   # Pascal
    (6.1, pascal.CU126),   # GTX 1080 Ti — the card this was written for
    (7.0, pascal.CU126),   # Volta: cu128 builds start at sm_75
    (7.5, pascal.CU128),   # Turing
    (8.6, pascal.CU128),
    (8.9, pascal.CU128),
    (0, pascal.CU126),     # unknown card: the index that still has every arch
])
def test_the_index_follows_the_card(cc, expected):
    assert pascal.torch_index_for(cc) == expected


@pytest.mark.parametrize("name,cc", [
    ("NVIDIA GeForce GTX 1080 Ti", 6.1),
    ("NVIDIA GeForce GTX 1660 SUPER", 6.1),
    ("Tesla P40", 6.1),
    ("Tesla V100-SXM2", 7.0),
    ("NVIDIA GeForce RTX 2060", 7.5),
    ("NVIDIA GeForce RTX 4070 Ti", 8.9),
    ("NVIDIA GeForce RTX 5090", 12.0),
    ("Some Unreleased Thing", 0),
])
def test_a_name_is_the_fallback_for_an_old_driver(name, cc):
    assert pascal.cc_from_name(name) == cc


class Smi:
    def __init__(self, out="", error=None):
        self.out, self.error = out, error

    def __call__(self, cmd, **kw):
        if self.error:
            raise self.error

        class Done:
            stdout, stderr, returncode = self.out, "", 0
        return Done()


def test_compute_capability_comes_from_the_driver_when_it_can(monkeypatch):
    monkeypatch.setattr(pascal.subprocess, "run",
                        Smi("NVIDIA GeForce GTX 1080 Ti, 6.1, 11264\n"))
    info = pascal.gpu_info()
    assert (info["cc"], info["vram_mb"]) == (6.1, 11264)
    assert "compute_cap" in info["source"]


def test_an_old_driver_gets_the_capability_from_the_name(monkeypatch):
    monkeypatch.setattr(pascal.subprocess, "run",
                        Smi("NVIDIA GeForce GTX 1080 Ti, [N/A], 11264\n"))
    assert pascal.gpu_info()["cc"] == 6.1


def test_no_driver_at_all_is_not_a_crash(monkeypatch):
    monkeypatch.setattr(pascal.subprocess, "run", Smi(error=OSError("нет nvidia-smi")))
    info = pascal.gpu_info()
    assert info["cc"] == 0 and info["name"] == ""


# --------------------------------------------------------------------------- #
#  the two installer generations, quoted from the real files
# --------------------------------------------------------------------------- #
# installer\Install.ps1 of 0.6.12 (the studio in the user's archive): the probe
# demotes a card below 7.5, the index is chosen by "is there a GPU at all", and
# torch is installed only when `import torch` fails.
HIS = """\ufeff# AstraForge — установщик-мастер (Windows 10/11 x64)\r
        if ($s.cc -and ([double]::Parse($s.cc, [Globalization.CultureInfo]::InvariantCulture) -lt 7.5)) { $s.gpuOld = $s.gpu; $s.gpu = $null; $s.vram = 0 }\r
function InstallComfyDeps($dir, $hasGpu) {\r
  $torchIndex = if ($hasGpu) { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }\r
  Native { & $py -c "import torch" 2>$null | Out-Null }\r
  if ($LASTEXITCODE -ne 0) {\r
    $tw = if ($hasGpu) { EngineTorchFile } else { $null }\r
    $done = $false\r
    if (!$done) { Uvx @('pip', 'install', '--python', $py, 'torch', 'torchvision', 'torchaudio', '--index-url', $torchIndex) 'PyTorch' }\r
  }\r
}\r
"""

# installer\Install.ps1 of 0.3.0: one line for both, no probe of compute at all
OLD = """\ufeff# AstraForge - setup wizard (Windows 10/11 x64).\r
function InstallComfyDeps($dir, $hasGpu) {\r
  $torchIndex = if ($hasGpu) { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }\r
  Native { & $py -c "import torch" 2>$null | Out-Null }\r
  if ($LASTEXITCODE -ne 0) { Run $uv @('pip', 'install', '--python', $py, 'torch', 'torchvision', 'torchaudio', '--index-url', $torchIndex) 'PyTorch' }\r
}\r
"""

# installer\engine\engine.ps1 of 0.6.12: the engine installs a pre-downloaded
# cu128 wheel, which is the same broken thing in the second venv
ENGINE = """\ufeff# Движок AstraForge\r
function InstallEngine($hasGpu) {\r
  if ($script:AmdGpu) {\r
    InstallAmdTorch $py\r
  } else {\r
    Native { & $py -c "import torch" 2>$null | Out-Null }\r
    if ($LASTEXITCODE -ne 0) {\r
      if ($hasGpu) {\r
        $whl = EngineTorchFile -Download\r
        Uvx @('pip', 'install', '--python', $py, $whl) 'PyTorch для NVIDIA'\r
      } else {\r
        Uvx @('pip', 'install', '--python', $py, 'torch==2.8.0') 'PyTorch для процессора'\r
      }\r
    }\r
  }\r
}\r
"""

INSTALL_PATCHES = pascal.PATCHES["installer/Install.ps1"]
ENGINE_PATCHES = pascal.PATCHES["installer/engine/engine.ps1"]


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def test_the_new_installer_gets_all_six_fixes(tmp_path):
    installer = write(tmp_path, "Install.ps1", HIS)
    engine = write(tmp_path, "engine.ps1", ENGINE)
    said = []
    assert pascal.patch_file(installer, INSTALL_PATCHES, say=said.append)[1] == 0
    assert pascal.patch_file(engine, ENGINE_PATCHES, say=said.append)[1] == 0

    text = installer.read_text(encoding="utf-8-sig")
    # the wheel index follows the card instead of the fact that a card exists
    assert "'https://download.pytorch.org/whl/cu126'" in text
    assert "compute_cap" in text
    # the card is no longer thrown away as "too old"
    assert "$s.gpuPascal = $s.gpu" in text
    assert "$s.gpu = $null; $s.vram = 0" not in text
    # an import is no longer proof that the build can run on this card
    assert "$torchOk = ($LASTEXITCODE -eq 0)" in text
    assert "get_arch_list()" in text
    # the cu128 engine wheel is skipped, and the pinned cu126 trio is forced in
    assert "$torchIndex -notmatch 'cu126'" in text
    assert "'--reinstall-package', 'torch'" in text
    assert "PyTorch 2.8.0 cu126 для Pascal" in text

    engine_text = engine.read_text(encoding="utf-8-sig")
    assert "$engCc" in engine_text and "cu126" in engine_text
    assert "'--reinstall-package', 'torch'" in engine_text
    # the CPU branch is still there and still last
    assert "PyTorch для процессора" in engine_text


def test_both_files_stay_balanced_and_byte_hygienic(tmp_path):
    """A Cyrillic .ps1 needs its BOM, and a stray brace would kill the installer."""
    for name, text in (("Install.ps1", HIS), ("engine.ps1", ENGINE)):
        path = write(tmp_path, name, text)
        assert brace_depth(path) == {"{": 0, "(": 0}, f"{name} не был сбалансирован"
        patches = INSTALL_PATCHES if name == "Install.ps1" else ENGINE_PATCHES
        pascal.patch_file(path, patches, say=lambda *_: None)
        assert brace_depth(path) == {"{": 0, "(": 0}, f"{name} сломан правкой"
        raw = path.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), f"{name}: BOM пропал"
        assert raw.count(b"\r\n") == raw.count(b"\n"), f"{name}: смешались переводы строк"


def test_the_original_is_kept_beside_it_and_rerun_changes_nothing(tmp_path):
    path = write(tmp_path, "Install.ps1", HIS)
    pascal.patch_file(path, INSTALL_PATCHES, say=lambda *_: None)
    once = path.read_bytes()
    assert path.with_suffix(".ps1.bak").read_bytes() == HIS.encode("utf-8")

    said = []
    applied, skipped = pascal.patch_file(path, INSTALL_PATCHES, say=said.append)
    assert path.read_bytes() == once, "второй прогон не должен ничего менять"
    assert skipped == 0
    assert sum("уже исправлено" in line for line in said) == len(INSTALL_PATCHES)


def test_the_old_installer_gets_what_it_has_and_a_clear_skip_for_the_rest(tmp_path):
    path = write(tmp_path, "Install.ps1", OLD)
    said = []
    applied, skipped = pascal.patch_file(path, INSTALL_PATCHES, say=said.append)
    text = path.read_text(encoding="utf-8-sig")
    assert "cu126" in text and "$torchOk" in text
    assert "PyTorch 2.8.0 cu126 (Pascal)" in text
    assert skipped == 3, "у 0.3.0 нет probe, файла движка и ветки с --upgrade"
    assert sum("другая версия установщика" in line for line in said) == 3
    assert brace_depth(path) == {"{": 0, "(": 0}


def test_a_foreign_file_is_refused_not_mangled(tmp_path):
    path = write(tmp_path, "Install.ps1", "# что-то другое\n")
    applied, skipped = pascal.patch_file(path, INSTALL_PATCHES, say=lambda *_: None)
    assert (applied, skipped) == (0, len(INSTALL_PATCHES))
    assert path.read_text(encoding="utf-8") == "# что-то другое\n"
    assert not path.with_suffix(".ps1.bak").exists()


# --------------------------------------------------------------------------- #
#  finding the studio and patching both files
# --------------------------------------------------------------------------- #
def make_studio(tmp_path):
    studio = tmp_path / "AstraForge"
    write(studio, "installer/Install.ps1", HIS)
    write(studio, "installer/engine/engine.ps1", ENGINE)
    return studio


def test_the_studio_is_found_by_layout_not_by_name(tmp_path):
    studio = make_studio(tmp_path)
    assert pascal.find_studio(str(studio)) == str(studio)
    assert pascal.find_studio(str(tmp_path)) == ""
    assert pascal.find_studio("") == "", "без подсказки ищутся только стандартные пути"


def test_patch_studio_touches_both_venvs(tmp_path):
    studio = make_studio(tmp_path)
    said = []
    assert pascal.patch_studio(str(studio), say=said.append) == 0
    assert "cu126" in (studio / "installer" / "Install.ps1").read_text(encoding="utf-8-sig")
    assert "$engCc" in (studio / "installer" / "engine" / "engine.ps1").read_text(
        encoding="utf-8-sig")
    assert any("итого" in line for line in said)


def test_a_studio_without_an_installer_says_so(tmp_path):
    said = []
    assert pascal.patch_studio(str(tmp_path), say=said.append) == 2
    assert any("ни одного файла установщика" in line for line in said)


def test_auto_reports_the_card_even_when_there_is_nothing_to_patch(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pascal.subprocess, "run",
                        Smi("NVIDIA GeForce GTX 1080 Ti, 6.1, 11264\n"))
    assert pascal.main(["--studio", str(tmp_path), "--auto"]) == 2
    out = capsys.readouterr().out
    assert "студия не найдена" in out
    assert "cu126" in out
