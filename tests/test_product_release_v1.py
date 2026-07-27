from __future__ import annotations

import sqlite3

import pytest

from modules.products import server as product_server
from modules.products import release_adapters
from domains.channel_operations.release_executor import (
    AdapterExecutionResult,
    AdapterRegistration,
)
from shared_platform import release_control, release_store
from shared_platform.release_store import ReleaseStore


def _dashboard() -> dict:
    targets = ["miaoshou:COMMON", "tiktok:MX"]
    approved_title = "Cute Dog PVC Wall Sticker 34 x 58 cm"
    copy_signature = "sha256:copy-facts-v1"
    return {
        "ok": True,
        "product": {
            "offer_id": "3828540231",
            "seller_sku_candidate": "0952",
            "revision": 41,
            "title": approved_title,
            "actual_product_approved": True,
            "actual_approval": {
                "approval_id": "product-approval:v1",
                "package_id": "product:3828540231:0952",
                "input_fingerprint": "facts-fingerprint",
            },
        },
        "content": {
            "approved": True,
            "approval_status": "approved",
            "package_id": "content:3828540231:v1",
            "strategy": "source_only",
            "images": [
                {
                    "position": 1,
                    "image_url": "https://assets.example/main.jpg",
                    "artifact_id": "source-1",
                    "audit_id": "review-1",
                    "asset_type": "source",
                    "decision_source": "review.image_actions",
                }
            ],
            "video_urls": ["https://assets.example/main.mp4"],
        },
        "publication_scope": {"selected_labels": targets},
        "pricing_review": {
            "status": "ready",
            "schema_version": "pricing-v1",
            "target_pricing": {
                "miaoshou:COMMON": {"status": "ready"},
                "tiktok:MX": {"status": "ready"},
            },
            "workbench_exchange_rates": {"MXN": 2.1},
            "shopee_exchange_rates": {},
            "ozon_exchange_rates": {},
        },
        "omnichannel_preview": {
            "available": True,
            "plan_id": "omnichannel:v1-test",
            "approval_summary": {"approval_scope_digest": "scope-digest"},
            "targets": [
                {
                    "channel": "miaoshou",
                    "site": "COMMON",
                    "adapter": "new_product_workbench_miaoshou_commit",
                    "preflights": [
                        {
                            "code": "audited_adapter_site",
                            "passed": True,
                            "detail": "legacy path found",
                        }
                    ],
                },
                {
                    "channel": "tiktok",
                    "site": "MX",
                    "adapter": "miaoshou_tiktok_publish",
                    "preflights": [
                        {
                            "code": "audited_adapter_site",
                            "passed": True,
                            "detail": "legacy path found",
                        }
                    ],
                },
            ],
            "blockers": [],
        },
        "actual_release_gate": {"ready": True, "blockers": []},
        "listing_copy": {
            "schema_version": "listing-copy-candidates-v4",
            "status": "adopted_in_product_facts",
            "provider": "toapi",
            "policy_version": "listing-copy-candidates-v4",
            "model": "gpt-5.4-mini-official",
            "input_signature": copy_signature,
            "current_input_signature": copy_signature,
            "semantic_master_en": approved_title,
            "shopee_description_en": "",
            "candidates": [
                {
                    "channel": "tiktok",
                    "site": "MX",
                    "language": "Spanish (Mexico)",
                    "limit": 255,
                    "title": "Pegatina de pared de perro PVC 34 x 58 cm",
                    "policy_check": "passed",
                    "created_at": "2026-07-27T01:00:00+00:00",
                }
            ],
        },
    }


def _request(view: dict, **extra) -> dict:
    plan = view["release_v1"]["plan"]
    return {
        "offer_id": "3828540231",
        "seller_sku": "0952",
        "publication_targets": ["miaoshou:COMMON", "tiktok:MX"],
        "plan_id": plan["plan_id"],
        "confirmation_token": plan["confirmation_token"],
        **extra,
    }


