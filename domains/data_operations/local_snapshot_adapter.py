"""Read-only adapters for local TikTok income and Shopee weekly snapshots."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from typing import Any

from domains.data_operations.financial_facts import DataQualityIssue


_CURRENCY_BY_REGION = {"TH": "THB", "MY": "MYR", "PH": "PHP", "VN": "VND"}
_TIKTOK_REGION = re.compile(r"income_([A-Z]{2})_", re.IGNORECASE)
_SHOPEE_DATA = re.compile(r"const\s+DATA\s*=\s*(\{.*?\})\s*;", re.DOTALL)


@dataclass(frozen=True)
class LocalSnapshotAdaptation:
    rows: tuple[Mapping[str, Any], ...]
    issues: tuple[DataQualityIssue, ...]
    snapshot_id: str
    checksum: str
    source_files: tuple[Mapping[str, Any], ...]
    raw_row_count: int
    normalized_row_count: int
    rejected_row_count: int

    def payload(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id, "checksum": self.checksum,
            "source_files": [dict(item) for item in self.source_files],
            "row_counts": {"raw": self.raw_row_count, "normalized": self.normalized_row_count, "rejected": self.rejected_row_count},
            "rows": [_json_ready(row) for row in self.rows],
            "issues": [_json_ready(issue) for issue in self.issues],
        }


def adapt_local_profit_snapshots(
    paths: Iterable[str | Path], *, costs_by_sku: Mapping[str, object] | None = None,
    seller_sku_by_platform_sku: Mapping[str, str] | None = None,
    reporting_period: tuple[date, date] | None = None,
) -> LocalSnapshotAdaptation:
    """Read explicit local snapshot files without writing or accessing a database.

    Supported inputs are TikTok ``income_XX_*.csv`` and Shopee
    ``weekly_shopee_profit_*.html`` snapshots.  Overlapping rows are retained;
    ``source_updated_at`` gives the digest a deterministic latest-row choice.
    """
    all_rows: list[Mapping[str, Any]] = []
    issues: list[DataQualityIssue] = []
    sources: list[Mapping[str, Any]] = []
    raw = normalized = rejected = 0
    for value in paths:
        path = Path(value)
        if not path.is_file():
            issues.append(_issue("missing_source_file", "snapshot", str(path), "path"))
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        result = adapt_profit_snapshot_text(text, source_name=path.name, source_updated_at=updated, costs_by_sku=costs_by_sku, seller_sku_by_platform_sku=seller_sku_by_platform_sku, reporting_period=reporting_period)
        all_rows.extend(result.rows); issues.extend(result.issues)
        raw += result.raw_row_count; normalized += result.normalized_row_count; rejected += result.rejected_row_count
        sources.append({"path": str(path), "name": path.name, "checksum": _checksum(text), "raw_row_count": result.raw_row_count, "normalized_row_count": result.normalized_row_count, "rejected_row_count": result.rejected_row_count})
    # Identity is content-based; display paths and filesystem mtimes are not
    # portable snapshot inputs (though mtime remains on each row for dedupe).
    checksum = _checksum({"sources": [{"name": item["name"], "checksum": item["checksum"]} for item in sources]})
    return LocalSnapshotAdaptation(tuple(all_rows), tuple(issues), f"local-snapshot:{checksum}", checksum, tuple(sources), raw, normalized, rejected)


def discover_local_profit_snapshots(
    directories: Iterable[str | Path], *, costs_by_sku: Mapping[str, object] | None = None,
    seller_sku_by_platform_sku: Mapping[str, str] | None = None,
    reporting_period: tuple[date, date] | None = None,
) -> LocalSnapshotAdaptation:
    """Discover only supported snapshot names in caller-supplied directories."""
    paths: list[Path] = []
    for directory in directories:
        root = Path(directory)
        if root.is_dir():
            paths.extend(sorted(root.glob("income_TH_*.csv")))
            paths.extend(sorted(root.glob("weekly_shopee_profit_*.html")))
    return adapt_local_profit_snapshots(paths, costs_by_sku=costs_by_sku, seller_sku_by_platform_sku=seller_sku_by_platform_sku, reporting_period=reporting_period)


def adapt_profit_snapshot_text(
    text: str, *, source_name: str, source_updated_at: str,
    costs_by_sku: Mapping[str, object] | None = None, seller_sku_by_platform_sku: Mapping[str, str] | None = None,
    reporting_period: tuple[date, date] | None = None,
) -> LocalSnapshotAdaptation:
    """Adapt supplied snapshot text; useful for fixtures and non-file callers."""
    if source_name.lower().endswith(".csv") and _TIKTOK_REGION.search(source_name):
        rows, issues, raw, rejected = _tiktok_rows(text, source_name, source_updated_at, costs_by_sku or {}, seller_sku_by_platform_sku or {}, reporting_period)
    elif source_name.lower().endswith(".html") and source_name.startswith("weekly_shopee_profit_"):
        rows, issues, raw, rejected = _shopee_rows(text, source_name, source_updated_at, reporting_period)
    else:
        rows, raw, rejected = [], 0, 0
        issues = [_issue("unsupported_snapshot", "snapshot", source_name, "source_name")]
    checksum = _checksum({"source_name": source_name, "text": text})
    return LocalSnapshotAdaptation(tuple(rows), tuple(issues), f"local-snapshot:{checksum}", checksum, (), raw, len(rows), rejected)


def _tiktok_rows(text: str, source: str, updated: str, costs: Mapping[str, object], sku_map: Mapping[str, str], period: tuple[date, date] | None):
    region = _TIKTOK_REGION.search(source).group(1).upper()
    currency = _CURRENCY_BY_REGION.get(region, "")
    rows: list[Mapping[str, Any]] = []; issues: list[DataQualityIssue] = []; raw = rejected = 0
    for index, item in enumerate(csv.DictReader(io.StringIO(text))):
        raw += 1
        kind = (item.get("Type ") or item.get("Type") or "").strip()
        if kind != "Order":
            rejected += 1; continue
        order = _text(item.get("Order/adjustment ID  ") or item.get("Order/adjustment ID"))
        source_sku = _text(item.get("SKU ID")); sku = _text(sku_map.get(source_sku)); occurred = _normalise_occurred(item.get("Statement Date"))
        amount = _decimal(item.get("Total settlement amount"))
        currency = _text(item.get("Currency")) or currency
        if not source_sku or not sku:
            issues.append(_issue("missing_seller_sku_mapping", "tiktok_income", f"{source}:{index}", "SKU ID")); rejected += 1; continue
        if not _required(order, sku, currency, occurred, amount, issues, source, index):
            rejected += 1; continue
        if not _within(occurred, period):
            rejected += 1; issues.append(_issue("out_of_reporting_period", "tiktok_income", f"{source}:{index}", "Statement Date")); continue
        cost = _decimal(costs.get(sku))
        if cost is None: issues.append(_issue("missing_cost", "tiktok_income", f"{source}:{index}", "SKU ID"))
        rows.append(_row("tiktok", region, order, sku, source_sku, currency, amount, cost, occurred, updated, source))
    return rows, issues, raw, rejected


def _shopee_rows(text: str, source: str, updated: str, period: tuple[date, date] | None):
    match = _SHOPEE_DATA.search(text)
    if not match:
        return [], [_issue("invalid_shopee_snapshot", "shopee_snapshot", source, "const DATA")], 0, 0
    try: data = json.loads(match.group(1))
    except json.JSONDecodeError: return [], [_issue("invalid_shopee_snapshot", "shopee_snapshot", source, "const DATA")], 0, 0
    indexes = {str(item.get("name")): i for i, item in enumerate(data.get("headers") or [])}
    rows: list[Mapping[str, Any]] = []; issues: list[DataQualityIssue] = []; raw = rejected = 0
    for index, item in enumerate(data.get("rows") or []):
        raw += 1; cells = item.get("cells") or []
        value = lambda name: _text(cells[indexes[name]]) if name in indexes and indexes[name] < len(cells) else ""
        order, source_sku = value("Order SN"), value("SKU"); sku = _normalise_shopee_sku(source_sku)
        region = _text(item.get("region")); currency = _text(item.get("currency") or value("Currency"))
        occurred = _normalise_occurred(value("Release Time") or value("Purchase Date"))
        amount = _decimal(item.get("settlement"))
        if not region:
            issues.append(_issue("missing_region", "shopee_snapshot", f"{source}:{index}", "region")); rejected += 1; continue
        if not sku:
            issues.append(_issue("invalid_shopee_seller_sku", "shopee_snapshot", f"{source}:{index}", "SKU")); rejected += 1; continue
        if not _required(order, sku, currency, occurred, amount, issues, source, index):
            rejected += 1; continue
        if not _within(occurred, period):
            rejected += 1; issues.append(_issue("out_of_reporting_period", "shopee_snapshot", f"{source}:{index}", "Release Time")); continue
        cost = _decimal(item.get("product_cost"))
        if cost is None: issues.append(_issue("missing_cost", "shopee_snapshot", f"{source}:{index}", "product_cost"))
        rows.append(_row("shopee", region, order, sku, source_sku, currency, amount, cost, occurred, updated, source))
    return rows, issues, raw, rejected


def _row(channel, region, order, sku, source_sku, currency, amount, cost, occurred, updated, source):
    return {"channel": channel, "region": region, "order_id": order, "sku_id": sku, "source_sku_id": source_sku, "currency": currency.upper(), "settlement_amount": amount, "cost_cny": cost, "occurred_at": occurred, "source_updated_at": updated, "calculation_kind": "realized", "source_file": source}
def _required(order, sku, currency, occurred, amount, issues, source, index):
    missing = [("order_id", order), ("sku_id", sku), ("currency", currency), ("occurred_at", occurred), ("settlement_amount", amount)]
    for field, value in missing:
        if value in (None, ""):
            issues.append(_issue(f"missing_{field}", "snapshot", f"{source}:{index}", field)); return False
    return True
def _within(value, period):
    if period is None: return True
    current = _date_value(value)
    if current is None: return False
    return period[0] <= current <= period[1]
def _normalise_shopee_sku(value):
    raw = _text(value)
    return raw[-4:].zfill(4) if raw.isdigit() else ""
def _normalise_occurred(value):
    raw = _text(value)
    if not raw: return ""
    try: return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        try: return datetime.strptime(raw, "%Y/%m/%d").date().isoformat()
        except ValueError: return ""
def _date_value(value):
    normalized = _normalise_occurred(value)
    try: return date.fromisoformat(normalized[:10]) if normalized else None
    except ValueError: return None
def _decimal(value):
    if value is None or str(value).strip() == "": return None
    try: return Decimal(str(value))
    except (InvalidOperation, ValueError): return None
def _text(value): return str(value).strip() if value is not None else ""
def _checksum(value): return sha256(json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
def _json_ready(value):
    if isinstance(value, Decimal): return str(value)
    if hasattr(value, "__dataclass_fields__"): return {key: _json_ready(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, Mapping): return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)): return [_json_ready(item) for item in value]
    return value
def _issue(code, source, record_id, field): return DataQualityIssue(code, source, record_id, field, f"{source} record {record_id} is missing or invalid {field}")
