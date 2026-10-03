"""Audited local orchestration for the weekly profit digest.

The runner reads existing local snapshots and the catalog database, delegates
all monetary aggregation to data operations, and optionally stores the result
in the local report store.  Dry-run is the default and has no write side effects.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.config import ROOT
from domains.data_operations import (
    DataQualityIssue,
    LocalSnapshotAdaptation,
    ReportRun,
    build_weekly_profit_digest,
    discover_local_profit_snapshots,
)
from shared_platform.report_store import ReportRunStore, StoredReportResult, default_report_store


@dataclass(frozen=True)
class CatalogProfitInputs:
    seller_sku_by_platform_sku: Mapping[str, str]
    costs_by_sku: Mapping[str, Decimal]
    version: str
    issues: tuple[DataQualityIssue, ...]
    source_row_count: int
    catalog: Any = None


@dataclass(frozen=True)
class FxSnapshot:
    rates_cny: Mapping[str, Decimal]
    source: str
    as_of: str | None
    issues: tuple[DataQualityIssue, ...]


@dataclass(frozen=True)
class WeeklyProfitPreview:
    report: ReportRun
    source_adaptation: LocalSnapshotAdaptation

    def summary(self) -> dict[str, Any]:
        payload = self.report.payload()
        return {
            "run_id": self.report.run_id,
            "status": self.report.status,
            "period": payload["period"],
            "source_file_count": len(self.source_adaptation.source_files),
            "source_row_counts": self.source_adaptation.payload()["row_counts"],
            "quality_issue_counts": dict(
                sorted(Counter(issue.code for issue in self.report.quality_issues).items())
            ),
            "realized_sku_buckets": len(self.report.realized_by_sku),
            "negative_profit_sku_buckets": len(self.report.negative_profit_skus),
            "mode": "preliminary_diagnostic",
            "preliminary_profit_cny": None if not self.report.realized_by_sku else str(
                sum(
                    (Decimal(str(item["profit_cny"])) for item in self.report.realized_by_sku),
                    Decimal("0"),
                )
            ),
        }


def previous_complete_week(reference: date | datetime | None = None) -> tuple[date, date]:
    """Return the previous Monday-through-Sunday period in Asia/Shanghai."""
    zone = _shanghai_timezone()
    if isinstance(reference, datetime):
        current = reference.astimezone(zone).date() if reference.tzinfo else reference.date()
    else:
        current = reference or datetime.now(zone).date()
    this_monday = current - timedelta(days=current.weekday())
    return this_monday - timedelta(days=7), this_monday - timedelta(days=1)


def load_catalog_profit_inputs(database_path: str | Path, *, period_start=None, period_end=None) -> CatalogProfitInputs:
    """Compatibility projection of the same I01 reviewed read-only snapshot.

    This preliminary runner does not opt into temporary highest/default costs.
    """
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    from domains.data_operations.profit_settlement.cost_policy import resolve_temporary_cost_policy

    catalog = load_local_catalog(database_path)
    policy = resolve_temporary_cost_policy(catalog, catalog.costs_by_sku,
                                          period_start=period_start, period_end=period_end)
    assumed = {warning.canonical_sku for warning in policy.warnings}
    costs = {sku: Decimal(item["unit_cost_cny"]) for sku, item in policy.values.items() if sku not in assumed}
    issues = [DataQualityIssue(item.code, "catalog", item.record_id, item.field, item.message) for item in catalog.issues]
    issues.extend(DataQualityIssue(item["code"], "catalog", item.get("canonical_sku", "report"), "cost", item["message"]) for item in policy.issues)
    return CatalogProfitInputs(catalog.seller_sku_by_platform_sku, costs,
                               policy.snapshot_id, tuple(issues), len(catalog.review.get("records", ())), catalog)


def load_fx_snapshot(settings_path: str | Path) -> FxSnapshot:
    """Read only the configured FX table; no other settings are exposed."""
    path = Path(settings_path)
    if not path.is_file():
        issue = DataQualityIssue(
            "missing_fx_settings",
            "settings",
            "exchange_rates",
            "path",
            f"FX settings file not found: {path}",
        )
        return FxSnapshot({}, "local_settings.exchange_rates", None, (issue,))
    data = json.loads(path.read_text(encoding="utf-8"))
    source_rates = data.get("exchange_rates") if isinstance(data, Mapping) else {}
    rates: dict[str, Decimal] = {}
    issues: list[DataQualityIssue] = []
    for currency, value in (source_rates or {}).items():
        rate = _decimal(value)
        code = _text(currency).upper()
        if not code or rate is None or rate <= 0:
            issues.append(
                DataQualityIssue(
                    "invalid_fx_rate",
                    "settings",
                    code or "unknown",
                    "exchange_rates",
                    "configured FX rate must be a positive decimal",
                )
            )
            continue
        rates[code] = rate
    as_of = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    return FxSnapshot(rates, "local_settings.exchange_rates", as_of, tuple(issues))


def build_weekly_profit_preview(
    *,
    period_start: date,
    period_end: date,
    root: str | Path = ROOT,
    database_path: str | Path | None = None,
    settings_path: str | Path | None = None,
    generated_at: datetime | None = None,
    code_version: str | None = None,
) -> WeeklyProfitPreview:
    """Build one reproducible report without persisting or notifying."""
    project_root = Path(root)
    catalog = load_catalog_profit_inputs(database_path or project_root / "data" / "shop.db",
        period_start=datetime.combine(period_start, datetime.min.time(), _shanghai_timezone()),
        period_end=datetime.combine(period_end + timedelta(days=1), datetime.min.time(), _shanghai_timezone()))
    fx = load_fx_snapshot(settings_path or project_root / "config" / "settings.json")
    adaptation = discover_local_profit_snapshots(
        [project_root / "CURSOR" / "Income_Data", project_root / "outputs"],
        costs_by_sku=catalog.costs_by_sku,
        seller_sku_by_platform_sku=catalog.seller_sku_by_platform_sku,
        reporting_period=(period_start, period_end),
        catalog=catalog.catalog,
    )
    adapter_issue_counts = Counter(issue.code for issue in adaptation.issues)
    blocking_adapter_issues = [
        issue for issue in adaptation.issues if issue.code != "out_of_reporting_period"
    ]
    source_issues = [
        *_condense_issues(blocking_adapter_issues),
        *catalog.issues,
        *fx.issues,
        DataQualityIssue(
            "missing_ad_spend",
            "weekly_profit_runner",
            "report",
            "ad_cost_cny",
            "No governed advertising-spend snapshot is attached; profit is preliminary.",
        ),
    ]
    now = generated_at or datetime.now(timezone.utc)
    if period_end >= now.astimezone(_shanghai_timezone()).date():
        source_issues.append(
            DataQualityIssue(
                "incomplete_reporting_period",
                "weekly_profit_runner",
                "report",
                "period_end",
                "The requested reporting period has not fully closed in Asia/Shanghai.",
            )
        )

    source_metadata = {
        "source_files": [
            {
                "name": item["name"],
                "checksum": item["checksum"],
                "raw_row_count": item["raw_row_count"],
                "normalized_row_count": item["normalized_row_count"],
                "rejected_row_count": item["rejected_row_count"],
            }
            for item in adaptation.source_files
        ],
        "adapter_row_counts": {
            "raw": adaptation.raw_row_count,
            "normalized": adaptation.normalized_row_count,
            "rejected": adaptation.rejected_row_count,
        },
        "adapter_issue_counts": dict(sorted(adapter_issue_counts.items())),
        "catalog": {
            "version": catalog.version,
            "source_row_count": catalog.source_row_count,
            "platform_sku_mapping_count": len(catalog.seller_sku_by_platform_sku),
            "cost_count": len(catalog.costs_by_sku),
        },
    }
    report = build_weekly_profit_digest(
        adaptation.rows,
        period_start=period_start,
        period_end=period_end,
        timezone_name="Asia/Shanghai",
        fx_rates_cny=fx.rates_cny,
        cost_version=catalog.version,
        fx_source=fx.source,
        fx_as_of=fx.as_of,
        snapshot_id=adaptation.snapshot_id,
        input_snapshot_metadata=source_metadata,
        assumptions={
            "version": "weekly-realized-profit-v1",
            "settlement_basis": "platform_net_settlement",
            "cost_basis": "catalog_unit_cost_cny_x_source_quantity",
            "advertising_basis": "not_attached_preliminary_only",
            "deduplication_key": "kind+channel+region+order_id+seller_sku",
        },
        upstream_source_quality_issues=source_issues,
        code_version=code_version or _git_revision(project_root),
        generated_at=now,
    )
    return WeeklyProfitPreview(report, adaptation)


def persist_weekly_profit_preview(
    preview: WeeklyProfitPreview,
    *,
    store: ReportRunStore | None = None,
) -> StoredReportResult:
    """Explicitly persist a preview to the local Orbit report store."""
    return (store or default_report_store()).store_report_run(preview.report.payload())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the audited weekly profit digest; dry-run unless --persist-local is set."
    )
    parser.add_argument("--start", help="report start date, YYYY-MM-DD")
    parser.add_argument("--end", help="report end date, YYYY-MM-DD")
    parser.add_argument(
        "--persist-local",
        action="store_true",
        help="store the report in the local report store",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be supplied together")
    if args.start:
        try:
            start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        start, end = previous_complete_week()

    preview = build_weekly_profit_preview(
        period_start=start,
        period_end=end,
        root=args.root,
    )
    result = None
    if args.persist_local:
        result = persist_weekly_profit_preview(preview)
    summary = preview.summary()
    summary["persisted_local"] = bool(result)
    if result:
        summary["report_created"] = result.report_created
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _condense_issues(issues: Iterable[DataQualityIssue]) -> list[DataQualityIssue]:
    grouped: dict[tuple[str, str, str], list[DataQualityIssue]] = defaultdict(list)
    for issue in issues:
        grouped[(issue.code, issue.source, issue.field)].append(issue)
    return [
        DataQualityIssue(
            code,
            source,
            f"{len(values)} record(s)",
            field,
            f"{len(values)} upstream source issue(s); first record: {values[0].record_id}",
        )
        for (code, source, field), values in sorted(grouped.items())
    ]


def _git_revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _shanghai_timezone():
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or _text(value) == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _checksum(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


if __name__ == "__main__":
    raise SystemExit(main())