def _verified_common_write_result(offer_id: str) -> dict:
    return {
        "written_to_miaoshou": True,
        "verified": True,
        "offer_id": offer_id,
        "detail_id": offer_id,
        "checks": {"title": True, "images": True},
        "draft": {"imgUrls": ["https://assets.example/main.jpg"]},
    }


def _verified_common_plan_write(payload: dict) -> dict:
    return _verified_common_write_result(str(payload["product_id"]))


def _successor_dashboard(dashboard: dict) -> dict:
    dashboard["product"]["revision"] += 1
    dashboard["product"]["title"] = "Cute Dog PVC Wall Decal 34 x 58 cm"
    dashboard["listing_copy"]["semantic_master_en"] = dashboard["product"]["title"]
    dashboard["listing_copy"]["input_signature"] = "sha256:copy-facts-v2"
    dashboard["listing_copy"]["current_input_signature"] = "sha256:copy-facts-v2"
    dashboard["listing_copy"]["candidates"][0][
        "title"
    ] = "Adhesivo de pared de perro PVC 34 x 58 cm"
    dashboard["omnichannel_preview"]["plan_id"] = "omnichannel:v2-test"
    return dashboard


def _two_tiktok_dashboard() -> dict:
    dashboard = _dashboard()
    dashboard["publication_scope"]["selected_labels"] = [
        "miaoshou:COMMON",
        "tiktok:GB",
        "tiktok:MX",
    ]
    dashboard["pricing_review"]["target_pricing"]["tiktok:GB"] = {
        "status": "ready"
    }
    dashboard["omnichannel_preview"]["targets"].insert(
        1,
        {
            "channel": "tiktok",
            "site": "GB",
            "adapter": "miaoshou_tiktok_publish",
            "preflights": [
                {
                    "code": "audited_adapter_site",
                    "passed": True,
                    "detail": "legacy path found",
                }
            ],
        },
    )
    dashboard["listing_copy"]["candidates"].append(
        {
            "channel": "tiktok",
            "site": "GB",
            "language": "English (UK)",
            "limit": 255,
            "title": dashboard["product"]["title"],
            "policy_check": "passed",
        }
    )
    return dashboard


def _executable_registry(execute):
    return {
        "new_product_workbench_miaoshou_commit": AdapterRegistration(
            adapter_name="new_product_workbench_miaoshou_commit",
            execute=lambda _req: AdapterExecutionResult(True, True, "common"),
            consumes_unified_plan=True,
            validates_confirmation_token=True,
            preserves_idempotency_key=True,
            verifies_readback=True,
        ),
        "miaoshou_tiktok_publish": AdapterRegistration(
            adapter_name="miaoshou_tiktok_publish",
            execute=execute,
            consumes_unified_plan=True,
            validates_confirmation_token=True,
            preserves_idempotency_key=True,
            verifies_readback=True,
        ),
    }


def test_formal_v1_preview_is_write_free_and_reports_executable_registry(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)

    view = product_server._product_workspace_view(_dashboard())

    assert view["workspace_mode"] == "formal_v1"
    assert view["release_v1"]["eligible_for_plan_approval"] is True
    assert view["release_v1"]["plan"]["targets"] == [
        "miaoshou:COMMON",
        "tiktok:MX",
    ]
    assert view["release_v1"]["adapter_blockers"] == []
    assert not store.path.exists()


def test_formal_view_backfills_a_reviewable_shopee_description_for_v2_drafts():
    dashboard = _dashboard()
    dashboard["product"].update(
        {
            "title": "Cute Dog PVC Self-Adhesive Wall Sticker 34 x 58 cm",
            "package_cm": [58, 34, 0.02],
        }
    )
    dashboard["listing_copy"] = {
        "schema_version": "listing-title-candidates-v2",
        "candidates": [],
    }

    view = product_server._product_workspace_view(dashboard)

    description = view["listing_copy"]["shopee_description_en"]
    assert len(description) >= 500
    assert "PVC" in description
    assert "58" in description
    assert (
        view["listing_copy"]["shopee_description_source"]
        == "deterministic_verified_facts_fallback"
    )


