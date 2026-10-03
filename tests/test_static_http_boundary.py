"""Exercise each unchanged HTTP handler against generated, non-sensitive files."""
from __future__ import annotations

import http.client
import importlib
import mimetypes
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
from threading import Thread
from http.server import ThreadingHTTPServer
import urllib.request
from urllib.parse import quote

import pytest


HANDLERS = (
    ("modules.products.server", "Handler", "index.html"),
    ("modules.sourcing.new_product_server", "NewProductHandler", "new_product.html"),
    ("modules.ozon.rus_server", "OrbitRusHandler", "ozon.html"),
)
SENTINEL = b"S00-A generated parent sentinel; no production data"
ASSETS = {
    "nested/app.js": b"window.s00Fixture = true;\n",
    "nested/theme.css": b"body { color: #123; }\n",
    "nested/pixel.png": b"\x89PNG\r\n\x1a\nS00 fixture bytes",
}


@pytest.fixture(params=HANDLERS, ids=("products", "new_product", "rus"))
def static_http(request, tmp_path, monkeypatch):
    # Import the real modules, but fail if an unrelated dependency attempts I/O.
    import core.config as config

    blocked_calls = []

    def forbidden(*args, **kwargs):
        blocked_calls.append("settings/database/provider call")
        raise AssertionError("static GET attempted unrelated I/O")

    monkeypatch.setattr(config, "load_settings", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    module_name, handler_name, page = request.param
    module = importlib.import_module(module_name)
    repo = Path(__file__).resolve().parents[1]
    assert Path(module.__file__).resolve().is_relative_to(repo)

    web = tmp_path / "web"
    public = web / "static"
    for relative, content in ASSETS.items():
        target = public / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (web / "sentinel.txt").write_bytes(SENTINEL)
    (web / page).write_bytes(b"S00-A trusted fixed page")
    monkeypatch.setattr(module, "WEB_DIR", web)
    if hasattr(module, "STATIC_DIR"):
        monkeypatch.setattr(module, "STATIC_DIR", public)

    # Do not call serve(): it may initialize external platform executors/tokens.
    server = ThreadingHTTPServer(("127.0.0.1", 0), getattr(module, handler_name))
    server.daemon_threads = True
    original_connect = socket.socket.connect

    def only_this_fixture(sock, address):
        if address != ("127.0.0.1", server.server_port):
            return forbidden()
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", only_this_fixture)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    def get(target):
        # http.client sends dot segments verbatim; URL clients may normalize them.
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", target)
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    try:
        yield get, public, module_name
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert server.socket.fileno() == -1
        assert blocked_calls == []


def test_parent_sentinel_is_not_served(static_http):
    get, _, _ = static_http
    status, _, body = get("/static/../sentinel.txt")
    assert (status, SENTINEL in body) == (404, False)


@pytest.mark.parametrize("relative", ASSETS)
def test_nested_assets_keep_bytes_and_mime(static_http, relative):
    get, _, module_name = static_http
    status, headers, body = get("/static/" + relative + "?v=s00")
    expected_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
    if expected_type.startswith("text/") or (
        module_name.endswith("new_product_server") and Path(relative).suffix in (".js", ".css")
    ):
        expected_type += "; charset=utf-8"
    assert status == 200
    assert body == ASSETS[relative]
    assert headers["Content-Type"] == expected_type
    assert headers["Content-Length"] == str(len(body))


def test_trusted_fixed_page_outside_static_root_still_works(static_http):
    get, _, module_name = static_http
    status, _, body = get("/")
    if module_name == 'modules.products.server':
        # The legacy products root was retired after the integrated Orbit
        # workspace became the only supported UI entry point.
        assert status == 404
        return
    if module_name == 'modules.ozon.rus_server':
        assert status == 404
        return
    assert (status, body) == (200, b"S00-A trusted fixed page")


@pytest.mark.parametrize("suffix", (
    "..\\sentinel.txt", "nested/../../sentinel.txt",
    "%2e%2e/sentinel.txt", "%2E%2E%2Fsentinel.txt", "..%5csentinel.txt",
    "%2e%2e%5Csentinel.txt", "nested%2f..%2f..%2fsentinel.txt",
    "/sentinel.txt", "%2fsentinel.txt", "C:sentinel.txt", "C:/sentinel.txt",
    "C%3a%5csentinel.txt", "//s00-fixture.invalid/share/sentinel.txt",
    "%5c%5cs00-fixture.invalid%5cshare%5csentinel.txt",
    "%5c%5c?%5cC:%5csentinel.txt", "nested/app.js:stream", "..%20/sentinel.txt",
    "nested/./app.js", "nested/app.js.", "nested/app.js%00", "%ff", "%2", "%GG",
    "", "nested", "nested/", "missing.js",
))
def test_rejected_paths_share_404(static_http, suffix):
    get, _, _ = static_http
    status, _, body = get("/static/" + suffix)
    assert status == 404
    assert SENTINEL not in body


def test_absolute_fixture_path_and_prefix_sibling_are_rejected(static_http):
    get, public, _ = static_http
    sibling = public.with_name("static-sibling")
    sibling.mkdir()
    (sibling / "sentinel.txt").write_bytes(SENTINEL)
    sentinel = public.parent / "sentinel.txt"
    for suffix in (str(sentinel), sentinel.as_posix(), quote(str(sentinel), safe=""), "../static-sibling/sentinel.txt"):
        status, _, body = get("/static/" + suffix)
        assert status == 404
        assert SENTINEL not in body


def test_url_decoding_is_exactly_once(static_http):
    get, public, _ = static_http
    percent_dir = public / "%2e%2e"
    percent_dir.mkdir()
    (percent_dir / "app.js").write_bytes(b"literal percent directory")
    for suffix, expected in (
        ("nested%2Fapp.js", ASSETS["nested/app.js"]),
        ("%252e%252e/app.js", b"literal percent directory"),
    ):
        status, _, body = get("/static/" + suffix)
        assert (status, body) == (200, expected)


@pytest.mark.parametrize("link_kind", ("file_symlink", "directory_symlink", "junction"))
@pytest.mark.parametrize("outside", (False, True), ids=("internal", "escape"))
def test_resolved_links_stay_inside_root(static_http, link_kind, outside, request):
    from core.static_files import resolve_static_path

    get, public, _ = static_http
    target_dir = public.with_name("static-sibling") if outside else public / "inside"
    target_dir.mkdir()
    target_file = target_dir / "sentinel.txt"
    target_file.write_bytes(SENTINEL if outside else b"internal linked fixture")
    link = public / ("linked.txt" if link_kind == "file_symlink" else "linked")
    if link_kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junctions are not supported on this OS")
        result = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink", "/J", str(link), str(target_dir)],
            capture_output=True,
        )
        output = (result.stdout + result.stderr).decode(errors="replace")
        assert result.returncode == 0, output
        assert link.is_junction()
    else:
        try:
            link.symlink_to(target_file if link_kind == "file_symlink" else target_dir,
                            target_is_directory=link_kind == "directory_symlink")
        except OSError as error:
            if os.name == "nt" and error.winerror == 1314:
                pytest.skip("Windows symlink privilege unavailable; junctions tested separately")
            raise
        assert link.is_symlink()
    request.node.user_properties.append(("link_kind", link_kind))
    suffix = link.name if link_kind == "file_symlink" else link.name + "/sentinel.txt"
    resolved = resolve_static_path(public, suffix)
    status, _, body = get("/static/" + suffix)
    if outside:
        assert resolved is None
        assert status == 404
        assert SENTINEL not in body
    else:
        assert resolved == target_file.resolve()
        assert (status, body) == (200, b"internal linked fixture")
