"""Real loopback regressions; no stores, providers, or production service.

The pre-fix fallback is the exact stdlib constructor used by operations_launch.
It exercises the old unbounded behavior rather than failing only an import.
"""
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time

try:
    from shared_platform.bounded_web_server import BoundedThreadingHTTPServer
except ModuleNotFoundError as error:
    if error.name != "shared_platform.bounded_web_server":
        raise
    BoundedThreadingHTTPServer = ThreadingHTTPServer


@contextmanager
def _server(*, limit=1, idle_timeout=0.2):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if self.path == "/hold":
                entered.set()
                release.wait(2)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    options = {} if BoundedThreadingHTTPServer is ThreadingHTTPServer else {
        "max_active_connections": limit, "connection_timeout": idle_timeout,
    }
    server = BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler, **options)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server, entered, release, calls
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


def _get(server, path="/health"):
    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=1)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def _busy(response):
    status, headers, body = response
    assert status == 503
    assert headers["Retry-After"] == "1"
    assert headers["Connection"].lower() == "close"
    payload = json.loads(body)
    assert payload["code"] == "SERVICE_BUSY"
    assert "稍后" in payload["error"]


def test_capacity_saturation_returns_terminal_503_and_recovers():
    with _server() as (server, entered, release, calls):
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            conn.request("GET", "/hold")
            assert entered.wait(1)
            start = time.monotonic()
            _busy(_get(server))
            assert time.monotonic() - start < 0.75
            assert calls == ["/hold"]
            release.set()
            assert conn.getresponse().read() == b'{"ok":true}'
        finally:
            release.set()
            conn.close()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            response = _get(server)
            if response[0] == 200:
                break
        assert response[0] == 200


def test_thread_start_resource_failure_returns_503_without_losing_capacity(monkeypatch):
    with _server() as (server, _, _, calls):
        original = threading.Thread.start
        failures = []

        def fail_one_request(thread):
            target = getattr(thread, "_target", None)
            if getattr(target, "__self__", None) is server and not failures:
                failures.append("cannot start request thread")
                raise RuntimeError("can't start new thread")
            return original(thread)

        monkeypatch.setattr(threading.Thread, "start", fail_one_request)
        response = None
        error = None
        try:
            response = _get(server)
        except Exception as exc:
            error = repr(exc)
        assert failures == ["cannot start request thread"]
        assert error is None, error
        _busy(response)
        assert calls == []
        assert _get(server)[0] == 200
        assert calls == ["/health"]


def test_idle_preconnection_has_finite_timeout_and_releases_slot():
    with _server(idle_timeout=0.15) as (server, _, _, calls):
        idle = socket.create_connection(("127.0.0.1", server.server_port), timeout=1)
        try:
            time.sleep(0.05)
            _busy(_get(server))
            time.sleep(0.25)
            assert _get(server)[0] == 200
            assert calls == ["/health"]
        finally:
            idle.close()


def test_normal_request_semantics_survive_sequential_connection_reuse():
    with _server(limit=2) as (server, _, _, calls):
        for _ in range(12):
            status, _, body = _get(server)
            assert status == 200
            assert json.loads(body) == {"ok": True}
        assert calls == ["/health"] * 12


def test_request_thread_allocation_memory_error_returns_503_and_recovers(monkeypatch):
    with _server() as (server, _, _, calls):
        original = threading.Thread.__init__
        failures = []

        def fail_one_allocation(thread, *args, **kwargs):
            target = kwargs.get("target")
            if getattr(target, "__self__", None) is server and not failures:
                failures.append("request-thread allocation")
                raise MemoryError("request thread allocation exhausted")
            return original(thread, *args, **kwargs)

        monkeypatch.setattr(threading.Thread, "__init__", fail_one_allocation)
        first = None
        first_error = None
        try:
            first = _get(server)
        except Exception as exc:
            first_error = repr(exc)
        recovered = _get(server)
        assert failures == ["request-thread allocation"]
        assert recovered[0] == 200, "MemoryError must return the acquired connection slot"
        assert first_error is None, first_error
        _busy(first)
        assert calls == ["/health"]
