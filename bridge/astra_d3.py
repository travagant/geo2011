#!/usr/bin/env python3
"""Bridge: AstraForge asset -> Disciples III unit -> mod `.vdpack`.

AstraForge (a local 3D studio, ``D:\\AstraForge`` by default) turns a picture
or a prompt into a rigged GLB.  A Disciples III unit is not a GLB: the game
reads ``.g`` geometry, a ``.scene`` graph, ``.ac``/``.a`` animation streams and
``.t`` textures, and it only accepts a replacement that an ``index`` manifest
describes (``docs/FORMATS.md`` §6f, §6g, §6h).  This tool is the missing link:

    AstraForge  --export-->  <asset>/exports/*.glb
        |
        |  `build`   Blender (headless) + the io_scene_d3unit add-on
        v
    <unit>/resources/characters/.../character_x.g  (+ .scene/.ac/.a/.t)
        |
        |  `pack`    d3tool vdpack pack --like ... -o Mods/<name>.vdpack
        v
    Mods/<name>.vdpack     verified with `d3tool vdpack scan --strict`

Nothing here needs Blender or d3tool to be importable: the bridge only builds
command lines and reads their reports, so it can be tested (and used to
diagnose) anywhere.

Commands::

    astra_d3.py doctor                  what is installed, and will it run here
    astra_d3.py list [--limit N]        the GLBs AstroForge has produced
    astra_d3.py build [--glb PATH]      Blender: GLB -> .g/.scene/.ac/.a/.t
    astra_d3.py pack                    unit folder -> Mods/<name>.vdpack
    astra_d3.py run [--glb PATH]        build then pack, with the scan gate
    astra_d3.py watch [--interval 5]    run on every new export
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
DEFAULT_STUDIO = r"D:\AstraForge"
USER_MOD_FOLDER = r"Documents\Disciples III Reincarnation\Mods"

DEFAULTS = {
    # AstraForge
    "studio": DEFAULT_STUDIO,
    "assets": "",                 # <studio>\data\library\assets
    "comfy_python": "",           # <studio>\runtime\comfy\.venv\Scripts\python.exe
    "blender": "",                # auto-detected under <studio>\runtime\blender
    # Disciples III side
    "d3tool_repo": "",            # folder that contains the `d3tool` package
    "python": "",                 # interpreter that can `import d3tool`
    "donor": "",                  # vanilla unit folder (.g/.a/.ac/.scene donors)
    "target": r"resources\characters\empire\inquisitor",
    "mods": "",                   # %USERPROFILE%\Documents\...\Mods
    "mod_name": "AstraForgeUnit",
    "reference_pack": "",         # shape donor for `vdpack pack --like`
    # export
    "weights": "AUTO",            # AUTO | 2 | 3 | 4
    "anim": True,
    "keep_temp": False,
    "blender_args": [],
}


# --------------------------------------------------------------------------- #
#  configuration
# --------------------------------------------------------------------------- #
class Config(dict):
    """Paths and switches, with the AstraForge defaults filled in."""

    def __init__(self, values=None):
        super().__init__(DEFAULTS)
        self["_path"] = ""
        if values:
            self.update(values)

    @classmethod
    def load(cls, path: str = "") -> "Config":
        cfg = cls()
        if path:
            with open(path, "r", encoding="utf-8-sig") as fh:
                cfg.update(json.load(fh))
            cfg["_path"] = path
        cfg.resolve()
        return cfg

    def resolve(self) -> None:
        """Derive what the user did not write down."""
        studio = Path(os.path.expandvars(str(self["studio"])))
        self["studio"] = str(studio)
        self["assets"] = str(Path(os.path.expandvars(self["assets"])) if self["assets"]
                            else studio / "data" / "library" / "assets")
        self["comfy_python"] = str(Path(os.path.expandvars(self["comfy_python"]))
                                   if self["comfy_python"]
                                   else studio / "runtime" / "comfy" / ".venv"
                                   / "Scripts" / "python.exe")
        self["blender"] = self["blender"] or find_blender(studio) or ""
        self["mods"] = self["mods"] or guess_mods()
        self["python"] = self["python"] or sys.executable
        self["d3tool_repo"] = self["d3tool_repo"] or str(BRIDGE_DIR.parent)

    def save(self, path: str = "") -> str:
        target = path or self.get("_path") or str(BRIDGE_DIR / "config.json")
        data = {k: v for k, v in self.items() if not k.startswith("_")}
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return target


def find_blender(studio: Path) -> str:
    """``blender.exe`` bundled with the studio, or a system install."""
    for pattern in ("runtime/blender/**/blender.exe",
                    "runtime/**/blender.exe"):
        for hit in sorted(studio.glob(pattern)):
            return str(hit)
    for base in (r"C:\Program Files\Blender Foundation",):
        root = Path(base)
        if root.is_dir():
            for hit in sorted(root.glob("Blender */blender.exe"), reverse=True):
                return str(hit)
    return ""


def guess_mods() -> str:
    """The game's ``Mods`` folder, if it is where the loader looks."""
    documents = Path(os.environ.get("USERPROFILE", "")) / "Documents"
    if not documents.is_dir():
        return ""
    for hit in sorted(documents.glob("*")):
        candidate = hit / "Mods"
        if candidate.is_dir() and any(
                p.suffix.lower() == ".vdpack" for p in candidate.glob("*")):
            return str(candidate)
    return ""


