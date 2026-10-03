"""Immutable, plan-bound policy for governed post-publish promotions.

The approved plan authorizes a *selection rule*, never an activity identifier
observed later in a browser or marketplace response.  During read-only
preparation an adapter must paginate the complete official activity catalogue
and may select exactly one ongoing direct-discount activity.  That selected
identity and time window are then bound to the prepared command/proof.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any


PROMOTION_APPROVAL_SCHEMA = "approved-postpublish-promotion-policy/v2"
LEGACY_APPROVAL_SCHEMA = "approved-postpublish-promotion-policy/v1"
PROMOTION_POLICY_VERSION = "oneclick-postpublish-promotion/v2"
LEGACY_POLICY_VERSION = "oneclick-postpublish-promotion/v1"
DISCOUNT_SOURCE = "immutable_target_pricing/v1"
PROMOTION_SELECTION_POLICY = "unique_ongoing_direct_discount/v1"
PROMOTION_ACTION_PREFIX = "promotion:"
TIKTOK_PROMOTION_WRITE_CLASS = "tiktok:promotion:discount"

TIKTOK_DISCOUNT_PERCENT = 32
SHOPEE_DISCOUNT_PERCENT = 30

_LEGACY_ELIGIBLE: dict[str, tuple[str, str, int]] = {
    "tiktok:LH_PH": ("tiktok", "PHP", TIKTOK_DISCOUNT_PERCENT),
    "tiktok:LH_MY": ("tiktok", "MYR", TIKTOK_DISCOUNT_PERCENT),
    "tiktok:LH_TH": ("tiktok", "THB", TIKTOK_DISCOUNT_PERCENT),
    "tiktok:LH_VN": ("tiktok", "VND", TIKTOK_DISCOUNT_PERCENT),
    "shopee:PH": ("shopee", "PHP", SHOPEE_DISCOUNT_PERCENT),
    "shopee:MY": ("shopee", "MYR", SHOPEE_DISCOUNT_PERCENT),
    "shopee:TH": ("shopee", "THB", SHOPEE_DISCOUNT_PERCENT),
    "shopee:VN": ("shopee", "VND", SHOPEE_DISCOUNT_PERCENT),
}

_ELIGIBLE: dict[str, tuple[str, str, str]] = {
    "tiktok:LH_PH": ("tiktok", "PHP", "tiktok_official"),
    "tiktok:LH_MY": ("tiktok", "MYR", "tiktok_official"),
    "tiktok:LH_TH": ("tiktok", "THB", "tiktok_official"),
    "tiktok:LH_VN": ("tiktok", "VND", "tiktok_official"),
    "tiktok:HB_PH": ("tiktok", "PHP", "miaoshou_browser"),
    "tiktok:HB_MY": ("tiktok", "MYR", "miaoshou_browser"),
    "tiktok:HB_TH": ("tiktok", "THB", "miaoshou_browser"),
    "tiktok:HB_VN": ("tiktok", "VND", "miaoshou_browser"),
    "tiktok:MX": ("tiktok", "MXN", "miaoshou_browser"),
    "tiktok:GB": ("tiktok", "GBP", "miaoshou_browser"),
    "shopee:PH": ("shopee", "PHP", "shopee_official"),
    "shopee:MY": ("shopee", "MYR", "shopee_official"),
    "shopee:TH": ("shopee", "THB", "shopee_official"),
    "shopee:VN": ("shopee", "VND", "shopee_official"),
}


class PostpublishPromotionContractError(ValueError):
    """The approved promotion policy is missing, malformed, or drifted."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _is_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _text(value: object, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
    ):
        raise PostpublishPromotionContractError(f"{field} is invalid")
    return value


def promotion_action_target(prerequisite_target: str) -> str:
    if prerequisite_target not in _ELIGIBLE:
        raise PostpublishPromotionContractError(
            "storefront is not eligible for an API promotion"
        )
    return f"{PROMOTION_ACTION_PREFIX}{prerequisite_target}"


def promotion_prerequisite_target(action_target: object) -> str | None:
    if type(action_target) is not str:
        return None
    if not action_target.startswith(PROMOTION_ACTION_PREFIX):
        return None
    prerequisite = action_target.removeprefix(PROMOTION_ACTION_PREFIX)
    return prerequisite if prerequisite in _ELIGIBLE else None


def is_postpublish_promotion_target(value: object) -> bool:
    return promotion_prerequisite_target(value) is not None


def eligible_promotion_action_targets(
    storefront_targets: Iterable[str],
) -> list[str]:
    return [
        promotion_action_target(target)
        for target in storefront_targets
        if target in _ELIGIBLE
    ]


def all_promotion_action_targets() -> tuple[str, ...]:
    return tuple(
        promotion_action_target(target) for target in sorted(_ELIGIBLE)
    )


