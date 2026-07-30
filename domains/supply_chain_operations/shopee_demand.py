"""Pure Shopee escrow-detail aggregation for replenishment demand.

The live client and credentials stay outside this module.  Callers inject
already-fetched escrow details and persist only SKU-level aggregates.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

_CANONICAL_SKU = re.compile(r"^\d{4}$")
_APPROVED_CHANNEL_ALIAS = re.compile(r"^(?:77|99)(\d{4})$")


def canonical_demand_sku(raw: object) -> str | None:
    """Return the approved four-digit sales SKU without fuzzy truncation.

    Channel aliases are limited to the explicitly approved ``77xxxx`` and
    ``99xxxx`` forms.  Any other shape is rejected instead of taking an
    arbitrary suffix.
    """

    if type(raw) is not str:
        return None
    value = raw.strip()
    if _CANONICAL_SKU.fullmatch(value):
        return value
    match = _APPROVED_CHANNEL_ALIAS.fullmatch(value)
    return match.group(1) if match else None


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def aggregate_escrow_details(
    details: Iterable[dict[str, Any]],
    *,
    window_days: int,
    recent_cutoff_ts: int,
    catalog_sku_by_model: dict[tuple[int, int], str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Aggregate redacted order details into exact-SKU demand facts."""

    if type(window_days) is not int or isinstance(window_days, bool) or window_days <= 0:
        raise ValueError("window_days must be a positive built-in int")
    if type(recent_cutoff_ts) is not int or isinstance(recent_cutoff_ts, bool):
        raise ValueError("recent_cutoff_ts must be a built-in int")

    totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "orders": 0,
            "units": 0,
            "recent30Units": 0,
            "customerPayment": 0.0,
            "actualShippingFee": 0.0,
            "sourceAliases": set(),
        }
    )
    rejected = 0
    detail_count = 0

    catalog_sku_by_model = catalog_sku_by_model or {}
    catalog_resolved = 0

    for envelope in details:
        detail_count += 1
        release_ts = envelope.get("release_ts")
        if type(release_ts) is not int or isinstance(release_ts, bool):
            release_ts = 0
        response = envelope.get("detail") or {}
        order_income = response.get("order_income") or {}
        items = order_income.get("items") or []
        shipping_total = _number(order_income.get("actual_shipping_fee"))

        prepared: list[tuple[str, str, int, float]] = []
        for item in items:
            source_sku = str(item.get("model_sku") or item.get("item_sku") or "").strip()
            canonical = canonical_demand_sku(source_sku)
            if canonical is None:
                item_id = item.get("item_id")
                model_id = item.get("model_id")
                if (
                    type(item_id) is int
                    and not isinstance(item_id, bool)
                    and type(model_id) is int
                    and not isinstance(model_id, bool)
                ):
                    catalog_sku = catalog_sku_by_model.get((item_id, model_id))
                    canonical = canonical_demand_sku(catalog_sku)
                    if canonical is not None:
                        catalog_resolved += 1
            quantity = item.get("quantity_purchased")
            if canonical is None or type(quantity) is not int or isinstance(quantity, bool) or quantity <= 0:
                rejected += 1
                continue
            unit_price = _number(item.get("discounted_price") or item.get("selling_price"))
            prepared.append((canonical, source_sku, quantity, unit_price * quantity))

        weight_total = sum(max(subtotal, 0.01) for _, _, _, subtotal in prepared) or 1.0
        seen_in_order: set[str] = set()
        for canonical, source_sku, quantity, subtotal in prepared:
            row = totals[canonical]
            if canonical not in seen_in_order:
                row["orders"] += 1
                seen_in_order.add(canonical)
            row["units"] += quantity
            if release_ts >= recent_cutoff_ts:
                row["recent30Units"] += quantity
            row["customerPayment"] += subtotal
            row["actualShippingFee"] += shipping_total * (max(subtotal, 0.01) / weight_total)
            row["sourceAliases"].add(source_sku)

    result: dict[str, dict[str, Any]] = {}
    for sku, row in sorted(totals.items()):
        result[sku] = {
            "days": window_days,
            "orders": row["orders"],
            "units": row["units"],
            "recent30Units": row["recent30Units"],
            "customerPayment": round(row["customerPayment"], 2),
            "actualShippingFee": round(row["actualShippingFee"], 2),
            "sourceAliases": sorted(row["sourceAliases"]),
        }
    return result, {
        "details": detail_count,
        "catalog_resolved_items": catalog_resolved,
        "rejected_items": rejected,
    }


def utc_timestamp(year: int, month: int, day: int) -> int:
    """Small deterministic helper used by the local pull workflow."""

    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())
