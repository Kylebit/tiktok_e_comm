from __future__ import annotations

from copy import deepcopy
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request

import pytest

from domains.channel_operations.release_executor import (
    AdapterExecutionResult,
    AdapterRegistration,
)
from modules.products import server as product_server
from shared_platform import release_control, release_store
from shared_platform import target_scoped_release_contracts as target_contracts
from shared_platform.release_store import (
    ImmutableReleaseError,
    ReleaseAuthorizationError,
    ReleaseStore,
    ReleaseStoreError,
)
from shared_platform.target_scoped_release_contracts import (
    OfficialTargetProof,
    OfficialTargetReconciliationProof,
    TargetScopedCommandUnavailable,
    TargetScopedContractError,
    TargetScopedOperationRequest,
    TargetScopedOperationResult,
    TargetScopedReconciliationRequest,
    approved_shopee_channel_master_digest,
    approved_shopee_copy_digest,
    approved_source_image_manifest_digest,
    canonical_digest,
    evaluate_shopee_global_image_observation,
    evaluate_shopee_regional_copy_observation,
    evaluate_shopee_regional_image_observation,
    operation_kind_for_target,
    planned_target_command,
    shopee_global_image_id_mapping_digest,
    shopee_global_image_observation_outcome,
    shopee_regional_observation_outcome,
    target_preflight_digest,
)


def _plan(target_label: str = "shopee:MY") -> dict:
    currency = "VND" if target_label == "shopee:VN" else "MYR"
    plan = {
        "plan_id": "omnichannel:target-scoped-platform",
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
                    "title": "Approved English Shopee master",
                    "policy_check": "passed",
                }
            ],
            "shopee_description_en": (
                "Approved immutable Shopee description. " * 10
            ),
        },
        "images": [
            {
                "position": 1,
                "image_url": "https://cdn.example/approved-1.jpg",
                "artifact_id": "asset-1",
                "audit_id": "audit-1",
            },
            {
                "position": 2,
                "image_url": "https://cdn.example/approved-2.jpg",
                "artifact_id": "asset-2",
                "audit_id": "audit-2",
            },
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
    if target_label == "ozon:RU":
        plan["target_actions"] = {
            "ozon:RU": {
                "schema_version": (
                    "ozon-existing-product-stock-command/v1"
                ),
                "expected_listing_digest": "listing-digest-0954",
                "desired_stock_quantity": 50,
                "inventory_snapshot_id": "inventory:0954:r1",
                "inventory_snapshot_revision_or_digest": "inventory-digest",
                "warehouse_policy": "single_active_non_kgt",
            }
        }
    return plan


def _failed_store(
    tmp_path,
    target_label: str = "shopee:MY",
    *,
    plan_payload: dict | None = None,
):
    store = ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(plan_payload or _plan(target_label))
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


def _request(store: ReleaseStore, plan: dict, target_label: str):
    context = store.target_scoped_action_context(
        plan_id=plan["plan_id"],
        target_label=target_label,
    )
    payload = plan["payload"]
    return TargetScopedOperationRequest(
        plan_id=plan["plan_id"],
        confirmation_token=plan["confirmation_token"],
        approval_scope_digest=payload["omnichannel_scope_digest"],
        product_id=plan["product_id"],
        seller_sku=plan["seller_sku"],
        product_package_id=plan["product_package_id"],
        content_package_id=plan["content_package_id"],
        run_id=context["run_id"],
        target_label=target_label,
        operation_kind=context["operation_kind"],
        product_revision=context["product_revision"],
        payload_digest=context["payload_digest"],
        planned_command=context["planned_command"],
        planned_command_digest=context["planned_command_digest"],
        preflight_digest=context["preflight_digest"],
        failure_attempt=context["failure_attempt"],
        failure_digest=context["failure_digest"],
        target_idempotency_key=context["target_idempotency_key"],
    )


def _legacy_v1_request(
    request: TargetScopedOperationRequest,
) -> TargetScopedOperationRequest:
    command = dict(request.planned_command)
    command["schema_version"] = "shopee-existing-global-command/v1"
    command["builder_policy_version"] = "target-scoped-shopee/v1"
    for field in (
        "approved_copy_digest",
        "approved_source_image_manifest_digest",
        "global_image_observation_policy_version",
        "global_image_order_authority",
        "approved_global_image_mapping_digest",
    ):
        command.pop(field, None)
    command_digest = canonical_digest(command)
    preflight_digest = target_preflight_digest(
        plan_id=request.plan_id,
        run_id=request.run_id,
        target_label=request.target_label,
        operation_kind=request.operation_kind,
        product_revision=request.product_revision,
        payload_digest=request.payload_digest,
        planned_command_digest=command_digest,
        failure_attempt=request.failure_attempt,
        failure_digest=request.failure_digest,
        target_idempotency_key=request.target_idempotency_key,
    )
    return TargetScopedOperationRequest(
        **{
            **request.__dict__,
            "planned_command": command,
            "planned_command_digest": command_digest,
            "preflight_digest": preflight_digest,
        }
    )


def _proof_value(request: TargetScopedOperationRequest, **overrides):
    now = datetime.now(timezone.utc)
    value = {
        "schema_version": "official-target-proof/v1",
        "operation_kind": request.operation_kind,
        "plan_id": request.plan_id,
        "run_id": request.run_id,
        "target_label": request.target_label,
        "product_revision": request.product_revision,
        "payload_digest": request.payload_digest,
        "planned_command_digest": request.planned_command_digest,
        "preflight_digest": request.preflight_digest,
        "failure_attempt": request.failure_attempt,
        "failure_digest": request.failure_digest,
        "provided_by": "03",
        "allow_refresh": False,
        "observed_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "checks": {
            "official_identity_exact": True,
            "duplicate_absent_or_existing_exact": True,
            "credential_or_tenant_ready": True,
        },
        "semantic_evidence": {
            "source": "official_platform_read",
            "target": request.target_label,
            "result": "safe",
        },
        "redacted_summary": {
            "target": request.target_label,
            "status": "safe",
        },
        "external_writes_performed": [],
    }
    value.update(overrides)
    return value


def _proof(request: TargetScopedOperationRequest, **overrides):
    return OfficialTargetProof.from_value(
        _proof_value(request, **overrides),
        request=request,
    )


def _success_result(external_reference: str = "item-0954"):
    return TargetScopedOperationResult.from_value(
        {
            "succeeded": True,
            "readback_verified": True,
            "detail": "single target write matched official readback",
            "external_reference": external_reference,
            "submission_accepted": False,
            "evidence": {
                "verified": True,
                "checks": {"identity": True, "payload": True},
                "external_writes_performed": ["shopee:regional_publish"],
            },
        }
    )


@pytest.mark.parametrize(
    ("target_label", "operation_kind"),
    [
        ("shopee:MY", "shopee_safe_pre_submit_retry_v1"),
        ("shopee:VN", "shopee_safe_pre_submit_retry_v1"),
        ("ozon:RU", "ozon_existing_product_stock_reconciliation_v1"),
    ],
)
def test_operation_kind_is_server_derived(target_label, operation_kind):
    assert operation_kind_for_target(target_label) == operation_kind
    with pytest.raises(TargetScopedContractError):
        operation_kind_for_target("shopee:PH")


@pytest.mark.parametrize(
    ("target_label", "region", "currency", "excluded"),
    [
        ("shopee:MY", "MY", "MYR", []),
        ("shopee:VN", "VN", "VND", [50052]),
    ],
)
def test_shopee_command_is_purely_derived_from_immutable_plan(
    target_label,
    region,
    currency,
    excluded,
):
    payload = _plan(target_label)
    command, digest = planned_target_command(
        payload,
        target_label=target_label,
    )
    with_untrusted_runtime_data = deepcopy(payload)
    with_untrusted_runtime_data["browser_state"] = {
        "local_original_price": 999999,
        "access_token": "must-not-be-read",
    }
    with_untrusted_runtime_data.update(
        {
            "regional_copy_policy_version": "browser-override",
            "expected_language": "browser-language",
            "source_global_master_digest": "browser-master",
            "approved_copy_digest": "browser-copy",
            "approved_source_image_manifest_digest": "browser-images",
            "global_image_observation_policy_version": "browser-policy",
            "global_image_order_authority": "browser-authority",
            "approved_global_image_mapping_digest": "browser-mapping",
        }
    )
    repeated, repeated_digest = planned_target_command(
        with_untrusted_runtime_data,
        target_label=target_label,
    )

    assert command == repeated
    assert digest == repeated_digest == canonical_digest(command)
    assert command == {
        "schema_version": "shopee-existing-global-command/v2",
        "builder_policy_version": "target-scoped-shopee/v2",
        "target_label": target_label,
        "operation_kind": "shopee_safe_pre_submit_retry_v1",
        "region": region,
        "seller_sku": "0954",
        "model_sku": "0954",
        "existing_global_only": True,
        "forbid_global_create": True,
        "forbid_global_update": True,
        "forbid_model_init": True,
        "allow_token_refresh": False,
        "item_status": "NORMAL",
        "local_original_price": 45.0,
        "local_currency": currency,
        "approved_master_digest": command["approved_master_digest"],
        "approved_copy_digest": command["approved_copy_digest"],
        "approved_source_image_manifest_digest": command[
            "approved_source_image_manifest_digest"
        ],
        "global_image_observation_policy_version": (
            "shopee-global-rehost-observation/v1"
        ),
        "global_image_order_authority": "unverifiable",
        "approved_global_image_mapping_digest": None,
        "regional_copy_policy_version": (
            "shopee-platform-derived-translation/v1"
        ),
        "source_global_master_digest": command[
            "approved_master_digest"
        ],
        "expected_language": (
            "vi-Latn" if target_label == "shopee:VN" else "ms-Latn"
        ),
        "regional_copy_lint_policy_version": (
            "shopee-regional-copy-lint/v1"
        ),
        "regional_image_verification_policy_version": (
            "shopee-linked-image-observation/v1"
        ),
        "regional_observation_policy_digest": command[
            "regional_observation_policy_digest"
        ],
        "approved_image_count": 2,
        "parcel": {
            "weight_kg": 0.2,
            "package_cm": [34.0, 58.0, 3.0],
        },
        "parcel_digest": command["parcel_digest"],
        "logistics_policy_version": (
            "approved-parcel-enabled-channels-exclude-50052/v1"
            if target_label == "shopee:VN"
            else "approved-parcel-enabled-channels/v1"
        ),
        "excluded_logistics_ids": excluded,
    }
    assert "access_token" not in json.dumps(command)


def test_shopee_channel_master_digest_ignores_internal_lineage():
    original = _plan("shopee:MY")
    changed_lineage = deepcopy(original)
    changed_lineage["images"][0].update(
        {
            "artifact_id": "replacement-artifact",
            "audit_id": "replacement-audit",
            "decision_source": "different-internal-lineage",
        }
    )

    original_command, original_digest = planned_target_command(
        original,
        target_label="shopee:MY",
    )
    changed_command, changed_digest = planned_target_command(
        changed_lineage,
        target_label="shopee:MY",
    )

    assert changed_command["approved_master_digest"] == (
        original_command["approved_master_digest"]
    )
    assert changed_command == original_command
    assert changed_digest == original_digest


def test_shopee_channel_master_digest_changes_for_each_visible_field():
    title = "Approved English Shopee master"
    description = "Exact approved description."
    urls = [
        "https://cdn.example/approved-1.jpg",
        "https://cdn.example/approved-2.jpg",
    ]
    baseline = approved_shopee_channel_master_digest(
        title,
        description,
        urls,
    )
    variants = [
        approved_shopee_channel_master_digest(
            title + " updated",
            description,
            urls,
        ),
        approved_shopee_channel_master_digest(
            title,
            description + " Updated.",
            urls,
        ),
        approved_shopee_channel_master_digest(
            title,
            description,
            list(reversed(urls)),
        ),
        approved_shopee_channel_master_digest(
            title,
            description,
            [urls[0], "https://cdn.example/different.jpg"],
        ),
    ]

    assert all(value != baseline for value in variants)
    assert len(set(variants)) == len(variants)


