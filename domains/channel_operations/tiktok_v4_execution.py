"""TikTok execution boundary for an approved-publication-snapshot/v4.

Product facts are projected only from the frozen v4 document.  The second
input is control identity from the durable collect-box action: it may identify
the exact Miaoshou draft and shop, but it cannot replace title, category, SKU,
price, image or parcel facts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Any, Protocol

from domains.product_operations.approved_publication_snapshot import (
    ApprovedPublicationSnapshotError,
    validate_approved_publication_snapshot,
)
from shared_platform.collectbox_action import CollectBoxTargetDetailIdentity


TIKTOK_V4_EXECUTION_PLAN_SCHEMA = "tiktok-v4-execution-plan/v1"
TIKTOK_V4_TARGET_COMMAND_SCHEMA = "tiktok-v4-target-command/v1"
TIKTOK_V4_EXECUTION_RECEIPT_SCHEMA = "tiktok-v4-execution-receipt/v1"
TIKTOK_OFFICIAL_CATEGORY_RESOLUTION_SCHEMA = (
    "tiktok-official-category-resolution/v1"
)
_PUBLISHER_SNAPSHOT_SCHEMA = "approved-tiktok-publish-snapshot/v2"
_READY_PREFLIGHT_STATUSES = frozenset({"READY", "REPAIR_REQUIRED"})


class TikTokV4ExecutionContractError(ValueError):
    """Raised when the immutable execution hand-off fails closed."""


class TikTokCategoryResolver(Protocol):
    def resolve(
        self,
        *,
        target: dict[str, str],
        product: dict[str, object],
        skus: list[dict[str, object]],
    ) -> Mapping[str, object]: ...


class TikTokTargetPublisher(Protocol):
    def preflight(self, snapshot: Mapping[str, object]) -> Mapping[str, object]: ...

    def publish(
        self,
        snapshot: Mapping[str, object],
        preflight: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]: ...


class TikTokStorefrontReadback(Protocol):
    def readback(
        self,
        *,
        command: Mapping[str, object],
        dispatch: Mapping[str, object],
    ) -> Mapping[str, object]: ...


def project_tiktok_v4_execution_plan(
    snapshot: Mapping[str, object],
    *,
    collectbox_contexts: Mapping[str, Mapping[str, object]],
    category_resolver: TikTokCategoryResolver | None,
) -> dict[str, object]:
    """Project independent per-store commands from one verified v4 snapshot."""

    try:
        frozen = validate_approved_publication_snapshot(snapshot).payload()
    except (ApprovedPublicationSnapshotError, TypeError, ValueError) as error:
        raise TikTokV4ExecutionContractError(
            f"approved v4 snapshot is invalid: {error}"
        ) from None
    if not isinstance(collectbox_contexts, Mapping):
        raise TikTokV4ExecutionContractError(
            "durable TikTok control identities must be a mapping"
        )

    target_rows = [
        row
        for row in frozen["publication_targets"]
        if row["platform"] == "tiktok"
    ]
    if not target_rows:
        raise TikTokV4ExecutionContractError(
            "approved snapshot selects no TikTok targets"
        )
    selected_labels = [row["target_label"] for row in target_rows]
    extras = sorted(set(collectbox_contexts).difference(selected_labels))
    if extras:
        raise TikTokV4ExecutionContractError(
            "durable TikTok control identities contain unapproved targets"
        )

    all_labels = [row["target_label"] for row in frozen["publication_targets"]]
    commands: list[dict[str, object]] = []
    blocked: list[dict[str, str]] = []
    for target in target_rows:
        label = target["target_label"]
        context = collectbox_contexts.get(label)
        if context is None:
            blocked.append(
                {
                    "target_label": label,
                    "reason_code": "DRAFT_IDENTITY_UNAVAILABLE",
                }
            )
            continue
        try:
            control = _verified_control_identity(
                frozen,
                target_label=label,
                context=context,
                all_target_labels=all_labels,
            )
        except TikTokV4ExecutionContractError:
            blocked.append(
                {
                    "target_label": label,
                    "reason_code": "DRAFT_IDENTITY_CONFLICT",
                }
            )
            continue

        category_row = frozen["categories_by_target"][label]
        try:
            category, category_evidence_digest = _resolved_category(
                category_row,
                target=target,
                product=frozen["product"],
                skus=frozen["skus"],
                resolver=category_resolver,
            )
        except (TikTokV4ExecutionContractError, RuntimeError, TypeError, ValueError):
            blocked.append(
                {
                    "target_label": label,
                    "reason_code": "CATEGORY_CONFIRMATION_REQUIRED",
                }
            )
            continue

        command = _target_command(
            frozen,
            target=target,
            control=control,
            category=category,
            category_evidence_digest=category_evidence_digest,
        )
        commands.append(command)

    body: dict[str, object] = {
        "schema_version": TIKTOK_V4_EXECUTION_PLAN_SCHEMA,
        "offer_id": frozen["offer_id"],
        "plan_id": frozen["plan_id"],
        "product_revision": frozen["product_revision"],
        "snapshot_digest": frozen["snapshot_digest"],
        "target_order": selected_labels,
        "targets": commands,
        "blocked_targets": blocked,
    }
    body["plan_digest"] = "sha256:" + _digest(body)
    return body


def execute_tiktok_v4_plan(
    execution_plan: Mapping[str, object],
    *,
    publisher: TikTokTargetPublisher,
    storefront_readback: TikTokStorefrontReadback,
) -> dict[str, object]:
    """Execute each TikTok store independently and always read after dispatch."""

    plan = _verified_execution_plan(execution_plan)
    outcomes: dict[str, dict[str, object]] = {
        row["target_label"]: {
            "target_label": row["target_label"],
            "status": "FAILED",
            "reason_code": row["reason_code"],
            "dispatch_attempted": False,
            "dispatch_outcome": "NOT_ATTEMPTED",
            "readback_authority": "NOT_ATTEMPTED",
            "readback_status": "NOT_ATTEMPTED",
            "external_write_count": 0,
            "retry_safe": True,
        }
        for row in plan["blocked_targets"]
    }

    for command in plan["targets"]:
        label = command["target_label"]
        publisher_snapshot = command["publisher_snapshot"]
        try:
            preflight = publisher.preflight(deepcopy(publisher_snapshot))
            preflight_status = _preflight_status(preflight, label)
        except Exception:
            preflight = None
            preflight_status = "READ_UNKNOWN"
        if preflight_status not in _READY_PREFLIGHT_STATUSES:
            outcomes[label] = {
                "target_label": label,
                "status": "FAILED",
                "reason_code": "PREFLIGHT_REJECTED",
                "dispatch_attempted": False,
                "dispatch_outcome": "NOT_ATTEMPTED",
                "readback_authority": "NOT_ATTEMPTED",
                "readback_status": "NOT_ATTEMPTED",
                "external_write_count": 0,
                "retry_safe": True,
            }
            continue

        try:
            receipt = publisher.publish(
                deepcopy(publisher_snapshot),
                deepcopy(preflight),
            )
            dispatch = _dispatch_fact(receipt, label)
        except Exception:
            dispatch = {
                "target_label": label,
                "outcome": "UNKNOWN",
                "external_write_count": None,
            }

        try:
            raw_readback = storefront_readback.readback(
                command=deepcopy(command),
                dispatch=deepcopy(dispatch),
            )
            readback = _readback_fact(raw_readback, label)
        except Exception:
            readback = {
                "target_label": label,
                "authority": "UNAVAILABLE",
                "status": "UNAVAILABLE",
                "exact": False,
            }
        outcomes[label] = _classify_target(dispatch, readback)

    ordered = [outcomes[label] for label in plan["target_order"]]
    status = _aggregate_status(ordered)
    write_counts = [row["external_write_count"] for row in ordered]
    return {
        "schema_version": TIKTOK_V4_EXECUTION_RECEIPT_SCHEMA,
        "offer_id": plan["offer_id"],
        "plan_id": plan["plan_id"],
        "product_revision": plan["product_revision"],
        "snapshot_digest": plan["snapshot_digest"],
        "plan_digest": plan["plan_digest"],
        "status": status,
        "published_target_count": sum(
            row["status"] == "PUBLISHED" for row in ordered
        ),
        "processing_target_count": sum(
            row["status"] == "PROCESSING" for row in ordered
        ),
        "failed_target_count": sum(row["status"] == "FAILED" for row in ordered),
        "external_write_count": (
            sum(write_counts) if all(type(value) is int for value in write_counts) else None
        ),
        "targets": ordered,
    }


def _target_command(
    frozen: Mapping[str, object],
    *,
    target: Mapping[str, str],
    control: Mapping[str, str],
    category: Mapping[str, object],
    category_evidence_digest: str,
) -> dict[str, object]:
    label = target["target_label"]
    skus: list[dict[str, object]] = []
    model_prices: dict[str, str] = {}
    variant_models: dict[str, str] = {}
    sku_parcels: dict[str, dict[str, object]] = {}
    currency: str | None = None
    for frozen_sku in frozen["skus"]:
        price = frozen_sku["prices"][label]
        if currency is None:
            currency = price["currency"]
        elif currency != price["currency"]:
            raise TikTokV4ExecutionContractError(
                f"{label} SKU currencies conflict"
            )
        sku = {
            "variant_key": frozen_sku["variant_key"],
            "seller_sku": frozen_sku["seller_sku"],
            "model_sku": frozen_sku["model_sku"],
            "specification": deepcopy(frozen_sku["specification"]),
            "price": price["amount"],
            "currency": price["currency"],
            "parcel": deepcopy(frozen_sku["parcel"]),
            "variant_images": deepcopy(frozen_sku["variant_images"]),
        }
        skus.append(sku)
        model_prices[sku["model_sku"]] = sku["price"]
        variant_models[sku["variant_key"]] = sku["model_sku"]
        sku_parcels[sku["variant_key"]] = deepcopy(sku["parcel"])
    assert currency is not None
    parent_parcel = _derived_parent_parcel(skus)
    product = {
        "title": frozen["product"]["title"],
        "description": frozen["product"]["description"],
        "images": deepcopy(frozen["product"]["images"]),
        "main_category": deepcopy(frozen["product"]["main_category"]),
        "target_category": deepcopy(category),
    }
    publisher_target = {
        "target_label": label,
        **dict(control),
        "expected_price": skus[0]["price"],
        "expected_sku_prices": model_prices,
        "expected_variant_model_skus": variant_models,
        "expected_weight_kg": parent_parcel["weight_kg"],
        "expected_package_cm": parent_parcel["package_cm"],
        "expected_sku_parcels": sku_parcels,
        "expected_currency": currency,
        "expected_category_id": category["id"],
        "category_evidence_digest": category_evidence_digest.removeprefix(
            "sha256:"
        ),
    }
    publisher_snapshot = {
        "schema_version": _PUBLISHER_SNAPSHOT_SCHEMA,
        "offer_id": frozen["offer_id"],
        "plan_id": frozen["plan_id"],
        "product_revision": frozen["product_revision"],
        "payload_digest": frozen["bindings"]["release_payload_digest"].removeprefix(
            "sha256:"
        ),
        "targets": [publisher_target],
        "unavailable_targets": [],
    }
    command: dict[str, object] = {
        "schema_version": TIKTOK_V4_TARGET_COMMAND_SCHEMA,
        "snapshot_digest": frozen["snapshot_digest"],
        "offer_id": frozen["offer_id"],
        "plan_id": frozen["plan_id"],
        "product_revision": frozen["product_revision"],
        "target_label": label,
        "target": deepcopy(target),
        "control": dict(control),
        "product": product,
        "skus": skus,
        "parent_parcel": parent_parcel,
        "publisher_snapshot": publisher_snapshot,
    }
    command["command_digest"] = "sha256:" + _digest(command)
    return command


def _verified_control_identity(
    frozen: Mapping[str, object],
    *,
    target_label: str,
    context: Mapping[str, object],
    all_target_labels: list[str],
) -> dict[str, str]:
    if (
        isinstance(context, Mapping)
        and context.get("schema_version")
        == "collectbox-tiktok-v4-publish-context/v1"
    ):
        return _verified_v4_draft_identity(
            frozen,
            target_label=target_label,
            context=context,
        )
    required = {
        "schema_version",
        "plan_id",
        "offer_id",
        "product_revision",
        "payload_digest",
        "targets_digest",
        "action_id",
        "platform",
        "common_identity_digest",
        "receipt_digest",
        "target_detail_identity",
        "publish_identity_digest",
    }
    if not isinstance(context, Mapping) or set(context) != required:
        raise TikTokV4ExecutionContractError("TikTok control identity is malformed")
    expected_payload = frozen["bindings"]["release_payload_digest"].removeprefix(
        "sha256:"
    )
    if (
        context["schema_version"] != "collectbox-tiktok-publish-context/v1"
        or context["plan_id"] != frozen["plan_id"]
        or context["offer_id"] != frozen["offer_id"]
        or context["product_revision"] != frozen["product_revision"]
        or context["payload_digest"] != expected_payload
        or context["targets_digest"] != _digest(all_target_labels)
        or context["platform"] != "TIKTOK"
        or not _plain_sha(context["common_identity_digest"])
        or not _plain_sha(context["receipt_digest"])
        or type(context["action_id"]) is not str
        or not context["action_id"]
    ):
        raise TikTokV4ExecutionContractError("TikTok control identity drifted")
    raw_detail = context["target_detail_identity"]
    if not isinstance(raw_detail, Mapping):
        raise TikTokV4ExecutionContractError("TikTok draft identity is missing")
    try:
        detail = CollectBoxTargetDetailIdentity(
            target_label=raw_detail.get("target_label"),
            detail_id=raw_detail.get("detail_id"),
            shop_id=raw_detail.get("shop_id"),
        ).internal_payload()
    except (TypeError, ValueError):
        raise TikTokV4ExecutionContractError("TikTok draft identity is invalid") from None
    if detail != raw_detail or detail["target_label"] != target_label:
        raise TikTokV4ExecutionContractError("TikTok draft identity drifted")
    bound = dict(context)
    supplied = bound.pop("publish_identity_digest")
    if supplied != _digest(bound):
        raise TikTokV4ExecutionContractError("TikTok publish identity drifted")
    return {
        "detail_id": detail["detail_id"],
        "shop_id": detail["shop_id"],
        "target_identity_digest": detail["identity_digest"],
        "publish_identity_digest": str(context["publish_identity_digest"]),
        "receipt_digest": str(context["receipt_digest"]),
    }


def _verified_v4_draft_identity(
    frozen: Mapping[str, object],
    *,
    target_label: str,
    context: Mapping[str, object],
) -> dict[str, str]:
    required = {
        "schema_version",
        "snapshot_digest",
        "plan_id",
        "offer_id",
        "product_revision",
        "release_payload_digest",
        "target_detail_identity",
        "context_digest",
    }
    if set(context) != required:
        raise TikTokV4ExecutionContractError("TikTok v4 draft identity is malformed")
    if (
        context.get("snapshot_digest") != frozen["snapshot_digest"]
        or context.get("plan_id") != frozen["plan_id"]
        or context.get("offer_id") != frozen["offer_id"]
        or context.get("product_revision") != frozen["product_revision"]
        or context.get("release_payload_digest")
        != frozen["bindings"]["release_payload_digest"]
    ):
        raise TikTokV4ExecutionContractError("TikTok v4 draft identity drifted")
    raw_detail = context.get("target_detail_identity")
    if not isinstance(raw_detail, Mapping):
        raise TikTokV4ExecutionContractError("TikTok v4 draft identity is missing")
    try:
        detail = CollectBoxTargetDetailIdentity(
            target_label=raw_detail.get("target_label"),
            detail_id=raw_detail.get("detail_id"),
            shop_id=raw_detail.get("shop_id"),
        ).internal_payload()
    except (TypeError, ValueError):
        raise TikTokV4ExecutionContractError(
            "TikTok v4 draft identity is invalid"
        ) from None
    if detail != raw_detail or detail["target_label"] != target_label:
        raise TikTokV4ExecutionContractError("TikTok v4 draft identity drifted")
    body = dict(context)
    supplied = _prefixed_sha(body.pop("context_digest"))
    if supplied != "sha256:" + _digest(body):
        raise TikTokV4ExecutionContractError("TikTok v4 context digest drifted")
    control_digest = supplied.removeprefix("sha256:")
    return {
        "detail_id": detail["detail_id"],
        "shop_id": detail["shop_id"],
        "target_identity_digest": detail["identity_digest"],
        "publish_identity_digest": control_digest,
        # The credential-free v4 context is the durable provider receipt used
        # by the target publisher.  The aggregate preparation receipt is kept
        # separately in the run checkpoint.
        "receipt_digest": control_digest,
    }


def _resolved_category(
    row: Mapping[str, object],
    *,
    target: Mapping[str, str],
    product: Mapping[str, object],
    skus: Sequence[Mapping[str, object]],
    resolver: TikTokCategoryResolver | None,
) -> tuple[dict[str, object], str]:
    decision = row.get("decision") if isinstance(row, Mapping) else None
    if not isinstance(decision, Mapping):
        raise TikTokV4ExecutionContractError("TikTok category decision is missing")
    status = decision.get("status")
    if status == "APPROVED":
        category = _verified_category(row.get("category"))
        digest = _prefixed_sha(decision.get("decision_digest"))
        return category, digest
    if status != "DEFERRED_TO_SKILL" or row.get("category") is not None:
        raise TikTokV4ExecutionContractError("TikTok category decision is invalid")
    if resolver is None:
        raise TikTokV4ExecutionContractError("TikTok category resolver is unavailable")
    receipt = resolver.resolve(
        target=deepcopy(dict(target)),
        product=deepcopy(dict(product)),
        skus=deepcopy(list(skus)),
    )
    if not isinstance(receipt, Mapping):
        raise TikTokV4ExecutionContractError("official category result is malformed")
    required = {
        "schema_version",
        "target_label",
        "category",
        "enabled",
        "metadata_valid",
        "resolution",
        "evidence_digest",
    }
    if (
        set(receipt) != required
        or receipt.get("schema_version")
        != TIKTOK_OFFICIAL_CATEGORY_RESOLUTION_SCHEMA
        or receipt.get("target_label") != target["target_label"]
        or receipt.get("enabled") is not True
        or receipt.get("metadata_valid") is not True
        or receipt.get("resolution") not in {"EXACT", "USER_APPROVED_FALLBACK"}
    ):
        raise TikTokV4ExecutionContractError("official category result is invalid")
    body = dict(receipt)
    supplied_digest = _prefixed_sha(body.pop("evidence_digest"))
    if supplied_digest != "sha256:" + _digest(body):
        raise TikTokV4ExecutionContractError("official category evidence drifted")
    return _verified_category(receipt.get("category")), supplied_digest


def _verified_category(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"id", "name", "path"}:
        raise TikTokV4ExecutionContractError("TikTok category is malformed")
    category_id = value.get("id")
    name = value.get("name")
    path = value.get("path")
    if (
        type(category_id) is not str
        or not category_id.isascii()
        or not category_id.isdigit()
        or int(category_id) <= 0
        or type(name) is not str
        or not name.strip()
        or not isinstance(path, list)
        or not path
    ):
        raise TikTokV4ExecutionContractError("TikTok category is invalid")
    normalized_path: list[dict[str, str]] = []
    for item in path:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"id", "name"}
            or type(item.get("id")) is not str
            or not item["id"]
            or type(item.get("name")) is not str
            or not item["name"].strip()
        ):
            raise TikTokV4ExecutionContractError("TikTok category path is invalid")
        normalized_path.append({"id": item["id"], "name": item["name"]})
    if normalized_path[-1] != {"id": category_id, "name": name}:
        raise TikTokV4ExecutionContractError("TikTok category path drifted")
    return {"id": category_id, "name": name, "path": normalized_path}


def _derived_parent_parcel(skus: Sequence[Mapping[str, object]]) -> dict[str, object]:
    weights = [Decimal(str(row["parcel"]["weight_kg"])) for row in skus]
    dimensions = [
        [Decimal(str(value)) for value in row["parcel"]["package_cm"]]
        for row in skus
    ]
    return {
        "weight_kg": _decimal_text(max(weights)),
        "package_cm": [
            _decimal_text(max(row[index] for row in dimensions))
            for index in range(3)
        ],
        "derived_from": "maximum_approved_sku_parcels",
    }


def _verified_execution_plan(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TikTokV4ExecutionContractError("TikTok execution plan is malformed")
    plan = deepcopy(dict(value))
    supplied = plan.pop("plan_digest", None)
    if _prefixed_sha(supplied) != "sha256:" + _digest(plan):
        raise TikTokV4ExecutionContractError("TikTok execution plan drifted")
    plan["plan_digest"] = supplied
    if plan.get("schema_version") != TIKTOK_V4_EXECUTION_PLAN_SCHEMA:
        raise TikTokV4ExecutionContractError("TikTok execution plan schema is invalid")
    if set(plan) != {
        "schema_version",
        "offer_id",
        "plan_id",
        "product_revision",
        "snapshot_digest",
        "target_order",
        "targets",
        "blocked_targets",
        "plan_digest",
    }:
        raise TikTokV4ExecutionContractError("TikTok execution plan fields are invalid")
    if (
        type(plan.get("offer_id")) is not str
        or not plan["offer_id"].isdigit()
        or type(plan.get("plan_id")) is not str
        or not plan["plan_id"]
        or type(plan.get("product_revision")) is not int
        or plan["product_revision"] <= 0
    ):
        raise TikTokV4ExecutionContractError("TikTok execution plan identity is invalid")
    _prefixed_sha(plan.get("snapshot_digest"))
    targets = plan.get("targets")
    blocked = plan.get("blocked_targets")
    order = plan.get("target_order")
    if not isinstance(targets, list) or not isinstance(blocked, list) or not isinstance(order, list):
        raise TikTokV4ExecutionContractError("TikTok execution target rows are invalid")
    labels: list[str] = []
    for command in targets:
        if not isinstance(command, Mapping):
            raise TikTokV4ExecutionContractError("TikTok target command is invalid")
        body = dict(command)
        command_digest = body.pop("command_digest", None)
        if (
            body.get("schema_version") != TIKTOK_V4_TARGET_COMMAND_SCHEMA
            or _prefixed_sha(command_digest) != "sha256:" + _digest(body)
        ):
            raise TikTokV4ExecutionContractError("TikTok target command drifted")
        _validate_target_command_identity(plan, command)
        labels.append(str(command.get("target_label") or ""))
    for row in blocked:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"target_label", "reason_code"}
            or row.get("reason_code")
            not in {
                "DRAFT_IDENTITY_UNAVAILABLE",
                "DRAFT_IDENTITY_CONFLICT",
                "CATEGORY_CONFIRMATION_REQUIRED",
            }
        ):
            raise TikTokV4ExecutionContractError("TikTok blocked target is invalid")
        labels.append(str(row.get("target_label") or ""))
    if labels != order or len(labels) != len(set(labels)):
        # Ready and blocked rows are separately stored, so compare sets while
        # retaining the original approved order for the receipt.
        if set(labels) != set(order) or len(order) != len(set(order)):
            raise TikTokV4ExecutionContractError("TikTok target coverage drifted")
    return plan


def _validate_target_command_identity(
    plan: Mapping[str, object], command: Mapping[str, object]
) -> None:
    if set(command) != {
        "schema_version",
        "snapshot_digest",
        "offer_id",
        "plan_id",
        "product_revision",
        "target_label",
        "target",
        "control",
        "product",
        "skus",
        "parent_parcel",
        "publisher_snapshot",
        "command_digest",
    }:
        raise TikTokV4ExecutionContractError("TikTok target command fields are invalid")
    for field in ("snapshot_digest", "offer_id", "plan_id", "product_revision"):
        if command.get(field) != plan.get(field):
            raise TikTokV4ExecutionContractError("TikTok target command identity drifted")
    label = command.get("target_label")
    target = command.get("target")
    control = command.get("control")
    product = command.get("product")
    skus = command.get("skus")
    publisher = command.get("publisher_snapshot")
    if (
        type(label) is not str
        or not isinstance(target, Mapping)
        or target.get("target_label") != label
        or target.get("platform") != "tiktok"
        or target.get("site") != target.get("store")
        or label != f"tiktok:{target.get('site')}"
        or not isinstance(control, Mapping)
        or not isinstance(product, Mapping)
        or not isinstance(skus, list)
        or not skus
        or not isinstance(publisher, Mapping)
    ):
        raise TikTokV4ExecutionContractError("TikTok target command identity is invalid")
    if (
        publisher.get("schema_version") != _PUBLISHER_SNAPSHOT_SCHEMA
        or publisher.get("offer_id") != plan["offer_id"]
        or publisher.get("plan_id") != plan["plan_id"]
        or publisher.get("product_revision") != plan["product_revision"]
        or publisher.get("unavailable_targets") != []
    ):
        raise TikTokV4ExecutionContractError("TikTok publisher identity drifted")
    publisher_targets = publisher.get("targets")
    if (
        not isinstance(publisher_targets, list)
        or len(publisher_targets) != 1
        or not isinstance(publisher_targets[0], Mapping)
    ):
        raise TikTokV4ExecutionContractError("TikTok publisher target identity is invalid")
    provider_target = publisher_targets[0]
    for field in (
        "detail_id",
        "shop_id",
        "target_identity_digest",
        "publish_identity_digest",
        "receipt_digest",
    ):
        if provider_target.get(field) != control.get(field):
            raise TikTokV4ExecutionContractError("TikTok publisher identity drifted")
    if provider_target.get("target_label") != label:
        raise TikTokV4ExecutionContractError("TikTok publisher target identity drifted")

    target_category = product.get("target_category")
    if (
        not isinstance(target_category, Mapping)
        or provider_target.get("expected_category_id") != target_category.get("id")
    ):
        raise TikTokV4ExecutionContractError("TikTok category identity drifted")
    model_prices: dict[str, object] = {}
    variant_models: dict[str, object] = {}
    sku_parcels: dict[str, object] = {}
    currencies: set[object] = set()
    for row in skus:
        if not isinstance(row, Mapping):
            raise TikTokV4ExecutionContractError("TikTok SKU identity is invalid")
        model = row.get("model_sku")
        variant = row.get("variant_key")
        if (
            type(model) is not str
            or not model.strip()
            or type(variant) is not str
            or not variant.strip()
        ):
            raise TikTokV4ExecutionContractError("TikTok SKU identity is invalid")
        if model in model_prices or variant in variant_models:
            raise TikTokV4ExecutionContractError("TikTok SKU identity drifted")
        model_prices[model] = row.get("price")
        variant_models[variant] = model
        sku_parcels[variant] = row.get("parcel")
        currencies.add(row.get("currency"))
    if (
        len(currencies) != 1
        or provider_target.get("expected_currency") not in currencies
        or provider_target.get("expected_sku_prices") != model_prices
        or provider_target.get("expected_variant_model_skus") != variant_models
        or provider_target.get("expected_sku_parcels") != sku_parcels
    ):
        raise TikTokV4ExecutionContractError("TikTok SKU execution facts drifted")


def _preflight_status(receipt: Mapping[str, object], label: str) -> str:
    rows = receipt.get("targets") if isinstance(receipt, Mapping) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        return "READ_UNKNOWN"
    if rows[0].get("target_label") != label:
        return "READ_UNKNOWN"
    return str(rows[0].get("status") or "").upper()


def _dispatch_fact(receipt: Mapping[str, object], label: str) -> dict[str, object]:
    rows = receipt.get("targets") if isinstance(receipt, Mapping) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        return {
            "target_label": label,
            "outcome": "UNKNOWN",
            "external_write_count": None,
        }
    row = rows[0]
    outcome = str(row.get("outcome") or "").upper()
    if row.get("target_label") != label or outcome not in {
        "ACCEPTED",
        "REJECTED",
        "UNKNOWN",
    }:
        return {
            "target_label": label,
            "outcome": "UNKNOWN",
            "external_write_count": None,
        }
    count = row.get("external_write_count")
    return {
        "target_label": label,
        "outcome": outcome,
        "external_write_count": count if type(count) is int and count >= 0 else None,
    }


def _readback_fact(value: Mapping[str, object], label: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or value.get("target_label") != label:
        raise TikTokV4ExecutionContractError("TikTok readback identity drifted")
    authority = str(value.get("authority") or "").upper()
    status = str(value.get("status") or "").upper()
    if authority not in {"OFFICIAL_STOREFRONT", "MIAOSHOU_DRAFT", "UNAVAILABLE"}:
        raise TikTokV4ExecutionContractError("TikTok readback authority is invalid")
    if not status:
        raise TikTokV4ExecutionContractError("TikTok readback status is invalid")
    return {
        "target_label": label,
        "authority": authority,
        "status": status,
        "exact": value.get("exact") is True,
    }


def _classify_target(
    dispatch: Mapping[str, object], readback: Mapping[str, object]
) -> dict[str, object]:
    outcome = str(dispatch["outcome"])
    authority = str(readback["authority"])
    readback_status = str(readback["status"])
    exact = readback.get("exact") is True
    if authority == "OFFICIAL_STOREFRONT" and readback_status == "VERIFIED" and exact:
        status = "PUBLISHED"
        reason = "OFFICIAL_STOREFRONT_VERIFIED"
    elif authority == "OFFICIAL_STOREFRONT" and readback_status in {
        "MISMATCH",
        "NOT_FOUND",
        "DELETED",
        "REJECTED",
    }:
        status = "FAILED"
        reason = "OFFICIAL_STOREFRONT_NOT_VERIFIED"
    elif outcome in {"ACCEPTED", "UNKNOWN"}:
        status = "PROCESSING"
        reason = (
            "STOREFRONT_READBACK_UNAVAILABLE"
            if authority != "OFFICIAL_STOREFRONT"
            else "OFFICIAL_STOREFRONT_PROCESSING"
        )
    else:
        status = "FAILED"
        reason = "DISPATCH_REJECTED"
    return {
        "target_label": dispatch["target_label"],
        "status": status,
        "reason_code": reason,
        "dispatch_attempted": True,
        "dispatch_outcome": outcome,
        "readback_authority": authority,
        "readback_status": readback_status,
        "external_write_count": dispatch["external_write_count"],
        "retry_safe": outcome == "REJECTED" and status == "FAILED",
    }


def _aggregate_status(rows: Sequence[Mapping[str, object]]) -> str:
    statuses = [row["status"] for row in rows]
    if statuses and all(status == "PUBLISHED" for status in statuses):
        return "PUBLISHED"
    if any(status == "FAILED" for status in statuses):
        return "PARTIAL" if any(status != "FAILED" for status in statuses) else "FAILED"
    return "PROCESSING" if statuses else "FAILED"


def _plain_sha(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _prefixed_sha(value: object) -> str:
    if type(value) is not str:
        raise TikTokV4ExecutionContractError("SHA-256 evidence is invalid")
    plain = value.removeprefix("sha256:")
    if not _plain_sha(plain):
        raise TikTokV4ExecutionContractError("SHA-256 evidence is invalid")
    return "sha256:" + plain


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


__all__ = [
    "TIKTOK_OFFICIAL_CATEGORY_RESOLUTION_SCHEMA",
    "TIKTOK_V4_EXECUTION_PLAN_SCHEMA",
    "TIKTOK_V4_EXECUTION_RECEIPT_SCHEMA",
    "TIKTOK_V4_TARGET_COMMAND_SCHEMA",
    "TikTokCategoryResolver",
    "TikTokStorefrontReadback",
    "TikTokTargetPublisher",
    "TikTokV4ExecutionContractError",
    "execute_tiktok_v4_plan",
    "project_tiktok_v4_execution_plan",
]
