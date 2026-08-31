"""Small, dependency-free UI helpers for the d3tool command-line interface.

Adds colour, a banner, status tokens (OK / SKIP / WROTE / FAIL) and a couple of
formatting helpers.  Colours are disabled automatically when the output is not a
terminal (e.g. piped to a file) or when ``NO_COLOR`` is set.
"""
from __future__ import annotations

import os
import sys
from typing import Iterable, Optional

_USE_COLOR = (
    hasattr(sys.stdout, "isatty")
    and sys.stdout.isatty()
    and os.environ.get("NO_COLOR") is None
    and os.environ.get("TERM") != "dumb"
)

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
# 256/ANSI colours
_CYAN = "\033[38;5;14m"
_GREEN = "\033[38;5;34m"
_YELLOW = "\033[38;5;214m"
_RED = "\033[38;5;196m"
_MAGENTA = "\033[38;5;5m"
_WHITE = "\033[38;5;15m"
_GREY = "\033[38;5;245m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _USE_COLOR else text


def cyan(t: str) -> str:
    return _c(_CYAN, t)


def green(t: str) -> str:
    return _c(_GREEN, t)


def yellow(t: str) -> str:
    return _c(_YELLOW, t)


def red(t: str) -> str:
    return _c(_RED, t)


def magenta(t: str) -> str:
    return _c(_MAGENTA, t)


def bold(t: str) -> str:
    return _c(_BOLD, t)


def grey(t: str) -> str:
    return _c(_GREY, t)


def section(title: str) -> None:
    """Print a section heading."""
    print("")
    print(bold(f"── {title} ") + grey("─" * max(0, 60 - len(title))))


def ok(msg: str = "ok") -> None:
    print(f"  {green('✔')} {msg}")


def fail(msg: str) -> None:
    print(f"  {red('✘')} {msg}")


def warn(msg: str) -> None:
    print(f"  {yellow('!')} {msg}")


def info(msg: str) -> None:
    print(f"  {cyan('·')} {msg}")


def wrote(path: str, extra: str = "") -> None:
    print(f"  {green('WROTE')} {path}" + (f"  {grey(extra)}" if extra else ""))


def skipped(path: str, why: str = "") -> None:
    print(f"  {yellow('SKIP')}  {path}" + (f"  {grey(why)}" if why else ""))


def table(rows: Iterable[tuple], headers: Optional[Iterable[str]] = None) -> None:
    """Print a simple aligned table (no external deps)."""
    rows = list(rows)
    headers = list(headers) if headers else None
    widths: list = []
    data = ([(headers)] if headers else []) + rows
    data = [[str(c) for c in r] for r in data]
    for r in data:
        for i, c in enumerate(r):
            if i >= len(widths):
                widths.append(len(c))
            else:
                widths[i] = max(widths[i], len(c))
    if headers:
        hdr = headers
        print("  " + "  ".join(
            bold(h.ljust(widths[i])) for i, h in enumerate(hdr)))
        print("  " + "  ".join(
            grey("─" * widths[i]) for i in range(len(widths))))
    for row in rows:
        cells = [str(c) for c in row]
        print("  " + "  ".join(
            c.ljust(widths[i]) for i, c in enumerate(cells)))


def banner() -> None:
    """Print the program banner."""
    print("")
    print(bold("d3tool ") + grey("· dis3tool / geo2011 reverse-engineering toolkit"))
    print(grey("glTF ↔ Disciples 3 (.g / .a / .scene / .ac)"))
    print("")


def confirm(question: str, default: bool = True) -> bool:
    """Ask a yes/no question (only when interactive).

    Interactivity is a *TTY stdin*, not colour support: under ``NO_COLOR``
    the prompt must still appear (uncoloured), because silently taking the
    destructive default is exactly what a colour-blind terminal must not
    cause.  Piped / non-interactive stdin keeps taking the default without
    reading, so batch scripts never hang on a prompt.
    """
    interactive = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    if not interactive:
        return default
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        sys.stdout.write(cyan("? " + question) + suffix + " ")
        sys.stdout.flush()
        ans = sys.stdin.readline().strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("  please answer yes/no")
