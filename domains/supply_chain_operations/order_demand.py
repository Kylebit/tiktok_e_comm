"""Pure order-led demand aggregation for TikTok and Shopee."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from domains.supply_chain_operations.demand_trend import calculate_segmented_trend


REGION_SKU_PREFIX = {"MY": "660", "TH": "990", "VN": "880", "PH": "770"}
REGION_UTC_OFFSET_HOURS = {"MY": 8, "TH": 7, "VN": 7, "PH": 8}
_CLIPPED_MARKERS = ("...", "…", "*")
_TIKTOK_EXCLUDED = frozenset({"UNPAID", "CANCELLED", "ON_HOLD"})
_SHOPEE_EXCLUDED = frozenset({"UNPAID", "CANCELLED", "IN_CANCEL"})


def canonical_order_sku(value: object, region: str) -> tuple[str, str] | None:
    """Return (canonical, complete source) for an approved country SKU shape."""

    if type(value) is not str:
        return None
    source = value.strip()
    region = region.upper()
    if not source or any(marker in source for marker in _CLIPPED_MARKERS):
        return None
    if len(source) == 4 and source.isdigit():
        return source, source
    prefix = REGION_SKU_PREFIX.get(region)
    if prefix and len(source) == 6 and source.isdigit() and source.startswith(prefix):
        return source[-4:], source
    return None


def _event_day(timestamp: object, region: str) -> str | None:
    if type(timestamp) is not int or timestamp <= 0:
        return None
    region_timezone = timezone(
        timedelta(hours=REGION_UTC_OFFSET_HOURS[region.upper()])
    )
    return datetime.fromtimestamp(timestamp, region_timezone).date().isoformat()


def _new_row() -> dict[str, Any]:
    return {
        "order_ids": set(),
        "units": 0,
        "daily": Counter(),
        "source_aliases": set(),
        "cancelled_units": 0,
        "returned_units": 0,
        "name": "",
        "image_url": "",
    }


def aggregate_tiktok_orders(
    orders: Iterable[dict[str, Any]], region: str
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    region = region.upper()
    rows: defaultdict[str, dict[str, Any]] = defaultdict(_new_row)
    evidence = Counter()
    statuses = Counter()

    for order in orders:
        evidence["orders_seen"] += 1
        status = str(order.get("status") or "").upper()
        statuses[status or "UNKNOWN"] += 1
        if (
            status in _TIKTOK_EXCLUDED
            or order.get("is_on_hold_order") is True
            or order.get("is_sample_order") is True
            or order.get("is_replacement_order") is True
        ):
            evidence["orders_excluded"] += 1
            continue
        event_ts = order.get("paid_time") or order.get("create_time")
        day = _event_day(event_ts, region)
        order_id = order.get("id")
        if day is None or type(order_id) is not str or not order_id:
            evidence["orders_invalid"] += 1
            continue
        order_has_eligible_item = False
        for item in order.get("line_items") or []:
            mapped = canonical_order_sku(item.get("seller_sku"), region)
            if mapped is None:
                evidence["item_lines_unresolved"] += 1
                continue
            sku, source = mapped
            row = rows[sku]
            row["units"] += 1
            row["daily"][day] += 1
            row["order_ids"].add(order_id)
            row["source_aliases"].add(source)
            if not row["name"] and type(item.get("product_name")) is str:
                row["name"] = item["product_name"].strip()
            if not row["image_url"] and type(item.get("sku_image")) is str:
                row["image_url"] = item["sku_image"].strip()
            order_has_eligible_item = True
            evidence["item_lines_included"] += 1
        if order_has_eligible_item:
            evidence["orders_included"] += 1

    evidence["status_counts"] = dict(sorted(statuses.items()))
    return dict(rows), dict(evidence)


def aggregate_shopee_orders(
    orders: Iterable[dict[str, Any]], region: str
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    region = region.upper()
    rows: defaultdict[str, dict[str, Any]] = defaultdict(_new_row)
    evidence = Counter()
    statuses = Counter()

    for order in orders:
        evidence["orders_seen"] += 1
        status = str(order.get("order_status") or "").upper()
        statuses[status or "UNKNOWN"] += 1
        if status in _SHOPEE_EXCLUDED:
            evidence["orders_excluded"] += 1
            continue
        day = _event_day(order.get("create_time"), region)
        order_id = order.get("order_sn")
        if day is None or type(order_id) is not str or not order_id:
            evidence["orders_invalid"] += 1
            continue
        order_has_eligible_item = False
        for item in order.get("item_list") or []:
            mapped = canonical_order_sku(
                item.get("model_sku") or item.get("item_sku"), region
            )
            quantity = item.get("model_quantity_purchased")
            cancelled = item.get("cancelled_qty") or 0
            returned = item.get("returned_qty") or 0
            if mapped is None:
                evidence["item_lines_unresolved"] += 1
                continue
            if (
                type(quantity) is not int
                or quantity <= 0
                or type(cancelled) is not int
                or cancelled < 0
                or cancelled > quantity
                or type(returned) is not int
                or returned < 0
            ):
                evidence["item_lines_invalid_quantity"] += 1
                continue
            eligible = quantity - cancelled
            if eligible <= 0:
                evidence["item_lines_fully_cancelled"] += 1
                continue
            sku, source = mapped
            row = rows[sku]
            row["units"] += eligible
            row["daily"][day] += eligible
            row["order_ids"].add(order_id)
            row["source_aliases"].add(source)
            row["cancelled_units"] += cancelled
            row["returned_units"] += returned
            if not row["name"] and type(item.get("item_name")) is str:
                row["name"] = item["item_name"].strip()
            image_info = item.get("image_info") or {}
            if (
                not row["image_url"]
                and isinstance(image_info, dict)
                and type(image_info.get("image_url")) is str
            ):
                row["image_url"] = image_info["image_url"].strip()
            order_has_eligible_item = True
            evidence["item_lines_included"] += 1
        if order_has_eligible_item:
            evidence["orders_included"] += 1

    evidence["status_counts"] = dict(sorted(statuses.items()))
    return dict(rows), dict(evidence)


def finalize_order_snapshot(
    rows: dict[str, dict[str, Any]],
    *,
    region: str,
    platform: str,
    captured_at: datetime,
    days: int,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    if type(days) is not int or days < 30:
        raise ValueError("days must be a built-in int of at least 30")
    region = region.upper()
    region_timezone = timezone(timedelta(hours=REGION_UTC_OFFSET_HOURS[region]))
    captured_day = captured_at.astimezone(region_timezone).date()
    facts: dict[str, Any] = {}

    for sku, row in sorted(rows.items()):
        daily = dict(row["daily"])
        buckets = [0, 0, 0]
        recent_days: list[int] = []
        for day_text, units in daily.items():
            age = (captured_day - datetime.fromisoformat(day_text).date()).days
            if 0 <= age < 7:
                buckets[0] += units
                recent_days.append(units)
            elif 7 <= age < 15:
                buckets[1] += units
                recent_days.append(units)
            elif 15 <= age < 30:
                buckets[2] += units
                recent_days.append(units)
        recent_total = sum(buckets)
        trend = calculate_segmented_trend(
            last_7_units=buckets[0],
            days_8_to_15_units=buckets[1],
            days_16_to_30_units=buckets[2],
            active_sales_days_30=sum(1 for value in recent_days if value > 0),
            max_daily_units_30=max(recent_days, default=0),
        )
        facts[sku] = {
            "days": days,
            "orders": len(row["order_ids"]),
            "units": row["units"],
            "recent30Units": recent_total,
            "quantityBasis": "valid_order",
            "eventTimeBasis": (
                "paid_time_preferred_confirmed_create_fallback"
                if platform == "TikTok"
                else "create_time_confirmed_order"
            ),
            "state": "READY",
            "source": f"{platform} {region} 有效订单",
            "evidence": "complete_order_window",
            "sourceAliases": sorted(row["source_aliases"]),
            "cancelledUnits": row["cancelled_units"],
            "returnedUnits": row["returned_units"],
            "name": row["name"],
            "imageUrl": row["image_url"],
            "trendDecision": trend,
        }

    digest_payload = {
        "region": region,
        "platform": platform,
        "captured_at": captured_at.isoformat(),
        "days": days,
        "facts": facts,
        "evidence": evidence,
    }
    digest = hashlib.sha256(
        json.dumps(
            digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return {**digest_payload, "digest": digest}
