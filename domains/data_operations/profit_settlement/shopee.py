"""Shopee-only order profit settlement.  It imports no other platform engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from typing import Any

from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot


PLATFORM = "shopee"
SCHEMA_VERSION = "profit-report/shopee/v1"


@dataclass(frozen=True)
class ShopeeQualityIssue:
    code: str
    record_id: str
    field: str
    message: str


@dataclass(frozen=True)
class ShopeeProfitReport:
    report_id: str
    idempotency_key: str
    calculation_kind: str
    period_kind: str
    period: Mapping[str, str]
    status: str
    totals: Mapping[str, Decimal]
    order_lines: tuple[Mapping[str, Any], ...]
    quality_issues: tuple[ShopeeQualityIssue, ...]
    source: Mapping[str, Any]
    advertising: Mapping[str, Any]
    generated_at: datetime
    code_version: str

    def payload(self) -> dict[str, Any]:
        return _ready({
            "schema_version": SCHEMA_VERSION,
            "report_id": self.report_id,
            "idempotency_key": self.idempotency_key,
            "platform": PLATFORM,
            "calculation_kind": self.calculation_kind,
            "period_kind": self.period_kind,
            "period": self.period,
            "status": self.status,
            "totals": self.totals,
            "order_lines": self.order_lines,
            "quality_issues": self.quality_issues,
            "source": self.source,
            "advertising": self.advertising,
            "generated_at": self.generated_at,
            "code_version": self.code_version,
        })


def build_weekly_report(
    rows: Iterable[Mapping[str, object]],
    *,
    period_start: date | str,
    period_end: date | str,
    costs: CostSnapshot,
    fx: FxSnapshot,
    ad_rate: Decimal | str = Decimal("0.22"),
    ad_rate_source: str | None = None,
    generated_at: datetime | None = None,
    code_version: str = "unknown",
) -> ShopeeProfitReport:
    rate_value = _decimal(ad_rate)
    if rate_value is None or rate_value < 0 or rate_value > 1:
        raise ValueError("ad_rate must be a decimal fraction between 0 and 1")
    start, end = _period(period_start, period_end)
    source_rows = [dict(row) for row in rows]
    issues: list[ShopeeQualityIssue] = []; lines=[]; rejected=out_of_period=unsettled=0
    _audit_metadata(issues, fx, code_version)
    for index,row in enumerate(source_rows):
        record_id=_text(row.get("order_line_id") or row.get("order_id")) or str(index)
        if _text(row.get("settlement_status")).lower() != "settled": unsettled+=1; continue
        settled_at=_datetime(row.get("settled_at"))
        if settled_at is None: issues.append(_issue("missing_settled_at",record_id,"settled_at"));rejected+=1;continue
        if not start<=settled_at.date()<=end:out_of_period+=1;continue
        sku=_text(row.get("canonical_sku"));cost=costs.get(sku);currency=_text(row.get("currency")).upper();fx_rate=fx.get(currency)
        quantity=_decimal(row.get("quantity"));settlement=_decimal(row.get("net_settlement_amount"));paid=_decimal(row.get("buyer_paid_product_amount"));invalid=False
        for missing,field,code in ((not sku or cost is None,"canonical_sku","missing_cost"),(not currency or fx_rate is None,"currency","missing_fx"),(quantity is None or quantity<=0,"quantity","invalid_quantity"),(settlement is None,"net_settlement_amount","missing_settlement"),(paid is None,"buyer_paid_product_amount","missing_ad_basis")):
            if missing:issues.append(_issue(code,record_id,field));invalid=True
        if invalid:rejected+=1;continue
        if cost.version == "unspecified": issues.append(_issue("missing_cost_version",record_id,"cost.version"))
        if not _text(row.get("source_snapshot_id")): issues.append(_issue("missing_source_snapshot",record_id,"source_snapshot_id"))
        fees,external=_fees(row.get("fee_items"),currency,fx,issues,record_id)
        if fees is None:rejected+=1;continue
        settlement_cny=settlement*fx_rate;product_cost=cost.unit_cost_cny*quantity;ad_local=paid*rate_value;ad_cny=ad_local*fx_rate
        lines.append({
            "identity":{"platform":PLATFORM,"shop_id":_text(row.get("shop_id")),"region":_text(row.get("region")).upper(),"order_id":_text(row.get("order_id")),"order_line_id":record_id},
            "product":{"platform_sku":_text(row.get("platform_sku")),"seller_sku":_text(row.get("seller_sku")),"canonical_sku":sku,"product_name":_text(row.get("product_name")),"variant_name":_text(row.get("variant_name")),"image_url":_text(row.get("image_url")),"quantity":quantity,"unit_weight_g":_decimal(row.get("unit_weight_g")),"package_weight_g":_decimal(row.get("package_weight_g")),"billable_weight_g":_decimal(row.get("billable_weight_g")),"weight_source":_text(row.get("weight_source"))},
            "occurred_at":_datetime(row.get("occurred_at")),"settled_at":settled_at,"settlement_status":"settled","settlement":{"currency":currency,"net_amount_local":settlement,"net_amount_cny":settlement_cny,"buyer_paid_product_amount_local":paid},"fx":{"rate_cny_per_local":fx_rate,**fx.payload()},
            "cost":{"unit_cost_cny":cost.unit_cost_cny,"quantity":quantity,"total_cny":product_cost,"version":cost.version,"effective_at":cost.effective_at,"source":cost.source,"snapshot_id":costs.snapshot_id},
            "advertising":{"mode":"estimated_rate","rate":rate_value,"input_source":_text(ad_rate_source) or ("default_22" if rate_value == Decimal("0.22") else "operator_global_override"),"policy_version":"operator-adjustable-ad-rate/v1","basis":"buyer_paid_product_amount","basis_amount_local":paid,"amount_local":ad_local,"amount_cny":ad_cny},
            "fee_items":fees,"external_costs_cny":external,"profit_cny":settlement_cny-product_cost-ad_cny-external,"source_snapshot_id":_text(row.get("source_snapshot_id")),"source_settlement_facts":list(row.get("source_settlement_facts") or []),
        })
    lines.sort(key=_line_settlement_sort_key)
    rate_source=_text(ad_rate_source) or ("default_22" if rate_value == Decimal("0.22") else "operator_global_override");source_checksum=_checksum(sorted((_ready(row) for row in source_rows),key=_canonical));fingerprint=_checksum({"schema":SCHEMA_VERSION,"period_kind":"weekly","period":[start.isoformat(),end.isoformat()],"source":source_checksum,"costs":costs.snapshot_id,"fx":fx.snapshot_id,"ad_rate":str(rate_value),"ad_rate_source":rate_source,"code_version":code_version})
    return ShopeeProfitReport(report_id=f"shopee-profit-{fingerprint[:16]}",idempotency_key=f"{SCHEMA_VERSION}:{fingerprint}",calculation_kind="realized_settlement_with_estimated_ads",period_kind="weekly",period={"start":start.isoformat(),"end":end.isoformat(),"timezone":"source_local_date"},status="ready" if not issues else "needs_review",totals=_totals(lines),order_lines=tuple(lines),quality_issues=tuple(issues),source={"input_checksum":source_checksum,"raw_row_count":len(source_rows),"calculated_row_count":len(lines),"rejected_row_count":rejected,"out_of_period_row_count":out_of_period,"unsettled_row_count":unsettled,"cost_snapshot":costs.payload(),"fx_snapshot":fx.payload()},advertising={"mode":"estimated_rate","rate":rate_value,"input_source":rate_source,"policy_version":"operator-adjustable-ad-rate/v1","basis":"buyer_paid_product_amount"},generated_at=generated_at or datetime.now(timezone.utc),code_version=code_version)


def build_monthly_report(
    rows: Iterable[Mapping[str, object]],
    *,
    period_start: date | str,
    period_end: date | str,
    costs: CostSnapshot,
    fx: FxSnapshot,
    actual_advertising: Mapping[str, object] | None,
    generated_at: datetime | None = None,
    code_version: str = "unknown",
) -> ShopeeProfitReport:
    start, end = _period(period_start, period_end)
    source_rows = [dict(row) for row in rows]
    issues: list[ShopeeQualityIssue] = []
    _audit_metadata(issues, fx, code_version)
    rejected = out_of_period = unsettled = 0
    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(source_rows):
        record_id = _text(row.get("order_line_id") or row.get("order_id")) or str(index)
        if _text(row.get("settlement_status")).lower() != "settled":
            unsettled += 1
            continue
        settled_at = _datetime(row.get("settled_at"))
        if settled_at is None:
            issues.append(_issue("missing_settled_at", record_id, "settled_at")); rejected += 1; continue
        if not start <= settled_at.date() <= end:
            out_of_period += 1; continue
        sku = _text(row.get("canonical_sku")); cost = costs.get(sku)
        currency = _text(row.get("currency")).upper(); fx_rate = fx.get(currency)
        quantity = _decimal(row.get("quantity")); settlement = _decimal(row.get("net_settlement_amount")); paid = _decimal(row.get("buyer_paid_product_amount"))
        invalid = False
        for missing, field, code in (
            (not sku or cost is None, "canonical_sku", "missing_cost"),
            (not currency or fx_rate is None, "currency", "missing_fx"),
            (quantity is None or quantity <= 0, "quantity", "invalid_quantity"),
            (settlement is None, "net_settlement_amount", "missing_settlement"),
            (paid is None or paid < 0, "buyer_paid_product_amount", "missing_ad_basis"),
        ):
            if missing: issues.append(_issue(code, record_id, field)); invalid = True
        if invalid:
            rejected += 1; continue
        if cost.version == "unspecified": issues.append(_issue("missing_cost_version",record_id,"cost.version"))
        if not _text(row.get("source_snapshot_id")): issues.append(_issue("missing_source_snapshot",record_id,"source_snapshot_id"))
        fees, external = _fees(row.get("fee_items"), currency, fx, issues, record_id)
        if fees is None:
            rejected += 1; continue
        prepared.append({"row": row, "record_id": record_id, "occurred": _datetime(row.get("occurred_at")), "settled_at": settled_at, "sku": sku, "cost": cost, "currency": currency, "fx_rate": fx_rate, "quantity": quantity, "settlement": settlement, "paid": paid, "paid_cny": paid * fx_rate, "fees": fees, "external": external})
    prepared.sort(key=_prepared_settlement_sort_key)

    advertising = _advertising(actual_advertising, issues)
    allocations = _allocate(advertising.get("total_cny") if advertising else None, prepared)
    calculated = tuple(_line(item, allocations[index], costs, fx, advertising) for index, item in enumerate(prepared)) if advertising else ()
    if not advertising and prepared:
        rejected += len(prepared)
    totals = _totals(calculated)
    source_checksum = _checksum(sorted((_ready(row) for row in source_rows), key=_canonical))
    fingerprint = _checksum({"schema": SCHEMA_VERSION, "period_kind": "monthly", "period": [start.isoformat(), end.isoformat()], "source": source_checksum, "costs": costs.snapshot_id, "fx": fx.snapshot_id, "advertising": advertising or {}, "code_version": code_version})
    return ShopeeProfitReport(
        report_id=f"shopee-profit-{fingerprint[:16]}",
        idempotency_key=f"{SCHEMA_VERSION}:{fingerprint}",
        calculation_kind="realized_settlement_with_actual_ads",
        period_kind="monthly",
        period={"start": start.isoformat(), "end": end.isoformat(), "timezone": "source_local_date"},
        status="ready" if not issues else "needs_review",
        totals=totals,
        order_lines=calculated,
        quality_issues=tuple(issues),
        source={"input_checksum": source_checksum, "raw_row_count": len(source_rows), "calculated_row_count": len(calculated), "rejected_row_count": rejected, "out_of_period_row_count": out_of_period, "unsettled_row_count": unsettled, "cost_snapshot": costs.payload(), "fx_snapshot": fx.payload()},
        advertising=advertising or {},
        generated_at=generated_at or datetime.now(timezone.utc),
        code_version=code_version,
    )


def _advertising(value: Mapping[str, object] | None, issues: list[ShopeeQualityIssue]) -> dict[str, Any] | None:
    item = dict(value or {})
    total = _decimal(item.get("total_cny"))
    source = _text(item.get("source")); as_of = _text(item.get("as_of")); snapshot = _text(item.get("snapshot_id"))
    if total is None or total < 0 or not source or not as_of or not snapshot:
        issues.append(ShopeeQualityIssue("missing_actual_advertising", "report", "actual_advertising", "Shopee monthly profit requires auditable actual advertising spend"))
        return None
    return {"mode": "allocated_actual_ads", "total_cny": total, "source": source, "as_of": as_of, "snapshot_id": snapshot, "allocation_basis": "buyer_paid_product_amount_cny", "allocation_version": "gmv-pro-rata-v1"}


def _allocate(total: Decimal | None, rows: list[dict[str, Any]]) -> list[Decimal]:
    if total is None or not rows:
        return []
    denominator = sum((row["paid_cny"] for row in rows), Decimal("0"))
    if denominator <= 0:
        return [Decimal("0")] * len(rows)
    result: list[Decimal] = []; assigned = Decimal("0")
    for index, row in enumerate(rows):
        amount = total - assigned if index == len(rows) - 1 else (total * row["paid_cny"] / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        result.append(amount); assigned += amount
    return result


def _line(item: Mapping[str, Any], advertising_cny: Decimal, costs: CostSnapshot, fx: FxSnapshot, advertising: Mapping[str, Any]) -> dict[str, Any]:
    row=item["row"]; settlement_cny=item["settlement"]*item["fx_rate"]; product_cost=item["cost"].unit_cost_cny*item["quantity"]
    return {
        "identity": {"platform": PLATFORM, "shop_id": _text(row.get("shop_id")), "region": _text(row.get("region")).upper(), "order_id": _text(row.get("order_id")), "order_line_id": item["record_id"]},
        "product": {"platform_sku": _text(row.get("platform_sku")), "seller_sku": _text(row.get("seller_sku")), "canonical_sku": item["sku"], "product_name": _text(row.get("product_name")), "variant_name": _text(row.get("variant_name")), "image_url": _text(row.get("image_url")), "quantity": item["quantity"], "unit_weight_g": _decimal(row.get("unit_weight_g")), "package_weight_g": _decimal(row.get("package_weight_g")), "billable_weight_g": _decimal(row.get("billable_weight_g")), "weight_source": _text(row.get("weight_source"))},
        "occurred_at": item["occurred"], "settled_at": item["settled_at"], "settlement_status": "settled",
        "settlement": {"currency": item["currency"], "net_amount_local": item["settlement"], "net_amount_cny": settlement_cny, "buyer_paid_product_amount_local": item["paid"]},
        "fx": {"rate_cny_per_local": item["fx_rate"], **fx.payload()},
        "cost": {"unit_cost_cny": item["cost"].unit_cost_cny, "quantity": item["quantity"], "total_cny": product_cost, "version": item["cost"].version, "effective_at": item["cost"].effective_at, "source": item["cost"].source, "snapshot_id": costs.snapshot_id},
        "advertising": {**advertising, "basis": "buyer_paid_product_amount_cny", "basis_amount_cny": item["paid_cny"], "amount_cny": advertising_cny},
        "fee_items": item["fees"], "external_costs_cny": item["external"],
        "profit_cny": settlement_cny-product_cost-advertising_cny-item["external"], "source_snapshot_id": _text(row.get("source_snapshot_id")), "source_settlement_facts": list(row.get("source_settlement_facts") or []),
    }


def _fees(raw: object, default_currency: str, fx: FxSnapshot, issues: list[ShopeeQualityIssue], record_id: str):
    output=[]; external=Decimal("0")
    for index,item in enumerate(raw if isinstance(raw,(list,tuple)) else ()):
        if not isinstance(item,Mapping): issues.append(_issue("invalid_fee_item",record_id,f"fee_items[{index}]")); return None,external
        amount=_decimal(item.get("amount")); currency=_text(item.get("currency") or default_currency).upper(); rate=fx.get(currency)
        if amount is None or rate is None: issues.append(_issue("invalid_fee_item",record_id,f"fee_items[{index}]")); return None,external
        amount_cny=amount*rate; included=bool(item.get("included_in_net_settlement")); output.append({"code":_text(item.get("code")),"label":_text(item.get("label")),"amount":amount,"currency":currency,"amount_cny":amount_cny,"included_in_net_settlement":included})
        if not included: external+=amount_cny
    return tuple(output),external


def _totals(lines):
    return {"settlement_cny":sum((x["settlement"]["net_amount_cny"] for x in lines),Decimal("0")),"product_cost_cny":sum((x["cost"]["total_cny"] for x in lines),Decimal("0")),"advertising_cny":sum((x["advertising"]["amount_cny"] for x in lines),Decimal("0")),"external_costs_cny":sum((x["external_costs_cny"] for x in lines),Decimal("0")),"profit_cny":sum((x["profit_cny"] for x in lines),Decimal("0"))}


def _period(start,end):
    first=start if isinstance(start,date) else date.fromisoformat(str(start)); last=end if isinstance(end,date) else date.fromisoformat(str(end))
    if last<first: raise ValueError("period_end must not precede period_start")
    return first,last


def _datetime(value):
    if isinstance(value,datetime): return value
    text=_text(value)
    if not text:return None
    try: parsed=datetime.fromisoformat(text.replace("Z","+00:00"))
    except ValueError:return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _prepared_settlement_sort_key(item):
    settled_at = item["settled_at"]
    if settled_at.tzinfo is None:
        settled_at = settled_at.replace(tzinfo=timezone.utc)
    return (-settled_at.timestamp(), _text(item["row"].get("order_id")), _text(item.get("record_id")))


def _line_settlement_sort_key(item):
    settled_at = item["settled_at"]
    if settled_at.tzinfo is None:
        settled_at = settled_at.replace(tzinfo=timezone.utc)
    identity = item["identity"]
    return (-settled_at.timestamp(), _text(identity.get("order_id")), _text(identity.get("order_line_id")))


def _decimal(value):
    if value is None or isinstance(value,bool) or str(value).strip()=="":return None
    try:return Decimal(str(value))
    except (InvalidOperation,ValueError):return None


def _text(value):return str(value).strip() if value is not None else ""
def _issue(code,record_id,field):return ShopeeQualityIssue(code,record_id,field,f"Shopee record {record_id} is missing or invalid {field}")
def _audit_metadata(issues,fx,code_version):
    if not fx.source:issues.append(_issue("missing_fx_source","report","fx.source"))
    if not fx.as_of:issues.append(_issue("missing_fx_as_of","report","fx.as_of"))
    if not _text(code_version) or code_version=="unknown":issues.append(_issue("missing_code_version","report","code_version"))
def _ready(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,(datetime,date)):return value.isoformat()
    if isinstance(value,Mapping):return {str(k):_ready(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [_ready(v) for v in value]
    if hasattr(value,"__dataclass_fields__"):return {k:_ready(getattr(value,k)) for k in value.__dataclass_fields__}
    return value
def _canonical(value):return json.dumps(_ready(value),ensure_ascii=False,sort_keys=True,separators=(",",":"))
def _checksum(value):return sha256(_canonical(value).encode()).hexdigest()
