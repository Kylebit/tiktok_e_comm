import json
from types import SimpleNamespace

import pytest

from shared_platform.product_publication_runs import public_publication_run_status
from shared_platform.product_publication_executors import _shopee_result
from shared_platform.shopee_publication_checkpoint import (
    ShopeePublicationCheckpointStore,
    public_shopee_progress_for_run,
)


def _request(root):
    return SimpleNamespace(
        run_id="shopee-observability-test",
        report_id="publication-report:shopee-observability-test",
        target_labels=("shopee:PH", "shopee:MY"),
        snapshot={
            "offer_id": "3956742887",
            "product_revision": 42,
            "plan_id": "omnichannel:" + "a" * 64,
            "snapshot_digest": "sha256:" + "b" * 64,
        },
    )


def _run():
    return {
        "run_id": "shopee-observability-test",
        "report_id": "publication-report:shopee-observability-test",
        "offer_id": "3956742887",
        "revision": 42,
        "plan_id": "omnichannel:" + "a" * 64,
        "snapshot_digest": "sha256:" + "b" * 64,
        "platform_scope": ["SHOPEE"],
        "target_count": 2,
        "state": "RUNNING",
        "created_at": "2026-09-13T00:00:00+00:00",
        "updated_at": "2026-09-13T00:00:01+00:00",
    }


def test_checkpoint_keeps_task_id_server_side_and_projects_only_binding(tmp_path):
    store = ShopeePublicationCheckpointStore(tmp_path, _request(tmp_path))
    store.record("DISPATCH", {
        "target_label": "shopee:PH",
        "attempted": True,
        "accepted": True,
        "outcome": "ACCEPTED",
        "provider_task_id": "task-12345",
        "provider_code": "",
        "message": "access_token=must-not-survive",
        "external_write_count": 1,
    })
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["targets"]["shopee:PH"]["provider_task_id"] == "task-12345"
    assert "must-not-survive" not in store.path.read_text(encoding="utf-8")

    progress = public_shopee_progress_for_run(tmp_path, _run())
    assert progress["targets"][0] == {
        "target_label": "shopee:PH",
        "stage": "DISPATCH",
        "outcome": "ACCEPTED",
        "request_attempted": True,
        "provider_identity_bound": True,
        "provider_code": "",
        "provider_message": "Shopee accepted the target; official readback is pending",
        "external_write_count": 1,
    }
    assert "task-12345" not in json.dumps(progress)

    public = public_publication_run_status(_run(), progress=progress)
    assert public["progress"] == progress


def test_readback_checkpoint_preserves_bound_task_and_failure_detail(tmp_path):
    store = ShopeePublicationCheckpointStore(tmp_path, _request(tmp_path))
    store.record("DISPATCH", {
        "target_label": "shopee:MY", "attempted": True, "accepted": True,
        "outcome": "ACCEPTED", "provider_task_id": "998877",
        "external_write_count": 1,
    })
    store.record("READBACK", {
        "target_label": "shopee:MY", "attempted": True, "accepted": True,
        "outcome": "FAILED", "provider_code": "model_price_mismatch",
        "message": "official model price did not match", "external_write_count": 0,
    })
    raw = store.load()
    target = raw["targets"]["shopee:MY"]
    assert target["provider_task_id"] == "998877"
    assert target["stage"] == "READBACK"
    assert target["provider_code"] == "model_price_mismatch"
    assert target["provider_message"] == "official model price did not match"
    assert len(raw["events"]) == 2


