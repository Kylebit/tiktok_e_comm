"""TikTok monthly evidence helpers independent from other platforms."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Mapping


CAMPAIGN_ADVERTISING_SCHEMA = "tiktok-campaign-advertising/v1"


def actual_advertising_from_campaign_snapshot(
    snapshot: Mapping[str, object],
    *,
    start: date,
    end: date,
    site: str,
    fx_rate_cny_per_source: Decimal,
    fx_snapshot_id: str,
) -> dict[str, object]:
    """Validate a redacted campaign export snapshot and convert its spend to CNY."""

    if str(snapshot.get("schema_version") or "") != CAMPAIGN_ADVERTISING_SCHEMA:
        raise ValueError("unsupported TikTok campaign advertising schema")
    if str(snapshot.get("platform") or "").lower() != "tiktok":
        raise ValueError("campaign advertising platform identity must be tiktok")
    expected_site = str(site or "").upper()
    if str(snapshot.get("site") or "").upper() != expected_site:
        raise ValueError("campaign advertising site identity does not match report")
    period = snapshot.get("period")
    if not isinstance(period, Mapping) or (
        str(period.get("start") or "") != start.isoformat()
        or str(period.get("end") or "") != end.isoformat()
    ):
        raise ValueError("campaign advertising period identity does not match report")
    cost = snapshot.get("cost")
    source = snapshot.get("source")
    if not isinstance(cost, Mapping) or not isinstance(source, Mapping):
        raise ValueError("campaign advertising cost and source metadata are required")
    try:
        amount = Decimal(str(cost.get("amount")))
        rate = Decimal(str(fx_rate_cny_per_source))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("campaign advertising amount and FX rate must be numeric") from None
    currency = str(cost.get("currency") or "").upper()
    label = str(source.get("label") or "").strip()
    source_digest = str(source.get("file_sha256") or "").lower()
    as_of = str(snapshot.get("as_of") or "").strip()
    if amount < 0 or rate <= 0 or not currency or not label or not as_of or not fx_snapshot_id:
        raise ValueError("campaign advertising audit metadata is incomplete")
    if len(source_digest) != 64 or any(character not in "0123456789abcdef" for character in source_digest):
        raise ValueError("campaign advertising source file digest must be SHA-256")
    try:
        datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("campaign advertising as_of must be an ISO datetime") from None
    canonical_payload = dict(snapshot)
    canonical_payload.pop("snapshot_id", None)
    canonical = json.dumps(
        canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    derived_snapshot_id = f"tiktok-campaign-actual-ads:{digest}"
    supplied_snapshot_id = str(snapshot.get("snapshot_id") or "")
    if supplied_snapshot_id and supplied_snapshot_id != derived_snapshot_id:
        raise ValueError("campaign advertising snapshot_id does not match its content")
    metrics = snapshot.get("metrics")
    return {
        "total_cny": amount * rate,
        "total_source": amount,
        "currency": currency,
        "source": label,
        "as_of": as_of,
        "snapshot_id": derived_snapshot_id,
        "source_file_sha256": source_digest,
        "source_metrics": dict(metrics) if isinstance(metrics, Mapping) else {},
        "allocation_policy": "buyer-paid-gmv-share/v1",
        "fx_snapshot_id": fx_snapshot_id,
    }


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