def test_release_plan_approval_and_miaoshou_prepare_are_exact_and_durable(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: _dashboard(),
    )
    writes: list[str] = []

    def fake_write(payload: dict) -> dict:
        writes.append(str(payload["product_id"]))
        return _verified_common_plan_write(payload)

    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        fake_write,
    )
    view = product_server._product_workspace_view(_dashboard())
    status, approval = product_server._approve_release_plan_locally(
        _request(view, approved_by="Kyle", user_approved=True)
    )

    assert status == 200
    assert approval["external_writes_performed"] == []
    assert store.get_plan(view["release_v1"]["plan"]["plan_id"])["status"] == "APPROVED"

    status, prepared = product_server._prepare_miaoshou_release(
        _request(view, confirm_miaoshou_write=True)
    )

    assert status == 200
    assert writes == ["3828540231"]
    common = next(
        row
        for row in prepared["run"]["targets"]
        if row["target_label"] == "miaoshou:COMMON"
    )
    assert common["status"] == "SUCCEEDED"
    assert common["attempts"] == 1

    status, repeated = product_server._prepare_miaoshou_release(
        _request(view, confirm_miaoshou_write=True)
    )
    assert status == 200
    assert repeated["idempotent"] is True
    assert writes == ["3828540231"]


def test_successor_common_can_be_reused_by_readback_without_write(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    writes: list[str] = []
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        lambda payload: writes.append(str(payload["product_id"]))
        or _verified_common_plan_write(payload),
    )
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    predecessor_plan_id = request["plan_id"]
    store.supersede_plan(
        predecessor_plan_id,
        reason="locked title refresh before successor approval",
    )
    dashboard["listing_copy"][
        "superseded_release_plan_id"
    ] = predecessor_plan_id
    successor = _successor_dashboard(dashboard)
    successor_view = product_server._product_workspace_view(successor)
    request = _request(successor_view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    monkeypatch.setattr(
        release_adapters,
        "readback_miaoshou_common",
        lambda _payload: {
            "verified": True,
            "mode": "readback_reuse_no_write",
            "checks": {"title": True, "images": True},
            "field_diffs": {},
            "source": "fixture-readonly",
            "offer_id": "3828540231",
            "image_count": 1,
            "external_writes_performed": [],
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("write path must not run")
        ),
    )

    status, reused = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )

    assert status == 200
    assert reused["mode"] == "readback_reuse_no_write"
    assert reused["external_writes_performed"] == []
    common = next(
        row
        for row in reused["run"]["targets"]
        if row["target_label"] == "miaoshou:COMMON"
    )
    assert common["status"] == "SUCCEEDED"
    assert common["readback"]["evidence"]["mode"] == "readback_reuse_no_write"
    assert common["readback"]["evidence"]["predecessor"]["common_status"] == "SUCCEEDED"
    assert writes == ["3828540231"]


def test_common_readback_mismatch_creates_no_run_and_never_edits(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    predecessor_plan_id = request["plan_id"]
    store.supersede_plan(
        predecessor_plan_id,
        reason="locked title refresh before successor approval",
    )
    dashboard["listing_copy"][
        "superseded_release_plan_id"
    ] = predecessor_plan_id
    successor_view = product_server._product_workspace_view(
        _successor_dashboard(dashboard)
    )
    request = _request(successor_view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    monkeypatch.setattr(
        release_adapters,
        "readback_miaoshou_common",
        lambda _payload: {
            "verified": False,
            "checks": {"title": False},
            "field_diffs": {
                "title": {"expected": "approved", "actual": "other"}
            },
            "external_writes_performed": [],
        },
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("write path must not run")
        ),
    )

    status, mismatch = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )

    assert status == 409
    assert mismatch["external_writes_performed"] == []
    assert mismatch["field_diffs"]["title"]["actual"] == "other"
    approved_plan = store.get_plan(request["plan_id"])
    assert approved_plan is not None
    assert store.get_run(
        f"release-run:{approved_plan['payload_digest'][:24]}"
    ) is None
    immutable_writes = []
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        lambda payload: immutable_writes.append(payload)
        or _verified_common_plan_write(payload),
    )

    status, overwritten = product_server._prepare_miaoshou_release(
        {
            **request,
            "confirm_miaoshou_write": True,
            "confirm_miaoshou_overwrite": True,
        }
    )

    assert status == 200
    assert len(immutable_writes) == 1
    assert (
        immutable_writes[0]["product_facts"]["title"]
        == dashboard["product"]["title"]
    )
    assert overwritten["external_writes_performed"] == [
        "miaoshou:COMMON:draft_write_and_readback"
    ]


