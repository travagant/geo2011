"""«os error 1455» is about the commit limit, not free RAM — so the arithmetic must be right.

What is pinned here: the verdict (does a 10.5 GB load fit into what Windows still
allows), the page file figure the advice asks for, the reading of «ollama ps», and that
the PowerShell command we hand to an elevated shell survives encoding intact.
"""

import base64
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

import ram_check  # noqa: E402

GB = ram_check.GB
MB = ram_check.MB


def status(total_gb=16, free_gb=2, pagefile_gb=2, commit_free_gb=1):
    """A machine as GlobalMemoryStatusEx reports it."""
    return {'total': int(total_gb * GB), 'free': int(free_gb * GB),
            'commit_limit': int((total_gb + pagefile_gb) * GB),
            'commit_free': int(commit_free_gb * GB)}


# --------------------------------------------------------------------------- #
#  the verdict
# --------------------------------------------------------------------------- #
def test_views_do_not_fit_a_16gb_pc_with_a_2gb_pagefile():
    """SDXL 6.9 + adapter 3.6 = 10.5 GB, plus 3 GB the engine keeps for the system."""
    v = ram_check.verdict(status(commit_free_gb=1), ram_check.NEED_MB['views'])
    assert v['ok'] is False
    assert v['need'] == ram_check.NEED_MB['views'] + ram_check.RESERVE_MB
    assert v['have'] == 1024
    assert v['short'] == v['need'] - 1024
    assert v['pagefile'] == 2048


def test_a_task_that_fits_is_not_told_to_change_anything():
    v = ram_check.verdict(status(total_gb=32, free_gb=20, pagefile_gb=16, commit_free_gb=18),
                          ram_check.NEED_MB['views'])
    assert v['ok'] is True
    assert v['advice'] == []
    assert v['pagefile_after'] == v['pagefile'] == 16 * 1024


def test_the_pagefile_figure_closes_the_gap_and_is_rounded_up():
    """Advice has to be a number a person can type: whole GB, and it must be enough."""
    v = ram_check.verdict(status(pagefile_gb=2, commit_free_gb=1), ram_check.NEED_MB['views'])
    assert v['pagefile_after'] >= v['pagefile'] + v['short']
    assert v['pagefile_after'] % 1024 == 0
    assert v['pagefile_after'] - (v['pagefile'] + v['short']) < 1024


def test_no_pagefile_at_all_is_reported_as_zero_not_as_a_crash():
    """Virtual memory switched off entirely: commit limit equals RAM."""
    st = status(total_gb=16, free_gb=9, pagefile_gb=0, commit_free_gb=0)
    assert ram_check.pagefile_bytes(st) == 0
    v = ram_check.verdict(st, 1000)
    assert v['ok'] is False
    assert v['short'] == 1000 + 3072


def test_the_engine_reserve_can_be_changed_for_other_tasks():
    """models.py keeps 3 GB by default; the number is a parameter, not a constant."""
    st = status(total_gb=64, free_gb=30, pagefile_gb=32, commit_free_gb=8)
    assert ram_check.verdict(st, 5000, reserve_mb=0)['ok'] is True
    assert ram_check.verdict(st, 6000, reserve_mb=3072)['ok'] is False


# --------------------------------------------------------------------------- #
#  «ollama ps»
# --------------------------------------------------------------------------- #
OLLAMA_PS = """NAME            ID              SIZE      PROCESSOR    UNTIL
qwen3:8b        500a1f067a9f    5.6 GB    100% GPU     4 minutes from now
defiant9b       d7eb4fac1234    6.98 GB   100% GPU     3 minutes from now
"""


def test_ollama_ps_is_read_into_models_and_sizes():
    def runner(argv, **kw):
        assert argv[:2] == ['ollama', 'ps']
        return subprocess.CompletedProcess(argv, 0, OLLAMA_PS, '')

    models = ram_check.ollama_loaded(runner)
    assert [m['name'] for m in models] == ['qwen3:8b', 'defiant9b']
    assert models[0]['size_mb'] == int(5.6 * 1024)
    assert models[1]['size_mb'] == int(6.98 * 1024)


def test_no_ollama_installed_is_an_empty_list_not_an_error():
    def missing(argv, **kw):
        raise FileNotFoundError('ollama')

    assert ram_check.ollama_loaded(missing) == []


def test_ollama_ps_that_fails_is_treated_as_nothing_loaded():
    def failing(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, '', 'Error: could not connect')

    assert ram_check.ollama_loaded(failing) == []


def test_free_ollama_stops_every_loaded_model():
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        if argv[1] == 'ps':
            return subprocess.CompletedProcess(argv, 0, OLLAMA_PS, '')
        return subprocess.CompletedProcess(argv, 0, '', '')

    stopped = ram_check.free_ollama(runner)
    assert stopped == ['qwen3:8b', 'defiant9b']
    assert ['ollama', 'stop', 'defiant9b'] in calls


