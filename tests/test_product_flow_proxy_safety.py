from __future__ import annotations

import ast
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
import urllib.error
import urllib.request
from urllib.parse import urlparse

import pytest

from modules.products import server as product_server_module
from modules.products.server import Handler


ROOT = Path(__file__).resolve().parents[1]
MAX_PROXY_BODY_BYTES = 2 * 1024 * 1024


@pytest.fixture
def product_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers=headers or {},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()


class _FakeUpstreamResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
    ):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self, _limit: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _RecordingOpener:
    def __init__(self, *, response: _FakeUpstreamResponse | None = None, error=None):
        self.response = response
        self.error = error
        self.requests: list[tuple[str, str, bytes | None, int]] = []

    def open(self, request, *, timeout: int):
        self.requests.append(
            (request.full_url, request.get_method(), request.data, timeout)
        )
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _install_proxy_opener(monkeypatch, opener: _RecordingOpener) -> None:
    fake_request_module = SimpleNamespace(
        Request=urllib.request.Request,
        build_opener=lambda *_handlers: opener,
    )
    monkeypatch.setattr(
        product_server_module,
        "urllib",
        SimpleNamespace(request=fake_request_module, error=urllib.error),
    )


def _proxy_allowlists() -> tuple[set[str], set[str]]:
    source = (ROOT / "modules/products/server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_handle_product_flow_proxy":
            values: dict[str, set[str]] = {}
            for child in ast.walk(node):
                if not isinstance(child, ast.Assign) or len(child.targets) != 1:
                    continue
                target = child.targets[0]
                if isinstance(target, ast.Name) and target.id in {
                    "allowed_get",
                    "allowed_post",
                }:
                    values[target.id] = set(ast.literal_eval(child.value))
            return values["allowed_get"], values["allowed_post"]
    raise AssertionError("product-flow proxy allowlists were not found")


def test_allowed_get_preview_is_forwarded_to_treasury_with_query(
    product_server, monkeypatch
):
    upstream = _RecordingOpener(
        response=_FakeUpstreamResponse(
            json.dumps({"ok": True, "offer_id": "3828540231"}).encode("utf-8")
        )
    )
    _install_proxy_opener(monkeypatch, upstream)

    status, headers, body = _request(
        product_server + "/api/product-flow/preview?offer_id=3828540231",
        headers={"Accept": "application/json"},
    )

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body) == {"ok": True, "offer_id": "3828540231"}
    assert upstream.requests == [
        (
            "http://127.0.0.1:8766/api/new-product/preview?offer_id=3828540231",
            "GET",
            None,
            120,
        )
    ]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unknown_product_flow_action_is_rejected_without_upstream_access(
    product_server, monkeypatch, method
):
    upstream = _RecordingOpener(
        error=AssertionError("unknown actions must not reach port 8766")
    )
    _install_proxy_opener(monkeypatch, upstream)

    status, _, body = _request(
        product_server + "/api/product-flow/not-registered",
        method=method,
        body=None,
        headers={"Content-Type": "application/json"},
    )

    assert status == 404
    assert json.loads(body)["error"] == "product-flow action is not registered"
    assert upstream.requests == []


def test_product_flow_write_rejects_cross_origin_request(product_server, monkeypatch):
    upstream = _RecordingOpener(error=AssertionError("cross-origin write reached upstream"))
    _install_proxy_opener(monkeypatch, upstream)

    status, _, body = _request(
        product_server + "/api/product-flow/review",
        method="POST",
        body=b"{}",
        headers={
            "Content-Type": "application/json",
            "Origin": "https://attacker.example",
        },
    )

    assert status == 403
    assert json.loads(body)["error"] == "cross-origin product-flow write rejected"
    assert upstream.requests == []


def test_product_flow_write_requires_json_content_type(product_server, monkeypatch):
    upstream = _RecordingOpener(error=AssertionError("non-JSON write reached upstream"))
    _install_proxy_opener(monkeypatch, upstream)

    status, _, body = _request(
        product_server + "/api/product-flow/review",
        method="POST",
        body=b"offer_id=3828540231",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert status == 415
    assert json.loads(body)["error"] == "product-flow writes require application/json"
    assert upstream.requests == []


def test_unavailable_treasury_service_maps_to_503(product_server, monkeypatch):
    upstream = _RecordingOpener(
        error=urllib.error.URLError("port 8766 is unavailable")
    )
    _install_proxy_opener(monkeypatch, upstream)

    status, headers, body = _request(
        product_server + "/api/product-flow/preview?offer_id=3828540231"
    )

    assert status == 503
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body) == {
        "ok": False,
        "error": "product workflow service is unavailable",
    }


def test_product_flow_post_body_larger_than_two_megabytes_is_rejected(
    product_server, monkeypatch
):
    upstream = _RecordingOpener(
        error=AssertionError("oversized bodies must not reach port 8766")
    )
    _install_proxy_opener(monkeypatch, upstream)
    parsed = urlparse(product_server)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    connection.putrequest("POST", "/api/product-flow/review")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", str(MAX_PROXY_BODY_BYTES + 1))
    connection.endheaders()
    response = connection.getresponse()
    status = response.status
    response_body = response.read()
    connection.close()

    assert status == 413
    assert json.loads(response_body)["error"] == "request body is too large"
    assert upstream.requests == []


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_legacy_new_product_api_remains_retired(product_server, method):
    status, _, body = _request(
        product_server + "/api/new-product/preview",
        method=method,
        body=b"{}" if method == "POST" else None,
        headers={"Content-Type": "application/json"},
    )

    assert status == 410
    assert "Orbit Treasury moved" in json.loads(body)["error"]


@pytest.mark.parametrize(
    "path",
    [
        "/ai-image-studio",
        "/new-product/images",
    ],
)
def test_ai_image_studio_routes_are_served_with_strict_csp(product_server, path):
    status, headers, body = _request(product_server + path)
    csp = headers.get("Content-Security-Policy", "")

    assert status == 200
    assert "Orbit · AI 图片工作室" in body.decode("utf-8")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert headers["Cache-Control"] == "no-store"


def test_proxy_exposes_only_the_ai_studio_write_surface():
    allowed_get, allowed_post = _proxy_allowlists()

    assert allowed_get == {
        "preview",
        "content-report",
        "content-image",
        "content-package/image-localization/artifact",
    }
    assert allowed_post == {
        "review",
        "content-package/prepare",
        "content-package/vision-proposal",
        "content-package/review",
        "content-package/finalize",
        "content-package/source-only/review",
        "content-package/suite-images-preflight",
        "content-package/remaining-images-generate",
        "content-package/miaoshou-images/commit",
        "content-package/generated-image/decision",
        "content-package/image-localization/initialize",
        "content-package/image-localization/regions",
        "content-package/image-localization/clean-master",
    }
    assert not {
        action
        for action in allowed_post
        if any(
            token in action
            for token in ("publish", "claim", "site-drafts", "sku-numbering", "miaoshou-draft")
        )
    }
