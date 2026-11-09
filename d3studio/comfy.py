"""Talking to a ComfyUI-compatible server, and the graph we ask it to run.

The contract is ComfyUI's public HTTP API — ``POST /prompt`` with a graph in the
"API format", ``GET /history/<id>``, ``GET /view`` — the same one a plain
ComfyUI on :8188 offers and the one the local engine on :8192 mirrors.  Our graph
is written here from that contract, not taken from anybody's studio: a standard
SDXL text-to-image chain (checkpoint → two text encoders → empty latent → sampler
→ decode → save).

No third-party HTTP client: :mod:`urllib.request` is enough and keeps the studio
installable with nothing but Python.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .config import DEFAULT_CHECKPOINT

MAX_SEED = 2 ** 31 - 1
#: how often a waiting job asks the server for the history; tests turn it down
POLL = 1.0


class ComfyError(RuntimeError):
    """Anything the generation server refused or never finished."""


def _round8(value: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    number = max(64, min(2048, number))
    return number - number % 8


def sdxl_graph(prompt: str, *, negative: str = "", checkpoint: str = DEFAULT_CHECKPOINT,
               width: int = 1024, height: int = 1024, steps: int = 30, cfg: float = 7.0,
               seed: int | None = None, sampler: str = "dpmpp_2m", scheduler: str = "karras",
               prefix: str = "d3studio", batch: int = 1) -> dict:
    """A ComfyUI API-format graph: SDXL text → image.

    Sizes are snapped to multiples of 8 (SDXL's VAE wants that and a hand-typed
    1023 would otherwise fail inside the sampler), the seed is frozen into the
    graph so a finished image can be reproduced from its job record.
    """
    text = (prompt or "").strip()
    if not text:
        raise ComfyError("Пустое описание: напишите, что нарисовать.")
    if seed is None:
        seed = random.randint(0, MAX_SEED)
    width, height = _round8(width, 1024), _round8(height, 1024)
    steps = max(1, min(150, int(steps or 30)))
    cfg = max(0.0, min(30.0, float(cfg if cfg is not None else 7.0)))
    batch = max(1, min(4, int(batch or 1)))
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative or "", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": batch}},
        "5": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": steps, "cfg": cfg,
            "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["6", 0]}},
    }


def graph_params(graph: dict) -> dict:
    """The settings a finished image can be reproduced from (what the job record keeps)."""
    sampler = graph.get("5", {}).get("inputs", {})
    latent = graph.get("4", {}).get("inputs", {})
    return {
        "checkpoint": graph.get("1", {}).get("inputs", {}).get("ckpt_name"),
        "prompt": graph.get("2", {}).get("inputs", {}).get("text"),
        "negative": graph.get("3", {}).get("inputs", {}).get("text"),
        "width": latent.get("width"), "height": latent.get("height"),
        "steps": sampler.get("steps"), "cfg": sampler.get("cfg"), "seed": sampler.get("seed"),
        "sampler": sampler.get("sampler_name"), "scheduler": sampler.get("scheduler"),
    }


class Comfy:
    """Minimal client: submit a graph, wait for it, take the images."""

    def __init__(self, url: str, *, timeout: float = 30.0, opener=None):
        self.url = (url or "").rstrip("/")
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex
        self._open = opener or urllib.request.urlopen

    # ---- raw calls ---------------------------------------------------------
    def _request(self, path: str, payload: dict | None = None, *, raw: bool = False, timeout: float | None = None):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=data, headers=headers,
                                         method="POST" if payload is not None else "GET")
        try:
            with self._open(request, timeout=timeout or self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise ComfyError(_server_error(exc.code, detail, path)) from None
        except (urllib.error.URLError, OSError) as exc:
            raise ComfyError(f"Сервер генерации {self.url} не отвечает ({exc}). "
                             "Запустите его и проверьте адрес в настройках.") from None
        if raw:
            return body
        try:
            return json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            raise ComfyError(f"Сервер генерации вернул не JSON на {path}.") from None

    def object_info(self, node: str | None = None) -> dict:
        return self._request("/object_info" + (f"/{node}" if node else ""))

    def checkpoints(self) -> list[str]:
        """Names the server can load; an empty list means "it does not tell"."""
        info = self.object_info("CheckpointLoaderSimple")
        try:
            names = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        except (KeyError, IndexError, TypeError):
            return []
        return [str(n) for n in names] if isinstance(names, list) else []

    def submit(self, graph: dict) -> str:
        response = self._request("/prompt", {"prompt": graph, "client_id": self.client_id})
        prompt_id = response.get("prompt_id")
        if not prompt_id:
            raise ComfyError("Сервер генерации не принял задание: " + json.dumps(response, ensure_ascii=False)[:600])
        return str(prompt_id)

    def history(self, prompt_id: str) -> dict | None:
        doc = self._request(f"/history/{urllib.parse.quote(prompt_id)}")
        return doc.get(prompt_id) or None

    def images(self, entry: dict) -> list[dict]:
        found = []
        for output in (entry.get("outputs") or {}).values():
            for image in output.get("images") or []:
                found.append(image)
        return found

    def download(self, image: dict, *, timeout: float = 120.0) -> bytes:
        query = urllib.parse.urlencode({
            "filename": image.get("filename", ""),
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        })
        return self._request(f"/view?{query}", raw=True, timeout=timeout)

    def wait(self, prompt_id: str, *, on_progress=None, poll: float | None = None,
             timeout: float = 1800.0, cancelled=None) -> dict:
        """Poll the history until the prompt finishes; returns its history entry."""
        poll = POLL if poll is None else poll
        deadline = time.monotonic() + max(5.0, float(timeout))
        started = time.monotonic()
        while True:
            if cancelled is not None and cancelled():
                self.interrupt()
                raise Cancelled("Отменено")
            entry = self.history(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ComfyError(_execution_error(status) or "Сервер генерации не смог выполнить задание.")
                if status.get("completed") or (entry.get("outputs") and status.get("status_str") in (None, "success")):
                    return entry
            if on_progress is not None:
                on_progress(time.monotonic() - started)
            if time.monotonic() > deadline:
                raise ComfyError(f"Генерация не закончилась за {int(timeout)} с.")
            time.sleep(max(0.05, float(poll)))

    def interrupt(self) -> None:
        try:
            self._request("/interrupt", {})
        except ComfyError:
            pass

    def free(self, unload: bool = True) -> None:
        try:
            self._request("/free", {"unload_models": bool(unload), "free_memory": True})
        except ComfyError:
            pass

    def reachable(self) -> bool:
        try:
            self.object_info("CheckpointLoaderSimple")
            return True
        except ComfyError:
            return False


class Cancelled(Exception):
    """The user pressed "cancel" while the job was waiting."""


def _execution_error(status: dict) -> str:
    for message in status.get("messages") or []:
        if isinstance(message, (list, tuple)) and message and message[0] == "execution_error":
            detail = message[1] or {}
            text = detail.get("exception_message") or ""
            node = detail.get("node_type") or ""
            return (f"{node}: {text}" if node else text).strip()[:600]
    return ""


def _server_error(code: int, detail: str, path: str) -> str:
    """A 400 from ComfyUI carries node_errors — show them, they name the missing file."""
    text = detail
    try:
        doc = json.loads(detail)
        parts = [doc.get("error", "")] if isinstance(doc.get("error"), str) else []
        for node, info in (doc.get("node_errors") or {}).items():
            for error in info.get("errors") or []:
                parts.append(f"{node}: {error.get('message', '')} {error.get('details', '')}".strip())
        text = "; ".join(p for p in parts if p) or detail
    except ValueError:
        pass
    return f"Сервер генерации ответил {code} на {path}: {text[:600]}"
