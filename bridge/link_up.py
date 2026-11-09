#!/usr/bin/env python3
"""Bring the studio link up: server + tunnel + the line to paste into the chat.

This is what ``Start studio link.cmd`` runs.  Everything is Python, because
Python is already required (the bridge is Python) and every step can be tested
and, when it fails, says which step failed instead of dying silently.

1. bind ``127.0.0.1:3211`` and run the link server **in this process** (a
   thread), so there is no second process to lose track of;
2. find ``cloudflared.exe`` (bundled under ``bridge/.link``, next to the studio,
   on PATH) or download it once;
3. try transports one after another — cloudflared over QUIC, cloudflared over
   HTTP/2, then plain ``ssh -R`` through localhost.run — because which one works
   depends on the network, not on us;
4. for every address, **ask it ``op=hello``** and believe only that: a quick
   tunnel prints its address before it is routable, and Cloudflare answers 530
   while the connector is not attached;
5. print ``link <url> <token>`` for the first address that answered, and keep it
   alive until Enter.  If none answered, keep the last one alive anyway and say
   so: a 530 from this machine can still answer from the other side, and that is
   worth one manual test rather than another blind retry.

Whatever happened is written to ``bridge/.link/last-attempt.txt`` (with the raw
answer of every probe, not just "not reachable"), and each transport keeps its
own log: ``tunnel-quic.log``, ``tunnel-http2.log``, ``tunnel-ssh.log``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
REPO_DIR = BRIDGE_DIR.parent
LINK_DIR = BRIDGE_DIR / ".link"
CLOUDFLARED_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/"
                   "download/cloudflared-windows-amd64.exe")
# quick tunnels from Cloudflare, and the ssh-based ones from localhost.run
URL_RE = re.compile(
    rb"https?://[a-z0-9][a-z0-9.-]*\.(?:trycloudflare\.com|lhr\.life|localhost\.run|"
    rb"lhr\.rocks|eu\.lhr\.life|pinggy\.link|pinggy\.io|serveo\.net|"
    rb"loca\.lt)")
# ssh is not on PATH on every Windows box, but it is installed
SSH_CANDIDATES = (r"C:\Windows\System32\OpenSSH\ssh.exe",)
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
    """The newest public address a tunnel client logged, or ''."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return ""
    hits = URL_RE.findall(raw)          # the newest address wins: tunnels retry
    return hits[-1].decode() if hits else ""


def port_busy(port: int, host="127.0.0.1", timeout=0.4) -> bool:
    """Is anything already answering on this port?

    Asked by *connecting*, not by binding: on Windows a bind with SO_REUSEADDR
    succeeds even when another process is listening (``HTTPServer`` sets that
    option by default), which is how a second copy of the bridge ends up talking
    to the first one and reporting ``403 bad token``.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def free_port(candidates=PORTS):
    """The first candidate port nothing is listening on."""
    for port in candidates:
        if not port_busy(port):
            return port
    raise Abort(f"заняты все порты из списка {list(candidates)} — закрой старое окно")


def bind_link_server(candidates, token, say=print):
    """``(httpd, port)`` — a port where *this* process answers, not a leftover one.

    A bind that succeeds proves nothing (see ``port_busy``), so every candidate is
    verified by asking it ``op=hello`` and checking the answer is ours. A stale
    instance answers 403 there, and the next port is tried instead of giving up.
    """
    tried = []
    for port in candidates:
        if port_busy(port):
            tried.append(f"{port}: занят (кто-то слушает)")
            say(f"    порт {port} занят — беру следующий")
            continue
        try:
            httpd = studio_link.StudioLinkServer(("127.0.0.1", port),
                                                 studio_link.Handler)
        except OSError as exc:
            tried.append(f"{port}: не открылся ({exc})")
            say(f"    порт {port} не открылся ({type(exc).__name__}) — беру следующий")
            continue
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        answer = probe_local(port, token)
        if answer == "ok":
            return httpd, port, tried
        httpd.shutdown()
        httpd.server_close()
        tried.append(f"{port}: ответил не наш сервер ({answer})")
        say(f"    на порту {port} ответил кто-то другой ({answer}) — беру следующий")
    raise Abort("ни один порт не подошёл: " + "; ".join(tried) +
                "\n  закрой старые окна моста (в них прошлые запуски) и попробуй снова")


def find_cloudflared(studio: str) -> str:
    """Bundled copy first, then the studio, then PATH; '' when nothing is found."""
    here = LINK_DIR / "cloudflared.exe"
    if here.is_file() and here.stat().st_size > 1_000_000:
        return str(here)
    for candidate in (Path(studio) / "runtime" / "cloudflared" / "cloudflared.exe",
                      Path(studio) / "tools" / "cloudflared.exe"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("cloudflared") or ""


def download_cloudflared(target: Path, say=print) -> str:
    """Fetch cloudflared once; report progress so a slow link is visible."""
    target.parent.mkdir(parents=True, exist_ok=True)
    say("скачиваю cloudflared (~35 МБ, один раз)…")
    request = urllib.request.Request(CLOUDFLARED_URL,
                                     headers={"User-Agent": "geo2011-bridge"})
    context = ssl.create_default_context()
    partial = target.with_suffix(".part")
    try:
        with urllib.request.urlopen(request, timeout=60, context=context) as resp, \
                open(partial, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = step = 0
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


def _no_window():
    """Do not flash a console window for the tunnel on Windows."""
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _spawn(cmd: list, log_path: Path) -> subprocess.Popen:
    """Start a client, tee its output to a log file, return the process."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=_no_window())

    def pump():
        with open(log_path, "a", encoding="utf-8", errors="replace") as fh:
            for raw in iter(proc.stdout.readline, b""):
                fh.write(raw.decode("utf-8", "replace"))
                fh.flush()

    threading.Thread(target=pump, daemon=True).start()
    return proc


