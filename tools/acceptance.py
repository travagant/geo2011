#!/usr/bin/env python3
"""Приёмка прогона: гоняет защиты и печатает таблицу.

    python tools/acceptance.py

Коды выхода: 0 — всё зелёное, 1 — есть провалы.
Запускать БЕЗ пайпа, иначе код выхода потеряется.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: (имя, путь, что ждём, минимум для прохода).
#: Ветка перебазирована на origin/main (bc059d1), числа — от него.
CHECKS = [
    ("test_d3tool",   ["tests/test_d3tool.py"],        "200/200 и exit 0", 200),
    ("corpus_parity", ["tests/corpus_parity.py"],      "84/84",            84),
    ("reverse_parity",["tests/reverse_parity.py"],     "84/84",            84),
    ("addon_import",  ["tests/addon_import_test.py"],  "PASS",             1),
    ("test_corpus",   ["tests/test_corpus.py"],        "13/16 (задача 3)", 13),
]


def last_meaningful(lines: list[str]) -> str:
    for line in reversed(lines):
        s = line.strip()
        if not s:
            continue
        if s.startswith(("PASS ", "FAIL ", "ok ", "SKIP ")):
            continue
        return s[:80]
    return ""


def extract_count(lines: list[str]) -> int:
    """Число пройденных проверок из последней содержательной строки."""
    import re
    for line in reversed(lines):
        m = re.search(r"(\d+)\s*/\s*(\d+)\s*passed", line)
        if m:
            return int(m.group(1))
        m = re.search(r"\{'EXACT':\s*(\d+)", line)
        if m:
            return int(m.group(1))
        if "add-on imports and registers" in line:
            return 1
    return -1


def run(name: str, argv: list[str], expect: str) -> tuple[bool, str, float]:
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable] + argv, cwd=REPO,
                           capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return False, "таймаут 30 мин", time.time() - t0
    out = (p.stdout + p.stderr).splitlines()
    summary = last_meaningful(out)
    ok = p.returncode == 0
    return ok, f"{summary} | ожидалось: {expect}", time.time() - t0, extract_count(out)


def main() -> int:
    print(f"приёмка в {REPO}")
    print("база: origin/main = bc059d1 (ветка перебазирована)\n")
    failed = 0
    for name, argv, expect, need in CHECKS:
        path = os.path.join(REPO, argv[0])
        if not os.path.isfile(path):
            print(f"НЕТ ФАЙЛА  {name:16} {argv[0]}")
            failed += 1
            continue
        _, detail, dt, count = run(name, argv, expect)
        ok = count >= need
        if not ok:
            failed += 1
        got = f"{count}" if count >= 0 else "?"
        print(f"{'ok    ' if ok else 'ПРОВАЛ'} {name:16} {dt:6.1f}s  получено {got} >= {need}  ({expect})")
        if not ok and detail.split("|")[0].strip():
            print(f"        {detail}")

    print()
    print("ИТОГ: всё зелёное" if failed == 0 else f"ИТОГ: провалов {failed}")
    if failed:
        print("Если провалился test_d3tool с 'получено -1' — гард не добавлен,")
        print("файл печатает ноль строк. Это и есть задача 1.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