def test_publish_endpoint_executes_unified_adapter_and_persists_readback(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(_dashboard())
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    # External readback evidence advances the workbench document revision in
    # production. It must not invalidate otherwise identical approved facts.
    dashboard["product"]["revision"] += 1
    calls = []

    def execute(req):
        calls.append(req)
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=True,
            detail="verified",
            external_reference="mx-product-1",
            readback_evidence={
                "source": "fake-official-api",
                "verified": True,
                "title": "approved title",
            },
        )

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: {
            "new_product_workbench_miaoshou_commit": AdapterRegistration(
                adapter_name="new_product_workbench_miaoshou_commit",
                execute=lambda _req: AdapterExecutionResult(
                    True,
                    True,
                    "common",
                ),
                consumes_unified_plan=True,
                validates_confirmation_token=True,
                preserves_idempotency_key=True,
                verifies_readback=True,
            ),
            "miaoshou_tiktok_publish": AdapterRegistration(
                adapter_name="miaoshou_tiktok_publish",
                execute=execute,
                consumes_unified_plan=True,
                validates_confirmation_token=True,
                preserves_idempotency_key=True,
                verifies_readback=True,
            ),
        },
    )

    status, payload = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 200
    assert payload["external_writes_performed"] == ["tiktok:MX"]
    assert len(calls) == 1
    tiktok = next(
        row
        for row in payload["run"]["targets"]
        if row["target_label"] == "tiktok:MX"
    )
    assert tiktok["status"] == "SUCCEEDED"
    assert tiktok["attempts"] == 1
    assert tiktok["readback"]["evidence"]["source"] == "fake-official-api"


def test_api_less_publish_is_submitted_once_then_manually_verified(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    calls = []

    def accepted(req):
        calls.append(req.target_label)
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=False,
            detail="accepted; no authorised official readback",
            external_reference="detail-mx:shop-mx",
            readback_evidence={
                "source": "miaoshou_open_api",
                "accepted": True,
                "pre_submit_audit": {
                    "submission_fingerprint": "audit-mx",
                },
            },
            submission_accepted=True,
        )

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: {
            "new_product_workbench_miaoshou_commit": AdapterRegistration(
                adapter_name="new_product_workbench_miaoshou_commit",
                execute=lambda _req: AdapterExecutionResult(True, True, "common"),
                consumes_unified_plan=True,
                validates_confirmation_token=True,
                preserves_idempotency_key=True,
                verifies_readback=True,
            ),
            "miaoshou_tiktok_publish": AdapterRegistration(
                adapter_name="miaoshou_tiktok_publish",
                execute=accepted,
                consumes_unified_plan=True,
                validates_confirmation_token=True,
                preserves_idempotency_key=True,
                verifies_readback=True,
            ),
        },
    )

    status, first = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )
    assert status == 200
    assert calls == ["tiktok:MX"]
    assert first["awaiting_manual_verification"] is True
    target = next(
        row
        for row in first["run"]["targets"]
        if row["target_label"] == "tiktok:MX"
    )
    assert target["status"] == "SUBMITTED_UNVERIFIED"
    assert target["submission"]["evidence"]["pre_submit_audit"][
        "submission_fingerprint"
    ] == "audit-mx"

    status, repeated = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )
    assert status == 200
    assert calls == ["tiktok:MX"]
    assert repeated["external_writes_performed"] == []

    status, verified = product_server._manually_verify_release_target(
        {
            **request,
            "target_label": "tiktok:MX",
            "marketplace_product_id": "mx-product-123",
            "verified_by": "Kyle",
            "user_verified": True,
            "checks": {
                "identity_matches": True,
                "seller_sku_matches": True,
                "single_listing_for_sku": True,
                "title_matches": True,
                "price_matches": True,
                "images_match": True,
                "logistics_match": True,
            },
        }
    )
    assert status == 200
    assert verified["external_writes_performed"] == []
    assert verified["run"]["status"] == "COMPLETED_WITH_MANUAL_VERIFICATION"


