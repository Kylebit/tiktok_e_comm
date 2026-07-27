"""Stable shared-platform contracts for one governed release target action.

The platform owns authority, durable state and proof consumption. Channel
operations owns the official proof providers and marketplace adapters. This
module deliberately contains no marketplace imports.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


SHOPEE_SAFE_PRE_SUBMIT_RETRY = "shopee_safe_pre_submit_retry_v1"
OZON_EXISTING_PRODUCT_STOCK_RECONCILIATION = (
    "ozon_existing_product_stock_reconciliation_v1"
)
TARGET_SCOPED_OPERATION_KINDS: dict[str, str] = {
    "shopee:MY": SHOPEE_SAFE_PRE_SUBMIT_RETRY,
    "shopee:VN": SHOPEE_SAFE_PRE_SUBMIT_RETRY,
    "ozon:RU": OZON_EXISTING_PRODUCT_STOCK_RECONCILIATION,
}

_SENSITIVE_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "confirmation_token",
    "authorization",
    "cookie",
    "secret",
    "raw_response",
)


class TargetScopedContractError(ValueError):
    """A target-scoped request, proof or result violated the stable contract."""


class TargetScopedCommandUnavailable(TargetScopedContractError):
    """The immutable plan cannot authorize a complete target command."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = _required_text(code, "code")


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise TargetScopedContractError(
            "target-scoped evidence must be JSON-serializable"
        ) from error


def canonical_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def operation_kind_for_target(target_label: str) -> str:
    label = str(target_label or "").strip()
    try:
        return TARGET_SCOPED_OPERATION_KINDS[label]
    except KeyError as error:
        raise TargetScopedContractError(
            f"target-scoped action is not supported for {label or 'empty target'}"
        ) from error


def _strict_non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TargetScopedContractError(
            f"{field} must be a non-negative integer"
        )
    return value


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TargetScopedContractError(f"{field} is required")
    return text


def _strict_positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            f"immutable plan requires numeric {field}",
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            f"immutable plan requires positive {field}",
        )
    return number


def _normalised_seller_sku(value: object) -> tuple[str, str]:
    seller_sku = _required_text(value, "seller_sku")
    if not seller_sku.isdigit() or len(seller_sku) > 32:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable seller_sku must contain 1-32 digits",
        )
    return seller_sku, seller_sku[-4:].zfill(4)


def approved_shopee_channel_master_digest(
    title: object,
    description: object,
    ordered_image_urls: object,
) -> str:
    """Digest only exact channel-visible fields reproducible by official GET."""

    clean_title = unicodedata.normalize(
        "NFC",
        str(title or "").strip(),
    )
    exact_description = str(
        description if description is not None else ""
    )
    if not clean_title or not exact_description.strip():
        raise TargetScopedContractError(
            "approved Shopee title and description are required"
        )
    if (
        isinstance(ordered_image_urls, (str, bytes))
        or not isinstance(ordered_image_urls, (list, tuple))
        or not ordered_image_urls
    ):
        raise TargetScopedContractError(
            "approved Shopee ordered image URLs are required"
        )
    ordered = []
    for position, value in enumerate(ordered_image_urls, start=1):
        image_url = str(value or "").strip()
        if not image_url:
            raise TargetScopedContractError(
                "approved Shopee image URL is required"
            )
        ordered.append(
            {"position": position, "image_url": image_url}
        )
    return canonical_digest(
        {
            "schema_version": "approved-shopee-channel-master/v1",
            "title": clean_title,
            "description": exact_description,
            "ordered_images": ordered,
        }
    )


def _approved_shopee_master_digest(payload: Mapping[str, Any]) -> tuple[str, int]:
    listing = payload.get("listing_copy")
    images = payload.get("images")
    if not isinstance(listing, Mapping) or not isinstance(images, list):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable plan lacks approved Shopee copy or ordered images",
        )
    candidates = [
        row
        for row in (listing.get("candidates") or ())
        if isinstance(row, Mapping)
        and str(row.get("channel") or "").lower() == "shopee"
        and str(row.get("site") or "").upper() == "CNSC"
        and str(row.get("policy_check") or "").lower() == "passed"
        and str(row.get("title") or "").strip()
    ]
    if len(candidates) != 1:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable plan requires one approved Shopee CNSC title",
        )
    description = str(listing.get("shopee_description_en") or "")
    if not description.strip() or not images:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable plan requires approved Shopee description and images",
        )
    ordered_image_urls: list[str] = []
    for index, row in enumerate(images, start=1):
        if not isinstance(row, Mapping):
            raise TargetScopedCommandUnavailable(
                "planned_command_incomplete",
                "immutable ordered image entry is invalid",
            )
        url = str(row.get("image_url") or "").strip()
        position = row.get("position")
        if not url or isinstance(position, bool) or not isinstance(position, int):
            raise TargetScopedCommandUnavailable(
                "planned_command_incomplete",
                "immutable ordered image requires position and image_url",
            )
        if position != index:
            raise TargetScopedCommandUnavailable(
                "planned_command_incomplete",
                "immutable images must use exact consecutive order",
            )
        ordered_image_urls.append(url)
    return (
        approved_shopee_channel_master_digest(
            candidates[0]["title"],
            description,
            ordered_image_urls,
        ),
        len(ordered_image_urls),
    )