def test_official_shopee_fields_recompute_planned_master_digest():
    payload = _plan("shopee:VN")
    command, _digest = planned_target_command(
        payload,
        target_label="shopee:VN",
    )
    official_title = payload["listing_copy"]["candidates"][0]["title"]
    official_description = payload["listing_copy"][
        "shopee_description_en"
    ]
    official_urls = [
        row["image_url"] for row in payload["images"]
    ]

    official_digest = approved_shopee_channel_master_digest(
        official_title,
        official_description,
        official_urls,
    )

    assert official_digest == command["approved_master_digest"]


def test_copy_and_source_manifest_digests_split_rehosted_official_facts():
    payload = _plan("shopee:MY")
    command, command_digest = planned_target_command(
        payload,
        target_label="shopee:MY",
    )
    title = payload["listing_copy"]["candidates"][0]["title"]
    description = payload["listing_copy"]["shopee_description_en"]
    source_urls = [row["image_url"] for row in payload["images"]]
    rehosted_urls = [
        "https://cf.shopee.example/rehosted-a.jpg",
        "https://cf.shopee.example/rehosted-b.jpg",
    ]

    assert approved_shopee_copy_digest(
        f"  {title}  ",
        description,
    ) == command["approved_copy_digest"]
    assert approved_source_image_manifest_digest(source_urls) == command[
        "approved_source_image_manifest_digest"
    ]
    assert approved_source_image_manifest_digest(rehosted_urls) != command[
        "approved_source_image_manifest_digest"
    ]
    assert approved_shopee_channel_master_digest(
        title,
        description,
        rehosted_urls,
    ) != command["approved_master_digest"]
    assert command["approved_master_digest"] == (
        planned_target_command(
            payload,
            target_label="shopee:MY",
        )[0]["approved_master_digest"]
    )
    assert command_digest == canonical_digest(command)


def test_copy_digest_is_exact_for_description_and_nfc_trimmed_title():
    composed = "Caf\u00e9 approved title"
    decomposed = "  Cafe\u0301 approved title  "
    description = "Exact description.\nSecond line."

    baseline = approved_shopee_copy_digest(composed, description)

    assert approved_shopee_copy_digest(
        decomposed,
        description,
    ) == baseline
    assert approved_shopee_copy_digest(
        composed,
        description + " ",
    ) != baseline
    assert approved_shopee_copy_digest(
        composed + " changed",
        description,
    ) != baseline


def test_global_rehost_observation_without_prior_mapping_is_warning():
    urls = [
        "https://cf.shopee.example/rehosted-a.jpg",
        "https://cf.shopee.example/rehosted-b.jpg",
    ]
    image_ids = ["official-image-a", "official-image-b"]

    observed = evaluate_shopee_global_image_observation(
        approved_count=2,
        official_image_urls=urls,
        official_image_ids=image_ids,
    )
    outcome = shopee_global_image_observation_outcome(
        global_hard_facts_exact=True,
        image_observation=observed,
    )
    serialized = json.dumps(
        {"observation": observed, "outcome": outcome}
    )

    assert observed["status"] == "warning"
    assert observed["verification_scope"] == (
        "linked_count_verified_order_unverifiable"
    )
    assert observed["url_identity_exact"] is False
    assert observed["approved_order_exact"] is False
    assert observed["manual_review_required"] is True
    assert outcome["execution_allowed"] is True
    assert outcome["manual_review_required"] is True
    assert outcome["semantic_equivalence"] == "unverified"
    assert all(value not in serialized for value in urls + image_ids)


def test_global_rehost_observation_uses_only_prior_plan_mapping_for_order():
    urls = [
        "https://cf.shopee.example/rehosted-a.jpg",
        "https://cf.shopee.example/rehosted-b.jpg",
    ]
    image_ids = ["official-image-a", "official-image-b"]
    mapping_digest = shopee_global_image_id_mapping_digest(image_ids)

    exact = evaluate_shopee_global_image_observation(
        approved_count=2,
        official_image_urls=urls,
        official_image_ids=image_ids,
        prior_mapping_digest=mapping_digest,
    )
    mismatch = evaluate_shopee_global_image_observation(
        approved_count=2,
        official_image_urls=urls,
        official_image_ids=list(reversed(image_ids)),
        prior_mapping_digest=mapping_digest,
    )

    assert exact["status"] == "observed"
    assert exact["approved_order_exact"] is True
    assert exact["verification_scope"] == "stable_ordered_ids_exact"
    assert mismatch["status"] == "needs_review"
    assert mismatch["approved_order_exact"] is False
    assert "global_image:prior_mapping_mismatch" in (
        mismatch["matched_rule_ids"]
    )
    assert shopee_global_image_observation_outcome(
        global_hard_facts_exact=True,
        image_observation=mismatch,
    )["execution_allowed"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_title", "raw approved title"),
        ("source_description", "raw approved description"),
        ("image_url_list", ["https://private.example/image.jpg"]),
        ("image_id_list", ["private-image-id"]),
        ("token", "private-token"),
        ("raw_response", {"private": True}),
    ],
)
def test_global_image_observation_validation_rejects_raw_fields(
    field,
    value,
):
    observed = evaluate_shopee_global_image_observation(
        approved_count=2,
        official_image_urls=["rehost-a", "rehost-b"],
        official_image_ids=["id-a", "id-b"],
    )
    tampered = {**observed, field: value}
    tampered.pop("evidence_digest")
    tampered["evidence_digest"] = canonical_digest(tampered)

    with pytest.raises(TargetScopedContractError):
        shopee_global_image_observation_outcome(
            global_hard_facts_exact=True,
            image_observation=tampered,
        )


def test_global_image_warning_cannot_forge_exact_count_shape():
    observed = evaluate_shopee_global_image_observation(
        approved_count=2,
        official_image_urls=["rehost-a", "rehost-b"],
        official_image_ids=["id-a", "id-b"],
    )
    tampered = {
        **observed,
        "official_ids_nonempty_unique": False,
    }
    tampered.pop("evidence_digest")
    tampered["evidence_digest"] = canonical_digest(tampered)

    with pytest.raises(
        TargetScopedContractError,
        match="exact count and shape",
    ):
        shopee_global_image_observation_outcome(
            global_hard_facts_exact=True,
            image_observation=tampered,
        )


@pytest.mark.parametrize(
    ("urls", "image_ids", "approved_count", "rule_id"),
    [
        (None, ["a", "b"], 2, "global_image:shape_ambiguous"),
        (["a", "b"], None, 2, "global_image:shape_ambiguous"),
        (["a", "a"], ["i1", "i2"], 2, (
            "global_image:official_urls_not_nonempty_unique"
        )),
        (["a", "b"], ["i1", "i1"], 2, (
            "global_image:official_ids_not_nonempty_unique"
        )),
        (["a"], ["i1"], 2, "global_image:count_mismatch"),
        (["a", "b"], ["i1", "i2"], True, (
            "global_image:shape_ambiguous"
        )),
    ],
)
def test_global_rehost_observation_invalid_shapes_need_review(
    urls,
    image_ids,
    approved_count,
    rule_id,
):
    observed = evaluate_shopee_global_image_observation(
        approved_count=approved_count,
        official_image_urls=urls,
        official_image_ids=image_ids,
    )

    assert observed["status"] == "needs_review"
    assert rule_id in observed["matched_rule_ids"]
    assert shopee_global_image_observation_outcome(
        global_hard_facts_exact=True,
        image_observation=observed,
    )["execution_allowed"] is False


def test_prior_mapping_and_global_policy_are_bound_to_command_digest(
    monkeypatch,
):
    payload = _plan("shopee:MY")
    baseline, baseline_digest = planned_target_command(
        payload,
        target_label="shopee:MY",
    )
    mapped_payload = deepcopy(payload)
    mapped_payload["shopee_global_image_mapping"] = {
        "schema_version": "approved-shopee-global-image-mapping/v1",
        "image_count": 2,
        "ordered_image_ids_digest": (
            shopee_global_image_id_mapping_digest(["id-1", "id-2"])
        ),
    }
    mapped, mapped_digest = planned_target_command(
        mapped_payload,
        target_label="shopee:MY",
    )
    monkeypatch.setattr(
        target_contracts,
        "SHOPEE_GLOBAL_IMAGE_OBSERVATION_POLICY_VERSION",
        "shopee-global-rehost-observation/v2-test",
    )
    changed_policy, changed_policy_digest = planned_target_command(
        payload,
        target_label="shopee:MY",
    )

    assert baseline["global_image_order_authority"] == "unverifiable"
    assert baseline["approved_global_image_mapping_digest"] is None
    assert mapped["global_image_order_authority"] == (
        "prior_plan_mapping"
    )
    assert mapped["approved_global_image_mapping_digest"]
    assert mapped_digest != baseline_digest
    assert changed_policy["global_image_observation_policy_version"].endswith(
        "v2-test"
    )
    assert changed_policy_digest != baseline_digest


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {
            "schema_version": "approved-shopee-global-image-mapping/v1",
            "image_count": True,
            "ordered_image_ids_digest": "a" * 64,
        },
        {
            "schema_version": "approved-shopee-global-image-mapping/v1",
            "image_count": 2,
            "ordered_image_ids_digest": "not-a-digest",
        },
    ],
)
def test_malformed_prior_mapping_fails_before_command(mapping):
    payload = _plan("shopee:MY")
    payload["shopee_global_image_mapping"] = mapping

    with pytest.raises(
        TargetScopedCommandUnavailable,
        match="global image mapping",
    ):
        planned_target_command(payload, target_label="shopee:MY")


def test_regional_policy_is_deterministic_and_bound_to_command_digest(
    monkeypatch,
):
    my_payload = _plan("shopee:MY")
    vn_payload = _plan("shopee:VN")
    original, original_digest = planned_target_command(
        my_payload,
        target_label="shopee:MY",
    )
    repeated, repeated_digest = planned_target_command(
        deepcopy(my_payload),
        target_label="shopee:MY",
    )
    vn_command, vn_digest = planned_target_command(
        vn_payload,
        target_label="shopee:VN",
    )
    changed_master = deepcopy(my_payload)
    changed_master["listing_copy"]["candidates"][0][
        "title"
    ] += " updated"
    _changed_master_command, changed_master_digest = (
        planned_target_command(
            changed_master,
            target_label="shopee:MY",
        )
    )
    monkeypatch.setattr(
        target_contracts,
        "SHOPEE_REGIONAL_COPY_LINT_POLICY_VERSION",
        "shopee-regional-copy-lint/v2-test",
    )
    changed_policy, changed_policy_digest = planned_target_command(
        my_payload,
        target_label="shopee:MY",
    )

    assert repeated == original
    assert repeated_digest == original_digest
    assert vn_command["expected_language"] == "vi-Latn"
    assert vn_digest != original_digest
    assert changed_master_digest != original_digest
    assert changed_policy["regional_copy_lint_policy_version"].endswith(
        "v2-test"
    )
    assert changed_policy_digest != original_digest


def test_my_english_source_copy_requires_review_without_raw_copy():
    source_title = "Approved English Shopee master"
    source_description = "Approved immutable description for the product."

    observed = evaluate_shopee_regional_copy_observation(
        source_title=source_title,
        source_description=source_description,
        source_global_master_digest="master-digest",
        regional_title=source_title,
        regional_description=source_description,
        site="MY",
    )
    repeated = evaluate_shopee_regional_copy_observation(
        source_title=source_title,
        source_description=source_description,
        source_global_master_digest="master-digest",
        regional_title=source_title,
        regional_description=source_description,
        site="MY",
    )
    serialized = json.dumps(observed, ensure_ascii=False)

    assert observed == repeated
    assert observed["status"] == "needs_review"
    assert observed["source_copy_leakage"] == "full_source_copy"
    assert "copy:full_source_leakage" in observed["matched_rule_ids"]
    assert observed["semantic_equivalence"] == "unverified"
    assert source_title not in serialized
    assert source_description not in serialized


def test_my_safe_latin_copy_without_strong_signal_is_warning():
    observed = evaluate_shopee_regional_copy_observation(
        source_title="Approved floral wall decoration",
        source_description=(
            "Approved source facts and application guidance for buyers."
        ),
        source_global_master_digest="master-digest",
        regional_title="Modern Floral Decoration for Living Spaces",
        regional_description=(
            "Decorative wall item with clear dimensions and simple "
            "application guidance for indoor rooms."
        ),
        site="MY",
    )

    assert observed["status"] == "warning"
    assert observed["language_signal"] == "weak"
    assert "copy:language_signal_weak" in observed["matched_rule_ids"]


