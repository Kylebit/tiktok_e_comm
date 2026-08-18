from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from threading import Thread
import urllib.error
import urllib.request

import pytest

from domains.channel_operations.release_executor import AdapterExecutionResult
from modules.products import release_adapters
from modules.products import server as product_server
from shared_platform import release_store
from shared_platform.release_store import ReleaseStore


class _ReadOnlyStore:
    def __init__(self, plan: dict | None, run: dict | None) -> None:
        self.plan = plan
        self.run = run
        self.reads: list[tuple[str, str]] = []

    def active_plan_for_product(self, product_id: str) -> dict | None:
        self.reads.append(("active_plan_for_product", product_id))
        return self.plan

    def get_run(self, run_id: str) -> dict | None:
        self.reads.append(("get_run", run_id))
        return self.run

    def __getattr__(self, name: str):
        raise AssertionError(f"read-only seam attempted unexpected store method {name}")


def _approved_plan_and_failed_run(
    *,
    target_label: str = "shopee:PH",
    status: str = "FAILED",
    external_id: str = "56164935203",
) -> tuple[dict, dict]:
    plan_id = "omnichannel:readonly-reconcile"
    payload_digest = "a" * 64
    confirmation_token = "PUBLISH-INTERNAL-TOKEN"
    scope_digest = "internal-approval-scope-digest"
    approval_id = "release-approval:readonly-reconcile"
    payload = {
        "plan_id": plan_id,
        "product_id": "3838616043",
        "seller_sku": "0954",
        "product_package_id": "product:3838616043:0954",
        "content_package_id": "content:3838616043:r15",
        "targets": ["miaoshou:COMMON", "shopee:PH", "shopee:TH"],
        "omnichannel_scope_digest": scope_digest,
    }
    plan = {
        **payload,
        "payload": payload,
        "payload_digest": payload_digest,
        "confirmation_token": confirmation_token,
        "status": "APPROVED",
        "approval": {
            "approval_id": approval_id,
            "plan_id": plan_id,
            "payload_digest": payload_digest,
            "confirmation_token": confirmation_token,
            "approved_by": "Kyle",
            "user_approved": 1,
            "status": "APPROVED",
        },
    }
    target = {
        "target_label": target_label,
        "idempotency_key": f"publish:{target_label}:readonly-reconcile",
        "status": status,
        "external_id": external_id,
        "latest_failure_evidence": {
            "evidence": {
                "source": "shopee_publish",
                "external_writes_performed": ["shopee:add_item"],
            }
        },
        "failure_events": [
            {
                "evidence": {
                    "source": "shopee_publish",
                    "external_writes_performed": ["shopee:add_item"],
                }
            }
        ],
    }
    run = {
        "run_id": f"release-run:{payload_digest[:24]}",
        "plan_id": plan_id,
        "approval_id": approval_id,
        "status": "PARTIAL_FAILED",
        "targets": [target],
    }
    return plan, run


def _official_result(
    *,
    region: str = "PH",
    item_id: str = "56164935203",
    verified: bool = False,
) -> AdapterExecutionResult:
    checks = {
        "seller_sku": True,
        "model_sku": True,
        "localized_title": True,
        "rich_localized_description": True,
        "price": verified,
        "image_count": True,
        "all_applicable_logistics": True,
        "status": True,
    }
    return AdapterExecutionResult(
        succeeded=verified,
        readback_verified=verified,
        detail="existing item readback completed",
        external_reference=item_id,
        readback_evidence={
            "verified": verified,
            "source": "official_shopee_partner_api",
            "authentication_mode": "existing_token_only",
            "reconciliation_mode": "read_only_existing_item",
            "region": region,
            "shop_id": 123456,
            "item_id": item_id,
            "seller_skus": ["0954"],
            "model_skus": ["0954"],
            "title": "sensitive observed title",
            "prices": [868],
            "expected_price": {"value": 81.69, "currency": "CNY"},
            "description_length": 618,
            "image_count": 6,
            "logistics": [{"logistic_id": 1, "enabled": True}],
            "disabled_logistics": [],
            "status": "NORMAL",
            "price_issues": [] if verified else ["sip_item_price_does_not_match"],
            "checks": checks,
            "external_writes_performed": [],
        },
    )


