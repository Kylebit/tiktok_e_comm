"""Pure projections derived only from an approved immutable product snapshot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


TIKTOK_CATEGORY_DECISION_SCHEMA = "approved-tiktok-category-decision/v1"
APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA = (
    "approved-tiktok-publish-snapshot/v2"
)
TIKTOK_PUBLISH_TARGETS = (
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

_TIKTOK_TARGET_SITES = {
    "tiktok:LH_PH": "PH",
    "tiktok:LH_MY": "MY",
    "tiktok:LH_TH": "TH",
    "tiktok:LH_VN": "VN",
    "tiktok:MX": "MX",
    "tiktok:GB": "GB",
    "tiktok:HB_PH": "PH",
    "tiktok:HB_MY": "MY",
    "tiktok:HB_TH": "TH",
    "tiktok:HB_VN": "VN",
}
_TIKTOK_CATEGORY_IDS = {
    "贴饰>墙贴": "600338",
    "墙贴": "600338",
    "wallsticker": "600338",
    "wallstickers": "600338",
}
_TIKTOK_SITE_RESOLVED_CATEGORY_NAMES = frozenset(
    {
        "居家布艺>桌旗",
        "桌布、桌旗",
        "桌布>桌旗",
        "家纺布艺>居家布艺>桌布、桌旗",
        "tablecloth",
        "tablerunner",
        "kitchenlinens",
    }
)
_TIKTOK_PRICE_BINDINGS = {
    "tiktok:LH_PH": ("lh_ph", "PHP"),
    "tiktok:LH_MY": ("lh_my", "MYR"),
    "tiktok:LH_TH": ("lh_th", "THB"),
    "tiktok:LH_VN": ("lh_vn", "VND"),
    "tiktok:MX": ("mx", "MXN"),
    "tiktok:GB": ("gb", "GBP"),
    "tiktok:HB_PH": ("hb_ph", "PHP"),
    "tiktok:HB_MY": ("hb_my", "MYR"),
    "tiktok:HB_TH": ("hb_th", "THB"),
    "tiktok:HB_VN": ("hb_vn", "VND"),
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
) -> dict[str, dict[str, str | None]]:
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
    if (
        category_id is None
        and normalized not in _TIKTOK_SITE_RESOLVED_CATEGORY_NAMES
        and product_category.get("confidence") != "approved"
    ):
        raise ValueError("approved product category has no TikTok projection")
    decisions: dict[str, dict[str, str | None]] = {}
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


def _normalize_price(raw_price: object, *, target: str) -> str:
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
    return normalized


def _normalize_positive_decimal(value: object, *, name: str) -> str:
    if type(value) not in {str, int, float} or isinstance(value, bool):
        raise ValueError(f"{name} is invalid")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} is invalid") from None
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{name} is invalid")
    normalized = format(result.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def _approved_parcel_facts(
    payload: Mapping[str, object],
    *,
    variant_model_skus: Mapping[str, str],
) -> tuple[str, list[str], dict[str, dict[str, object]]]:
    facts = payload.get("product_facts")
    if not isinstance(facts, Mapping):
        raise ValueError("approved TikTok parcel inputs are incomplete")
    weight = _normalize_positive_decimal(
        facts.get("weight_kg"), name="approved parent weight"
    )
    raw_package = facts.get("package_cm")
    if not isinstance(raw_package, list) or len(raw_package) != 3:
        raise ValueError("approved parent package is invalid")
    package = [
        _normalize_positive_decimal(value, name="approved parent package")
        for value in raw_package
    ]
    if not variant_model_skus:
        return weight, package, {}

    raw_rows = facts.get("sku_commercial_facts")
    if raw_rows is None and len(variant_model_skus) == 1:
        only_variant = next(iter(variant_model_skus))
        return weight, package, {
            only_variant: {
                "weight_kg": weight,
                "package_cm": list(package),
            }
        }
    if not isinstance(raw_rows, Mapping):
        raise ValueError("approved per-SKU parcel inputs are incomplete")
    normalized_rows: dict[str, Mapping[str, object]] = {}
    for raw_variant, raw_row in raw_rows.items():
        if (
            type(raw_variant) is not str
            or not raw_variant.strip().strip(";")
            or not isinstance(raw_row, Mapping)
        ):
            raise ValueError("approved per-SKU parcel inputs are invalid")
        variant = raw_variant.strip().strip(";")
        if variant in normalized_rows:
            raise ValueError("approved per-SKU parcel identity is ambiguous")
        normalized_rows[variant] = raw_row
    if set(normalized_rows) != set(variant_model_skus):
        raise ValueError("approved per-SKU parcel coverage drifted")

    sku_parcels: dict[str, dict[str, object]] = {}
    for variant in variant_model_skus:
        row = normalized_rows[variant]
        raw_sku_package = row.get("package_cm")
        if not isinstance(raw_sku_package, list) or len(raw_sku_package) != 3:
            raise ValueError("approved per-SKU package is invalid")
        sku_parcels[variant] = {
            "weight_kg": _normalize_positive_decimal(
                row.get("weight_kg"), name="approved per-SKU weight"
            ),
            "package_cm": [
                _normalize_positive_decimal(
                    value, name="approved per-SKU package"
                )
                for value in raw_sku_package
            ],
        }
    return weight, package, sku_parcels


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
    return _normalize_price(row.get("list_price"), target=target), expected_currency


def _approved_sku_prices(
    payload: Mapping[str, object], target: str
) -> dict[str, str]:
    expected_key, expected_currency = _TIKTOK_PRICE_BINDINGS[target]
    pricing = payload.get("pricing")
    selected = pricing.get("selected_targets") if isinstance(pricing, Mapping) else None
    facts = selected.get(target) if isinstance(selected, Mapping) else None
    raw_rows = facts.get("sku_prices") if isinstance(facts, Mapping) else None
    product_facts = payload.get("product_facts")
    selected_sku_keys = (
        product_facts.get("selected_sku_keys")
        if isinstance(product_facts, Mapping)
        else None
    )
    selected_sku_count = (
        len(selected_sku_keys) if isinstance(selected_sku_keys, list) else 0
    )
    sku_lineage = payload.get("sku_lineage")
    assignment = (
        sku_lineage.get("assignment") if isinstance(sku_lineage, Mapping) else None
    )
    lineage_rows = (
        assignment.get("model_skus") if isinstance(assignment, Mapping) else None
    )
    lineage_model_skus = {
        row.get("model_sku").strip()
        for row in lineage_rows or []
        if isinstance(row, Mapping)
        and type(row.get("model_sku")) is str
        and row.get("model_sku").strip()
    }
    if raw_rows is None:
        if selected_sku_count > 1 or len(lineage_model_skus) > 1:
            raise ValueError(f"{target} requires approved per-SKU prices")
        return {}
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError(f"{target} approved SKU prices are invalid")
    result: dict[str, str] = {}
    for row in raw_rows:
        if (
            not isinstance(row, Mapping)
            or row.get("target_key") != expected_key
            or row.get("currency") != expected_currency
        ):
            raise ValueError(f"{target} approved SKU price binding drifted")
        model_sku = row.get("model_sku")
        if type(model_sku) is not str or not model_sku.strip():
            raise ValueError(f"{target} approved model SKU is invalid")
        clean_model_sku = model_sku.strip()
        if clean_model_sku in result:
            raise ValueError(f"{target} approved model SKU is duplicated")
        result[clean_model_sku] = _normalize_price(
            row.get("list_price"), target=target
        )
    if selected_sku_count and len(result) != selected_sku_count:
        raise ValueError(f"{target} approved SKU price coverage drifted")
    if lineage_model_skus and set(result) != lineage_model_skus:
        raise ValueError(f"{target} approved model SKU price coverage drifted")
    return result


def _approved_variant_model_skus(
    payload: Mapping[str, object],
) -> dict[str, str]:
    product_facts = payload.get("product_facts")
    raw_selected = (
        product_facts.get("selected_sku_keys")
        if isinstance(product_facts, Mapping)
        else None
    )
    sku_lineage = payload.get("sku_lineage")
    assignment = (
        sku_lineage.get("assignment") if isinstance(sku_lineage, Mapping) else None
    )
    raw_rows = assignment.get("model_skus") if isinstance(assignment, Mapping) else None
    if raw_selected is None and raw_rows is None:
        return {}
    if (
        not isinstance(raw_selected, list)
        or not raw_selected
        or any(type(value) is not str or not value.strip() for value in raw_selected)
        or not isinstance(raw_rows, list)
        or not raw_rows
    ):
        raise ValueError("approved TikTok variant lineage is incomplete")
    selected = [value.strip().strip(";") for value in raw_selected]
    if any(not value for value in selected) or len(selected) != len(set(selected)):
        raise ValueError("approved TikTok variant identity is ambiguous")
    by_variant: dict[str, str] = {}
    seen_models: set[str] = set()
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise ValueError("approved TikTok variant lineage is invalid")
        raw_variant = row.get("variant_key")
        raw_model = row.get("model_sku")
        if (
            type(raw_variant) is not str
            or not raw_variant.strip().strip(";")
            or type(raw_model) is not str
            or not raw_model.strip()
        ):
            raise ValueError("approved TikTok variant lineage is invalid")
        variant = raw_variant.strip().strip(";")
        model_sku = raw_model.strip()
        if variant in by_variant or model_sku in seen_models:
            raise ValueError("approved TikTok variant lineage is ambiguous")
        by_variant[variant] = model_sku
        seen_models.add(model_sku)
    if set(by_variant) != set(selected):
        raise ValueError("approved TikTok variant lineage coverage drifted")
    return {variant: by_variant[variant] for variant in selected}


def _approved_categories(
    payload: Mapping[str, object], targets: tuple[str, ...]
) -> dict[str, dict[str, str | None]]:
    product_facts = payload.get("product_facts")
    if not isinstance(product_facts, Mapping):
        raise ValueError("approved TikTok category inputs are incomplete")
    expected = project_approved_tiktok_category_decisions(
        product_facts.get("category"),
        targets=targets,
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
    if (
        not tiktok_targets
        or len(tiktok_targets) != len(set(tiktok_targets))
        or any(target not in TIKTOK_PUBLISH_TARGETS for target in tiktok_targets)
    ):
        raise ValueError("approved TikTok targets are invalid")
    if not isinstance(collectbox_contexts, Mapping):
        raise ValueError("collect-box identities are unavailable")
    selected_targets = tuple(tiktok_targets)
    categories = _approved_categories(payload, selected_targets)
    variant_model_skus = _approved_variant_model_skus(payload)
    weight, package, sku_parcels = _approved_parcel_facts(
        payload,
        variant_model_skus=variant_model_skus,
    )
    targets: list[dict[str, object]] = []
    unavailable_targets: list[dict[str, str]] = []
    for target in selected_targets:
        price, currency = _approved_price(payload, target)
        sku_prices = _approved_sku_prices(payload, target)
        if target not in collectbox_contexts:
            unavailable_targets.append(
                {
                    "target_label": target,
                    "reason_code": "draft_identity_unavailable",
                }
            )
            continue
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
                "expected_sku_prices": sku_prices,
                "expected_variant_model_skus": variant_model_skus,
                "expected_weight_kg": weight,
                "expected_package_cm": package,
                "expected_sku_parcels": sku_parcels,
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
        "unavailable_targets": unavailable_targets,
    }


__all__ = [
    "APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA",
    "TIKTOK_CATEGORY_DECISION_SCHEMA",
    "TIKTOK_PUBLISH_TARGETS",
    "build_approved_tiktok_publish_snapshot",
    "project_approved_tiktok_category_decisions",
]
