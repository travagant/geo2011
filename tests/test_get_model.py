"""A 7 GB download has to survive what happens to 7 GB downloads.

Resume, size, sha256 and the GGUF header are the parts worth pinning: a truncated
`Q4_K_M` loads as garbage, and an architecture this Ollama does not know must be
reported here rather than in the studio an hour later.
"""

import hashlib
import http.server
import struct
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))

import get_model  # noqa: E402

PAYLOAD = bytes(range(256)) * 400          # 100 KiB, enough for several chunks


# --------------------------------------------------------------------------- #
#  a local server that honours Range — and one that can be told not to
# --------------------------------------------------------------------------- #
class RangeServer:
    def __init__(self, payload=PAYLOAD, honour_range=True, truncate_at=None):
        self.payload, self.honour_range, self.truncate_at = payload, honour_range, truncate_at
        self.ranges = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):                                    # noqa: N802
                outer = self.server.owner
                header = self.headers.get("Range", "")
                start = 0
                if header.startswith("bytes="):
                    start = int(header.split("=")[1].split("-")[0] or 0)
                outer.ranges.append(header)
                body = outer.payload[start:] if (header and outer.honour_range) else outer.payload
                if outer.truncate_at is not None:
                    body = body[:outer.truncate_at]
                self.send_response(206 if (header and outer.honour_range) else 200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.owner = self
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/model.gguf"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    made = []

    def make(**kw):
        s = RangeServer(**kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.stop()


# --------------------------------------------------------------------------- #
#  links
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("link,expected", [
    ("https://huggingface.co/DavidAU/Some-Repo/blob/main/x.gguf", ("DavidAU/Some-Repo", "x.gguf")),
    ("https://huggingface.co/DavidAU/Some-Repo/resolve/main/x.gguf", ("DavidAU/Some-Repo", "x.gguf")),
    ("https://huggingface.co/DavidAU/Some-Repo", ("DavidAU/Some-Repo", "")),
    ("DavidAU/Some-Repo", ("DavidAU/Some-Repo", "")),
])
def test_a_browser_link_is_understood(link, expected):
    assert get_model.parse_hf_link(link) == expected


def test_the_preset_points_at_the_file_that_was_asked_for():
    spec = get_model.preset("defiant")
    assert spec["file"].endswith("-MTP-Q4_K_M.gguf")
    assert spec["size"] == 6979975392
    assert spec["sha256"].startswith("d7eb4fac")
    assert spec["ollama"] == "defiant9b"


def test_a_preset_comes_back_as_a_copy():
    first = get_model.preset("defiant")
    first["file"] = "подменили"
    assert get_model.preset("defiant")["file"].endswith("Q4_K_M.gguf")


def test_an_unknown_preset_says_what_exists():
    with pytest.raises(SystemExit) as exc:
        get_model.preset("нет-такого")
    assert "defiant" in str(exc.value)


# --------------------------------------------------------------------------- #
#  the download
# --------------------------------------------------------------------------- #
def test_a_full_download_lands_with_the_right_sha(tmp_path, server):
    srv = server()
    dest = tmp_path / "m.gguf"
    result = get_model.download(srv.url, dest, size=len(PAYLOAD),
                                sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                                say=lambda *a, **k: None)
    assert result["ok"] and result["bytes"] == len(PAYLOAD)
    assert dest.read_bytes() == PAYLOAD


def test_a_half_file_is_continued_not_restarted(tmp_path, server):
    """The quantity that matters: a dead download must not start from zero."""
    srv = server()
    dest = tmp_path / "m.gguf"
    dest.write_bytes(PAYLOAD[:40000])
    result = get_model.download(srv.url, dest, size=len(PAYLOAD),
                                sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                                say=lambda *a, **k: None)
    assert result["ok"]
    assert dest.read_bytes() == PAYLOAD, "файл собран неверно"
    assert srv.ranges and srv.ranges[0].startswith("bytes=40000-"), srv.ranges


def test_a_server_without_range_restarts_but_still_lands_correctly(tmp_path, server):
    srv = server(honour_range=False)
    dest = tmp_path / "m.gguf"
    dest.write_bytes(b"junk" * 1000)
    said = []
    result = get_model.download(srv.url, dest, size=len(PAYLOAD),
                                sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                                say=lambda *a, **k: said.append("".join(map(str, a))))
    assert result["ok"]
    assert dest.read_bytes() == PAYLOAD
    assert any("не умеет докачку" in line for line in said)


def test_a_short_file_is_refused_not_registered(tmp_path, server):
    srv = server(truncate_at=1000)
    dest = tmp_path / "m.gguf"
    said = []
    result = get_model.download(srv.url, dest, size=len(PAYLOAD),
                                say=lambda *a, **k: said.append("".join(map(str, a))))
    assert not result["ok"] and result["reason"] == "размер"
    assert any("не совпал" in line for line in said)


def test_a_bad_sha_deletes_the_file(tmp_path, server):
    srv = server()
    dest = tmp_path / "m.gguf"
    said = []
    result = get_model.download(srv.url, dest, size=len(PAYLOAD),
                                sha256="00" * 32,
                                say=lambda *a, **k: said.append("".join(map(str, a))))
    assert not result["ok"] and result["reason"] == "sha256"
    assert not dest.exists(), "битый файл должен исчезнуть, иначе его примут за годный"
    assert any("удаляю" in line for line in said)


class HalfResponse:
    """Hands over a few megabytes, then dies like a real connection does."""

    status = 200
    headers = {"Content-Length": str(len(PAYLOAD))}

    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, count):
        if self.pos >= len(self.data):
            raise OSError("connection reset")
        block = self.data[self.pos:self.pos + min(count, 4000)]
        self.pos += len(block)
        return block

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_a_broken_connection_keeps_what_it_got(tmp_path, server):
    srv = server()
    dest = tmp_path / "m.gguf"
    said = []
    result = get_model.download(srv.url, dest, size=len(PAYLOAD),
                                say=lambda *a, **k: said.append("".join(map(str, a))),
                                opener=lambda url, start=0, timeout=60: HalfResponse(PAYLOAD[:9000]))
    assert not result["ok"] and result["reason"] == "обрыв"
    assert dest.exists() and dest.stat().st_size > 0, "частичный файл должен остаться"
    assert any("продолжит" in line for line in said)


def test_a_dead_connection_before_a_byte_says_so(tmp_path, server):
    srv = server()
    dest = tmp_path / "m.gguf"
    said = []

    def exploding(url, start=0, timeout=60.0):
        raise OSError("connection reset")

    result = get_model.download(srv.url, dest, size=len(PAYLOAD),
                                say=lambda *a, **k: said.append("".join(map(str, a))),
                                opener=exploding)
    assert not result["ok"] and result["reason"] == "обрыв"
    assert any("ничего не скачалось" in line for line in said)


def test_a_file_longer_than_expected_is_started_over(tmp_path, server):
    srv = server()
    dest = tmp_path / "m.gguf"
    dest.write_bytes(PAYLOAD + b"extra")
    said = []
    result = get_model.download(srv.url, dest, size=len(PAYLOAD),
                                sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                                say=lambda *a, **k: said.append("".join(map(str, a))))
    assert result["ok"] and dest.read_bytes() == PAYLOAD
    assert any("длиннее ожидаемого" in line for line in said)


# --------------------------------------------------------------------------- #
#  the GGUF header
# --------------------------------------------------------------------------- #
def make_gguf(path: Path, arch="qwen3", name="Defiant 9B"):
    def kv_string(key, value):
        out = struct.pack("<Q", len(key)) + key.encode()
        out += struct.pack("<I", 8) + struct.pack("<Q", len(value)) + value.encode()
        return out

    body = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 3)
    body += kv_string("general.architecture", arch)
    body += kv_string("general.name", name)
    body += struct.pack("<Q", len("llama.context_length")) + b"llama.context_length"
    body += struct.pack("<I", 4) + struct.pack("<I", 8192)
    path.write_bytes(body + b"\0" * 64)
    return path


