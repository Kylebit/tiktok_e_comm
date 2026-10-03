"""Synthetic registered-R3 counterexamples; no real provider is called."""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from modules.products import server
from shared_platform import publication_r2_review as review
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import product_publication_runner as runner_module
from shared_platform.product_publication_runner import ProductPublicationRunner
from test_b4b_publication_preview import (
    reviewed_marketplace,
    seed_preexisting_marketplace_approval,
)
from test_product_publication_start_http import publication_start_server, _post


@pytest.mark.parametrize("path", [
    "/api/product-workspace/publish-tiktok",
    "/api/product-workspace/publish-shopee-global",
    "/api/product-workspace/publish-ozon",
])
def test_registered_r3_http_refuses_historical_snapshot_before_claim(
    publication_start_server, monkeypatch, path,
):
    base, snapshot, report_store, calls = publication_start_server
    identity = {"synthetic": "r2-identity"}
    monkeypatch.setattr(review, "has_registration", lambda offer, **_: offer == snapshot["offer_id"])
    monkeypatch.setattr(review, "review_view", lambda *_, **__: {
        "offer_id": snapshot["offer_id"],
        "r2_consumer": {"status": "PASSED", "identity": identity},
    })
    monkeypatch.setattr(bridge, "load_r2_documents", lambda *_: {})
    monkeypatch.setattr(bridge, "validate_r2_identity", lambda *_: identity)
    monkeypatch.setattr(
        runner_module, "claim_product_publication_request",
        lambda **_: pytest.fail("registered R3 HTTP reached durable claim"),
    )
    callbacks = []
    monkeypatch.setattr(server, "_launch_product_publication_background", callbacks.append)
    status, body = _post(base, path, {
        "offer_id": snapshot["offer_id"], "plan_id": snapshot["plan_id"],
    })
    assert status == 409, body
    assert body.get("code") == "REGISTERED_R3_PUBLISH_ADMISSION_BLOCKED"
    assert body.get("external_write_count") == 0
    assert report_store.get_report_by_run(run_id=body.get("run_id", "none")) is None
    assert calls == []
    assert callbacks == []


@pytest.mark.parametrize("registration_visible", [True, False])
def test_r3_plan_refuses_historical_approval_even_without_registry(
    tmp_path, monkeypatch, registration_visible,
):
    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    seed_preexisting_marketplace_approval(store, data, market)
    resumed_status, resumed = server._resume_r3_marketplace_stage(data)
    assert resumed_status == 200, resumed
    source = bridge.REPORTS_ROOT / data["offer_id"] / "release-candidates"
    target = (server._product_publication_report_store().reports_root.parent
              / "product-preparation" / data["offer_id"] / "release-candidates")
    shutil.copytree(source, target)
    registered = {"value": registration_visible}
    monkeypatch.setattr(review, "has_registration", lambda offer, **_: registered["value"])

    callbacks = []
    fake_executor_calls = []
    monkeypatch.setattr(server, "_launch_product_publication_background", callbacks.append)
    monkeypatch.setattr(
        runner_module, "claim_product_publication_request",
        lambda **_: pytest.fail("R3 plan reached durable claim"),
    )
    monkeypatch.setattr(server, "_product_publication_platform_executors", lambda: {
        "TIKTOK": lambda request: fake_executor_calls.append(request.platform) or {
            "schema_version": "product-publication-platform-result/v1",
            "platform": request.platform,
            "targets": [{"target_label": target, "status": "FAILED"}
                        for target in request.target_labels],
            "dispatch_attempted": False, "readback_completed": False,
            "external_write_count": 0, "requires_human_action": True,
        },
    })
    status, result = server._start_product_publication(
        {"offer_id": data["offer_id"], "plan_id": data["plan_id"]},
        platform="TIKTOK",
    )
    assert status == 409, result
    assert result.get("code") == "REGISTERED_R3_PUBLISH_ADMISSION_BLOCKED"
    assert callbacks == []
    assert fake_executor_calls == []
    assert io.mutations == 1  # The synthetic fixture's earlier COMMON write only.


