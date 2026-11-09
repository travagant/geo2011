#!/usr/bin/env python3
"""Tests for :mod:`d3studio` — the studio's own web app.

    python3 tests/test_studio.py

Everything runs without a video card and without a network: a fake
ComfyUI-compatible server (the same HTTP API the real one speaks) answers
``/object_info``, ``/prompt``, ``/history`` and ``/view``; the d3tool side is a
patched runner that writes the files an export would write.  What is checked is
what the studio promises: a graph the server can actually run, a job that ends in
a saved picture, a cancellation that ends as "cancelled" instead of hanging, a
clear message when the checkpoint is missing, and an HTTP API the page can use.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from d3studio import comfy as comfymod                       # noqa: E402
from d3studio.api import create_app                          # noqa: E402
from d3studio.config import Settings                         # noqa: E402
from d3studio.pipeline import (ExportError, HANDLERS, d3tool_export,  # noqa: E402
                               run_export_d3, slug)
from d3studio.queue import Worker                            # noqa: E402
from d3studio.store import Store                             # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"d3studio-test-picture"
CKPT = "sd_xl_base_1.0.safetensors"


# --------------------------------------------------------------------------
# a ComfyUI-compatible server, in-process
# --------------------------------------------------------------------------
class FakeComfy:
    def __init__(self, *, checkpoints=(CKPT,), complete=True, fail_with=None):
        self.checkpoints = list(checkpoints)
        self.complete = complete
        self.fail_with = fail_with
        self.graphs: list[dict] = []
        self.interrupted = 0
        self.prompt_counter = 0
        self.history_polls = 0
        self._server = None
        self._thread = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "FakeComfy":
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, payload, code=200, raw=None):
                body = raw if raw is not None else json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/octet-stream" if raw else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path.startswith("/object_info"):
                    return self._send({"CheckpointLoaderSimple": {
                        "input": {"required": {"ckpt_name": [fake.checkpoints, {}]}}}})
                if path.startswith("/history/"):
                    fake.history_polls += 1
                    prompt_id = path.rsplit("/", 1)[-1]
                    if not fake.complete:
                        return self._send({})
                    if fake.fail_with:
                        return self._send({prompt_id: {"status": {
                            "status_str": "error",
                            "messages": [["execution_error", {"node_type": "KSampler",
                                                              "exception_message": fake.fail_with}]]},
                            "outputs": {}}})
                    return self._send({prompt_id: {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {"7": {"images": [{"filename": "d3studio_00001_.png",
                                                      "subfolder": "", "type": "output"}]}}}})
                if path.startswith("/view"):
                    return self._send(None, raw=PNG)
                self._send({"error": "no route " + path}, 404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path.startswith("/prompt"):
                    fake.graphs.append(body.get("prompt") or {})
                    fake.prompt_counter += 1
                    return self._send({"prompt_id": f"p{fake.prompt_counter}"})
                if self.path.startswith("/interrupt"):
                    fake.interrupted += 1
                    return self._send({})
                if self.path.startswith("/free"):
                    return self._send({})
                self._send({"error": "no route " + self.path}, 404)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class Harness:
    """A studio in a temporary folder, wired to a fake generation server."""

    def __init__(self, **fake_options):
        self.dir = tempfile.mkdtemp(prefix="d3studio-test-")
        self.settings = Settings.load(self.dir)
        self.fake = FakeComfy(**fake_options).start()
        self.settings.comfy_url = self.fake.url
        self.settings.timeout_s = 60
        self.store = Store(self.settings.db_path)
        self.project = self.store.create_project("Тест")
        self.worker = Worker(self.store, self.settings, HANDLERS, poll=0.05)
        self.worker.start()

    def job(self, kind, params):
        job = self.store.create_job(kind, params, self.project["id"])
        self.worker.submit(job["id"])
        return job

    def wait(self, job_id, statuses=("done", "failed", "cancelled"), seconds=20.0):
        deadline = time.time() + seconds
        while time.time() < deadline:
            job = self.store.job(job_id)
            if job and job["status"] in statuses:
                return job
            time.sleep(0.02)
        return self.store.job(job_id)

    def close(self):
        self.worker.stop()
        self.fake.stop()
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def wait_for(predicate, seconds=10.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------
def test_graph_is_a_runnable_sdxl_chain():
    graph = comfymod.sdxl_graph("knight", negative="blurry", width=1023, height=769,
                                steps=24, cfg=6.5, seed=42, checkpoint="mine.safetensors")
    assert [node["class_type"] for node in graph.values()] == [
        "CheckpointLoaderSimple", "CLIPTextEncode", "CLIPTextEncode", "EmptyLatentImage",
        "KSampler", "VAEDecode", "SaveImage"]
    assert graph["1"]["inputs"]["ckpt_name"] == "mine.safetensors"
    assert graph["2"]["inputs"]["text"] == "knight"
    assert graph["3"]["inputs"]["text"] == "blurry"
    # sizes snap down to a multiple of 8, otherwise the VAE refuses the latent
    assert graph["4"]["inputs"]["width"] % 8 == 0 and graph["4"]["inputs"]["height"] % 8 == 0
    assert graph["4"]["inputs"]["width"] == 1016 and graph["4"]["inputs"]["height"] == 768
    assert (graph["5"]["inputs"]["steps"], graph["5"]["inputs"]["cfg"]) == (24, 6.5)
    assert graph["5"]["inputs"]["seed"] == 42
    # every link points at a node that exists — that is what the server checks first
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph, value
    params = comfymod.graph_params(graph)
    assert params["seed"] == 42 and params["checkpoint"] == "mine.safetensors" and params["width"] == 1016


def test_graph_refuses_empty_prompt_and_fixes_stupid_sizes():
    try:
        comfymod.sdxl_graph("   ")
    except comfymod.ComfyError as exc:
        assert "описание" in str(exc).lower()
    else:
        raise AssertionError("пустой промпт должен отвергаться")
    graph = comfymod.sdxl_graph("x", width=99999, height=-4, steps=0, cfg=99)
    assert graph["4"]["inputs"]["width"] == 2048
    assert 64 <= graph["4"]["inputs"]["height"] <= 2048
    assert graph["5"]["inputs"]["steps"] >= 1
    assert graph["5"]["inputs"]["cfg"] <= 30


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------
def test_store_keeps_projects_assets_and_jobs():
    directory = tempfile.mkdtemp(prefix="d3studio-store-")
    try:
        path = os.path.join(directory, "s.sqlite3")
        store = Store(path)
        project = store.create_project("Первый")
        assert store.projects()[0]["id"] == project["id"]
        assert store.ensure_project()["id"] == project["id"]      # второй раз не создаётся
        asset = store.add_asset(project["id"], "concept", "knight.png", "/tmp/knight.png",
                                {"seed": 7, "steps": 30})
        job = store.create_job("concept", {"prompt": "knight"}, project["id"])
        assert store.asset(asset["id"])["meta"]["seed"] == 7
        store.update_job(job["id"], status="running", progress=42.5, message="Рисую")
        store.update_job(job["id"], progress=120)                 # clamped, not stored as 120
        again = store.job(job["id"])
        assert again["status"] == "running" and again["progress"] == 100
        assert store.unfinished_jobs()[0]["id"] == job["id"]
        store.close()

        reopened = Store(path)                                    # survives a restart
        assert reopened.job(job["id"])["params"]["prompt"] == "knight"
        reopened.close()
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_settings_survive_a_broken_file_and_clamp_junk():
    directory = tempfile.mkdtemp(prefix="d3studio-settings-")
    try:
        settings = Settings.load(directory)
        settings.steps = 9999
        settings.width = 3000
        settings.comfy_url = "http://127.0.0.1:8188"
        settings.save()
        loaded = Settings.load(directory)
        assert loaded.steps == 150 and loaded.width == 2048
        assert loaded.comfy_url == "http://127.0.0.1:8188"
        with open(os.path.join(directory, "settings.json"), "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        again = Settings.load(directory)                          # a broken file is not fatal
        assert again.comfy_url.startswith("http://")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_slug_is_safe_for_a_file_name():
    assert slug("Medieval Knight, sword!") == "medieval-knight-sword"
    assert slug("   ") == "concept"
    assert "/" not in slug("a/b\\c")


# --------------------------------------------------------------------------
# the concept job, end to end
# --------------------------------------------------------------------------
def test_concept_job_saves_the_picture_and_its_settings():
    comfymod.POLL = 0.05
    harness = Harness()
    try:
        job = harness.job("concept", {"prompt": "wooden chest", "seed": 5, "steps": 20})
        done = harness.wait(job["id"])
        assert done["status"] == "done", done["message"]
        assert done["progress"] == 100
        asset = harness.store.assets(harness.project["id"])[0]
        assert asset["kind"] == "concept" and asset["name"].endswith("-5.png")
        with open(asset["path"], "rb") as handle:
            assert handle.read() == PNG
        assert asset["meta"]["seed"] == 5 and asset["meta"]["steps"] == 20
        assert asset["meta"]["checkpoint"] == CKPT
        # the settings travel with the picture, so it can be reproduced later
        sidecar = os.path.splitext(asset["path"])[0] + ".json"
        with open(sidecar, encoding="utf-8") as handle:
            assert json.load(handle)["prompt"] == "wooden chest"
        # and the graph we sent really carried the job's choices
        sent = harness.fake.graphs[0]
        assert sent["5"]["inputs"]["seed"] == 5 and sent["5"]["inputs"]["steps"] == 20
    finally:
        harness.close()


def test_missing_checkpoint_fails_with_a_useful_message():
    comfymod.POLL = 0.05
    harness = Harness(checkpoints=["other.safetensors"])
    try:
        job = harness.job("concept", {"prompt": "knight"})
        done = harness.wait(job["id"])
        assert done["status"] == "failed"
        assert CKPT in done["message"] and "other.safetensors" in done["message"]
        assert not harness.fake.graphs, "задание не должно уходить в движок"
        assert not harness.store.assets(), "и не должно оставлять пустых ассетов"
    finally:
        harness.close()


def test_engine_error_is_reported_not_swallowed():
    comfymod.POLL = 0.05
    harness = Harness(fail_with="CUDA out of memory")
    try:
        job = harness.job("concept", {"prompt": "knight"})
        done = harness.wait(job["id"])
        assert done["status"] == "failed" and "CUDA out of memory" in done["message"]
    finally:
        harness.close()


def test_cancel_of_a_running_job_ends_as_cancelled():
    comfymod.POLL = 0.05
    harness = Harness(complete=False)
    try:
        job = harness.job("concept", {"prompt": "slow knight"})
        assert wait_for(lambda: harness.store.job(job["id"])["status"] == "running", 5)
        assert harness.worker.cancel(job["id"]) is True
        done = harness.wait(job["id"])
        assert done["status"] == "cancelled", done["status"]
        assert harness.fake.interrupted >= 1, "движку надо послать interrupt, иначе он рисует в пустоту"
    finally:
        harness.close()


def test_cancel_of_a_queued_job_never_starts_it():
    comfymod.POLL = 0.05
    harness = Harness()
    try:
        harness.worker.stop()                                     # ничего не выполняет
        job = harness.store.create_job("concept", {"prompt": "knight"}, harness.project["id"])
        assert harness.worker.cancel(job["id"]) is True
        assert harness.store.job(job["id"])["status"] == "cancelled"
        assert not harness.fake.graphs
    finally:
        harness.close()


# --------------------------------------------------------------------------
# export to Disciples 3
# --------------------------------------------------------------------------
def test_export_job_calls_d3tool_and_lists_the_files():
    import d3studio.pipeline as pipeline

    comfymod.POLL = 0.05
    harness = Harness()
    original = pipeline.d3tool_export
    calls = []

    def fake_export(gltf_path, out_dir, **kwargs):
        calls.append((str(gltf_path), str(out_dir), kwargs))
        os.makedirs(out_dir, exist_ok=True)
        for name in ("knight.g", "knight.scene", "knight.ac"):
            with open(os.path.join(out_dir, name), "wb") as handle:
                handle.write(b"x")
        return {"returncode": 0, "output": "ok", "command": ["d3tool"]}

    try:
        asset_path = os.path.join(harness.settings.assets_dir(harness.project["id"]), "knight.glb")
        os.makedirs(os.path.dirname(asset_path), exist_ok=True)
        with open(asset_path, "wb") as handle:
            handle.write(b"glTF")
        asset = harness.store.add_asset(harness.project["id"], "model", "knight.glb", asset_path, {})
        pipeline.d3tool_export = fake_export
        job = harness.job("export.d3", {"assetId": asset["id"]})
        done = harness.wait(job["id"])
        assert done["status"] == "done", done["message"]
        assert sorted(done["result"]["files"]) == ["knight.ac", "knight.g", "knight.scene"]
        assert calls and calls[0][0].endswith("knight.glb")
        assert os.path.isdir(done["result"]["outDir"])
    finally:
        pipeline.d3tool_export = original
        harness.close()


def test_export_says_no_to_a_picture():
    comfymod.POLL = 0.05
    harness = Harness()
    try:
        path = os.path.join(harness.settings.assets_dir(harness.project["id"]), "concept.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()
        asset = harness.store.add_asset(harness.project["id"], "concept", "concept.png", path, {})
        job = harness.job("export.d3", {"assetId": asset["id"]})
        done = harness.wait(job["id"])
        assert done["status"] == "failed" and ".glb" in done["message"]
    finally:
        harness.close()


def test_d3tool_runner_builds_the_documented_command():
    import d3studio.pipeline as pipeline
    from unittest import mock

    with tempfile.TemporaryDirectory() as directory:
        gltf = os.path.join(directory, "unit.glb")
        open(gltf, "wb").close()
        finished = mock.Mock(returncode=0, stdout="written", stderr="")
        with mock.patch.object(pipeline.subprocess, "run", return_value=finished) as run:
            result = d3tool_export(gltf, os.path.join(directory, "out"), python="py-test", repo=REPO)
        command = run.call_args[0][0]
        assert command[:4] == ["py-test", "-m", "d3tool", "export"]
        assert command[4] == gltf and command[5] == "-o"
        assert run.call_args[1]["cwd"] == REPO
        assert result["output"] == "written"

        broken = mock.Mock(returncode=2, stdout="", stderr="line1\ncannot read gltf")
        with mock.patch.object(pipeline.subprocess, "run", return_value=broken):
            try:
                d3tool_export(gltf, directory, python="py-test", repo=REPO)
            except ExportError as exc:
                assert "кодом 2" in str(exc) and "cannot read gltf" in str(exc)
            else:
                raise AssertionError("ненулевой код d3tool должен быть ошибкой")


def test_export_handler_requires_an_asset():
    comfymod.POLL = 0.05
    harness = Harness()
    try:
        job = harness.job("export.d3", {})
        done = harness.wait(job["id"])
        assert done["status"] == "failed" and "ассет" in done["message"].lower()
    finally:
        harness.close()


# --------------------------------------------------------------------------
# the HTTP API the page uses
# --------------------------------------------------------------------------
def _request(url, method="GET", payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def _json_request(url, method="GET", payload=None):
    status, body, _ = _request(url, method, payload)
    return status, json.loads(body.decode("utf-8")) if body else {}


def test_http_api_drives_a_whole_concept():
    comfymod.POLL = 0.05
    harness = Harness()
    app = None
    try:
        app = create_app(harness.settings, store=harness.store, worker=harness.worker).start()
        base = app.url

        status, health = _json_request(base + "/api/health")
        assert status == 200 and health["ok"]
        assert health["comfy"]["reachable"] is True
        assert health["comfy"]["checkpoints"] == [CKPT]

        status, page, headers = _request(base + "/")
        assert status == 200 and b"d3studio" in page and "text/html" in headers["Content-Type"]

        status, project = _json_request(base + "/api/projects", "POST", {"name": "Замок"})
        assert status == 200 and project["name"] == "Замок"
        status, listing = _json_request(base + "/api/projects")
        assert len(listing["projects"]) >= 1

        status, job = _json_request(base + "/api/jobs", "POST", {
            "kind": "concept", "projectId": project["id"], "params": {"prompt": "tower", "seed": 1}})
        assert status == 200 and job["status"] == "queued"

        assert wait_for(lambda: _json_request(base + "/api/jobs/" + job["id"])[1]["status"] == "done", 20)
        _, finished = _json_request(base + "/api/jobs/" + job["id"])
        assert finished["progress"] == 100 and finished["result"]["asset"]

        _, assets = _json_request(base + "/api/projects/" + project["id"] + "/assets")
        assert len(assets["assets"]) == 1
        asset = assets["assets"][0]
        status, picture, headers = _request(base + f"/assets/{asset['id']}")
        assert status == 200 and picture == PNG and headers["Content-Type"].startswith("image/")

        # a broken request is an error message, not a crash
        status, error = _json_request(base + "/api/jobs", "POST", {"kind": "concept", "params": {}})
        assert status == 400 and "описание" in error["error"].lower()
        status, error = _json_request(base + "/api/jobs", "POST", {"kind": "nonsense"})
        assert status == 400
        status, error = _json_request(base + "/api/jobs/deadbeef")
        assert status == 404
        status, error = _json_request(base + "/api/nope")
        assert status == 404

        # uploading a GLB by hand is how a model made elsewhere gets into the studio
        status, uploaded = _json_request(base + "/api/projects/" + project["id"] + "/assets", "POST", {
            "name": "knight.glb", "data": base64.b64encode(b"glTF").decode()})
        assert status == 200 and uploaded["kind"] == "model"
        assert os.path.isfile(uploaded["path"])
        status, error = _json_request(base + "/api/projects/" + project["id"] + "/assets", "POST", {
            "name": "evil.exe", "data": base64.b64encode(b"x").decode()})
        assert status == 400
    finally:
        if app:
            app.stop()
        harness.close()


def test_http_api_keeps_the_settings_and_reads_the_exports():
    harness = Harness()
    app = None
    try:
        app = create_app(harness.settings, store=harness.store, worker=harness.worker).start()
        base = app.url
        status, saved = _json_request(base + "/api/settings", "PUT", {"steps": 12, "checkpoint": "x.safetensors"})
        assert status == 200 and saved["steps"] == 12 and saved["checkpoint"] == "x.safetensors"

        folder = os.path.join(harness.settings.exports_dir(harness.project["id"]), "knight")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "knight.g"), "wb") as handle:
            handle.write(b"g")
        status, exports = _json_request(base + "/api/projects/" + harness.project["id"] + "/exports")
        assert status == 200 and exports["exports"][0]["files"] == ["knight.g"]
        status, body, _ = _request(base + f"/exports/{harness.project['id']}/knight/knight.g")
        assert status == 200 and body == b"g"
        status, _, _ = _request(base + f"/exports/{harness.project['id']}/../../settings.json")
        assert status in (403, 404), status          # outside the export folder, either way
    finally:
        if app:
            app.stop()
        harness.close()


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------
def main() -> int:
    tests = [(name, value) for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failed = []
    started = time.time()
    for name, function in tests:
        try:
            function()
            print(f"  ок    {name}")
        except Exception as exc:            # noqa: BLE001 — a runner reports, it does not stop
            failed.append((name, exc))
            print(f"  ПРОВАЛ {name}: {exc.__class__.__name__}: {exc}")
    print(f"\n{len(tests) - len(failed)} из {len(tests)} за {time.time() - started:.1f} с")
    if failed:
        import traceback
        for name, exc in failed:
            print("\n---", name)
            traceback.print_exception(type(exc), exc, exc.__traceback__)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
