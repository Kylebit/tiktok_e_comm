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
    assert "商品审批与字段锁定" in html
    assert "我已核对 Seller SKU、成本、重量、包装、站点和规格" in html
    assert "/api/product-workspace/approve" in script
    assert 'method: "POST"' in script
    assert "expected_revision" in script
    assert "user_approved: true" in script
    assert "approvalEligible" in script
    assert "不会上传妙手、创建渠道草稿或发布商品" in html
    assert ".approval-card" in css
    assert "并行发布队列" in html
    assert "refreshAllButton" in html
    assert "localStorage.getItem(QUEUE_STORAGE_KEY)" in script
    assert "localStorage.setItem(" in script
    assert "QUEUE_REFRESH_CONCURRENCY = 4" in script
    assert "Promise.allSettled(workers)" in script
    assert 'data-action="switch"' in script
    assert 'data-action="remove"' in script
    assert "key !== currentQueueKey" in script
    assert "loadedQueueKey !== currentQueueKey" in script
    assert "history.replaceState" in script
    assert ".queue-grid { grid-template-columns: 1fr; }" in css
    assert "一键全渠道发布准备" in html
    assert "选择本次准备的平台与国家" in html
    assert 'id="publicationScopeForm"' in html
    assert 'id="publicationTargetGrid"' in html
    assert "全选 10 个目标" in html
    assert "应用选择并审查售价" in html
    assert 'params.append("target", target)' in script
    assert "renderPublicationScope" in script
    assert "publication_scope" in script
    assert "pendingPublicationTargets" in script
    assert ".publication-target-grid" in css
    assert "全部国家与店铺售价审查" in html
    assert "已选平台与国家售价" in html
    assert 'id="selectedChannelPriceGrid"' in html
    assert "pricing_review" in script
    assert "all_legacy_store_prices" in script
    assert "renderPricingReview" in script
    assert "channelPriceLine" in script
    assert "等待 TikTok 回读" in script
    assert "真实写入前必须重新回读" in script
    assert "佣金" in script
    assert "平台附加费" in script
    assert ".store-price-grid" in css
    assert ".selected-channel-price-grid" in css
    assert "妙手公共草稿" in html
    assert "TikTok 主商品回读" in html
    assert "publishAllButton" in html
    assert "并行打开内容与图片工作室" in html
    assert 'id="workbenchLink"' in html and 'target="_blank"' in html
    assert "omnichannel_preview" in script
    assert "repository_adapter_audited" in script
    assert "$(\"#publishAllButton\").disabled = true" in script
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
