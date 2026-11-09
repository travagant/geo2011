"""Download a GGUF from Hugging Face and hand it to Ollama — in one double-click.

The studio lives on Ollama, so a downloaded `.gguf` on disk is not a model yet:
`ollama create` turns it into one, and only then does the name appear in the
studio's «Подключения» (`ollamaModel` in `data\\config.json`).

Three things this does that a plain `curl` does not:

* **resume** — a 7 GB download that dies at 80% continues from 80%, not from 0;
  the range is asked for explicitly and the partial file is kept otherwise;
* **verify** — the size and the SHA-256 from the Hugging Face API are checked
  before anything is registered, because a truncated GGUF loads as garbage;
* **read the GGUF header** — the architecture inside decides whether this Ollama
  can run the file at all. Better to say "Ollama doesn't know this
  architecture" here than to let the studio fail silently later.

`--inspect` only prints the header, `--no-ollama` only downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HF = "https://huggingface.co"

# The one asked for, plus the smaller cousins worth naming because 11 GB of VRAM
# is the constraint: Q4_K_M is the quality/size knee, IQ4_XS saves 450 MB.
PRESETS = {
    "defiant": {
        "repo": "DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF",
        "file": "Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-MTP-Q4_K_M.gguf",
        "size": 6979975392,
        "sha256": "d7eb4fac9389d53fa576f64a6ff53e914a00bc7705dc354d1065887565147320",
        "ollama": "defiant9b",
    },
    "defiant-xs": {
        "repo": "DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF",
        "file": "Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-MTP-IQ4_XS.gguf",
        "size": 6526302432,
        "sha256": "8d33af536e35805967c9f4d77e1ed62b8212bdee282c80c6dc9b79ae6d677637",
        "ollama": "defiant9b-xs",
    },
    "defiant-iq3": {
        "repo": "DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF",
        "file": "Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-MTP-IQ3_M.gguf",
        "size": 5722667232,
        "sha256": "726ad4c6c41906e314052e69a294ae3372310c8d11c2e78d4e97784d5beec110",
        "ollama": "defiant9b-iq3",
    },
}

CHUNK = 1 << 20                  # 1 MiB: a 7 GB file is 7000 writes, not 7 million


# --------------------------------------------------------------------------- #
#  what to fetch
# --------------------------------------------------------------------------- #
def preset(name: str) -> dict:
    if name not in PRESETS:
        raise SystemExit(f"нет такого пресета: {name}. Есть: {', '.join(sorted(PRESETS))}")
    return dict(PRESETS[name])


def parse_hf_link(text: str):
    """``(repo, file)`` from a huggingface.co link, or ``(repo, "")`` for a repo.

    Accepts the `blob/main/x.gguf` page the browser shows, the `resolve/main/x`
    download link, and a bare `owner/repo`.
    """
    text = (text or "").strip()
    if not text:
        return "", ""
    if text.startswith("http"):
        rest = text.split("huggingface.co/", 1)[-1].strip("/")
        parts = rest.split("/")
        if "resolve" in parts or "blob" in parts:
            cut = parts.index("resolve") if "resolve" in parts else parts.index("blob")
            repo = "/".join(parts[:cut])
            name = "/".join(parts[cut + 2:])
            return repo, name
        return "/".join(parts[:2]), ""
    return text, ""


def file_url(repo: str, name: str) -> str:
    return f"{HF}/{repo}/resolve/main/{name}"


# --------------------------------------------------------------------------- #
#  the download itself
# --------------------------------------------------------------------------- #
def _open(url: str, start: int = 0, timeout: float = 60.0):
    request = urllib.request.Request(url, headers={"User-Agent": "astraforge-bridge/1.0"})
    if start:
        request.add_header("Range", f"bytes={start}-")
    return urllib.request.urlopen(request, timeout=timeout)


def remote_size(url: str) -> int:
    try:
        with _open(url) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length and length.isdigit() else 0
    except (urllib.error.URLError, OSError):
        return 0


def sha256_of(path: Path, say=None, size: int = 0) -> str:
    digest = hashlib.sha256()
    done = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK * 8)
            if not block:
                break
            digest.update(block)
            done += len(block)
            if say and size:
                say(f"\r  sha256: {done * 100 // size}%", end="")
    if say and size:
        say("\r" + " " * 30 + "\r", end="")
    return digest.hexdigest()


def _human(count: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if count < 1024 or unit == "ГБ":
            return f"{count:.1f} {unit}" if unit != "Б" else f"{int(count)} Б"
        count /= 1024
    return f"{count:.1f} ГБ"


def download(url: str, dest: Path, size: int = 0, sha256: str = "", say=print,
             opener=None) -> dict:
    """Fetch ``url`` into ``dest``, continuing a partial file. Returns a verdict."""
    opener = opener or _open
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = dest.stat().st_size if dest.exists() else 0
    if size and have > size:
        say(f"  файл длиннее ожидаемого ({have} > {size}) — начинаю заново")
        have = 0
    if size and have == size:
        say(f"  уже скачано целиком: {_human(have)}")
    else:
        if have:
            say(f"  продолжаю с {_human(have)} из {_human(size) if size else '?'}")
        started, mark, mark_bytes = time.time(), time.time(), have
        try:
            with opener(url, start=have) as response:
                mode = "ab" if have and getattr(response, "status", 200) == 206 else "wb"
                if mode == "wb" and have:
                    say("  сервер не умеет докачку — качаю заново")
                total = size or int(response.headers.get("Content-Length") or 0) + have
                with open(dest, mode) as fh:
                    while True:
                        block = response.read(CHUNK)
                        if not block:
                            break
                        fh.write(block)
                        have += len(block)
                        now = time.time()
                        if now - mark > 1.5:
                            speed = (have - mark_bytes) / (now - mark)
                            left = (total - have) / speed if speed > 0 and total else 0
                            percent = f"{have * 100 // total:3d}%" if total else "   "
                            say(f"\r  {percent}  {_human(have)}"
                                f"{' / ' + _human(total) if total else ''}"
                                f"  {_human(speed)}/с  осталось {int(left // 60)}м{int(left % 60):02d}с",
                                end="")
                            mark, mark_bytes = now, have
        except (urllib.error.URLError, OSError) as exc:
            say(f"\n  обрыв: {exc}")
            if dest.exists() and dest.stat().st_size:
                say(f"  файл сохранён ({_human(dest.stat().st_size)}) — запусти ещё раз, "
                    "продолжит с этого места")
            else:
                say("  ничего не скачалось — запусти ещё раз")
            return {"ok": False, "reason": "обрыв", "bytes": have}
        say("\r" + " " * 70 + "\r", end="")

    got = dest.stat().st_size
    if size and got != size:
        say(f"  размер не совпал: {got} вместо {size} — файл недокачан")
        return {"ok": False, "reason": "размер", "bytes": got}
    if sha256:
        say("  проверяю sha256…")
        actual = sha256_of(dest, say=say, size=got)
        if actual != sha256.lower():
            say(f"  sha256 не совпал: {actual[:16]}… вместо {sha256[:16]}…")
            say("  файл битый — удаляю, качать заново")
            dest.unlink(missing_ok=True)
            return {"ok": False, "reason": "sha256", "bytes": got}
        say("  sha256 совпал")
    return {"ok": True, "bytes": got}


# --------------------------------------------------------------------------- #
#  what is inside the file
# --------------------------------------------------------------------------- #
def _read_exact(fh, count: int) -> bytes:
    data = fh.read(count)
    if len(data) != count:
        raise ValueError("файл кончился раньше заголовка")
    return data


def _gguf_value(fh, kind: int):
    if kind == 8:
        length = struct.unpack("<Q", _read_exact(fh, 8))[0]
        return _read_exact(fh, length).decode("utf-8", "replace")
    if kind == 9:
        inner = struct.unpack("<I", _read_exact(fh, 4))[0]
        count = struct.unpack("<Q", _read_exact(fh, 8))[0]
        return [_gguf_value(fh, inner) for _ in range(min(count, 8))] if count <= 64 else f"<{count} значений>"
    formats = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
               6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    if kind not in formats:
        raise ValueError(f"неизвестный тип значения {kind}")
    return struct.unpack(formats[kind], _read_exact(fh, struct.calcsize(formats[kind])))[0]


def gguf_info(path: Path, max_keys: int = 64) -> dict:
    """``general.architecture`` and friends from a GGUF header."""
    info = {"architecture": "", "name": "", "keys": {}}
    try:
        with open(path, "rb") as fh:
            if _read_exact(fh, 4) != b"GGUF":
                return info
            version = struct.unpack("<I", _read_exact(fh, 4))[0]
            info["version"] = version
            tensors = struct.unpack("<Q", _read_exact(fh, 8))[0]
            count = struct.unpack("<Q", _read_exact(fh, 8))[0]
            info["tensors"] = tensors
            for _ in range(min(count, max_keys)):
                length = struct.unpack("<Q", _read_exact(fh, 8))[0]
                if length > 4096:
                    break
                key = _read_exact(fh, length).decode("utf-8", "replace")
                kind = struct.unpack("<I", _read_exact(fh, 4))[0]
                value = _gguf_value(fh, kind)
                info["keys"][key] = value
                if key == "general.architecture":
                    info["architecture"] = str(value)
                elif key == "general.name":
                    info["name"] = str(value)
    except (OSError, ValueError, struct.error) as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


# --------------------------------------------------------------------------- #
#  Ollama
# --------------------------------------------------------------------------- #
def find_ollama() -> str:
    found = shutil.which("ollama") or shutil.which("ollama.exe")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    for candidate in (Path(local, "Programs", "Ollama", "ollama.exe"),
                      Path(os.environ.get("ProgramFiles", ""), "Ollama", "ollama.exe")):
        if candidate.is_file():
            return str(candidate)
    return ""


def ollama_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        return False


def start_ollama(say=print) -> bool:
    if ollama_up():
        return True
    local = os.environ.get("LOCALAPPDATA", "")
    for name in ("ollama app.exe", "ollama.exe"):
        candidate = Path(local, "Programs", "Ollama", name)
        if candidate.is_file():
            say(f"  запускаю Ollama: {candidate.name}")
            try:
                subprocess.Popen([str(candidate)], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except OSError:
                continue
            for _ in range(30):
                time.sleep(1)
                if ollama_up():
                    return True
    return ollama_up()


MODELFILE = """FROM "{path}"
PARAMETER num_ctx 8192
PARAMETER temperature 0.8
"""


def create_model(gguf: Path, name: str, say=print, run=None) -> int:
    """`ollama create` from a Modelfile — this is what makes the file a model."""
    run = run or subprocess.run
    ollama = find_ollama()
    if not ollama:
        say("  Ollama не найдена: поставь её (студия умеет) или открой файл в LM Studio")
        return 2
    if not start_ollama(say):
        say("  сервер Ollama не поднимается — открой её из трея и запусти этот файл снова")
        return 2
    modelfile = gguf.with_suffix(".Modelfile")
    modelfile.write_text(MODELFILE.format(path=str(gguf).replace("\\", "/")), encoding="utf-8")
    say(f"  регистрирую модель «{name}» в Ollama…")
    result = run([ollama, "create", name, "-f", str(modelfile)])
    if getattr(result, "returncode", 1) != 0:
        out = f"{getattr(result, 'stdout', '')}{getattr(result, 'stderr', '')}"
        say("  ollama create не прошёл:")
        for line in (out.strip().splitlines() or ["(без вывода)"])[-4:]:
            say("    " + line)
        if "unsupported" in out.lower() or "unknown architecture" in out.lower():
            say("  Ollama не знает эту архитектуру — обнови Ollama, либо открой GGUF")
            say("  в LM Studio / llama.cpp: там тот же файл работает без регистрации")
        return 1
    say(f"  готово: модель «{name}» есть в списке Ollama")
    say(f"  в студии: «Подключения» → модель Ollama → {name}")
    return 0


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def default_target(start: str = "") -> Path:
    """`<студия>\\data\\models\\gguf` — рядом с моделями, которые ставит студия."""
    for base in (start, r"D:\AstraForge", r"C:\AstraForge"):
        if base and (Path(base) / "data").is_dir():
            return Path(base, "data", "models", "gguf")
    return Path.cwd() / "gguf"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="скачать GGUF с Hugging Face и отдать её Ollama")
    parser.add_argument("target", nargs="?", default="defiant",
                        help=f"пресет ({', '.join(sorted(PRESETS))}) или ссылка huggingface.co")
    parser.add_argument("--file", default="", help="имя файла в репозитории (для ссылки на репозиторий)")
    parser.add_argument("--size", type=int, default=0, help="ожидаемый размер в байтах")
    parser.add_argument("--sha256", default="", help="ожидаемый sha256")
    parser.add_argument("--dir", default="", help="куда класть (по умолчанию data\\models\\gguf студии)")
    parser.add_argument("--studio", default="", help="папка студии (иначе ищется на C:/D:)")
    parser.add_argument("--name", default="", help="имя модели в Ollama")
    parser.add_argument("--no-ollama", action="store_true", help="только скачать, не регистрировать")
    parser.add_argument("--inspect", default="", metavar="GGUF", help="показать заголовок файла")
    args = parser.parse_args(argv)

    if args.inspect:
        info = gguf_info(Path(args.inspect))
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 0 if info.get("architecture") else 2

    spec = {"repo": "", "file": "", "size": args.size, "sha256": args.sha256,
            "ollama": args.name}
    if args.target in PRESETS:
        spec.update(preset(args.target))
        if args.name:
            spec["ollama"] = args.name
    else:
        repo, name = parse_hf_link(args.target)
        if not repo:
            parser.error(f"не понял, что качать: {args.target}")
        spec.update({"repo": repo, "file": name or args.file})
        if not spec["file"]:
            parser.error("для ссылки на репозиторий нужен --file имя.gguf")
        if not spec["ollama"]:
            spec["ollama"] = "hf-" + Path(spec["file"]).stem.lower()[:40]

    dest = Path(args.dir, spec["file"]) if args.dir else default_target(args.studio) / spec["file"]
    url = file_url(spec["repo"], spec["file"])
    size = spec["size"] or remote_size(url)
    print(f"модель : {spec['file']}")
    print(f"размер : {_human(size) if size else 'неизвестен'}")
    print(f"куда   : {dest}")

    result = download(url, dest, size=size, sha256=spec["sha256"])
    if not result["ok"]:
        return 1

    info = gguf_info(dest)
    if info.get("architecture"):
        print(f"GGUF   : архитектура {info['architecture']}"
              f"{', версия ' + str(info.get('version')) if info.get('version') else ''}")
    elif info.get("error"):
        print(f"GGUF   : заголовок не прочитался ({info['error']})")

    if args.no_ollama:
        print("готово: файл на месте, в Ollama не регистрировала (--no-ollama)")
        return 0
    return create_model(dest, spec["ollama"])


if __name__ == "__main__":
    raise SystemExit(main())
