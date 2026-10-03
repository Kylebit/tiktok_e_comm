"""Real loopback regression for maintenance-mode rejected POST bodies."""

from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from threading import Event, Thread
import time

import pytest

from shared_platform.operations_launch import maintenance_handler


@contextmanager
def _maintenance_server(*, allow_local_posts=True):
    delegated: list[str] = []
    responses: list[int] = []

    class Base(BaseHTTPRequestHandler):
        def _json(self, status, body):
            responses.append(status)
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            delegated.append(self.path)
            self._json(200, {"ok": True})

        def do_GET(self):
            self._json(200, {"ok": True, "read_only": True})

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), maintenance_handler(Base, allow_local_posts=allow_local_posts)
    )
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, delegated, responses
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

def test_rejected_megabyte_post_has_stable_409_without_delegation():
    statuses: list[int] = []
    errors: list[str] = []
    with _maintenance_server() as (server, delegated, _responses):
        for _ in range(20):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request(
                    "POST", "/api/product-workspace/round1-category/options",
                    b"x" * 1_048_576, {"Content-Type": "application/octet-stream"},
                )
                response = connection.getresponse()
                statuses.append(response.status)
                assert json.loads(response.read())["code"] == "WEB_ONLY_EXECUTION_PAUSED"
            except OSError as error:
                errors.append(f"{type(error).__name__}: {error}")
            finally:
                connection.close()
        assert delegated == []
    assert errors == [], f"statuses={statuses}, errors={errors}"
    assert statuses == [409] * 20


@pytest.mark.parametrize("headers,expected", [
    ([], 411),
    ([("Content-Length", "invalid")], 400),
    ([("Content-Length", "-1")], 400),
    ([("Content-Length", "1,2")], 400),
    ([("Content-Length", "2"), ("Content-Length", "2")], 400),
    ([("Content-Length", "1048577")], 413),
    ([("Content-Length", "9" * 5000)], 413),
    ([("Transfer-Encoding", "chunked")], 400),
])
def test_rejected_post_rejects_unbounded_or_ambiguous_body_framing(headers, expected):
    with _maintenance_server() as (server, delegated, _responses):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.putrequest("POST", "/api/workbench/tasks")
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            assert response.status == expected
            assert json.loads(response.read())["ok"] is False
        finally:
            connection.close()
        assert delegated == []


def test_rejected_post_incomplete_body_is_time_bounded_and_never_delegated():
    with _maintenance_server() as (server, delegated, responses):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        started = time.monotonic()
        try:
            connection.putrequest("POST", "/api/product-workspace/publish")
            connection.putheader("Content-Length", "65536")
            connection.endheaders()
            connection.send(b"x")
            try:
                response = connection.getresponse()
                assert response.status == 408
                response.read()
            except OSError:
                # Closing a deliberately incomplete upload may itself reset
                # the client; the server's bounded rejection is the contract.
                pass
        finally:
            connection.close()
        assert time.monotonic() - started < 3
        assert 408 in responses
        assert delegated == []


def test_rejected_post_slow_trickle_obeys_absolute_body_deadline():
    with _maintenance_server() as (server, delegated, responses):
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=3) as client:
            client.sendall(
                b"POST /api/product-workspace/publish HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: 65536\r\n\r\n"
            )
            stopped = Event()

            def drip_body():
                while not stopped.is_set():
                    try:
                        client.sendall(b"x")
                    except OSError:
                        break
                    stopped.wait(0.1)

            sender = Thread(target=drip_body, daemon=True)
            started = time.monotonic()
            sender.start()
            try:
                while time.monotonic() - started < 2.8 and 408 not in responses:
                    time.sleep(0.01)
                assert 408 in responses, "one-byte trickle must not extend the two-second deadline"
                assert delegated == []
            finally:
                stopped.set()
                try:
                    client.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                sender.join(timeout=1)


@pytest.mark.parametrize("headers,body,expected", [
    ([], b"x" * 1_048_576, 411),
    ([("Content-Length", "1048577")], b"x" * 1_048_577, 413),
], ids=["missing-length", "oversized"])
def test_unframed_or_oversized_upload_never_waits_for_or_delegates_body(headers, body, expected):
    with _maintenance_server() as (server, delegated, responses):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        started = time.monotonic()
        try:
            connection.putrequest("POST", "/api/product-workspace/publish")
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders()
            try:
                connection.send(body)
                response = connection.getresponse()
                assert response.status == expected
                response.read()
            except OSError:
                # Immediate fail-closed rejection may reset an upload that
                # exceeded (or omitted) the declared framing boundary.
                pass
        finally:
            connection.close()
        assert time.monotonic() - started < 3
        assert expected in responses
        assert delegated == []


def test_zero_length_rejection_and_get_leave_allowed_routes_unchanged():
    with _maintenance_server() as (server, delegated, _responses):
        for method, path, body, expected in (
            ("POST", "/api/workbench/tasks", b"", 409),
            ("GET", "/unrelated", None, 200),
            ("POST", "/api/catalog/cost", b"{}", 200),
        ):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request(method, path, body)
                response = connection.getresponse()
                assert response.status == expected
                response.read()
            finally:
                connection.close()
        assert delegated == ["/api/catalog/cost"]


def test_identity_only_mode_drains_then_blocks_normally_allowed_local_post():
    with _maintenance_server(allow_local_posts=False) as (server, delegated, _responses):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request("POST", "/api/catalog/cost", b"x" * 1_048_576)
            response = connection.getresponse()
            assert response.status == 409
            assert json.loads(response.read())["code"] == "WEB_ONLY_EXECUTION_PAUSED"
        finally:
            connection.close()
        assert delegated == []
