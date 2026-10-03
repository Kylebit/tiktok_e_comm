"""Pure TikTok created-order to Finance-settlement coverage audit."""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
import json
from typing import Iterable, Mapping

from .monthly_missing_cost_scope import monthly_cost_period


SITE_LOCAL_BASIS = 'site_local_order_created_at'


def _canonical_status(value: object) -> str:
    return str(value or "UNKNOWN").strip().upper() or "UNKNOWN"


def _is_cancelled(status: str) -> bool:
    return "CANCEL" in status


def build_coverage(
    *,
    orders: Iterable[Mapping[str, object]],
    settled_order_ids: set[str],
    start: date,
    end: date,
    as_of: date,
    settlement_snapshot_id: str,
    site: str = "TH",
    timezone_name: str = "Asia/Bangkok",
    date_basis: str = "legacy_timestamp_date",
) -> dict:
    """Return a redacted coverage artifact; v2 binds site-local dates explicitly.

    The default preserves historical v1 bytes. Existing v1 receipts must not
    be relabelled as v2: rebuild from frozen original orders and reconcile.
    """

    if date_basis not in {'legacy_timestamp_date', SITE_LOCAL_BASIS}:
        raise ValueError('unsupported coverage date basis')
    site_zone = None
    if date_basis == SITE_LOCAL_BASIS:
        _, local_end = monthly_cost_period(site, start, end, timezone_name)
        site_zone = local_end.tzinfo

    deduplicated = {}
    for raw in orders:
        order_id = str(raw.get("order_id") or "").strip()
        if not order_id:
            continue
        deduplicated[order_id] = {
            "order_id": order_id,
            "order_created_at": str(raw.get("order_created_at") or ""),
            "order_status": _canonical_status(raw.get("order_status")),
        }

    normalized = [deduplicated[key] for key in sorted(deduplicated)]
    if site_zone is not None:
        for order in normalized:
            stamp = datetime.fromisoformat(order['order_created_at'].replace('Z', '+00:00'))
            if stamp.tzinfo is None or not start <= stamp.astimezone(site_zone).date() <= end:
                raise ValueError('coverage order is outside exact site-local created period')
    settled = []
    cancelled_with_settlement = []
    cancelled = []
    unsettled = []
    for order in normalized:
        if order["order_id"] in settled_order_ids:
            settled.append(order)
            if _is_cancelled(order["order_status"]):
                cancelled_with_settlement.append(order)
        elif _is_cancelled(order["order_status"]):
            cancelled.append(order)
        else:
            unsettled.append(order)

    canonical_input = {
        "orders": normalized,
        "settled_order_ids": sorted(settled_order_ids),
        "settlement_snapshot_id": settlement_snapshot_id,
        "site": site,
        "timezone": timezone_name,
    }
    if site_zone is not None:
        canonical_input['date_basis'] = SITE_LOCAL_BASIS
    checksum = sha256(
        json.dumps(
            canonical_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    counts = {
        "created_orders": len(normalized),
        "settled_orders": len(settled),
        "cancelled_orders": len(cancelled_with_settlement) + len(cancelled),
        "cancelled_with_settlement": len(cancelled_with_settlement),
        "cancelled_without_settlement": len(cancelled),
        "unsettled_non_cancelled": len(unsettled),
    }
    return {
        "schema_version": "tiktok-order-settlement-coverage/v2" if site_zone is not None else "tiktok-order-settlement-coverage/v1",
        **({'date_basis': SITE_LOCAL_BASIS} if site_zone is not None else {}),
        "status": "ready" if not unsettled else "needs_review",
        "platform": "tiktok",
        "site": site,
        "created_period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone": timezone_name,
        },
        "settlement_observed_through": as_of.isoformat(),
        "settlement_snapshot_id": settlement_snapshot_id,
        "snapshot_id": f"tiktok-order-settlement-coverage:{checksum}",
        "checksum": checksum,
        "counts": counts,
        "all_created_orders_settled": counts["settled_orders"] == counts["created_orders"],
        "all_non_cancelled_orders_settled": not unsettled,
        "settled_orders": settled,
        "cancelled_with_settlement_orders": cancelled_with_settlement,
        "cancelled_without_settlement_orders": cancelled,
        "unsettled_non_cancelled_orders": unsettled,
        "receipt": {
            "external_reads_performed": [],
            "external_writes_performed": [],
            "raw_response_retained": False,
        },
    }
