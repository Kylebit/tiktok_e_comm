"""Regression contract for the simplified platform publish buttons.

The browser submits one platform batch and the HTTP response itself is the
final Miaoshou result.  The UI must not create a client-side polling or manual
acceptance workflow after that response.
"""

from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
import inspect
import os
from pathlib import Path
import subprocess
import threading
import urllib.error
import urllib.request

from modules.products import server as product_server
from shared_platform import oneclick_release_controlplane as oneclick_controlplane
from tests.test_tiktok_independent_http import (
    _approved_plan,
    _CollectBoxStore,
    _Publisher,
    _publish_contexts,
    _ReleaseStore,
    _request_body,
)
from tests.test_release_ux_contract import (
    BROWSER_CONTRACT,
    ROOT,
    _browser_runtime,
    _static_server,
)


def _post_json(url: str, payload: dict[str, object]):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_tiktok_button_response_is_the_final_miaoshou_result(
    tmp_path, monkeypatch
):
    plan = _approved_plan()
    contexts = _publish_contexts(plan)
    publisher = _Publisher()
    monkeypatch.setattr(
        product_server, "_tiktok_release_store", lambda: _ReleaseStore(plan)
    )
    monkeypatch.setattr(
        product_server, "_collectbox_action_store", lambda: _CollectBoxStore(contexts)
    )
    monkeypatch.setattr(
        product_server, "_tiktok_publisher", lambda: publisher
    )
    monkeypatch.setattr(
        product_server,
        "_start_oneclick_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("TikTok must not enter the shared platform job")
        ),
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _post_json(
            (
                f"http://127.0.0.1:{server.server_address[1]}"
                "/api/product-workspace/publish-tiktok"
            ),
            _request_body(plan),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert body["schema_version"] == "miaoshou-platform-publish-result/v1"
    assert body["success"] is True
    assert body["target_count"] == 6
    assert body["successful_target_count"] == 6
    assert body["failed_targets"] == []
    assert body["write_request_count"] == 6
    assert body["external_write_count"] == 6
    assert len(publisher.snapshots) == 1


def test_shopee_global_success_is_read_from_shared_controls(monkeypatch):
    """The global master is a shared control, not a storefront target row."""

    class FakeStore:
        def get_job(self, *, job_id):
            assert job_id == "job-shopee-global"
            return {
                "targets": [],
                "shared_controls": [
                    {
                        "target_label": "shopee:GLOBAL",
                        "status": "SUCCEEDED",
                    }
                ],
            }

    class FakeWorker:
        def __init__(self, *_args, **_kwargs):
            self.advanced = False

        def advance_once(self, job_id):
            assert job_id == "job-shopee-global"
            if self.advanced:
                return False
            self.advanced = True
            return True

    monkeypatch.setattr(
        oneclick_controlplane,
        "OneClickReleaseWorker",
        FakeWorker,
    )
    monkeypatch.setattr(
        product_server,
        "_consume_oneclick_outcome_receipts",
        lambda _store: None,
    )

    status, body = product_server._complete_oneclick_platform_batch(
        control_store=FakeStore(),
        job_id="job-shopee-global",
        target_labels=("shopee:GLOBAL",),
        batch_scope="SHOPEE_GLOBAL",
    )

    assert status == 200
    assert body["success"] is True
    assert body["successful_target_count"] == 1
    assert body["failed_targets"] == []


def test_shopee_button_uses_approved_plan_for_global_only_publish(monkeypatch):
    """Shopee is independent and creates only one CNSC global product.

    The approved ReleasePlan is already the user's approval boundary.  The
    button must not require a second Shopee-global approval/job, and must
    never start TikTok, Ozon, or Shopee regional publication.
    """

    payload = {
        "product_id": "3846511157",
        "seller_sku": "0959",
        "product_facts": {
            "title": "Approved Shopee title",
        },
        "listing_copy": {
            "shopee_description_en": "Approved Shopee description",
            "candidates": [
                {
                    "channel": "shopee",
                    "site": "CNSC",
                    "policy_check": "passed",
                    "title": "Approved Shopee title",
                }
            ],
        },
        "pricing": {
            "master_price_source": {
                "region": "PH",
                "target_key": "lh_ph",
            },
            "selected_targets": {
                "shopee:PH": {
                    "source": {"target_key": "lh_ph"},
                    "derived_preview": {
                        "global_original_price_cny": 61.71,
                    },
                }
            },
        },
    }
    monkeypatch.setattr(
        product_server,
        "_platform_approved_context",
        lambda _data: (
            {
                "payload": payload,
                "plan": {
                    "plan_id": "approved-plan",
                    "seller_sku": "0959",
                    "payload": payload,
                },
                "store": object(),
                "dashboard": {},
            },
            None,
        ),
    )
    monkeypatch.setattr(
        product_server,
        "_start_oneclick_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Shopee must not enter the shared platform job")
        ),
    )
    approved_facts = {
        "seller_sku": "0959",
        "title": "Approved Shopee title",
        "description": "Approved Shopee description",
        "region": "PH",
        "global_original_price_cny": 61.71,
        "images": ["https://images.example/1.jpg"],
        "package_cm": [38.0, 85.0, 0.1],
        "weight_kg": 0.2,
        "variants": [{"seller_sku": "0959", "name": "Default"}],
        "sku_commercial_facts": {"0959": {}},
        "sku_prices": {"0959": 61.71},
        "quantity": 1,
    }
    monkeypatch.setattr(
        product_server,
        "_approved_shopee_global_publish_facts",
        lambda _payload: approved_facts,
    )
    calls: list[dict[str, object]] = []

    def fake_publish(facts):
        calls.append(facts)
        return {
            "ok": True,
            "flow": "global_only",
            "global_item_id": 123456,
        }

    monkeypatch.setattr(
        "modules.shopee.approved_global_publisher.publish_approved_global",
        fake_publish,
    )

    status, body = product_server._start_shopee_global_release(
        {
            "confirm_publish": True,
            "offer_id": "3846511157",
            "plan_id": "approved-plan",
            "confirmation_token": "token",
        }
    )

    assert status == 200
    assert body["success"] is True
    assert body["platform"] == "SHOPEE_GLOBAL"
    assert body["target_count"] == 1
    assert body["successful_target_count"] == 1
    assert calls == [approved_facts]


