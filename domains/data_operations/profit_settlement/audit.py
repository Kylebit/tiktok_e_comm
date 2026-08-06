"""Independent second-pass arithmetic and inclusion audit for report JSON."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any


@dataclass(frozen=True)
class AuditFinding:
    code: str
    record_id: str
    message: str


@dataclass(frozen=True)
class ProfitReportAudit:
    status: str
    report_id: str
    report_digest: str
    checked_order_line_count: int
    findings: tuple[AuditFinding, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "profit-report-audit/v1",
            "status": self.status,
            "report_id": self.report_id,
            "report_digest": self.report_digest,
            "checked_order_line_count": self.checked_order_line_count,
            "findings": [finding.__dict__ for finding in self.findings],
        }


def audit_profit_report(report: Mapping[str, Any]) -> ProfitReportAudit:
    findings: list[AuditFinding] = []
    platform = str(report.get("platform") or "").strip().lower()
    if platform not in {"tiktok", "shopee", "ozon"}:
        findings.append(AuditFinding("invalid_platform", "report", "Unsupported or missing platform"))
    if str(report.get("status") or "") != "ready":
        findings.append(AuditFinding("report_not_ready", "report", "Only a ready report can pass final audit"))
    lines = report.get("order_lines")
    if not isinstance(lines, list):
        findings.append(AuditFinding("invalid_order_lines", "report", "order_lines must be a JSON array"))
        lines = []
    seen: set[tuple[str, str, str]] = set()
    sums = {key: Decimal("0") for key in ("settlement_cny", "product_cost_cny", "advertising_cny", "external_costs_cny", "profit_cny")}
    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            findings.append(AuditFinding("invalid_order_line", str(index), "Order line must be an object")); continue
        identity = line.get("identity") if isinstance(line.get("identity"), Mapping) else {}
        record_id = str(identity.get("order_line_id") or identity.get("order_id") or index)
        if str(identity.get("platform") or "").lower() != platform:
            findings.append(AuditFinding("cross_platform_line", record_id, "Order line platform differs from report platform"))
        if str(line.get("settlement_status") or "").lower() != "settled":
            findings.append(AuditFinding("unsettled_order_in_profit", record_id, "Profit contains a row not explicitly marked settled"))
        key = (str(identity.get("shop_id") or ""), str(identity.get("order_id") or ""), record_id)
        if key in seen:
            findings.append(AuditFinding("duplicate_order_line", record_id, "Duplicate platform/shop/order-line identity"))
        seen.add(key)
        settlement = _money((line.get("settlement") or {}).get("net_amount_cny"))
        cost = _money((line.get("cost") or {}).get("total_cny"))
        ads = _money((line.get("advertising") or {}).get("amount_cny"))
        external = _money(line.get("external_costs_cny"))
        profit = _money(line.get("profit_cny"))
        if None in (settlement, cost, ads, external, profit):
            findings.append(AuditFinding("invalid_money", record_id, "Required order-line money is missing or invalid")); continue
        expected = settlement - cost - ads - external
        if profit != expected:
            findings.append(AuditFinding("profit_mismatch", record_id, f"Expected {expected}, found {profit}"))
        sums["settlement_cny"] += settlement; sums["product_cost_cny"] += cost; sums["advertising_cny"] += ads; sums["external_costs_cny"] += external; sums["profit_cny"] += profit
    totals = report.get("totals") if isinstance(report.get("totals"), Mapping) else {}
    for field, expected in sums.items():
        actual = _money(totals.get(field))
        if actual != expected:
            findings.append(AuditFinding("total_mismatch", field, f"Expected {expected}, found {actual}"))
    source = report.get("source") if isinstance(report.get("source"), Mapping) else {}
    counted = sum(int(source.get(field) or 0) for field in ("calculated_row_count", "rejected_row_count", "out_of_period_row_count", "unsettled_row_count"))
    raw = int(source.get("raw_row_count") or 0)
    if counted != raw:
        findings.append(AuditFinding("source_count_mismatch", "report", f"Source accounting {counted} does not equal raw {raw}"))
    digest = sha256(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ProfitReportAudit("PASSED" if not findings else "FAILED", str(report.get("report_id") or ""), f"sha256:{digest}", len(lines), tuple(findings))


def _money(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