def promotion_target_policy(action_target: str) -> dict[str, Any]:
    prerequisite = promotion_prerequisite_target(action_target)
    if prerequisite is None:
        raise PostpublishPromotionContractError(
            "promotion action target is invalid"
        )
    channel, currency, execution_surface = _ELIGIBLE[prerequisite]
    if channel == "tiktok":
        region = (
            prerequisite.rsplit("_", 1)[-1]
            if "_" in prerequisite
            else prerequisite.split(":", 1)[1]
        )
    else:
        region = prerequisite.split(":", 1)[1]
    return {
        "target_label": action_target,
        "prerequisite_target": prerequisite,
        "channel": channel,
        "region": region,
        "currency": currency,
        "discount_source": DISCOUNT_SOURCE,
        "execution_surface": execution_surface,
        "selection_policy": PROMOTION_SELECTION_POLICY,
    }


def _legacy_target_policy(action_target: str) -> dict[str, Any]:
    prerequisite = promotion_prerequisite_target(action_target)
    if prerequisite is None:
        raise PostpublishPromotionContractError(
            "promotion action target is invalid"
        )
    channel, currency, discount = _LEGACY_ELIGIBLE[prerequisite]
    region = (
        prerequisite.rsplit("_", 1)[-1]
        if channel == "tiktok"
        else prerequisite.split(":", 1)[1]
    )
    return {
        "target_label": action_target,
        "prerequisite_target": prerequisite,
        "channel": channel,
        "region": region,
        "currency": currency,
        "discount_percent": discount,
        "selection_policy": PROMOTION_SELECTION_POLICY,
    }


def promotion_policy_digest(*, legacy: bool = False) -> str:
    return _digest(
        {
            "schema_version": LEGACY_APPROVAL_SCHEMA if legacy else PROMOTION_APPROVAL_SCHEMA,
            "policy_version": LEGACY_POLICY_VERSION if legacy else PROMOTION_POLICY_VERSION,
            "selection_policy": PROMOTION_SELECTION_POLICY,
            "eligible": [
                (_legacy_target_policy if legacy else promotion_target_policy)(promotion_action_target(target))
                for target in sorted(_LEGACY_ELIGIBLE if legacy else _ELIGIBLE)
            ],
            "prepare_authority": "official_complete_activity_pagination",
            "success_authority": "official_activity_product_readback",
        }
    )


def build_approved_postpublish_promotion_policy(
    *,
    approval_reference: object,
    approved_by: object = "Kyle",
) -> dict[str, Any]:
    reference = _text(approval_reference, "approval_reference")
    actor = _text(approved_by, "approved_by")
    if actor != "Kyle":
        raise PostpublishPromotionContractError(
            "promotion policy approval requires Kyle"
        )
    root = {
        "schema_version": PROMOTION_APPROVAL_SCHEMA,
        "policy_version": PROMOTION_POLICY_VERSION,
        "selection_policy": PROMOTION_SELECTION_POLICY,
        "policy_digest": promotion_policy_digest(),
        "discounts": {"source": DISCOUNT_SOURCE},
        "approved_by": actor,
        "approval_reference": reference,
    }
    return {**root, "approval_digest": _digest(root)}


def approved_postpublish_promotion_policy(
    immutable_plan_payload: Mapping[str, Any],
) -> dict[str, Any]:
    approval = immutable_plan_payload.get(
        "approved_postpublish_promotion_policy"
    )
    if not isinstance(approval, Mapping):
        raise PostpublishPromotionContractError(
            "approved promotion policy is unavailable"
        )
    expected_keys = {
        "schema_version",
        "policy_version",
        "selection_policy",
        "policy_digest",
        "discounts",
        "approved_by",
        "approval_reference",
        "approval_digest",
    }
    if set(approval) != expected_keys:
        raise PostpublishPromotionContractError(
            "approved promotion policy shape is invalid"
        )
    root = {
        key: approval[key]
        for key in expected_keys
        if key != "approval_digest"
    }
    legacy = approval.get("policy_version") == LEGACY_POLICY_VERSION
    if (
        approval.get("schema_version") != (LEGACY_APPROVAL_SCHEMA if legacy else PROMOTION_APPROVAL_SCHEMA)
        or approval.get("policy_version") != (LEGACY_POLICY_VERSION if legacy else PROMOTION_POLICY_VERSION)
        or approval.get("selection_policy") != PROMOTION_SELECTION_POLICY
        or approval.get("policy_digest") != promotion_policy_digest(legacy=legacy)
        or approval.get("discounts")
        != ({"shopee": SHOPEE_DISCOUNT_PERCENT, "tiktok": TIKTOK_DISCOUNT_PERCENT}
            if legacy else {"source": DISCOUNT_SOURCE})
        or approval.get("approved_by") != "Kyle"
        or _text(
            approval.get("approval_reference"),
            "approval_reference",
        )
        != approval.get("approval_reference")
        or not _is_digest(approval.get("approval_digest"))
        or _digest(root) != approval["approval_digest"]
    ):
        raise PostpublishPromotionContractError(
            "approved promotion policy identity drifted"
        )
    return dict(approval)


