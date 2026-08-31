"""Reader / writer for the Disciples 3 animation-configuration file (`.ac`).

The `.ac` is a small text format that maps animation *states* (Idle, Attack,
Damage, Death, Run ...) to external `.a` animation files, frame ranges, FPS and
cross-state links/events.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class State:
    name: str = ""
    file: str = ""
    frame0: int = 0
    frame1: int = 0
    fps: float = 15.0
    priority: int = 256
    flags: int = 0
    links: List[tuple] = field(default_factory=list)  # (target, dir, blend)
    events: List[tuple] = field(default_factory=list)
    meshfile: str = ""
    gaestate: str = ""

    def to_lines(self) -> List[str]:
        lines = [f'state "{self.name}"', "{"]
        lines.append(f'file "{self.file}";')
        lines.append(f"frame0 {self.frame0};")
        lines.append(f"frame1 {self.frame1};")
        lines.append(f"fps {self.fps:.6f};")
        lines.append(f"priority {self.priority};")
        lines.append(f"flags {self.flags};")
        for ev in self.events:
            if len(ev) == 4:
                lines.append(
                    f'event2 "{ev[0]}" {ev[1]} "{ev[2]}" "{ev[3]}";'
                )
        for link in self.links:
            lines.append(f'link "{link[0]}" {link[1]}; blend {link[2]};')
        if self.gaestate:
            lines.append(f'gaestate "{self.gaestate}";')
        if self.meshfile:
            lines.append(f'meshfile "{self.meshfile}";')
        lines.append("}")
        return lines


@dataclass
class AnimConfig:
    version: str = "0.2"
    states: List[State] = field(default_factory=list)


def parse_ac(text: str) -> AnimConfig:
    """Parse a `.ac` file.  A light-weight, tolerance-friendly parser."""
    config = AnimConfig()
    # state blocks are `state "X" { ... }`
    block_re = re.compile(r'state\s+"([^"]+)"\s*\{(.*?)\}', re.S)
    for name, body in block_re.findall(text):
        st = State(name=name)
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(";")
            m = re.match(r'file\s+"([^"]+)"', line)
            if m:
                st.file = m.group(1)
            m = re.match(r"frame0\s+(-?\d+)", line)
            if m:
                st.frame0 = int(m.group(1))
            m = re.match(r"frame1\s+(-?\d+)", line)
            if m:
                st.frame1 = int(m.group(1))
            m = re.match(r"fps\s+([-+.\d]+)", line)
            if m:
                st.fps = float(m.group(1))
            m = re.match(r"priority\s+(\d+)", line)
            if m:
                st.priority = int(m.group(1))
            m = re.match(r"flags\s+(\d+)", line)
            if m:
                st.flags = int(m.group(1))
            m = re.match(r'meshfile\s+"([^"]+)"', line)
            if m:
                st.meshfile = m.group(1)
            m = re.match(r'gaestate\s+"([^"]+)"', line)
            if m:
                st.gaestate = m.group(1)
            m = re.match(r'link\s+"([^"]+)"\s+(-?\d+)\s*;\s*blend\s+(\d+)', line)
            if m:
                st.links.append((m.group(1), int(m.group(2)), int(m.group(3))))
            m = re.match(
                r'event2\s+"([^"]+)"\s+(-?\d+)\s+"([^"]*)"\s+"([^"]*)"', line)
            if m:
                st.events.append((m.group(1), int(m.group(2)), m.group(3), m.group(4)))
        config.states.append(st)
    return config


def write_ac(config: AnimConfig) -> str:
    """Render an :class:`AnimConfig` back into `.ac` text."""
    out = ["// ANIMATION CONFIGURATION file", f"version {config.version}"]
    for st in config.states:
        out.extend(st.to_lines())
    return "\n".join(out) + "\n"


def default_ac(meshfile: str, anim_base: str,
               anim_files: Optional[Dict[str, str]] = None) -> AnimConfig:
    """Build a plausible `.ac` for a skinned character.

    ``anim_files`` maps a state name to an actual `.a` file (relative/absolute)
    discovered in the asset folder, e.g. ``{"Idle": "..._iadd.a",
    "Run": "..._run.a"}``.  Defaults fall back to ``{anim_base}_iadd.a`` and
    ``{anim_base}_run.a``.  The `.a` binaries are *referenced*, not re-created.
    """
    base = anim_base
    files = anim_files or {}
    # derive the resource directory from the meshfile (with backslashes)
    dirname = meshfile.rsplit("\\", 1)[0] if "\\" in meshfile else ""
    idle_f = files.get("Idle", f"{base}_iadd.a")
    run_f = files.get("Run", f"{base}_run.a")
    if "\\" not in idle_f and dirname:
        idle_f = f"{dirname}\\{idle_f.split('/')[-1]}"
    if "\\" not in run_f and dirname:
        run_f = f"{dirname}\\{run_f.split('/')[-1]}"

    states = [
        State("Idle", idle_f, 1, 150, 15.0, flags=1,
              links=[("Attack", 0, 3), ("Damage", 0, 0),
                     ("Death", 0, 0), ("Run", 0, 3)],
              meshfile=meshfile),
        State("Attack", idle_f, 150, 210, 15.0,
              links=[("Idle", 1, 0)], meshfile=meshfile),
        State("Damage", idle_f, 210, 270, 15.0,
              links=[("Idle", 1, 0), ("Run", 0, 0)], gaestate="Idle",
              meshfile=meshfile),
        State("Death", idle_f, 270, 345, 15.0, meshfile=meshfile),
        State("Run", run_f, 1, 16, 15.0, flags=1,
              links=[("Idle", 0, 3)], gaestate="Idle", meshfile=meshfile),
    ]
    return AnimConfig(states=states)


def detect_anim_files(src_dir: str, base: str) -> Dict[str, str]:
    """Resolve the ``.a`` files belonging to geometry ``base`` in ``src_dir``.

    Returns a mapping like ``{"Idle": "<basename>", "Run": "<basename>"}``.

    Resolution order (the folder usually holds *several* units, so the
    ``base`` argument has to drive the choice — taking "the last ``.a`` in
    the folder" hands one unit another unit's animation):

    1. the unit's own ``.ac`` (``<base>.ac``, or the main model's config for a
       ``*_lod`` mesh) — the authoritative source, exactly what the engine
       loads;
    2. ``.a`` files whose stem starts with ``base``;
    3. the conventional ``<base>_iadd.a`` / ``<base>_run.a`` names.
    """
    import glob
    import os

    def _ac_states(stem: str) -> List[Tuple[str, str]]:
        p = os.path.join(src_dir, stem + ".ac")
        if not os.path.isfile(p):
            return []
        try:
            with open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
                cfg = parse_ac(fh.read())
        except OSError:
            return []
        return [(s.name,
                 os.path.basename(s.file.replace("\\", "/").rsplit("/", 1)[-1]))
                for s in cfg.states if s.file]

    main_stem = base[:-4] if base.lower().endswith("_lod") else base
    for stem in dict.fromkeys((base, main_stem)):
        named = [(nm, n) for nm, n in _ac_states(stem)
                 if os.path.isfile(os.path.join(src_dir, n))]
        if named:
            # Keep *every* state, in `.ac` order.  Collapsing this to just
            # Idle/Run dropped the Attack/Damage/Death streams: Angel's `.ac`
            # names five `.a` files totalling 263 frames, and dis3tool
            # concatenates them into the exported animation.
            out: Dict[str, str] = {}
            for nm, n in named:
                out.setdefault(nm or "Idle", n)
            out.setdefault("Idle", named[0][1])
            out.setdefault("Run",
                           next((n for _nm, n in named if "_run." in n),
                                named[0][1]))
            return out

    candidates = sorted(
        n for n in (os.path.basename(c)
                    for c in glob.glob(os.path.join(src_dir, "*.a")))
        if n.startswith(base) or n.startswith(main_stem)
    )
    run = next((n for n in candidates if "_run." in n), None)
    combined = next((n for n in candidates if n is not run), None)
    return {"Idle": combined or f"{base}_iadd.a",
            "Run": run or f"{base}_run.a"}
