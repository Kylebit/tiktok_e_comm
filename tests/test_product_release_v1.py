from __future__ import annotations

from copy import deepcopy
import hashlib
import sqlite3

import pytest

from modules.products import server as product_server
from modules.products import release_adapters
from domains.channel_operations.release_executor import (
    AdapterExecutionResult,
    AdapterRegistration,
)
from domains.product_operations import (
    ModelSkuAssignment,
    SkuAssignment,
    finalize_new_source_sku_reservation,
    resolve_sku_lineage_reservation,
    resolve_source_product_identity,
)
from shared_platform import release_control, release_store
from shared_platform.release_store import ReleaseStore


def _dashboard() -> dict:
    targets = ["miaoshou:COMMON", "tiktok:MX"]
    approved_title = "Cute Dog PVC Wall Sticker 34 x 58 cm"
    copy_signature = "sha256:copy-facts-v1"
    source_inputs = {
        "collect_box": {
            "source_item_id": "986159122616",
            "source_item_code": "DOG-WALL-34X58",
        },
        "precollect": {
            "records": [{"source_id": "986159122616"}],
        },
        "source_record": {"source_id": "986159122616"},
        "source_authority": "1688",
    }
    source_resolution = resolve_source_product_identity(**source_inputs)
    assert source_resolution.ready is True
    assert source_resolution.identity is not None
    lineage_resolution = resolve_sku_lineage_reservation(
        source_identity=source_resolution.identity,
        predecessor_records=(),
    )
    assignment = SkuAssignment(
        seller_sku="0952",
        model_skus=(
            ModelSkuAssignment(
                variant_key="default",
                model_sku="0952",
            ),
        ),
    )
    finalized = finalize_new_source_sku_reservation(
        source_identity=source_resolution.identity,
        assignment=assignment,
    )
    assert finalized.ready is True
    assert finalized.reservation is not None
    lineage_payload = {
        **lineage_resolution.payload(),
        "assignment": assignment.payload(),
        "reservation": finalized.reservation.payload(),
    }
    return {
        "ok": True,
        "_source_identity_inputs": source_inputs,
        "_source_product_identity": source_resolution.payload(),
        "_sku_lineage": lineage_payload,
        "source_product_identity": {
            "schema_version": source_resolution.payload()["schema_version"],
            "status": source_resolution.status,
            "ready": source_resolution.ready,
            "blockers": [],
            "identity_digest": source_resolution.identity.identity_digest,
            "source_item_code": source_resolution.identity.source_item_code,
        },
        "sku_lineage": {
            "schema_version": lineage_payload["schema_version"],
            "status": lineage_payload["status"],
            "ready": lineage_payload["ready"],
            "lineage_mode": lineage_payload["lineage_mode"],
            "assignment": lineage_payload["assignment"],
            "reservation_digest": finalized.reservation.reservation_digest,
            "blockers": [],
        },
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
            "schema_version": "listing-copy-candidates-v6",
            "status": "adopted_in_product_facts",
            "provider": "toapi",
            "policy_version": "listing-copy-candidates-v6",
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


def test_release_dependency_policy_keeps_shopee_and_ozon_independent():
    statuses = {
        "miaoshou:COMMON": "SUCCEEDED",
        "tiktok:LH_MY": "FAILED",
        "tiktok:LH_PH": "FAILED",
        "shopee:MY": "PENDING",
        "ozon:RU": "PENDING",
    }

    assert product_server._release_target_dependencies(
        "tiktok:LH_MY",
        statuses,
    ) == ("miaoshou:COMMON",)
    assert product_server._release_target_dependencies(
        "shopee:MY",
        statuses,
    ) == ()
    assert product_server._release_target_dependencies(
        "ozon:RU",
        statuses,
    ) == ()
    assert product_server._release_target_dependencies(
        "tiktok:LH_MY",
        {},
    ) == ("miaoshou:COMMON",)


def test_existing_unsafe_tiktok_failure_does_not_block_pristine_targets(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = deepcopy(_dashboard())
    targets = [
        "miaoshou:COMMON",
        "tiktok:LH_MY",
        "shopee:MY",
        "ozon:RU",
    ]
    dashboard["publication_scope"]["selected_labels"] = targets
    dashboard["pricing_review"]["target_pricing"] = {
        "miaoshou:COMMON": {"status": "ready"},
        "tiktok:LH_MY": {"status": "ready"},
        "shopee:MY": {
            "status": "ready",
            "target_site": "MY",
            "derived_preview": {
                "global_original_price_cny": 40.95,
                "local_original_price": 33,
                "source_currency": "MYR",
                "exchange_rate_cny_per_local": 1.2409,
            },
        },
        "ozon:RU": {"status": "ready"},
    }
    dashboard["omnichannel_preview"]["targets"] = [
        {
            "channel": "miaoshou",
            "site": "COMMON",
            "adapter": "new_product_workbench_miaoshou_commit",
            "preflights": [{"code": "audited_adapter_site", "passed": True}],
        },
        {
            "channel": "tiktok",
            "site": "LH_MY",
            "adapter": "miaoshou_tiktok_publish",
            "preflights": [{"code": "audited_adapter_site", "passed": True}],
        },
        {
            "channel": "shopee",
            "site": "MY",
            "adapter": "shopee_publish",
            "preflights": [{"code": "audited_adapter_site", "passed": True}],
        },
        {
            "channel": "ozon",
            "site": "RU",
            "adapter": "ozon_publish",
            "preflights": [{"code": "audited_adapter_site", "passed": True}],
        },
    ]
    dashboard["listing_copy"]["shopee_description_en"] = (
        "Approved factual English description. " * 25
    )
    dashboard["listing_copy"]["candidates"] = [
        {
            "channel": "tiktok",
            "site": "MY",
            "language": "English",
            "limit": 255,
            "title": dashboard["product"]["title"],
            "policy_check": "passed",
        },
        {
            "channel": "shopee",
            "site": "CNSC",
            "language": "English",
            "limit": 120,
            "title": dashboard["product"]["title"],
            "policy_check": "passed",
        },
        {
            "channel": "ozon",
            "site": "RU",
            "language": "Russian",
            "limit": 200,
            "title": "Approved Ozon title",
            "policy_check": "passed",
        },
    ]
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
    calls: list[str] = []

    def execute(request):
        calls.append(request.target_label)
        if request.target_label == "tiktok:LH_MY":
            return AdapterExecutionResult(
                succeeded=False,
                readback_verified=False,
                detail="TikTok draft conflict before publish submission",
                external_reference=None,
                readback_evidence={
                    "verified": False,
                    "pre_submit_failure": True,
                    "external_writes_performed": [],
                },
            )
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=True,
            detail=f"{request.channel} official readback verified",
            external_reference=(
                "57115039489"
                if request.target_label == "shopee:MY"
                else "ozon-product-1"
            ),
            readback_evidence={
                "source": f"official_{request.channel}_api",
                "verified": True,
            },
        )

    def registration(name):
        return AdapterRegistration(
            adapter_name=name,
            execute=execute,
            consumes_unified_plan=True,
            validates_confirmation_token=True,
            preserves_idempotency_key=True,
            verifies_readback=True,
        )

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: {
            "new_product_workbench_miaoshou_commit": registration(
                "new_product_workbench_miaoshou_commit"
            ),
            "miaoshou_tiktok_publish": registration(
                "miaoshou_tiktok_publish"
            ),
            "shopee_publish": registration("shopee_publish"),
            "ozon_publish": registration("ozon_publish"),
        },
    )

    _approve_shopee_global_fixture(dashboard, monkeypatch)
    view = product_server._product_workspace_view(dashboard)
    request = _request(view, publication_targets=targets)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    prepared_status, prepared = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )
    assert prepared_status == 200
    store.begin_target(prepared["run"]["run_id"], "tiktok:LH_MY")
    store.record_target_failure(
        prepared["run"]["run_id"],
        "tiktok:LH_MY",
        error="TikTok draft outcome requires reconciliation",
        failure_evidence={
            "verified": False,
            "durable_state_uncertain": True,
            "external_writes_performed": ["miaoshou:tiktok_detail:update"],
        },
    )

    status, response = product_server._publish_selected_release(
        {**request, "confirm_publish": True}
    )

    assert status == 200
    assert calls == ["shopee:MY", "ozon:RU"]
    states = {
        row["target_label"]: row["status"]
        for row in response["run"]["targets"]
    }
    assert states["tiktok:LH_MY"] == "FAILED"
    assert states["shopee:MY"] == "SUCCEEDED"
    assert states["ozon:RU"] == "SUCCEEDED"


