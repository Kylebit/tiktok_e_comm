"""Build a TikTok created-order monthly profit report from reviewed evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
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
from domains.data_operations.profit_settlement.frozen_monthly_fx import load_frozen_monthly_fx
from domains.data_operations.profit_settlement.monthly_missing_cost_scope import resolve_monthly_missing_cost_policy, monthly_cost_period
from shared_platform.internal_catalog_sku import internal_sku
from domains.data_operations.profit_settlement.audit import audit_profit_report
from domains.data_operations.profit_settlement.cost_policy import resolve_temporary_cost_policy
from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
from domains.data_operations.profit_settlement.render import render_profit_report_html
from domains.data_operations.profit_settlement.settlement_evidence_adapter import adapt_settlement_evidence
from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot
from domains.data_operations.profit_settlement.tiktok import (
    TikTokQualityIssue,
    build_monthly_estimated_report,
    build_monthly_report,
)
from domains.data_operations.profit_settlement.tiktok_monthly import (
    actual_advertising_from_campaign_snapshot,
    actual_advertising_from_finance,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--site")
    parser.add_argument("--fx-input", type=Path)
    parser.add_argument("--artifact-suffix", default="")
    parser.add_argument("--allow-missing-cost-default", action="store_true")
    parser.add_argument("--allow-shared-sku-missing-cost-default", action="store_true")
    parser.add_argument("--local-fulfillment-fee-cny", default="4")
    parser.add_argument(
        "--ad-rate",
        help="explicit monthly estimated advertising fraction; when present, actual Finance ads are retained as reference only",
    )
    parser.add_argument(
        "--actual-advertising-json",
        type=Path,
        help="redacted tiktok-campaign-advertising/v1 export snapshot used as actual monthly advertising spend",
    )
    args = parser.parse_args(argv)
    if args.allow_shared_sku_missing_cost_default and not args.allow_missing_cost_default:
        parser.error("shared SKU policy requires explicit missing-cost authority")
    if args.artifact_suffix and not re.fullmatch(r"[A-Za-z0-9._-]+", args.artifact_suffix):
        parser.error("artifact suffix must be a safe slug")
    if args.ad_rate is not None and args.actual_advertising_json is not None:
        raise RuntimeError("--ad-rate and --actual-advertising-json are mutually exclusive")

    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    site = str(args.site or evidence.get("site") or "").upper()
    if not site or site != str(coverage.get("site") or "").upper():
        raise RuntimeError("evidence and coverage site identity must match")
    _validate_coverage_inputs(evidence, coverage, site, args.start, args.end)
    catalog = load_local_catalog(args.project_root / "data" / "shop.db")
    adapted = adapt_settlement_evidence(evidence, catalog, period_kind="monthly")
    period_order_ids = {str(o.get("order_id") or "") for o in evidence.get("orders") or [] if args.start.isoformat() <= str(o.get("order_created_at") or "")[:10] <= args.end.isoformat()}
    required_skus = {str(row.get("canonical_sku") or "") for row in adapted.rows if str(row.get("order_id") or "") in period_order_ids}
    required_skus.update(internal_sku(i.get("seller_sku")) for o in evidence.get("orders") or [] if str(o.get("order_id") or "") in period_order_ids for i in o.get("items") or [] if isinstance(i, dict))
    required_skus.discard("")
    missing_cost_receipt = None
    period_start, period_end = monthly_cost_period(site, args.start, args.end, coverage['created_period']['timezone'])
    if args.allow_shared_sku_missing_cost_default:
        cost_policy, missing_cost_receipt = resolve_monthly_missing_cost_policy(catalog, evidence, required_skus, site=site, start=args.start, end=args.end, allow_missing_default=args.allow_missing_cost_default, timezone_name=coverage['created_period']['timezone'])
    else:
        cost_policy = resolve_temporary_cost_policy(catalog, required_skus, allow_missing_default=args.allow_missing_cost_default, allow_conflict_high=False, period_start=period_start, period_end=period_end)
    resolved_catalog = replace(
        catalog,
        costs_by_sku={
            sku: Decimal(str(value["unit_cost_cny"]))
            for sku, value in cost_policy.values.items()
        },
    )
    adapted = adapt_settlement_evidence(evidence, resolved_catalog, period_kind="monthly")
    costs = CostSnapshot.from_mapping(cost_policy.values)
    if args.fx_input:
        monthly_fx, fx_input_sha256 = load_frozen_monthly_fx(args.fx_input, args.start, args.end)
        fx = monthly_fx.snapshot
        live_fx = {"provider": "frozen monthly-average-fx/v1 artifact"}
    else:
        monthly_fx, fx_input_sha256 = None, None
        live_fx = weekly_helpers._live_fx()
        fx = FxSnapshot.from_mapping(live_fx["rates"], source=live_fx["provider"], as_of=live_fx["as_of"])
    currencies = {
        str(row.get("currency") or "").upper()
        for row in evidence.get("orders") or []
        if row.get("currency")
    }
    if len(currencies) != 1:
        raise RuntimeError("TikTok monthly evidence must use one explicit currency")
    local_currency = next(iter(currencies))
    local_rate = fx.get(local_currency)
    if local_rate is None:
        raise RuntimeError(f"live FX snapshot has no {local_currency} rate")
    finance_actual_ads = None
    if args.actual_advertising_json is not None:
        campaign_snapshot = json.loads(
            args.actual_advertising_json.read_text(encoding="utf-8")
        )
        campaign_currency = str(
            (campaign_snapshot.get("cost") or {}).get("currency") or ""
        ).upper()
        campaign_rate = fx.get(campaign_currency)
        if campaign_rate is None:
            raise RuntimeError(
                f"live FX snapshot has no campaign advertising currency {campaign_currency}"
            )
        actual_ads = actual_advertising_from_campaign_snapshot(
            campaign_snapshot,
            start=args.start,
            end=args.end,
            site=site,
            fx_rate_cny_per_source=campaign_rate,
            fx_snapshot_id=fx.snapshot_id,
        )
        try:
            finance_actual_ads = actual_advertising_from_finance(
                evidence,
                start=args.start,
                end=args.end,
                fx_rate_cny_per_local=local_rate,
                fx_snapshot_id=fx.snapshot_id,
            )
        except ValueError:
            finance_actual_ads = None
    else:
        try:
            finance_actual_ads = actual_advertising_from_finance(
                evidence, start=args.start, end=args.end,
                fx_rate_cny_per_local=local_rate, fx_snapshot_id=fx.snapshot_id,
            )
        except ValueError:
            if args.ad_rate is None:
                raise
            finance_actual_ads = None
        actual_ads = finance_actual_ads
    common = {
        "period_start": args.start,
        "period_end": args.end,
        "costs": costs,
        "fx": fx,
        "local_fulfillment_fee_cny": args.local_fulfillment_fee_cny if site in {"TH", "MY", "PH", "VN"} else "0",
        "generated_at": datetime.now(timezone.utc),
    }
    if args.ad_rate is not None:
        report = build_monthly_estimated_report(
            adapted.rows,
            ad_rate=args.ad_rate,
            ad_rate_source="operator_monthly_override",
            code_version="profit-settlement-v1-tiktok-monthly-estimated-ads",
            **common,
        )
    else:
        report = build_monthly_report(
            adapted.rows,
            period_basis="order_created_at",
            actual_advertising=actual_ads,
            code_version="profit-settlement-v1-tiktok-monthly-created-orders",
            **common,
        )
    adapter_issues = tuple(
        TikTokQualityIssue(issue.code, issue.record_id, issue.field, issue.message)
        for issue in adapted.issues
        if str(issue.record_id).split(":", 1)[0] in period_order_ids or issue.code == "missing_order_created_at"
    )
    if adapter_issues:
        report = replace(
            report,
            status="needs_review",
            quality_issues=report.quality_issues + adapter_issues,
        )
    payload = report.payload()
    if site not in {"TH", "MY", "PH", "VN"}:
        _apply_unsupported_site_fulfillment_gate(payload, site)
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
    historical_inputs = {sku: _json_ready(catalog.cost_records_by_sku.get(sku, ())) for sku in sorted(required_skus)}
    payload['source']['historical_cost_inputs'] = {'mode':'traceable_internal_sku_legacy_observations', 'period_start':period_start.isoformat(), 'period_end_exclusive':period_end.isoformat(), 'historical_cost_confirmed':False, 'candidates_by_internal_sku':historical_inputs, 'conflict_sku_count':sum(i['code']=='conflicting_cost_requires_approval' and i['canonical_sku'] in required_skus for i in cost_policy.issues)}
    for sku, records in historical_inputs.items():
        if sku in cost_policy.values and any(c.get('historical_cost_basis') == 'undated_legacy_observation' for c in records):
            payload['assumption_warnings'].append({'code':'undated_legacy_cost_observation','canonical_sku':sku,'message':'Original undated legacy cost observation used as an explicit unperiodized input; historical applicability is not confirmed.'})
    for item in cost_policy.issues:
        if item['canonical_sku'] in required_skus:
            payload['quality_issues'].append({'code':item['code'],'record_id':item['canonical_sku'],'field':'cost','message':item['message']})
            payload['status'] = 'needs_review'
    _apply_coverage_gate(payload, coverage)
    payload["source"]["actual_advertising"] = _json_ready(actual_ads)
    payload["source"]["finance_actual_advertising_reference"] = _json_ready(
        finance_actual_ads
    )
    payload["source"]["actual_advertising_usage"] = (
        "reference_only_not_used_in_profit"
        if args.ad_rate is not None
        else "used_in_profit"
    )
    payload["source"]["external_reads"] = [
        "settlement-evidence/v1 JSON artifact",
        "tiktok-order-settlement-coverage/v1 JSON artifact",
        "shop.db via SQLite mode=ro",
        live_fx["provider"],
    ]
    if args.actual_advertising_json is not None:
        payload["source"]["external_reads"].append(
            "tiktok-campaign-advertising/v1 JSON artifact"
        )
    if monthly_fx is not None:
        payload["source"]["monthly_average_fx"] = monthly_fx.evidence
        payload["source"]["monthly_average_fx_input_sha256"] = fx_input_sha256
    if missing_cost_receipt is not None:
        payload["source"]["missing_cost_policy_receipt"] = missing_cost_receipt
    payload["source"]["external_writes_performed"] = []
    first_audit = audit_profit_report(payload).payload()
    second_audit = audit_profit_report(payload).payload()
    payload["audit_passes"] = [first_audit, second_audit]
    if any(item["status"] != "PASSED" for item in payload["audit_passes"]):
        payload["status"] = "needs_review"

    args.output.mkdir(parents=True, exist_ok=True)
    stem = f"tiktok_{site}_{args.start}_{args.end}.monthly-profit"
    if args.artifact_suffix:
        stem += "." + args.artifact_suffix
    json_path = args.output / f"{stem}.json"
    html_path = args.output / f"{stem}.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    html_path.write_text(render_profit_report_html(payload), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "json": str(json_path),
        "html": str(html_path),
        "order_line_count": len(payload["order_lines"]),
        "quality_issue_counts": dict(sorted(Counter(item["code"] for item in payload["quality_issues"]).items())),
        "assumption_warning_counts": dict(sorted(Counter(item["code"] for item in payload["assumption_warnings"]).items())),
        "actual_advertising": actual_ads,
        "finance_actual_advertising_reference": finance_actual_ads,
        "audit_passes": [item["status"] for item in payload["audit_passes"]],
        "external_writes_performed": [],
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if payload["status"] == "ready" else 2


def _validate_coverage_inputs(evidence, coverage, site, start, end):
    period = coverage.get("created_period") or {}
    if (coverage.get('date_basis') is not None
            or coverage.get('schema_version') == 'tiktok-order-settlement-coverage/v2'):
        raise ValueError('legacy monthly CLI cannot consume v2 site-local coverage; use the fixed producer')
    _, local_end = monthly_cost_period(site, start, end, period.get('timezone'))
    for order in evidence.get('orders') or []:
        raw = str(order.get('order_created_at') or '')
        stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.date() != stamp.astimezone(local_end.tzinfo).date():
            raise ValueError('legacy monthly CLI needs site-local source timestamps; reconcile as v2')
    expected = (evidence.get("platform"), str(evidence.get("site", "")).upper(), str(evidence.get("shop_id") or ""))
    actual = (coverage.get("platform"), str(coverage.get("site", "")).upper(), str(coverage.get("shop_id") or ""))
    if expected != ("tiktok", site, expected[2]) or not expected[2] or actual != expected:
        raise ValueError("coverage exact platform/site/shop mismatch")
    if period.get("start") != start.isoformat() or period.get("end") != end.isoformat() or not period.get("timezone"):
        raise ValueError("coverage created-period/timezone mismatch")
    if coverage.get("settlement_snapshot_id") != evidence.get("snapshot_id") or not evidence.get("snapshot_id"):
        raise ValueError("coverage not bound to settlement snapshot")
    if not coverage.get("snapshot_id") or not isinstance(coverage.get("counts"), dict) or not isinstance(coverage.get("all_non_cancelled_orders_settled"), bool):
        raise ValueError("created-order coverage completeness unknown")
    counts = coverage["counts"]
    names = ("created_orders", "settled_orders", "cancelled_orders", "cancelled_with_settlement", "cancelled_without_settlement", "unsettled_non_cancelled")
    if any(type(counts.get(key)) is not int or counts[key] < 0 for key in names):
        raise ValueError("coverage counts missing or invalid")
    if counts["created_orders"] != counts["settled_orders"] + counts["cancelled_without_settlement"] + counts["unsettled_non_cancelled"] or counts["cancelled_orders"] != counts["cancelled_with_settlement"] + counts["cancelled_without_settlement"]:
        raise ValueError("coverage parent counts do not reconcile")
    if coverage["all_non_cancelled_orders_settled"] != (counts["unsettled_non_cancelled"] == 0):
        raise ValueError("coverage completion flag contradicts counts")
    receipt = coverage.get("receipt") or {}
    segments = receipt.get("pagination_segment_count")
    terminals = receipt.get("pagination_terminal_page_count")
    invalid_rows = receipt.get("invalid_order_row_count")
    if type(segments) is not int or segments <= 0 or type(terminals) is not int or terminals != segments or type(invalid_rows) is not int or invalid_rows != 0 or receipt.get("pagination_termination") != "empty_next_page_token_per_segment":
        raise ValueError("created-order enumeration is not complete")
    settled = coverage.get("settled_orders") or []
    settled_ids = {str(row.get("order_id") or "") for row in settled}
    if "" in settled_ids or len(settled_ids) != len(settled) or len(settled_ids) != counts["settled_orders"]:
        raise ValueError("coverage settled parent identities invalid")
    official_mappings = {}
    finance_settled_ids = set()
    for order in evidence.get("orders") or []:
        if start.isoformat() <= str(order.get("order_created_at") or "")[:10] <= end.isoformat():
            if not order.get("order_id") or order.get("shop_id") != expected[2] or str(order.get("region", "")).upper() != site:
                raise ValueError("settlement parent order identity mismatch")
            if order.get("settlement_status") == "settled" and order["order_id"] not in settled_ids:
                raise ValueError("settled parent not in created-order coverage")
            if order.get("settlement_status") == "settled":
                finance_settled_ids.add(str(order["order_id"]))
            for item in order.get("items") or []:
                identity = (order["shop_id"], order["order_id"], str(item.get("platform_sku") or ""))
                raw_sku = str(item.get("seller_sku") or "").strip()
                if raw_sku and not identity[2]:
                    raise ValueError("official Seller SKU lacks same-order platform SKU")
                if identity in official_mappings and official_mappings[identity] != raw_sku:
                    raise ValueError("official same-order SKU mapping conflicts across statements")
                official_mappings[identity] = raw_sku
    if finance_settled_ids != settled_ids:
        raise ValueError("coverage settled parents missing from raw Finance evidence: " + ",".join(sorted(settled_ids - finance_settled_ids)))


def _json_ready(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _apply_coverage_gate(payload, coverage):
    counts = dict(coverage.get("counts") or {})
    snapshot_id = str(coverage.get("snapshot_id") or "")
    unsettled_non_cancelled = int(counts.get("unsettled_non_cancelled") or 0)
    coverage_status = str(coverage.get("status") or "unknown")
    all_non_cancelled_settled = coverage.get("all_non_cancelled_orders_settled") is True
    payload["source"]["order_coverage"] = counts
    payload["source"]["coverage_snapshot_id"] = snapshot_id
    payload["source"]["coverage_status"] = coverage_status
    payload["source"]["settlement_observed_through"] = coverage.get("settlement_observed_through")
    payload["source"]["all_non_cancelled_orders_settled"] = all_non_cancelled_settled
    if unsettled_non_cancelled or not all_non_cancelled_settled:
        payload["quality_issues"].append({
            "code": "unsettled_non_cancelled_orders",
            "record_id": snapshot_id or "coverage:unknown",
            "field": "coverage.all_non_cancelled_orders_settled",
            "message": (
                f"{unsettled_non_cancelled} non-cancelled order(s) created in the reporting month "
                "have no Finance settlement evidence through the declared coverage as-of time"
            ),
        })
        payload["status"] = "needs_review"
    fingerprint = sha256(json.dumps({
        "base_idempotency_key": payload.get("idempotency_key"),
        "coverage_snapshot_id": snapshot_id,
        "coverage_status": coverage_status,
        "settlement_observed_through": coverage.get("settlement_observed_through"),
        "counts": counts,
        "all_non_cancelled_orders_settled": all_non_cancelled_settled,
        "site": next((
            str(line.get("identity", {}).get("region") or "").upper()
            for line in payload.get("order_lines") or []
            if line.get("identity", {}).get("region")
        ), ""),
        "fulfillment_policy": payload.get("source", {}).get("fulfillment_policy"),
        "quality_issue_codes": sorted(str(item.get("code") or "") for item in payload.get("quality_issues") or []),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
    payload["idempotency_key"] = f"profit-report/tiktok-monthly-coverage/v1:{fingerprint}"
    payload["report_id"] = f"tiktok-profit:{fingerprint[:16]}"


def _apply_unsupported_site_fulfillment_gate(payload, site):
    rule = f"unsupported_tiktok_{site.lower()}_fulfillment_rule/v1"
    parent_orders = set()
    for line in payload.get("order_lines") or []:
        identity = line.get("identity") or {}
        parent_orders.add(str(identity.get("order_id") or ""))
        fulfillment = line.get("fulfillment") or {}
        fulfillment["mode"] = "unknown"
        fulfillment["classification_rule"] = rule
        fulfillment["local_fulfillment_cost_cny"] = "0"
        fulfillment["allocation_method"] = "not_applicable"
        fulfillment.pop("order_cost_policy", None)
    policy = {
        "local_fulfillment_fee_cny_per_order": "0",
        "cost_components": [],
        "classification_rule": rule,
    }
    payload["source"]["fulfillment_order_counts"] = {
        "cross_border": 0,
        "local": 0,
        "unknown": len(parent_orders - {""}),
    }
    payload["source"]["local_fulfillment_charged_order_count"] = 0
    payload["source"]["fulfillment_policy"] = policy
    payload["assumptions"]["fulfillment_policy"] = policy
    payload["totals"]["local_fulfillment_cost_cny"] = "0"
    payload["quality_issues"].append({
        "code": "unsupported_tiktok_site_fulfillment_rule",
        "record_id": f"tiktok:{site}",
        "field": "fulfillment.classification_rule",
        "message": (
            f"TikTok {site} has no operator-approved local/cross-border classification rule; "
            "no local-fulfillment cost was recognized"
        ),
    })
    payload["status"] = "needs_review"


if __name__ == "__main__":
    raise SystemExit(main())
