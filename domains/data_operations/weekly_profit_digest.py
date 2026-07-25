"""Pure, audit-friendly weekly profit digest builder.

The builder consumes supplied normalized rows only.  It performs no database
access, file I/O, scheduling, or notification.  A caller can therefore test a
report run from an immutable input snapshot before deciding how to persist or
deliver it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CALCULATION_KIND = "weekly_profit_digest"


@dataclass(frozen=True)
class ReportingPeriod:
    start: datetime
    end: datetime
    timezone_name: str

    def payload(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "timezone": self.timezone_name}


@dataclass(frozen=True)
class ReportQualityIssue:
    code: str
    record_id: str
    field: str
    message: str


@dataclass(frozen=True)
class ReportRun:
    run_id: str
    calculation_kind: str
    period: ReportingPeriod
    input_snapshot: Mapping[str, Any]
    raw_row_count: int
    deduplicated_row_count: int
    fx: Mapping[str, Any]
    cost: Mapping[str, Any]
    assumptions: Mapping[str, Any]
    code_version: str
    status: str
    idempotency_key: str
    generated_at: datetime
    freshness: Mapping[str, Any]
    quality_issues: tuple[ReportQualityIssue, ...]
    realized_by_sku: tuple[Mapping[str, Any], ...]
    estimate_by_sku: tuple[Mapping[str, Any], ...]
    negative_profit_skus: tuple[Mapping[str, Any], ...]

    def payload(self) -> dict[str, Any]:
        """Return a JSON-ready audit payload, with every amount represented exactly."""
        return {
            "run_id": self.run_id,
            "calculation_kind": self.calculation_kind,
            "period": self.period.payload(),
            "input_snapshot": _json_value(self.input_snapshot),
            "row_counts": {"before_deduplication": self.raw_row_count, "after_deduplication": self.deduplicated_row_count},
            "fx": _json_value(self.fx),
            "cost": _json_value(self.cost),
            "assumptions": _json_value(self.assumptions),
            "code_version": self.code_version,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "generated_at": self.generated_at.isoformat(),
            "freshness": _json_value(self.freshness),
            "quality_issues": [_json_value(issue) for issue in self.quality_issues],
            "realized_by_sku": _json_value(self.realized_by_sku),
            "estimate_by_sku": _json_value(self.estimate_by_sku),
            "negative_profit_skus": _json_value(self.negative_profit_skus),
        }


def build_weekly_profit_digest(
    rows: Iterable[Mapping[str, object]],
    *,
    period_start: date | datetime | str,
    period_end: date | datetime | str,
    timezone_name: str = "Asia/Shanghai",
    fx_rates_cny: Mapping[str, object] | None = None,
    cost_version: str = "unspecified",
    fx_source: str = "caller_supplied",
    fx_as_of: date | datetime | str | None = None,
    assumptions: Mapping[str, object] | None = None,
    code_version: str = "unknown",
    generated_at: datetime | None = None,
    freshness_threshold: timedelta = timedelta(days=8),
) -> ReportRun:
    """Build a weekly operational snapshot from normalized input rows.

    Each row needs a ``sku_id`` and either ``settlement_amount`` or
    ``settlement_local``. ``calculation_kind`` is ``realized`` or ``estimate``
    (default: realized). Costs are CNY (``cost_cny``/``product_cost``), and FX
    maps local currency to CNY. Invalid rows stay visible as quality issues;
    they are never silently converted into zero-profit results.
    """
    zone = _timezone(timezone_name)
    period = _period(period_start, period_end, zone, timezone_name)
    now = _as_datetime(generated_at or datetime.now(timezone.utc), zone)
    source_rows = [dict(row) for row in rows]
    snapshot_checksum = _checksum(source_rows)
    issues: list[ReportQualityIssue] = []
    deduped = _deduplicate(source_rows, issues)
    rates = {str(currency).upper(): _decimal(rate) for currency, rate in (fx_rates_cny or {}).items()}
    rates["CNY"] = Decimal("1")

    aggregates: dict[str, dict[str, dict[str, Any]]] = {"realized": {}, "estimate": {}}
    observed_at: list[datetime] = []
    for row_index, row in enumerate(deduped):
        record_id = _record_id(row, row_index)
        sku_id = _text(row.get("sku_id") or row.get("seller_sku"))
        if not sku_id:
            issues.append(_issue("missing_sku", record_id, "sku_id"))
            continue
        kind = _text(row.get("calculation_kind") or row.get("kind")).lower() or "realized"
        if kind not in aggregates:
            issues.append(_issue("invalid_calculation_kind", record_id, "calculation_kind"))
            continue
        settlement = _decimal(row.get("settlement_amount", row.get("settlement_local")))
        if settlement is None:
            issues.append(_issue("missing_settlement", record_id, "settlement_amount"))
            continue
        currency = _text(row.get("currency")).upper()
        rate = _decimal(row.get("fx_cny_per_local")) or rates.get(currency)
        if not currency or rate is None or rate <= 0:
            issues.append(_issue("missing_fx", record_id, "currency" if not currency else "fx_cny_per_local"))
            continue
        cost = _decimal(row.get("cost_cny", row.get("product_cost")))
        if cost is None or cost < 0:
            issues.append(_issue("missing_cost", record_id, "cost_cny"))
            continue
        occurred_at = _as_datetime(row.get("occurred_at") or row.get("statement_date") or row.get("release_time"), zone)
        if occurred_at is None:
            issues.append(_issue("missing_occurred_at", record_id, "occurred_at"))
        else:
            observed_at.append(occurred_at)
        ad_cost = _decimal(row.get("ad_cost_cny")) or Decimal("0")
        bucket = aggregates[kind].setdefault(sku_id, {"sku_id": sku_id, "settlement_cny": Decimal("0"), "cost_cny": Decimal("0"), "ad_cost_cny": Decimal("0"), "profit_cny": Decimal("0"), "row_count": 0, "currencies": set()})
        settlement_cny = settlement * rate
        bucket["settlement_cny"] += settlement_cny
        bucket["cost_cny"] += cost
        bucket["ad_cost_cny"] += ad_cost
        bucket["profit_cny"] += settlement_cny - cost - ad_cost
        bucket["row_count"] += 1
        bucket["currencies"].add(currency)

    realized = _summaries(aggregates["realized"])
    estimates = _summaries(aggregates["estimate"])
    negatives = tuple(item for item in (*realized, *estimates) if item["profit_cny"] < Decimal("0"))
    freshness = _freshness(observed_at, now, freshness_threshold)
    if freshness["state"] == "stale":
        issues.append(ReportQualityIssue("stale_data", "report", "occurred_at", "Newest source row is older than the freshness threshold"))
    status = "ready" if not issues else "needs_review"
    fingerprint = _checksum({"period": period.payload(), "input": snapshot_checksum, "fx": rates, "cost_version": cost_version, "assumptions": assumptions or {}, "code_version": code_version})
    return ReportRun(
        run_id=f"weekly-profit-{fingerprint[:16]}", calculation_kind=CALCULATION_KIND, period=period,
        input_snapshot={"checksum": snapshot_checksum, "row_count": len(source_rows)}, raw_row_count=len(source_rows), deduplicated_row_count=len(deduped),
        fx={"source": fx_source, "as_of": _date_text(fx_as_of), "rates_cny": rates}, cost={"version": cost_version, "currency": "CNY"},
        assumptions=dict(assumptions or {}), code_version=code_version, status=status, idempotency_key=f"{CALCULATION_KIND}:{fingerprint}",
        generated_at=now, freshness=freshness, quality_issues=tuple(issues), realized_by_sku=realized, estimate_by_sku=estimates, negative_profit_skus=negatives,
    )


def _deduplicate(rows: list[dict[str, object]], issues: list[ReportQualityIssue]) -> list[dict[str, object]]:
    kept: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for index, row in enumerate(rows):
        sku = _text(row.get("sku_id") or row.get("seller_sku"))
        order = _text(row.get("order_id") or row.get("order_sn") or row.get("statement_id"))
        if not sku or not order:
            issues.append(_issue("missing_deduplication_key", _record_id(row, index), "order_id"))
            kept[("__row__", str(index), "", "")] = row
            continue
        key = (_text(row.get("channel") or row.get("platform")).lower(), _text(row.get("region")).upper(), order, sku)
        previous = kept.get(key)
        if previous is None or _sort_time(row) >= _sort_time(previous):
            kept[key] = row
    return list(kept.values())


def _summaries(buckets: Mapping[str, Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple({**item, "currencies": tuple(sorted(item["currencies"]))} for _, item in sorted(buckets.items()))


def _freshness(observed: list[datetime], now: datetime, threshold: timedelta) -> dict[str, Any]:
    newest = max(observed) if observed else None
    age = now - newest if newest else None
    return {"newest_occurred_at": newest.isoformat() if newest else None, "age_seconds": int(age.total_seconds()) if age else None, "threshold_seconds": int(threshold.total_seconds()), "state": "fresh" if age is not None and age <= threshold else "stale"}


def _period(start: date | datetime | str, end: date | datetime | str, zone: tzinfo, timezone_name: str) -> ReportingPeriod:
    first = _as_datetime(start, zone)
    last = _as_datetime(end, zone)
    if first is None or last is None:
        raise ValueError("period_start and period_end must be ISO dates or datetimes")
    if isinstance(end, (date, str)) and not isinstance(end, datetime) and "T" not in str(end):
        last = datetime.combine(last.date(), time.max, tzinfo=zone)
    if last < first:
        raise ValueError("period_end must not precede period_start")
    return ReportingPeriod(first, last, timezone_name)


def _as_datetime(value: object, zone: tzinfo) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)


def _timezone(timezone_name: str) -> tzinfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name=timezone_name)
        raise ValueError(f"time zone data is unavailable for {timezone_name}")


def _sort_time(row: Mapping[str, object]) -> datetime:
    return _as_datetime(row.get("source_updated_at") or row.get("occurred_at") or row.get("statement_date"), timezone.utc) or datetime.min.replace(tzinfo=timezone.utc)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _checksum(value: object) -> str:
    return sha256(json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _json_value(value: object) -> Any:
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, date): return value.isoformat()
    if isinstance(value, set): return sorted(_json_value(item) for item in value)
    if isinstance(value, Mapping): return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [_json_value(item) for item in value]
    if hasattr(value, "__dataclass_fields__"): return {key: _json_value(getattr(value, key)) for key in value.__dataclass_fields__}
    return value


def _text(value: object) -> str: return str(value).strip() if value is not None else ""
def _record_id(row: Mapping[str, object], index: int) -> str: return _text(row.get("id") or row.get("order_id") or row.get("order_sn") or row.get("statement_id")) or str(index)
def _issue(code: str, record_id: str, field: str) -> ReportQualityIssue: return ReportQualityIssue(code, record_id, field, f"Record {record_id} is missing or invalid {field}")
def _date_text(value: object) -> str | None: return value.isoformat() if isinstance(value, (date, datetime)) else (_text(value) or None)