def test_vn_full_english_source_copy_without_language_signal_needs_review():
    observed = evaluate_shopee_regional_copy_observation(
        source_title="Approved English title",
        source_description="Approved English description",
        source_global_master_digest="master-digest",
        regional_title="Approved English title",
        regional_description="Approved English description",
        site="VN",
    )

    assert observed["status"] == "needs_review"
    assert observed["language_signal"] == "weak"
    assert "copy:full_source_leakage" in observed["matched_rule_ids"]


def test_vn_diacritic_copy_is_observed_but_semantics_remain_unverified():
    observed = evaluate_shopee_regional_copy_observation(
        source_title="Approved floral wall decoration",
        source_description=(
            "Approved source facts and application guidance for buyers."
        ),
        source_global_master_digest="master-digest",
        regional_title="Trang trí tường hoa lá cho phòng khách",
        regional_description=(
            "Sản phẩm trang trí phù hợp cho không gian trong nhà và có "
            "hướng dẫn sử dụng rõ ràng."
        ),
        site="VN",
    )

    assert observed["status"] == "observed"
    assert observed["expected_language"] == "vi-Latn"
    assert observed["language_signal"] == "strong"
    assert observed["semantic_equivalence"] == "unverified"
    assert observed["matched_rule_ids"] == []


@pytest.mark.parametrize(
    ("regional_title", "regional_description", "expected_rule"),
    [
        ("", "", "copy:title_missing"),
        (
            "墙贴装饰",
            "适合家庭使用的墙面装饰。",
            "copy:cjk_present",
        ),
        (
            "Modern Wall Decoration",
            "This waterproof decoration has an unapproved new claim.",
            "copy:new_high_risk:waterproof",
        ),
        (
            ["ambiguous"],
            "Regional description",
            "copy:shape_ambiguous",
        ),
    ],
)
def test_regional_copy_empty_cjk_new_claim_or_shape_needs_review(
    regional_title,
    regional_description,
    expected_rule,
):
    observed = evaluate_shopee_regional_copy_observation(
        source_title="Approved English title",
        source_description=(
            "Approved description without unverified performance claims."
        ),
        source_global_master_digest="master-digest",
        regional_title=regional_title,
        regional_description=regional_description,
        site="MY",
    )

    assert observed["status"] == "needs_review"
    assert expected_rule in observed["matched_rule_ids"]


def test_regional_images_stable_ids_are_observed_without_raw_urls():
    urls = [
        "https://regional.example/rehosted-1.jpg",
        "https://regional.example/rehosted-2.jpg",
    ]
    observed = evaluate_shopee_regional_image_observation(
        approved_count=2,
        regional_image_urls=urls,
        global_linkage_verified=True,
        stable_ordered_image_ids=["image-id-1", "image-id-2"],
        stable_ordered_ids_exact=True,
    )
    serialized = json.dumps(observed)

    assert observed["status"] == "observed"
    assert observed["verification_scope"] == "stable_ordered_ids_exact"
    assert observed["url_identity_exact"] is False
    assert all(url not in serialized for url in urls)


def test_rehosted_count_only_images_are_an_honest_warning():
    observed = evaluate_shopee_regional_image_observation(
        approved_count=2,
        regional_image_urls=[
            "https://regional.example/rehosted-1.jpg",
            "https://regional.example/rehosted-2.jpg",
        ],
        global_linkage_verified=True,
    )

    assert observed["status"] == "warning"
    assert observed["verification_scope"] == (
        "linked_count_verified_order_unverifiable"
    )
    assert observed["url_identity_exact"] is False
    assert "image:linked_count_verified_order_unverifiable" in (
        observed["matched_rule_ids"]
    )


@pytest.mark.parametrize(
    "regional_urls",
    [
        ["https://regional.example/only-one.jpg"],
        ["", "https://regional.example/second.jpg"],
        ["https://regional.example/main.jpg", ""],
    ],
)
def test_regional_image_count_mismatch_or_empty_main_needs_review(
    regional_urls,
):
    observed = evaluate_shopee_regional_image_observation(
        approved_count=2,
        regional_image_urls=regional_urls,
        global_linkage_verified=True,
    )

    assert observed["status"] == "needs_review"


@pytest.mark.parametrize(
    ("copy_status", "image_status", "hard_exact", "expected"),
    [
        ("observed", "observed", True, "SUCCEEDED"),
        ("warning", "observed", True, "SUCCEEDED"),
        ("observed", "warning", True, "SUCCEEDED"),
        ("needs_review", "observed", True, "RECONCILIATION_REQUIRED"),
        ("observed", "needs_review", True, "RECONCILIATION_REQUIRED"),
        ("observed", "observed", False, "RECONCILIATION_REQUIRED"),
    ],
)
def test_regional_observation_outcome_never_fabricates_hard_facts(
    copy_status,
    image_status,
    hard_exact,
    expected,
):
    copy_observation = {
        "schema_version": (
            "platform-derived-translation-observation/v1"
        ),
        "status": copy_status,
        "matched_rule_ids": [],
        "semantic_equivalence": "unverified",
    }
    copy_observation["evidence_digest"] = canonical_digest(
        copy_observation
    )
    image_observation = {
        "schema_version": "platform-derived-image-observation/v1",
        "status": image_status,
        "matched_rule_ids": [],
    }
    image_observation["evidence_digest"] = canonical_digest(
        image_observation
    )
    outcome = shopee_regional_observation_outcome(
        listing_hard_exact=hard_exact,
        copy_observation=copy_observation,
        image_observation=image_observation,
    )

    assert outcome["outcome"] == expected
    assert outcome["listing_identity_verified"] is hard_exact
    assert outcome["profit_status"] == "unverified"
    assert outcome["semantic_equivalence"] == "unverified"
    assert outcome["reconciliation_required"] is (
        expected == "RECONCILIATION_REQUIRED"
    )
    assert outcome["manual_review_required"] is (
        copy_status != "observed" or image_status != "observed"
    )


def test_regional_outcome_rejects_tampered_observation_digest():
    copy_observation = evaluate_shopee_regional_copy_observation(
        source_title="Approved title",
        source_description="Approved description",
        source_global_master_digest="master-digest",
        regional_title="Produk hiasan yang sesuai untuk rumah",
        regional_description=(
            "Produk ini sesuai untuk hiasan rumah dan mudah digunakan."
        ),
        site="MY",
    )
    image_observation = evaluate_shopee_regional_image_observation(
        approved_count=1,
        regional_image_urls=["https://regional.example/rehosted.jpg"],
        global_linkage_verified=True,
    )
    copy_observation["status"] = "needs_review"

    with pytest.raises(
        TargetScopedContractError,
        match="evidence digest",
    ):
        shopee_regional_observation_outcome(
            listing_hard_exact=True,
            copy_observation=copy_observation,
            image_observation=image_observation,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("pricing"),
        lambda payload: payload["listing_copy"].pop(
            "shopee_description_en"
        ),
        lambda payload: payload["images"].reverse(),
        lambda payload: payload["product_facts"].pop("weight_kg"),
    ],
)
def test_incomplete_shopee_plan_cannot_build_a_command(mutation):
    payload = _plan("shopee:MY")
    mutation(payload)

    with pytest.raises(
        TargetScopedCommandUnavailable,
        match="immutable",
    ) as raised:
        planned_target_command(payload, target_label="shopee:MY")

    assert raised.value.code == "planned_command_incomplete"


def test_ozon_requires_successor_stock_decision_but_future_schema_is_supported():
    payload = _plan("ozon:RU")
    successor_command, successor_digest = planned_target_command(
        payload,
        target_label="ozon:RU",
    )
    payload.pop("target_actions")

    with pytest.raises(TargetScopedCommandUnavailable) as raised:
        planned_target_command(payload, target_label="ozon:RU")

    assert raised.value.code == "successor_plan_stock_decision_required"
    assert successor_command["desired_stock_quantity"] == 50
    assert successor_command["inventory_snapshot_id"] == "inventory:0954:r1"
    assert successor_command["warehouse_policy"] == "single_active_non_kgt"
    assert successor_command["forbid_import"] is True
    assert successor_command["forbid_create"] is True
    assert successor_digest == canonical_digest(successor_command)


def test_verified_adapter_result_can_truthfully_record_submission_acceptance():
    result = TargetScopedOperationResult.from_value(
        AdapterExecutionResult(
            succeeded=True,
            readback_verified=True,
            detail="official create readback matched",
            external_reference="item-0954",
            readback_evidence={
                "verified": True,
                "external_writes_performed": ["shopee:regional_publish"],
            },
            submission_accepted=True,
        )
    )

    assert result.outcome == "SUCCEEDED"
    assert result.submission_accepted is True


def test_atomic_claim_consumes_proof_without_making_target_pending(tmp_path):
    store, plan, run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)

    claim = store.claim_target_scoped_operation(
        request=request,
        proof=proof,
    )

    assert claim["action"] == "claimed"
    asserted = store.get_run(run["run_id"])
    target = asserted["targets"][0]
    assert target["storage_status"] == "FAILED"
    assert target["status"] == "RUNNING"
    assert target["attempts"] == request.failure_attempt + 1
    assert target["target_scoped_operation"]["status"] == "RUNNING"
    with sqlite3.connect(store.path) as connection:
        proof_row = connection.execute(
            """
            SELECT status, operation_digest, proof_json
            FROM release_target_retry_proofs
            WHERE proof_digest = ?
            """,
            (proof.proof_digest,),
        ).fetchone()
        operation_row = connection.execute(
            """
            SELECT request_json
            FROM release_target_retry_operations
            WHERE operation_digest = ?
            """,
            (claim["operation"]["operation_digest"],),
        ).fetchone()
        physical = connection.execute(
            """
            SELECT status FROM release_target_runs
            WHERE run_id = ? AND target_label = 'shopee:MY'
            """,
            (run["run_id"],),
        ).fetchone()
    assert proof_row[0] == "CONSUMED"
    assert proof_row[1] == claim["operation"]["operation_digest"]
    proof_identity = json.loads(proof_row[2])
    operation_identity = json.loads(operation_row[0])
    assert proof_identity["planned_command_digest"] == (
        request.planned_command_digest
    )
    assert operation_identity["planned_command_digest"] == (
        request.planned_command_digest
    )
    assert operation_identity["planned_command"] == dict(
        request.planned_command
    )
    assert "confirmation_token" not in operation_identity
    assert "access_token" not in json.dumps(
        {"proof": proof_identity, "operation": operation_identity}
    )
    assert physical[0] == "FAILED"


def test_concurrent_claim_has_exactly_one_winner(tmp_path):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)

    def claim():
        try:
            return store.claim_target_scoped_operation(
                request=request,
                proof=proof,
            )["action"]
        except ReleaseStoreError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted([future.result() for future in [pool.submit(claim), pool.submit(claim)]])

    assert outcomes == ["claimed", "rejected"]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_operations"
        ).fetchone()[0] == 1


def test_success_is_atomic_and_exact_claim_replay_is_zero_write(tmp_path):
    store, plan, run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)
    claim = store.claim_target_scoped_operation(request=request, proof=proof)

    completed = store.record_target_scoped_success(
        claim["operation"]["operation_digest"],
        result=_success_result(),
    )
    assert completed["targets"][0]["status"] == "SUCCEEDED"
    assert completed["targets"][0]["external_id"] == "item-0954"

    replay = store.claim_target_scoped_operation(request=request, proof=proof)
    assert replay["action"] == "already_succeeded"
    assert replay["operation"]["status"] == "SUCCEEDED"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_operations"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "target_label",
    ["shopee:MY", "shopee:VN", "ozon:RU"],
)
def test_pre_submit_failure_stays_failed_and_generic_retry_is_forbidden(
    tmp_path,
    target_label,
):
    store, plan, run = _failed_store(tmp_path, target_label)
    request = _request(store, plan, target_label)
    claim = store.claim_target_scoped_operation(
        request=request,
        proof=_proof(request),
    )
    result = TargetScopedOperationResult.from_value(
        {
            "succeeded": False,
            "readback_verified": False,
            "detail": "logistics changed before submission",
            "external_reference": None,
            "submission_accepted": False,
            "evidence": {
                "pre_submit_failure": True,
                "external_writes_performed": [],
            },
        }
    )

    failed = store.record_target_scoped_pre_submit_failure(
        claim["operation"]["operation_digest"],
        result=result,
    )
    target = failed["targets"][0]
    assert target["status"] == "FAILED"
    assert target["storage_status"] == "FAILED"
    assert target["target_scoped_operation"]["status"] == "FAILED_PRE_SUBMIT"
    with pytest.raises(ReleaseAuthorizationError, match="target-scoped"):
        store.retry_failed_targets(run["run_id"], [target_label])


