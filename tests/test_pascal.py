"""A GTX 1080 Ti is compute 6.1, and that number decides which torch it gets.

The failure is nasty: `torch.cuda.is_available()` is True, the card is listed,
and the first kernel dies with "no kernel image is available". These tests pin
the two decisions that follow — which wheel a card needs, and that the installer
keeps choosing it after a reinstall.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

import pascal  # noqa: E402


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
    ("NVIDIA GeForce GTX 1060 6GB", 6.1),
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


# --------------------------------------------------------------------------- #
#  reading the card
# --------------------------------------------------------------------------- #
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
    assert info["cc"] == 6.1
    assert info["name"] == "NVIDIA GeForce GTX 1080 Ti"
    assert info["vram_mb"] == 11264
    assert "compute_cap" in info["source"]


def test_an_old_driver_gets_the_capability_from_the_name(monkeypatch):
    monkeypatch.setattr(pascal.subprocess, "run",
                        Smi("NVIDIA GeForce GTX 1080 Ti, [N/A], 11264\n"))
    info = pascal.gpu_info()
    assert info["cc"] == 6.1
    assert "по имени" in info["source"]


def test_no_driver_at_all_is_not_a_crash(monkeypatch):
    monkeypatch.setattr(pascal.subprocess, "run",
                        Smi(error=OSError("nvidia-smi not found")))
    info = pascal.gpu_info()
    assert info["cc"] == 0 and info["name"] == ""
    assert pascal.torch_index_for(info["cc"]) == pascal.CU126


# --------------------------------------------------------------------------- #
#  the installer patch
# --------------------------------------------------------------------------- #
INSTALLER = """\ufeff# AstraForge - установщик\r
function InstallComfyDeps($dir, $hasGpu) {\r
  $uv = Uv\r
  $torchIndex = if ($hasGpu) { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }\r
  Native { & $py -c "import torch" 2>$null | Out-Null }\r
  if ($LASTEXITCODE -ne 0) { Run $uv @('pip', 'install', '--python', $py, 'torch', 'torchvision', 'torchaudio', '--index-url', $torchIndex) 'PyTorch' }\r
  Run $uv @('pip', 'install', '--python', $py, '-r', (Join-Path $dir 'requirements.txt')) 'ComfyUI libraries'\r
}\r
"""


def write_installer(tmp_path, text=INSTALLER):
    path = tmp_path / "Install.ps1"
    path.write_bytes(text.encode("utf-8"))
    return path


def test_the_patch_sends_pascal_to_cu126_and_nobody_else(tmp_path):
    path = write_installer(tmp_path)
    assert pascal.patch_installer(path) == 0
    text = path.read_text(encoding="utf-8-sig")
    assert "cu126" in text and pascal.MARKER in text
    assert "nvidia-smi --query-gpu=compute_cap" in text
    # the unconditional cu128 line is gone, and so is the import-only check
    assert "if ($hasGpu) { 'https://download.pytorch.org/whl/cu128' }" not in text
    assert "get_arch_list()" in text, "наличие sm_61 должно проверяться, а не только import"
    # 6.1 must be reachable from the name fallback as well
    assert "GTX" in text


def test_the_patch_keeps_the_file_byte_hygienic(tmp_path):
    """A Cyrillic .ps1 without a BOM is mojibake in PowerShell 5.1."""
    path = write_installer(tmp_path)
    pascal.patch_installer(path)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "BOM пропал"
    assert raw.count(b"\r\n") == raw.count(b"\n"), "смешались переводы строк"
    assert "установщик".encode("utf-8") in raw


def test_the_original_is_kept_beside_it(tmp_path):
    path = write_installer(tmp_path)
    pascal.patch_installer(path)
    assert path.with_suffix(".ps1.bak").read_bytes() == INSTALLER.encode("utf-8")


def test_the_patch_is_idempotent(tmp_path):
    path = write_installer(tmp_path)
    pascal.patch_installer(path)
    once = path.read_bytes()
    said = []
    assert pascal.patch_installer(path, say=said.append) == 0
    assert path.read_bytes() == once, "второй прогон не должен ничего менять"
    assert "второй раз ничего не меняю" in "\n".join(said)


def test_a_foreign_file_is_refused_not_mangled(tmp_path):
    path = tmp_path / "Install.ps1"
    path.write_text("# something else\n", encoding="utf-8")
    assert pascal.patch_installer(path) == 2
    assert path.read_text(encoding="utf-8") == "# something else\n"
    assert not path.with_suffix(".ps1.bak").exists()


def test_the_installer_is_found_by_layout_not_by_name(tmp_path):
    studio = tmp_path / "Whatever it is called"
    (studio / "installer").mkdir(parents=True)
    (studio / "installer" / "Install.ps1").write_text(INSTALLER, encoding="utf-8")
    assert pascal.find_installer(str(studio)) == str(studio / "installer" / "Install.ps1")
    assert pascal.find_installer(str(tmp_path / "empty")) == ""


def test_auto_patches_what_it_finds_and_reports_the_card(tmp_path, monkeypatch):
    studio = tmp_path / "AstraForge"
    (studio / "installer").mkdir(parents=True)
    (studio / "installer" / "Install.ps1").write_text(INSTALLER, encoding="utf-8")
    monkeypatch.setattr(pascal.subprocess, "run",
                        Smi("NVIDIA GeForce GTX 1080 Ti, 6.1, 11264\n"))
    assert pascal.main(["--studio", str(studio), "--auto"]) == 0
    assert pascal.MARKER in (studio / "installer" / "Install.ps1").read_text(
        encoding="utf-8-sig")


def test_a_missing_installer_does_not_fail_the_run(tmp_path, monkeypatch, capsys):
    """The studio can be updated or trimmed; the card report still matters."""
    monkeypatch.setattr(pascal.subprocess, "run",
                        Smi("NVIDIA GeForce GTX 1080 Ti, 6.1, 11264\n"))
    assert pascal.main(["--studio", str(tmp_path), "--auto"]) == 0
    out = capsys.readouterr().out
    assert "не найден" in out
    assert "cu126" in out