def _common_mismatch_readback(
    *,
    field: str = "title",
    unknown_field: bool = False,
) -> dict:
    comparison = {
        "title": {"expected": "approved title", "actual": "existing title"},
        "seller_sku": {"expected": "0952", "actual": "0952"},
        "selected_sku_keys": {"expected": ["34x58"], "actual": ["34x58"]},
        "selected_sku_numbers": {"expected": ["0952"], "actual": ["0952"]},
        "spec_labels": {"expected": ["34x58"], "actual": ["34x58"]},
        "weight": {"expected": 0.02, "actual": 0.02},
        "dimensions": {"expected": [58, 34, 0.02], "actual": [58, 34, 0.02]},
        "images": {"expected": ["approved-image"], "actual": ["existing-image"]},
        "description_notes": {"expected": "approved", "actual": "existing"},
        "description_image_count": {"expected": 1, "actual": 1},
        "video_action": {"expected": "approved-video", "actual": "existing-video"},
    }
    changed_field = "future_field" if unknown_field else field
    return {
        "verified": False,
        "source": "miaoshou_common_readonly_detail",
        "readback_ambiguous": False,
        "existing_detail_digest": "sha256:existing-detail-v1",
        "checks": {changed_field: False},
        "field_diffs": {
            changed_field: {
                "expected": "approved-sensitive-value",
                "actual": "existing-sensitive-value",
            }
        },
        "_comparison": comparison,
        "external_writes_performed": [],
    }


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