@pytest.mark.parametrize(
    ("target_label", "region", "external_id"),
    [
        ("shopee:PH", "PH", "56164935203"),
        ("shopee:TH", "TH", "51564925929"),
    ],
)
def test_service_constructs_private_authority_and_returns_redacted_exact_evidence(
    monkeypatch,
    target_label,
    region,
    external_id,
):
    plan, run = _approved_plan_and_failed_run(
        target_label=target_label,
        external_id=external_id,
    )
    store = _ReadOnlyStore(plan, run)
    requests = []
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        release_adapters,
        "reconcile_existing_shopee_target",
        lambda request: requests.append(request)
        or _official_result(region=region, item_id=external_id),
    )

    status, response = product_server._reconcile_existing_shopee_target_readonly(
        offer_id="3838616043",
        target_label=target_label,
    )

    assert status == 200
    assert response["ok"] is True
    assert response["verified"] is False
    assert response["target_label"] == target_label
    assert response["external_id"] == external_id
    assert response["evidence"]["checks"]["price"] is False
    assert response["evidence"]["price_issues"] == [
        "sip_item_price_does_not_match"
    ]
    assert response["external_writes_performed"] == []
    assert response["state_mutations_performed"] == []
    request = requests[0]
    assert request.plan_id == plan["plan_id"]
    assert request.confirmation_token == plan["confirmation_token"]
    assert request.approval_scope_digest == (
        plan["payload"]["omnichannel_scope_digest"]
    )
    assert request.idempotency_key == run["targets"][0]["idempotency_key"]
    assert store.reads == [
        ("active_plan_for_product", "3838616043"),
        ("get_run", run["run_id"]),
    ]
    encoded = json.dumps(response, ensure_ascii=False)
    for secret in (
        plan["confirmation_token"],
        plan["payload"]["omnichannel_scope_digest"],
        plan["seller_sku"],
        "sensitive observed title",
        '"prices"',
        '"expected_price"',
        '"shop_id"',
    ):
        assert secret not in encoded


def test_readonly_reconcile_does_not_change_durable_store_bytes(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(
        {
            "plan_id": "omnichannel:readonly-store-proof",
            "product_id": "3838616043",
            "seller_sku": "0954",
            "product_package_id": "product:3838616043:0954",
            "content_package_id": "content:3838616043:r15",
            "targets": ["shopee:PH"],
            "omnichannel_scope_digest": "internal-approval-scope-digest",
            "commercial_scope": {
                "cost_snapshot_id": "cost:3838616043:r15",
                "fx_snapshot_id": "fx:2026-07-27",
                "pricing_rule_version": "sea-v1",
            },
        }
    )
    store.approve_plan(
        plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=plan["confirmation_token"],
    )
    run = store.start_run(plan["plan_id"])
    store.begin_target(run["run_id"], "shopee:PH")
    store.record_target_failure(
        run["run_id"],
        "shopee:PH",
        error="item created; price readback requires reconciliation",
        external_id="56164935203",
        failure_evidence={
            "source": "shopee_publish",
            "external_writes_performed": ["shopee:add_item"],
        },
    )
    before = store.path.read_bytes()
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        release_adapters,
        "reconcile_existing_shopee_target",
        lambda _request: _official_result(),
    )

    status, response = product_server._reconcile_existing_shopee_target_readonly(
        offer_id="3838616043",
        target_label="shopee:PH",
    )

    assert status == 200
    assert response["external_writes_performed"] == []
    assert response["state_mutations_performed"] == []
    assert store.path.read_bytes() == before
    unchanged = store.get_run(run["run_id"])
    target = unchanged["targets"][0]
    assert unchanged["status"] == "FAILED"
    assert target["status"] == "FAILED"
    assert target["external_id"] == "56164935203"
    assert target["readback"] is None


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("wrong_plan_status", "current approved immutable ReleasePlan"),
        ("wrong_run_plan", "durable release run does not match"),
        ("no_external_id", "requires the recorded external_id"),
        ("invalid_target_state", "requires a FAILED durable target"),
    ],
)
def test_readonly_reconcile_fail_closed_before_adapter(
    monkeypatch,
    case,
    expected_error,
):
    plan, run = _approved_plan_and_failed_run()
    if case == "wrong_plan_status":
        plan["status"] = "PENDING_APPROVAL"
    elif case == "wrong_run_plan":
        run["plan_id"] = "omnichannel:other"
    elif case == "no_external_id":
        run["targets"][0]["external_id"] = ""
    elif case == "invalid_target_state":
        run["targets"][0]["status"] = "SUCCEEDED"
    store = _ReadOnlyStore(plan, run)
    calls = []
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        release_adapters,
        "reconcile_existing_shopee_target",
        lambda request: calls.append(request) or _official_result(),
    )

    status, response = product_server._reconcile_existing_shopee_target_readonly(
        offer_id="3838616043",
        target_label="shopee:PH",
    )

    assert status == 409
    assert expected_error in response["error"]
    assert response["external_writes_performed"] == []
    assert response["state_mutations_performed"] == []
    assert calls == []


