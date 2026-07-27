from __future__ import annotations

from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
import sqlite3
from threading import Thread
import urllib.error
import urllib.parse
import urllib.request

import pytest

from domains.channel_operations import target_scoped_retry_adapters as adapters
from modules.products import server as product_server
from modules.shopee import auth as shopee_auth
from modules.shopee import client as shopee_client
from modules.shopee import global_sku_map
from modules.shopee import target_scoped as shopee_target
from shared_platform import release_store
from shared_platform.release_store import ReleaseStore


SOURCE_TITLE = "Approved English Shopee master"
SOURCE_DESCRIPTION = "Approved immutable Shopee description. " * 10
SOURCE_IMAGES = [
    "https://cdn.example/approved-1.jpg",
    "https://cdn.example/approved-2.jpg",
]
GLOBAL_ITEM_ID = "40283034166"
REGIONAL_ITEM_ID = "56164935203"


def _plan(target_label: str) -> dict:
    currency = "VND" if target_label == "shopee:VN" else "MYR"
    return {
        "plan_id": f"omnichannel:cross-layer-{target_label[-2:].lower()}",
        "product_id": "3838616043",
        "seller_sku": "0954",
        "product_package_id": "product:3838616043:0954:r1",
        "content_package_id": "content:3838616043:r1",
        "targets": [target_label],
        "product_revision": 41,
        "omnichannel_scope_digest": "scope-0954",
        "product_facts": {
            "weight_kg": 0.2,
            "package_cm": [34, 58, 3],
        },
        "listing_copy": {
            "candidates": [
                {
                    "channel": "shopee",
                    "site": "CNSC",
                    "title": SOURCE_TITLE,
                    "policy_check": "passed",
                }
            ],
            "shopee_description_en": SOURCE_DESCRIPTION,
        },
        "images": [
            {
                "position": index,
                "image_url": url,
                "artifact_id": f"private-lineage-{index}",
                "audit_id": f"private-audit-{index}",
            }
            for index, url in enumerate(SOURCE_IMAGES, start=1)
        ],
        "pricing": {
            "selected_targets": {
                target_label: {
                    "derived_preview": {
                        "local_original_price": 45,
                        "source_currency": currency,
                    }
                }
            }
        },
    }


def _failed_store(tmp_path, target_label: str) -> tuple[ReleaseStore, dict, dict]:
    store = ReleaseStore(tmp_path / f"release-{target_label[-2:]}.db")
    plan = store.create_plan(_plan(target_label))
    store.approve_plan(
        plan["plan_id"],
        confirmation_token=plan["confirmation_token"],
        approved_by="Kyle",
        user_approved=True,
    )
    run = store.start_run(plan["plan_id"])
    store.begin_target(run["run_id"], target_label)
    store.record_target_failure(
        run["run_id"],
        target_label,
        error="official pre-submit validation failed; no external write",
        failure_evidence={
            "phase": "pre_submit",
            "pre_submit_failure": True,
            "external_writes_performed": [],
        },
    )
    return store, store.get_plan(plan["plan_id"]), store.get_run(run["run_id"])


@pytest.fixture
def target_scoped_http_server():
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


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _database_counts(store: ReleaseStore) -> tuple[int, int]:
    with sqlite3.connect(store.path) as connection:
        return (
            connection.execute(
                "SELECT COUNT(*) FROM release_target_retry_proofs"
            ).fetchone()[0],
            connection.execute(
                "SELECT COUNT(*) FROM release_target_retry_operations"
            ).fetchone()[0],
        )


def _gate(monkeypatch, store: ReleaseStore, plan: dict, run_id: str) -> None:
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)

    def readonly_gate(_data, *, store):
        return {
            "dashboard": {},
            "payload": plan["payload"],
            "plan": plan,
            "run": store.get_run(run_id),
            "registry": {},
            "target_rows": [],
        }, None

    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        readonly_gate,
    )


