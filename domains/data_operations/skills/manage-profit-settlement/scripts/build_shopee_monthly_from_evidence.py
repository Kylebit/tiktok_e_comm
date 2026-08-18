"""Build a Shopee monthly profit report from reviewed settlement evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "domains" / "data_operations" / "profit_settlement").is_dir():
            return parent
    raise RuntimeError("profit settlement repository root not found")


ROOT = _repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_weekly_from_evidence as weekly_helpers
from domains.data_operations.profit_settlement.audit import audit_profit_report
from domains.data_operations.profit_settlement.cost_policy import resolve_temporary_cost_policy
from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
from domains.data_operations.profit_settlement.render import render_profit_report_html
from domains.data_operations.profit_settlement.settlement_evidence_adapter import (
    adapt_settlement_evidence,
)
from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot
from domains.data_operations.profit_settlement.shopee import (
    ShopeeQualityIssue,
    build_monthly_estimated_report,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--site", required=True)
    parser.add_argument("--ad-rate", required=True)
    parser.add_argument("--local-fulfillment-fee-cny", default="4")
    args = parser.parse_args(argv)

    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    site = str(args.site).upper()
    if evidence.get("platform") != "shopee":
        raise RuntimeError("evidence must be a Shopee settlement-evidence/v1 artifact")
    if site != str(evidence.get("site") or "").upper():
        raise RuntimeError("evidence and requested Shopee site identity must match")
    if evidence.get("status") != "ready":
        raise RuntimeError("Shopee settlement evidence must be ready before profit calculation")

    catalog = load_local_catalog(args.project_root / "data" / "shop.db")
    adapted = adapt_settlement_evidence(evidence, catalog, period_kind="monthly")
    required_skus = {str(row.get("canonical_sku") or "") for row in adapted.rows}
    cost_policy = resolve_temporary_cost_policy(catalog, required_skus)
    resolved_catalog = replace(
        catalog,
        costs_by_sku={
            sku: Decimal(str(value["unit_cost_cny"]))
            for sku, value in cost_policy.values.items()
        },
    )
    adapted = adapt_settlement_evidence(
        evidence,
        resolved_catalog,
        period_kind="monthly",
    )
    costs = CostSnapshot.from_mapping(cost_policy.values)
    live_fx = weekly_helpers._live_fx()
    fx = FxSnapshot.from_mapping(
        live_fx["rates"],
        source=live_fx["provider"],
        as_of=live_fx["as_of"],
    )
    generated_at = datetime.now(timezone.utc)
    report = build_monthly_estimated_report(
        adapted.rows,
        period_start=args.start,
        period_end=args.end,
        costs=costs,
        fx=fx,
        ad_rate=args.ad_rate,
        ad_rate_source="operator_monthly_override",
        local_fulfillment_fee_cny=args.local_fulfillment_fee_cny,
        generated_at=generated_at,
        code_version="profit-settlement-v1-shopee-monthly-estimated-ads",
    )
    adapter_issues = tuple(
        ShopeeQualityIssue(issue.code, issue.record_id, issue.field, issue.message)
        for issue in adapted.issues
    )
    if adapter_issues:
        report = replace(
            report,
            status="needs_review",
            quality_issues=report.quality_issues + adapter_issues,
        )

    payload = report.payload()
    payload["assumption_warnings"] = [
        {
            "code": warning.code,
            "canonical_sku": warning.canonical_sku,
            "message": warning.message,
            "policy_version": warning.policy_version,
        }
        for warning in cost_policy.warnings
    ]
    payload["source"]["evidence_reconciliation"] = adapted.payload()["reconciliation"]
    payload["source"]["settlement_evidence_snapshot_id"] = evidence.get("snapshot_id")
    payload["source"]["actual_advertising_usage"] = (
        "not_provided_operator_approved_estimate_used"
    )
    payload["source"]["external_reads"] = [
        "settlement-evidence/v1 JSON artifact",
        "shop.db via SQLite mode=ro",
        live_fx["provider"],
    ]
    payload["source"]["external_writes_performed"] = []
    payload["assumptions"] = {
        "advertising": {
            "mode": "estimated_rate",
            "rate": str(Decimal(args.ad_rate)),
            "basis": "buyer_cash_paid_product_amount",
            "source": "operator_monthly_override",
            "actual_advertising_pending": True,
        },
        "local_fulfillment_fee_cny_per_order": str(
            Decimal(args.local_fulfillment_fee_cny)
        ),
    }
    first_audit = audit_profit_report(payload).payload()
    second_audit = audit_profit_report(payload).payload()
    payload["audit_passes"] = [first_audit, second_audit]
    if any(item["status"] != "PASSED" for item in payload["audit_passes"]):
        payload["status"] = "needs_review"

    args.output.mkdir(parents=True, exist_ok=True)
    stem = f"shopee_{site}_{args.start}_{args.end}.monthly-profit"
    json_path = args.output / f"{stem}.json"
    html_path = args.output / f"{stem}.html"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    html_path.write_text(render_profit_report_html(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "site": site,
                "json": str(json_path),
                "html": str(html_path),
                "order_line_count": len(payload["order_lines"]),
                "quality_issue_counts": dict(
                    sorted(
                        Counter(
                            item["code"] for item in payload["quality_issues"]
                        ).items()
                    )
                ),
                "assumption_warning_counts": dict(
                    sorted(
                        Counter(
                            item["code"] for item in payload["assumption_warnings"]
                        ).items()
                    )
                ),
                "advertising": payload["advertising"],
                "audit_passes": [
                    item["status"] for item in payload["audit_passes"]
                ],
                "external_writes_performed": [],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0 if payload["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
