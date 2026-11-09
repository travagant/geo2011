"""The two jobs the studio can run today.

``concept``     text → one SDXL image on the local generation server;
``export.d3``   glTF/GLB → Disciples 3 files through :mod:`d3tool`.

Both are plain functions ``handler(job, ctx)``: they report progress through
``ctx``, honour cancellation, and return a small dict that ends up in the job's
``result`` (so the browser can show the picture or the list of written files).
The 3D step (image → GLB) plugs in here later as a third handler without the
queue, the API or the UI having to change.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .comfy import Comfy, ComfyError, MAX_SEED, graph_params, sdxl_graph

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MODEL_SUFFIXES = {".glb", ".gltf"}


def slug(text: str, limit: int = 40) -> str:
    keep = re.sub(r"[^0-9a-zA-Zа-яА-Я]+", "-", (text or "").strip().lower()).strip("-")
    return (keep or "concept")[:limit]


def concept_params(job: dict, settings) -> dict:
    """Job parameters over the studio defaults, with the guard rails of a job."""
    params = job.get("params") or {}
    seed = params.get("seed")
    if seed in ("", None):
        seed = None
    else:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            seed = None
        if seed is not None and not 0 <= seed <= MAX_SEED:
            seed = None
    return {
        "prompt": (params.get("prompt") or "").strip(),
        "negative": (params.get("negative") or "").strip(),
        "checkpoint": (params.get("checkpoint") or settings.checkpoint).strip(),
        "width": int(params.get("width") or settings.width),
        "height": int(params.get("height") or settings.height),
        "steps": int(params.get("steps") or settings.steps),
        "cfg": float(params.get("cfg") or settings.cfg),
        "seed": seed,
    }


def run_concept(job: dict, ctx) -> dict:
    settings = ctx.settings
    values = concept_params(job, settings)
    if not values["prompt"]:
        raise ComfyError("Пустое описание: напишите, что нарисовать.")
    project_id = job.get("project_id")

    server = Comfy(settings.comfy_url, timeout=30)
    ctx.progress(2, "Проверяю сервер генерации")
    available = server.checkpoints()
    if available and values["checkpoint"] not in available:
        short = ", ".join(available[:6]) + ("…" if len(available) > 6 else "")
        raise ComfyError(f"В движке нет чекпойнта {values['checkpoint']}. Доступны: {short}")

    graph = sdxl_graph(
        values["prompt"], negative=values["negative"], checkpoint=values["checkpoint"],
        width=values["width"], height=values["height"], steps=values["steps"],
        cfg=values["cfg"], seed=values["seed"], prefix="d3studio/" + (project_id or "job"),
    )
    ctx.progress(5, "Ставлю в очередь движка")
    prompt_id = server.submit(graph)
    started = time.monotonic()

    def on_tick(elapsed: float) -> None:
        # the server does not tell step counts on the plain API, so the bar moves
        # with time and never claims to be finished before the picture is here
        share = min(90.0, 10.0 + elapsed * 2.5)
        ctx.progress(share, f"Рисую · {int(elapsed)} с")

    try:
        entry = server.wait(prompt_id, on_progress=on_tick, timeout=settings.timeout_s,
                            cancelled=lambda: ctx.cancelled)
    except ComfyError as exc:
        if "не закончилась" in str(exc):
            server.interrupt()
        raise
    images = server.images(entry)
    if not images:
        raise ComfyError("Движок закончил, но не вернул ни одной картинки.")
    ctx.progress(92, "Забираю картинку")
    data = server.download(images[0])

    directory = settings.assets_dir(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    meta = {**graph_params(graph), "comfy_url": settings.comfy_url, "prompt_id": prompt_id,
            "seconds": round(time.monotonic() - started, 1)}
    name = f"{slug(values['prompt'])}-{meta['seed']}.png"
    path = directory / name
    path.write_bytes(data)
    # the sidecar makes one picture portable: prompt, seed and checkpoint travel with it
    (directory / (path.stem + ".json")).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")

    ctx.store.add_asset(project_id, "concept", name, str(path), meta)
    ctx.progress(100, "Готово")
    return {"asset": name, "path": str(path), "seed": meta["seed"],
            "seconds": meta["seconds"], "message": "Концепт готов"}


# --------------------------------------------------------------------------
# glTF/GLB → Disciples 3, through the toolkit that already knows the formats
# --------------------------------------------------------------------------
class ExportError(RuntimeError):
    pass


def d3tool_export(gltf_path: str | Path, out_dir: str | Path, *, python: str | None = None,
                  repo: str | Path | None = None, timeout: float = 1800) -> dict:
    """Run ``python -m d3tool export <gltf> -o <dir>`` and return its output."""
    repo_path = Path(repo or Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_path) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    command = [python or sys.executable, "-m", "d3tool", "export", str(gltf_path), "-o", str(out_dir)]
    try:
        done = subprocess.run(command, cwd=str(repo_path), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise ExportError("Не найден python для d3tool: проверьте путь в настройках.") from None
    except subprocess.TimeoutExpired:
        raise ExportError(f"d3tool не уложился в {int(timeout)} с.") from None
    output = ((done.stdout or "") + "\n" + (done.stderr or "")).strip()
    if done.returncode != 0:
        tail = "\n".join(output.splitlines()[-12:]) or "без вывода"
        raise ExportError(f"d3tool export завершился с кодом {done.returncode}:\n{tail}")
    return {"returncode": done.returncode, "output": output, "command": command}


def run_export_d3(job: dict, ctx) -> dict:
    settings = ctx.settings
    params = job.get("params") or {}
    asset_id = params.get("assetId") or params.get("asset_id")
    if not asset_id:
        raise ExportError("Не указан ассет для экспорта.")
    asset = ctx.store.asset(str(asset_id))
    if not asset:
        raise ExportError("Ассет не найден — обновите страницу.")
    source = Path(asset["path"])
    if not source.is_file():
        raise ExportError(f"Файла {source.name} больше нет на диске.")
    if source.suffix.lower() not in MODEL_SUFFIXES:
        raise ExportError(f"{source.name}: экспорт в Disciples 3 умеет только .glb и .gltf.")

    ctx.progress(10, "Готовлю папку экспорта")
    out_dir = settings.exports_dir(job.get("project_id")) / Path(slug(asset.get("name") or "unit")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx.progress(25, "Экспортирую через d3tool")
    ctx.raise_if_cancelled()
    result = d3tool_export(source, out_dir, python=params.get("python") or None,
                           repo=settings.d3tool_repo)
    ctx.progress(90, "Собираю список файлов")
    written = sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file())
    if not written:
        raise ExportError("d3tool закончил, но не записал ни одного файла — пришлите вывод ниже.")
    ctx.progress(100, "Готово")
    return {"files": written, "outDir": str(out_dir),
            "message": f"Экспортировано файлов: {len(written)}",
            "output": result["output"][-4000:]}


HANDLERS = {"concept": run_concept, "export.d3": run_export_d3}
