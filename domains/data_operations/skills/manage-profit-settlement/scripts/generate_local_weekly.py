"""Generate a read-only latest-week bundle from existing local snapshots."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from hashlib import sha256
from pathlib import Path
import re

from domains.data_operations.local_snapshot_adapter import adapt_local_profit_snapshots
from domains.data_operations.profit_settlement.audit import audit_profit_report
from domains.data_operations.profit_settlement.cost_policy import resolve_temporary_cost_policy
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
    parser.add_argument("--timezone", required=True, help="Explicit settlement UTC offset, for example +07:00")
    args = parser.parse_args()
    if args.end < args.start or not re.fullmatch(r"[+-]\d{2}:\d{2}", args.timezone):
        parser.error("an ordered period and explicit UTC offset are required")
    try:
        zone = datetime.fromisoformat("2000-01-01T00:00:00" + args.timezone).tzinfo
    except ValueError:
        parser.error("invalid UTC offset")
    period_start = datetime.combine(args.start, time.min, zone)
    period_end = datetime.combine(args.end + timedelta(days=1), time.min, zone)

    catalog = load_local_catalog(args.root / "data" / "shop.db")
    policy = resolve_temporary_cost_policy(catalog, catalog.product_by_seller_sku,
        period_start=period_start, period_end=period_end)
    values, policy_issues = dict(policy.values), list(policy.issues)
    # Same opt-in boundary as captured profiles; this legacy entry has no opt-in.
    for warning in policy.warnings:
        values.pop(warning.canonical_sku, None)
        policy_issues.append({"code": "cost_assumption_not_selected", "canonical_sku": warning.canonical_sku,
                              "message": "Temporary cost selection is not enabled in this legacy consumer"})
    catalog = replace(catalog, costs_by_sku={sku: Decimal(value["unit_cost_cny"]) for sku, value in values.items()})
    paths, coverage_issues = _select_sources(args.root, args.start, args.end)
    fx_payload = _live_fx()
    fx = FxSnapshot.from_mapping(fx_payload["rates"], source=fx_payload["provider"], as_of=fx_payload["as_of"])
    costs = CostSnapshot.from_mapping(values)
    policy_metadata = {"snapshot_id": policy.snapshot_id, "temporary_assumptions_selected": False,
                       "period_start": period_start.isoformat(), "period_end_exclusive": period_end.isoformat(),
                       "issues": policy_issues}
    bundle: dict = {
        "schema_version": "profit-weekly-bundle/v1",
        "period": {"start": args.start.isoformat(), "end": args.end.isoformat(), "timezone": args.timezone},
        "cost_policy": policy_metadata,
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
            reporting_period=None,  # Select only after converting to the declared offset below.
            catalog=catalog,
        )
        period_rows, excluded_ids = _period_rows(adaptation.rows, period_start, period_end)
        adapted_rows = [enrich_settlement_row(row, catalog) for row in period_rows]
        rows, duplicate_count = _deduplicate(adapted_rows)
        if platform == "tiktok":
            from domains.data_operations.profit_settlement.tiktok import build_weekly_report
        else:
            from domains.data_operations.profit_settlement.shopee import build_weekly_report
        report = build_weekly_report(rows, period_start=args.start, period_end=args.end, costs=costs, fx=fx, ad_rate=Decimal(args.ad_rate), code_version="profit-settlement-v1")
        payload = report.payload()
        used_skus = {str(row.get("canonical_sku") or "") for row in rows}
        used_identities = [row.get("catalog_identity") for row in rows]
        input_issues = [*(_issue_payload(i, platform=platform) for i in adaptation.issues if i.code != "out_of_reporting_period" and i.record_id not in excluded_ids),
            *(_issue_payload(i) for i in catalog.issues if (i.identity and i.identity in used_identities)
              or (not i.identity and i.record_id in used_skus)),
            *(dict(i) for i in policy_issues if i.get("canonical_sku") in used_skus)]
        payload["quality_issues"].extend(input_issues)
        if input_issues:
            payload["status"] = "needs_review"
            payload["result_scope"] = "partial_diagnostic" if payload["order_lines"] else "no_calculated_facts"
        payload["source"]["catalog_snapshot_id"] = catalog.snapshot_id
        payload["source"]["local_snapshot_id"] = adaptation.snapshot_id
        payload["source"]["cost_policy"] = {**policy_metadata, "issues": [dict(i) for i in policy_issues if i.get("canonical_sku") in used_skus]}
        payload["period"]["timezone"] = args.timezone
        engine_key = payload["idempotency_key"]
        digest = sha256(json.dumps({"engine": engine_key, "source": payload["source"], "period": payload["period"], "issues": payload["quality_issues"]}, sort_keys=True, default=str).encode()).hexdigest()
        payload["source"]["engine_idempotency_key"] = engine_key
        payload["report_id"] = f"{platform}-profit-{digest[:16]}"
        payload["idempotency_key"] = f"profit-local-consumer/v2:{digest}"
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
                "out_of_period_row_count": len(excluded_ids),
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


def _period_rows(rows, start, end):
    """Use one explicit interval for source selection and cost applicability.

    Date-only/naive legacy stamps mean local time in the caller's offset;
    aware stamps are converted. Neither machine timezone nor mtime is used.
    """
    selected, excluded = [], set()
    for source in rows:
        row = dict(source)
        for field in ("settled_at", "occurred_at"):
            if row.get(field):
                stamp = datetime.fromisoformat(str(row[field]).replace("Z", "+00:00"))
                row[field] = (stamp.replace(tzinfo=start.tzinfo) if stamp.tzinfo is None else stamp.astimezone(start.tzinfo)).isoformat()
        if row.get("settled_at") and not start <= datetime.fromisoformat(row["settled_at"]) < end:
            excluded.add(str(row.get("order_line_id") or row.get("order_id")))
        else:
            selected.append(row)
    return selected, excluded


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
