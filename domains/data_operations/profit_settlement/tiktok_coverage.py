"""Pure TikTok created-order to Finance-settlement coverage audit."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from typing import Iterable, Mapping


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
) -> dict:
    """Return a redacted, deterministic coverage artifact."""

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
    settled = []
    cancelled = []
    unsettled = []
    for order in normalized:
        if order["order_id"] in settled_order_ids:
            settled.append(order)
        elif _is_cancelled(order["order_status"]):
            cancelled.append(order)
        else:
            unsettled.append(order)

    canonical_input = {
        "orders": normalized,
        "settled_order_ids": sorted(settled_order_ids),
        "settlement_snapshot_id": settlement_snapshot_id,
    }
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
        "cancelled_without_settlement": len(cancelled),
        "unsettled_non_cancelled": len(unsettled),
    }
    return {
        "schema_version": "tiktok-order-settlement-coverage/v1",
        "status": "ready" if not unsettled else "needs_review",
        "platform": "tiktok",
        "site": "TH",
        "created_period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone": "Asia/Bangkok",
        },
        "settlement_observed_through": as_of.isoformat(),
        "settlement_snapshot_id": settlement_snapshot_id,
        "snapshot_id": f"tiktok-order-settlement-coverage:{checksum}",
        "checksum": checksum,
        "counts": counts,
        "all_created_orders_settled": counts["settled_orders"] == counts["created_orders"],
        "all_non_cancelled_orders_settled": not unsettled,
        "settled_orders": settled,
        "cancelled_without_settlement_orders": cancelled,
        "unsettled_non_cancelled_orders": unsettled,
        "receipt": {
            "external_reads_performed": [],
            "external_writes_performed": [],
            "raw_response_retained": False,
        },
    }
