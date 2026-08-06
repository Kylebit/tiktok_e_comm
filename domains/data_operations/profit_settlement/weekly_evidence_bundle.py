"""Build an audited weekly bundle from three independent settlement artifacts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from domains.data_operations.profit_settlement.audit import audit_profit_report
from domains.data_operations.profit_settlement.settlement_evidence_adapter import (
    AdaptedSettlementEvidence,
    adapt_settlement_evidence,
)
from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot


def build_weekly_evidence_bundle(
    evidence_by_platform: Mapping[str, Mapping[str, Any]],
    catalog: object,
    *,
    period_start: date | str,
    period_end: date | str,
    costs: CostSnapshot,
    fx: FxSnapshot,
    ad_rate: Decimal | str = Decimal("0.22"),
    seller_sku_by_ozon_sku: Mapping[str, str] | None = None,
    quantity_by_ozon_order_sku: Mapping[str, object] | None = None,
    cost_assumption_warnings: tuple[object, ...] = (),
    generated_at: datetime | None = None,
    code_version: str = "unknown",
) -> dict[str, Any]:
    """Return JSON-ready reports without reading files, databases, or networks."""
    start = period_start if isinstance(period_start, date) else date.fromisoformat(str(period_start))
    end = period_end if isinstance(period_end, date) else date.fromisoformat(str(period_end))
    if end < start:
        raise ValueError("period_end must not precede period_start")
    now = generated_at or datetime.now(timezone.utc)
    reports: dict[str, Any] = {}
    bundle_issues: list[dict[str, str]] = []

    for platform in ("tiktok", "shopee", "ozon"):
        evidence = evidence_by_platform.get(platform)
        if not isinstance(evidence, Mapping):
            bundle_issues.append(_issue("missing_settlement_evidence", platform, "evidence", "No settlement-evidence/v1 artifact was supplied"))
            reports[platform] = {"status": "blocked"}
            continue
        adapted = adapt_settlement_evidence(
            evidence,
            catalog,
            period_kind="weekly",
            seller_sku_by_platform_sku=seller_sku_by_ozon_sku if platform == "ozon" else None,
            quantity_by_order_platform_sku=quantity_by_ozon_order_sku if platform == "ozon" else None,
        )
        bundle_issues.extend(_adapter_issue_payload(platform, issue) for issue in adapted.issues)
        report = _platform_report(
            platform,
            adapted,
            start=start,
            end=end,
            costs=costs,
            fx=fx,
            ad_rate=ad_rate,
            generated_at=now,
            code_version=code_version,
        )
        payload = report.payload()
        used_skus = {
            str(row.get("canonical_sku") or "") for row in adapted.rows
        }
        report_warnings = [
            warning.payload()
            for warning in cost_assumption_warnings
            if str(getattr(warning, "canonical_sku", "")) in used_skus
        ]
        payload["assumption_warnings"] = report_warnings
        first_audit = audit_profit_report(payload).payload()
        second_audit = audit_profit_report(payload).payload()
        reports[platform] = {
            "status": "ready" if adapted.status == "ready" and second_audit["status"] == "PASSED" else "needs_review",
            "adapter": adapted.payload(),
            "report": payload,
            "audit_round_1": first_audit,
            "audit_round_2": second_audit,
        }

    status = "ready" if not bundle_issues and all(item.get("status") == "ready" for item in reports.values()) else "needs_review"
    return {
        "schema_version": "profit-weekly-evidence-bundle/v1",
        "status": status,
        "period": {"start": start.isoformat(), "end": end.isoformat(), "timezone": "platform settlement local date"},
        "advertising": {
            "tiktok": {"mode": "estimated_rate", "rate": str(ad_rate), "basis": "buyer_paid_product_amount"},
            "shopee": {"mode": "estimated_rate", "rate": str(ad_rate), "basis": "buyer_paid_product_amount"},
            "ozon": {"mode": "estimated_rate", "rate": "0.22", "basis": "buyer_paid_product_amount", "policy_version": "ozon-fixed-ad-rate/v1"},
        },
        "cost_snapshot": costs.payload(),
        "fx_snapshot": fx.payload(),
        "reports": reports,
        "quality_issues": bundle_issues,
        "quality_issue_counts": dict(sorted(Counter(item["code"] for item in bundle_issues).items())),
        "assumption_warnings": [warning.payload() for warning in cost_assumption_warnings],
        "generated_at": now.isoformat(),
        "code_version": code_version,
        "external_writes_performed": [],
    }


def _platform_report(platform, adapted, *, start, end, costs, fx, ad_rate, generated_at, code_version):
    arguments = {
        "period_start": start,
        "period_end": end,
        "costs": costs,
        "fx": fx,
        "generated_at": generated_at,
        "code_version": code_version,
    }
    if platform == "tiktok":
        from domains.data_operations.profit_settlement.tiktok import build_weekly_report

        return build_weekly_report(adapted.rows, ad_rate=ad_rate, **arguments)
    if platform == "shopee":
        from domains.data_operations.profit_settlement.shopee import build_weekly_report

        return build_weekly_report(adapted.rows, ad_rate=ad_rate, **arguments)
    from domains.data_operations.profit_settlement.ozon import build_weekly_report

    return build_weekly_report(adapted.rows, **arguments)


def _adapter_issue_payload(platform: str, issue: object) -> dict[str, str]:
    return _issue(
        str(getattr(issue, "code", "adapter_quality_issue")),
        platform,
        str(getattr(issue, "field", "evidence")),
        str(getattr(issue, "message", "Settlement evidence adaptation requires review")),
        record_id=str(getattr(issue, "record_id", "report")),
    )


def _issue(code: str, platform: str, field: str, message: str, *, record_id: str = "report") -> dict[str, str]:
    return {"code": code, "platform": platform, "record_id": record_id, "field": field, "message": message}
