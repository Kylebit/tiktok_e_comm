import json
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform.product_publication_runs import public_publication_run_status
from shared_platform.product_publication_executors import _shopee_result
from shared_platform.shopee_publication_checkpoint import (
    ShopeePublicationCheckpointStore,
    public_shopee_progress_for_run,
)


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows path compatibility regression')
def test_captured_PH_existing_dispatch_long_path_keeps_identical_journal(tmp_path):
    value=os.environ.get('ORBIT_B0431_SH_PATH_CONTRAST')
    if not value:pytest.skip('explicit private captured contrast not supplied')
    proof=Path(value)
    raw=proof.read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='c0df7afb23104d335fd2953c0e0849e011d25f97021dde4f409abe19aeeb1259'
    captured=json.loads(raw);row=captured['direct_original_dispatch']['targets'][0]
    assert row['existing_item_id']=='47970054181' and row['attempted'] is False and row['external_write_count']==0
    request=SimpleNamespace(run_id='native-files-24c3e512eb40b27ee4b58b72a18171caf87359e8c2099676920970900eaab939',
        report_id='native-report-24c3e512eb40b27ee4b58b72a18171caf87359e8c2099676920970900eaab939',target_labels=('shopee:PH',),
        snapshot=dict(offer_id='8412287877065586543',product_revision=6,
            plan_id='r3-marketplace:8412287877065586543:07707b4b7901ca9b97b08acd',
            snapshot_digest='sha256:8eecd1c0b7beadcaddc7a8655f25b5f9e981729c4cb44e33dbd5aac17eaaa3de'))
    long_root=tmp_path/'long'
    while len(str(long_root))<170:long_root=long_root/'retained-checkpoint-scope'
    short=ShopeePublicationCheckpointStore(tmp_path/'short',request)
    long=ShopeePublicationCheckpointStore(long_root,request)
    assert len(str(long.path))>260
    short.record('DISPATCH',row);long.record('DISPATCH',row)
    assert short.load()==long.load()
    loaded=ShopeePublicationCheckpointStore(long_root,request)
    assert loaded.path==long.path and loaded.identity==long.identity
    assert loaded.public_progress()['targets'][0]['provider_identity_bound'] is True
    run={**loaded.identity,'revision':6,'platform_scope':['SHOPEE']}
    assert public_shopee_progress_for_run(long_root,run)==loaded.public_progress()
    loaded.record('READBACK',dict(target_label='shopee:PH',attempted=False,accepted=True,outcome='PROCESSING',item_id='47970054181',external_write_count=0))
    assert loaded.load()['events'][:1]==short.load()['events']
    assert loaded.load()['targets']['shopee:PH']['provider_item_id']=='47970054181'


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
