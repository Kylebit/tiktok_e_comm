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
from shared_platform.original_profit_reports import INDEX as ORIGINAL_PROFIT_INDEX
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
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()


def test_product_workspace_exposes_revision_bound_first_review_image_plan(
    tmp_path, monkeypatch
):
    from modules.sourcing import new_product_workbench

    report = tmp_path / "reports" / "product-preparation" / "offer-1" / "first-review.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps({
            "offer_id": "offer-1",
            "product_center_revision": 7,
            "image_execution_plan": {
                "schema_version": "first-review-image-plan/v1",
                "status": "PROPOSED",
                "source_actions": [
                    {"position": 6, "action": "TRANSLATE", "target_languages": ["th-TH"], "output_count": 1},
                    {"position": 7, "action": "TRANSLATE", "target_languages": ["en-master", "th-TH"], "output_count": 2},
                ],
                "generated_assets": [],
                "summary": {
                    "translation_positions": [6, 7],
                    "localized_output_count": 3,
                    "net_new_output_count": 0,
                    "paid_generation_required": True,
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(product_server_module, "ROOT", tmp_path)
    monkeypatch.setattr(
        new_product_workbench,
        "load_state",
        lambda _offer_id: {
            "review": {
                "image_actions": [{"action": "keep"} for _ in range(6)]
                + [{"action": "remove"}]
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


def test_final_product_routes_follow_current_navigation(product_server):
    for path in ("/new-product", "/product-workspace"):
        status, _, body = _get(product_server + path)
        assert status == 200
        assert "商品三轮审核" in body.decode("utf-8")

    status, _, body = _get(product_server + "/internal/release")
    assert status == 200
    assert "Release Lab" in body.decode("utf-8")

    status, _, body = _get(product_server + "/profit")
    assert status == 200
    assert 'id="profitTasks"' in body.decode("utf-8")
    assert f'href="{ORIGINAL_PROFIT_INDEX}"' in body.decode("utf-8")
    with urllib.request.urlopen(product_server + "/release", timeout=10) as release:
        assert release.geturl().endswith("/new-product")


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
        product_server + "/api/product-workspace/dashboard?offer_id=3828811808"
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
        product_server + "/api/profit-center/weekly?start=2026-07-13&end=2026-07-19"
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


def test_product_workspace_is_the_user_surface_and_fails_without_stale_results():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(encoding="utf-8")
    assert 'id="originalPublicationReview"' in html
    assert 'aria-label="商品三轮审核"' in html
    assert 'id="approval" class="approval-section operator-clutter"' in html
    assert 'id="releasePlan" class="release-plan-section"' in html
    assert 'id="lookupForm"' in html
    assert 'id="firstReviewImagePlan"' in html
    assert 'id="embeddedImageReview"' in html
    assert 'id="publicationScopeForm"' in html
    assert "/api/product-workspace/dashboard" in script
    assert "renderFailure(message)" in script
    assert "页面不会沿用上一次商品结果" in script
    assert "QUEUE_REFRESH_CONCURRENCY = 4" in script


def test_product_center_embeds_only_the_actionable_image_review_surface():
    html = (ROOT / "web/product_workspace.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/product_workspace.js").read_text(encoding="utf-8")
    assert 'id="embeddedImageReview"' in html
    assert 'id="embeddedSourceImageGrid"' in html
    assert 'id="saveEmbeddedImageReviewButton"' in html
    assert 'href="#pane-images"' in html
    assert "loadEmbeddedImageReview" in script
    assert "saveEmbeddedImageReview" in script
    assert "/api/product-flow/content-package/review" in script
    assert "renderFirstReviewImagePlan" in script
    assert "loadedQueueKey !== currentQueueKey" in script


def test_profit_center_preserves_snapshot_and_original_report_boundaries():
    html = (ROOT / "web/profit_center.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/profit_center.js").read_text(encoding="utf-8")
    assert "财务工作台" in html
    assert "快照复核" in html
    assert "单 SKU 证据 / 估算" in html
    assert "复核与已批准月报分别保留" in html
    assert "本页不会自动拉取订单、刷新 FX 或生成报告" in html
    assert 'id="reportFrame"' in html
    assert 'id="skuDialog"' in html
    assert "/api/profit-center/weekly" not in script
