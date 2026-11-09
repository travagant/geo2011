"""One worker thread that runs the studio's jobs, in order, one at a time.

Deliberately serial: one video card, one generation.  A job is a row in the store
with a status (``queued`` → ``running`` → ``done``/``failed``/``cancelled``), so
the browser can be closed, reopened, and still see where the work is; cancelling
does not kill anything, it raises a flag the handler polls and the worker turns
into a status.
"""
from __future__ import annotations

import queue as queue_mod
import threading
import time
import traceback

from .comfy import Cancelled


class JobContext:
    """What a handler gets: progress reporting, the cancel flag, the settings."""

    def __init__(self, store, settings, job, cancel_event: threading.Event | None = None):
        self.store = store
        self.settings = settings
        self.job = job
        self._cancelled = cancel_event or threading.Event()
        self._last = -1.0
        self._last_message = None
        self.messages: list[str] = []

    # -- cancellation --------------------------------------------------------
    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise Cancelled("Отменено")

    # -- progress ------------------------------------------------------------
    def progress(self, value: float, message: str | None = None) -> None:
        """Write progress to the store, but not more often than the UI can show it."""
        now = time.monotonic()
        changed_message = message is not None and message != self._last_message
        if not changed_message and abs(value - self._last) < 0.5 and value not in (0.0, 100.0):
            return
        self._last = float(value)
        if changed_message:
            self._last_message = message
            self.messages.append(message)
        self.store.update_job(self.job["id"], progress=value,
                              **({"message": message} if changed_message else {}))

    def say(self, message: str) -> None:
        self.messages.append(message)
        self._last_message = message
        self.store.update_job(self.job["id"], message=message)


class Worker(threading.Thread):
    def __init__(self, store, settings, handlers: dict, *, poll: float = 0.2, on_event=None):
        super().__init__(name="d3studio-worker", daemon=True)
        self.store = store
        self.settings = settings
        self.handlers = handlers
        self.poll = poll
        self.on_event = on_event
        self._queue: queue_mod.Queue[str] = queue_mod.Queue()
        self._stop_event = threading.Event()   # never `_stop`: Thread uses that name
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # ---- public API --------------------------------------------------------
    def submit(self, job_id: str) -> None:
        self._queue.put(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            event = self._cancels.get(job_id)
        if event is not None:
            event.set()
            return True
        job = self.store.job(job_id)
        if job and job["status"] == "queued":
            self.store.update_job(job_id, status="cancelled", message="Отменено до запуска")
            return True
        return False

    def stop(self, wait: float = 2.0) -> None:
        self._stop_event.set()
        self._queue.put("")
        if self.is_alive():
            self.join(timeout=wait)

    # ---- thread ------------------------------------------------------------
    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                job_id = self._queue.get(timeout=self.poll)
            except queue_mod.Empty:
                continue
            if not job_id or self._stop_event.is_set():
                continue
            self._run_one(job_id)

    def _run_one(self, job_id: str) -> None:
        job = self.store.job(job_id)
        if not job or job["status"] != "queued":
            return   # cancelled before it started
        handler = self.handlers.get(job["kind"])
        if handler is None:
            self.store.update_job(job_id, status="failed", message=f"Неизвестный вид задания: {job['kind']}")
            return
        cancel_event = threading.Event()
        with self._lock:
            self._cancels[job_id] = cancel_event
        self.store.update_job(job_id, status="running", progress=0, message="Запуск")
        ctx = JobContext(self.store, self.settings, job, cancel_event)
        try:
            result = handler(job, ctx) or {}
            ctx.raise_if_cancelled()
            self.store.update_job(job_id, status="done", progress=100,
                                  message=result.get("message") or "Готово", result=result)
        except Cancelled:
            self.store.update_job(job_id, status="cancelled", message="Отменено")
        except Exception as exc:   # noqa: BLE001 — a bad job must not kill the worker
            text = str(exc).strip() or exc.__class__.__name__
            self.store.update_job(job_id, status="failed", message=text[:500])
            if self.on_event:
                self.on_event("job.error", {"job": job_id, "error": text, "trace": traceback.format_exc()[-2000:]})
        finally:
            with self._lock:
                self._cancels.pop(job_id, None)
            if self.on_event:
                self.on_event("job.done", {"job": job_id})
