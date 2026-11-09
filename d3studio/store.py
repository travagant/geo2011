"""The studio's library in one SQLite file: projects, assets and jobs.

Plain :mod:`sqlite3`, one connection opened for the process and a lock around
writes — the HTTP handlers run on several threads and the job worker on one
more, so the store must never hand out a half-written row.  Rows are returned as
dicts with the JSON columns already decoded, because every caller (the API, the
worker, the tests) wants the object, not the TEXT.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS assets_by_project ON assets(project_id, created);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    kind TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '{}',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs(status, created);
"""

JSON_COLUMNS = {"meta", "params", "result"}


def new_id() -> str:
    return uuid.uuid4().hex


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    # ---- plumbing ----------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _row(self, row) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        for column in JSON_COLUMNS:
            if column in item:
                try:
                    item[column] = json.loads(item[column] or "{}")
                except ValueError:
                    item[column] = {}
        return item

    def _query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return self._row(row)

    def _write(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._db.execute(sql, args)
            self._db.commit()

    # ---- projects ----------------------------------------------------------
    def create_project(self, name: str) -> dict:
        clean = (name or "").strip() or "Новый проект"
        project = {"id": new_id(), "name": clean, "created": time.time()}
        self._write("INSERT INTO projects(id, name, created) VALUES(?,?,?)",
                    (project["id"], project["name"], project["created"]))
        return project

    def projects(self) -> list[dict]:
        return self._query("SELECT * FROM projects ORDER BY created DESC")

    def project(self, project_id: str) -> dict | None:
        return self._one("SELECT * FROM projects WHERE id=?", (project_id,))

    def ensure_project(self, name: str = "Проект") -> dict:
        """The first project, created on demand — the UI needs something to draw into."""
        known = self.projects()
        return known[0] if known else self.create_project(name)

    # ---- assets ------------------------------------------------------------
    def add_asset(self, project_id: str, kind: str, name: str, path: str,
                  meta: dict | None = None, asset_id: str | None = None) -> dict:
        asset = {
            "id": asset_id or new_id(),
            "project_id": project_id,
            "kind": kind,
            "name": name,
            "path": str(path),
            "meta": meta or {},
            "created": time.time(),
        }
        self._write(
            "INSERT INTO assets(id, project_id, kind, name, path, meta, created) VALUES(?,?,?,?,?,?,?)",
            (asset["id"], project_id, kind, name, asset["path"],
             json.dumps(asset["meta"], ensure_ascii=False), asset["created"]),
        )
        return asset

    def assets(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            return self._query("SELECT * FROM assets WHERE project_id=? ORDER BY created DESC", (project_id,))
        return self._query("SELECT * FROM assets ORDER BY created DESC")

    def asset(self, asset_id: str) -> dict | None:
        return self._one("SELECT * FROM assets WHERE id=?", (asset_id,))

    def delete_asset(self, asset_id: str) -> None:
        self._write("DELETE FROM assets WHERE id=?", (asset_id,))

    # ---- jobs --------------------------------------------------------------
    def create_job(self, kind: str, params: dict | None = None, project_id: str | None = None) -> dict:
        now = time.time()
        job = {"id": new_id(), "project_id": project_id, "kind": kind, "params": params or {},
               "status": "queued", "progress": 0.0, "message": "В очереди", "result": {},
               "created": now, "updated": now}
        self._write(
            "INSERT INTO jobs(id, project_id, kind, params, status, progress, message, result, created, updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job["id"], job["project_id"], job["kind"], json.dumps(job["params"], ensure_ascii=False),
             job["status"], job["progress"], job["message"], "{}", job["created"], job["updated"]),
        )
        return job

    def update_job(self, job_id: str, **fields) -> dict | None:
        if not fields:
            return self.job(job_id)
        allowed = {"status", "progress", "message", "result"}
        values = {k: v for k, v in fields.items() if k in allowed}
        if not values:
            return self.job(job_id)
        if "result" in values:
            values["result"] = json.dumps(values["result"] or {}, ensure_ascii=False)
        if "progress" in values:
            values["progress"] = max(0.0, min(100.0, float(values["progress"])))
        values["updated"] = time.time()
        columns = ", ".join(f"{name}=?" for name in values)
        self._write(f"UPDATE jobs SET {columns} WHERE id=?", tuple(values.values()) + (job_id,))
        return self.job(job_id)

    def job(self, job_id: str) -> dict | None:
        return self._one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def jobs(self, limit: int = 50, project_id: str | None = None) -> list[dict]:
        if project_id:
            return self._query("SELECT * FROM jobs WHERE project_id=? ORDER BY created DESC LIMIT ?",
                               (project_id, int(limit)))
        return self._query("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (int(limit),))

    def unfinished_jobs(self) -> list[dict]:
        return self._query("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY created")
