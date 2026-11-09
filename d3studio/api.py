"""The studio's HTTP API and the page that uses it.

Local-only by default (``127.0.0.1``): the studio has no accounts and no auth, so
it has no business listening on a network interface unless the user asks for it.
Everything is JSON over :mod:`http.server`; the browser app in ``web/`` is served
from the same process, so one command starts the whole studio.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .comfy import Comfy, ComfyError
from .queue import Worker
from .pipeline import HANDLERS, MODEL_SUFFIXES, IMAGE_SUFFIXES

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_UPLOAD = 200 * 1024 * 1024

ROUTES = [
    ("GET", re.compile(r"^/api/health$"), "health"),
    ("GET", re.compile(r"^/api/settings$"), "get_settings"),
    ("PUT", re.compile(r"^/api/settings$"), "put_settings"),
    ("GET", re.compile(r"^/api/projects$"), "list_projects"),
    ("POST", re.compile(r"^/api/projects$"), "create_project"),
    ("GET", re.compile(r"^/api/projects/(?P<project>[^/]+)/assets$"), "list_assets"),
    ("POST", re.compile(r"^/api/projects/(?P<project>[^/]+)/assets$"), "upload_asset"),
    ("GET", re.compile(r"^/api/projects/(?P<project>[^/]+)/exports$"), "list_exports"),
    ("GET", re.compile(r"^/api/jobs$"), "list_jobs"),
    ("POST", re.compile(r"^/api/jobs$"), "create_job"),
    ("GET", re.compile(r"^/api/jobs/(?P<job>[0-9a-f]+)$"), "get_job"),
    ("POST", re.compile(r"^/api/jobs/(?P<job>[0-9a-f]+)/cancel$"), "cancel_job"),
    ("GET", re.compile(r"^/assets/(?P<asset>[0-9a-f]+)$"), "get_asset_file"),
    ("GET", re.compile(r"^/exports/(?P<project>[^/]+)/(?P<path>.+)$"), "get_export_file"),
]


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class StudioServer:
    """The HTTP server plus the objects it needs; keep it in one place for tests."""

    def __init__(self, settings, store, worker, *, host: str = "127.0.0.1", port: int = 0):
        self.settings = settings
        self.store = store
        self.worker = worker
        self.host = host
        self.httpd = ThreadingHTTPServer((host, port), _handler_for(self))
        self.httpd.daemon_threads = True
        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{host}:{self.port}"

    def start(self) -> "StudioServer":
        if self.thread is None:
            self.thread = threading.Thread(target=self.httpd.serve_forever, name="d3studio-http", daemon=True)
            self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def create_app(settings, *, store=None, worker=None, host: str = "127.0.0.1", port: int = 0,
               start_worker: bool = True) -> StudioServer:
    from .store import Store

    store = store or Store(settings.db_path)
    worker = worker or Worker(store, settings, HANDLERS)
    if start_worker and not worker.is_alive():
        worker.start()
    server = StudioServer(settings, store, worker, host=host, port=port)
    # jobs left "queued" by a restart are put back in the queue, running ones are failed:
    # the picture was never saved, so pretending it is still running would lie to the user
    for job in store.unfinished_jobs():
        if job["status"] == "queued":
            worker.submit(job["id"])
        else:
            store.update_job(job["id"], status="failed", message="Задание прервано перезапуском студии")
    return server


def _handler_for(app: StudioServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "d3studio/" + __version__

        # ---- plumbing ------------------------------------------------------
        def log_message(self, fmt, *args):   # keep the console for our own lines
            pass

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _dispatch(self, method: str):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                for route_method, pattern, name in ROUTES:
                    if route_method != method:
                        continue
                    match = pattern.match(path)
                    if match:
                        query = urllib.parse.parse_qs(parsed.query)
                        body = self._body() if method in ("POST", "PUT") else {}
                        result = getattr(self, "api_" + name)(body, match.groupdict(), query)
                        if result is not None:
                            self._json(200, result)
                        return
                if method == "GET" and not path.startswith("/api/"):
                    self._static(path)
                    return
                self._json(404, {"error": "Нет такого адреса: " + path})
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except ComfyError as exc:
                self._json(502, {"error": str(exc)})
            except Exception as exc:   # noqa: BLE001 — one bad request must not kill the server
                self._json(500, {"error": f"{exc.__class__.__name__}: {exc}"})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > MAX_UPLOAD:
                raise ApiError("Файл больше 200 МБ — студия такие не принимает.", 413)
            raw = self.rfile.read(length)
            try:
                doc = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                raise ApiError("Тело запроса — не JSON.") from None
            if not isinstance(doc, dict):
                raise ApiError("Тело запроса должно быть объектом JSON.")
            return doc

        def _json(self, status: int, payload: dict):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def _file(self, path: Path, *, download_name: str | None = None):
            if not path.is_file():
                raise ApiError("Файла нет на диске.", 404)
            data = path.read_bytes()
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            if download_name:
                self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def _static(self, path: str):
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (WEB_DIR / rel).resolve()
            if not str(target).startswith(str(WEB_DIR)) or not target.is_file():
                raise ApiError("Нет такой страницы.", 404)
            self._file(target)

        # ---- API -----------------------------------------------------------
        def api_health(self, body, params, query):
            server = Comfy(app.settings.comfy_url, timeout=3)
            checkpoints: list[str] = []
            reachable = False
            try:
                checkpoints = server.checkpoints()
                reachable = True
            except ComfyError:
                reachable = False
            return {
                "ok": True,
                "version": __version__,
                "comfy": {"url": app.settings.comfy_url, "reachable": reachable,
                          "checkpoints": checkpoints},
                "settings": app.settings.to_dict(),
            }

        def api_get_settings(self, body, params, query):
            return app.settings.to_dict()

        def api_put_settings(self, body, params, query):
            from .config import Settings

            merged = {**app.settings.to_dict(), **{k: v for k, v in body.items() if v is not None}}
            merged["root"] = app.settings.root
            updated = Settings.from_dict(merged, root=app.settings.root)
            updated.save()
            for field in ("comfy_url", "checkpoint", "width", "height", "steps", "cfg", "timeout_s", "d3tool_path"):
                setattr(app.settings, field, getattr(updated, field))
            return app.settings.to_dict()

        def api_list_projects(self, body, params, query):
            projects = app.store.projects()
            for project in projects:
                project["assets"] = len(app.store.assets(project["id"]))
            return {"projects": projects}

        def api_create_project(self, body, params, query):
            name = str(body.get("name") or "").strip()
            if not name:
                raise ApiError("У проекта должно быть имя.")
            return app.store.create_project(name)

        def api_list_assets(self, body, params, query):
            project = self._project(params)
            return {"assets": app.store.assets(project["id"])}

        def api_upload_asset(self, body, params, query):
            project = self._project(params)
            name = str(body.get("name") or "").strip()
            data = body.get("data") or ""
            if not name or not data:
                raise ApiError("Нужны имя файла и его содержимое (base64).")
            suffix = Path(name).suffix.lower()
            if suffix not in MODEL_SUFFIXES | IMAGE_SUFFIXES:
                raise ApiError("Принимаю .glb, .gltf, .png, .jpg, .webp.")
            try:
                raw = base64.b64decode(data.split(",")[-1], validate=True)
            except (ValueError, TypeError):
                raise ApiError("Содержимое файла — не base64.") from None
            directory = app.settings.assets_dir(project["id"])
            directory.mkdir(parents=True, exist_ok=True)
            path = _free_path(directory / Path(name).name)
            path.write_bytes(raw)
            kind = "model" if suffix in MODEL_SUFFIXES else "image"
            asset = app.store.add_asset(project["id"], kind, path.name, str(path),
                                        {"bytes": len(raw), "uploaded": True})
            return asset

        def api_list_exports(self, body, params, query):
            project = self._project(params)
            root = app.settings.exports_dir(project["id"])
            folders = []
            if root.is_dir():
                for folder in sorted(root.iterdir()):
                    if not folder.is_dir():
                        continue
                    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
                    folders.append({"name": folder.name, "files": files})
            return {"exports": folders}

        def api_list_jobs(self, body, params, query):
            limit = int((query.get("limit") or [50])[0])
            project = (query.get("project") or [None])[0]
            return {"jobs": app.store.jobs(limit=limit, project_id=project)}

        def api_create_job(self, body, params, query):
            kind = str(body.get("kind") or "").strip()
            if kind not in HANDLERS:
                raise ApiError("Неизвестный вид задания: " + (kind or "—"))
            project_id = body.get("projectId") or body.get("project_id")
            job_params = body.get("params") or {}
            if not isinstance(job_params, dict):
                raise ApiError("params должен быть объектом.")
            if project_id and not app.store.project(str(project_id)):
                raise ApiError("Проект не найден.", 404)
            if kind == "concept" and not str(job_params.get("prompt") or "").strip():
                raise ApiError("Пустое описание: напишите, что нарисовать.")
            if kind == "export.d3" and not (job_params.get("assetId") or job_params.get("asset_id")):
                raise ApiError("Выберите модель для экспорта.")
            job = app.store.create_job(kind, job_params, project_id)
            app.worker.submit(job["id"])
            return job

        def api_get_job(self, body, params, query):
            job = app.store.job(params["job"])
            if not job:
                raise ApiError("Задание не найдено.", 404)
            return job

        def api_cancel_job(self, body, params, query):
            job = app.store.job(params["job"])
            if not job:
                raise ApiError("Задание не найдено.", 404)
            cancelled = app.worker.cancel(job["id"])
            return {"cancelled": cancelled, "job": app.store.job(job["id"])}

        def api_get_asset_file(self, body, params, query):
            asset = app.store.asset(params["asset"])
            if not asset:
                raise ApiError("Ассет не найден.", 404)
            path = Path(asset["path"]).resolve()
            if not str(path).startswith(str(app.settings.root_path)):
                raise ApiError("Ассет вне папки студии.", 403)
            self._file(path)
            return None

        def api_get_export_file(self, body, params, query):
            project = self._project(params)
            root = app.settings.exports_dir(project["id"]).resolve()
            target = (root / params["path"]).resolve()
            if not str(target).startswith(str(root)):
                raise ApiError("Файл вне папки экспорта.", 403)
            self._file(target, download_name=target.name)
            return None

        # ---- helpers -------------------------------------------------------
        def _project(self, params) -> dict:
            project = app.store.project(params.get("project", ""))
            if not project:
                raise ApiError("Проект не найден.", 404)
            return project

    return Handler


def _free_path(path: Path) -> Path:
    """Never overwrite an uploaded file: `unit.glb` → `unit-2.glb`."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise ApiError("Слишком много файлов с таким именем.")
