"""A channel that survives a network which kills every tunnel.

Measured on the machine that could not connect: cloudflared *registers* a QUIC
tunnel and loses it nine seconds later (`failed to accept QUIC stream: timeout:
no recent network activity`), the same over HTTP/2 (`control stream: client
disconnected`), and both ssh providers answer `Connection reset` right after
their banner. Every one of those is the same shape: a long-lived connection that
the network drops. The one thing that kept working for twenty minutes was a
plain HTTPS request to a public notify relay.

So the link stops being a tunnel and becomes a post box:

    the session  -> publishes a command  ->  ntfy topic  <base>-cmd
    this program -> polls that topic, runs the op on 127.0.0.1, publishes
                    the answer to  <base>-out
    the session  -> polls  <base>-out  and reads it

Nothing listens on the user's PC, nothing is inbound, no provider needs to
tolerate a long connection: every exchange is one short request, the same
direction and the same size as a browser page. Ops are unchanged — the same
`op=ping`, `op=tail`, `op=api`, `op=cuda` strings the tunnel carried.

Payloads are capped by the relay (~4 KB per message), so answers are cut at
`LIMIT` characters with an honest marker; big transfers were never going to fit
through here and the ops that matter (`build`, `pack`, `d3tool`) run locally
anyway and return numbers.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote

BRIDGE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BRIDGE_DIR))

import astra_d3        # noqa: E402
import link_up         # noqa: E402
import studio_link     # noqa: E402

RELAY_FILE = studio_link.LINK_DIR / "ntfy.txt"
RELAY_BASE = os.environ.get("NTFY_BASE", "https://ntfy.sh")
LIMIT = 3400            # bytes per message the relay accepts, minus the header
POLL_EVERY = 2.0
CLEAN_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


# --------------------------------------------------------------------------- #
#  the message shape, small enough to be obviously correct
# --------------------------------------------------------------------------- #
def read_config(path=None) -> dict:
    """``topic`` / ``key`` / ``port`` from ``ntfy.txt`` written into the bundle."""
    out = {}
    try:
        text = Path(path or RELAY_FILE).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, _, value = line.partition(":")
        out[name.strip().lower()] = value.strip()
    return out


def parse_command(text: str):
    """``(nonce, query)`` from ``n=12 op=tail path=comfy.log`` or ``None``.

    Space-separated on purpose: a relay message travels in a query string, where
    an unescaped ``&`` would silently cut the command in half.
    """
    parts = (text or "").split()
    if not parts or not parts[0].startswith("n="):
        return None
    nonce = parts[0][2:]
    if not nonce.isdigit():
        return None
    pairs = []
    for token in parts[1:]:
        if "=" not in token:
            continue
        name, _, value = token.partition("=")
        pairs.append(f"{name}={unquote(value)}")
    if not any(pair.startswith("op=") for pair in pairs):
        return None
    return int(nonce), "&".join(pairs)


def format_result(nonce: int, status: int, body: str) -> tuple:
    """``(header, rest)`` — the header always fits, the body is cut honestly."""
    body = body or ""
    total = len(body)
    head = f"n={nonce} s={status} len={total}"
    if total > LIMIT:
        return head, body[:LIMIT] + f"\n…[обрезано: всего {total} символов]"
    return head, body


def parse_result(text: str) -> dict:
    """``{"n", "status", "body"}`` from what this program published."""
    first, _, body = (text or "").partition("\n")
    fields = dict(part.split("=", 1) for part in first.split() if "=" in part)
    if "n" not in fields:
        return {}
    try:
        return {"n": int(fields["n"]), "status": int(fields.get("s", 0)),
                "body": body, "len": int(fields.get("len", len(body)))}
    except ValueError:
        return {}


# --------------------------------------------------------------------------- #
#  the two ends of the post box
# --------------------------------------------------------------------------- #
def publish(topic: str, text: str, base: str = RELAY_BASE,
            timeout: float = 15.0) -> tuple:
    payload = text.encode("utf-8")
    request = urllib.request.Request(f"{base}/{topic}", data=payload,
                                     method="POST",
                                     headers={"Title": topic, "Tags": "bridge"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except Exception as exc:      # noqa: BLE001 - the window says so
        return False, f"{type(exc).__name__}: {exc}"


def fetch(topic: str, since: int = 0, base: str = RELAY_BASE,
          timeout: float = 20.0) -> list:
    """Every message on ``topic`` newer than ``since`` (unix seconds)."""
    url = f"{base}/{topic}/json?poll=1&since={int(since)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception:             # noqa: BLE001 - empty poll is not an error
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("event") == "message":
            out.append(data)
    return out


def link_up_replay(local: str, token: str, query: str, timeout: float = 180.0):
    url = f"{local}/cmd?token={token}&{query}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception:         # noqa: BLE001
            return exc.code, ""
    except Exception as exc:      # noqa: BLE001 - answer anyway, silence is worse
        return 599, json.dumps({"ok": False,
                                "error": f"{type(exc).__name__}: {exc}"})


def start_local(cfg: dict, port: int):
    """``(httpd, port, token)`` — the same server the tunnel used to publish."""
    link = studio_link.Link(cfg, port, "http://127.0.0.1:3210")
    studio_link.Handler.link = link
    httpd, port, _ = link_up.bind_link_server([port, port + 1, port + 2, port + 3],
                                              link.token)
    link.port = port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port, link.token


def serve(base_topic: str, key: str, local: str, token: str, say=print,
          stop_after: int = 0, relay: str = RELAY_BASE) -> int:
    """Poll for commands, answer them, until interrupted."""
    cmd_topic, out_topic = f"{base_topic}-cmd", f"{base_topic}-out"
    since = int(time.time()) - 30
    seen = set()
    served = 0
    quiet = 0
    while True:
        messages = fetch(cmd_topic, since, base=relay)
        for message in messages:
            ident = str(message.get("id") or "")
            when = int(message.get("time") or 0)
            if when and when > since:
                since = when
            if not ident or ident in seen:
                continue
            seen.add(ident)
            parsed = parse_command(str(message.get("message") or ""))
            if not parsed:
                continue
            nonce, query = parsed
            if f"key={key}" not in query:
                continue
            op = query.split("op=")[-1].split("&")[0]
            say(f"<< {nonce} {op}")
            status, body = link_up_replay(local, token, query)
            head, rest = format_result(nonce, status, body)
            ok, detail = publish(out_topic, head + "\n" + rest, base=relay)
            say(f">> {nonce} {status}, {len(body)} симв. — отправлено: "
                + ("да" if ok else f"нет ({detail})"))
            served += 1
            quiet = 0
            if stop_after and served >= stop_after:
                return served
        quiet += 1
        if quiet % 30 == 0:
            say(f"... жду команды ({time.strftime('%H:%M:%S')})")
        time.sleep(POLL_EVERY)


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="post-box link, no tunnel")
    parser.add_argument("--config", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--key", default="")
    parser.add_argument("--port", type=int, default=3211)
    parser.add_argument("--studio", default="")
    parser.add_argument("--relay", default=RELAY_BASE)
    parser.add_argument("--once", type=int, default=0)
    args = parser.parse_args(argv)

    conf = read_config(args.config or None)
    topic = args.topic or conf.get("topic", "")
    key = args.key or conf.get("key", "")
    relay = args.relay or conf.get("relay", RELAY_BASE)
    if not topic or not key:
        print("нет настроек: нужен файл bridge\\.link\\ntfy.txt со строками\n"
              "  topic: astraforge-…\n  key: …\n(он лежит в архиве моста)")
        return 2
    if not CLEAN_ID.match(topic):
        print(f"тема выглядит странно: {topic!r}")
        return 2

    cfg = astra_d3.Config.load(None)
    if args.studio:
        cfg["studio"] = args.studio
        cfg.resolve()
    studio = cfg.get("studio") or "(не найдена)"
    print("=== AstraForge <-> Arena: канал без туннеля ===")
    print(f"сборка  : {studio_link.build_marker()}")
    print(f"папка   : {BRIDGE_DIR.parent}")
    print(f"студия  : {studio}  (есть: {Path(studio).is_dir()})")
    print(f"тема    : {topic}-cmd / {topic}-out   через {relay}")

    httpd, port, token = start_local(cfg, args.port)
    print(f"сервер  : http://127.0.0.1:{port}  (только этот ПК)")
    probe = link_up.probe_local(port, token)
    print(f"самопроверка: {probe}")
    if probe != "ok":
        print("сервер не отвечает сам себе — дальше нет смысла")
        return 1
    print("жду команды из чата. Это окно не закрывай: Ctrl+C или крестик = стоп.")
    try:
        serve(topic, key, f"http://127.0.0.1:{port}", token,
              stop_after=args.once, relay=relay)
    except KeyboardInterrupt:
        print("\nостановлено")
    finally:
        try:
            httpd.shutdown()
        except Exception:         # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