def test_publish_common_blocker_creates_no_run(tmp_path, monkeypatch):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(lambda _req: None),
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200

    status, blocked = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 409
    assert "COMMON" in " ".join(blocked["blockers"])
    plan = store.get_plan(request["plan_id"])
    assert store.get_run(f"release-run:{plan['payload_digest'][:24]}") is None


def test_publish_registry_blocker_does_not_mutate_existing_run(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    prepared = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[1]
    before = prepared["run"]
    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: {
            "new_product_workbench_miaoshou_commit": _executable_registry(
                lambda _req: None
            )["new_product_workbench_miaoshou_commit"]
        },
    )

    status, blocked = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 409
    assert blocked["adapter_blockers"][0]["code"] == "adapter_not_registered"
    assert store.get_run(before["run_id"]) == before


def _run_two_target_drift_case(tmp_path, monkeypatch, *, mutation):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _two_tiktok_dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = {
        **_request(view),
        "publication_targets": list(
            dashboard["publication_scope"]["selected_labels"]
        ),
    }
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    calls: list[str] = []

    def execute(req):
        calls.append(req.target_label)
        mutation(dashboard, store, request, len(calls))
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=True,
            detail="verified",
            external_reference=f"external:{req.target_label}",
            readback_evidence={
                "source": "fixture-official-readback",
                "verified": True,
            },
        )

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(execute),
    )
    status, response = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )
    return store, request, calls, status, response


def test_plan_bound_drift_after_first_adapter_stops_second_before_begin(
    tmp_path,
    monkeypatch,
):
    def mutate(dashboard, _store, _request, call_count):
        if call_count == 1:
            dashboard["content"]["images"][0][
                "image_url"
            ] = "https://assets.example/drifted.jpg"

    store, request, calls, status, response = _run_two_target_drift_case(
        tmp_path,
        monkeypatch,
        mutation=mutate,
    )

    assert status == 409
    assert calls == ["tiktok:GB"]
    assert response["blocked_target"] == "tiktok:MX"
    run = response["run"]
    by_label = {row["target_label"]: row for row in run["targets"]}
    assert by_label["tiktok:GB"]["status"] == "SUCCEEDED"
    assert by_label["tiktok:MX"]["status"] == "PENDING"
    assert by_label["tiktok:MX"]["attempts"] == 0


def test_operational_revision_drift_after_first_adapter_allows_second(
    tmp_path,
    monkeypatch,
):
    def mutate(dashboard, _store, _request, _call_count):
        dashboard["product"]["revision"] += 1

    _store, _request_data, calls, status, response = _run_two_target_drift_case(
        tmp_path,
        monkeypatch,
        mutation=mutate,
    )

    assert status == 200
    assert calls == ["tiktok:GB", "tiktok:MX"]
    assert response["completed"] is True


def test_superseded_plan_after_first_success_stops_next_target(
    tmp_path,
    monkeypatch,
):
    def mutate(_dashboard, _store, _request, _call_count):
        return None

    original_record = ReleaseStore.record_target_success
    superseded = {"done": False}

    def record_then_supersede(self, run_id, target_label, **kwargs):
        result = original_record(
            self,
            run_id,
            target_label,
            **kwargs,
        )
        if target_label == "tiktok:GB" and not superseded["done"]:
            superseded["done"] = True
            plan_id = (self.get_run(run_id) or {})["plan_id"]
            self.supersede_plan(plan_id, reason="fixture plan drift")
        return result

    monkeypatch.setattr(
        ReleaseStore,
        "record_target_success",
        record_then_supersede,
    )
    _store, _request_data, calls, status, response = _run_two_target_drift_case(
        tmp_path,
        monkeypatch,
        mutation=mutate,
    )

    assert status == 409
    assert calls == ["tiktok:GB"]
    assert response["blocked_target"] == "tiktok:MX"
    assert "approved ReleasePlan" in " ".join(response["blockers"])


