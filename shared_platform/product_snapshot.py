"""Pure projections derived only from an approved immutable product snapshot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


TIKTOK_CATEGORY_DECISION_SCHEMA = "approved-tiktok-category-decision/v1"
APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA = (
    "approved-tiktok-publish-snapshot/v1"
)
TIKTOK_PUBLISH_TARGETS = (
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:MX",
    "tiktok:GB",
)

_TIKTOK_TARGET_SITES = {
    "tiktok:LH_PH": "PH",
    "tiktok:LH_MY": "MY",
    "tiktok:LH_TH": "TH",
    "tiktok:LH_VN": "VN",
    "tiktok:MX": "MX",
    "tiktok:GB": "GB",
}
_TIKTOK_CATEGORY_IDS = {
    "贴饰>墙贴": "600338",
    "墙贴": "600338",
    "wallsticker": "600338",
    "wallstickers": "600338",
}
_TIKTOK_PRICE_BINDINGS = {
    "tiktok:LH_PH": ("lh_ph", "PHP"),
    "tiktok:LH_MY": ("lh_my", "MYR"),
    "tiktok:LH_TH": ("lh_th", "THB"),
    "tiktok:LH_VN": ("lh_vn", "VND"),
    "tiktok:MX": ("mx", "MXN"),
    "tiktok:GB": ("gb", "GBP"),
}


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


def project_approved_tiktok_category_decisions(
    product_category: object,
    *,
    targets: tuple[str, ...],
) -> dict[str, dict[str, str]]:
    """Project category evidence without reading mutable UI or channel state."""

    if not isinstance(product_category, Mapping):
        raise ValueError("approved product category must be a mapping")
    raw_name = product_category.get("name")
    if type(raw_name) is not str or not raw_name.strip():
        raise ValueError("approved product category name is missing")
    if (
        type(targets) is not tuple
        or not targets
        or any(type(target) is not str for target in targets)
        or len(set(targets)) != len(targets)
        or any(target not in _TIKTOK_TARGET_SITES for target in targets)
    ):
        raise ValueError("approved TikTok targets are invalid")
    normalized = "".join(raw_name.split()).lower()
    category_id = _TIKTOK_CATEGORY_IDS.get(normalized)
    if category_id is None:
        raise ValueError("approved product category has no TikTok projection")
    decisions: dict[str, dict[str, str]] = {}
    for target in targets:
        evidence = {
            "schema_version": TIKTOK_CATEGORY_DECISION_SCHEMA,
            "approved_product_category": raw_name.strip(),
            "target_label": target,
            "site": _TIKTOK_TARGET_SITES[target],
            "category_id": category_id,
        }
        decisions[target] = {
            "category_id": category_id,
            "evidence_digest": _digest(evidence),
        }
    return decisions


def _is_sha256(value: object) -> bool:
    return bool(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _approved_price(payload: Mapping[str, object], target: str) -> tuple[str, str]:
    expected_key, expected_currency = _TIKTOK_PRICE_BINDINGS[target]
    pricing = payload.get("pricing")
    selected = pricing.get("selected_targets") if isinstance(pricing, Mapping) else None
    facts = selected.get(target) if isinstance(selected, Mapping) else None
    store_prices = facts.get("store_prices") if isinstance(facts, Mapping) else None
    if not isinstance(store_prices, list) or len(store_prices) != 1:
        raise ValueError(f"{target} requires one approved store price")
    row = store_prices[0]
    if (
        not isinstance(row, Mapping)
        or row.get("target_key") != expected_key
        or row.get("currency") != expected_currency
    ):
        raise ValueError(f"{target} approved price binding drifted")
    raw_price = row.get("list_price")
    if type(raw_price) not in {str, int, float} or isinstance(raw_price, bool):
        raise ValueError(f"{target} approved price is invalid")
    try:
        price = Decimal(str(raw_price))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{target} approved price is invalid") from None
    if not price.is_finite() or price <= 0:
        raise ValueError(f"{target} approved price is invalid")
    normalized = format(price.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized, expected_currency


def _approved_categories(payload: Mapping[str, object]) -> dict[str, dict[str, str]]:
    product_facts = payload.get("product_facts")
    if not isinstance(product_facts, Mapping):
        raise ValueError("approved TikTok category inputs are incomplete")
    expected = project_approved_tiktok_category_decisions(
        product_facts.get("category"),
        targets=TIKTOK_PUBLISH_TARGETS,
    )
    supplied = payload.get("approved_tiktok_category_decisions")
    if supplied is not None and supplied != expected:
        raise ValueError("approved TikTok category evidence drifted")
    return expected


def _approved_target_context(
    *,
    context: object,
    target: str,
    approved_identity: Mapping[str, object],
) -> dict[str, str]:
    from shared_platform.collectbox_action import CollectBoxTargetDetailIdentity

    if not isinstance(context, Mapping) or set(context) != {
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
    }:
        raise ValueError(f"{target} collect-box context is malformed")
    if (
        context.get("schema_version")
        != "collectbox-tiktok-publish-context/v1"
        or context.get("platform") != "TIKTOK"
        or context.get("plan_id") != approved_identity["plan_id"]
        or context.get("offer_id") != approved_identity["offer_id"]
        or context.get("product_revision") != approved_identity["product_revision"]
        or context.get("payload_digest") != approved_identity["payload_digest"]
        or context.get("targets_digest") != approved_identity["targets_digest"]
        or type(context.get("action_id")) is not str
        or not context["action_id"]
        or not _is_sha256(context.get("common_identity_digest"))
        or not _is_sha256(context.get("receipt_digest"))
        or not _is_sha256(context.get("publish_identity_digest"))
    ):
        raise ValueError(f"{target} collect-box context drifted")
    raw_detail = context.get("target_detail_identity")
    if not isinstance(raw_detail, Mapping):
        raise ValueError(f"{target} target detail identity is missing")
    detail = CollectBoxTargetDetailIdentity(
        target_label=raw_detail.get("target_label"),
        detail_id=raw_detail.get("detail_id"),
        shop_id=raw_detail.get("shop_id"),
    ).internal_payload()
    if detail != raw_detail or detail["target_label"] != target:
        raise ValueError(f"{target} target detail identity drifted")
    binding = dict(context)
    binding.pop("publish_identity_digest")
    if _digest(binding) != context["publish_identity_digest"]:
        raise ValueError(f"{target} publish identity drifted")
    return {
        "detail_id": detail["detail_id"],
        "shop_id": detail["shop_id"],
        "target_identity_digest": detail["identity_digest"],
        "publish_identity_digest": str(context["publish_identity_digest"]),
        "receipt_digest": str(context["receipt_digest"]),
    }


def build_approved_tiktok_publish_snapshot(
    plan: object,
    *,
    collectbox_contexts: object,
) -> dict[str, object]:
    """Build the JSON-only publisher input from two immutable read sources."""

    from shared_platform.collectbox_action import approved_plan_identity

    if not isinstance(plan, Mapping):
        raise ValueError("approved plan is missing")
    identity = approved_plan_identity(plan)
    payload = plan.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("approved plan payload is missing")
    tiktok_targets = [
        target
        for target in plan.get("targets", [])
        if type(target) is str and target.startswith("tiktok:")
    ]
    if tiktok_targets != list(TIKTOK_PUBLISH_TARGETS):
        raise ValueError("approved TikTok target set or order drifted")
    if not isinstance(collectbox_contexts, Mapping) or set(
        collectbox_contexts
    ) != set(TIKTOK_PUBLISH_TARGETS):
        raise ValueError("exact six-target collect-box identity is unavailable")
    categories = _approved_categories(payload)
    targets: list[dict[str, object]] = []
    for target in TIKTOK_PUBLISH_TARGETS:
        price, currency = _approved_price(payload, target)
        draft = _approved_target_context(
            context=collectbox_contexts[target],
            target=target,
            approved_identity=identity,
        )
        targets.append(
            {
                "target_label": target,
                **draft,
                "expected_price": price,
                "expected_currency": currency,
                "expected_category_id": categories[target]["category_id"],
                "category_evidence_digest": categories[target][
                    "evidence_digest"
                ],
            }
        )
    return {
        "schema_version": APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA,
        "offer_id": identity["offer_id"],
        "plan_id": identity["plan_id"],
        "product_revision": identity["product_revision"],
        "payload_digest": identity["payload_digest"],
        "targets": targets,
    }


__all__ = [
    "APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA",
    "TIKTOK_CATEGORY_DECISION_SCHEMA",
    "TIKTOK_PUBLISH_TARGETS",
    "build_approved_tiktok_publish_snapshot",
    "project_approved_tiktok_category_decisions",
]
