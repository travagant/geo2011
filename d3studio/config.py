"""Settings and folders of the studio.

One JSON file (``<root>/settings.json``) holds everything a run needs: where the
generation server listens, which checkpoint to draw with, the default sizes and
where the d3tool checkout lives.  The root itself defaults to ``./studio-data``
and is created on demand, so a fresh clone can start the studio with one command.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DEFAULT_ROOT = "studio-data"
# The user's local engine (the studio we are replacing) answers the same ComfyUI
# API on 8192; a plain ComfyUI answers on 8188.  Both work — the URL is a setting.
DEFAULT_COMFY = "http://127.0.0.1:8192"
DEFAULT_CHECKPOINT = "sd_xl_base_1.0.safetensors"
SETTINGS_FILE = "settings.json"


@dataclass
class Settings:
    root: str = DEFAULT_ROOT
    comfy_url: str = DEFAULT_COMFY
    checkpoint: str = DEFAULT_CHECKPOINT
    width: int = 1024
    height: int = 1024
    steps: int = 30
    cfg: float = 7.0
    #: folder with the d3tool package; empty means "the repository this file is in"
    d3tool_path: str = ""
    #: how long one generation may take before the job is failed
    timeout_s: int = 1800

    # ---- folders -----------------------------------------------------------
    @property
    def root_path(self) -> Path:
        return Path(self.root).expanduser().resolve()

    @property
    def settings_file(self) -> Path:
        return self.root_path / SETTINGS_FILE

    @property
    def db_path(self) -> Path:
        return self.root_path / "studio.sqlite3"

    @property
    def library(self) -> Path:
        return self.root_path / "library"

    @property
    def jobs_dir(self) -> Path:
        return self.root_path / "jobs"

    def project_dir(self, project_id: str) -> Path:
        return self.library / _safe(project_id)

    def assets_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "assets"

    def exports_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "exports"

    def ensure_folders(self) -> None:
        for path in (self.root_path, self.library, self.jobs_dir):
            path.mkdir(parents=True, exist_ok=True)

    # ---- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, doc: dict, root: str | None = None) -> "Settings":
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in (doc or {}).items() if k in known}
        if root:
            values["root"] = root
        settings = cls(**values)
        # a hand-edited file must not carry a nonsense size/step count into a job
        settings.width = _clamp(settings.width, 64, 2048, 1024)
        settings.height = _clamp(settings.height, 64, 2048, 1024)
        settings.steps = _clamp(settings.steps, 1, 150, 30)
        settings.cfg = _clamp(settings.cfg, 0.0, 30.0, 7.0)
        settings.timeout_s = _clamp(settings.timeout_s, 30, 6 * 3600, 1800)
        return settings

    @classmethod
    def load(cls, root: str | None = None) -> "Settings":
        base = Path(root or DEFAULT_ROOT).expanduser().resolve()
        file = base / SETTINGS_FILE
        doc = {}
        if file.is_file():
            try:
                doc = json.loads(file.read_text(encoding="utf-8-sig") or "{}")
            except (OSError, ValueError):
                doc = {}   # a broken settings file must not stop the studio
        settings = cls.from_dict(doc, root=str(base))
        settings.ensure_folders()
        return settings

    def save(self) -> None:
        self.ensure_folders()
        tmp = self.settings_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.settings_file)

    # ---- d3tool ------------------------------------------------------------
    @property
    def d3tool_repo(self) -> Path:
        if self.d3tool_path:
            return Path(self.d3tool_path).expanduser().resolve()
        return Path(__file__).resolve().parent.parent


def _safe(name: str) -> str:
    """A project id never leaves its folder: no separators, no dots-only names."""
    keep = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(name))
    return keep or "project"


def _clamp(value, low, high, fallback):
    try:
        number = type(fallback)(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))