def test_platform_publish_error_redacts_json_style_credentials():
    detail = product_server._safe_platform_publish_error(
        RuntimeError(
            '{"access_token":"access-123",'
            '"partner_key":"partner-456",'
            '"signature":"signature-789",'
            '"url":"https://secret.example/path"}'
        )
    )

    assert "access-123" not in detail
    assert "partner-456" not in detail
    assert "signature-789" not in detail
    assert "secret.example" not in detail

    bearer = product_server._safe_platform_publish_error(
        RuntimeError(
            "Authorization: Bearer bearer-real-token https://secret.example/path"
        )
    )
    assert "bearer-real-token" not in bearer
    assert "secret.example" not in bearer


def test_ozon_button_uses_isolated_official_import_path(monkeypatch):
    """Ozon must use its proven Seller API import, not Miaoshou or another job."""

    payload = {
        "product_id": "3846511157",
        "seller_sku": "0959",
        "product_facts": {
            "package_cm": [38, 85, 0.1],
        },
        "listing_copy": {
            "candidates": [
                {
                    "channel": "ozon",
                    "site": "RU",
                    "policy_check": "passed",
                    "title": "Approved Russian Ozon title",
                }
            ],
        },
        "images": [
            {"position": 1, "image_url": "https://images.example/1.jpg"},
            {"position": 2, "image_url": "https://images.example/2.jpg"},
        ],
        "pricing": {
            "selected_targets": {
                "ozon:RU": {
                    "derived_preview": {
                        "price_cny": 62,
                        "old_price_cny": 81,
                    }
                }
            }
        },
    }
    monkeypatch.setattr(
        product_server,
        "_platform_approved_context",
        lambda _data: (
            {
                "payload": payload,
                "plan": {
                    "plan_id": "approved-plan",
                    "seller_sku": "0959",
                    "payload": payload,
                },
                "store": object(),
                "dashboard": {},
            },
            None,
        ),
    )
    monkeypatch.setattr(
        product_server,
        "_start_oneclick_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Ozon must not enter the shared/Miaoshou job")
        ),
    )
    approved_facts = {
        "seller_sku": "0959",
        "title": "Approved Russian Ozon title",
        "size": (38.0, 85.0),
        "package_cm": [38.0, 85.0, 0.1],
        "weight_kg": 0.2,
        "quantity": 1,
        "source_category": {"id": "wall-decor", "name": "Wall decor"},
        "price": 62,
        "old_price": 81,
        "images": [
            "https://images.example/1.jpg",
            "https://images.example/2.jpg",
        ],
    }
    monkeypatch.setattr(
        product_server,
        "_approved_ozon_publish_facts",
        lambda _payload: approved_facts,
    )
    monkeypatch.setattr(
        "modules.ozon.target_scoped.read_existing_product",
        lambda **_kwargs: {"checks": {}},
    )
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_migrate(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "ok": True,
            "offer_id": "0959",
            "task_id": "ozon-task-1",
            "import_request_attempted": True,
            "import_dispatch_outcome": "accepted",
        }

    monkeypatch.setattr(
        "modules.ozon.migrate_batch.migrate_one",
        fake_migrate,
    )

    status, body = product_server._start_ozon_release(
        {
            "confirm_publish": True,
            "offer_id": "3846511157",
            "plan_id": "approved-plan",
            "confirmation_token": "token",
        }
    )

    assert status == 200
    assert body["success"] is True
    assert body["platform"] == "OZON"
    assert calls == [
        (
            ("0959",),
            {
                "allow_deepseek": False,
                "title_candidate": "Approved Russian Ozon title",
                "product_size_cm": (38.0, 85.0),
                "quantity": 1,
                "price_cny_override": 62,
                "old_price_cny_override": 81,
                "price_source_override": "approved_release_plan",
                "price_label_override": "ozon:RU",
                "image_urls_override": [
                    "https://images.example/1.jpg",
                    "https://images.example/2.jpg",
                ],
                "approved_snapshot": {
                    "seller_sku": "0959",
                    "title": "Approved Russian Ozon title",
                    "package_cm": [38.0, 85.0, 0.1],
                    "weight_kg": 0.2,
                    "quantity": 1,
                    "price_cny": 62,
                    "old_price_cny": 81,
                    "images": [
                        "https://images.example/1.jpg",
                        "https://images.example/2.jpg",
                    ],
                    "source_category": {
                        "id": "wall-decor",
                        "name": "Wall decor",
                    },
                },
                "process_images": False,
                "wait_for_import": False,
                "skip_rich_content": True,
                "skip_mapping_write": True,
            },
        )
    ]