def test_release_plan_rejection_returns_fresh_dashboard_and_exact_blocker(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    dashboard["product"]["actual_product_approved"] = False
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    view = product_server._product_workspace_view(dashboard)

    status, payload = product_server._approve_release_plan_locally(
        _request(view, approved_by="Kyle", user_approved=True)
    )

    assert status == 409
    assert payload["error_code"] == "release_plan_not_ready"
    assert payload["error"] == "商品事实尚未由 Kyle 批准并锁定"
    assert payload["blockers"] == ["商品事实尚未由 Kyle 批准并锁定"]
    assert payload["external_writes_performed"] == []
    assert payload["dashboard"]["release_v1"]["eligible_for_plan_approval"] is False
    assert payload["dashboard"]["product"]["actual_product_approved"] is False
    assert store.active_plan_for_product("3828540231") is None


def _approved_successor_context(tmp_path, monkeypatch, *, readback=None):
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
    dashboard["listing_copy"]["superseded_release_plan_id"] = predecessor_plan_id
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
        lambda _payload: readback or _common_mismatch_readback(),
    )
    plan = store.get_plan(request["plan_id"])
    return store, dashboard, request, plan


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


def _single_shopee_dashboard() -> dict:
    dashboard = _dashboard()
    targets = ["miaoshou:COMMON", "shopee:PH"]
    dashboard["publication_scope"]["selected_labels"] = targets
    dashboard["pricing_review"]["target_pricing"] = {
        "miaoshou:COMMON": {"status": "ready"},
        "shopee:PH": {
            "status": "ready",
            "target_site": "PH",
            "derived_preview": {
                "global_original_price_cny": 40.95,
                "local_original_price": 329,
                "source_currency": "PHP",
                "exchange_rate_cny_per_local": 0.1245,
            },
        },
    }
    dashboard["omnichannel_preview"]["targets"] = [
        dashboard["omnichannel_preview"]["targets"][0],
        {
            "channel": "shopee",
            "site": "PH",
            "adapter": "shopee_cnsc_publish",
            "preflights": [
                {
                    "code": "audited_adapter_site",
                    "passed": True,
                    "detail": "governed official readback adapter",
                }
            ],
        },
    ]
    dashboard["listing_copy"]["shopee_description_en"] = (
        "Approved factual English product description. " * 25
    )
    dashboard["listing_copy"]["candidates"] = [
        {
            "channel": "shopee",
            "site": "CNSC",
            "language": "English",
            "limit": 120,
            "title": dashboard["product"]["title"],
            "policy_check": "passed",
        }
    ]
    return dashboard