# --------------------------------------------------------------------------- #
#  what the studio produced
# --------------------------------------------------------------------------- #
_UUID = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$")


def discover_glbs(assets: str) -> list:
    """Every ``.glb`` under the studio's asset tree, newest first.

    The studio keeps one folder per asset (``<uuid>/``) with its versions and
    exports inside; the layout moved between 0.3 and 0.4, so the bridge does
    not read ``asset.json`` — it walks for ``.glb`` and takes the newest.  The
    asset folder name is carried along so a report can say *which* asset the
    unit came from.
    """
    root = Path(assets)
    if not root.is_dir():
        return []
    found = []
    for path in root.rglob("*.glb"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        parts = path.relative_to(root).parts
        asset = parts[0] if parts else ""
        found.append({
            "path": str(path),
            "asset": asset if _UUID.match(asset.lower()) else "",
            "name": path.stem,
            "bytes": stat.st_size,
            "mtime": stat.st_mtime,
            "is_export": any(part.lower().startswith("export")
                             for part in parts),
        })
    found.sort(key=lambda row: row["mtime"], reverse=True)
    return found


def newest_glb(assets: str) -> dict:
    rows = discover_glbs(assets)
    return rows[0] if rows else {}


# --------------------------------------------------------------------------- #
#  command lines
# --------------------------------------------------------------------------- #
def build_paths(cfg: Config, glb: str, base: str = "") -> dict:
    """Where the ``.g``, its folder and the report land."""
    base = base or Path(glb).stem
    out_dir = Path(os.path.expandvars(cfg["donor"])) if cfg["donor"] else Path.cwd()
    work = Path(cfg["studio"]) / "data" / "runtime" / "d3bridge"
    return {
        "g": str(out_dir / (base + ".g")),
        "dir": str(out_dir),
        "report": str(work / (base + ".build.json")),
        "log": str(work / (base + ".build.log")),
    }


def blender_command(cfg: Config, glb: str, base: str = "") -> list:
    """The headless Blender run that turns one GLB into a unit folder."""
    if not cfg["blender"]:
        raise SystemExit("blender not found: set `blender` in bridge config")
    paths = build_paths(cfg, glb, base)
    cmd = [cfg["blender"], "-b", "--factory-startup",
           "--python", str(BRIDGE_DIR / "build_d3.py"),
           "--",
           glb,
           paths["g"],
           paths["report"],
           "--donor", cfg["donor"] or "",
           "--weights", str(cfg.get("weights", "AUTO")),
           "--addon", str(Path(cfg["d3tool_repo"]).parent / "blender"
                          / "io_scene_d3unit")
           if (Path(cfg["d3tool_repo"]).parent / "blender" / "io_scene_d3unit").is_dir()
           else "",
           "--d3tool", cfg["d3tool_repo"]]
    if not cfg.get("anim", True):
        cmd.append("--no-anim")
    if cfg.get("keep_temp"):
        cmd.append("--keep-temp")
    cmd.extend([str(a) for a in cfg.get("blender_args") or []])
    return cmd


def d3tool_command(cfg: Config, *args: str) -> list:
    """``python -m d3tool ...`` with the repository as the working directory."""
    interpreter = cfg.get("python") or sys.executable
    return [interpreter, "-m", "d3tool", *map(str, args)]


def pack_command(cfg: Config, unit_dir: str = "") -> list:
    """``vdpack pack`` for the exported unit folder."""
    unit_dir = unit_dir or cfg["donor"]
    name = cfg["mod_name"]
    out = str(Path(cfg["mods"]) / (name + ".vdpack")) if cfg["mods"] else name + ".vdpack"
    args = ["vdpack", "pack", unit_dir, "-o", out, "--name", name]
    if cfg["reference_pack"]:
        args += ["--like", cfg["reference_pack"]]
    return d3tool_command(cfg, *args)


def scan_command(cfg: Config, target: str = "") -> list:
    """``vdpack scan --json --strict`` over the pack that was just written."""
    target = target or (str(Path(cfg["mods"]) / (cfg["mod_name"] + ".vdpack"))
                        if cfg["mods"] else cfg["mod_name"] + ".vdpack")
    return d3tool_command(cfg, "vdpack", "scan", target, "--json")


# --------------------------------------------------------------------------- #
#  running things
# --------------------------------------------------------------------------- #
def run(cmd: list, cwd: str = "", log: str = "") -> tuple:
    """Run a command; return ``(code, output)``, never raise on failure."""
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(cmd, cwd=cwd or None, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        return 127, str(exc)
    output = (proc.stdout or "") + (proc.stderr or "")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("$ " + " ".join(str(c) for c in cmd) + "\n")
            fh.write(output)
            fh.write(f"\n[exit {proc.returncode}]\n")
    return proc.returncode, output


def read_build_report(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def scan_verdict(payload) -> tuple:
    """``(ok, messages)`` from ``vdpack scan --json``.

    The gate is deliberately the toolkit's own verdict rather than a second
    implementation of it: if `vdpack scan` says a pack would be rejected, the
    bridge must not report success (`docs/FORMATS.md` §6h).
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return False, ["scan output is not JSON"]
    if not isinstance(payload, dict):
        return False, ["scan output has an unexpected shape"]
    messages = [row.get("text", "")
                for row in payload.get("findings") or []
                if row.get("level") in ("bad", "warn")]
    packs = payload.get("packs") or []
    bad = any(row.get("level") == "bad" for row in payload.get("findings") or [])
    ok = bool(packs) and not bad
    return ok, messages


# --------------------------------------------------------------------------- #
#  commands
# --------------------------------------------------------------------------- #
def cmd_doctor(cfg: Config, args) -> int:
    rows = []

    def add(label, value, note=""):
        rows.append((label, value or "—", note))

    studio = Path(cfg["studio"])
    add("studio", str(studio) if studio.is_dir() else f"{studio} (missing)")
    add("assets", cfg["assets"] if Path(cfg["assets"]).is_dir() else
        f"{cfg['assets']} (missing)")
    add("blender", cfg["blender"])
    add("comfy python", cfg["comfy_python"] if Path(cfg["comfy_python"]).is_file() else
        f"{cfg['comfy_python']} (missing)")
    add("d3tool repo", cfg["d3tool_repo"])
    add("donor", cfg["donor"] if cfg["donor"] and Path(cfg["donor"]).is_dir() else
        (cfg["donor"] or "—") + (" (missing)" if cfg["donor"] else ""))
    add("mods", cfg["mods"] if cfg["mods"] and Path(cfg["mods"]).is_dir() else
        (cfg["mods"] or "—") + (" (missing)" if cfg["mods"] else ""))
    add("reference pack", cfg["reference_pack"])

    torch_note = ""
    if Path(cfg["comfy_python"]).is_file():
        code, out = run([cfg["comfy_python"], "-c",
                         "import torch;print(torch.__version__);"
                         "print(torch.version.cuda);"
                         "print(torch.cuda.get_device_capability());"
                         "print(';'.join(torch.cuda.get_arch_list()))"])
        lines = [line.strip() for line in out.strip().splitlines() if line.strip()]
        if code == 0 and len(lines) >= 4:
            version, cuda, cap, arch = lines[-4:]
            torch_note = f"torch {version} / CUDA {cuda} / sm {cap}"
            if "sm_61" not in arch and "sm_60" not in arch:
                torch_note += "  ← no Pascal kernel (needs cu121/cu126 build)"
        else:
            torch_note = out.strip().splitlines()[-1] if out.strip() else "torch check failed"
    add("torch", torch_note)

    if os.name == "nt":
        code, out = run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                         "--format=csv,noheader"])
        add("gpu", out.strip() if code == 0 else "nvidia-smi failed")

    width = max(len(r[0]) for r in rows)
    for label, value, note in rows:
        print(f"  {label.ljust(width)}  {value}" + (f"   {note}" if note else ""))
    return 0


def cmd_list(cfg: Config, args) -> int:
    rows = discover_glbs(cfg["assets"])
    if not rows:
        print(f"no .glb under {cfg['assets']}")
        return 1
    for row in rows[: args.limit]:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["mtime"]))
        tag = "export" if row["is_export"] else "asset"
        print(f"  {when}  {row['bytes']:>10}  {tag:6}  {row['path']}")
    return 0


def cmd_build(cfg: Config, args) -> int:
    glb = args.glb or (newest_glb(cfg["assets"]) or {}).get("path", "")
    if not glb or not Path(glb).is_file():
        print("no GLB to build: nothing under the studio's asset tree")
        return 1
    if not cfg["donor"]:
        print("`donor` is not set: it is the vanilla unit folder whose "
              ".g/.a/.ac/.scene the exporter reads as donors, and where the "
              "new files land")
        return 1
    cmd = blender_command(cfg, glb, args.base)
    paths = build_paths(cfg, glb, args.base)
    print(f"GLB    {glb}")
    print(f"->     {paths['g']}")
    code, out = run(cmd, log=paths["log"])
    report = read_build_report(paths["report"])
    for line in (report.get("log") or []):
        print("  " + line)
    if code != 0 or not Path(paths["g"]).is_file():
        print(out.strip()[-4000:])
        print(f"build failed (exit {code}); log {paths['log']}")
        return 1
    stats = report.get("stats") or {}
    print(f"ok: {stats.get('parts', '?')} part(s), "
          f"{stats.get('vertices', '?')} vertices, "
          f"{stats.get('bones', '?')} bone(s)")
    return 0


def cmd_pack(cfg: Config, args) -> int:
    if not cfg["donor"]:
        print("`donor` is not set: it is the unit folder to pack")
        return 1
    code, out = run(pack_command(cfg, cfg["donor"]), cwd=cfg["d3tool_repo"])
    print(out.strip()[-4000:])
    if code != 0:
        return code
    if args.no_gate:
        return 0
    code, out = run(scan_command(cfg), cwd=cfg["d3tool_repo"])
    ok, messages = scan_verdict(out)
    for line in messages[:10]:
        print("  ! " + line)
    if not ok:
        print("scan gate: the loader would reject this pack — not shipping it")
        return 1
    print("scan gate: ok")
    return 0


def cmd_run(cfg: Config, args) -> int:
    code = cmd_build(cfg, args)
    if code != 0:
        return code
    return cmd_pack(cfg, args)


def cmd_watch(cfg: Config, args) -> int:
    seen = {row["path"] for row in discover_glbs(cfg["assets"])}
    print(f"watching {cfg['assets']} every {args.interval}s "
          f"({len(seen)} GLB to start); Ctrl+C to stop")
    try:
        while True:
            time.sleep(args.interval)
            rows = discover_glbs(cfg["assets"])
            fresh = [row for row in rows if row["path"] not in seen]
            for row in reversed(fresh):          # oldest new first
                seen.add(row["path"])
                print(f"\nnew export: {row['path']}")
                args.glb = row["path"]
                cmd_run(cfg, args)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


# --------------------------------------------------------------------------- #
#  entry point
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="astra_d3",
        description="Bridge AstraForge exports into Disciples III mod packs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Commands::")[-1])
    parser.add_argument("-c", "--config", default="",
                        help="bridge config JSON (see bridge/config.example.json)")
    parser.add_argument("--studio", default="",
                        help=f"AstraForge folder (default {DEFAULT_STUDIO})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="what is installed and will it run")
    p_list = sub.add_parser("list", help="the GLBs the studio has produced")
    p_list.add_argument("--limit", type=int, default=10)

    def common(p):
        p.add_argument("--glb", default="", help="use this GLB instead of the newest")
        p.add_argument("--base", default="", help="unit base name (default: GLB stem)")

    p_build = sub.add_parser("build", help="Blender: GLB -> .g/.scene/.ac/.a/.t")
    common(p_build)
    p_pack = sub.add_parser("pack", help="unit folder -> Mods/<name>.vdpack")
    p_pack.add_argument("--no-gate", action="store_true",
                        help="skip the `vdpack scan` check (not recommended)")
    p_run = sub.add_parser("run", help="build then pack, with the scan gate")
    common(p_run)
    p_run.add_argument("--no-gate", action="store_true")
    p_watch = sub.add_parser("watch", help="run on every new export")
    common(p_watch)
    p_watch.add_argument("--interval", type=float, default=10.0)
    p_watch.add_argument("--no-gate", action="store_true")

    p_cfg = sub.add_parser("config", help="write a starter config.json")
    p_cfg.add_argument("--out", default="")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)
    if args.studio:
        cfg["studio"] = args.studio
        cfg.resolve()
    if args.cmd == "config":
        path = cfg.save(args.out)
        print(f"wrote {path} — edit `donor`, `mods` and `reference_pack`")
        return 0
    handler = {"doctor": cmd_doctor, "list": cmd_list, "build": cmd_build,
               "pack": cmd_pack, "run": cmd_run, "watch": cmd_watch}[args.cmd]
    return handler(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