def enabled_promotion_action_targets(
    immutable_plan_payload: Mapping[str, Any],
    storefront_targets: Iterable[str],
) -> list[str]:
    """Return server-owned actions only when the immutable plan opted in."""

    if "approved_postpublish_promotion_policy" not in immutable_plan_payload:
        return []
    approval = approved_postpublish_promotion_policy(immutable_plan_payload)
    eligible = _LEGACY_ELIGIBLE if approval["policy_version"] == LEGACY_POLICY_VERSION else _ELIGIBLE
    return [promotion_action_target(target) for target in storefront_targets if target in eligible]


def approved_promotion_action_policy(
    immutable_plan_payload: Mapping[str, Any],
    action_target: str,
) -> dict[str, Any]:
    approval = approved_postpublish_promotion_policy(
        immutable_plan_payload
    )
    legacy = approval["policy_version"] == LEGACY_POLICY_VERSION
    if legacy and promotion_prerequisite_target(action_target) not in _LEGACY_ELIGIBLE:
        raise PostpublishPromotionContractError("target is outside the frozen legacy approval")
    policy = _legacy_target_policy(action_target) if legacy else promotion_target_policy(action_target)
    if not legacy:
        policy["discount_percent"] = _approved_target_discount_percent(
            immutable_plan_payload, policy["prerequisite_target"], expected_currency=policy["currency"])
    return {
        "schema_version": "approved-postpublish-promotion-action-policy/v1",
        **policy,
        "policy_version": approval["policy_version"],
        "policy_digest": approval["policy_digest"],
        "approval_reference": approval["approval_reference"],
        "approval_digest": approval["approval_digest"],
        "action_policy_digest": _digest(
            {
                **policy,
                "policy_version": approval["policy_version"],
                "policy_digest": approval["policy_digest"],
                "approval_reference": approval["approval_reference"],
                "approval_digest": approval["approval_digest"],
            }
        ),
    }



def _approved_target_discount_percent(
    payload: Mapping[str, Any],
    target: str,
    *,
    expected_currency: str,
) -> int:
    pricing = payload.get("pricing")
    selected = pricing.get("selected_targets") if isinstance(pricing, Mapping) else None
    row = selected.get(target) if isinstance(selected, Mapping) else None
    prices = row.get("store_prices") if isinstance(row, Mapping) else None
    if (
        not isinstance(prices, list)
        or len(prices) != 1
        or not isinstance(prices[0], Mapping)
    ):
        raise PostpublishPromotionContractError(
            "exactly one immutable target pricing row is required"
        )
    price = prices[0]
    try:
        percent = Decimal(str(price.get("discount_reserve_pct")))
        list_price = Decimal(str(price.get("list_price")))
        sale_price = Decimal(str(price.get("sale_after_discount")))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PostpublishPromotionContractError(
            "immutable discount pricing is invalid"
        ) from error
    if (
        not percent.is_finite()
        or percent != percent.to_integral_value()
        or not (Decimal("0") < percent < Decimal("100"))
        or not list_price.is_finite()
        or list_price <= 0
        or not sale_price.is_finite()
        or sale_price <= 0
        or str(price.get("currency") or "").upper() != expected_currency
    ):
        raise PostpublishPromotionContractError(
            "immutable discount pricing is invalid"
        )
    expected_sale = list_price * (Decimal("1") - percent / Decimal("100"))
    if abs(expected_sale - sale_price) > Decimal("0.005"):
        raise PostpublishPromotionContractError(
            "immutable sale price does not match discount reserve"
        )
    return int(percent)

__all__ = [
    "PostpublishPromotionContractError",
    "all_promotion_action_targets",
    "DISCOUNT_SOURCE",
    "PROMOTION_ACTION_PREFIX",
    "PROMOTION_APPROVAL_SCHEMA",
    "PROMOTION_POLICY_VERSION",
    "PROMOTION_SELECTION_POLICY",
    "SHOPEE_DISCOUNT_PERCENT",
    "TIKTOK_DISCOUNT_PERCENT",
    "TIKTOK_PROMOTION_WRITE_CLASS",
    "approved_postpublish_promotion_policy",
    "approved_promotion_action_policy",
    "build_approved_postpublish_promotion_policy",
    "eligible_promotion_action_targets",
    "enabled_promotion_action_targets",
    "is_postpublish_promotion_target",
    "promotion_action_target",
    "promotion_policy_digest",
    "promotion_prerequisite_target",
    "promotion_target_policy",
]