@pytest.mark.parametrize("flip_during_domain", [False, True])
def test_worker_rechecks_when_legacy_offer_becomes_registered(
    publication_start_server, monkeypatch, flip_during_domain,
):
    _, snapshot, report_store, calls = publication_start_server
    registered = {"value": False}
    monkeypatch.setattr(review, "has_registration", lambda *_args, **_kwargs: registered["value"])
    monkeypatch.setattr(server, "_catalog_publication_sync", lambda: None)
    callbacks = []
    monkeypatch.setattr(server, "_launch_product_publication_background", callbacks.append)
    status, result = server._start_product_publication(
        {"offer_id": snapshot["offer_id"], "plan_id": snapshot["plan_id"]},
        platform="TIKTOK",
    )
    assert status == 202, result
    assert len(callbacks) == 1
    if flip_during_domain:
        from shared_platform import operations_domain_guard

        def register_during_domain(*_args, **_kwargs):
            registered["value"] = True
            return None

        monkeypatch.setattr(
            operations_domain_guard, "begin_snapshot_publication", register_during_domain,
        )
    else:
        registered["value"] = True
    callbacks[0]()
    run = server._product_publication_run_store().get_run_by_id(run_id=result["run_id"])
    assert run["state"] == "FAILED"
    assert run["failure_code"] == "REGISTERED_R3_PUBLISH_ADMISSION_BLOCKED"
    assert report_store.get_report_by_run(run_id=result["run_id"]) is None
    assert calls == []


def test_registered_r3_direct_runner_refuses_fake_provider(
    publication_start_server, monkeypatch,
):
    _, snapshot, report_store, _ = publication_start_server
    monkeypatch.setattr(review, "has_registration", lambda offer, **_: offer == snapshot["offer_id"])
    calls = []
    runner = ProductPublicationRunner(
        release_store=server._release_store(), report_store=report_store,
    )
    with pytest.raises(ValueError, match="REGISTERED_R3_PUBLISH_ADMISSION_BLOCKED"):
        runner.run(
            run_id="synthetic-direct-run", offer_id=snapshot["offer_id"],
            plan_id=snapshot["plan_id"], platform_scope=("TIKTOK",),
            platform_executors={"TIKTOK": calls.append},
        )
    assert calls == []


def test_registered_r3_direct_claim_cannot_create_run(monkeypatch):
    monkeypatch.setattr(review, "has_registration", lambda *_args, **_kwargs: True)
    with pytest.raises(ValueError, match="REGISTERED_R3_PUBLISH_ADMISSION_BLOCKED"):
        runner_module.claim_product_publication_request(
            prepared=SimpleNamespace(offer_id="3956742887", plan_id="synthetic-plan"), platform="TIKTOK",
            execution_identity={}, run_store=object(), report_store=object(),
            release_store=object(),
        )


def test_direct_runner_rechecks_after_preparation_before_fake_provider(
    publication_start_server, monkeypatch,
):
    _, snapshot, report_store, _ = publication_start_server
    registered = {"value": False}
    monkeypatch.setattr(review, "has_registration", lambda *_args, **_kwargs: registered["value"])
    original_prepare = runner_module.prepare_product_publication_run

    def prepare_then_register(**kwargs):
        prepared = original_prepare(**kwargs)
        registered["value"] = True
        return prepared

    monkeypatch.setattr(runner_module, "prepare_product_publication_run", prepare_then_register)
    calls = []
    runner = ProductPublicationRunner(
        release_store=server._release_store(), report_store=report_store,
    )
    with pytest.raises(ValueError, match="REGISTERED_R3_PUBLISH_ADMISSION_BLOCKED"):
        runner.run(
            run_id="synthetic-direct-race", offer_id=snapshot["offer_id"],
            plan_id=snapshot["plan_id"], platform_scope=("TIKTOK",),
            platform_executors={"TIKTOK": calls.append},
        )
    assert calls == []