def test_reconciliation_preserves_truthful_write_and_blocks_replay(tmp_path):
    store, plan, run = _failed_store(tmp_path, "ozon:RU")
    request = _request(store, plan, "ozon:RU")
    claim = store.claim_target_scoped_operation(
        request=request,
        proof=_proof(request),
    )
    result = TargetScopedOperationResult.from_value(
        {
            "succeeded": False,
            "readback_verified": False,
            "detail": "stock write accepted; readback timed out",
            "external_reference": "5687436857",
            "submission_accepted": False,
            "evidence": {
                "durable_state_uncertain": True,
                "external_writes_performed": ["ozon:stock:update"],
            },
        }
    )
    reconciled = store.record_target_scoped_reconciliation(
        claim["operation"]["operation_digest"],
        result=result,
    )

    target = reconciled["targets"][0]
    assert target["status"] == "RECONCILIATION_REQUIRED"
    assert target["storage_status"] == "FAILED"
    assert target["external_id"] == "5687436857"
    assert target["target_scoped_operation"]["result"][
        "external_writes_performed"
    ] == ["ozon:stock:update"]
    with pytest.raises(ReleaseStoreError, match="terminal"):
        store.claim_target_scoped_operation(
            request=request,
            proof=_proof(request),
        )


def test_claim_fails_closed_on_token_proof_and_failure_identity_drift(tmp_path):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    wrong_token = TargetScopedOperationRequest(
        **{
            **request.__dict__,
            "confirmation_token": "PUBLISH-WRONG",
        }
    )
    with pytest.raises(ReleaseAuthorizationError, match="authority"):
        store.claim_target_scoped_operation(
            request=wrong_token,
            proof=_proof(wrong_token),
        )

    wrong_proof = _proof_value(request, failure_attempt=request.failure_attempt + 1)
    with pytest.raises(TargetScopedContractError, match="identity"):
        OfficialTargetProof.from_value(wrong_proof, request=request)
    wrong_command_proof = _proof_value(
        request,
        planned_command_digest="different-command",
    )
    with pytest.raises(TargetScopedContractError, match="identity"):
        OfficialTargetProof.from_value(
            wrong_command_proof,
            request=request,
        )

    store.retry_failed_targets  # keep the store instance live for coverage
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE release_target_runs SET error = 'failure drifted'
            WHERE run_id = ? AND target_label = ?
            """,
            (request.run_id, request.target_label),
        )
        connection.commit()
    with pytest.raises(ReleaseStoreError, match="failure identity"):
        store.claim_target_scoped_operation(
            request=request,
            proof=_proof(request),
        )


def test_claim_recomputes_planned_command_from_immutable_plan(tmp_path):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    client_command = {
        **dict(request.planned_command),
        "local_original_price": 999999.0,
    }
    client_digest = canonical_digest(client_command)
    client_preflight = target_preflight_digest(
        plan_id=request.plan_id,
        run_id=request.run_id,
        target_label=request.target_label,
        operation_kind=request.operation_kind,
        product_revision=request.product_revision,
        payload_digest=request.payload_digest,
        planned_command_digest=client_digest,
        failure_attempt=request.failure_attempt,
        failure_digest=request.failure_digest,
        target_idempotency_key=request.target_idempotency_key,
    )
    injected = TargetScopedOperationRequest(
        **{
            **request.__dict__,
            "planned_command": client_command,
            "planned_command_digest": client_digest,
            "preflight_digest": client_preflight,
        }
    )

    with pytest.raises(
        ImmutableReleaseError,
        match="failure identity",
    ):
        store.claim_target_scoped_operation(
            request=injected,
            proof=_proof(injected),
        )

    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_proofs"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_operations"
        ).fetchone()[0] == 0


def test_legacy_v1_command_digest_cannot_persist_proof_or_claim(tmp_path):
    store, plan, _run = _failed_store(tmp_path)
    current = _request(store, plan, "shopee:MY")
    legacy = _legacy_v1_request(current)

    with pytest.raises(
        ImmutableReleaseError,
        match="failure identity",
    ):
        store.claim_target_scoped_operation(
            request=legacy,
            proof=_proof(legacy),
        )

    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_proofs"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_operations"
        ).fetchone()[0] == 0


def test_server_rejects_persisted_legacy_contract_before_proof_or_execute(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path)
    current = _request(store, plan, "shopee:MY")
    legacy = _legacy_v1_request(current)
    proof = _proof(legacy)
    operation_digest = legacy.operation_digest(proof.proof_digest)
    timestamp = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO release_target_retry_proofs (
                proof_digest, plan_id, run_id, target_label,
                operation_kind, product_revision, payload_digest,
                preflight_digest, failure_attempt, failure_digest,
                proof_json, status, created_at, consumed_at,
                operation_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONSUMED', ?, ?, ?)
            """,
            (
                proof.proof_digest,
                legacy.plan_id,
                legacy.run_id,
                legacy.target_label,
                legacy.operation_kind,
                legacy.product_revision,
                legacy.payload_digest,
                legacy.preflight_digest,
                legacy.failure_attempt,
                legacy.failure_digest,
                json.dumps(proof.durable_payload(), sort_keys=True),
                timestamp,
                timestamp,
                operation_digest,
            ),
        )
        connection.execute(
            """
            INSERT INTO release_target_retry_operations (
                operation_digest, proof_digest, plan_id, run_id,
                target_label, operation_kind, request_json, status,
                created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'FAILED_PRE_SUBMIT', ?, ?, ?)
            """,
            (
                operation_digest,
                proof.proof_digest,
                legacy.plan_id,
                legacy.run_id,
                legacy.target_label,
                legacy.operation_kind,
                json.dumps(legacy.durable_identity(), sort_keys=True),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.commit()

    calls = []
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda _data, *, store: (
            {
                "dashboard": {},
                "payload": plan["payload"],
                "plan": plan,
                "run": store.get_run(legacy.run_id),
                "registry": {},
                "target_rows": [],
            },
            None,
        ),
    )
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: SimpleNamespace(
            build_official_target_proof=lambda *_args, **_kwargs: calls.append(
                "proof"
            ),
            execute_target_scoped_operation=lambda *_args: calls.append(
                "execute"
            ),
        ),
    )

    status, payload = product_server._execute_target_scoped_release_action(
        _post_body(legacy, proof)
    )

    assert status == 409
    assert payload["code"] == "target_scoped_contract_stale"
    assert payload["available"] is False
    assert payload["external_writes_performed"] == []
    assert "run" not in payload
    assert calls == []
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_proofs"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_operations"
        ).fetchone()[0] == 1


def _resolved_gate(store, plan, request):
    run = store.get_run(request.run_id)
    operation = store.get_target_scoped_operation(
        run_id=request.run_id,
        target_label=request.target_label,
    )
    return {
        "gate": {
            "plan": plan,
            "payload": plan["payload"],
            "run": run,
            "dashboard": {},
            "registry": {},
            "target_rows": [],
        },
        "operation_kind": request.operation_kind,
        "existing_operation": operation,
        "request": None if operation else request,
        "context": None,
        "gate_data": {},
    }, None


def _post_body(request, proof):
    return {
        "offer_id": request.product_id,
        "seller_sku": request.seller_sku,
        "publication_targets": [request.target_label],
        "target_label": request.target_label,
        "plan_id": request.plan_id,
        "confirmation_token": request.confirmation_token,
        "expected_revision": request.product_revision,
        "failure_attempt": request.failure_attempt,
        "payload_digest": request.payload_digest,
        "planned_command_digest": request.planned_command_digest,
        "preflight_digest": request.preflight_digest,
        "proof_digest": proof.proof_digest,
        "approved_by": "Kyle",
        "confirm_target_scoped_action": True,
    }


@pytest.mark.parametrize(
    ("target_label", "remove_field", "expected_code"),
    [
        (
            "shopee:MY",
            "pricing",
            "planned_command_incomplete",
        ),
        (
            "ozon:RU",
            "target_actions",
            "successor_plan_stock_decision_required",
        ),
    ],
)
def test_preview_blocks_incomplete_plan_before_proof_or_claim(
    tmp_path,
    monkeypatch,
    target_label,
    remove_field,
    expected_code,
):
    payload = _plan(target_label)
    payload.pop(remove_field)
    store, plan, run = _failed_store(
        tmp_path,
        target_label,
        plan_payload=payload,
    )
    proof_calls = []
    gate = {
        "dashboard": {},
        "payload": plan["payload"],
        "plan": plan,
        "run": run,
        "registry": {},
        "target_rows": [],
    }
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda *_args, **_kwargs: (gate, None),
    )
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: proof_calls.append("proof"),
    )

    status, response = product_server._preview_target_scoped_release_action(
        offer_id="3838616043",
        target_label=target_label,
    )

    assert status == 409
    assert response["code"] == expected_code
    assert response["available"] is False
    assert response["external_writes_performed"] == []
    assert proof_calls == []
    asserted = store.get_run(run["run_id"])
    assert asserted["targets"][0]["status"] == "FAILED"
    assert asserted["targets"][0]["attempts"] == 1
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_proofs"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_operations"
        ).fetchone()[0] == 0


def test_preview_is_readonly_redacted_and_never_refreshes(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)
    calls = []
    adapter = SimpleNamespace(
        build_official_target_proof=lambda req, *, allow_refresh: (
            calls.append((req.target_label, allow_refresh))
            or proof.durable_payload()
        )
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )

    status, payload = product_server._preview_target_scoped_release_action(
        offer_id=request.product_id,
        target_label=request.target_label,
    )

    assert status == 200
    assert payload["summary"] == {"target": "shopee:MY", "status": "safe"}
    assert "confirmation_token" not in payload
    assert "planned_command" not in payload
    assert payload["planned_command_digest"] == (
        request.planned_command_digest
    )
    assert calls == [("shopee:MY", False)]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_operations"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM release_target_retry_proofs"
        ).fetchone()[0] == 0