def _wait_for_url(proc, log_path: Path, seconds=45) -> str:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        url = url_from_log(log_path)
        if url:
            return url
        time.sleep(0.5)
    return url_from_log(log_path)


def start_cloudflared(exe: str, port: int, protocol: str = "") -> tuple:
    """``(process, url, log)`` for one cloudflared attempt."""
    log_path = LINK_DIR / (f"tunnel-{protocol or 'auto'}.log")
    # The address must come from THIS run: a log left by a previous attempt holds
    # an address whose tunnel is long dead, and probing it answers 1033/530 no
    # matter what we do next — which reads as "the tunnel does not work".
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    cmd = [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate",
           "--logfile", str(log_path), "--loglevel", "info"]
    if protocol:
        cmd += ["--protocol", protocol]
    # cloudflared writes its own log file when --logfile is given; stdout then
    # stays quiet, so the address is read from that file
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=_no_window())
    threading.Thread(target=lambda: [None for _ in iter(proc.stdout.readline, b"")],
                     daemon=True).start()
    url = _wait_for_url(proc, log_path)
    return proc, url, log_path


def find_ssh() -> str:
    """``ssh.exe`` from PATH, or the copy Windows ships but does not always add."""
    found = shutil.which("ssh")
    if found:
        return found
    for candidate in SSH_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return ""


def start_ssh(port: int, host: str = "nokey@localhost.run", ssh: str = "",
              extra=(), log_name: str = "tunnel-ssh.log", remote="80") -> tuple:
    """``(process, url, log)`` for an ssh tunnel: needs no download at all.

    ``remote`` is the remote port to ask for: ``80`` for localhost.run, ``0`` for
    pinggy (it assigns one and hands back an https address).
    """
    log_path = LINK_DIR / log_name
    cmd = [ssh or "ssh", "-o", "StrictHostKeyChecking=no",
           "-o", f"UserKnownHostsFile={os.devnull}",
           "-o", "ServerAliveInterval=20", "-o", "ExitOnForwardFailure=yes",
           *extra, "-R", f"{remote}:127.0.0.1:{port}", host]
    proc = _spawn(cmd, log_path)
    url = _wait_for_url(proc, log_path, seconds=45)
    return proc, url, log_path


def _json_body(raw: bytes, status: int = 200) -> tuple:
    """``(is_ours, ok, error)`` for an answer from our own ``/cmd``.

    ``403`` is never ours: the token was refused, so the server on that port is
    another copy of the bridge (a leftover window), not this one.

    Parsed, not grepped: Cloudflare's error pages are HTML, and ``"ok"`` inside a
    530 page must never be mistaken for our answer.
    """
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return False, False, ""
    if not isinstance(data, dict):
        return False, False, ""
    if status == 403:
        return False, False, str(data.get("error") or "bad token")
    if "error" in data and "ok" not in data:
        return False, False, str(data["error"])
    return True, data.get("ok") is True, str(data.get("error") or "")