# --------------------------------------------------------------------------- #
#  the command for the elevated shell
# --------------------------------------------------------------------------- #
def test_powershell_script_is_utf16_base64_of_itself():
    """-EncodedCommand takes UTF-16LE base64: no quotes, no code page, no Cyrillic loss."""
    script = ram_check.pagefile_script('auto', status(), 10500, [{'path': 'C:\\', 'free': 40 * GB}])
    enc = ram_check.encoded_command(script)
    assert enc == base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    assert base64.b64decode(enc).decode('utf-16-le') == script


def test_automatic_pagefile_only_touches_the_computer_system_switch():
    script = ram_check.pagefile_script('auto', status(), 10500, [])
    assert 'AutomaticManagedPagefile = $true' in script
    assert 'Win32_PageFileSetting' in script  # only to show the result
    assert 'New-CimInstance' not in script


def test_fixed_pagefile_goes_to_the_disk_with_most_room_and_is_big_enough():
    drives = [{'path': 'C:\\', 'free': 5 * GB}, {'path': 'D:\\', 'free': 120 * GB}]
    script = ram_check.pagefile_script('fixed', status(pagefile_gb=2, commit_free_gb=1), 10500, drives)
    assert "'D:\\\\pagefile.sys'" in script or "'D:\\pagefile.sys'" in script
    assert 'C:\\pagefile.sys' not in script
    want = ram_check.verdict(status(pagefile_gb=2, commit_free_gb=1), 10500)['pagefile_after']
    assert 'MaximumSize = %d' % want in script
    assert 'AutomaticManagedPagefile = $false' in script
    assert 'Restart Windows' in script


def test_fixed_pagefile_never_asks_for_less_than_8gb():
    """Even a small gap gets a respectable file: sizing it to the gap alone leaves
    nothing for the next task."""
    huge = status(total_gb=128, free_gb=120, pagefile_gb=8, commit_free_gb=7)
    script = ram_check.pagefile_script('fixed', huge, 3000, [{'path': 'C:\\', 'free': 200 * GB}])
    assert 'MaximumSize = 8192' in script


# --------------------------------------------------------------------------- #
#  the text a person reads
# --------------------------------------------------------------------------- #
def test_report_says_it_does_not_fit_and_names_the_numbers():
    text, ok = ram_check.report(status(commit_free_gb=1), ram_check.NEED_MB['views'],
                                'Ракурсы (MV-Adapter)', [], [{'path': 'C:\\', 'free': 40 * GB}])
    assert ok is False
    assert 'os error 1455' in text
    assert 'не хватает' in text
    v = ram_check.verdict(status(commit_free_gb=1), ram_check.NEED_MB['views'])
    assert ram_check.gib(v['need'] * MB) in text
    assert ram_check.gib(v['short'] * MB) in text


def test_report_shows_ollama_and_how_to_stop_it():
    text, _ = ram_check.report(status(commit_free_gb=1), 10500, 'Ракурсы',
                               [{'name': 'qwen3:8b', 'size_mb': 5734, 'processor': 'GPU', 'until': ''}],
                               [])
    assert 'ollama stop qwen3:8b' in text


def test_report_warns_when_there_is_no_room_on_disk_for_automatic_growth():
    text, _ = ram_check.report(status(commit_free_gb=1), 10500, 'Ракурсы', [],
                               [{'path': 'C:\\', 'free': 4 * GB}])
    assert 'меньше 16 ГБ' in text


def test_report_for_a_task_that_fits_points_at_the_other_culprit():
    text, ok = ram_check.report(status(total_gb=32, free_gb=20, pagefile_gb=16, commit_free_gb=18),
                                10500, 'Ракурсы', [], [])
    assert ok is True
    assert 'проходит' in text


# --------------------------------------------------------------------------- #
#  command line
# --------------------------------------------------------------------------- #
def test_unknown_argument_is_refused(monkeypatch):
    monkeypatch.setattr(ram_check, 'memory_status', lambda: status())
    assert ram_check.main(['--wat']) == 2


def test_not_windows_exits_with_its_own_code(monkeypatch):
    monkeypatch.setattr(ram_check, 'memory_status', lambda: None)
    assert ram_check.main([]) == 2


def test_need_in_mb_overrides_the_default(monkeypatch, capsys):
    monkeypatch.setattr(ram_check, 'memory_status', lambda: status(commit_free_gb=1))
    monkeypatch.setattr(ram_check, 'ollama_loaded', lambda *a, **k: [])
    monkeypatch.setattr(ram_check, 'disks', lambda *a, **k: [])
    assert ram_check.main(['--need', '2000']) == 1
    out = capsys.readouterr().out
    assert '2000' not in out.replace('2.0 ГБ', '')  # printed as GB, not raw MB
    assert 'Задача' in out


def test_exit_code_is_zero_when_it_fits(monkeypatch, capsys):
    monkeypatch.setattr(ram_check, 'memory_status',
                        lambda: status(total_gb=32, free_gb=20, pagefile_gb=16, commit_free_gb=20))
    monkeypatch.setattr(ram_check, 'ollama_loaded', lambda *a, **k: [])
    monkeypatch.setattr(ram_check, 'disks', lambda *a, **k: [])
    assert ram_check.main([]) == 0


