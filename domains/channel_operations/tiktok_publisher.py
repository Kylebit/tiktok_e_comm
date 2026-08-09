"""Independent TikTok publication through Miaoshou.

This module deliberately knows nothing about the release control plane, Shopee,
or Ozon.  It consumes one approved, read-only product snapshot and returns one
redacted receipt for whichever TikTok stores the approved plan selected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Protocol

from modules.miaoshou.client import MiaoshouBusinessRejectedError


APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA = "approved-tiktok-publish-snapshot/v2"
TIKTOK_PREFLIGHT_RECEIPT_SCHEMA = "tiktok-publish-preflight/v1"
TIKTOK_PUBLISH_RECEIPT_SCHEMA = "tiktok-publish-receipt/v1"

TIKTOK_TARGETS = (
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:MX",
    "tiktok:GB",
    "tiktok:HB_PH",
    "tiktok:HB_MY",
    "tiktok:HB_TH",
    "tiktok:HB_VN",
)

_CURRENCY_BY_TARGET = {
    "tiktok:LH_PH": "PHP",
    "tiktok:LH_MY": "MYR",
    "tiktok:LH_TH": "THB",
    "tiktok:LH_VN": "VND",
    "tiktok:MX": "MXN",
    "tiktok:GB": "GBP",
    "tiktok:HB_PH": "PHP",
    "tiktok:HB_MY": "MYR",
    "tiktok:HB_TH": "THB",
    "tiktok:HB_VN": "VND",
}
_LOGGER = logging.getLogger(__name__)


class TikTokPublishContractError(ValueError):
    """The server-owned approved snapshot is malformed or has drifted."""


class TikTokPreWritePreparationError(ValueError):
    """A deterministic target error proven to occur before a write request."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class TikTokPublishTransport(Protocol):
    def read_draft(self, target: Mapping[str, object]) -> Mapping[str, object]: ...

    def draft_matches(
        self, target: Mapping[str, object], draft: Mapping[str, object]
    ) -> bool: ...

    def save_approved_draft(
        self, target: Mapping[str, object], draft: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def submit(self, target: Mapping[str, object]) -> Mapping[str, object]: ...


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _positive_digits(value: object, name: str) -> str:
    if isinstance(value, bool) or type(value) not in {str, int}:
        raise TikTokPublishContractError(f"{name} must be positive digits")
    result = str(value).strip()
    if not result.isascii() or not result.isdigit() or int(result) <= 0:
        raise TikTokPublishContractError(f"{name} must be positive digits")
    return result


def _positive_price(value: object) -> str:
    if isinstance(value, bool) or type(value) not in {str, int, float}:
        raise TikTokPublishContractError("expected_price is invalid")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise TikTokPublishContractError("expected_price is invalid") from error
    if not result.is_finite() or result <= 0:
        raise TikTokPublishContractError("expected_price is invalid")
    return format(result, "f")


def _positive_decimal(value: object, name: str) -> str:
    if isinstance(value, bool) or type(value) not in {str, int, float}:
        raise TikTokPublishContractError(f"{name} is invalid")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise TikTokPublishContractError(f"{name} is invalid") from error
    if not result.is_finite() or result <= 0:
        raise TikTokPublishContractError(f"{name} is invalid")
    return format(result, "f")


def _package_cm(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) != 3:
        raise TikTokPublishContractError(f"{name} is invalid")
    return [_positive_decimal(dimension, name) for dimension in value]


def _expected_sku_parcels(
    value: object,
    *,
    variant_model_skus: Mapping[str, str],
) -> dict[str, dict[str, object]]:
    if not isinstance(value, Mapping):
        raise TikTokPublishContractError("expected_sku_parcels is invalid")
    result: dict[str, dict[str, object]] = {}
    for raw_variant, raw_row in value.items():
        if (
            type(raw_variant) is not str
            or not raw_variant.strip().strip(";")
            or not isinstance(raw_row, Mapping)
        ):
            raise TikTokPublishContractError("expected_sku_parcels is invalid")
        variant = raw_variant.strip().strip(";")
        if variant in result:
            raise TikTokPublishContractError("expected_sku_parcels is invalid")
        result[variant] = {
            "weight_kg": _positive_decimal(
                raw_row.get("weight_kg"), "expected SKU weight"
            ),
            "package_cm": _package_cm(
                raw_row.get("package_cm"), "expected SKU package"
            ),
        }
    if set(result) != set(variant_model_skus):
        raise TikTokPublishContractError("expected SKU parcel coverage drifted")
    return result


def _expected_sku_prices(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TikTokPublishContractError("expected_sku_prices is invalid")
    result: dict[str, str] = {}
    for raw_model_sku, raw_price in value.items():
        if type(raw_model_sku) is not str or not raw_model_sku.strip():
            raise TikTokPublishContractError("expected_sku_prices is invalid")
        model_sku = raw_model_sku.strip()
        if model_sku in result:
            raise TikTokPublishContractError("expected_sku_prices is invalid")
        result[model_sku] = _positive_price(raw_price)
    return result


def _expected_variant_model_skus(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TikTokPublishContractError(
            "expected_variant_model_skus is invalid"
        )
    result: dict[str, str] = {}
    seen_models: set[str] = set()
    for raw_variant, raw_model_sku in value.items():
        if (
            type(raw_variant) is not str
            or not raw_variant.strip().strip(";")
            or type(raw_model_sku) is not str
            or not raw_model_sku.strip()
        ):
            raise TikTokPublishContractError(
                "expected_variant_model_skus is invalid"
            )
        variant = raw_variant.strip().strip(";")
        model_sku = raw_model_sku.strip()
        if variant in result or model_sku in seen_models:
            raise TikTokPublishContractError(
                "expected_variant_model_skus is invalid"
            )
        result[variant] = model_sku
        seen_models.add(model_sku)
    return result


def _validate_snapshot(snapshot: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(snapshot, Mapping):
        raise TikTokPublishContractError("snapshot must be a mapping")
    if snapshot.get("schema_version") != APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA:
        raise TikTokPublishContractError("snapshot schema is invalid")
    offer_id = _positive_digits(snapshot.get("offer_id"), "offer_id")
    plan_id = snapshot.get("plan_id")
    if type(plan_id) is not str or not plan_id.strip():
        raise TikTokPublishContractError("plan_id is invalid")
    revision = snapshot.get("product_revision")
    if type(revision) is not int or revision <= 0:
        raise TikTokPublishContractError("product_revision is invalid")
    payload_digest = snapshot.get("payload_digest")
    if (
        type(payload_digest) is not str
        or len(payload_digest) != 64
        or any(char not in "0123456789abcdef" for char in payload_digest)
    ):
        raise TikTokPublishContractError("payload_digest is invalid")
    raw_targets = snapshot.get("targets")
    if not isinstance(raw_targets, list):
        raise TikTokPublishContractError("targets are invalid")
    targets: list[dict[str, object]] = []
    labels: set[str] = set()
    for raw in raw_targets:
        if not isinstance(raw, Mapping):
            raise TikTokPublishContractError("target is invalid")
        label = raw.get("target_label")
        if type(label) is not str or label not in TIKTOK_TARGETS or label in labels:
            raise TikTokPublishContractError("target_label is invalid")
        labels.add(label)
        currency = raw.get("expected_currency")
        if currency != _CURRENCY_BY_TARGET[label]:
            raise TikTokPublishContractError("expected_currency is invalid")
        sku_prices = _expected_sku_prices(raw.get("expected_sku_prices"))
        variant_model_skus = _expected_variant_model_skus(
            raw.get("expected_variant_model_skus")
        )
        expected_weight = _positive_decimal(
            raw.get("expected_weight_kg"), "expected_weight_kg"
        )
        expected_package = _package_cm(
            raw.get("expected_package_cm"), "expected_package_cm"
        )
        sku_parcels = _expected_sku_parcels(
            raw.get("expected_sku_parcels"),
            variant_model_skus=variant_model_skus,
        )
        if len(sku_prices) > 1 and not variant_model_skus:
            raise TikTokPublishContractError(
                "multi-SKU target lacks approved variant lineage"
            )
        if (
            variant_model_skus
            and sku_prices
            and set(variant_model_skus.values()) != set(sku_prices)
        ):
            raise TikTokPublishContractError(
                "approved variant, model SKU and price coverage drifted"
            )
        targets.append(
            {
                "target_label": label,
                "detail_id": _positive_digits(raw.get("detail_id"), "detail_id"),
                "shop_id": _positive_digits(raw.get("shop_id"), "shop_id"),
                "expected_price": _positive_price(raw.get("expected_price")),
                **(
                    {"expected_sku_prices": sku_prices}
                    if raw.get("expected_sku_prices") is not None
                    else {}
                ),
                **(
                    {"expected_variant_model_skus": variant_model_skus}
                    if raw.get("expected_variant_model_skus") is not None
                    else {}
                ),
                "expected_weight_kg": expected_weight,
                "expected_package_cm": expected_package,
                "expected_sku_parcels": sku_parcels,
                "expected_currency": currency,
                # This is approved product evidence.  It is intentionally not
                # derived from the site or a platform-wide default.
                "expected_category_id": (
                    None
                    if raw.get("expected_category_id") is None
                    else _positive_digits(
                        raw.get("expected_category_id"),
                        "expected_category_id",
                    )
                ),
                "category_evidence_digest": _sha256(
                    raw.get("category_evidence_digest"),
                    "category_evidence_digest",
                ),
                "target_identity_digest": _sha256(
                    raw.get("target_identity_digest"),
                    "target_identity_digest",
                ),
                "publish_identity_digest": _sha256(
                    raw.get("publish_identity_digest"),
                    "publish_identity_digest",
                ),
                "receipt_digest": _sha256(
                    raw.get("receipt_digest"), "receipt_digest"
                ),
            }
        )
    raw_unavailable = snapshot.get("unavailable_targets", [])
    if not isinstance(raw_unavailable, list):
        raise TikTokPublishContractError("unavailable_targets are invalid")
    unavailable_targets: list[dict[str, str]] = []
    for raw in raw_unavailable:
        if not isinstance(raw, Mapping):
            raise TikTokPublishContractError("unavailable target is invalid")
        label = raw.get("target_label")
        if (
            type(label) is not str
            or label not in TIKTOK_TARGETS
            or label in labels
            or raw.get("reason_code") != "draft_identity_unavailable"
        ):
            raise TikTokPublishContractError("unavailable target is invalid")
        labels.add(label)
        unavailable_targets.append(
            {
                "target_label": label,
                "reason_code": "draft_identity_unavailable",
            }
        )
    if not labels:
        raise TikTokPublishContractError("targets are invalid")
    normalized = {
        "schema_version": APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA,
        "offer_id": offer_id,
        "plan_id": plan_id.strip(),
        "product_revision": revision,
        "payload_digest": payload_digest,
        "targets": targets,
        "unavailable_targets": unavailable_targets,
    }
    return normalized


def _sha256(value: object, name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise TikTokPublishContractError(f"{name} is invalid")
    return value


def _safe_code(value: object) -> str:
    code = str(value or "business_rejected").strip()
    if not code or len(code) > 80 or any(
        not (char.isalnum() or char in {"_", "-"}) for char in code
    ):
        return "business_rejected"
    return code


def _safe_reason(value: object) -> str:
    reason = " ".join(str(value or "Miaoshou request rejected").split())
    if not reason:
        return "Miaoshou request rejected"
    reason = re.sub(r"https?://\S+", "[redacted-url]", reason, flags=re.IGNORECASE)
    reason = re.sub(
        r"(?i)\bauthorization\b\s*[:=]?\s*(?:bearer\s+)?\S+",
        "authorization=[redacted]",
        reason,
    )
    reason = re.sub(
        r"(?i)\b(authorization|bearer|token|secret|app[_-]?key)\b\s*[:=]?\s*\S+",
        r"\1=[redacted]",
        reason,
    )
    reason = re.sub(r"\b\d{9,}\b", "[redacted-id]", reason)
    reason = re.sub(r"\b[0-9a-fA-F]{32,}\b", "[redacted-digest]", reason)
    return reason[:240]


def _provider_acceptance(response: Mapping[str, object]) -> tuple[str, str]:
    if not isinstance(response, Mapping):
        raise RuntimeError("provider response is malformed")
    if str(response.get("result") or "").casefold() != "success":
        raise MiaoshouBusinessRejectedError(
            _safe_reason(response.get("message") or "Miaoshou request rejected"),
            code=response.get("code"),
        )
    return _safe_code(response.get("code") or "200"), _safe_reason(
        response.get("message") or "Success"
    )


@dataclass(frozen=True)
class TikTokPublisher:
    transport: TikTokPublishTransport

    def preflight(self, snapshot: Mapping[str, object]) -> dict[str, object]:
        approved = _validate_snapshot(snapshot)
        snapshot_digest = _canonical_digest(approved)
        results: list[dict[str, object]] = []
        for target in approved["targets"]:
            label = str(target["target_label"])
            try:
                draft = self.transport.read_draft(target)
                status = (
                    "READY"
                    if self.transport.draft_matches(target, draft)
                    else "REPAIR_REQUIRED"
                )
                results.append({"target_label": label, "status": status})
            except MiaoshouBusinessRejectedError as error:
                results.append(
                    {
                        "target_label": label,
                        "status": "READ_REJECTED",
                        "provider_code": _safe_code(error.code),
                        "provider_reason": _safe_reason(error),
                    }
                )
            except TikTokPreWritePreparationError as error:
                results.append(
                    {
                        "target_label": label,
                        "status": "PREPARATION_REJECTED",
                        "provider_code": _safe_code(error.code),
                        "provider_reason": _safe_reason(error),
                    }
                )
            except Exception:
                results.append(
                    {
                        "target_label": label,
                        "status": "READ_UNKNOWN",
                        "provider_code": "transport_unknown",
                        "provider_reason": "Miaoshou draft read outcome is unknown",
                    }
                )
        results.extend(
            {
                "target_label": row["target_label"],
                "status": "IDENTITY_UNAVAILABLE",
                "provider_code": row["reason_code"],
                "provider_reason": "Miaoshou draft identity is unavailable",
            }
            for row in approved["unavailable_targets"]
        )
        return {
            "schema_version": TIKTOK_PREFLIGHT_RECEIPT_SCHEMA,
            "offer_id": approved["offer_id"],
            "plan_id": approved["plan_id"],
            "snapshot_digest": snapshot_digest,
            "targets": results,
        }

    def publish(
        self,
        snapshot: Mapping[str, object],
        preflight: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        approved = _validate_snapshot(snapshot)
        snapshot_digest = _canonical_digest(approved)
        if preflight is not None:
            if (
                not isinstance(preflight, Mapping)
                or preflight.get("schema_version") != TIKTOK_PREFLIGHT_RECEIPT_SCHEMA
                or preflight.get("snapshot_digest") != snapshot_digest
                or preflight.get("offer_id") != approved["offer_id"]
                or preflight.get("plan_id") != approved["plan_id"]
            ):
                raise TikTokPublishContractError(
                    "preflight does not bind this snapshot"
                )
            preflight_targets = preflight.get("targets")
            if not isinstance(preflight_targets, list) or [
                row.get("target_label") if isinstance(row, Mapping) else None
                for row in preflight_targets
            ] != [
                *[row["target_label"] for row in approved["targets"]],
                *[
                    row["target_label"]
                    for row in approved["unavailable_targets"]
                ],
            ]:
                raise TikTokPublishContractError(
                    "preflight target coverage is invalid"
                )

        results: list[dict[str, object]] = []
        for target in approved["targets"]:
            results.append(self._publish_target(target))
        results.extend(
            {
                "target_label": row["target_label"],
                "outcome": "NOT_ATTEMPTED",
                "provider_code": row["reason_code"],
                "provider_reason": "Miaoshou draft identity is unavailable",
                "external_write_count": 0,
                "write_request_count": 0,
            }
            for row in approved["unavailable_targets"]
        )
        counts = {
            outcome: sum(row["outcome"] == outcome for row in results)
            for outcome in ("ACCEPTED", "REJECTED", "UNKNOWN", "NOT_ATTEMPTED")
        }
        return {
            "schema_version": TIKTOK_PUBLISH_RECEIPT_SCHEMA,
            "offer_id": approved["offer_id"],
            "plan_id": approved["plan_id"],
            "snapshot_digest": snapshot_digest,
            "accepted_target_count": counts["ACCEPTED"],
            "rejected_target_count": counts["REJECTED"],
            "unknown_target_count": counts["UNKNOWN"],
            "not_attempted_target_count": counts["NOT_ATTEMPTED"],
            "targets": results,
        }

    def _publish_target(self, target: Mapping[str, object]) -> dict[str, object]:
        label = str(target["target_label"])
        write_request_count = 0
        confirmed_write_count = 0
        try:
            draft = self.transport.read_draft(target)
            # GB must always materialize its deterministic category metadata,
            # COD, delivery and size-chart fields before submission.  Other
            # targets avoid a no-op save when the exact draft already matches.
            repair_required = (
                True
                if label == "tiktok:GB"
                else not self.transport.draft_matches(target, draft)
            )
            if repair_required:
                try:
                    save_response = self.transport.save_approved_draft(
                        target, draft
                    )
                except TikTokPreWritePreparationError:
                    raise
                except Exception:
                    # The transport crossed (or may have crossed) the write
                    # boundary; preserve the request as an unknown outcome.
                    write_request_count += 1
                    raise
                write_request_count += 1
                _provider_acceptance(save_response)
                confirmed_write_count += 1
            write_request_count += 1
            provider_code, provider_reason = _provider_acceptance(
                self.transport.submit(target)
            )
            confirmed_write_count += 1
            _LOGGER.info(
                "tiktok_publish_accepted target=%s writes=%s requests=%s",
                label,
                confirmed_write_count,
                write_request_count,
            )
            return {
                "target_label": label,
                "outcome": "ACCEPTED",
                "provider_code": provider_code,
                "provider_reason": provider_reason,
                "external_write_count": confirmed_write_count,
                "write_request_count": write_request_count,
            }
        except TikTokPreWritePreparationError as error:
            provider_code = _safe_code(error.code)
            provider_reason = _safe_reason(error)
            _LOGGER.warning(
                "tiktok_publish_prewrite_rejected target=%s code=%s writes=%s requests=%s",
                label,
                provider_code,
                confirmed_write_count,
                write_request_count,
            )
            return {
                "target_label": label,
                "outcome": "REJECTED",
                "provider_code": provider_code,
                "provider_reason": provider_reason,
                "external_write_count": confirmed_write_count,
                "write_request_count": write_request_count,
            }
        except MiaoshouBusinessRejectedError as error:
            provider_code = _safe_code(error.code)
            provider_reason = _safe_reason(error)
            _LOGGER.warning(
                "tiktok_publish_rejected target=%s code=%s writes=%s requests=%s",
                label,
                provider_code,
                confirmed_write_count,
                write_request_count,
            )
            return {
                "target_label": label,
                "outcome": "REJECTED",
                "provider_code": provider_code,
                "provider_reason": provider_reason,
                "external_write_count": confirmed_write_count,
                "write_request_count": write_request_count,
            }
        except Exception:
            _LOGGER.error(
                "tiktok_publish_unknown target=%s requests=%s",
                label,
                write_request_count,
            )
            return {
                "target_label": label,
                "outcome": "UNKNOWN",
                "provider_code": "transport_unknown",
                "provider_reason": "Miaoshou request outcome is unknown",
                "external_write_count": None,
                "write_request_count": write_request_count,
            }