def probe_detail(address: str, token: str, tries=20, gap=3.0, timeout=15,
                 op="ping") -> dict:
    """Ask ``address`` for one cheap op; keep what it answered, verbatim.

    ``op=ping`` on purpose: it touches no config and no studio, so a 400 from a
    half-configured bridge cannot be mistaken for "the tunnel is dead" — which
    used to throw away a working tunnel.

    A bare "not reachable" is useless when the answer is Cloudflare's 530 page, a
    1033 "tunnel not connected", or our own "bad token": they mean three
    different things. So the body is kept, and ``ours`` says whether the token
    was accepted.
    """
    result = {"ok": False, "ours": False, "url": address, "op": op,
              "detail": "no attempt", "status": 0, "tries": 0, "body": ""}
    for index in range(tries):
        result["tries"] = index + 1
        url = f"{address}/cmd?token={token}&op={op}&_=probe{index}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                raw = resp.read(8192)
                ours, ok, error = _json_body(raw, resp.status)
                result.update(status=resp.status, ours=ours,
                              body=raw.decode("utf-8", "replace")[:800])
                if ok:
                    result.update(ok=True, detail="ok")
                    return result
                if ours:
                    # the token matched: this is our server, the op itself failed
                    result["detail"] = f"наш сервер, {op} ответил ошибкой: {error[:120]}"
                    break
                result["detail"] = f"ответил {resp.status}, но это не наш JSON"
                break
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read(8192)
            except Exception:  # noqa: BLE001
                pass
            ours, ok, error = _json_body(raw, exc.code)
            result.update(status=exc.code, ours=ours,
                          body=raw.decode("utf-8", "replace")[:800],
                          detail=f"HTTP {exc.code}")
            if ours and exc.code == 400:
                result["detail"] = f"наш сервер, {op} ответил ошибкой: {error[:120]}"
                break
            if exc.code in (502, 503, 504, 530, 1033):
                time.sleep(gap)          # tunnel still attaching: keep asking
                continue
            break
        except Exception as exc:  # noqa: BLE001 - retried
            result["detail"] = f"{type(exc).__name__}: {exc}"
            time.sleep(gap)
    return result


def probe(address: str, token: str, tries=20, gap=3.0, timeout=15, op="ping") -> str:
    """``ok`` or a short reason — the older, string-shaped interface."""
    outcome = probe_detail(address, token, tries=tries, gap=gap, timeout=timeout, op=op)
    return "ok" if outcome["ok"] else f"not reachable ({outcome['detail']})"


def probe_local(port: int, token: str) -> str:
    return probe(f"http://127.0.0.1:{port}", token, tries=10, gap=0.3, timeout=5)


def copy_to_clipboard(text: str) -> bool:
    """Best effort: clip.exe on Windows, pbcopy/xclip elsewhere."""
    commands = [["clip"]] if os.name == "nt" else [["pbcopy"],
                                                   ["xclip", "-selection", "clipboard"]]
    for cmd in commands:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(cmd, input=text.encode(
                "utf-16-le" if os.name == "nt" else "utf-8"), check=True, timeout=10)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


# --------------------------------------------------------------------------- #
#  the run
# --------------------------------------------------------------------------- #
def transports(exe: str, port: int) -> list:
    """Every way we can get a public address, in the order worth trying."""
    out = []
    if exe:
        out.append(("cloudflared QUIC", lambda: start_cloudflared(exe, port, "quic")))
        out.append(("cloudflared HTTP/2",
                    lambda: start_cloudflared(exe, port, "http2")))
    ssh = find_ssh()
    if ssh:
        # pinggy answers on port 443, which survives networks that drop 22;
        # localhost.run is the plain fallback (port 22)
        out.append(("ssh → pinggy (443)",
                    lambda: start_ssh(port, host="a.pinggy.io", ssh=ssh,
                                      extra=("-p", "443"), remote="0",
                                      log_name="tunnel-pinggy.log")))
        out.append(("ssh → localhost.run", lambda: start_ssh(port, ssh=ssh)))
    return out