def _official_fixture(
    monkeypatch,
    *,
    target_label: str,
    failure: str | None = None,
) -> dict[str, int]:
    counters = {
        "proof": 0,
        "execute": 0,
        "merchant_post": 0,
        "task_get": 0,
        "regional_get": 0,
    }
    original_proof = adapters.build_official_target_proof
    original_execute = adapters.execute_target_scoped_operation

    def counted_proof(request, allow_refresh=False):
        counters["proof"] += 1
        return original_proof(request, allow_refresh=allow_refresh)

    def counted_execute(request, proof):
        counters["execute"] += 1
        return original_execute(request, proof)

    monkeypatch.setattr(adapters, "build_official_target_proof", counted_proof)
    monkeypatch.setattr(
        adapters,
        "execute_target_scoped_operation",
        counted_execute,
    )
    monkeypatch.setattr(
        adapters,
        "_prepared_shopee_credentials",
        lambda _region: (12, "prepared-shop-token"),
    )
    monkeypatch.setattr(
        shopee_target,
        "scan_prepared_shop_sku",
        lambda **_kwargs: {
            "matches": [],
            "complete": True,
            "statuses": {
                status: {
                    "pages": 1,
                    "item_count": 0,
                    "base_rows": 0,
                    "model_rows": 0,
                    "count": 0,
                    "digest": f"{status.lower()}-digest",
                    "complete": True,
                }
                for status in ("NORMAL", "UNLIST", "BANNED")
            },
        },
    )
    monkeypatch.setattr(
        shopee_target,
        "compatible_prepared_logistics",
        lambda **_kwargs: [1, 2],
    )
    monkeypatch.setattr(
        global_sku_map,
        "global_item_id_for_match_key",
        lambda _seller_sku: GLOBAL_ITEM_ID,
    )
    monkeypatch.setattr(
        shopee_auth,
        "load_tokens",
        lambda: {
            "shops": {"12": {"merchant_id": 34}},
            "merchants": {"34": {"access_token": "prepared-merchant-token"}},
        },
    )

    def merchant_post(path, merchant_id, token, body):
        assert path.endswith("/create_publish_task")
        assert merchant_id == 34
        assert token == "prepared-merchant-token"
        assert body["global_item_id"] == int(GLOBAL_ITEM_ID)
        assert body["shop_region"] == target_label[-2:]
        counters["merchant_post"] += 1
        return {"response": {"publish_task_id": 777}}

    def merchant_get(path, merchant_id, token, query):
        assert merchant_id == 34
        assert token == "prepared-merchant-token"
        if path.endswith("/get_global_item_info"):
            return {
                "response": {
                    "global_item_list": [
                        {
                            "global_item_name": SOURCE_TITLE,
                            "description": SOURCE_DESCRIPTION,
                            "image": {"image_url_list": SOURCE_IMAGES},
                        }
                    ]
                }
            }
        if path.endswith("/get_global_model_list"):
            return {
                "response": {
                    "global_model": [
                        {
                            "global_model_id": 1,
                            "global_model_sku": "0954",
                            "tier_index": [0],
                        }
                    ]
                }
            }
        if path.endswith("/get_publish_task_result"):
            counters["task_get"] += 1
            if failure == "task_timeout":
                raise TimeoutError("simulated accepted-task timeout")
            if failure == "task_parse":
                raise ValueError("simulated accepted-task parse failure")
            return {
                "response": {
                    "publish_status": "success",
                    "item_id": int(REGIONAL_ITEM_ID),
                }
            }
        if path.endswith("/get_global_item_id"):
            return {
                "response": {
                    "item_id_map": [
                        {
                            "item_id": int(REGIONAL_ITEM_ID),
                            "global_item_id": int(GLOBAL_ITEM_ID),
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected merchant GET {path}")

    def shop_get(path, shop_id, token, query=None):
        assert shop_id == 12
        assert token == "prepared-shop-token"
        if path.endswith("/get_item_base_info"):
            counters["regional_get"] += 1
            if failure == "regional_timeout":
                raise TimeoutError("simulated regional readback timeout")
            if failure == "regional_parse":
                raise ValueError("simulated regional readback parse failure")
            images = [
                "https://regional.example/rehosted-main.jpg",
                "https://regional.example/rehosted-detail.jpg",
            ]
            regional_title = "Elegant wall decoration"
            regional_description = (
                "Durable decoration for bedrooms and living rooms with "
                "simple care instructions."
            )
            if failure == "copy_needs_review":
                regional_title = "中文标题"
                regional_description = "中文描述"
            return {
                "response": {
                    "item_list": [
                        {
                            "item_id": int(REGIONAL_ITEM_ID),
                            "item_name": regional_title,
                            "description": regional_description,
                            "item_status": "NORMAL",
                            "logistic_info": [
                                {"logistic_id": 1, "enabled": True},
                                {"logistic_id": 2, "enabled": True},
                            ],
                            "image": {"image_url_list": images},
                        }
                    ]
                }
            }
        if path.endswith("/get_model_list"):
            currency = "VND" if target_label == "shopee:VN" else "MYR"
            return {
                "response": {
                    "model": [
                        {
                            "model_id": 11,
                            "model_sku": "0954",
                            "price_info": [
                                {
                                    "currency": currency,
                                    "original_price": (
                                        "46"
                                        if failure == "hard_mismatch"
                                        else "45"
                                    ),
                                }
                            ],
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected shop GET {path}")

    monkeypatch.setattr(shopee_client, "merchant_post", merchant_post)
    monkeypatch.setattr(shopee_client, "merchant_get", merchant_get)
    monkeypatch.setattr(shopee_target, "shop_get", shop_get)
    return counters


def _freeze_proof_clock(monkeypatch) -> None:
    fixed_now = datetime.now(timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr(adapters, "datetime", FixedDateTime)


def _preview_url(base_url: str, target_label: str) -> str:
    query = urllib.parse.urlencode(
        {"offer_id": "3838616043", "target_label": target_label}
    )
    return (
        base_url
        + "/api/product-workspace/release-target/"
        + "target-scoped-action-preview?"
        + query
    )


def _post_body(plan: dict, target_label: str, preview: dict) -> dict:
    return {
        "offer_id": "3838616043",
        "seller_sku": "0954",
        "publication_targets": [target_label],
        "target_label": target_label,
        "plan_id": plan["plan_id"],
        "confirmation_token": plan["confirmation_token"],
        "expected_revision": preview["expected_revision"],
        "failure_attempt": preview["failure_attempt"],
        "payload_digest": preview["payload_digest"],
        "planned_command_digest": preview["planned_command_digest"],
        "preflight_digest": preview["preflight_digest"],
        "proof_digest": preview["proof_digest"],
        "approved_by": "Kyle",
        "confirm_target_scoped_action": True,
    }


def _post_url(base_url: str) -> str:
    return (
        base_url
        + "/api/product-workspace/release-target/target-scoped-action"
    )


@pytest.mark.parametrize("target_label", ["shopee:MY", "shopee:VN"])
def test_http_warning_outcome_succeeds_and_persists_manual_review(
    tmp_path,
    monkeypatch,
    target_scoped_http_server,
    target_label,
):
    store, plan, run = _failed_store(tmp_path, target_label)
    _gate(monkeypatch, store, plan, run["run_id"])
    _freeze_proof_clock(monkeypatch)
    counters = _official_fixture(monkeypatch, target_label=target_label)

    preview_status, preview = _http_json(
        _preview_url(target_scoped_http_server, target_label)
    )
    assert preview_status == 200
    status, payload = _http_json(
        _post_url(target_scoped_http_server),
        method="POST",
        payload=_post_body(plan, target_label, preview),
    )

    assert status == 200, payload
    assert payload["operation_status"] == "SUCCEEDED"
    assert payload["external_writes_performed"] == [
        "shopee:regional_publish"
    ]
    durable_run = store.get_run(run["run_id"])
    assert durable_run["status"] == "SUCCEEDED"
    assert durable_run["targets"][0]["status"] == "SUCCEEDED"
    operation = store.get_target_scoped_operation(
        run_id=run["run_id"],
        target_label=target_label,
    )
    assert operation["status"] == "SUCCEEDED"
    durable_result = json.dumps(
        operation["result"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert '"manual_review_required": true' in durable_result
    assert '"profit_status": "unverified"' in durable_result
    assert counters == {
        "proof": 2,
        "execute": 1,
        "merchant_post": 1,
        "task_get": 1,
        "regional_get": 1,
    }

    replay_status, replay = _http_json(
        _post_url(target_scoped_http_server),
        method="POST",
        payload=_post_body(plan, target_label, preview),
    )
    assert replay_status == 200
    assert replay["idempotent"] is True
    assert replay["external_writes_performed"] == []
    assert counters["proof"] == 2
    assert counters["execute"] == 1
    assert counters["merchant_post"] == 1


@pytest.mark.parametrize(
    "failure",
    ["copy_needs_review", "hard_mismatch"],
)
def test_postwrite_review_or_hard_fact_failure_never_marks_target_succeeded(
    tmp_path,
    monkeypatch,
    target_scoped_http_server,
    failure,
):
    target_label = "shopee:VN"
    store, plan, run = _failed_store(tmp_path, target_label)
    _gate(monkeypatch, store, plan, run["run_id"])
    _freeze_proof_clock(monkeypatch)
    counters = _official_fixture(
        monkeypatch,
        target_label=target_label,
        failure=failure,
    )
    _, preview = _http_json(
        _preview_url(target_scoped_http_server, target_label)
    )
    status, payload = _http_json(
        _post_url(target_scoped_http_server),
        method="POST",
        payload=_post_body(plan, target_label, preview),
    )

    assert status == 409
    assert payload["operation_status"] == "RECONCILIATION_REQUIRED"
    assert payload["external_writes_performed"] == [
        "shopee:regional_publish"
    ]
    assert counters["merchant_post"] == 1
    durable_run = store.get_run(run["run_id"])
    assert durable_run["status"] == "FAILED"
    assert (
        durable_run["targets"][0]["status"]
        == "RECONCILIATION_REQUIRED"
    )
    operation = store.get_target_scoped_operation(
        run_id=run["run_id"],
        target_label=target_label,
    )
    assert operation["status"] == "RECONCILIATION_REQUIRED"
    assert operation["result"]["outcome"] == "RECONCILIATION_REQUIRED"


def test_preview_proof_can_be_consumed_when_official_facts_do_not_change(
    tmp_path,
    monkeypatch,
    target_scoped_http_server,
):
    target_label = "shopee:MY"
    store, plan, run = _failed_store(tmp_path, target_label)
    _gate(monkeypatch, store, plan, run["run_id"])
    counters = _official_fixture(monkeypatch, target_label=target_label)
    preview_status, preview = _http_json(
        _preview_url(target_scoped_http_server, target_label)
    )
    assert preview_status == 200

    status, payload = _http_json(
        _post_url(target_scoped_http_server),
        method="POST",
        payload=_post_body(plan, target_label, preview),
    )

    assert status != 409 or payload.get("code") != "official_target_proof_drift"
    assert counters["proof"] == 2
    assert counters["execute"] == 1
    assert counters["merchant_post"] == 1


@pytest.mark.parametrize(
    "failure",
    ["task_timeout", "task_parse", "regional_timeout", "regional_parse"],
)
def test_postwrite_readback_errors_preserve_truthful_write_and_replay_zero(
    tmp_path,
    monkeypatch,
    target_scoped_http_server,
    failure,
):
    target_label = "shopee:MY"
    store, plan, run = _failed_store(tmp_path, target_label)
    _gate(monkeypatch, store, plan, run["run_id"])
    _freeze_proof_clock(monkeypatch)
    counters = _official_fixture(
        monkeypatch,
        target_label=target_label,
        failure=failure,
    )
    preview_status, preview = _http_json(
        _preview_url(target_scoped_http_server, target_label)
    )
    assert preview_status == 200
    body = _post_body(plan, target_label, preview)

    status, payload = _http_json(
        _post_url(target_scoped_http_server),
        method="POST",
        payload=body,
    )
    assert status == 409
    assert payload["operation_status"] == "RECONCILIATION_REQUIRED"
    assert payload["external_writes_performed"] == [
        "shopee:regional_publish"
    ]
    assert payload["durable_state_uncertain"] is True
    assert counters["merchant_post"] == 1
    operation = store.get_target_scoped_operation(
        run_id=run["run_id"],
        target_label=target_label,
    )
    assert operation["status"] == "RECONCILIATION_REQUIRED"
    assert operation["result"]["external_writes_performed"] == [
        "shopee:regional_publish"
    ]

    before = dict(counters)
    replay_status, replay = _http_json(
        _post_url(target_scoped_http_server),
        method="POST",
        payload=body,
    )
    assert replay_status == 409
    assert replay["operation_status"] == "RECONCILIATION_REQUIRED"
    assert replay["external_writes_performed"] == []
    assert counters == before


@pytest.mark.parametrize(
    "failure_point",
    ["scan", "global", "logistics"],
)
def test_preview_official_errors_leave_zero_proof_claim_execute_or_post(
    tmp_path,
    monkeypatch,
    target_scoped_http_server,
    failure_point,
):
    target_label = "shopee:VN"
    store, plan, run = _failed_store(tmp_path, target_label)
    _gate(monkeypatch, store, plan, run["run_id"])
    counters = _official_fixture(monkeypatch, target_label=target_label)

    def failed(**_kwargs):
        raise RuntimeError(f"simulated malformed {failure_point} response")

    if failure_point == "scan":
        monkeypatch.setattr(shopee_target, "scan_prepared_shop_sku", failed)
    elif failure_point == "global":
        monkeypatch.setattr(shopee_target, "inspect_existing_global", failed)
    else:
        monkeypatch.setattr(
            shopee_target,
            "compatible_prepared_logistics",
            failed,
        )

    status, payload = _http_json(
        _preview_url(target_scoped_http_server, target_label)
    )
    assert status == 409
    assert payload["code"] == "official_target_proof_failed"
    assert payload["external_writes_performed"] == []
    assert _database_counts(store) == (0, 0)
    assert counters["execute"] == 0
    assert counters["merchant_post"] == 0


def test_ozon_missing_successor_action_never_reaches_dynamic_adapter(
    tmp_path,
    monkeypatch,
    target_scoped_http_server,
):
    target_label = "ozon:RU"
    store, plan, run = _failed_store(tmp_path, target_label)
    _gate(monkeypatch, store, plan, run["run_id"])
    calls = []
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: calls.append("adapter") or adapters,
    )

    status, payload = _http_json(
        _preview_url(target_scoped_http_server, target_label)
    )
    assert status == 409
    assert payload["code"] == "successor_plan_stock_decision_required"
    assert payload["external_writes_performed"] == []
    assert calls == []
    assert _database_counts(store) == (0, 0)


def test_durable_and_http_evidence_is_redacted(
    tmp_path,
    monkeypatch,
    target_scoped_http_server,
):
    target_label = "shopee:MY"
    store, plan, run = _failed_store(tmp_path, target_label)
    _gate(monkeypatch, store, plan, run["run_id"])
    _freeze_proof_clock(monkeypatch)
    _official_fixture(
        monkeypatch,
        target_label=target_label,
        failure="task_timeout",
    )
    _, preview = _http_json(
        _preview_url(target_scoped_http_server, target_label)
    )
    _, payload = _http_json(
        _post_url(target_scoped_http_server),
        method="POST",
        payload=_post_body(plan, target_label, preview),
    )
    operation = store.get_target_scoped_operation(
        run_id=run["run_id"],
        target_label=target_label,
    )
    encoded = json.dumps(
        {"http": payload, "durable": operation},
        ensure_ascii=False,
        sort_keys=True,
    )
    for secret in (
        "prepared-shop-token",
        "prepared-merchant-token",
        SOURCE_TITLE,
        SOURCE_DESCRIPTION.strip(),
        *SOURCE_IMAGES,
        "Elegant wall decoration",
        "Durable decoration for bedrooms",
    ):
        assert secret not in encoded
    assert "RECONCILIATION_REQUIRED" in encoded