def _approved_parcel(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    facts = payload.get("product_facts")
    if not isinstance(facts, Mapping):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable plan lacks approved parcel facts",
        )
    weight = _strict_positive_number(facts.get("weight_kg"), "weight_kg")
    package = facts.get("package_cm")
    if not isinstance(package, list) or len(package) != 3:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable plan requires three package dimensions",
        )
    dimensions = [
        _strict_positive_number(value, f"package_cm[{index}]")
        for index, value in enumerate(package)
    ]
    parcel = {
        "weight_kg": weight,
        "package_cm": dimensions,
    }
    return parcel, canonical_digest(
        {"schema_version": "approved-parcel/v1", **parcel}
    )


def _planned_shopee_command(
    payload: Mapping[str, Any],
    *,
    target_label: str,
) -> dict[str, Any]:
    region = target_label.rsplit(":", 1)[1]
    seller_sku, model_sku = _normalised_seller_sku(
        payload.get("seller_sku")
    )
    pricing = payload.get("pricing")
    selected = (
        pricing.get("selected_targets")
        if isinstance(pricing, Mapping)
        else None
    )
    target_pricing = (
        selected.get(target_label)
        if isinstance(selected, Mapping)
        else None
    )
    derived = (
        target_pricing.get("derived_preview")
        if isinstance(target_pricing, Mapping)
        else None
    )
    if not isinstance(derived, Mapping):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            f"immutable plan lacks {target_label} approved pricing",
        )
    local_price = _strict_positive_number(
        derived.get("local_original_price"),
        "local_original_price",
    )
    currency = str(derived.get("source_currency") or "").strip().upper()
    expected_currency = {"MY": "MYR", "VN": "VND"}[region]
    if currency != expected_currency:
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            f"immutable {target_label} price must use {expected_currency}",
        )
    master_digest, image_count = _approved_shopee_master_digest(payload)
    parcel, parcel_digest = _approved_parcel(payload)
    excluded = [50052] if region == "VN" else []
    return {
        "schema_version": "shopee-existing-global-command/v1",
        "builder_policy_version": "target-scoped-shopee/v1",
        "target_label": target_label,
        "operation_kind": SHOPEE_SAFE_PRE_SUBMIT_RETRY,
        "region": region,
        "seller_sku": seller_sku,
        "model_sku": model_sku,
        "existing_global_only": True,
        "forbid_global_create": True,
        "forbid_global_update": True,
        "forbid_model_init": True,
        "allow_token_refresh": False,
        "item_status": "NORMAL",
        "local_original_price": local_price,
        "local_currency": currency,
        "approved_master_digest": master_digest,
        "approved_image_count": image_count,
        "parcel": parcel,
        "parcel_digest": parcel_digest,
        "logistics_policy_version": (
            "approved-parcel-enabled-channels-exclude-50052/v1"
            if region == "VN"
            else "approved-parcel-enabled-channels/v1"
        ),
        "excluded_logistics_ids": excluded,
    }