def run(args) -> int:
    LINK_DIR.mkdir(parents=True, exist_ok=True)
    attempt = LINK_DIR / "last-attempt.txt"
    report = []
    live = None          # the tunnel we are keeping alive
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

    build = studio_link.build_marker()
    print("=== AstraForge <-> Arena: studio link ===")
    print(f"сборка  : {build}")
    print(f"python  : {sys.executable}")
    print(f"папка   : {REPO_DIR}")
    report.append(f"when:    {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"build:   {build}")
    report.append(f"bridge:  {REPO_DIR}")

    cfg = astra_d3.Config.load(args.config)
    if args.studio:
        cfg["studio"] = args.studio
        cfg.resolve()
    print(f"студия  : {cfg['studio']}")
    report.append(f"studio:  {cfg['studio']}  (exists: {Path(cfg['studio']).is_dir()})")
    # the most common way a fix "does not arrive": an older copy, unzipped inside
    # the studio folder, is what actually gets double-clicked
    studio_path = Path(cfg["studio"]).resolve()
    if studio_path in REPO_DIR.resolve().parents:
        print("\n[!] ЭТА КОПИЯ ЛЕЖИТ ВНУТРИ ПАПКИ СТУДИИ: " + str(REPO_DIR))
        print("    Это, скорее всего, старая распаковка. Свежий архив надо")
        print("    распаковать отдельно (например D:\\geo2011) и запускать оттуда.")
        print(f"    Здесь сборка {build} — сравни номер в окне с тем, что обещал чат.")
        report.append(f"warning: bridge inside studio: {REPO_DIR}")

    link = studio_link.Link(cfg, args.ports[0], args.studio_url)
    studio_link.Handler.link = link
    httpd, port, tried = bind_link_server(args.ports, link.token)
    link.port = port
    print(f"сервер  : http://127.0.0.1:{port}  (только этот ПК)")
    report.append(f"port:    {port}")
    for line in tried:
        report.append(f"port try: {line}")

    local = probe_local(port, link.token)
    report.append(f"local:   {local}")
    if local != "ok":
        print(f"сервер не отвечает сам себе: {local}")
        return finish(1, "local-fail")
    print("сервер отвечает локально: OK")

    try:
        exe = args.cloudflared or find_cloudflared(cfg["studio"])
        if not exe and not args.no_download:
            try:
                exe = download_cloudflared(LINK_DIR / "cloudflared.exe")
            except Abort as exc:
                print(f"{exc}\nпробую без него (ssh)…")
                exe = ""
        report.append(f"cloudflared: {exe or '(none)'}")

        choices = transports(exe, port)
        if not choices:
            raise Abort("нечем поднимать туннель: нет ни cloudflared, ни ssh")

        winner = None
        last = len(choices) - 1
        for index, (name, starter) in enumerate(choices):
            print(f"\n--- {name}")
            proc, url, log_path = None, "", LINK_DIR / "tunnel.log"
            try:
                proc, url, log_path = starter()
            except Exception as exc:  # noqa: BLE001 - try the next transport
                print(f"    не запустилось: {type(exc).__name__}: {exc}")
                report.append(f"{name}: launch failed ({exc})")
                continue
            report.append(f"{name}: url={url or '(none)'} "
                          f"exit={proc.poll()} log={log_path}")
            if not url:
                print("    адреса нет — смотрю лог")
                tail = _tail(log_path)
                for line in tail[-8:]:
                    print("      " + line)
                report.extend([f"--- {log_path.name} (tail) ---"] + tail)
                _kill(proc)
                continue
            print(f"    адрес: {url}")
            outcome = probe_detail(url, link.token, tries=args.probe_tries)
            print(f"    проверка снаружи: {outcome['tries']} попыток, "
                  f"ответ {outcome['detail']}")
            report.append(f"{name}: probe status={outcome['status']} "
                          f"detail={outcome['detail']} tries={outcome['tries']}")
            if outcome["body"]:
                report.append(f"    body: {outcome['body'][:400]}")
            if outcome["ok"]:
                winner = (name, proc, url, log_path)
                # the tunnel is up; now say whether the studio itself answers
                through = probe_detail(url, link.token, tries=2, gap=1.0,
                                       timeout=20, op="hello")
                report.append(f"hello through the tunnel: status={through['status']} "
                              f"ok={through['ok']} detail={through['detail']}")
                print("    туннель проверен; студия через него: "
                      + ("отвечает" if through["ok"] else through["detail"]))
                break
            if index == last:
                # the last transport is kept running: a 530 seen from this
                # machine can still answer from the other side, and the session
                # can only test that if the tunnel is still up
                print("    не прошло, но канал оставляю включённым для проверки снаружи")
                live = (name, proc, url, log_path)
            else:
                print("    не прошло — пробую следующий способ")
                _kill(proc)

        if winner:
            name, proc, url, log_path = winner
            live = winner
            return _announce(url, link.token, name, report, finish, httpd)
        return _announce(live[2] if live else "", link.token,
                         live[0] if live else "", report, finish, httpd,
                         unverified=True)
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
        if live is not None:
            _kill(live[1])
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass


def _announce(url, token, transport, report, finish, httpd, unverified=False):
    """Print the line, keep the tunnel alive until Enter."""
    if not url:
        print("\nКанал не поднялся ни одним из способов. Логи выше и в отчёте.")
        print("Если нужен другой путь (например, свой VPN) — скажи, придумаем.")
        return finish(1, "no-address")
    line = f"link {url} {token}"
    build = studio_link.build_marker()
    # One small file to drag into the chat instead of hunting for the window and
    # copying two values by hand — that hand-off is the step that keeps failing.
    send_me = LINK_DIR / "SEND-ME.txt"
    try:
        send_me.write_text(
            line + "\n"
            + f"build: {build}\n"
            + f"url: {url}\n"
            + f"token: {token}\n"
            + f"folder: {REPO_DIR}\n"
            + f"verdict: {'unverified' if unverified else 'ok'}\n",
            encoding="utf-8")
    except OSError:
        send_me = None
    copied = copy_to_clipboard(line)
    print("\n" + "=" * 64)
    print(f" Адрес : {url}")
    print(f" Токен : {token}")
    print("")
    if unverified:
        print(" ВНИМАНИЕ: проверка с этого компьютера не прошла,")
        print(" но канал ОСТАВЛЕН ВКЛЮЧЁННЫМ — иногда он отвечает снаружи.")
        print(" Пришли строку ниже: я проверю со своей стороны и скажу точно.")
    else:
        print(f" Канал проверен ({transport}): адрес отвечает снаружи."
              + ("  Строка в буфере." if copied else ""))
    print(f" Сборка: {build}  (папка {REPO_DIR})")
    print(" Строка для чата:")
    print("   " + line)
    if send_me is not None:
        print("")
        print(" Проще: перетащи в чат один файл")
        print(f"   {send_me}")
        print("   (там эта же строка; можно и просто скопировать её выше)")
    print("")
    print(" Окно не закрывай: пока оно открыто — канал жив. Enter = закрыть.")
    print(" Если в чате скажут «канал не отвечает» — проверь номер сборки выше:") 
    print(" старая распаковка моста запускается так же, но лечит старые ошибки.")
    print("=" * 64)
    report.append(f"url:     {url}")
    finish(0 if not unverified else 1, "ok" if not unverified else "unverified")
    try:
        input("\nEnter — остановить туннель и сервер ")
    except (EOFError, KeyboardInterrupt):
        pass
    return 0


def _tail(path: Path, count=25) -> list:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
    except OSError:
        return []


def _kill(proc):
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
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
    parser.add_argument("--probe-tries", type=int, default=20,
                        help="how many times to ask the public address (default 20)")
    parser.add_argument("--no-download", action="store_true",
                        help="never download cloudflared (ssh transport only)")
    args = parser.parse_args(argv)
    args.ports = [int(p) for p in args.ports.split(",") if p.strip()]
    try:
        code = run(args)
    except KeyboardInterrupt:
        print("\nостановлено")
        return 0
    # Double-clicking this .py (when .cmd is blocked by policy) has no console
    # wrapper to hold the window; Start studio link.cmd sets D3_LINK_NO_PAUSE.
    if code != 0 and os.name == "nt" and not os.environ.get("D3_LINK_NO_PAUSE"):
        try:
            input("\nEnter — закрыть ")
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
