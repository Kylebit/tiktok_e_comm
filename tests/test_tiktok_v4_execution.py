from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json

import pytest

from domains.channel_operations.tiktok_v4_execution import (
    TikTokV4ExecutionContractError,
    execute_tiktok_v4_plan,
    project_tiktok_v4_execution_plan,
)
from domains.channel_operations.tiktok_publisher import TikTokPublisher
from domains.product_operations import build_approved_publication_snapshot
from shared_platform.approved_publication_snapshot_projection import (
    project_release_plan_for_publication_snapshot,
)
from shared_platform.collectbox_action import CollectBoxTargetDetailIdentity
from test_approved_publication_snapshot_inputs import (
    _approved_from_projected,
    _raw_approval_inputs,
)
from test_approved_publication_snapshot import _approved_plan
from domains.product_operations import build_approved_publication_snapshot_inputs


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _snapshot() -> dict[str, object]:
    dashboard, payload = _raw_approval_inputs(sku_count=2)
    inputs = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )
    projection = project_release_plan_for_publication_snapshot(
        payload,
        approved_inputs=inputs,
    )
    assert projection.ready is True
    return build_approved_publication_snapshot(
        _approved_from_projected(projection.payload)
    ).payload()


def _context(snapshot: dict[str, object], target: str, detail_id: str) -> dict[str, object]:
    detail = CollectBoxTargetDetailIdentity(
        target_label=target,
        detail_id=detail_id,
        shop_id={"tiktok:LH_PH": "3001", "tiktok:LH_MY": "3002"}[target],
    ).internal_payload()
    labels = [row["target_label"] for row in snapshot["publication_targets"]]
    context = {
        "schema_version": "collectbox-tiktok-publish-context/v1",
        "plan_id": snapshot["plan_id"],
        "offer_id": snapshot["offer_id"],
        "product_revision": snapshot["product_revision"],
        "payload_digest": snapshot["bindings"]["release_payload_digest"].removeprefix(
            "sha256:"
        ),
        "targets_digest": _digest(labels),
        "action_id": "action-v4-1",
        "platform": "TIKTOK",
        "common_identity_digest": "1" * 64,
        "receipt_digest": "2" * 64,
        "target_detail_identity": detail,
    }
    context["publish_identity_digest"] = _digest(context)
    return context


