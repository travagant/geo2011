#!/usr/bin/env python3
"""Studio link: let an Arena session drive AstraForge (and this toolkit) on your PC.

The studio only listens on ``127.0.0.1:3210``, and an Arena session cannot open
a connection into your machine.  So the direction is reversed: this file runs on
*your* PC, and the session talks to it over a link that starts on your side.

    your PC                                    Arena session
    ┌──────────────────────────┐               ┌──────────────────┐
    │ AstraForge 127.0.0.1:3210│◀──┐           │ https://….       │
    │ studio_link  127.0.0.1:3211│  │           │ trycloudflare.com│
    │ cloudflared ─────────────┼──┴──────────▶│  GET /cmd?...    │
    └──────────────────────────┘   one GET      └──────────────────┘

Everything the session does arrives as a plain ``GET /cmd?token=…&op=…`` and is
answered with JSON.  The tunnel only forwards; the server binds to localhost and
refuses every request without the token.

Start it with ``bridge\\Start studio link.cmd`` (double-click) — that finds
Python, starts this server, opens a temporary cloudflared tunnel, and prints the
URL and token to paste into the chat.

Operations (all ``GET /cmd?token=…&op=…``):

    hello                     who/where: paths, versions, whether the studio answers
    doctor                    the bridge's own health report (torch arch, Blender…)
    list                      GLBs the studio produced, newest first
    assets                    the studio's asset list (via its own HTTP API)
    studio_jobs               the studio's generation jobs
    studio_job&id=…           one studio job, with its steps
    api&path=/api/…[&method=POST][&body=<json>]
                              any studio endpoint; GET out, POST in
    read&path=…[&max=…]       text file (within the allowed roots)
    ls&path=…                 folder listing (within the allowed roots)
    write&path=…&b64=…        write a small text file (config, notes)
    d3tool&args=vdpack scan … run the toolkit, JSON back
    build[&glb=…]             Blender: GLB -> .g/.scene/.ac/.a/.t   (background)
    pack                      unit folder -> Mods/<name>.vdpack     (background)
    run[&glb=…]               build + pack + the `vdpack scan` gate (background)
    job&id=<id>               what a background job has done so far
    jobs_local                every background job this server ran

Long operations never block the request: they return ``{"job": "<id>"}`` and the
outcome is read back with ``op=job&id=<id>``.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import re
import secrets
import shlex
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
REPO_DIR = BRIDGE_DIR.parent
LINK_DIR = BRIDGE_DIR / ".link"
JOBS_DIR = LINK_DIR / "jobs"
TOKEN_FILE = LINK_DIR / "token.txt"
DEFAULT_PORT = 3211
STUDIO_DEFAULT = r"D:\AstraForge"
MAX_TEXT = 400_000          # bytes returned by `read`
MAX_WRITE = 256_000         # bytes accepted by `write`

sys.path.insert(0, str(BRIDGE_DIR))
import astra_d3  # noqa: E402  - the bridge itself, reused for build/pack


# --------------------------------------------------------------------------- #
#  state
# --------------------------------------------------------------------------- #
class Link:
    def __init__(self, cfg, port, studio_url):
        self.cfg = cfg
        self.port = port
        self.studio_url = studio_url.rstrip("/")
        LINK_DIR.mkdir(parents=True, exist_ok=True)
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        self.token = self._token()
        self.jobs = {}
        self.lock = threading.Lock()

    @staticmethod
    def _token():
        if TOKEN_FILE.is_file():
            saved = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if len(saved) >= 16:
                return saved
        token = secrets.token_urlsafe(24)
        TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
        return token

    # -- allowed places ---------------------------------------------------- #
    def roots(self):
        out = [Path(REPO_DIR), JOBS_DIR]
        for key in ("studio", "donor", "mods"):
            value = self.cfg.get(key)
            path = Path(os.path.expandvars(str(value))) if value else None
            if path is not None and path.is_absolute():
                out.append(path)
        return [p.resolve() for p in out]

    def resolve(self, raw):
        path = Path(os.path.expandvars(str(raw))).resolve()
        for root in self.roots():
            if path == root or root in path.parents:
                return path
        raise PermissionError(f"{path} is outside the folders this link may touch")

    # -- async jobs -------------------------------------------------------- #
    def start(self, kind, fn, params=None):
        job_id = time.strftime("%H%M%S") + "-" + secrets.token_hex(3)
        record = {"id": job_id, "kind": kind, "params": params or {},
                  "status": "running", "started": time.time(),
                  "finished": None, "result": None, "error": "",
                  "tail": ""}
        with self.lock:
            self.jobs[job_id] = record

        def work():
            try:
                result = fn()
                record["result"] = result
                record["status"] = "ok" if not result.get("failed") else "failed"
            except BaseException as exc:  # noqa: BLE001 - reported to the caller
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["traceback"] = traceback.format_exc()
                record["status"] = "failed"
            finally:
                record["finished"] = time.time()
                record["seconds"] = round(record["finished"] - record["started"], 1)
                self._save(record)

        threading.Thread(target=work, daemon=True).start()
        return {"job": job_id, "status": "running"}

    def _save(self, record):
        path = JOBS_DIR / f"{record['id']}.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False, default=str)

    def job(self, job_id):
        record = self.jobs.get(job_id)
        if record is None:
            path = JOBS_DIR / f"{job_id}.json"
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
            return {"error": f"no job {job_id}"}
        return dict(record)


# --------------------------------------------------------------------------- #
#  the studio's own HTTP API
# --------------------------------------------------------------------------- #
def studio_call(base, path, method="GET", body=None, timeout=120):
    """One call to AstraForge; returns ``{status, body}`` and never raises."""
    url = base + path
    data = None
    if body not in (None, ""):
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method.upper())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(2_000_000)
            return {"status": resp.status, "url": url, "body": _maybe_json(raw)}
    except urllib.error.HTTPError as exc:
        raw = exc.read(200_000)
        return {"status": exc.code, "url": url, "body": _maybe_json(raw)}
    except Exception as exc:  # noqa: BLE001 - the caller wants the reason
        return {"status": 0, "url": url, "error": f"{type(exc).__name__}: {exc}",
                "hint": "is the studio running? (npm start / AstraForge.exe)"}


def _maybe_json(raw):
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return text[:MAX_TEXT]


# --------------------------------------------------------------------------- #
#  operations
# --------------------------------------------------------------------------- #
def op_hello(link, params):
    studio = studio_call(link.studio_url, "/api/health", timeout=5)
    assets = astra_d3.discover_glbs(link.cfg["assets"])
    return {
        "where": socket.gethostname(),
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "studio_path": link.cfg["studio"],
        "studio_api": studio,
        "blender": link.cfg["blender"],
        "assets": link.cfg["assets"],
        "glb_count": len(assets),
        "newest_glb": assets[0]["path"] if assets else "",
        "donor": link.cfg["donor"],
        "mods": link.cfg["mods"],
        "reference_pack": link.cfg["reference_pack"],
        "repo": str(REPO_DIR),
        "port": link.port,
    }


def op_doctor(link, params):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        astra_d3.cmd_doctor(link.cfg, argparse.Namespace())
    return {"report": buf.getvalue()}


def op_list(link, params):
    rows = astra_d3.discover_glbs(link.cfg["assets"])
    limit = int(params.get("limit") or 20)
    return {"count": len(rows), "rows": rows[:limit]}


def op_assets(link, params):
    calls = []
    for path in ("/api/forge/assets", "/api/assets", "/api/status"):
        if path == "/api/assets":
            break                      # that one is a POST endpoint in 0.3
        result = studio_call(link.studio_url, path, timeout=15)
        calls.append({"path": path, **result})
        if result.get("status") == 200:
            return {"path": path, "body": result["body"], "tried": calls}
    return {"failed": True, "tried": calls,
            "hint": "no asset endpoint answered; run op=api on /api/health first"}


def op_studio_jobs(link, params):
    out = {}
    for path in ("/api/forge/jobs", "/api/jobs"):
        result = studio_call(link.studio_url, path, timeout=15)
        out[path] = result
        if result.get("status") == 200:
            body = result["body"]
            if isinstance(body, list):
                body = body[-10:]
            return {"path": path, "body": body, "tried": out}
    return {"failed": True, "tried": out}


def op_studio_job(link, params):
    job_id = params.get("id") or ""
    if not job_id:
        raise ValueError("op=studio_job needs &id=<studio job id>")
    out = {}
    for path in (f"/api/forge/jobs/{job_id}", f"/api/jobs/{job_id}"):
        result = studio_call(link.studio_url, path, timeout=30)
        out[path] = result
        if result.get("status") == 200:
            return {"path": path, "body": result["body"]}
    return {"failed": True, "tried": out}


def op_api(link, params):
    path = params.get("path") or ""
    if not path.startswith("/api/"):
        raise ValueError("op=api needs &path=/api/...")
    method = (params.get("method") or "GET").upper()
    body = params.get("body") or ""
    if params.get("b64"):
        body = base64.b64decode(params["b64"]).decode("utf-8")
    return studio_call(link.studio_url, path, method=method, body=body or None,
                       timeout=int(params.get("timeout") or 120))


def op_read(link, params):
    path = link.resolve(params.get("path") or "")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    limit = min(int(params.get("max") or MAX_TEXT), MAX_TEXT)
    raw = path.read_bytes()[:limit]
    try:
        text = raw.decode("utf-8")
        return {"path": str(path), "bytes": path.stat().st_size, "text": text}
    except UnicodeDecodeError:
        return {"path": str(path), "bytes": path.stat().st_size,
                "base64": base64.b64encode(raw).decode(), "binary": True}


def op_ls(link, params):
    path = link.resolve(params.get("path") or REPO_DIR)
    rows = []
    for entry in sorted(path.iterdir())[:500]:
        try:
            stat = entry.stat()
        except OSError:
            continue
        rows.append({"name": entry.name, "dir": entry.is_dir(),
                     "bytes": stat.st_size,
                     "mtime": time.strftime("%Y-%m-%d %H:%M",
                                            time.localtime(stat.st_mtime))})
    return {"path": str(path), "rows": rows}


def op_write(link, params):
    path = link.resolve(params.get("path") or "")
    raw = base64.b64decode(params["b64"]) if params.get("b64") else \
        (params.get("text") or "").encode("utf-8")
    if len(raw) > MAX_WRITE:
        raise ValueError(f"{len(raw)} bytes is more than {MAX_WRITE}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": str(path), "bytes": len(raw)}


def op_d3tool(link, params):
    args = shlex.split(params.get("args") or "")
    if not args:
        raise ValueError("op=d3tool needs &args=<vdpack scan …>")
    code, out = astra_d3.run(astra_d3.d3tool_command(link.cfg, *args),
                             cwd=link.cfg["d3tool_repo"])
    payload = out.strip()
    try:
        parsed = json.loads(payload)
    except ValueError:
        parsed = None
    return {"exit": code, "json": parsed,
            "text": payload[-8000:] if parsed is None else ""}


def _build(link, glb=""):
    glb = glb or (astra_d3.newest_glb(link.cfg["assets"]) or {}).get("path", "")
    if not glb:
        return {"failed": True, "reason": "no GLB under " + link.cfg["assets"]}
    if not link.cfg["donor"]:
        return {"failed": True, "reason": "`donor` is not set in bridge\\config.json"}
    paths = astra_d3.build_paths(link.cfg, glb)
    code, out = astra_d3.run(astra_d3.blender_command(link.cfg, glb),
                             log=paths["log"])
    report = astra_d3.read_build_report(paths["report"])
    return {"glb": glb, "exit": code, "g": paths["g"],
            "g_exists": Path(paths["g"]).is_file(),
            "stats": report.get("stats"), "warnings": report.get("warnings"),
            "error": report.get("error", ""),
            "log": (report.get("log") or [])[-20:],
            "output": out.strip()[-4000:] if code else "",
            "failed": code != 0 or not Path(paths["g"]).is_file()}


def _pack(link):
    if not link.cfg["donor"]:
        return {"failed": True, "reason": "`donor` is not set in bridge\\config.json"}
    code, out = astra_d3.run(astra_d3.pack_command(link.cfg),
                             cwd=link.cfg["d3tool_repo"])
    result = {"pack_exit": code, "pack_output": out.strip()[-4000:]}
    if code != 0:
        result["failed"] = True
        return result
    scan_code, scan_out = astra_d3.run(astra_d3.scan_command(link.cfg),
                                       cwd=link.cfg["d3tool_repo"])
    ok, messages = astra_d3.scan_verdict(scan_out)
    result.update({"scan_exit": scan_code, "scan_ok": ok,
                   "scan_messages": messages[:20]})
    result["failed"] = not ok
    return result


def op_build(link, params):
    return link.start("build", lambda: _build(link, params.get("glb", "")),
                      {"glb": params.get("glb", "")})


def op_pack(link, params):
    return link.start("pack", lambda: _pack(link))


def op_run(link, params):
    glb = params.get("glb", "")

    def both():
        built = _build(link, glb)
        if built.get("failed"):
            return {"build": built, "failed": True}
        packed = _pack(link)
        return {"build": built, "pack": packed, "failed": packed.get("failed", False)}

    return link.start("run", both, {"glb": glb})


OPS = {
    "hello": op_hello,
    "doctor": op_doctor,
    "list": op_list,
    "assets": op_assets,
    "studio_jobs": op_studio_jobs,
    "studio_job": op_studio_job,
    "job": lambda link, params: link.job(params.get("id") or ""),
    "api": op_api,
    "read": op_read,
    "ls": op_ls,
    "write": op_write,
    "d3tool": op_d3tool,
    "build": op_build,
    "pack": op_pack,
    "run": op_run,
}


# --------------------------------------------------------------------------- #
#  HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    link: Link = None
    server_version = "studio-link/1.0"
    # HTTP/1.1 with an explicit Content-Length on every answer (see `_send`):
    # a proxy in front of us — cloudflared, an ssh tunnel — behaves better when
    # the origin speaks keep-alive, and a wrong length would hang a client
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep stdout for the operator
        pass

    def _send(self, payload, status=200):
        raw = json.dumps(payload, indent=2, ensure_ascii=False,
                         default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        params = {key: values[-1]
                  for key, values in urllib.parse.parse_qs(parsed.query,
                                                           keep_blank_values=True).items()}
        started = time.time()
        if parsed.path in ("/", "/cmd", "/ping"):
            if not hmac.compare_digest(params.get("token", ""), self.link.token):
                self._send({"ok": False, "error": "bad token"}, 403)
                self._audit(params, "403 bad token", started)
                return
            op = params.get("op") or "hello"
            if op == "jobs_local":
                body = {"ok": True,
                        "jobs": [{k: v for k, v in job.items() if k != "result"}
                                 for job in self.link.jobs.values()]}
            elif op in OPS:
                try:
                    body = {"ok": True, "op": op, "result": OPS[op](self.link, params)}
                except Exception as exc:  # noqa: BLE001 - reported, never fatal
                    body = {"ok": False, "op": op,
                            "error": f"{type(exc).__name__}: {exc}",
                            "traceback": traceback.format_exc()[-1500:]}
            else:
                body = {"ok": False, "error": f"unknown op {op!r}",
                        "ops": sorted(OPS) + ["jobs_local"]}
            self._send(body, 200 if body.get("ok") else 400)
            self._audit(params, body.get("ok"), started, body)
            return
        self._send({"ok": False, "error": "use /cmd?token=…&op=…"}, 404)

    def do_POST(self):
        self._send({"ok": False, "hint": "POST is translated for you: "
                                         "GET /cmd?op=api&path=/api/…&method=POST"
                                         "&body=<json>"}, 405)

    def _audit(self, params, result, started, body=None):
        keep = {k: v for k, v in params.items() if k != "token"}
        for key in ("b64", "body", "text"):
            if key in keep:
                keep[key] = f"<{len(keep[key])} chars>"
        line = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "op": keep.pop("op", ""),
            "params": keep,
            "ok": result is True or result == "ok",
            "status": str(result)[:80],
            "ms": int((time.time() - started) * 1000),
        }
        try:
            with open(LINK_DIR / "log.txt", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass


def build_link(args) -> Link:
    cfg = astra_d3.Config.load(args.config)
    if args.studio:
        cfg["studio"] = args.studio
        cfg.resolve()
    link = Link(cfg, args.port, args.studio_url)
    return link


def serve(args) -> int:
    link = build_link(args)
    Handler.link = link
    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    httpd.daemon_threads = True
    print(f"studio link listening on http://{args.bind}:{args.port}")
    print(f"studio API assumed at {link.studio_url}")
    print(f"token {link.token}")
    print(f"allowed roots: " + ", ".join(str(r) for r in link.roots()))
    print("waiting for the tunnel…", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def selftest(args) -> int:
    """Answer one `hello` against a running server — proves token + bind work."""
    link = build_link(args)
    query = urllib.parse.urlencode({"token": link.token, "op": "hello"})
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{args.port}/cmd?{query}", timeout=20) as resp:
            print(resp.read().decode("utf-8"))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"selftest failed: {type(exc).__name__}: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("cmd", nargs="?", default="serve",
                        choices=["serve", "selftest", "token"])
    parser.add_argument("-c", "--config", default="")
    parser.add_argument("--studio", default="")
    parser.add_argument("--port", type=int, default=int(os.environ.get("D3_LINK_PORT",
                                                                     DEFAULT_PORT)))
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--studio-url", default="http://127.0.0.1:3210")
    args = parser.parse_args(argv)
    if args.cmd == "token":
        print(build_link(args).token)
        return 0
    if args.cmd == "selftest":
        return selftest(args)
    return serve(args)


if __name__ == "__main__":
    sys.exit(main())