def _approve_shopee_global_fixture(dashboard, monkeypatch):
    from shared_platform.shopee_global_plan import (
        build_shopee_global_plan_candidate,
    )
    from tests.test_shopee_global_plan import _base_args

    dashboard["product"].setdefault("weight_kg", 0.02)
    dashboard["product"].setdefault("package_cm", [58, 34, 0.02])
    payload, _blockers = product_server._release_plan_payload_from_dashboard(
        dashboard,
        bind_shopee_global_plan=False,
    )
    seed = product_server._shopee_global_plan_seed(payload)
    args = _base_args()
    args.update(
        {key: value for key, value in seed.items() if key != "targets"}
    )
    args["selected_image_positions"] = list(
        range(1, len(seed["ordered_approved_images"]) + 1)
    )
    model_sku = payload["sku_lineage"]["assignment"]["model_skus"][0][
        "model_sku"
    ]
    args["variations"] = [
        {
            "name": "Model",
            "option_list": [
                {
                    "option": "Default",
                    "approved_image_position": 1,
                }
            ],
        }
    ]
    args["models"] = [
        {
            "global_model_sku": model_sku,
            "tier_index": [0],
            "original_price_cny": seed["target_pricing"][
                "global_original_price"
            ],
            "seller_stock_quantity": args["seller_stock"]["quantity"],
        }
    ]
    candidate = build_shopee_global_plan_candidate(**args)
    assert candidate.status == "READY", candidate.blocker_codes
    monkeypatch.setattr(
        product_server,
        "_observe_shopee_global_plan_candidate",
        lambda _payload: candidate,
    )
    status, _response = product_server._approve_shopee_global_plan_locally(
        {
            "offer_id": payload["product_id"],
            "expected_product_revision": payload["product_revision"],
            "expected_candidate_digest": candidate.candidate_digest,
            "approved_by": "Kyle",
            "confirm_approved_shopee_global_plan": True,
        }
    )
    assert status == 200


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


def test_successor_one_click_never_reopens_predecessor_submission(
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
    predecessor_view = product_server._product_workspace_view(dashboard)
    predecessor_request = _request(predecessor_view)
    assert product_server._approve_release_plan_locally(
        {
            **predecessor_request,
            "approved_by": "Kyle",
            "user_approved": True,
        }
    )[0] == 200
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        _verified_common_plan_write,
    )
    assert product_server._prepare_miaoshou_release(
        {**predecessor_request, "confirm_miaoshou_write": True}
    )[0] == 200

    predecessor_calls: list[str] = []

    def accepted(req):
        predecessor_calls.append(req.target_label)
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=False,
            detail="accepted; API-less target requires manual review",
            external_reference="submitted-mx-1",
            readback_evidence={
                "source": "fixture",
                "accepted": True,
                "submission_accepted": True,
            },
            submission_accepted=True,
        )

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(accepted),
    )
    assert product_server._publish_selected_release(
        {**predecessor_request, "confirm_publish": True}
    )[0] == 200
    assert predecessor_calls == ["tiktok:MX"]

    predecessor_plan_id = predecessor_request["plan_id"]
    store.supersede_plan(
        predecessor_plan_id,
        reason="approved listing copy changed",
    )
    dashboard["listing_copy"][
        "superseded_release_plan_id"
    ] = predecessor_plan_id
    dashboard = _successor_dashboard(dashboard)
    successor_view = product_server._product_workspace_view(dashboard)
    successor_request = _request(successor_view)
    assert product_server._approve_release_plan_locally(
        {
            **successor_request,
            "approved_by": "Kyle",
            "user_approved": True,
        }
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
    assert product_server._prepare_miaoshou_release(
        {**successor_request, "confirm_miaoshou_write": True}
    )[0] == 200
    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(
            lambda _req: (_ for _ in ()).throw(
                AssertionError("successor must not resubmit predecessor target")
            )
        ),
    )

    status, blocked = product_server._publish_selected_release(
        {**successor_request, "confirm_publish": True}
    )

    assert status == 409
    assert blocked["code"] == "no_runnable_release_targets"
    action = next(
        row
        for row in blocked["target_recovery_actions"]
        if row["target_label"] == "tiktok:MX"
    )
    assert action["runnable"] is False
    assert action["action_kind"] == "READONLY_RECONCILE"
    assert (
        action["reason_code"]
        == "predecessor_external_outcome_requires_resolution"
    )
    successor_run = store.get_run(
        f"release-run:{store.get_plan(successor_request['plan_id'])['payload_digest'][:24]}"
    )
    successor_target = next(
        row
        for row in successor_run["targets"]
        if row["target_label"] == "tiktok:MX"
    )
    assert successor_target["status"] == "PENDING"
    assert successor_target["attempts"] == 0
    assert predecessor_calls == ["tiktok:MX"]