@pytest.mark.parametrize("target_label", ["shopee:MY", "shopee:VN", "tiktok:PH"])
def test_readonly_reconcile_allowlist_is_exact(monkeypatch, target_label):
    calls = []
    monkeypatch.setattr(
        release_store,
        "default_release_store",
        lambda: calls.append("store") or pytest.fail("store must not be opened"),
    )

    status, response = product_server._reconcile_existing_shopee_target_readonly(
        offer_id="3838616043",
        target_label=target_label,
    )

    assert status == 400
    assert response["allowed_targets"] == ["shopee:PH", "shopee:TH"]
    assert response["external_writes_performed"] == []
    assert calls == []


def test_readonly_reconcile_rejects_nonexact_or_write_evidence(monkeypatch):
    plan, run = _approved_plan_and_failed_run()
    store = _ReadOnlyStore(plan, run)
    result = _official_result()
    evidence = dict(result.readback_evidence or {})
    evidence["external_writes_performed"] = ["shopee:update_item"]
    unsafe = AdapterExecutionResult(
        succeeded=result.succeeded,
        readback_verified=result.readback_verified,
        detail=result.detail,
        external_reference=result.external_reference,
        readback_evidence=evidence,
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        release_adapters,
        "reconcile_existing_shopee_target",
        lambda _request: unsafe,
    )

    status, response = product_server._reconcile_existing_shopee_target_readonly(
        offer_id="3838616043",
        target_label="shopee:PH",
    )

    assert status == 502
    assert "exact contract" in response["error"]
    assert response["external_writes_performed"] == []
    assert response["state_mutations_performed"] == []


@pytest.fixture
def product_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
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
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_get_route_is_readonly_and_post_is_not_registered(
    product_http_server,
    monkeypatch,
):
    calls = []

    def reconcile(**kwargs):
        calls.append(kwargs)
        return 200, {
            "ok": True,
            "mode": "read_only_existing_target",
            "target_label": kwargs["target_label"],
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }

    monkeypatch.setattr(
        product_server,
        "_reconcile_existing_shopee_target_readonly",
        reconcile,
    )
    status, body = _request(
        product_http_server
        + "/api/product-workspace/reconcile-target"
        + "?offer_id=3838616043&target_label=shopee%3APH"
    )
    payload = json.loads(body)

    assert status == 200
    assert payload["mode"] == "read_only_existing_target"
    assert payload["external_writes_performed"] == []
    assert calls == [
        {"offer_id": "3838616043", "target_label": "shopee:PH"}
    ]

    status, _ = _request(
        product_http_server + "/api/product-workspace/reconcile-target",
        method="POST",
        body=b"{}",
    )

    assert status == 404
    assert len(calls) == 1


def test_shopee_price_repair_preview_is_get_only(
    product_http_server,
    monkeypatch,
):
    calls = []

    def preview(**kwargs):
        calls.append(kwargs)
        return 200, {
            "ok": True,
            "repair_allowed": True,
            "plan_id": "omnichannel:preview",
            "target_label": kwargs["target_label"],
            "expected_revision": 31,
            "payload_digest": "d" * 64,
            "preflight_digest": "p" * 64,
            "external_writes_performed": [],
            "state_mutations_performed": [],
        }

    monkeypatch.setattr(
        product_server,
        "_preview_existing_shopee_target_price",
        preview,
    )
    url = (
        product_http_server
        + "/api/product-workspace/release-target/"
        "shopee-price-repair-preview"
    )
    status, body = _request(
        url + "?offer_id=3838616043&target_label=shopee%3APH"
    )
    payload = json.loads(body)

    assert status == 200
    assert payload["repair_allowed"] is True
    assert payload["external_writes_performed"] == []
    assert payload["state_mutations_performed"] == []
    assert calls == [
        {"offer_id": "3838616043", "target_label": "shopee:PH"}
    ]

    status, _body = _request(url, method="POST", body=b"{}")

    assert status == 404
    assert len(calls) == 1
