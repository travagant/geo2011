#!/usr/bin/env python3
"""Bring the studio link up: server + tunnel + the line to paste into the chat.

This is what ``Start studio link.cmd`` runs.  It deliberately does the whole job
in Python rather than in a PowerShell script, because Python is already required
(the bridge itself is Python) and every step here can be tested and, when it
fails, says which step failed instead of dying silently:

1. bind ``127.0.0.1:3211`` and run the link server **in this process** (a thread),
   so there is no second process to lose track of;
2. find ``cloudflared.exe`` (bundled under ``bridge/.link``, next to the studio,
   on PATH) or download it once;
3. start the tunnel, wait for its ``trycloudflare.com`` address;
4. **prove the address works** by asking it ``op=hello`` — a quick tunnel prints
   the address before it is routable, and on a network that drops QUIC it never
   becomes routable, which is why the first attempt looked fine and was dead;
5. if that fails, restart the tunnel over HTTP/2 (works where QUIC does not);
6. print ``link <url> <token>``, copy it to the clipboard, and wait for Enter.

Whatever happened is written to ``bridge/.link/last-attempt.txt`` so a failure
can be reported without reading logs by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
REPO_DIR = BRIDGE_DIR.parent
LINK_DIR = BRIDGE_DIR / ".link"
CLOUDFLARED_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/"
                   "download/cloudflared-windows-amd64.exe")
URL_RE = re.compile(rb"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
PORTS = (3211, 3212, 3213, 3220, 3230)

sys.path.insert(0, str(BRIDGE_DIR))
import astra_d3      # noqa: E402
import studio_link   # noqa: E402


class Abort(Exception):
    """A step failed in a way the operator has to see."""


# --------------------------------------------------------------------------- #
#  small pieces, each testable on its own
# --------------------------------------------------------------------------- #
def url_from_log(path: Path) -> str:
    """The newest ``https://….trycloudflare.com`` the tunnel logged, or ''."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return ""
    hits = URL_RE.findall(raw)
    return hits[-1].decode() if hits else ""


def free_port(candidates=PORTS):
    """The first candidate port nothing is listening on."""
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise Abort(f"заняты все порты из списка {list(candidates)} — закрой старое окно")


def find_cloudflared(studio: str) -> str:
    """Bundled copy first, then the studio, then PATH; '' when nothing is found."""
    here = LINK_DIR / "cloudflared.exe"
    if here.is_file() and here.stat().st_size > 1_000_000:
        return str(here)
    for candidate in (Path(studio) / "runtime" / "cloudflared" / "cloudflared.exe",
                      Path(studio) / "tools" / "cloudflared.exe"):
        if candidate.is_file():
            return str(candidate)
    found = shutil_which("cloudflared")
    return found or ""


def shutil_which(name):
    import shutil
    return shutil.which(name) or ""