def test_ozon_button_rejects_vendor_errors_even_when_result_says_ok(monkeypatch):
    context = {"payload": {}, "plan": {}, "store": object(), "dashboard": {}}
    monkeypatch.setattr(
        product_server,
        "_platform_approved_context",
        lambda _data: (context, None),
    )
    monkeypatch.setattr(
        product_server,
        "_approved_ozon_publish_facts",
        lambda _payload: {
            "seller_sku": "0959",
            "title": "Approved title",
            "size": (38.0, 85.0),
            "price": 62,
            "old_price": 81,
            "images": ["https://images.example/1.jpg"],
        },
    )
    monkeypatch.setattr(
        "modules.ozon.target_scoped.read_existing_product",
        lambda **_kwargs: {"checks": {}},
    )
    monkeypatch.setattr(
        "modules.ozon.migrate_batch.migrate_one",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status": "imported",
            "task_id": "task-with-error",
            "errors": [{"code": "invalid_attribute"}],
            "import_dispatch_outcome": "accepted",
        },
    )

    status, body = product_server._start_ozon_release(
        {"confirm_publish": True, "offer_id": "3846511157"}
    )

    assert status == 409
    assert body["success"] is False


def test_frontend_uses_only_four_simple_states_and_never_polls_after_post():
    script = (
        product_server.ROOT / "web/static/product_workspace.js"
    ).read_text(encoding="utf-8")
    start = script.index("async function publishPlatformBatch(")
    end = script.index("async function publishSelectedTargets()", start)
    publish = script[start:end]

    assert "发布中" in publish
    assert "发布成功" in publish
    assert "发布失败" in publish
    assert "payload.success !== true" in publish
    assert "response.status !== 200" in publish
    assert "scheduleOneClickStatusPoll" not in publish
    assert "payload.accepted" not in publish
    assert "payload.job" not in publish
    assert "WAITING_MANUAL_ACCEPTANCE" not in publish
    assert "RECONCILIATION_REQUIRED" not in publish


def test_server_startup_never_resumes_historical_publish_jobs():
    """Only an explicit platform-button request may trigger Miaoshou writes."""

    startup = inspect.getsource(product_server.serve)

    assert "_start_oneclick_background_worker()" not in startup


def test_authoritative_dashboard_render_initializes_four_state_cards():
    """An already-approved real dashboard must not wait for an auxiliary GET."""

    script = (
        product_server.ROOT / "web/static/product_workspace.js"
    ).read_text(encoding="utf-8")
    start = script.index("function render(data) {")
    end = script.index("function clearCurrentApprovalContext()", start)
    render = script[start:end]

    identity_position = render.index("ensureOneClickExecution(data);")
    cards_position = render.index("renderOneClickExecution(data);")
    assert cards_position > identity_position


def test_real_chromium_covers_all_simple_publish_paths_with_screenshots(
    tmp_path,
):
    runtime = _browser_runtime()
    assert runtime is not None, "bundled Node + Playwright runtime is required"
    node, modules = runtime
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    assert chrome.is_file(), "local Chrome executable is required"
    configured_artifacts = os.environ.get("ORBIT_BROWSER_ARTIFACT_DIR")
    artifacts = (
        Path(configured_artifacts)
        if configured_artifacts
        else tmp_path / "simple-platform-publish-screenshots"
    )
    environment = os.environ.copy()
    environment.update(
        {
            "NODE_PATH": str(modules),
            "ORBIT_CHROMIUM_BIN": str(chrome),
            "ORBIT_BROWSER_CONTRACT_ONLY": "simplified-platform-publish",
            "ORBIT_BROWSER_ARTIFACT_DIR": str(artifacts),
        }
    )
    with _static_server() as base_url:
        result = subprocess.run(
            [str(node), str(BROWSER_CONTRACT), base_url],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        "simplified platform publish Chromium contract failed\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    expected = {
        f"{viewport}-{state}.png"
        for viewport in ("1440x900", "390x844")
        for state in (
            "initial",
            "publishing",
            "failure-and-independent-success",
            "all-success-after-retry",
            "sibling-cards-stable-after-reimport",
        )
    }
    assert {path.name for path in artifacts.glob("*.png")} == expected