def test_existing_item_and_models_bind_identity_without_a_publish_task(tmp_path):
    store = ShopeePublicationCheckpointStore(tmp_path, _request(tmp_path))
    store.record("DISPATCH", {
        "target_label": "shopee:PH", "attempted": False, "accepted": True,
        "outcome": "ACCEPTED", "existing_item_id": "441122",
        "external_write_count": 0,
    })
    store.record("READBACK", {
        "target_label": "shopee:PH", "attempted": True, "accepted": True,
        "outcome": "PUBLISHED", "item_id": "441122",
        "official_catalog_rows": [
            {"identity": {"variant_id": "7001"}},
            {"identity": {"variant_id": "7002"}},
        ],
        "external_write_count": 1,
    })
    raw = store.load()["targets"]["shopee:PH"]
    assert raw["provider_task_id"] == ""
    assert raw["provider_item_id"] == "441122"
    assert raw["provider_model_ids"] == ["7001", "7002"]
    public = store.public_progress()["targets"][0]
    assert public["provider_identity_bound"] is True
    assert "441122" not in json.dumps(public)


@pytest.mark.parametrize("secret", [
    "Authorization: Basic abc", "Bearer abc", "api_key=abc", "api-key=abc",
    "API KEY abc", "password=hunter2", "passwd hunter2", "Cookie: sid=abc",
    "Set-Cookie: sid=abc", "client_secret=abc",
])
def test_public_progress_never_returns_secret_shaped_provider_message(tmp_path, secret):
    store = ShopeePublicationCheckpointStore(tmp_path, _request(tmp_path))
    store.record("DISPATCH", {
        "target_label": "shopee:PH", "attempted": True, "accepted": False,
        "outcome": "REJECTED", "provider_code": "REJECTED",
        "message": secret, "external_write_count": 0,
    })
    serialized = json.dumps(store.public_progress()).casefold()
    assert secret.casefold() not in serialized
    assert "abc" not in serialized


def test_final_shopee_result_retains_safe_target_evidence_without_raw_task_id():
    result = _shopee_result(
        {"targets": [{
            "target_label": "shopee:PH", "attempted": True,
            "accepted": True, "outcome": "ACCEPTED",
            "provider_task_id": "task-private-123", "external_write_count": 1,
        }]},
        {"targets": [{
            "target_label": "shopee:PH", "attempted": True,
            "accepted": True, "outcome": "FAILED",
            "provider_code": "PRICE_MISMATCH", "external_write_count": 0,
        }]},
        labels=("shopee:PH",),
        readback_completed=True,
        include_target_evidence=True,
    )
    evidence = result["targets"][0]["evidence"]
    assert evidence["stage"] == "READBACK"
    assert evidence["provider_code"] == "PRICE_MISMATCH"
    assert evidence["provider_identity_bound"] is True
    assert "task-private-123" not in json.dumps(result)


def test_existing_item_readback_write_sets_request_attempted_and_exact_count():
    result = _shopee_result(
        {"targets": [{
            "target_label": "shopee:PH", "attempted": False,
            "accepted": True, "outcome": "ACCEPTED",
            "existing_item_id": "441122", "external_write_count": 0,
        }]},
        {"targets": [{
            "target_label": "shopee:PH", "attempted": True,
            "accepted": True, "outcome": "PUBLISHED", "item_id": "441122",
            "listing_attempted": True, "external_write_count": 1,
        }]},
        labels=("shopee:PH",), readback_completed=True,
        include_target_evidence=True,
    )
    evidence = result["targets"][0]["evidence"]
    assert evidence["request_attempted"] is True
    assert evidence["external_write_count"] == 1
    assert evidence["provider_identity_bound"] is True


def test_unavailable_catalog_placeholder_does_not_bind_provider_identity():
    result = _shopee_result(
        {"targets": [{
            "target_label": "shopee:PH", "attempted": False,
            "accepted": False, "outcome": "NOT_ATTEMPTED",
            "external_write_count": 0,
        }]},
        {"targets": [{
            "target_label": "shopee:PH", "attempted": True,
            "accepted": False, "outcome": "FAILED",
            "official_catalog_rows": [{
                "authority": "UNAVAILABLE", "verified": False,
                "reason": "official_catalog_identity_unbound",
            }],
            "external_write_count": 0,
        }]},
        labels=("shopee:PH",), readback_completed=True,
        include_target_evidence=True,
    )
    assert result["targets"][0]["evidence"]["provider_identity_bound"] is False