def test_the_architecture_is_read_from_the_header(tmp_path):
    info = get_model.gguf_info(make_gguf(tmp_path / "m.gguf"))
    assert info["architecture"] == "qwen3"
    assert info["name"] == "Defiant 9B"
    assert info["version"] == 3
    assert info["keys"]["llama.context_length"] == 8192


def test_a_file_that_is_not_gguf_says_nothing_rather_than_lying(tmp_path):
    bogus = tmp_path / "m.gguf"
    bogus.write_bytes(b"not a gguf at all")
    info = get_model.gguf_info(bogus)
    assert info["architecture"] == "" and "version" not in info


# --------------------------------------------------------------------------- #
#  registering it in Ollama
# --------------------------------------------------------------------------- #
class FakeRun:
    def __init__(self, code=0, out=""):
        self.code, self.out, self.calls = code, out, []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))

        class Done:
            returncode, stdout, stderr = self.code, self.out, ""
        return Done()


def test_a_good_file_becomes_a_model(tmp_path, monkeypatch):
    gguf = tmp_path / "Qwen3.5-defiant.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setattr(get_model, "find_ollama", lambda: r"C:\Ollama\ollama.exe")
    monkeypatch.setattr(get_model, "start_ollama", lambda say=print: True)
    run = FakeRun()
    said = []
    assert get_model.create_model(gguf, "defiant9b", say=said.append, run=run) == 0
    assert run.calls[0][1:] == ["create", "defiant9b", "-f",
                                str(gguf.with_suffix(".Modelfile"))]
    modelfile = gguf.with_suffix(".Modelfile").read_text(encoding="utf-8")
    assert "FROM" in modelfile and "num_ctx 8192" in modelfile
    assert any("defiant9b" in line for line in said)


def test_an_architecture_ollama_cannot_run_says_so_plainly(tmp_path, monkeypatch):
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setattr(get_model, "find_ollama", lambda: "ollama")
    monkeypatch.setattr(get_model, "start_ollama", lambda say=print: True)
    run = FakeRun(code=1, out="Error: unsupported architecture: qwen3.5moe")
    said = []
    assert get_model.create_model(gguf, "x", say=said.append, run=run) == 1
    text = "\n".join(said)
    assert "не прошёл" in text and "архитектур" in text
    assert "LM Studio" in text


def test_without_ollama_the_file_is_still_worth_having(tmp_path, monkeypatch):
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setattr(get_model, "find_ollama", lambda: "")
    said = []
    assert get_model.create_model(gguf, "x", say=said.append) == 2
    assert any("Ollama не найдена" in line for line in said)


def test_the_default_destination_follows_the_studio(tmp_path):
    studio = tmp_path / "AstraForge"
    (studio / "data").mkdir(parents=True)
    assert get_model.default_target(str(studio)) == studio / "data" / "models" / "gguf"
    assert get_model.default_target(str(tmp_path / "пусто")).name == "gguf"