def test_successor_reuse_fails_closed_on_predecessor_external_id_mismatch(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        lambda payload: {
            **_verified_common_plan_write(payload),
            "offer_id": "999999",
        },
    )
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    predecessor_plan_id = request["plan_id"]
    store.supersede_plan(
        predecessor_plan_id,
        reason="locked title refresh before successor approval",
    )
    dashboard["listing_copy"][
        "superseded_release_plan_id"
    ] = predecessor_plan_id
    successor_view = product_server._product_workspace_view(
        _successor_dashboard(dashboard)
    )
    request = _request(successor_view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    readbacks: list[str] = []
    monkeypatch.setattr(
        release_adapters,
        "readback_miaoshou_common",
        lambda _payload: readbacks.append("called") or {"verified": True},
    )

    status, blocked = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )

    assert status == 409
    assert "external_id" in " ".join(blocked["blockers"])
    assert readbacks == []
    plan = store.get_plan(request["plan_id"])
    assert store.get_run(f"release-run:{plan['payload_digest'][:24]}") is None


def test_failed_detail_readback_keeps_external_write_receipt_and_never_retries(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    calls = []

    def changed_but_unverified(req):
        calls.append(req.target_label)
        raise release_adapters.MiaoshouDraftVerificationError(
            "save accepted but title readback differed",
            external_reference="3227308139:16265910",
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "save_accepted": True,
                "detail_id": 3227308139,
                "shop_id": 16265910,
                "checks": {"title": False, "images": True},
                "external_writes_performed": [
                    "miaoshou:tiktok_detail:update"
                ],
            },
        )

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(changed_but_unverified),
    )

    status, first = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 200
    assert calls == ["tiktok:MX"]
    assert first["external_writes_performed"] == [
        "miaoshou:tiktok_detail:update"
    ]
    target = next(
        row
        for row in first["run"]["targets"]
        if row["target_label"] == "tiktok:MX"
    )
    assert target["status"] == "FAILED"
    assert target["external_id"] == "3227308139:16265910"
    failure = target["latest_failure_evidence"]["evidence"]
    assert failure["save_accepted"] is True
    assert failure["checks"]["title"] is False
    assert target["readback"] is None

    status, blocked = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 409
    assert calls == ["tiktok:MX"]
    assert blocked["blocked_targets"] == ["tiktok:MX"]


def test_explicit_adapter_failure_persists_partial_write_evidence(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(
            lambda _req: AdapterExecutionResult(
                succeeded=False,
                readback_verified=False,
                detail="remote save accepted; exact readback differed",
                external_reference="3227308139:16265910",
                readback_evidence={
                    "verified": False,
                    "save_accepted": True,
                    "external_writes_performed": [
                        "miaoshou:tiktok_detail:update"
                    ],
                },
            )
        ),
    )

    status, response = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 200
    assert response["external_writes_performed"] == [
        "miaoshou:tiktok_detail:update"
    ]
    target = next(
        row
        for row in response["run"]["targets"]
        if row["target_label"] == "tiktok:MX"
    )
    assert target["status"] == "FAILED"
    assert target["latest_failure_evidence"]["evidence"][
        "save_accepted"
    ] is True


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("evidence_digest", "tampered", "digest"),
        ("verified_at", "", "receipt"),
    ],
)
def test_successor_reuse_rejects_tampered_predecessor_receipt(
    tmp_path,
    monkeypatch,
    column,
    value,
    message,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    predecessor_result = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[1]
    predecessor_plan_id = request["plan_id"]
    store.supersede_plan(
        predecessor_plan_id,
        reason="locked title refresh before successor approval",
    )
    dashboard["listing_copy"][
        "superseded_release_plan_id"
    ] = predecessor_plan_id
    successor_view = product_server._product_workspace_view(
        _successor_dashboard(dashboard)
    )
    request = _request(successor_view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            f"""
            UPDATE release_target_readbacks
            SET {column} = ?
            WHERE run_id = ? AND target_label = 'miaoshou:COMMON'
            """,
            (value, predecessor_result["run"]["run_id"]),
        )
        connection.commit()
    calls = []
    monkeypatch.setattr(
        release_adapters,
        "readback_miaoshou_common",
        lambda _payload: calls.append("read") or {"verified": True},
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        lambda _payload: calls.append("write") or {},
    )

    status, blocked = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )

    assert status == 409
    assert message in " ".join(blocked["blockers"])
    assert calls == []
    successor = store.get_plan(request["plan_id"])
    assert store.get_run(
        f"release-run:{successor['payload_digest'][:24]}"
    ) is None