def test_successor_one_click_uses_only_governed_shopee_recovery(
    tmp_path,
    monkeypatch,
):
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _single_shopee_dashboard()
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
    calls: list[str] = []

    def execute(request):
        calls.append(request.target_label)
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=True,
            detail="official identity and readback verified",
            external_reference="shopee-item-1",
            readback_evidence={
                "source": "official_shopee_partner_api",
                "verified": True,
                "external_writes_performed": [],
            },
        )

    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _shopee_recovery_registry(execute),
    )
    targets = ["miaoshou:COMMON", "shopee:PH"]
    _approve_shopee_global_fixture(dashboard, monkeypatch)
    predecessor_view = product_server._product_workspace_view(dashboard)
    predecessor_request = _request(
        predecessor_view,
        publication_targets=targets,
    )
    assert product_server._approve_release_plan_locally(
        {
            **predecessor_request,
            "approved_by": "Kyle",
            "user_approved": True,
        }
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**predecessor_request, "confirm_miaoshou_write": True}
    )[0] == 200
    assert product_server._publish_selected_release(
        {**predecessor_request, "confirm_publish": True}
    )[0] == 200
    assert calls == ["shopee:PH"]

    predecessor_plan_id = predecessor_request["plan_id"]
    store.supersede_plan(
        predecessor_plan_id,
        reason="Kyle approved corrected immutable listing copy",
    )
    dashboard["listing_copy"][
        "superseded_release_plan_id"
    ] = predecessor_plan_id
    dashboard = _successor_dashboard(dashboard)
    dashboard["listing_copy"]["candidates"][0]["title"] = dashboard[
        "product"
    ]["title"]
    _approve_shopee_global_fixture(dashboard, monkeypatch)
    successor_view = product_server._product_workspace_view(dashboard)
    successor_request = _request(
        successor_view,
        publication_targets=targets,
    )
    assert product_server._approve_release_plan_locally(
        {
            **successor_request,
            "approved_by": "Kyle",
            "user_approved": True,
        }
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
    assert product_server._prepare_miaoshou_release(
        {**successor_request, "confirm_miaoshou_write": True}
    )[0] == 200

    successor_view = product_server._product_workspace_view(dashboard)
    release_v1 = successor_view["release_v1"]
    assert release_v1["target_recovery_actions"] == []
    assert release_v1["canonical_next_action"] == {
        "action": "start_collectbox_action",
        "target_focus": None,
    }

    status, result = product_server._publish_selected_release(
        {**successor_request, "confirm_publish": True}
    )

    assert status == 200
    assert calls == ["shopee:PH", "shopee:PH"]
    target = next(
        row
        for row in result["run"]["targets"]
        if row["target_label"] == "shopee:PH"
    )
    assert target["status"] == "SUCCEEDED"
    assert target["attempts"] == 1


def test_predecessor_evidence_fold_cannot_be_bypassed_by_empty_successor():
    root_digest = "a" * 64
    middle_digest = "b" * 64

    class Store:
        def predecessor_plan_for(self, plan_id):
            return {
                "successor": {
                    "plan_id": "middle",
                    "payload_digest": middle_digest,
                },
                "middle": {
                    "plan_id": "root",
                    "payload_digest": root_digest,
                },
            }.get(plan_id)

        def get_run(self, run_id):
            if run_id == f"release-run:{middle_digest[:24]}":
                return {
                    "targets": [
                        {
                            "target_label": "tiktok:GB",
                            "status": "PENDING",
                            "attempts": 0,
                        }
                    ]
                }
            if run_id == f"release-run:{root_digest[:24]}":
                return {
                    "targets": [
                        {
                            "target_label": "tiktok:GB",
                            "status": "SUBMITTED_UNVERIFIED",
                            "attempts": 1,
                            "external_id": "submitted-gb-1",
                        }
                    ]
                }
            return None

    folded = product_server._release_predecessor_evidence_run(
        Store(),
        {"plan_id": "successor"},
    )

    assert folded is not None
    assert folded["targets"] == [
        {
            "target_label": "tiktok:GB",
            "status": "SUBMITTED_UNVERIFIED",
            "attempts": 1,
            "external_id": "submitted-gb-1",
        }
    ]


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
        lambda _payload: _common_mismatch_readback(),
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
    assert "field_diffs" not in mismatch
    review = mismatch["common_overwrite_review"]
    assert review["overwrite_allowed"] is True
    assert review["changed_fields"] == ["title"]
    assert "existing-sensitive-value" not in str(mismatch)
    assert mismatch["dashboard"]["release_v1"][
        "common_overwrite_review"
    ]["review_digest"] == review["review_digest"]
    approved_plan = store.get_plan(request["plan_id"])
    assert approved_plan is not None
    assert store.get_run(
        f"release-run:{approved_plan['payload_digest'][:24]}"
    ) is None
    immutable_writes = []
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        lambda payload, *, overwrite_guard: immutable_writes.append(
            (payload, overwrite_guard)
        )
        or _verified_common_plan_write(payload),
    )

    status, overwritten = product_server._prepare_miaoshou_release(
        {
            **request,
            "confirm_miaoshou_write": True,
            "confirm_miaoshou_overwrite": True,
            "approved_by": "Kyle",
            "expected_revision": approved_plan["payload"]["product_revision"],
            "payload_digest": approved_plan["payload_digest"],
            "overwrite_review_digest": review["review_digest"],
        }
    )

    assert status == 200
    assert len(immutable_writes) == 1
    assert (
        immutable_writes[0][0]["product_facts"]["title"]
        == dashboard["product"]["title"]
    )
    assert immutable_writes[0][1]["identity_exact"] is True
    assert overwritten["external_writes_performed"] == [
        "miaoshou:COMMON:draft_write_and_readback"
    ]
    assert store.get_common_overwrite_review(request["plan_id"])[
        "status"
    ] == "RESOLVED"


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("confirmation_token", "wrong-token"),
        ("approved_by", "NotKyle"),
        ("expected_revision", -1),
        ("payload_digest", "wrong-digest"),
    ],
)
def test_common_overwrite_rejects_wrong_exact_contract_without_write(
    tmp_path,
    monkeypatch,
    bad_field,
    bad_value,
):
    store, _dashboard_value, request, plan = _approved_successor_context(
        tmp_path,
        monkeypatch,
    )
    status, mismatch = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )
    assert status == 409
    writes = []
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        lambda *_args, **_kwargs: writes.append("write"),
    )
    overwrite_request = {
        **request,
        "confirm_miaoshou_write": True,
        "confirm_miaoshou_overwrite": True,
        "approved_by": "Kyle",
        "expected_revision": plan["payload"]["product_revision"],
        "payload_digest": plan["payload_digest"],
        "overwrite_review_digest": mismatch["common_overwrite_review"][
            "review_digest"
        ],
        bad_field: bad_value,
    }

    status, rejected = product_server._prepare_miaoshou_release(
        overwrite_request
    )

    assert status == 409
    assert rejected["external_writes_performed"] == []
    assert writes == []
    assert store.get_run(
        f"release-run:{plan['payload_digest'][:24]}"
    ) is None