def test_global_rehost_observation_keeps_raw_facts_out_of_api_and_durable_json(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    raw_title = "Approved raw title must remain in memory"
    raw_description = "Approved raw description must remain in memory"
    raw_urls = [
        "https://cf.shopee.example/private-a.jpg",
        "https://cf.shopee.example/private-b.jpg",
    ]
    raw_ids = ["raw-image-id-a", "raw-image-id-b"]
    raw_response = '{"response":{"global_item_list":"private"}}'
    observation = evaluate_shopee_global_image_observation(
        approved_count=2,
        official_image_urls=raw_urls,
        official_image_ids=raw_ids,
    )
    proof = _proof(
        request,
        semantic_evidence={
            "source": "shopee:official_get_only",
            "official_copy_digest": approved_shopee_copy_digest(
                raw_title,
                raw_description,
            ),
            "global_image_observation": observation,
        },
        redacted_summary={
            "target": "shopee:MY",
            "global_image_status": observation["status"],
            "global_image_verification_scope": observation[
                "verification_scope"
            ],
            "approved_image_count": observation["approved_count"],
            "official_image_count": observation[
                "official_image_id_count"
            ],
            "manual_review_required": True,
        },
    )
    adapter = SimpleNamespace(
        build_official_target_proof=lambda *_args, **_kwargs: (
            proof.durable_payload()
        )
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )

    status, payload = product_server._preview_target_scoped_release_action(
        offer_id=request.product_id,
        target_label=request.target_label,
    )
    assert status == 200
    assert payload["summary"]["global_image_status"] == "warning"
    store.claim_target_scoped_operation(request=request, proof=proof)
    with sqlite3.connect(store.path) as connection:
        proof_json = connection.execute(
            "SELECT proof_json FROM release_target_retry_proofs"
        ).fetchone()[0]
        request_json = connection.execute(
            "SELECT request_json FROM release_target_retry_operations"
        ).fetchone()[0]
    serialized = json.dumps(payload) + proof_json + request_json

    forbidden = [
        raw_title,
        raw_description,
        *raw_urls,
        *raw_ids,
        raw_response,
        request.confirmation_token,
    ]
    assert all(value not in serialized for value in forbidden)
    assert "official_image_id_snapshot_digest" in proof_json
    assert "approved_source_image_manifest_digest" in request_json


def test_post_exact_single_target_success_and_replay_call_nothing(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)
    proof_calls = []
    execute_calls = []
    adapter = SimpleNamespace(
        build_official_target_proof=lambda req, *, allow_refresh: (
            proof_calls.append((req.target_label, allow_refresh))
            or proof.durable_payload()
        ),
        execute_target_scoped_operation=lambda req, supplied_proof: (
            execute_calls.append(
                (req.target_label, supplied_proof.proof_digest)
            )
            or _success_result()
        ),
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )
    body = _post_body(request, proof)

    status, first = product_server._execute_target_scoped_release_action(body)
    replay_status, replay = (
        product_server._execute_target_scoped_release_action(body)
    )

    assert status == 200
    assert first["external_writes_performed"] == [
        "shopee:regional_publish"
    ]
    assert replay_status == 200
    assert replay["idempotent"] is True
    assert proof_calls == [("shopee:MY", False)]
    assert len(execute_calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("approved_by", "NotKyle"),
        ("confirm_target_scoped_action", False),
        ("expected_revision", 42),
        ("failure_attempt", 99),
        ("payload_digest", "wrong"),
        ("planned_command_digest", "wrong"),
        ("preflight_digest", "wrong"),
        ("confirmation_token", "wrong"),
        ("planned_command", {"local_original_price": 1}),
    ],
)
def test_post_drift_fails_before_adapter_or_claim(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    proof = _proof(request)
    calls = []
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: SimpleNamespace(
            build_official_target_proof=lambda *_args, **_kwargs: calls.append(
                "proof"
            ),
            execute_target_scoped_operation=lambda *_args: calls.append(
                "execute"
            ),
        ),
    )
    body = {**_post_body(request, proof), field: value}

    status, _payload = product_server._execute_target_scoped_release_action(
        body
    )

    assert status in {400, 409}
    assert calls == []
    assert store.get_target_scoped_operation(
        run_id=request.run_id,
        target_label=request.target_label,
    ) is None


def test_post_proof_drift_fails_before_claim_or_execute(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path)
    request = _request(store, plan, "shopee:MY")
    preview_proof = _proof(request)
    changed_proof = _proof(
        request,
        semantic_evidence={
            "source": "official_platform_read",
            "target": request.target_label,
            "result": "safe",
            "observation": "changed after preview",
        },
    )
    execute_calls = []
    adapter = SimpleNamespace(
        build_official_target_proof=lambda *_args, **_kwargs: (
            changed_proof.durable_payload()
        ),
        execute_target_scoped_operation=lambda *_args: execute_calls.append(
            "execute"
        ),
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )

    status, payload = product_server._execute_target_scoped_release_action(
        _post_body(request, preview_proof)
    )

    assert status == 409
    assert payload["code"] == "official_target_proof_drift"
    assert execute_calls == []
    assert store.get_target_scoped_operation(
        run_id=request.run_id,
        target_label=request.target_label,
    ) is None


def test_adapter_unknown_after_write_is_truthful_and_replay_calls_nothing(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path, "ozon:RU")
    request = _request(store, plan, "ozon:RU")
    proof = _proof(request)
    calls = []

    class AmbiguousWriteError(RuntimeError):
        external_reference = "5687436857"
        external_write_evidence = {
            "external_writes_performed": ["ozon:stock:update"],
            "submission_accepted": True,
        }

    def execute(*_args):
        calls.append("execute")
        raise AmbiguousWriteError("official readback timed out")

    adapter = SimpleNamespace(
        build_official_target_proof=lambda *_args, **_kwargs: (
            calls.append("proof") or proof.durable_payload()
        ),
        execute_target_scoped_operation=execute,
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )
    body = _post_body(request, proof)

    status, payload = product_server._execute_target_scoped_release_action(
        body
    )
    replay_status, replay = (
        product_server._execute_target_scoped_release_action(body)
    )

    assert status == 409
    assert payload["reconciliation_required"] is True
    assert payload["durable_state_uncertain"] is True
    assert payload["external_writes_performed"] == ["ozon:stock:update"]
    assert replay_status == 409
    assert replay["operation_status"] == "RECONCILIATION_REQUIRED"
    assert calls == ["proof", "execute"]


def test_post_receipt_failure_becomes_truthful_reconciliation(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path, "ozon:RU")
    request = _request(store, plan, "ozon:RU")
    proof = _proof(request)
    result = TargetScopedOperationResult.from_value(
        {
            "succeeded": True,
            "readback_verified": True,
            "detail": "stock update and readback succeeded",
            "external_reference": "5687436857",
            "submission_accepted": False,
            "evidence": {
                "verified": True,
                "external_writes_performed": ["ozon:stock:update"],
            },
        }
    )
    original_success = store.record_target_scoped_success

    def fail_receipt(*_args, **_kwargs):
        raise sqlite3.OperationalError("injected receipt failure")

    store.record_target_scoped_success = fail_receipt
    adapter = SimpleNamespace(
        build_official_target_proof=lambda *_args, **_kwargs: (
            proof.durable_payload()
        ),
        execute_target_scoped_operation=lambda *_args: result,
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )

    status, payload = product_server._execute_target_scoped_release_action(
        _post_body(request, proof)
    )

    assert status == 409
    assert payload["reconciliation_required"] is True
    assert payload["external_writes_performed"] == ["ozon:stock:update"]
    operation = store.get_target_scoped_operation(
        run_id=request.run_id,
        target_label=request.target_label,
    )
    assert operation["status"] == "RECONCILIATION_REQUIRED"
    assert operation["result"]["external_writes_performed"] == [
        "ozon:stock:update"
    ]
    store.record_target_scoped_success = original_success


def test_double_receipt_failure_reports_write_and_prevents_redispatch(
    tmp_path,
    monkeypatch,
):
    store, plan, _run = _failed_store(tmp_path, "ozon:RU")
    request = _request(store, plan, "ozon:RU")
    proof = _proof(request)
    result = TargetScopedOperationResult.from_value(
        {
            "succeeded": True,
            "readback_verified": True,
            "detail": "stock update and readback succeeded",
            "external_reference": "5687436857",
            "submission_accepted": False,
            "evidence": {
                "verified": True,
                "external_writes_performed": ["ozon:stock:update"],
            },
        }
    )
    execute_calls = []
    adapter = SimpleNamespace(
        build_official_target_proof=lambda *_args, **_kwargs: (
            proof.durable_payload()
        ),
        execute_target_scoped_operation=lambda *_args: (
            execute_calls.append("execute") or result
        ),
    )
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_action_gate",
        lambda *_args, **_kwargs: _resolved_gate(store, plan, request),
    )
    monkeypatch.setattr(
        product_server, "_target_scoped_adapter_module", lambda: adapter
    )
    monkeypatch.setattr(
        store,
        "record_target_scoped_success",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("injected success receipt failure")
        ),
    )
    monkeypatch.setattr(
        store,
        "record_target_scoped_reconciliation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("injected reconciliation failure")
        ),
    )
    body = _post_body(request, proof)

    status, payload = product_server._execute_target_scoped_release_action(
        body
    )
    replay_status, replay = (
        product_server._execute_target_scoped_release_action(body)
    )

    assert status == 500
    assert payload["code"] == "target_scoped_durable_receipt_uncertain"
    assert payload["durable_state_uncertain"] is True
    assert payload["external_writes_performed"] == ["ozon:stock:update"]
    assert replay_status == 409
    assert replay["operation_status"] == "RUNNING"
    assert execute_calls == ["execute"]


def test_generic_publish_never_resets_or_dispatches_a_failed_target(
    monkeypatch,
):
    run = {
        "run_id": "run-1",
        "status": "FAILED",
        "targets": [
            {
                "target_label": "shopee:MY",
                "status": "FAILED",
                "storage_status": "FAILED",
            }
        ],
    }

    class StoreSpy:
        retry_calls = 0
        begin_calls = 0

        def get_run(self, _run_id):
            return run

        def retry_failed_targets(self, *_args, **_kwargs):
            self.retry_calls += 1
            raise AssertionError("generic retry must not be called")

        def begin_target(self, *_args, **_kwargs):
            self.begin_calls += 1
            raise AssertionError("FAILED target must not begin")

    store = StoreSpy()
    gate = {
        "dashboard": {},
        "payload": {"product_id": "1", "targets": ["shopee:MY"]},
        "run": run,
        "registry": {},
        "target_rows": [],
    }
    monkeypatch.setattr(
        release_store, "default_release_store", lambda: store
    )
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda *_args, **_kwargs: (gate, None),
    )

    status, payload = product_server._publish_selected_release(
        {
            "confirm_publish": True,
            "plan_id": "plan",
            "confirmation_token": "token",
        }
    )

    assert status == 409
    assert payload["code"] == "target_scoped_action_required"
    assert store.retry_calls == 0
    assert store.begin_calls == 0


def test_generic_publish_still_executes_a_first_pending_target(monkeypatch):
    run = {
        "run_id": "run-first",
        "status": "PENDING",
        "targets": [
            {
                "target_label": "shopee:MY",
                "status": "PENDING",
                "storage_status": "PENDING",
                "idempotency_key": "publish:shopee:MY:first",
            }
        ],
    }
    calls = []

    class StoreSpy:
        def get_run(self, _run_id):
            return run

        def begin_target(self, _run_id, label):
            calls.append(("begin", label))
            run["targets"][0]["status"] = "RUNNING"

        def record_target_success(
            self,
            _run_id,
            label,
            *,
            external_id,
            readback_evidence,
        ):
            calls.append(("success", label, external_id))
            run["targets"][0].update(
                {
                    "status": "SUCCEEDED",
                    "storage_status": "SUCCEEDED",
                    "external_id": external_id,
                    "readback": {"evidence": readback_evidence},
                }
            )
            run["status"] = "SUCCEEDED"

    store = StoreSpy()
    registration = AdapterRegistration(
        adapter_name="shopee-adapter",
        execute=lambda request: (
            calls.append(("execute", request.target_label))
            or AdapterExecutionResult(
                True,
                True,
                "first target succeeded",
                "item-0954",
                {"verified": True, "external_writes_performed": ["write"]},
            )
        ),
        consumes_unified_plan=True,
        validates_confirmation_token=True,
        preserves_idempotency_key=True,
        verifies_readback=True,
    )
    gate = {
        "dashboard": {},
        "payload": {
            "plan_id": "plan",
            "product_id": "3838616043",
            "seller_sku": "0954",
            "product_package_id": "product",
            "content_package_id": "content",
            "targets": ["shopee:MY"],
            "omnichannel_scope_digest": "scope",
        },
        "run": run,
        "registry": {"shopee-adapter": registration},
        "target_rows": [
            {
                "channel": "shopee",
                "site": "MY",
                "adapter": "shopee-adapter",
            }
        ],
    }
    monkeypatch.setattr(
        release_store, "default_release_store", lambda: store
    )
    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        lambda *_args, **_kwargs: (gate, None),
    )
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: gate["dashboard"],
    )

    status, payload = product_server._publish_selected_release(
        {
            "confirm_publish": True,
            "plan_id": "plan",
            "confirmation_token": "token",
        }
    )

    assert status == 200
    assert payload["completed"] is True
    assert calls == [
        ("begin", "shopee:MY"),
        ("execute", "shopee:MY"),
        ("success", "shopee:MY", "item-0954"),
    ]


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
    content_type: str = "application/json",
) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
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