def test_failure_receipt_store_error_stops_before_next_adapter(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _two_tiktok_dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = {
        **_request(view),
        "publication_targets": list(
            dashboard["publication_scope"]["selected_labels"]
        ),
    }
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    calls = []

    def execute(req):
        calls.append(req.target_label)
        raise RuntimeError("adapter fixture failed")

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(execute),
    )
    monkeypatch.setattr(
        store,
        "record_target_failure",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            release_store.ReleaseStoreError("fixture ledger unavailable")
        ),
    )

    status, blocked = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 409
    assert calls == ["tiktok:GB"]
    assert blocked["record_error"] == "fixture ledger unavailable"
    assert blocked["blocked_target"] == "tiktok:GB"
    run = blocked["run"]
    mx = next(
        row for row in run["targets"] if row["target_label"] == "tiktok:MX"
    )
    assert mx["status"] == "PENDING"
    assert mx["attempts"] == 0


@pytest.mark.parametrize(
    ("receipt_method", "result"),
    [
        (
            "record_target_success",
            AdapterExecutionResult(
                succeeded=True,
                readback_verified=True,
                detail="official readback verified",
                external_reference="mx-product-1",
                readback_evidence={
                    "source": "official-api",
                    "verified": True,
                    "external_writes_performed": [
                        "tiktok:MX:publish"
                    ],
                },
            ),
        ),
        (
            "record_target_submission",
            AdapterExecutionResult(
                succeeded=True,
                readback_verified=False,
                detail="submission accepted",
                external_reference="detail-mx:shop-mx",
                readback_evidence={
                    "source": "miaoshou_open_api",
                    "accepted": True,
                    "external_writes_performed": [
                        "miaoshou:tiktok_publish:submission"
                    ],
                },
                submission_accepted=True,
            ),
        ),
    ],
)
def test_external_success_with_terminal_receipt_failure_requires_reconciliation(
    tmp_path,
    monkeypatch,
    receipt_method,
    result,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    calls = []

    def execute(req):
        calls.append(req.target_label)
        return result

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(execute),
    )
    monkeypatch.setattr(
        store,
        receipt_method,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            release_store.ReleaseStoreError("terminal receipt unavailable")
        ),
    )

    status, uncertain = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 409
    assert calls == ["tiktok:MX"]
    assert uncertain["code"] == "reconciliation_required"
    assert uncertain["durable_state_uncertain"] is True
    assert uncertain["external_reference"] == result.external_reference
    assert uncertain["readback_evidence"] == dict(
        result.readback_evidence or {}
    )
    assert uncertain["external_writes_performed"]
    target = next(
        row
        for row in uncertain["run"]["targets"]
        if row["target_label"] == "tiktok:MX"
    )
    assert target["status"] == "FAILED"
    evidence = target["latest_failure_evidence"]["evidence"]
    assert evidence["durable_state_uncertain"] is True
    assert evidence["external_writes_performed"]

    repeated_status, _blocked = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )
    assert repeated_status == 409
    assert calls == ["tiktok:MX"]