@pytest.mark.parametrize(
    "readback",
    [
        _common_mismatch_readback(field="seller_sku"),
        _common_mismatch_readback(field="selected_sku_keys"),
        _common_mismatch_readback(field="common_id"),
        _common_mismatch_readback(field="source_identity"),
        _common_mismatch_readback(field="detail_binding"),
        _common_mismatch_readback(unknown_field=True),
    ],
)
def test_common_overwrite_blocks_identity_or_unknown_diff_without_write(
    tmp_path,
    monkeypatch,
    readback,
):
    store, _dashboard_value, request, plan = _approved_successor_context(
        tmp_path,
        monkeypatch,
        readback=readback,
    )
    writes = []
    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        lambda *_args, **_kwargs: writes.append("write"),
    )

    status, rejected = product_server._prepare_miaoshou_release(
        {
            **request,
            "confirm_miaoshou_write": True,
            "confirm_miaoshou_overwrite": True,
            "approved_by": "Kyle",
            "expected_revision": plan["payload"]["product_revision"],
            "payload_digest": plan["payload_digest"],
        }
    )

    assert status == 409
    assert rejected["common_overwrite_review"]["overwrite_allowed"] is False
    assert rejected["external_writes_performed"] == []
    assert writes == []
    assert store.get_run(
        f"release-run:{plan['payload_digest'][:24]}"
    ) is None