def download_cloudflared(target: Path, say=print) -> str:
    """Fetch cloudflared once; report progress so a slow link is visible."""
    target.parent.mkdir(parents=True, exist_ok=True)
    say("скачиваю cloudflared (~35 МБ, один раз)…")
    request = urllib.request.Request(CLOUDFLARED_URL, headers={"User-Agent": "geo2011-bridge"})
    context = ssl.create_default_context()
    partial = target.with_suffix(".part")
    try:
        with urllib.request.urlopen(request, timeout=60, context=context) as resp, \
                open(partial, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            step = 0
            while True:
                chunk = resp.read(262_144)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if total and done // (5_000_000) > step:
                    step = done // 5_000_000
                    say(f"  {done / 1_048_576:.1f} / {total / 1_048_576:.1f} МиБ")
    except Exception as exc:  # noqa: BLE001 - turned into an instruction
        partial.unlink(missing_ok=True)
        raise Abort(
            f"не скачалось ({type(exc).__name__}: {exc}).\n"
            "  Скачай вручную:\n"
            f"    {CLOUDFLARED_URL}\n"
            f"  и положи сюда: {target}") from exc
    partial.replace(target)
    return str(target)


def start_tunnel(exe: str, port: int, log_path: Path, extra=()) -> tuple:
    """Run cloudflared and return ``(process, url)``; ``url`` may be ''."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    cmd = [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate",
           *extra]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=_no_window())
    lines = []

    def pump():
        with open(log_path, "a", encoding="utf-8", errors="replace") as fh:
            for raw in iter(proc.stdout.readline, b""):
                text = raw.decode("utf-8", "replace")
                fh.write(text)
                fh.flush()
                lines.append(text)

    threading.Thread(target=pump, daemon=True).start()
    url = ""
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        url = url_from_log(log_path)
        if url:
            break
        time.sleep(0.5)
    return proc, url


def _no_window():
    """Do not flash a console window for the tunnel on Windows."""
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def probe(address: str, token: str, tries=10, gap=3.0, timeout=15) -> str:
    """``ok`` if the public address answers our JSON, else the last reason."""
    last = "no attempt"
    for index in range(tries):
        url = f"{address}/cmd?token={token}&op=hello&_=probe{index}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read(4096).decode("utf-8", "replace")
                if '"ok"' in body and "true" in body:
                    return "ok"
                return f"answered {resp.status} without our JSON: {body[:120]}"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - retried
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(gap)
    return f"not reachable ({last})"


def probe_local(port: int, token: str) -> str:
    return probe(f"http://127.0.0.1:{port}", token, tries=10, gap=0.3, timeout=5)


def copy_to_clipboard(text: str) -> bool:
    """Best effort: clip.exe on Windows, pbcopy/xclip elsewhere."""
    commands = [["clip"]] if os.name == "nt" else [["pbcopy"], ["xclip", "-selection", "clipboard"]]
    import shutil
    for cmd in commands:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(cmd, input=text.encode("utf-16-le" if os.name == "nt" else "utf-8"),
                           check=True, timeout=10)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


# --------------------------------------------------------------------------- #
#  the run
# --------------------------------------------------------------------------- #
def run(args) -> int:
    LINK_DIR.mkdir(parents=True, exist_ok=True)
    attempt = LINK_DIR / "last-attempt.txt"
    report = [f"when:    {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    tunnel_proc = None
    httpd = None

    def finish(code, verdict):
        report.append(f"verdict: {verdict}")
        try:
            attempt.write_text("\n".join(report) + "\n", encoding="utf-8")
        except OSError:
            pass
        if verdict != "ok":
            print(f"\nотчёт: {attempt}")
        return code

    print("=== AstraForge <-> Arena: studio link ===")
    print(f"python  : {sys.executable}")
    print(f"папка   : {REPO_DIR}")

    cfg = astra_d3.Config.load(args.config)
    if args.studio:
        cfg["studio"] = args.studio
        cfg.resolve()
    report.append(f"studio:  {cfg['studio']}  (exists: {Path(cfg['studio']).is_dir()})")

    port = free_port(args.ports)
    link = studio_link.Link(cfg, port, args.studio_url)
    studio_link.Handler.link = link
    httpd = ThreadingHTTPServer(("127.0.0.1", port), studio_link.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"сервер  : http://127.0.0.1:{port}  (только этот ПК)")
    report.append(f"port:    {port}")

    local = probe_local(port, link.token)
    report.append(f"local:   {local}")
    if local != "ok":
        print(f"сервер не отвечает сам себе: {local}")
        return finish(1, "local-fail")
    print("сервер отвечает локально: OK")

    try:
        exe = args.cloudflared or find_cloudflared(cfg["studio"])
        if not exe:
            exe = download_cloudflared(LINK_DIR / "cloudflared.exe")
        print(f"туннель : {exe}")
        report.append(f"cloudflared: {exe}")

        print("поднимаю туннель…")
        log_path = LINK_DIR / "tunnel.log"
        tunnel_proc, url = start_tunnel(exe, port, log_path)
        report.append(f"tunnel1: {url or '(no address)'}  exit={tunnel_proc.poll()}")
        verdict = "no-address"
        if url:
            print(f"адрес: {url} — проверяю, доходит ли до него запрос")
            verdict = probe(url, link.token, tries=args.probe_tries)
        if verdict != "ok":
            print(f"не прошло ({verdict}).")
            print("Пробую по HTTP/2: во многих сетях режут QUIC, а он у cloudflared по умолчанию.")
            try:
                tunnel_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            tunnel_proc, url = start_tunnel(exe, port, log_path,
                                            extra=["--protocol", "http2"])
            report.append(f"tunnel2: {url or '(no address)'}  exit={tunnel_proc.poll()}")
            verdict = probe(url, link.token, tries=args.probe_tries) if url \
                else "no-address"
        if tunnel_proc.poll() is not None:
            verdict = f"cloudflared exited ({tunnel_proc.returncode})"

        if verdict != "ok":
            print("\nКанал не поднялся. Последние строки лога туннеля:")
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
            except OSError:
                tail = []
            for line in tail:
                print("  " + line)
            report.extend(["--- tunnel.log (tail) ---"] + tail)
            print("\nЧтобы я разобралась — пришли этот файл:")
            print(f"  {attempt}")
            if url:
                print(f"\nМожно всё же попробовать адрес: link {url} {link.token}")
            return finish(1, verdict)

        line = f"link {url} {link.token}"
        copied = copy_to_clipboard(line)
        print("\n" + "=" * 62)
        print(f" Адрес : {url}")
        print(f" Токен : {link.token}")
        print("")
        print(" Канал проверен: адрес отвечает снаружи." + ("  Строка в буфере." if copied else ""))
        print(" Вставь в чат Арены одной строкой:")
        print("   " + line)
        print("")
        print(" Окно не закрывай крестиком: пока оно открыто — канал жив.")
        print(" Enter здесь = закрыть канал.")
        print("=" * 62)
        report.append(f"url:     {url}")
        finish(0, "ok")
        try:
            input("\nEnter — остановить туннель и сервер ")
        except (EOFError, KeyboardInterrupt):
            pass
        return 0
    except Abort as exc:
        print(f"\n{exc}")
        report.append(f"abort: {exc}")
        return finish(1, "abort")
    except Exception as exc:  # noqa: BLE001 - never die silently
        print(f"\nнепредвиденная ошибка: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        report.append(traceback.format_exc())
        return finish(1, "crash")
    finally:
        if tunnel_proc is not None:
            try:
                tunnel_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start the AstraForge studio link.")
    parser.add_argument("-c", "--config", default="")
    parser.add_argument("--studio", default="")
    parser.add_argument("--studio-url", default="http://127.0.0.1:3210")
    parser.add_argument("--ports", default=",".join(str(p) for p in PORTS),
                        help="ports to try, comma separated")
    parser.add_argument("--cloudflared", default="",
                        help="use this cloudflared.exe instead of looking for one")
    parser.add_argument("--probe-tries", type=int, default=10,
                        help="how many times to ask the public address (default 10)")
    args = parser.parse_args(argv)
    args.ports = [int(p) for p in args.ports.split(",") if p.strip()]
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nостановлено")
        return 0


if __name__ == "__main__":
    sys.exit(main())
