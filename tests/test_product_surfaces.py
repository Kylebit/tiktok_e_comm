from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import urllib.error
import urllib.request

import pytest

from modules.products.server import Handler
from shared_platform import release_control
from shared_platform.registry import owner_for_http_path


ROOT = Path(__file__).resolve().parents[1]


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


def _get(url: str) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()


def test_final_product_routes_are_separate_from_internal_release_lab(product_server):
    for path, marker in (
        ("/new-product", "商品发布中心"),
        ("/profit", "利润"),
        ("/internal/release", "Release Lab"),
    ):
        status, _, body = _get(product_server + path)
        assert status == 200
        assert marker in body.decode("utf-8")

    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
    with opener.open(product_server + "/release", timeout=10) as response:
        assert response.geturl().endswith("/new-product")


def test_product_workspace_api_is_a_product_named_view_of_read_only_evidence(
    product_server, monkeypatch
):
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **kwargs: {
            "ok": True,
            "mode": "rehearsal",
            "safety": {"external_writes_performed": []},
            "received": kwargs,
        },
    )

    status, _, body = _get(
        product_server
        + "/api/product-workspace/dashboard?offer_id=3828811808&seller_sku=0946"
    )
    payload = json.loads(body)

    assert status == 200
    assert payload["schema_version"] == "product-workspace-v1"
    assert payload["workspace_mode"] == "pre_release"
    assert payload["safety"]["external_writes_performed"] == []
    assert payload["received"]["seller_sku"] == "0946"


def test_profit_center_weekly_api_reuses_governed_week_contract(
    product_server, monkeypatch
):
    monkeypatch.setattr(
        release_control,
        "build_weekly_profit_rehearsal",
        lambda **kwargs: {
            "ok": True,
            "persisted": False,
            "notifications_sent": False,
            "period_start": kwargs["period_start"].isoformat(),
        },
    )

    status, _, body = _get(
        product_server
        + "/api/profit-center/weekly?start=2026-07-13&end=2026-07-19"
    )
    payload = json.loads(body)

    assert status == 200
    assert payload["persisted"] is False
    assert payload["notifications_sent"] is False


def test_product_routes_have_domain_ownership():
    assert owner_for_http_path("/new-product") == "product_operations"
    assert owner_for_http_path("/api/product-workspace/dashboard") == "product_operations"
    assert owner_for_http_path("/profit") == "data_operations"
    assert owner_for_http_path("/api/profit-center/weekly") == "data_operations"
    assert owner_for_http_path("/internal/release") == "shared_platform"


def test_product_workspace_is_the_user_surface_and_fails_without_stale_results():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(encoding="utf-8")
    css = (ROOT / "web/static/product_workspace.css").read_text(encoding="utf-8")

    assert "商品发布中心" in html
    assert "五阶段发布进度" in html
    assert "最终商品图片" in html
    assert "/api/product-workspace/dashboard" in script
    assert "renderFailure(message)" in script
    assert "页面不会沿用上一次商品结果" in script
    assert 'method: "POST"' not in script
    assert "@media (max-width:" in css


def test_profit_center_keeps_realized_and_estimate_semantics_separate():
    html = (ROOT / "web/profit_center.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/profit_center.js").read_text(encoding="utf-8")
    css = (ROOT / "web/static/profit_center.css").read_text(encoding="utf-8")

    assert "周度经营结论" in html
    assert "SKU 利润查询" in html
    assert "REALIZED" in html and "ESTIMATE" in html
    assert "/api/profit-center/weekly" in script
    assert "decision_usable" in script
    assert "ad_rate_percent" in script
    assert "不展示不完整利润" in script
    assert 'method: "POST"' not in script
    assert "@media (max-width:" in css