def test_partial_failure_result_with_lost_failure_receipt_stays_running(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200
    calls = []

    def execute(req):
        calls.append(req.target_label)
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=False,
            detail="publish accepted but readback did not converge",
            external_reference="3227308139:16265910",
            readback_evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "external_writes_performed": [
                    "miaoshou:tiktok_publish:submission"
                ],
            },
        )

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(execute),
    )
    monkeypatch.setattr(
        store,
        "record_target_failure",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            release_store.ReleaseStoreError("failure receipt unavailable")
        ),
    )

    status, uncertain = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 409
    assert uncertain["code"] == "reconciliation_required"
    assert uncertain["external_reference"] == "3227308139:16265910"
    assert uncertain["run_record_error"] == "failure receipt unavailable"
    target = next(
        row
        for row in uncertain["run"]["targets"]
        if row["target_label"] == "tiktok:MX"
    )
    assert target["status"] == "RUNNING"

    before = store.get_run(uncertain["run"]["run_id"])
    repeated_status, repeated = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )
    after = store.get_run(uncertain["run"]["run_id"])

    assert repeated_status == 409
    assert repeated["code"] == "reconciliation_required"
    assert repeated["blocked_targets"] == ["tiktok:MX"]
    assert calls == ["tiktok:MX"]
    assert after == before


def test_running_target_blocks_publish_without_adapter_or_state_change(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    prepared = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[1]
    run_id = prepared["run"]["run_id"]
    store.begin_target(run_id, "tiktok:MX")
    before = store.get_run(run_id)
    calls = []
    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(
            lambda req: calls.append(req.target_label)
        ),
    )

    status, blocked = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 409
    assert blocked["code"] == "reconciliation_required"
    assert blocked["blocked_targets"] == ["tiktok:MX"]
    assert blocked["external_writes_performed"] == []
    assert calls == []
    assert store.get_run(run_id) == before


def test_common_success_with_lost_success_receipt_requires_reconciliation(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    writes = []

    def write(payload):
        writes.append(str(payload["product_id"]))
        return _verified_common_plan_write(payload)

    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        write,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    monkeypatch.setattr(
        store,
        "record_target_success",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            release_store.ReleaseStoreError("COMMON receipt unavailable")
        ),
    )

    status, uncertain = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )

    assert status == 502
    assert writes == ["3828540231"]
    assert uncertain["durable_state_uncertain"] is True
    assert uncertain["reconciliation_required"] is True
    assert uncertain["external_reference"] == "3828540231"
    assert uncertain["readback_evidence"]["verified"] is True
    assert uncertain["external_writes_performed"] == [
        "miaoshou:COMMON:immutable_plan_write"
    ]
    common = next(
        row
        for row in uncertain["run"]["targets"]
        if row["target_label"] == "miaoshou:COMMON"
    )
    assert common["status"] == "FAILED"
    assert common["latest_failure_evidence"]["evidence"][
        "durable_state_uncertain"
    ] is True

    repeated_status, _blocked = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )
    assert repeated_status == 409
    assert writes == ["3828540231"]


def test_common_unknown_after_dispatch_is_recorded_and_never_retried(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    calls = []

    def unknown_after_dispatch(payload):
        calls.append(str(payload["product_id"]))
        raise release_adapters.MiaoshouDraftVerificationError(
            "COMMON write response was lost",
            external_reference=str(payload["product_id"]),
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "save_accepted": False,
                "write_outcome": "unknown_after_dispatch",
                "external_writes_performed": [
                    "miaoshou:COMMON:immutable_plan_write"
                ],
            },
        )

    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        unknown_after_dispatch,
    )
    view = product_server._product_workspace_view(dashboard)
    request = _request(view)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200

    status, uncertain = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )

    assert status == 502
    assert uncertain["reconciliation_required"] is True
    assert uncertain["external_reference"] == "3828540231"
    common = next(
        row
        for row in uncertain["run"]["targets"]
        if row["target_label"] == "miaoshou:COMMON"
    )
    assert common["status"] == "FAILED"
    assert common["latest_failure_evidence"]["evidence"][
        "write_outcome"
    ] == "unknown_after_dispatch"

    repeated_status, _blocked = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )
    assert repeated_status == 409
    assert calls == ["3828540231"]