def test_common_overwrite_network_ambiguity_keeps_reconciliation_evidence(
    tmp_path,
    monkeypatch,
):
    store, _dashboard_value, request, plan = _approved_successor_context(
        tmp_path,
        monkeypatch,
    )
    status, mismatch = product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )
    assert status == 409
    calls = []

    def ambiguous_write(_payload, *, overwrite_guard):
        calls.append(overwrite_guard["review_digest"])
        raise release_adapters.MiaoshouDraftVerificationError(
            "socket closed after COMMON edit dispatch",
            external_reference="3828540231",
            evidence={
                "write_outcome": "unknown_after_dispatch",
                "durable_state_uncertain": True,
                "external_writes_performed": [
                    "miaoshou:COMMON:immutable_plan_write"
                ],
            },
        )

    monkeypatch.setattr(
        release_adapters,
        "write_miaoshou_common_from_plan",
        ambiguous_write,
    )

    status, ambiguous = product_server._prepare_miaoshou_release(
        {
            **request,
            "confirm_miaoshou_write": True,
            "confirm_miaoshou_overwrite": True,
            "approved_by": "Kyle",
            "expected_revision": plan["payload"]["product_revision"],
            "payload_digest": plan["payload_digest"],
            "overwrite_review_digest": mismatch["common_overwrite_review"][
                "review_digest"
            ],
        }
    )

    assert status == 502
    assert len(calls) == 1
    assert ambiguous["reconciliation_required"] is True
    assert ambiguous["durable_state_uncertain"] is True
    assert ambiguous["external_writes_performed"] == [
        "miaoshou:COMMON:immutable_plan_write"
    ]
    assert ambiguous["dashboard"]["release_v1"]["run"]["status"] == "FAILED"
    assert ambiguous["dashboard"]["release_v1"][
        "common_overwrite_review"
    ]["status"] == "MISMATCH"
    common = next(
        row
        for row in store.get_run(
            f"release-run:{plan['payload_digest'][:24]}"
        )["targets"]
        if row["target_label"] == "miaoshou:COMMON"
    )
    assert common["status"] == "FAILED"
    assert common["latest_failure_evidence"]["evidence"][
        "write_outcome"
    ] == "unknown_after_dispatch"


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


def test_canonical_common_readback_supersedes_stale_pre_common_image_gate(
    tmp_path,
    monkeypatch,
):
    """A verified current-plan COMMON receipt is the post-sync authority."""

    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _dashboard()
    dashboard["actual_release_gate"] = {
        "ready": False,
        "blockers": ["The previous 11-image Miaoshou write is stale."],
    }


def _shopee_recovery_registry(execute):
    return {
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
        "shopee_cnsc_publish": AdapterRegistration(
            adapter_name="shopee_cnsc_publish",
            execute=execute,
            consumes_unified_plan=True,
            validates_confirmation_token=True,
            preserves_idempotency_key=True,
            verifies_readback=True,
            predecessor_recovery_mode=(
                "OFFICIAL_READBACK_THEN_BOUNDED_WRITE"
            ),
        ),
    }
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
    monkeypatch.setattr(
        release_adapters,
        "production_adapter_registry",
        lambda: _executable_registry(
            lambda _request: AdapterExecutionResult(
                succeeded=True,
                readback_verified=True,
                detail="verified",
                external_reference="mx-product-1",
                readback_evidence={"source": "fake-official-api", "verified": True},
            )
        ),
    )
    initial = product_server._product_workspace_view(dashboard)
    request = _request(initial)
    assert product_server._approve_release_plan_locally(
        {**request, "approved_by": "Kyle", "user_approved": True}
    )[0] == 200
    assert product_server._prepare_miaoshou_release(
        {**request, "confirm_miaoshou_write": True}
    )[0] == 200

    refreshed = product_server._product_workspace_view(dashboard)

    assert refreshed["actual_release_gate"]["ready"] is False
    assert refreshed["release_v1"]["canonical_common_ready"] is True
    assert refreshed["release_v1"]["common_evidence_blockers"] == []
    assert (
        refreshed["release_v1"]["release_preflight_authority"]
        == "canonical_common_readback"
    )
    assert refreshed["release_v1"]["publish_ready"] is True
    assert (
        refreshed["workflow_next_action"]["code"]
        == "publish_selected_targets"
    )
    gate, failure = product_server._release_execution_readonly_gate(
        request,
        store=store,
    )
    assert failure is None
    assert gate is not None


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

    from shared_platform.oneclick_release_controlplane import (
        OneClickReleaseStore,
    )

    close_calls = []
    job_reads = []

    def oneclick_job_once(_self, **_kwargs):
        job_reads.append(True)
        return (
            {
                "job_id": "oneclick-job:test",
                "targets": [
                    {
                        "target_label": "tiktok:MX",
                        "status": "SUBMITTED_UNVERIFIED",
                    }
                ],
            }
            if len(job_reads) == 1
            else None
        )

    monkeypatch.setattr(
        OneClickReleaseStore,
        "get_job",
        oneclick_job_once,
    )

    def close_oneclick(_self, **kwargs):
        close_calls.append(kwargs)
        store.record_manual_verification(
            kwargs["run_id"],
            kwargs["target_label"],
            verified_by=kwargs["verified_by"],
            user_verified=kwargs["user_verified"],
            verification_evidence=kwargs["verification_evidence"],
        )
        return {
            "idempotent": False,
            "external_writes_performed": [],
        }

    monkeypatch.setattr(
        OneClickReleaseStore,
        "record_manual_acceptance",
        close_oneclick,
    )
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
    assert len(close_calls) == 1
    assert close_calls[0]["target_label"] == "tiktok:MX"
    assert close_calls[0]["verified_by"] == "Kyle"