class CategoryResolver:
    def __init__(self, *, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.calls: list[tuple[str, dict[str, object]]] = []

    def resolve(self, *, target: dict[str, str], product: dict[str, object], skus):
        label = target["target_label"]
        self.calls.append((label, deepcopy(product)))
        if label in self.fail:
            raise RuntimeError("official category tree unavailable")
        category = {
            "id": "600338" if label.endswith("PH") else "600339",
            "name": "Refrigerator Magnets",
            "path": [
                {"id": "600001", "name": "Home Decor"},
                {
                    "id": "600338" if label.endswith("PH") else "600339",
                    "name": "Refrigerator Magnets",
                },
            ],
        }
        receipt = {
            "schema_version": "tiktok-official-category-resolution/v1",
            "target_label": label,
            "category": category,
            "enabled": True,
            "metadata_valid": True,
            "resolution": "EXACT",
        }
        receipt["evidence_digest"] = "sha256:" + _digest(receipt)
        return receipt


def _project(*, resolver: CategoryResolver | None = None):
    snapshot = _snapshot()
    contexts = {
        "tiktok:LH_PH": _context(snapshot, "tiktok:LH_PH", "7001"),
        "tiktok:LH_MY": _context(snapshot, "tiktok:LH_MY", "7002"),
    }
    return snapshot, project_tiktok_v4_execution_plan(
        snapshot,
        collectbox_contexts=contexts,
        category_resolver=resolver or CategoryResolver(),
    )


def test_projection_uses_only_frozen_v4_facts_and_exact_durable_identity():
    snapshot, plan = _project()

    assert plan["schema_version"] == "tiktok-v4-execution-plan/v1"
    assert plan["snapshot_digest"] == snapshot["snapshot_digest"]
    assert [row["target_label"] for row in plan["targets"]] == [
        "tiktok:LH_PH",
        "tiktok:LH_MY",
    ]
    ph = plan["targets"][0]
    assert ph["product"]["title"] == snapshot["product"]["title"]
    assert ph["product"]["description"] == snapshot["product"]["description"]
    assert ph["control"]["detail_id"] == "7001"
    assert ph["control"]["shop_id"] == "3001"
    assert [row["model_sku"] for row in ph["skus"]] == ["0958", "0959"]
    assert [row["price"] for row in ph["skus"]] == ["129", "132"]
    assert ph["parent_parcel"] == {
        "weight_kg": format(
            max(Decimal(row["parcel"]["weight_kg"]) for row in snapshot["skus"]),
            "f",
        ),
        "package_cm": ["8", "8", "2"],
        "derived_from": "maximum_approved_sku_parcels",
    }
    assert ph["publisher_snapshot"]["targets"][0][
        "expected_category_id"
    ] == "600338"

    class ExactDraftTransport:
        def read_draft(self, target):
            return {"target_label": target["target_label"]}

        def draft_matches(self, target, draft):
            return draft["target_label"] == target["target_label"]

        def save_approved_draft(self, target, draft):  # pragma: no cover
            raise AssertionError("preflight must be read-only")

        def submit(self, target):  # pragma: no cover
            raise AssertionError("preflight must be read-only")

    preflight = TikTokPublisher(ExactDraftTransport()).preflight(
        ph["publisher_snapshot"]
    )
    assert preflight["targets"] == [
        {"target_label": "tiktok:LH_PH", "status": "READY"}
    ]


def test_tampered_v4_snapshot_fails_before_category_or_control_reads():
    snapshot = _snapshot()
    snapshot["skus"][0]["prices"]["tiktok:LH_PH"]["amount"] = "999"
    resolver = CategoryResolver()

    with pytest.raises(TikTokV4ExecutionContractError, match="snapshot"):
        project_tiktok_v4_execution_plan(
            snapshot,
            collectbox_contexts={},
            category_resolver=resolver,
        )

    assert resolver.calls == []


def test_deferred_category_failure_blocks_only_that_store():
    snapshot = _snapshot()
    resolver = CategoryResolver(fail={"tiktok:LH_PH"})
    contexts = {
        "tiktok:LH_PH": _context(snapshot, "tiktok:LH_PH", "7001"),
        "tiktok:LH_MY": _context(snapshot, "tiktok:LH_MY", "7002"),
    }

    plan = project_tiktok_v4_execution_plan(
        snapshot,
        collectbox_contexts=contexts,
        category_resolver=resolver,
    )

    assert [row["target_label"] for row in plan["targets"]] == [
        "tiktok:LH_MY"
    ]
    assert plan["blocked_targets"] == [
        {
            "target_label": "tiktok:LH_PH",
            "reason_code": "CATEGORY_CONFIRMATION_REQUIRED",
        }
    ]
    assert [label for label, _ in resolver.calls] == [
        "tiktok:LH_PH",
        "tiktok:LH_MY",
    ]


def test_control_identity_drift_blocks_one_store_without_rebuilding_from_dashboard():
    snapshot = _snapshot()
    contexts = {
        "tiktok:LH_PH": _context(snapshot, "tiktok:LH_PH", "7001"),
        "tiktok:LH_MY": _context(snapshot, "tiktok:LH_MY", "7002"),
    }
    contexts["tiktok:LH_PH"]["offer_id"] = "999999"

    plan = project_tiktok_v4_execution_plan(
        snapshot,
        collectbox_contexts=contexts,
        category_resolver=CategoryResolver(),
    )

    assert [row["target_label"] for row in plan["targets"]] == [
        "tiktok:LH_MY"
    ]
    assert plan["blocked_targets"] == [
        {
            "target_label": "tiktok:LH_PH",
            "reason_code": "DRAFT_IDENTITY_CONFLICT",
        }
    ]


def test_already_approved_provider_categories_do_not_query_runtime_resolver():
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    resolver = CategoryResolver(fail={"tiktok:LH_PH", "tiktok:LH_MY"})
    contexts = {
        "tiktok:LH_PH": _context(snapshot, "tiktok:LH_PH", "7001"),
        "tiktok:LH_MY": _context(snapshot, "tiktok:LH_MY", "7002"),
    }

    plan = project_tiktok_v4_execution_plan(
        snapshot,
        collectbox_contexts=contexts,
        category_resolver=resolver,
    )

    assert resolver.calls == []
    assert [
        row["publisher_snapshot"]["targets"][0]["expected_category_id"]
        for row in plan["targets"]
    ] == ["600338", "600339"]


def test_execution_plan_rejects_internal_identity_drift_after_rehash():
    _, plan = _project()
    tampered = deepcopy(plan)
    command = tampered["targets"][0]
    command["publisher_snapshot"]["targets"][0]["detail_id"] = "9999"
    command_body = dict(command)
    command_body.pop("command_digest")
    command["command_digest"] = "sha256:" + _digest(command_body)
    plan_body = dict(tampered)
    plan_body.pop("plan_digest")
    tampered["plan_digest"] = "sha256:" + _digest(plan_body)

    with pytest.raises(TikTokV4ExecutionContractError, match="identity"):
        execute_tiktok_v4_plan(
            tampered,
            publisher=Publisher(),
            storefront_readback=Readback(),
        )


def test_execution_plan_rejects_non_string_sku_identity_after_rehash():
    _, plan = _project()
    tampered = deepcopy(plan)
    command = tampered["targets"][0]
    sku = command["skus"][0]
    old_model = sku["model_sku"]
    variant = sku["variant_key"]
    price = sku["price"]
    sku["model_sku"] = None
    provider_target = command["publisher_snapshot"]["targets"][0]
    del provider_target["expected_sku_prices"][old_model]
    provider_target["expected_sku_prices"]["None"] = price
    provider_target["expected_variant_model_skus"][variant] = None
    command_body = dict(command)
    command_body.pop("command_digest")
    command["command_digest"] = "sha256:" + _digest(command_body)
    plan_body = dict(tampered)
    plan_body.pop("plan_digest")
    tampered["plan_digest"] = "sha256:" + _digest(plan_body)

    with pytest.raises(TikTokV4ExecutionContractError, match="SKU identity"):
        execute_tiktok_v4_plan(
            tampered,
            publisher=Publisher(),
            storefront_readback=Readback(),
        )


class Publisher:
    def __init__(self, *, preflight=None, dispatch=None) -> None:
        self.preflight_values = preflight or {}
        self.dispatch_values = dispatch or {}
        self.preflight_calls: list[str] = []
        self.publish_calls: list[str] = []

    def preflight(self, snapshot):
        label = snapshot["targets"][0]["target_label"]
        self.preflight_calls.append(label)
        value = self.preflight_values.get(label, "READY")
        if isinstance(value, BaseException):
            raise value
        return {
            "schema_version": "tiktok-publish-preflight/v1",
            "offer_id": snapshot["offer_id"],
            "plan_id": snapshot["plan_id"],
            "snapshot_digest": "unused-by-thin-executor",
            "targets": [{"target_label": label, "status": value}],
        }

    def publish(self, snapshot, preflight=None):
        label = snapshot["targets"][0]["target_label"]
        self.publish_calls.append(label)
        value = self.dispatch_values.get(label, "ACCEPTED")
        if isinstance(value, BaseException):
            raise value
        return {
            "schema_version": "tiktok-publish-receipt/v1",
            "offer_id": snapshot["offer_id"],
            "plan_id": snapshot["plan_id"],
            "targets": [
                {
                    "target_label": label,
                    "outcome": value,
                    "provider_code": "200",
                    "external_write_count": 1 if value == "ACCEPTED" else 0,
                    "write_request_count": 1,
                }
            ],
        }


class Readback:
    def __init__(self, values=None) -> None:
        self.values = values or {}
        self.calls: list[tuple[str, str]] = []

    def readback(self, *, command, dispatch):
        label = command["target_label"]
        self.calls.append((label, dispatch["outcome"]))
        value = self.values.get(
            label,
            {"authority": "UNAVAILABLE", "status": "UNAVAILABLE"},
        )
        if isinstance(value, BaseException):
            raise value
        return {"target_label": label, **value}


def test_preflight_fails_closed_per_store_and_other_store_continues():
    _, plan = _project()
    publisher = Publisher(
        preflight={"tiktok:LH_PH": "READ_UNKNOWN"},
    )
    readback = Readback()

    receipt = execute_tiktok_v4_plan(
        plan,
        publisher=publisher,
        storefront_readback=readback,
    )

    assert publisher.publish_calls == ["tiktok:LH_MY"]
    assert readback.calls == [("tiktok:LH_MY", "ACCEPTED")]
    assert receipt["status"] == "PARTIAL"
    assert [row["status"] for row in receipt["targets"]] == [
        "FAILED",
        "PROCESSING",
    ]


def test_dispatch_exception_still_runs_readback_and_official_truth_wins():
    _, plan = _project()
    publisher = Publisher(
        dispatch={"tiktok:LH_PH": RuntimeError("timeout after submit")}
    )
    readback = Readback(
        {
            "tiktok:LH_PH": {
                "authority": "OFFICIAL_STOREFRONT",
                "status": "VERIFIED",
                "exact": True,
            },
            "tiktok:LH_MY": {
                "authority": "MIAOSHOU_DRAFT",
                "status": "VERIFIED",
                "exact": True,
            },
        }
    )

    receipt = execute_tiktok_v4_plan(
        plan,
        publisher=publisher,
        storefront_readback=readback,
    )

    assert readback.calls == [
        ("tiktok:LH_PH", "UNKNOWN"),
        ("tiktok:LH_MY", "ACCEPTED"),
    ]
    assert [row["status"] for row in receipt["targets"]] == [
        "PUBLISHED",
        "PROCESSING",
    ]
    assert receipt["status"] == "PROCESSING"


def test_no_official_storefront_readback_never_reports_published():
    _, plan = _project()
    receipt = execute_tiktok_v4_plan(
        plan,
        publisher=Publisher(),
        storefront_readback=Readback(
            {
                "tiktok:LH_PH": {
                    "authority": "MIAOSHOU_DRAFT",
                    "status": "VERIFIED",
                    "exact": True,
                },
                "tiktok:LH_MY": {
                    "authority": "UNAVAILABLE",
                    "status": "UNAVAILABLE",
                },
            }
        ),
    )

    assert receipt["status"] == "PROCESSING"
    assert {row["status"] for row in receipt["targets"]} == {"PROCESSING"}