def test_http_routes_are_exact_single_target_and_json_only(
    target_scoped_http_server,
    monkeypatch,
):
    calls = []

    def preview(*, offer_id, target_label):
        calls.append(("preview", offer_id, target_label))
        return 200, {
            "ok": True,
            "preview": True,
            "available": True,
            "target_label": target_label,
            "external_writes_performed": [],
        }

    def execute(data):
        calls.append(("execute", dict(data)))
        return 409, {
            "ok": False,
            "code": "target_scoped_reconciliation_required",
            "target_label": data["target_label"],
            "external_writes_performed": ["shopee:regional_publish"],
        }

    monkeypatch.setattr(
        product_server,
        "_preview_target_scoped_release_action",
        preview,
    )
    monkeypatch.setattr(
        product_server,
        "_execute_target_scoped_release_action",
        execute,
    )
    query = urllib.parse.urlencode(
        {"offer_id": "3838616043", "target_label": "shopee:MY"}
    )
    status, preview_payload = _http_json(
        target_scoped_http_server
        + "/api/product-workspace/release-target/"
        + "target-scoped-action-preview?"
        + query
    )
    post_body = {
        "target_label": "shopee:MY",
        "confirm_target_scoped_action": True,
    }
    post_status, post_payload = _http_json(
        target_scoped_http_server
        + "/api/product-workspace/release-target/target-scoped-action",
        method="POST",
        payload=post_body,
    )
    media_status, media_payload = _http_json(
        target_scoped_http_server
        + "/api/product-workspace/release-target/target-scoped-action",
        method="POST",
        payload=post_body,
        content_type="text/plain",
    )

    assert status == 200
    assert preview_payload["external_writes_performed"] == []
    assert post_status == 409
    assert post_payload["code"] == "target_scoped_reconciliation_required"
    assert media_status == 415
    assert "application/json" in media_payload["error"]
    assert calls == [
        ("preview", "3838616043", "shopee:MY"),
        ("execute", post_body),
    ]


def _reconciliation_case(
    tmp_path,
    target_label: str = "shopee:MY",
    *,
    prior_writes: list[str] | None = None,
):
    store, plan, _run = _failed_store(tmp_path, target_label)
    request = _request(store, plan, target_label)
    selected_logistics_ids = (
        [19010, 19011, 19012]
        if target_label == "shopee:MY"
        else [50001, 50007, 50010]
    )
    global_item_id = "103040506070809"
    proof = _proof(
        request,
        semantic_evidence={
            "source": "official_platform_read",
            "target": request.target_label,
            "result": "safe",
            "selected_logistics_ids": selected_logistics_ids,
            "selected_logistics_digest": canonical_digest(
                {"ids": selected_logistics_ids}
            ),
            "global_item_id": global_item_id,
        },
    )
    claim = store.claim_target_scoped_operation(
        request=request,
        proof=proof,
    )
    external_id = (
        "53914952703"
        if target_label == "shopee:MY"
        else "51564925929"
    )
    prior = TargetScopedOperationResult.from_value(
        {
            "succeeded": False,
            "readback_verified": False,
            "detail": (
                "regional publish accepted; official readback was unknown"
            ),
            "external_reference": external_id,
            "submission_accepted": True,
            "evidence": {
                "durable_state_uncertain": True,
                "reconciliation_required": True,
                "external_writes_performed": (
                    ["shopee:regional_publish"]
                    if prior_writes is None
                    else prior_writes
                ),
            },
        }
    )
    store.record_target_scoped_reconciliation(
        claim["operation"]["operation_digest"],
        result=prior,
    )
    context = store.target_scoped_reconciliation_context(
        plan_id=plan["plan_id"],
        target_label=target_label,
    )
    return store, plan, request, context


def _reconciliation_proof_value(
    request: TargetScopedReconciliationRequest,
    **overrides,
):
    now = datetime.now(timezone.utc)
    value = {
        "schema_version": "official-target-reconciliation-proof/v1",
        "reconciliation_mode": request.reconciliation_mode,
        "reconciliation_request_digest": request.request_digest,
        "plan_id": request.operation_request.plan_id,
        "run_id": request.operation_request.run_id,
        "target_label": request.operation_request.target_label,
        "operation_digest": request.operation_digest,
        "operation_proof_digest": request.operation_proof_digest,
        "prior_result_digest": request.prior_result_digest,
        "external_identity_digest": request.external_identity_digest,
        "original_proof_evidence_digest": (
            request.original_proof_evidence_digest
        ),
        "global_item_identity_digest": (
            request.global_item_identity_digest
        ),
        "provided_by": "03",
        "allow_refresh": False,
        "observed_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "checks": {
            "existing_item_unique": True,
            "existing_model_unique": True,
            "global_linkage_exact": True,
            "normal_status": True,
            "local_price_currency_exact": True,
            "image_count_primary_exact": True,
            "selected_logistics_exact": True,
        },
        "semantic_evidence": {
            "schema_version": "shopee-regional-readback-redacted/v1",
            "authority": "shopee_official_get",
            "target_label": request.operation_request.target_label,
            "external_identity_digest": request.external_identity_digest,
            "resolved_global_item_identity_digest": (
                request.global_item_identity_digest
            ),
            "listing_identity_verified": True,
            "image_count": 6,
            "logistics_count": 12,
            "status": "exact",
        },
        "redacted_summary": {
            "target_label": request.operation_request.target_label,
            "status": "exact_official_readback",
            "listing_identity_verified": True,
            "image_count": 6,
            "logistics_count": 12,
        },
        "external_writes_performed": [],
    }
    value.update(overrides)
    return value


def _reconciliation_proof(
    request: TargetScopedReconciliationRequest,
    **overrides,
):
    return OfficialTargetReconciliationProof.from_value(
        _reconciliation_proof_value(request, **overrides),
        request=request,
    )


def _reconciliation_result(
    request: TargetScopedReconciliationRequest,
    **evidence_overrides,
):
    evidence = {
        "verified": True,
        "reconciliation_mode": "official_get_only_durable_close",
        "checks": {
            "existing_item_unique": True,
            "existing_model_unique": True,
            "global_linkage_exact": True,
            "normal_status": True,
            "local_price_currency_exact": True,
            "image_count_primary_exact": True,
            "selected_logistics_exact": True,
        },
        "manual_review_required": True,
        "profit_status": "unverified",
        "external_writes_performed": [],
    }
    evidence.update(evidence_overrides)
    return TargetScopedOperationResult.from_value(
        {
            "succeeded": True,
            "readback_verified": True,
            "detail": "official GET-only regional readback is exact",
            "external_reference": request.external_id,
            "submission_accepted": True,
            "evidence": evidence,
        }
    )


def _patch_reconciliation_gate(
    monkeypatch,
    store: ReleaseStore,
    plan: dict,
    request: TargetScopedReconciliationRequest,
):
    monkeypatch.setattr(
        release_store,
        "default_release_store",
        lambda: store,
    )

    def gate(_data, *, store):
        return {
            "dashboard": {
                "product": {
                    "revision": (
                        request.operation_request.product_revision
                    )
                }
            },
            "payload": plan["payload"],
            "plan": plan,
            "run": store.get_run(request.operation_request.run_id),
            "registry": {},
            "target_rows": [],
        }, None

    monkeypatch.setattr(
        product_server,
        "_release_execution_readonly_gate",
        gate,
    )


def _reconciliation_post_body(
    preview: dict,
    request: TargetScopedReconciliationRequest,
) -> dict:
    base = request.operation_request
    return {
        "offer_id": base.product_id,
        "seller_sku": base.seller_sku,
        "publication_targets": list(request.publication_targets),
        "target_label": base.target_label,
        "plan_id": base.plan_id,
        "run_id": base.run_id,
        "confirmation_token": base.confirmation_token,
        "expected_revision": base.product_revision,
        "failure_attempt": base.failure_attempt,
        "payload_digest": base.payload_digest,
        "planned_command_digest": base.planned_command_digest,
        "preflight_digest": base.preflight_digest,
        "operation_digest": request.operation_digest,
        "operation_proof_digest": request.operation_proof_digest,
        "prior_result_digest": request.prior_result_digest,
        "external_identity_digest": request.external_identity_digest,
        "original_proof_evidence_digest": (
            request.original_proof_evidence_digest
        ),
        "global_item_identity_digest": (
            request.global_item_identity_digest
        ),
        "reconciliation_request_digest": request.request_digest,
        "reconciliation_proof_digest": preview[
            "reconciliation_proof_digest"
        ],
        "approved_by": "Kyle",
        "confirm_target_scoped_reconciliation": True,
    }


def _drift_original_selected_logistics(
    store: ReleaseStore,
    request: TargetScopedReconciliationRequest,
) -> None:
    with sqlite3.connect(store.path) as connection:
        # Simulate corruption that bypassed the normal SQLite immutability
        # trigger, so the read-path and close-path defenses are independently
        # exercised.
        connection.execute(
            "DROP TRIGGER trg_release_target_retry_proof_identity_immutable"
        )
        row = connection.execute(
            """
            SELECT proof_json
            FROM release_target_retry_proofs
            WHERE proof_digest = ?
            """,
            (request.operation_proof_digest,),
        ).fetchone()
        payload = json.loads(row[0])
        evidence = payload["semantic_evidence"]
        changed_ids = [*evidence["selected_logistics_ids"], 59999]
        evidence["selected_logistics_ids"] = changed_ids
        evidence["selected_logistics_digest"] = canonical_digest(
            {"ids": changed_ids}
        )
        connection.execute(
            """
            UPDATE release_target_retry_proofs
            SET proof_json = ?
            WHERE proof_digest = ?
            """,
            (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                request.operation_proof_digest,
            ),
        )
        connection.commit()


def _drift_original_global_item_id(
    store: ReleaseStore,
    request: TargetScopedReconciliationRequest,
) -> None:
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "DROP TRIGGER trg_release_target_retry_proof_identity_immutable"
        )
        row = connection.execute(
            """
            SELECT proof_json
            FROM release_target_retry_proofs
            WHERE proof_digest = ?
            """,
            (request.operation_proof_digest,),
        ).fetchone()
        payload = json.loads(row[0])
        payload["semantic_evidence"]["global_item_id"] = "different-global"
        connection.execute(
            """
            UPDATE release_target_retry_proofs
            SET proof_json = ?
            WHERE proof_digest = ?
            """,
            (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                request.operation_proof_digest,
            ),
        )
        connection.commit()


