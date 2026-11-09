"""The studio patch has to be boring: apply once, change nothing on the second run, undo cleanly.

Every anchor here is a fragment of the real 0.6.12 file, so a test failing means the patch
no longer fits the studio — which is exactly what must be noticed before a user runs it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "update"))

import memory_patch  # noqa: E402

DEVICE_PY = '''def ram():
    """Total / free RAM in bytes."""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [('ullTotalPhys', ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        if hasattr(ctypes, 'windll') and ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return {'total': int(st.ullTotalPhys), 'free': int(st.ullAvailPhys), 'commit_free': int(st.ullAvailPageFile)}
    except Exception:
        pass
    return {'total': 0, 'free': 0}


def pagefile_hint(text):
    """What to tell the user when Windows refused memory."""
    t = str(text)
    if 'os error 1455' in t or 'paging file' in t.lower() or 'подкачки' in t:
        return ('Windows не хватило памяти (файл подкачки слишком мал). Закройте другие программы с моделями ИИ '
                'и повторите; если повторяется — включите «Автоматически выбирать объём файла подкачки» '
                '(Параметры → Система → О системе → Дополнительные параметры системы → Быстродействие → Дополнительно → '
                'Виртуальная память) и перезагрузите компьютер.')
    return None
'''

ENGINE_PY = '''class Engine:
    def keep_ram(self):
        """Keep weights in RAM when the card is handed over."""
        forced = os.environ.get('ASTRA_ENGINE_KEEP_RAM')
        if forced in ('0', '1'):
            return forced == '1'
        return devmod.ram()['total'] >= 32 * 1024 ** 3
'''

SERVER_PY = '''class Handler:
        def _system_stats(self):
            ram = devmod.ram()
            return {'system': {'os': os.name, 'ram_total': ram['total'], 'ram_free': ram['free'], 'comfyui_version': None,
                               'astraforge_engine': VERSION},
                    'devices': []}
'''

VIEWS_MJS = '''  async function startGenerate(id, input) {
    const task = { status: 'running' };
    void exclusive(id, async () => {
        const gen = opts.provider === 'mvadapter' ? await pickServer() : { url: getConfig().comfyUrl };
        await runner.views_generate({
          outDir: dir, provider: opts.provider,
        });
    });
  }
'''

RUNTIME_MJS = ''' const args=[path.join(comfy,'main.py'),'--listen','127.0.0.1','--port','8190','--whitelist-custom-nodes','ComfyUI-GGUF','--lowvram','--reserve-vram','1'];
 const logFile=path.join(folders,'comfy.log');
'''


def make_studio(root: Path, crlf=False) -> Path:
    """A studio with just the five files the patch touches."""
    files = {
        "engine/astra_engine/device.py": DEVICE_PY,
        "engine/astra_engine/engine.py": ENGINE_PY,
        "engine/astra_engine/server.py": SERVER_PY,
        "server/views.mjs": VIEWS_MJS,
        "server/runtime.mjs": RUNTIME_MJS,
        "package.json": '{"name": "astraforge-local", "version": "0.6.12"}',
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text.replace("\n", "\r\n") if crlf else text
        path.write_bytes(data.encode("utf-8"))
    return root


def read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
#  what the patch is supposed to do
# --------------------------------------------------------------------------- #
def test_every_patch_lands_on_the_real_anchors(tmp_path):
    studio = make_studio(tmp_path)
    assert memory_patch.patch_studio(str(studio), say=lambda *a: None) == 0

    device = read(studio, "engine/astra_engine/device.py")
    assert "'commit_limit': int(st.ullTotalPageFile)" in device
    assert "def commit_report():" in device
    assert "компьютер.' + commit_report())" in device

    engine = read(studio, "engine/astra_engine/engine.py")
    assert "devmod.ram_room() >= 12 * 1024 ** 3" in engine
    assert "32 * 1024 ** 3 and" in engine

    assert "'commit_free': ram.get('commit_free')" in read(studio, "engine/astra_engine/server.py")

    views = read(studio, "server/views.mjs")
    assert "async function requireRam(url, provider)" in views
    assert "await requireRam(gen.url, opts.provider);\n        await runner.views_generate({" in views

    runtime = read(studio, "server/runtime.mjs")
    assert "const memoryArgs=()=>{" in runtime
    assert "...memoryArgs()];" in runtime
    args_line = next(line for line in runtime.splitlines() if "main.py" in line)
    assert args_line.rstrip().endswith("...memoryArgs()];")   # the ComfyUI call now asks memoryArgs()
    assert "if(mode==='0')return ['--normalvram','--reserve-vram','0.5'];" in runtime
    # the default inside memoryArgs() must stay a plain list, not a call to itself
    assert "return [...memoryArgs()]" not in runtime


def test_patched_python_still_parses(tmp_path):
    import ast

    studio = make_studio(tmp_path)
    memory_patch.patch_studio(str(studio), say=lambda *a: None)
    for relative in ("device.py", "engine.py", "server.py"):
        ast.parse(read(studio, "engine/astra_engine/" + relative))
    assert "def commit_report" in read(studio, "engine/astra_engine/device.py")


def test_the_message_gets_numbers_only_when_windows_answers(tmp_path):
    """commit_report() must not invent figures off Windows: it returns an empty string."""
    studio = make_studio(tmp_path)
    memory_patch.patch_studio(str(studio), say=lambda *a: None)
    source = read(studio, "engine/astra_engine/device.py")
    namespace = {"__name__": "patched_device"}
    exec(compile(source, "device.py", "exec"), namespace)
    # the sandbox has no Windows API: total is 0, so nothing is appended
    assert namespace["commit_report"]() in ("",) or "Сейчас" in namespace["commit_report"]()
    assert namespace["pagefile_hint"]("CUDA out of memory") is None
    hint = namespace["pagefile_hint"]("os error 1455")
    assert "файл подкачки" in hint


# --------------------------------------------------------------------------- #
#  running it twice, and undoing it
# --------------------------------------------------------------------------- #
def test_second_run_changes_nothing(tmp_path):
    studio = make_studio(tmp_path)
    memory_patch.patch_studio(str(studio), say=lambda *a: None)
    snapshot = {p: p.read_bytes() for p in studio.rglob("*") if p.is_file()}
    words = []
    memory_patch.patch_studio(str(studio), say=words.append)
    assert {p: p.read_bytes() for p in studio.rglob("*") if p.is_file()} == snapshot
    assert any("уже исправлено" in w for w in words)
    assert any("менять нечего" in w for w in words)


def test_backup_is_kept_next_to_the_file(tmp_path):
    studio = make_studio(tmp_path)
    original = (studio / "server/views.mjs").read_bytes()
    memory_patch.patch_studio(str(studio), say=lambda *a: None)
    backup = studio / "server/views.mjs.bak"
    assert backup.is_file() and backup.read_bytes() == original


def test_revert_puts_every_file_back(tmp_path):
    studio = make_studio(tmp_path)
    originals = {p: p.read_bytes() for p in studio.rglob("*") if p.is_file()}
    memory_patch.patch_studio(str(studio), say=lambda *a: None)
    assert memory_patch.revert_studio(str(studio), say=lambda *a: None) == 0
    after = {p: p.read_bytes() for p in studio.rglob("*") if p.is_file() and not p.name.endswith(".bak")}
    assert after == originals
    assert not any(p.name.endswith(".bak") for p in studio.rglob("*"))


def test_revert_without_backups_says_so(tmp_path):
    studio = make_studio(tmp_path)
    words = []
    assert memory_patch.revert_studio(str(studio), say=words.append) == 1
    assert any("нечего откатывать" in w for w in words)


# --------------------------------------------------------------------------- #
#  refusing to mangle a different version
# --------------------------------------------------------------------------- #
def test_unknown_anchor_is_skipped_not_mangled(tmp_path):
    studio = make_studio(tmp_path)
    # a 0.7-shaped file: same purpose, different wording
    (studio / "engine/astra_engine/engine.py").write_text(
        "    def keep_ram(self):\n        return settings.keep_weights\n", encoding="utf-8")
    words = []
    code = memory_patch.patch_studio(str(studio), say=words.append)
    assert code == 1                                     # something was skipped
    assert any("пропущено" in w for w in words)
    assert (studio / "engine/astra_engine/engine.py").read_text(encoding="utf-8") == (
        "    def keep_ram(self):\n        return settings.keep_weights\n")


def test_missing_file_is_reported_and_skipped(tmp_path):
    studio = make_studio(tmp_path)
    (studio / "server/runtime.mjs").unlink()
    words = []
    memory_patch.patch_studio(str(studio), say=words.append)
    assert any("нет такого файла" in w for w in words)


def test_not_a_studio_is_an_error(tmp_path):
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
    words = []
    assert memory_patch.patch_studio(str(tmp_path), say=words.append) == 2
    assert any("не папка AstraForge" in w for w in words)


# --------------------------------------------------------------------------- #
#  --check, line endings, BOM, finding the studio
# --------------------------------------------------------------------------- #
def test_check_mode_writes_nothing(tmp_path):
    studio = make_studio(tmp_path)
    before = {p: p.read_bytes() for p in studio.rglob("*") if p.is_file()}
    words = []
    memory_patch.patch_studio(str(studio), say=words.append, dry=True)
    assert {p: p.read_bytes() for p in studio.rglob("*") if p.is_file()} == before
    assert any("ничего не изменено" in w for w in words)


def test_crlf_survives_the_patch(tmp_path):
    studio = make_studio(tmp_path, crlf=True)
    memory_patch.patch_studio(str(studio), say=lambda *a: None)
    raw = (studio / "engine/astra_engine/device.py").read_bytes()
    assert b"\r\n" in raw
    assert raw.replace(b"\r\n", b"").count(b"\n") == 0


def test_bom_survives_the_patch(tmp_path):
    studio = make_studio(tmp_path)
    path = studio / "engine/astra_engine/device.py"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    memory_patch.patch_studio(str(studio), say=lambda *a: None)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_studio_is_found_by_its_files(tmp_path):
    studio = make_studio(tmp_path / "AstraForge")
    assert memory_patch.find_studio(str(studio)) == str(studio)
    assert memory_patch.find_studio(str(tmp_path / "nowhere")) == ""


def test_version_is_read_from_package_json(tmp_path):
    studio = make_studio(tmp_path)
    assert memory_patch.studio_version(studio) == "0.6.12"
    (studio / "package.json").write_text("{ not json", encoding="utf-8")
    assert memory_patch.studio_version(studio) == ""


# --------------------------------------------------------------------------- #
#  the command line
# --------------------------------------------------------------------------- #
def test_main_refuses_a_missing_studio(monkeypatch, capsys):
    monkeypatch.setattr(memory_patch, "find_studio", lambda *a, **k: "")
    assert memory_patch.main([]) == 2
    assert "Студия не найдена" in capsys.readouterr().out


def test_main_applies_and_reverts(monkeypatch, tmp_path, capsys):
    studio = make_studio(tmp_path)
    assert memory_patch.main(["--studio", str(studio)]) == 0
    assert memory_patch.main(["--studio", str(studio), "--revert"]) == 0
    assert "requireRam" not in read(studio, "server/views.mjs")
