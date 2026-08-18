"""Generate a read-only latest-week bundle from existing local snapshots."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re

from domains.data_operations.local_snapshot_adapter import adapt_local_profit_snapshots
from domains.data_operations.profit_settlement.audit import audit_profit_report
from domains.data_operations.profit_settlement.local_catalog import enrich_settlement_row, load_local_catalog
from domains.data_operations.profit_settlement.render import render_profit_report_html
from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ad-rate", default="0.22")
    args = parser.parse_args()

    catalog = load_local_catalog(args.root / "data" / "shop.db")
    paths, coverage_issues = _select_sources(args.root, args.start, args.end)
    fx_payload = _live_fx()
    fx = FxSnapshot.from_mapping(fx_payload["rates"], source=fx_payload["provider"], as_of=fx_payload["as_of"])
    costs = CostSnapshot.from_mapping(
        catalog.costs_by_sku,
        snapshot_id=catalog.snapshot_id,
        default_version=catalog.snapshot_id,
        default_effective_at=catalog.effective_at,
        source="shop.db:sku_costs:mode=ro",
    )
    bundle: dict = {
        "schema_version": "profit-weekly-bundle/v1",
        "period": {"start": args.start.isoformat(), "end": args.end.isoformat(), "timezone": "platform settlement local date"},
        "advertising": {"tiktok": {"mode": "estimated_rate", "rate": args.ad_rate}, "shopee": {"mode": "estimated_rate", "rate": args.ad_rate}, "ozon": {"mode": "actual_required"}},
        "fx": fx.payload(), "cost_snapshot": costs.payload(), "reports": {},
        "source_quality_issues": [*coverage_issues, *(_issue_payload(i) for i in catalog.issues)],
        "external_reads": ["shop.db (SQLite mode=ro)", "local settlement snapshots", fx_payload["provider"]],
        "external_writes": [], "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    for platform in ("tiktok", "shopee"):
        source_paths = paths.get(platform, [])
        adaptation = adapt_local_profit_snapshots(
            source_paths, costs_by_sku=catalog.costs_by_sku,
            seller_sku_by_platform_sku=catalog.seller_sku_by_platform_sku,
            reporting_period=(args.start, args.end),
        )
        adapted_rows = [enrich_settlement_row(row, catalog) for row in adaptation.rows]
        rows, duplicate_count = _deduplicate(adapted_rows)
        if platform == "tiktok":
            from domains.data_operations.profit_settlement.tiktok import build_weekly_report
        else:
            from domains.data_operations.profit_settlement.shopee import build_weekly_report
        report = build_weekly_report(rows, period_start=args.start, period_end=args.end, costs=costs, fx=fx, ad_rate=Decimal(args.ad_rate), code_version="profit-settlement-v1")
        payload = report.payload()
        audit = audit_profit_report(payload).payload()
        bundle["reports"][platform] = {
            "report": payload, "audit_round_1": audit,
            "audit_round_2": audit_profit_report(payload).payload(),
            "adapter": {
                "snapshot_id": adaptation.snapshot_id,
                "source_files": list(adaptation.source_files),
                "row_counts": adaptation.payload()["row_counts"],
                "deduplicated_row_count": len(rows),
                "duplicate_row_count": duplicate_count,
                "quality_issue_counts": dict(sorted(Counter(i.code for i in adaptation.issues).items())),
            },
        }
        bundle["source_quality_issues"].extend(
            _issue_payload(i, platform=platform) for i in adaptation.issues
            if i.code != "out_of_reporting_period"
        )
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / f"{platform}_{args.start}_{args.end}.html").write_text(render_profit_report_html(payload), encoding="utf-8")

    bundle["reports"]["ozon"] = {"status": "not_available", "reason": "No governed settled-order Ozon snapshot was found; V1 requires actual order advertising evidence."}
    bundle["source_quality_issues"].append({"code": "missing_ozon_settlement_source", "platform": "ozon", "field": "settlement", "message": bundle["reports"]["ozon"]["reason"]})
    all_audits_pass = all(item.get("audit_round_2", {}).get("status") == "PASSED" for item in bundle["reports"].values())
    bundle["status"] = "ready" if all_audits_pass and not bundle["source_quality_issues"] else "needs_review"
    (args.output / f"weekly_profit_{args.start}_{args.end}.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": bundle["status"], "output": str(args.output), "source_quality_issue_counts": dict(sorted(Counter(i["code"] for i in bundle["source_quality_issues"]).items())), "report_statuses": {p: r.get("report", {}).get("status", r.get("status")) for p, r in bundle["reports"].items()}}, ensure_ascii=False, indent=2))
    return 0 if bundle["status"] == "ready" else 2


def _select_sources(root: Path, start: date, end: date):
    income = [p for p in (root / "CURSOR" / "Income_Data").glob("income_TH_*.csv") if "manual" not in p.name.lower() and "probe" not in p.name.lower()]
    shopee = list((root / "outputs").glob("weekly_shopee_profit_*.html"))
    selected = {"tiktok": [_latest(income)], "shopee": [_latest(shopee)]}
    issues = []
    for platform, paths in selected.items():
        if paths[0] is None:
            selected[platform] = []
            issues.append({"code": "missing_settlement_source", "platform": platform, "field": "source", "message": "No supported local settlement snapshot was found"})
        else:
            coverage = _filename_period(paths[0].name)
            if coverage and not (coverage[0] <= start and coverage[1] >= end):
                issues.append({"code": "incomplete_source_coverage", "platform": platform, "field": "source_period", "message": f"Selected snapshot covers {coverage[0]} through {coverage[1]}, not the complete requested period {start} through {end}"})
    return selected, issues


def _latest(paths):
    return max(paths, key=lambda p: (p.stat().st_mtime_ns, p.name)) if paths else None


def _filename_period(name):
    match = re.search(r"income_[A-Z]{2}_(\d{6})_(\d{6})", name, re.IGNORECASE)
    if match:
        return tuple(datetime.strptime(value, "%y%m%d").date() for value in match.groups())
    match = re.search(r"weekly_shopee_profit_(\d{8})_(\d{8})", name, re.IGNORECASE)
    if match:
        return tuple(datetime.strptime(value, "%Y%m%d").date() for value in match.groups())
    return None


def _deduplicate(rows):
    kept = {}
    for row in rows:
        key = (
            str(row.get("platform") or row.get("channel") or ""),
            str(row.get("region") or ""),
            str(row.get("order_line_id") or ""),
        )
        canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        previous = kept.get(key)
        if previous is None or canonical > previous[0]:
            kept[key] = (canonical, row)
    output = [item[1] for _, item in sorted(kept.items())]
    return output, len(rows) - len(output)


def _live_fx():
    from modules.sourcing.fx_rates import _fetch_fawaz_jsdelivr, _fetch_open_er_api
    errors = []
    for fetcher in (_fetch_open_er_api, _fetch_fawaz_jsdelivr):
        try:
            return fetcher()
        except Exception as exc:  # the report must not fall back to invented FX
            errors.append(f"{fetcher.__name__}: {type(exc).__name__}: {exc}")
    raise RuntimeError("all live FX reads failed; " + "; ".join(errors))


def _issue_payload(issue, *, platform="catalog"):
    return {"code": issue.code, "platform": platform, "record_id": issue.record_id, "field": issue.field, "message": issue.message}


if __name__ == "__main__":
    raise SystemExit(main())