@pytest.mark.parametrize("target_label", ["shopee:MY", "shopee:VN"])
def test_store_getonly_close_is_atomic_and_preserves_prior_write(
    tmp_path,
    target_label,
):
    store, _plan_value, _base, context = _reconciliation_case(
        tmp_path,
        target_label,
    )
    assert context["eligible"] is True
    request = context["reconciliation_request"]
    proof = _reconciliation_proof(request)
    result = _reconciliation_result(request)
    before = store.get_run(request.operation_request.run_id)
    attempts = next(
        row["attempts"]
        for row in before["targets"]
        if row["target_label"] == target_label
    )
    with pytest.raises(ReleaseAuthorizationError, match="target-scoped"):
        store.retry_failed_targets(
            request.operation_request.run_id,
            [target_label],
        )

    completed = store.record_target_scoped_reconciled_success(
        request=request,
        proof=proof,
        result=result,
    )

    target = next(
        row
        for row in completed["targets"]
        if row["target_label"] == target_label
    )
    operation = target["target_scoped_operation"]
    assert target["status"] == "SUCCEEDED"
    assert target["storage_status"] == "SUCCEEDED"
    assert target["attempts"] == attempts
    assert operation["status"] == "SUCCEEDED"
    assert operation["result"]["prior_result_digest"] == (
        request.prior_result_digest
    )
    assert operation["result"]["prior_external_writes_performed"] == [
        "shopee:regional_publish"
    ]
    assert operation["result"][
        "reconciliation_external_writes_performed"
    ] == []
    assert operation["result"]["external_writes_performed"] == [
        "shopee:regional_publish"
    ]
    repeated = store.record_target_scoped_reconciled_success(
        request=request,
        proof=proof,
        result=result,
    )
    assert repeated["targets"][0]["attempts"] == attempts
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM release_target_retry_operations
            WHERE run_id = ? AND target_label = ?
            """,
            (
                request.operation_request.run_id,
                target_label,
            ),
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "mutation",
    ["missing_external_id", "wrong_storage_status", "wrong_prior_write"],
)
def test_reconciliation_context_fails_closed_on_durable_drift(
    tmp_path,
    mutation,
):
    if mutation == "wrong_prior_write":
        store, plan, _request_value, context = _reconciliation_case(
            tmp_path,
            prior_writes=["unexpected:write"],
        )
        assert context["eligible"] is False
        assert any(
            "prior Shopee publish evidence" in blocker
            for blocker in context["blockers"]
        )
        return
    store, plan, request, _context = _reconciliation_case(tmp_path)
    with sqlite3.connect(store.path) as connection:
        if mutation == "missing_external_id":
            connection.execute(
                """
                UPDATE release_target_runs SET external_id = NULL
                WHERE run_id = ? AND target_label = ?
                """,
                (request.run_id, request.target_label),
            )
        else:
            connection.execute(
                """
                UPDATE release_target_runs SET status = 'SUCCEEDED'
                WHERE run_id = ? AND target_label = ?
                """,
                (request.run_id, request.target_label),
            )
        connection.commit()
    context = store.target_scoped_reconciliation_context(
        plan_id=plan["plan_id"],
        target_label=request.target_label,
    )
    assert context["eligible"] is False


@pytest.mark.parametrize(
    ("table", "assignment"),
    [
        ("release_plans", "status = 'PENDING_APPROVAL'"),
        ("release_approvals", "status = 'SUPERSEDED'"),
        (
            "release_target_retry_operations",
            "status = 'FAILED_PRE_SUBMIT'",
        ),
    ],
)
def test_reconciliation_context_requires_exact_authority_and_status(
    tmp_path,
    table,
    assignment,
):
    store, plan, request, _context = _reconciliation_case(tmp_path)
    with sqlite3.connect(store.path) as connection:
        if table in {"release_plans", "release_approvals"}:
            connection.execute(
                f"UPDATE {table} SET {assignment} WHERE plan_id = ?",
                (plan["plan_id"],),
            )
        else:
            connection.execute(
                f"UPDATE {table} SET {assignment} "
                "WHERE run_id = ? AND target_label = ?",
                (request.run_id, request.target_label),
            )
        connection.commit()
    context = store.target_scoped_reconciliation_context(
        plan_id=plan["plan_id"],
        target_label=request.target_label,
    )
    assert context["eligible"] is False


def test_reconciliation_allowlist_excludes_ozon(tmp_path):
    store, plan, _request_value, _context = _reconciliation_case(
        tmp_path,
        "ozon:RU",
        prior_writes=["ozon:stock:update"],
    )
    context = store.target_scoped_reconciliation_context(
        plan_id=plan["plan_id"],
        target_label="ozon:RU",
    )
    assert context["eligible"] is False
    assert any("allowlisted" in value for value in context["blockers"])


def test_reconciliation_proof_rejects_raw_or_drifted_evidence(tmp_path):
    _store, _plan_value, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    with pytest.raises(TargetScopedContractError, match="raw copy"):
        _reconciliation_proof(
            request,
            semantic_evidence={
                "title": "raw official title",
                "status": "exact",
                "resolved_global_item_identity_digest": (
                    request.global_item_identity_digest
                ),
            },
        )
    with pytest.raises(
        TargetScopedContractError,
        match="identity does not match",
    ):
        _reconciliation_proof(
            request,
            prior_result_digest="f" * 64,
        )
    with pytest.raises(
        TargetScopedContractError,
        match="internal logistics IDs",
    ):
        _reconciliation_proof(
            request,
            redacted_summary={
                "status": "exact",
                "selected_logistics_ids": [19010],
            },
        )
    with pytest.raises(
        TargetScopedContractError,
        match="internal global item ID",
    ):
        _reconciliation_proof(
            request,
            redacted_summary={
                "status": "exact",
                "global_item_id": "must-not-be-exposed",
            },
        )


def test_preview_is_db_byte_stable_redacted_and_getonly(
    tmp_path,
    monkeypatch,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    proof = _reconciliation_proof(request)
    calls = []

    def build_proof(req, *, allow_refresh):
        calls.append(
            (
                "proof",
                req.operation_request.plan_id,
                req.operation_request.run_id,
                req.operation_request.target_label,
                tuple(
                    req.original_proof_evidence[
                        "selected_logistics_ids"
                    ]
                ),
                req.original_proof_evidence["global_item_id"],
                allow_refresh,
            )
        )
        return proof.durable_payload()

    adapter = SimpleNamespace(
        build_official_target_reconciliation_proof=build_proof
    )
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: adapter,
    )
    before = store.path.read_bytes()

    status, payload = (
        product_server._preview_target_scoped_reconciliation(
            offer_id=request.operation_request.product_id,
            target_label=request.operation_request.target_label,
        )
    )

    assert status == 200
    assert payload["available"] is True
    assert payload["external_writes_performed"] == []
    assert payload["state_mutations_performed"] == []
    assert "confirmation_token" not in payload
    assert "external_id" not in payload
    assert "planned_command" not in payload
    assert payload["external_identity_digest"] == (
        request.external_identity_digest
    )
    assert payload["original_proof_evidence_digest"] == (
        request.original_proof_evidence_digest
    )
    assert payload["selected_logistics_count"] == 3
    assert payload["global_item_identity_digest"] == (
        request.global_item_identity_digest
    )
    assert "selected_logistics_ids" not in payload
    assert "global_item_id" not in payload
    assert payload["run_id"] == request.operation_request.run_id
    assert store.path.read_bytes() == before
    assert calls == [
        (
            "proof",
            request.operation_request.plan_id,
            request.operation_request.run_id,
            request.operation_request.target_label,
            tuple(
                request.original_proof_evidence[
                    "selected_logistics_ids"
                ]
            ),
            request.original_proof_evidence["global_item_id"],
            False,
        )
    ]
    encoded = json.dumps(payload, sort_keys=True)
    for raw in (
        request.operation_request.confirmation_token,
        request.external_id,
        request.original_proof_evidence["global_item_id"],
        "raw official title",
        "https://",
    ):
        assert raw not in encoded


def test_original_proof_logistics_drift_blocks_preview_before_adapter(
    tmp_path,
    monkeypatch,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    calls = []
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: SimpleNamespace(
            build_official_target_reconciliation_proof=(
                lambda *_args, **_kwargs: calls.append("proof")
            )
        ),
    )
    _drift_original_selected_logistics(store, request)
    before_call = store.path.read_bytes()

    status, payload = product_server._preview_target_scoped_reconciliation(
        offer_id=request.operation_request.product_id,
        target_label=request.operation_request.target_label,
    )

    assert status == 409
    assert payload["code"] == "target_scoped_reconciliation_blocked"
    assert calls == []
    assert store.path.read_bytes() == before_call
    assert store.get_target_scoped_operation(
        run_id=request.operation_request.run_id,
        target_label=request.operation_request.target_label,
    )["status"] == "RECONCILIATION_REQUIRED"


def test_original_proof_global_item_drift_blocks_preview_before_adapter(
    tmp_path,
    monkeypatch,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    calls = []
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: SimpleNamespace(
            build_official_target_reconciliation_proof=(
                lambda *_args, **_kwargs: calls.append("proof")
            )
        ),
    )
    _drift_original_global_item_id(store, request)
    before_call = store.path.read_bytes()

    status, payload = product_server._preview_target_scoped_reconciliation(
        offer_id=request.operation_request.product_id,
        target_label=request.operation_request.target_label,
    )

    assert status == 409
    assert payload["code"] == "target_scoped_reconciliation_blocked"
    assert calls == []
    assert store.path.read_bytes() == before_call
    assert store.get_target_scoped_operation(
        run_id=request.operation_request.run_id,
        target_label=request.operation_request.target_label,
    )["status"] == "RECONCILIATION_REQUIRED"


def test_wrong_official_global_linkage_proof_is_unavailable_and_closes_zero(
    tmp_path,
    monkeypatch,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    wrong_digest = canonical_digest(
        {
            "provider": "shopee",
            "global_item_id": "different-global",
        }
    )
    raw_proof = _reconciliation_proof_value(request)
    raw_proof["global_item_identity_digest"] = wrong_digest
    raw_proof["semantic_evidence"] = {
        **raw_proof["semantic_evidence"],
        "resolved_global_item_identity_digest": wrong_digest,
    }
    calls = []
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: SimpleNamespace(
            build_official_target_reconciliation_proof=(
                lambda req, *, allow_refresh: (
                    calls.append(
                        (
                            req.original_proof_evidence["global_item_id"],
                            allow_refresh,
                        )
                    )
                    or raw_proof
                )
            ),
            reconcile_target_scoped_operation=lambda *_args: (
                calls.append("reconcile")
            ),
        ),
    )
    before = store.get_run(request.operation_request.run_id)

    status, payload = product_server._preview_target_scoped_reconciliation(
        offer_id=request.operation_request.product_id,
        target_label=request.operation_request.target_label,
    )

    assert status == 409
    assert payload["code"] == "official_reconciliation_proof_failed"
    assert payload["external_writes_performed"] == []
    assert calls == [
        (
            request.original_proof_evidence["global_item_id"],
            False,
        )
    ]
    assert store.get_run(request.operation_request.run_id) == before
    assert store.get_target_scoped_operation(
        run_id=request.operation_request.run_id,
        target_label=request.operation_request.target_label,
    )["status"] == "RECONCILIATION_REQUIRED"


def test_original_proof_logistics_drift_after_preview_blocks_post_close(
    tmp_path,
    monkeypatch,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    proof = _reconciliation_proof(request)
    calls = []
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: SimpleNamespace(
            build_official_target_reconciliation_proof=(
                lambda *_args, **_kwargs: (
                    calls.append("proof")
                    or proof.durable_payload()
                )
            ),
            reconcile_target_scoped_operation=lambda *_args: (
                calls.append("reconcile")
            ),
        ),
    )
    _, preview = product_server._preview_target_scoped_reconciliation(
        offer_id=request.operation_request.product_id,
        target_label=request.operation_request.target_label,
    )
    body = _reconciliation_post_body(preview, request)
    _drift_original_selected_logistics(store, request)
    before_call = store.path.read_bytes()

    status, payload = product_server._execute_target_scoped_reconciliation(
        body
    )

    assert status == 409
    assert payload["external_writes_performed"] == []
    assert calls == ["proof"]
    assert store.path.read_bytes() == before_call
    assert store.get_target_scoped_operation(
        run_id=request.operation_request.run_id,
        target_label=request.operation_request.target_label,
    )["status"] == "RECONCILIATION_REQUIRED"


def test_store_revalidates_original_proof_evidence_before_local_close(
    tmp_path,
):
    store, _plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    proof = _reconciliation_proof(request)
    result = _reconciliation_result(request)
    before = store.get_run(request.operation_request.run_id)
    _drift_original_selected_logistics(store, request)

    with pytest.raises(
        ReleaseAuthorizationError,
        match="original target-scoped proof evidence is invalid",
    ):
        store.record_target_scoped_reconciled_success(
            request=request,
            proof=proof,
            result=result,
        )

    after = store.get_run(request.operation_request.run_id)
    assert after == before
    assert store.get_target_scoped_operation(
        run_id=request.operation_request.run_id,
        target_label=request.operation_request.target_label,
    )["status"] == "RECONCILIATION_REQUIRED"


def test_http_getonly_close_success_repeat_zero_adapter_calls(
    tmp_path,
    monkeypatch,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    proof = _reconciliation_proof(request)
    result = _reconciliation_result(request)
    calls = []
    adapter = SimpleNamespace(
        build_official_target_reconciliation_proof=(
            lambda req, *, allow_refresh: (
                calls.append(("proof", allow_refresh))
                or proof.durable_payload()
            )
        ),
        reconcile_target_scoped_operation=lambda req, supplied: (
            calls.append(("reconcile", supplied.proof_digest))
            or result
        ),
    )
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: adapter,
    )
    preview_status, preview = (
        product_server._preview_target_scoped_reconciliation(
            offer_id=request.operation_request.product_id,
            target_label=request.operation_request.target_label,
        )
    )
    body = _reconciliation_post_body(preview, request)

    status, payload = (
        product_server._execute_target_scoped_reconciliation(body)
    )
    before_replay = list(calls)
    replay_status, replay = (
        product_server._execute_target_scoped_reconciliation(body)
    )

    assert preview_status == 200
    assert status == 200
    assert payload["operation_status"] == "SUCCEEDED"
    assert payload["external_writes_performed"] == []
    assert replay_status == 200
    assert replay["idempotent"] is True
    assert replay["external_writes_performed"] == []
    assert calls == before_replay == [
        ("proof", False),
        ("proof", False),
        ("reconcile", proof.proof_digest),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seller_sku", "9999"),
        ("publication_targets", ["shopee:VN"]),
        ("plan_id", "other-plan"),
        ("run_id", "other-run"),
        ("confirmation_token", "wrong-token"),
        ("expected_revision", 99),
        ("failure_attempt", 99),
        ("payload_digest", "wrong"),
        ("planned_command_digest", "wrong"),
        ("preflight_digest", "wrong"),
        ("operation_digest", "wrong"),
        ("operation_proof_digest", "wrong"),
        ("prior_result_digest", "wrong"),
        ("external_identity_digest", "wrong"),
        ("original_proof_evidence_digest", "wrong"),
        ("global_item_identity_digest", "wrong"),
        ("reconciliation_request_digest", "wrong"),
    ],
)
def test_post_identity_drift_calls_no_reconcile_or_close(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    proof = _reconciliation_proof(request)
    calls = []
    adapter = SimpleNamespace(
        build_official_target_reconciliation_proof=(
            lambda *_args, **_kwargs: calls.append("proof")
            or proof.durable_payload()
        ),
        reconcile_target_scoped_operation=lambda *_args: (
            calls.append("reconcile")
        ),
    )
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: adapter,
    )
    _, preview = product_server._preview_target_scoped_reconciliation(
        offer_id=request.operation_request.product_id,
        target_label=request.operation_request.target_label,
    )
    body = _reconciliation_post_body(preview, request)
    body[field] = value

    status, payload = (
        product_server._execute_target_scoped_reconciliation(body)
    )

    assert status == 409
    assert payload["external_writes_performed"] == []
    assert calls == ["proof"]
    operation = store.get_target_scoped_operation(
        run_id=request.operation_request.run_id,
        target_label=request.operation_request.target_label,
    )
    assert operation["status"] == "RECONCILIATION_REQUIRED"


@pytest.mark.parametrize(
    "body_change",
    [
        {"confirm_target_scoped_reconciliation": False},
        {"confirm_target_scoped_reconciliation": None},
        {"approved_by": "NotKyle"},
    ],
)
def test_post_requires_dedicated_literal_consent_and_kyle(
    tmp_path,
    monkeypatch,
    body_change,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    proof = _reconciliation_proof(request)
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: SimpleNamespace(
            build_official_target_reconciliation_proof=(
                lambda *_args, **_kwargs: proof.durable_payload()
            )
        ),
    )
    _, preview = product_server._preview_target_scoped_reconciliation(
        offer_id=request.operation_request.product_id,
        target_label=request.operation_request.target_label,
    )
    body = _reconciliation_post_body(preview, request)
    body.update(body_change)

    status, payload = (
        product_server._execute_target_scoped_reconciliation(body)
    )

    assert status == 400
    assert payload["external_writes_performed"] == []
    assert store.get_target_scoped_operation(
        run_id=request.operation_request.run_id,
        target_label=request.operation_request.target_label,
    )["status"] == "RECONCILIATION_REQUIRED"


def test_proof_drift_or_adapter_failure_leaves_durable_state_unchanged(
    tmp_path,
    monkeypatch,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    preview_proof = _reconciliation_proof(request)
    changed_proof = _reconciliation_proof(
        request,
        semantic_evidence={
            **dict(preview_proof.semantic_evidence),
            "status": "exact-but-changed",
        },
    )
    calls = []
    proofs = iter(
        [
            preview_proof.durable_payload(),
            changed_proof.durable_payload(),
        ]
    )
    adapter = SimpleNamespace(
        build_official_target_reconciliation_proof=(
            lambda *_args, **_kwargs: next(proofs)
        ),
        reconcile_target_scoped_operation=lambda *_args: (
            calls.append("reconcile")
        ),
    )
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: adapter,
    )
    _, preview = product_server._preview_target_scoped_reconciliation(
        offer_id=request.operation_request.product_id,
        target_label=request.operation_request.target_label,
    )
    body = _reconciliation_post_body(preview, request)

    status, payload = (
        product_server._execute_target_scoped_reconciliation(body)
    )

    assert status == 409
    assert payload["code"] == "official_reconciliation_proof_drift"
    assert payload["external_writes_performed"] == []
    assert calls == []
    assert store.get_target_scoped_operation(
        run_id=request.operation_request.run_id,
        target_label=request.operation_request.target_label,
    )["status"] == "RECONCILIATION_REQUIRED"


@pytest.mark.parametrize("failure", ["timeout", "mismatch"])
def test_getonly_reconcile_failure_performs_no_local_close(
    tmp_path,
    monkeypatch,
    failure,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    proof = _reconciliation_proof(request)

    def reconcile(*_args):
        if failure == "timeout":
            raise TimeoutError("official GET timed out")
        return TargetScopedOperationResult.from_value(
            {
                "succeeded": False,
                "readback_verified": False,
                "detail": "official identity mismatch",
                "external_reference": request.external_id,
                "submission_accepted": True,
                "evidence": {
                    "verified": False,
                    "external_writes_performed": [],
                },
            }
        )

    adapter = SimpleNamespace(
        build_official_target_reconciliation_proof=(
            lambda *_args, **_kwargs: proof.durable_payload()
        ),
        reconcile_target_scoped_operation=reconcile,
    )
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: adapter,
    )
    _, preview = product_server._preview_target_scoped_reconciliation(
        offer_id=request.operation_request.product_id,
        target_label=request.operation_request.target_label,
    )
    body = _reconciliation_post_body(preview, request)

    status, payload = (
        product_server._execute_target_scoped_reconciliation(body)
    )

    assert status == 409
    assert payload["external_writes_performed"] == []
    assert payload["state_mutations_performed"] == []
    assert store.get_target_scoped_operation(
        run_id=request.operation_request.run_id,
        target_label=request.operation_request.target_label,
    )["status"] == "RECONCILIATION_REQUIRED"


def test_local_close_failure_before_commit_is_zero_mutation(
    tmp_path,
    monkeypatch,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    proof = _reconciliation_proof(request)
    result = _reconciliation_result(request)
    adapter = SimpleNamespace(
        build_official_target_reconciliation_proof=(
            lambda *_args, **_kwargs: proof.durable_payload()
        ),
        reconcile_target_scoped_operation=lambda *_args: result,
    )
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: adapter,
    )
    _, preview = product_server._preview_target_scoped_reconciliation(
        offer_id=request.operation_request.product_id,
        target_label=request.operation_request.target_label,
    )
    body = _reconciliation_post_body(preview, request)
    monkeypatch.setattr(
        store,
        "record_target_scoped_reconciled_success",
        lambda **_kwargs: (_ for _ in ()).throw(
            ReleaseStoreError("simulated transaction rollback")
        ),
    )

    status, payload = (
        product_server._execute_target_scoped_reconciliation(body)
    )

    assert status == 502
    assert payload["external_writes_performed"] == []
    assert payload["state_mutations_performed"] == []
    operation = store.get_target_scoped_operation(
        run_id=request.operation_request.run_id,
        target_label=request.operation_request.target_label,
    )
    assert operation["status"] == "RECONCILIATION_REQUIRED"


def test_local_receipt_failure_recovers_or_fails_closed_truthfully(
    tmp_path,
    monkeypatch,
):
    store, plan, _base, context = _reconciliation_case(tmp_path)
    request = context["reconciliation_request"]
    proof = _reconciliation_proof(request)
    result = _reconciliation_result(request)
    adapter = SimpleNamespace(
        build_official_target_reconciliation_proof=(
            lambda *_args, **_kwargs: proof.durable_payload()
        ),
        reconcile_target_scoped_operation=lambda *_args: result,
    )
    _patch_reconciliation_gate(monkeypatch, store, plan, request)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: adapter,
    )
    _, preview = product_server._preview_target_scoped_reconciliation(
        offer_id=request.operation_request.product_id,
        target_label=request.operation_request.target_label,
    )
    body = _reconciliation_post_body(preview, request)
    original = store.record_target_scoped_reconciled_success

    def committed_then_failed(**kwargs):
        original(**kwargs)
        raise RuntimeError("response lost after commit")

    monkeypatch.setattr(
        store,
        "record_target_scoped_reconciled_success",
        committed_then_failed,
    )
    status, payload = (
        product_server._execute_target_scoped_reconciliation(body)
    )
    assert status == 200
    assert payload["durable_receipt_recovered"] is True
    assert payload["external_writes_performed"] == []

    store2, plan2, _base2, context2 = _reconciliation_case(
        tmp_path / "double"
    )
    request2 = context2["reconciliation_request"]
    proof2 = _reconciliation_proof(request2)
    result2 = _reconciliation_result(request2)
    _patch_reconciliation_gate(monkeypatch, store2, plan2, request2)
    monkeypatch.setattr(
        product_server,
        "_target_scoped_adapter_module",
        lambda: SimpleNamespace(
            build_official_target_reconciliation_proof=(
                lambda *_args, **_kwargs: proof2.durable_payload()
            ),
            reconcile_target_scoped_operation=lambda *_args: result2,
        ),
    )
    _, preview2 = product_server._preview_target_scoped_reconciliation(
        offer_id=request2.operation_request.product_id,
        target_label=request2.operation_request.target_label,
    )
    body2 = _reconciliation_post_body(preview2, request2)
    monkeypatch.setattr(
        store2,
        "record_target_scoped_reconciled_success",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("close failed")
        ),
    )
    monkeypatch.setattr(
        store2,
        "get_target_scoped_operation",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("recovery failed")
        ),
    )

    status2, payload2 = (
        product_server._execute_target_scoped_reconciliation(body2)
    )

    assert status2 == 500
    assert payload2["durable_state_uncertain"] is True
    assert payload2["external_writes_performed"] == []
    assert payload2["state_mutations_performed"] == [
        "unknown:local_durable_close"
    ]


def test_http_routes_expose_dedicated_reconciliation_seam(
    target_scoped_http_server,
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        product_server,
        "_preview_target_scoped_reconciliation",
        lambda *, offer_id, target_label: (
            calls.append(("preview", offer_id, target_label))
            or (
                200,
                {
                    "ok": True,
                    "available": True,
                    "external_writes_performed": [],
                },
            )
        ),
    )
    monkeypatch.setattr(
        product_server,
        "_execute_target_scoped_reconciliation",
        lambda data: (
            calls.append(("close", dict(data)))
            or (
                200,
                {
                    "ok": True,
                    "operation_status": "SUCCEEDED",
                    "external_writes_performed": [],
                },
            )
        ),
    )
    query = urllib.parse.urlencode(
        {"offer_id": "3838616043", "target_label": "shopee:MY"}
    )
    status, preview = _http_json(
        target_scoped_http_server
        + "/api/product-workspace/release-target/"
        + "target-scoped-reconciliation-preview?"
        + query
    )
    post_body = {
        "target_label": "shopee:MY",
        "confirm_target_scoped_reconciliation": True,
    }
    post_status, payload = _http_json(
        target_scoped_http_server
        + "/api/product-workspace/release-target/"
        + "target-scoped-reconciliation",
        method="POST",
        payload=post_body,
    )
    media_status, _media = _http_json(
        target_scoped_http_server
        + "/api/product-workspace/release-target/"
        + "target-scoped-reconciliation",
        method="POST",
        payload=post_body,
        content_type="text/plain",
    )
    assert status == 200
    assert preview["external_writes_performed"] == []
    assert post_status == 200
    assert payload["external_writes_performed"] == []
    assert media_status == 415
    assert calls == [
        ("preview", "3838616043", "shopee:MY"),
        ("close", post_body),
    ]
