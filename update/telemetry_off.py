"""Turn off the studio's reports and learning journal — with the switch the author built in.

AstraForge sends three things to keys.dops.media on every update check: error reports,
the «Кузница»/«Приёмка» learning journal (what was generated, parameters, verdicts, defect
tags) and, with those on, an installation number. PRIVACY.md says it is all optional and
names the switch: «О программе → Отчёты и обучение». The same switch is an API call —
`PUT /api/telemetry {errors, learning}` — and this script makes it without the mouse.

This is not a workaround: nothing here bypasses a licence check, it uses the documented
opt-out. What it does not touch: the subscription check, the update check, owner mode.
Those decide whether the studio updates, and removing them would break the licence, not
the telemetry.

Usage:
    python update/telemetry_off.py            # read the state, switch both off
    python update/telemetry_off.py --check    # only read, change nothing
    python update/telemetry_off.py --on       # switch both back on
    python update/telemetry_off.py --port 3211

Exit codes: 0 done (or already off), 2 the studio did not answer.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# The studio rejects any request that does not come from its own page.
HEADERS = {"x-astra-client": "studio", "Content-Type": "application/json"}
DEFAULT_PORT = 3210          # server/index.mjs: PORT || 3210


def endpoint(port=DEFAULT_PORT):
    return "http://127.0.0.1:%d/api/telemetry" % int(port)


def _call(method, port, payload=None, timeout=5, opener=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(endpoint(port), data=data, method=method, headers=HEADERS)
    opener = opener or urllib.request.urlopen
    with opener(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def state(port=DEFAULT_PORT, timeout=5, opener=None):
    """{'installId','errors','learning','pendingBytes',...} as the studio reports it."""
    return _call("GET", port, timeout=timeout, opener=opener)


def set_consent(port=DEFAULT_PORT, errors=False, learning=False, timeout=5, opener=None):
    """The documented switch: PUT /api/telemetry {errors, learning}. Returns the new state."""
    return _call("PUT", port, {"errors": bool(errors), "learning": bool(learning)},
                 timeout=timeout, opener=opener)


def describe(st):
    """One line a person can read: what is on and how much is waiting to be sent."""
    pending = st.get("pendingBytes") or 0
    return ("отчёты об ошибках: %s, журнал обучения: %s, ждёт отправки: %s"
            % ("вкл" if st.get("errors") else "выкл",
               "вкл" if st.get("learning") else "выкл",
               "%.1f КБ" % (pending / 1024.0) if pending else "ничего"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Отчёты и журнал обучения AstraForge: выключить")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="порт студии (по умолчанию 3210)")
    parser.add_argument("--check", action="store_true", help="только показать состояние")
    parser.add_argument("--on", action="store_true", help="включить обратно")
    args = parser.parse_args(argv)

    try:
        before = state(args.port)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("Студия не ответила на порту %d (%s)." % (args.port, exc))
        print("Запустите студию, или укажите порт: --port 3211")
        return 2

    print("сейчас :", describe(before))
    if args.check:
        return 0
    if not args.on and not before.get("errors") and not before.get("learning"):
        print("уже выключено — отправлять нечего.")
        return 0

    try:
        after = set_consent(args.port, errors=bool(args.on), learning=bool(args.on))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("Не удалось переключить (%s)." % exc)
        return 2

    print("стало  :", describe(after))
    if args.on:
        print("Отчёты снова включены.")
    else:
        print("Готово: студия больше не отправляет отчёты и журнал обучения.")
        print("Проверка обновлений остаётся — это не телеметрия, а обновления.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
