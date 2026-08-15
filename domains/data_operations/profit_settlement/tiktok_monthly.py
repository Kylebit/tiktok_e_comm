"""TikTok monthly evidence helpers independent from other platforms."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Mapping


def actual_advertising_from_finance(
    evidence: Mapping[str, object],
    *,
    start: date,
    end: date,
    fx_rate_cny_per_local: Decimal,
    fx_snapshot_id: str,
) -> dict[str, object]:
    """Extract actual TikTok GMV-ad charges dated in the calendar month."""

    selected = []
    for row in evidence.get("orders") or []:
        if not isinstance(row, Mapping):
            continue
        transaction_type = str(row.get("transaction_type") or "")
        if "gmv payment" not in transaction_type.lower() or "ads" not in transaction_type.lower():
            continue
        try:
            settled_at = datetime.fromisoformat(str(row.get("settled_at") or ""))
            amount = Decimal(str(row.get("net_settlement_amount")))
        except (ValueError, TypeError, InvalidOperation):
            continue
        if start <= settled_at.date() <= end:
            selected.append({
                "statement_id": str(row.get("statement_id") or ""),
                "settled_at": settled_at.isoformat(),
                "amount_local": str(amount),
                "currency": str(row.get("currency") or ""),
            })
    if not selected:
        raise ValueError("no TikTok Finance GMV advertising charges in monthly period")
    currencies = {row["currency"] for row in selected}
    if len(currencies) != 1 or "" in currencies:
        raise ValueError("monthly advertising charges must use one explicit currency")
    total_local = -sum((Decimal(row["amount_local"]) for row in selected), Decimal("0"))
    if total_local < 0:
        raise ValueError("monthly advertising charges resolve to a negative cost")
    canonical = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "total_cny": total_local * fx_rate_cny_per_local,
        "total_local": total_local,
        "currency": next(iter(currencies)),
        "source": "TikTok Finance GMV Payment for TikTok Ads",
        "as_of": max(row["settled_at"] for row in selected),
        "snapshot_id": f"tiktok-finance-actual-ads:{digest}",
        "source_row_count": len(selected),
        "allocation_policy": "buyer-paid-gmv-share/v1",
        "fx_snapshot_id": fx_snapshot_id,
    }