def test_json_output_has_the_verdict(monkeypatch, capsys):
    import json

    monkeypatch.setattr(ram_check, 'memory_status', lambda: status(commit_free_gb=1))
    monkeypatch.setattr(ram_check, 'ollama_loaded', lambda *a, **k: [])
    monkeypatch.setattr(ram_check, 'disks', lambda *a, **k: [])
    ram_check.main(['--json'])
    data = json.loads(capsys.readouterr().out)
    assert data['ok'] is False
    assert data['verdict']['short'] > 0
    assert data['status']['commit_limit'] > data['status']['total']


def test_pagefile_auto_action_prints_only_the_encoded_command(monkeypatch, capsys):
    monkeypatch.setattr(ram_check, 'memory_status', lambda: status(commit_free_gb=1))
    monkeypatch.setattr(ram_check, 'ollama_loaded', lambda *a, **k: [])
    monkeypatch.setattr(ram_check, 'disks', lambda *a, **k: [{'path': 'C:\\', 'free': 40 * GB}])
    assert ram_check.main(['--pagefile-auto']) == 0
    out = capsys.readouterr().out.strip()
    assert '\n' not in out
    script = base64.b64decode(out).decode('utf-16-le')
    assert 'AutomaticManagedPagefile' in script


def test_free_action_reports_what_it_stopped(monkeypatch, capsys):
    monkeypatch.setattr(ram_check, 'memory_status', lambda: status(commit_free_gb=1))
    monkeypatch.setattr(ram_check, 'ollama_loaded',
                        lambda *a, **k: [{'name': 'qwen3:8b', 'size_mb': 5734, 'processor': '', 'until': ''}])
    monkeypatch.setattr(ram_check, 'free_ollama', lambda *a, **k: ['qwen3:8b'])
    monkeypatch.setattr(ram_check, 'disks', lambda *a, **k: [])
    ram_check.main(['--free-ollama'])
    out = capsys.readouterr().out
    assert 'выгружено: qwen3:8b' in out


# --------------------------------------------------------------------------- #
#  the numbers the studio itself uses
# --------------------------------------------------------------------------- #
def test_views_need_is_sdxl_plus_the_adapter():
    """engine/plugins/mvadapter/nodes.py: ram = checkpoint size + adapter size."""
    assert ram_check.NEED_MB['views'] == ram_check.NEED_MB['sdxl'] + ram_check.NEED_MB['mvadapter']
    # file sizes from the studio, in MiB
    assert ram_check.NEED_MB['sdxl'] == 6657        # 6 979 975 392 B
    assert ram_check.NEED_MB['mvadapter'] == 3436   # 3 602 537 816 B (installer/manifest.json)


def test_reserve_matches_the_engine():
    """models.py: when ram_room() < ram + 3 GB the loaded models are dropped first."""
    assert ram_check.RESERVE_MB == 3072


def test_gib_formats_like_task_manager():
    assert ram_check.gib(ram_check.NEED_MB['views'] * MB) == '9.9 ГБ'
    assert ram_check.gib(GB) == '1.0 ГБ'


def test_explicit_pagefile_size_is_used_as_is():
    """200 GB free on the disk: 32 GB of page file is a choice, not a calculation."""
    drives = [{'path': 'D:\\', 'free': 200 * GB}]
    script = ram_check.pagefile_script('fixed', status(), 10500, drives, size_mb=32768)
    assert 'InitialSize = 32768' in script and 'MaximumSize = 32768' in script


def test_pagefile_fixed_takes_the_size_on_the_command_line(monkeypatch, capsys):
    monkeypatch.setattr(ram_check, 'memory_status', lambda: status())
    monkeypatch.setattr(ram_check, 'ollama_loaded', lambda *a, **k: [])
    monkeypatch.setattr(ram_check, 'disks', lambda *a, **k: [{'path': 'D:\\', 'free': 200 * GB}])
    assert ram_check.main(['--pagefile-fixed', '32768']) == 0
    script = base64.b64decode(capsys.readouterr().out.strip()).decode('utf-16-le')
    assert 'MaximumSize = 32768' in script


def test_32gb_machine_is_told_about_keep_ram():
    """engine.py keep_ram(): RAM >= 32 GB keeps the weights in RAM after a job."""
    text, _ = ram_check.report(status(total_gb=32, free_gb=10, pagefile_gb=8, commit_free_gb=6),
                               10500, 'Ракурсы', [], [])
    assert 'ASTRA_ENGINE_KEEP_RAM' in text


def test_16gb_machine_is_not_told_about_keep_ram():
    text, _ = ram_check.report(status(total_gb=16, free_gb=4), 10500, 'Ракурсы', [], [])
    assert 'ASTRA_ENGINE_KEEP_RAM' not in text