def test_shopee_verified_warning_manual_body_is_distinct_and_digest_bound(
    tmp_path,
    monkeypatch,
):
    from shared_platform.oneclick_release_controlplane import (
        OneClickReleaseStore,
    )

    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    dashboard = _single_shopee_dashboard()
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: dashboard,
    )
    _approve_shopee_global_fixture(dashboard, monkeypatch)
    view = product_server._product_workspace_view(dashboard)
    request = {
        **_request(view),
        "publication_targets": [
            "miaoshou:COMMON",
            "shopee:PH",
        ],
    }
    assert product_server._approve_release_plan_locally(
        {
            **request,
            "approved_by": "Kyle",
            "user_approved": True,
        }
    )[0] == 200
    plan = store.get_plan(request["plan_id"])
    store.start_run(plan["plan_id"])
    observation_digest = "a" * 64
    result_digest = "b" * 64
    outcome_digest = "c" * 64
    calls = []

    monkeypatch.setattr(
        OneClickReleaseStore,
        "get_job",
        lambda _self, **_kwargs: {
            "job_id": "oneclick-job:shopee-warning",
            "phase": "WAITING_MANUAL_ACCEPTANCE",
            "runnable_target_count": 0,
            "shared_controls": [],
            "targets": [
                {
                    "target_label": "shopee:PH",
                    "status": "SUCCEEDED_MANUAL_REVIEW",
                    "result": {
                        "evidence_digest": result_digest,
                        "observation_digests": [observation_digest],
                    },
                    "outcome_receipt": {
                        "receipt_digest": outcome_digest,
                    },
                }
            ],
        },
    )

    def accept(_self, **kwargs):
        calls.append(kwargs)
        return {
            "idempotent": len(calls) > 1,
            "external_writes_performed": [],
        }

    monkeypatch.setattr(
        OneClickReleaseStore,
        "record_manual_acceptance",
        accept,
    )
    body = {
        **request,
        "target_label": "shopee:PH",
        "verified_by": "Kyle",
        "user_verified": True,
        "manual_review_accepted": True,
        "observation_evidence_digest": observation_digest,
    }
    status, response = product_server._manually_verify_release_target(body)
    assert status == 200
    assert response["external_writes_performed"] == []
    assert calls[0]["verification_evidence"] == {
        "source": "kyle_verified_shopee_observation_review",
        "manual_review_accepted": True,
        "observation_evidence_digest": observation_digest,
        "job_identity_digest": hashlib.sha256(
            b"oneclick-job:shopee-warning"
        ).hexdigest(),
        "result_evidence_digest": result_digest,
        "readback_evidence_digest": result_digest,
        "outcome_receipt_digest": outcome_digest,
        "observation_evidence_digests": [observation_digest],
    }
    status, _response = product_server._manually_verify_release_target(
        {**body, "observation_evidence_digest": "d" * 64}
    )
    assert status == 409
    status, _response = product_server._manually_verify_release_target(
        {**body, "marketplace_product_id": "must-not-be-used"}
    )
    assert status == 400
    status, _response = product_server._manually_verify_release_target(
        {**body, "checks": {"identity_matches": True}}
    )
    assert status == 400
    assert len(calls) == 1


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