def _planned_ozon_command(payload: Mapping[str, Any]) -> dict[str, Any]:
    actions = payload.get("target_actions")
    action = (
        actions.get("ozon:RU") if isinstance(actions, Mapping) else None
    )
    if not isinstance(action, Mapping):
        raise TargetScopedCommandUnavailable(
            "successor_plan_stock_decision_required",
            "Ozon stock action requires a Kyle-approved successor plan",
        )
    seller_sku, offer_id = _normalised_seller_sku(
        payload.get("seller_sku")
    )
    required_text = {
        field: str(action.get(field) or "").strip()
        for field in (
            "expected_listing_digest",
            "inventory_snapshot_id",
            "inventory_snapshot_revision_or_digest",
        )
    }
    if any(not value for value in required_text.values()):
        raise TargetScopedCommandUnavailable(
            "successor_plan_stock_decision_required",
            "Ozon successor plan lacks governed listing or inventory identity",
        )
    stock = action.get("desired_stock_quantity")
    if isinstance(stock, bool) or not isinstance(stock, int) or stock <= 0:
        raise TargetScopedCommandUnavailable(
            "successor_plan_stock_decision_required",
            "Ozon successor plan requires a positive desired stock quantity",
        )
    if (
        str(action.get("schema_version") or "")
        != "ozon-existing-product-stock-command/v1"
        or str(action.get("warehouse_policy") or "")
        != "single_active_non_kgt"
    ):
        raise TargetScopedCommandUnavailable(
            "successor_plan_stock_decision_required",
            "Ozon successor plan stock schema or warehouse policy is invalid",
        )
    return {
        "schema_version": "ozon-existing-product-stock-command/v1",
        "builder_policy_version": "target-scoped-ozon-stock/v1",
        "target_label": "ozon:RU",
        "operation_kind": OZON_EXISTING_PRODUCT_STOCK_RECONCILIATION,
        "seller_sku": seller_sku,
        "offer_id": offer_id,
        "existing_product_only": True,
        "forbid_import": True,
        "forbid_create": True,
        "expected_listing_digest": required_text[
            "expected_listing_digest"
        ],
        "desired_stock_quantity": stock,
        "inventory_snapshot_id": required_text["inventory_snapshot_id"],
        "inventory_snapshot_revision_or_digest": required_text[
            "inventory_snapshot_revision_or_digest"
        ],
        "warehouse_policy": "single_active_non_kgt",
    }


def planned_target_command(
    plan_payload: Mapping[str, Any],
    *,
    target_label: str,
) -> tuple[dict[str, Any], str]:
    """Purely derive one write command from an immutable ReleasePlan payload."""

    if not isinstance(plan_payload, Mapping):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "immutable release payload is required",
        )
    label = str(target_label or "").strip()
    operation_kind_for_target(label)
    if label not in list(plan_payload.get("targets") or ()):
        raise TargetScopedCommandUnavailable(
            "planned_command_incomplete",
            "target is absent from the immutable release plan",
        )
    if label in {"shopee:MY", "shopee:VN"}:
        command = _planned_shopee_command(
            plan_payload,
            target_label=label,
        )
    else:
        command = _planned_ozon_command(plan_payload)
    return command, canonical_digest(command)


