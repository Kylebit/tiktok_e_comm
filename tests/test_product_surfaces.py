from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import urllib.error
import urllib.request

import pytest

from modules.products.server import Handler
from modules.products import server as product_server_module
from shared_platform import release_control
from shared_platform.registry import owner_for_http_path


ROOT = Path(__file__).resolve().parents[1]


def test_product_workspace_exposes_revision_bound_first_review_image_plan(
    tmp_path, monkeypatch
):
    from modules.sourcing import new_product_workbench

    report = (
        tmp_path
        / "reports"
        / "product-preparation"
        / "offer-1"
        / "first-review.json"
    )
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                "offer_id": "offer-1",
                "product_center_revision": 7,
                "image_execution_plan": {
                    "schema_version": "first-review-image-plan/v1",
                    "status": "PROPOSED",
                    "source_actions": [
                        {
                            "position": 6,
                            "action": "TRANSLATE",
                            "target_languages": ["th-TH"],
                            "output_count": 1,
                        },
                        {
                            "position": 7,
                            "action": "TRANSLATE",
                            "target_languages": ["en-master", "th-TH"],
                            "output_count": 2,
                        }
                    ],
                    "generated_assets": [],
                    "summary": {
                        "translation_positions": [6, 7],
                        "localized_output_count": 3,
                        "net_new_output_count": 0,
                        "paid_generation_required": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(product_server_module, "ROOT", tmp_path)
    monkeypatch.setattr(
        new_product_workbench,
        "load_state",
        lambda _offer_id: {
            "review": {
                "image_actions": [
                    {"action": "keep"} for _ in range(6)
                ] + [{"action": "remove"}]
            }
        },
    )

    current = product_server_module._first_review_image_plan_view(
        {"product": {"offer_id": "offer-1", "revision": 7}}
    )
    stale = product_server_module._first_review_image_plan_view(
        {"product": {"offer_id": "offer-1", "revision": 8}}
    )

    assert current["status"] == "PROPOSED"
    assert current["summary"]["translation_positions"] == [6]
    assert current["summary"]["localized_output_count"] == 1
    assert current["source_actions"][1]["action"] == "REMOVE"
    assert current["source_actions"][1]["target_languages"] == []
    assert current["source_actions"][1]["output_count"] == 0
    assert stale["status"] == "STALE"


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
        + "/api/product-workspace/dashboard?offer_id=3828811808"
    )
    payload = json.loads(body)

    assert status == 200
    assert payload["schema_version"] == "product-workspace-v1"
    assert payload["workspace_mode"] == "formal_v1"
    assert payload["safety"]["external_writes_performed"] == []
    assert "seller_sku" not in payload["received"]


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
    assert 'aria-labelledby="journeyTitle" class="operator-clutter"' in html
    assert "七阶段正式发布进度" not in html
    assert "最终图片结果" in html
    assert "翻译与生成计划" in html
    assert 'id="firstReviewImagePlan"' in html
    assert "renderFirstReviewImagePlan" in script
    assert "first_review_image_plan" in script
    assert "/api/product-workspace/dashboard" in script
    assert "renderFailure(message)" in script
    assert "页面不会沿用上一次商品结果" in script
    assert 'id="approval" class="approval-section operator-clutter"' in html
    assert 'id="releasePlan" class="release-plan-section operator-clutter"' in html
    assert 'id="approvalCheckbox"' not in html
    assert "/api/product-workspace/approve" in script
    assert 'method: "POST"' in script
    assert "expected_revision" in script
    assert "user_approved: true" in script
    assert "approvalEligible" in script
    assert "不会上传妙手、创建渠道草稿或发布商品" in html
    assert ".approval-card" in css
    assert "并行发布队列" in html
    assert 'name="seller_sku"' not in html
    assert "系统读取后自动分配" in html
    assert "你只需输入 Offer ID" in html
    assert "validOfferId" in script
    assert 'url.searchParams.delete("seller_sku")' in script
    assert "请检查 Offer ID、Seller SKU" not in script
    assert "automatic-sku" in css
    assert "refreshAllButton" in html
    assert "localStorage.getItem(QUEUE_STORAGE_KEY)" in script
    assert "localStorage.setItem(" in script
    assert "QUEUE_REFRESH_CONCURRENCY = 4" in script
    assert "Promise.allSettled(workers)" in script
    assert "hydrateUnloadedQueueProducts" in script


def test_product_center_embeds_only_the_actionable_image_review_surface():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(encoding="utf-8")
    css = (ROOT / "web/static/product_workspace.css").read_text(encoding="utf-8")

    assert 'id="embeddedImageReview"' in html
    assert 'id="embeddedSourceImageGrid"' in html
    assert 'id="saveEmbeddedImageReviewButton"' in html
    assert "来源图片选择" in html
    assert "保存图片选择" in html
    assert "loadEmbeddedImageReview" in script
    assert "saveEmbeddedImageReview" in script
    assert "/api/product-flow/preview" in script
    assert "/api/product-flow/content-package/review" in script
    assert "embeddedImageReviewDirty" in script
    assert "removed source images never enter translation or generation" in script
    assert "renderFirstReviewImagePlan(currentFirstReviewImagePlan)" in script
    assert script.count("renderFirstReviewImagePlan(currentFirstReviewImagePlan)") >= 3
    assert ".operator-clutter" in css
    assert "display: none" in css
    assert "product?.thumbnail" in script
    assert "data-queue-image" in script
    assert "/api/proxy-image?url=" in script
    assert ".queue-thumbnail" in css
    assert 'data-action="switch"' in script
    assert 'data-action="remove"' in script
    assert "key !== currentQueueKey" in script
    assert "loadedQueueKey !== currentQueueKey" in script
    assert "history.replaceState" in script
    assert ".queue-grid { grid-template-columns: 1fr; }" in css
    assert "店铺与售价" in html
    assert "选择本次准备的平台与国家" in html
    assert 'id="publicationScopeForm"' in html
    assert 'id="publicationTargetGrid"' in html
    assert "全选 16 个目标" in html
    assert "应用选择并审查售价" in html
    assert 'params.append("target", target)' in script
    assert "renderPublicationScope" in script
    assert "publication_scope" in script
    assert "pendingPublicationTargets" in script
    assert ".publication-target-grid" in css
    assert "已选店铺售价" in html
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
    assert "releasePlanApprovalForm" in html
    assert "prepareMiaoshouButton" in html
    assert "/api/product-workspace/release-plan/approve" in script
    assert "/api/product-workspace/miaoshou-draft/commit" in script
    assert '"/api/product-workspace/publish"' not in script
    assert '"/api/product-workspace/publish-tiktok"' in script
    assert '"/api/product-workspace/publish-shopee-global"' in script
    assert '"/api/product-workspace/publish-ozon"' in script
    assert "并行打开内容与图片工作室" in html
    assert 'id="workbenchLink"' in html and 'target="_blank"' in html
    assert "omnichannel_preview" in script
    assert "repository_adapter_audited" in script
    assert "updateReleaseControls" in script
    assert "@media (max-width:" in css
    assert "中文事实 → 平台标题候选" in html
    assert "/api/product-workspace/title-draft" in script
    assert "AI 生成平台标题" in html
    assert "不会修改商品事实，也不会写妙手或平台" in html


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
