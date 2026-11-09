"""The telemetry switch must speak the studio's own protocol: header, method, JSON body.

No real studio is needed: a local HTTP server answers like /api/telemetry does, and the
tests hold the script to it — a wrong header would be 403 in the studio, a wrong method
would be ignored, so both are pinned here.
"""

import json
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "update"))

import telemetry_off  # noqa: E402


class StudioServer:
    """Answers GET/PUT /api/telemetry the way server/telemetry.mjs does."""

    def __init__(self, errors=True, learning=True, pending=4096, reject_bad_header=True):
        self.state = {"installId": "af-123", "errors": errors, "learning": learning,
                      "pendingBytes": pending, "lastSyncAt": None, "totals": {}}
        self.reject_bad_header = reject_bad_header
        self.requests = []          # (method, path, headers, body)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _record(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8") if length else ""
                outer.requests.append((method, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                return body

            def _reply(self, code, payload):
                data = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._record("GET")
                if self.path != "/api/telemetry":
                    return self._reply(404, {"error": "нет"})
                self._reply(200, outer.state)

            def do_PUT(self):
                body = self._record("PUT")
                if outer.reject_bad_header and self.headers.get("x-astra-client") != "studio":
                    return self._reply(403, {"error": "Запрос должен быть отправлен из студии."})
                try:
                    payload = json.loads(body or "{}")
                except ValueError:
                    return self._reply(400, {"error": "bad json"})
                for key in ("errors", "learning"):
                    if isinstance(payload.get(key), bool):
                        outer.state[key] = payload[key]
                self._reply(200, outer.state)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------- #
#  the call itself
# --------------------------------------------------------------------------- #
def test_state_reads_the_studio_answer():
    with StudioServer(errors=True, learning=True) as studio:
        st = telemetry_off.state(studio.port)
    assert st["errors"] is True and st["learning"] is True
    assert st["installId"] == "af-123"


def test_switch_sends_put_with_the_studio_header():
    with StudioServer() as studio:
        st = telemetry_off.set_consent(studio.port, errors=False, learning=False)
    method, path, headers, body = studio.requests[-1]
    assert method == "PUT" and path == "/api/telemetry"
    assert headers.get("x-astra-client") == "studio"
    assert json.loads(body) == {"errors": False, "learning": False}
    assert st["errors"] is False and st["learning"] is False


def test_switch_does_not_touch_the_install_id():
    with StudioServer() as studio:
        telemetry_off.set_consent(studio.port)
    assert studio.state["installId"] == "af-123"


def test_studio_that_rejects_a_foreign_request_is_reported():
    """The studio answers 403 without the header: the script must show that, not crash."""
    with StudioServer(reject_bad_header=True) as studio:
        opener = urllib.request.urlopen
        # pretend another client: drop the header on purpose
        def bare(request, timeout=None):
            request.headers = {"Content-Type": "application/json"}
            return opener(request, timeout=timeout)

        with pytest.raises(urllib.error.HTTPError) as caught:
            telemetry_off.set_consent(studio.port, opener=bare)
    assert caught.value.code == 403


# --------------------------------------------------------------------------- #
#  command line
# --------------------------------------------------------------------------- #
def test_check_reads_and_changes_nothing(capsys):
    with StudioServer(errors=True, learning=True) as studio:
        assert telemetry_off.main(["--port", str(studio.port), "--check"]) == 0
        assert [m for m, *_ in studio.requests] == ["GET"]
    out = capsys.readouterr().out
    assert "сейчас" in out and "вкл" in out


def test_main_switches_both_off(capsys):
    with StudioServer(errors=True, learning=True) as studio:
        assert telemetry_off.main(["--port", str(studio.port)]) == 0
        assert studio.state["errors"] is False and studio.state["learning"] is False
    out = capsys.readouterr().out
    assert "больше не отправляет" in out


def test_second_run_is_a_no_op(capsys, monkeypatch):
    with StudioServer(errors=False, learning=False) as studio:
        assert telemetry_off.main(["--port", str(studio.port)]) == 0
        assert [m for m, *_ in studio.requests] == ["GET"]
    assert "уже выключено" in capsys.readouterr().out


def test_on_switches_them_back():
    with StudioServer(errors=False, learning=False) as studio:
        assert telemetry_off.main(["--port", str(studio.port), "--on"]) == 0
        assert studio.state["errors"] is True and studio.state["learning"] is True


def test_no_studio_says_so_and_returns_2(capsys):
    assert telemetry_off.main(["--port", "1"]) == 2
    out = capsys.readouterr().out
    assert "не ответила" in out


def test_endpoint_matches_the_studio():
    assert telemetry_off.endpoint(3210) == "http://127.0.0.1:3210/api/telemetry"
    assert telemetry_off.endpoint(4000) == "http://127.0.0.1:4000/api/telemetry"


def test_describe_shows_pending_bytes():
    line = telemetry_off.describe({"errors": False, "learning": False, "pendingBytes": 2048})
    assert "выкл, журнал обучения: выкл" in line
    assert "2.0 КБ" in line