def _assert_redacted(value: object, *, path: str = "evidence") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                raise TargetScopedContractError(
                    f"{path}.{key} contains a forbidden sensitive field"
                )
            _assert_redacted(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_redacted(item, path=f"{path}[{index}]")


def _parse_utc(value: object, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise TargetScopedContractError(
            f"{field} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise TargetScopedContractError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def target_failure_digest(
    *,
    target_label: str,
    attempts: int,
    error: object,
    failure_event_digests: list[str] | tuple[str, ...],
) -> str:
    return canonical_digest(
        {
            "target_label": _required_text(target_label, "target_label"),
            "attempts": _strict_non_negative_int(attempts, "attempts"),
            "error": str(error or ""),
            "failure_event_digests": [
                str(value or "").strip() for value in failure_event_digests
            ],
        }
    )


def target_preflight_digest(
    *,
    plan_id: str,
    run_id: str,
    target_label: str,
    operation_kind: str,
    product_revision: int,
    payload_digest: str,
    planned_command_digest: str,
    failure_attempt: int,
    failure_digest: str,
    target_idempotency_key: str,
) -> str:
    expected_kind = operation_kind_for_target(target_label)
    if operation_kind != expected_kind:
        raise TargetScopedContractError(
            "operation_kind does not match the server target allowlist"
        )
    return canonical_digest(
        {
            "schema_version": "target-scoped-preflight/v1",
            "plan_id": _required_text(plan_id, "plan_id"),
            "run_id": _required_text(run_id, "run_id"),
            "target_label": _required_text(target_label, "target_label"),
            "operation_kind": operation_kind,
            "product_revision": _strict_non_negative_int(
                product_revision, "product_revision"
            ),
            "payload_digest": _required_text(
                payload_digest, "payload_digest"
            ),
            "planned_command_digest": _required_text(
                planned_command_digest, "planned_command_digest"
            ),
            "failure_attempt": _strict_non_negative_int(
                failure_attempt, "failure_attempt"
            ),
            "failure_digest": _required_text(
                failure_digest, "failure_digest"
            ),
            "target_idempotency_key": _required_text(
                target_idempotency_key, "target_idempotency_key"
            ),
        }
    )


@dataclass(frozen=True)
class TargetScopedOperationRequest:
    """Exact server-authorized request passed to a channel proof/adapter seam."""

    plan_id: str
    confirmation_token: str
    approval_scope_digest: str
    product_id: str
    seller_sku: str
    product_package_id: str
    content_package_id: str
    run_id: str
    target_label: str
    operation_kind: str
    product_revision: int
    payload_digest: str
    planned_command: Mapping[str, Any]
    planned_command_digest: str
    preflight_digest: str
    failure_attempt: int
    failure_digest: str
    target_idempotency_key: str
    approved_by: str = "Kyle"

    def __post_init__(self) -> None:
        for field in (
            "plan_id",
            "confirmation_token",
            "approval_scope_digest",
            "product_id",
            "seller_sku",
            "product_package_id",
            "content_package_id",
            "run_id",
            "target_label",
            "operation_kind",
            "payload_digest",
            "planned_command_digest",
            "preflight_digest",
            "failure_digest",
            "target_idempotency_key",
        ):
            _required_text(getattr(self, field), field)
        _strict_non_negative_int(self.product_revision, "product_revision")
        _strict_non_negative_int(self.failure_attempt, "failure_attempt")
        if self.approved_by != "Kyle":
            raise TargetScopedContractError(
                "target-scoped action requires approved_by=Kyle"
            )
        expected_kind = operation_kind_for_target(self.target_label)
        if self.operation_kind != expected_kind:
            raise TargetScopedContractError(
                "operation_kind does not match target_label"
            )
        if not isinstance(self.planned_command, Mapping):
            raise TargetScopedContractError(
                "planned_command must be a mapping"
            )
        _assert_redacted(self.planned_command, path="planned_command")
        if canonical_digest(dict(self.planned_command)) != (
            self.planned_command_digest
        ):
            raise TargetScopedContractError(
                "planned_command_digest does not match planned_command"
            )
        if (
            self.planned_command.get("target_label") != self.target_label
            or self.planned_command.get("operation_kind")
            != self.operation_kind
        ):
            raise TargetScopedContractError(
                "planned_command identity does not match request"
            )
        expected_preflight = target_preflight_digest(
            plan_id=self.plan_id,
            run_id=self.run_id,
            target_label=self.target_label,
            operation_kind=self.operation_kind,
            product_revision=self.product_revision,
            payload_digest=self.payload_digest,
            planned_command_digest=self.planned_command_digest,
            failure_attempt=self.failure_attempt,
            failure_digest=self.failure_digest,
            target_idempotency_key=self.target_idempotency_key,
        )
        if self.preflight_digest != expected_preflight:
            raise TargetScopedContractError(
                "preflight_digest does not match target failure identity"
            )

    @property
    def confirmation_token_digest(self) -> str:
        return hashlib.sha256(
            self.confirmation_token.encode("utf-8")
        ).hexdigest()

    def durable_identity(self) -> dict[str, Any]:
        """Return the immutable operation identity without persisting secrets."""

        return {
            "schema_version": "target-scoped-operation-request/v1",
            "plan_id": self.plan_id,
            "confirmation_token_digest": self.confirmation_token_digest,
            "approval_scope_digest": self.approval_scope_digest,
            "product_id": self.product_id,
            "seller_sku": self.seller_sku,
            "product_package_id": self.product_package_id,
            "content_package_id": self.content_package_id,
            "run_id": self.run_id,
            "target_label": self.target_label,
            "operation_kind": self.operation_kind,
            "product_revision": self.product_revision,
            "payload_digest": self.payload_digest,
            "planned_command": dict(self.planned_command),
            "planned_command_digest": self.planned_command_digest,
            "preflight_digest": self.preflight_digest,
            "failure_attempt": self.failure_attempt,
            "failure_digest": self.failure_digest,
            "target_idempotency_key": self.target_idempotency_key,
            "approved_by": self.approved_by,
        }

    def operation_digest(self, proof_digest: str) -> str:
        return canonical_digest(
            {
                **self.durable_identity(),
                "proof_digest": _required_text(
                    proof_digest, "proof_digest"
                ),
            }
        )


@dataclass(frozen=True)
class OfficialTargetProof:
    """Redacted official proof bound to one exact target failure attempt."""

    schema_version: str
    operation_kind: str
    plan_id: str
    run_id: str
    target_label: str
    product_revision: int
    payload_digest: str
    planned_command_digest: str
    preflight_digest: str
    failure_attempt: int
    failure_digest: str
    provided_by: str
    allow_refresh: bool
    observed_at: str
    expires_at: str
    checks: Mapping[str, bool]
    semantic_evidence: Mapping[str, Any]
    redacted_summary: Mapping[str, Any]
    external_writes_performed: tuple[str, ...]
    proof_digest: str

    @classmethod
    def from_value(
        cls,
        value: object,
        *,
        request: TargetScopedOperationRequest,
        now: datetime | None = None,
    ) -> "OfficialTargetProof":
        if isinstance(value, cls):
            value = value.durable_payload()
        if not isinstance(value, Mapping):
            raise TargetScopedContractError(
                "official target proof must be a mapping"
            )
        else:
            raw = dict(value)
            checks = raw.get("checks")
            semantic = raw.get("semantic_evidence")
            summary = raw.get("redacted_summary") or {}
            writes = raw.get("external_writes_performed")
            if not isinstance(checks, Mapping) or not checks:
                raise TargetScopedContractError(
                    "official target proof requires named checks"
                )
            if not isinstance(semantic, Mapping) or not semantic:
                raise TargetScopedContractError(
                    "official target proof requires semantic_evidence"
                )
            if not isinstance(summary, Mapping):
                raise TargetScopedContractError(
                    "redacted_summary must be a mapping"
                )
            if not isinstance(writes, (list, tuple)):
                raise TargetScopedContractError(
                    "external_writes_performed must be a list"
                )
            semantic_payload = {
                "schema_version": str(
                    raw.get("schema_version")
                    or "official-target-proof/v1"
                ),
                "operation_kind": str(raw.get("operation_kind") or ""),
                "plan_id": str(raw.get("plan_id") or ""),
                "run_id": str(raw.get("run_id") or ""),
                "target_label": str(raw.get("target_label") or ""),
                "product_revision": raw.get("product_revision"),
                "payload_digest": str(raw.get("payload_digest") or ""),
                "planned_command_digest": str(
                    raw.get("planned_command_digest") or ""
                ),
                "preflight_digest": str(raw.get("preflight_digest") or ""),
                "failure_attempt": raw.get("failure_attempt"),
                "failure_digest": str(raw.get("failure_digest") or ""),
                "provided_by": str(raw.get("provided_by") or ""),
                "allow_refresh": raw.get("allow_refresh"),
                "checks": dict(checks),
                "semantic_evidence": dict(semantic),
                "external_writes_performed": list(writes),
            }
            computed_digest = canonical_digest(semantic_payload)
            supplied_digest = str(raw.get("proof_digest") or "").strip()
            if supplied_digest and supplied_digest != computed_digest:
                raise TargetScopedContractError(
                    "official proof_digest does not match semantic evidence"
                )
            proof = cls(
                schema_version=semantic_payload["schema_version"],
                operation_kind=semantic_payload["operation_kind"],
                plan_id=semantic_payload["plan_id"],
                run_id=semantic_payload["run_id"],
                target_label=semantic_payload["target_label"],
                product_revision=_strict_non_negative_int(
                    semantic_payload["product_revision"],
                    "product_revision",
                ),
                payload_digest=semantic_payload["payload_digest"],
                planned_command_digest=semantic_payload[
                    "planned_command_digest"
                ],
                preflight_digest=semantic_payload["preflight_digest"],
                failure_attempt=_strict_non_negative_int(
                    semantic_payload["failure_attempt"],
                    "failure_attempt",
                ),
                failure_digest=semantic_payload["failure_digest"],
                provided_by=semantic_payload["provided_by"],
                allow_refresh=semantic_payload["allow_refresh"] is True,
                observed_at=str(raw.get("observed_at") or ""),
                expires_at=str(raw.get("expires_at") or ""),
                checks=dict(checks),
                semantic_evidence=dict(semantic),
                redacted_summary=dict(summary),
                external_writes_performed=tuple(str(item) for item in writes),
                proof_digest=computed_digest,
            )

        expected = {
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
        }
        actual = {field: getattr(proof, field) for field in expected}
        if actual != expected:
            raise TargetScopedContractError(
                "official target proof identity does not match the request"
            )
        if proof.provided_by != "03":
            raise TargetScopedContractError(
                "official target proof must be provided by channel operations"
            )
        if proof.allow_refresh:
            raise TargetScopedContractError(
                "official target proof must use allow_refresh=false"
            )
        if proof.external_writes_performed:
            raise TargetScopedContractError(
                "official target proof must perform zero external writes"
            )
        if any(value is not True for value in proof.checks.values()):
            raise TargetScopedContractError(
                "official target proof did not pass every required check"
            )
        _assert_redacted(proof.semantic_evidence)
        _assert_redacted(proof.redacted_summary, path="redacted_summary")
        observed = _parse_utc(proof.observed_at, "observed_at")
        expires = _parse_utc(proof.expires_at, "expires_at")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if observed > current:
            raise TargetScopedContractError(
                "official target proof observed_at is in the future"
            )
        if expires <= current or expires <= observed:
            raise TargetScopedContractError(
                "official target proof is expired"
            )
        return proof

    def durable_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_kind": self.operation_kind,
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "target_label": self.target_label,
            "product_revision": self.product_revision,
            "payload_digest": self.payload_digest,
            "planned_command_digest": self.planned_command_digest,
            "preflight_digest": self.preflight_digest,
            "failure_attempt": self.failure_attempt,
            "failure_digest": self.failure_digest,
            "provided_by": self.provided_by,
            "allow_refresh": self.allow_refresh,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "checks": dict(self.checks),
            "semantic_evidence": dict(self.semantic_evidence),
            "redacted_summary": dict(self.redacted_summary),
            "external_writes_performed": list(
                self.external_writes_performed
            ),
            "proof_digest": self.proof_digest,
        }


@dataclass(frozen=True)
class TargetScopedOperationResult:
    """Normalized, redacted outcome from one channel operation call."""

    succeeded: bool
    readback_verified: bool
    detail: str
    external_reference: str | None
    submission_accepted: bool
    evidence: Mapping[str, Any]

    @classmethod
    def from_value(cls, value: object) -> "TargetScopedOperationResult":
        if isinstance(value, cls):
            result = value
        elif isinstance(value, Mapping):
            raw = dict(value)
            evidence = raw.get("readback_evidence")
            if evidence is None:
                evidence = raw.get("evidence")
            result = cls(
                succeeded=raw.get("succeeded") is True,
                readback_verified=raw.get("readback_verified") is True,
                detail=str(raw.get("detail") or "").strip(),
                external_reference=(
                    str(raw.get("external_reference") or "").strip() or None
                ),
                submission_accepted=raw.get("submission_accepted") is True,
                evidence=dict(evidence or {}),
            )
        else:
            evidence = getattr(value, "readback_evidence", None)
            result = cls(
                succeeded=getattr(value, "succeeded", None) is True,
                readback_verified=(
                    getattr(value, "readback_verified", None) is True
                ),
                detail=str(getattr(value, "detail", "") or "").strip(),
                external_reference=(
                    str(
                        getattr(value, "external_reference", "") or ""
                    ).strip()
                    or None
                ),
                submission_accepted=(
                    getattr(value, "submission_accepted", None) is True
                ),
                evidence=dict(evidence or {}),
            )
        if not result.detail:
            raise TargetScopedContractError(
                "target-scoped adapter result requires detail"
            )
        writes = result.evidence.get("external_writes_performed")
        if not isinstance(writes, (list, tuple)):
            raise TargetScopedContractError(
                "adapter result must explicitly report external_writes_performed"
            )
        _assert_redacted(result.evidence, path="result_evidence")
        return result

    @property
    def external_writes_performed(self) -> list[str]:
        return [
            str(value)
            for value in (
                self.evidence.get("external_writes_performed") or ()
            )
            if str(value)
        ]

    @property
    def outcome(self) -> str:
        if (
            self.succeeded
            and self.readback_verified
            and self.evidence.get("verified") is True
        ):
            return "SUCCEEDED"
        if (
            not self.succeeded
            and not self.readback_verified
            and not self.external_reference
            and not self.submission_accepted
            and self.evidence.get("pre_submit_failure") is True
            and not self.external_writes_performed
        ):
            return "FAILED_PRE_SUBMIT"
        return "RECONCILIATION_REQUIRED"

    def durable_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "target-scoped-operation-result/v1",
            "succeeded": self.succeeded,
            "readback_verified": self.readback_verified,
            "detail": self.detail,
            "external_reference": self.external_reference,
            "submission_accepted": self.submission_accepted,
            "evidence": dict(self.evidence),
            "external_writes_performed": self.external_writes_performed,
            "outcome": self.outcome,
        }
