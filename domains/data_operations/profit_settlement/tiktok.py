"""TikTok-only order profit settlement.  It imports no other platform engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from typing import Any

from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot


PLATFORM = "tiktok"
SCHEMA_VERSION = "profit-report/tiktok/v1"


@dataclass(frozen=True)
class TikTokQualityIssue:
    code: str
    record_id: str
    field: str
    message: str


@dataclass(frozen=True)
class TikTokProfitReport:
    report_id: str
    idempotency_key: str
    calculation_kind: str
    period_kind: str
    period: Mapping[str, str]
    status: str
    totals: Mapping[str, Decimal]
    order_lines: tuple[Mapping[str, Any], ...]
    quality_issues: tuple[TikTokQualityIssue, ...]
    source: Mapping[str, Any]
    assumptions: Mapping[str, Any]
    generated_at: datetime
    code_version: str

    def payload(self) -> dict[str, Any]:
        return _json_ready(
            {
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
                "assumptions": self.assumptions,
                "generated_at": self.generated_at,
                "code_version": self.code_version,
            }
        )


def build_weekly_report(
    rows: Iterable[Mapping[str, object]],
    *,
    period_start: date | str,
    period_end: date | str,
    costs: CostSnapshot,
    fx: FxSnapshot,
    ad_rate: Decimal | str = Decimal("0.22"),
    ad_rate_source: str | None = None,
    local_fulfillment_fee_cny: Decimal | str = Decimal("4"),
    generated_at: datetime | None = None,
    code_version: str = "unknown",
) -> TikTokProfitReport:
    rate = _decimal(ad_rate)
    if rate is None or rate < 0 or rate > 1:
        raise ValueError("ad_rate must be a decimal fraction between 0 and 1")
    local_fulfillment = _nonnegative_money(local_fulfillment_fee_cny, "local_fulfillment_fee_cny")
    return _build_report(
        rows,
        period_start=period_start,
        period_end=period_end,
        period_kind="weekly",
        costs=costs,
        fx=fx,
        ad_rate=rate,
        ad_rate_source=_text(ad_rate_source) or ("default_22" if rate == Decimal("0.22") else "operator_global_override"),
        local_fulfillment_fee_cny=local_fulfillment,
        generated_at=generated_at,
        code_version=code_version,
    )


def build_monthly_report(
    rows: Iterable[Mapping[str, object]],
    *,
    period_start: date | str,
    period_end: date | str,
    costs: CostSnapshot,
    fx: FxSnapshot,
    actual_advertising: Mapping[str, object] | None,
    local_fulfillment_fee_cny: Decimal | str = Decimal("4"),
    generated_at: datetime | None = None,
    code_version: str = "unknown",
) -> TikTokProfitReport:
    local_fulfillment=_nonnegative_money(local_fulfillment_fee_cny,"local_fulfillment_fee_cny")
    start,end=_period(period_start,period_end);source_rows=[dict(row) for row in rows];issues=[];prepared=[];rejected=out_of_period=unsettled=0
    _audit_metadata(issues, fx, code_version)
    for index,row in enumerate(source_rows):
        record_id=_text(row.get("order_line_id") or row.get("order_id")) or str(index)
        if _text(row.get("settlement_status")).lower() != "settled":unsettled+=1;continue
        settled_at=_datetime(row.get("settled_at"))
        if settled_at is None:issues.append(_issue("missing_settled_at",record_id,"settled_at"));rejected+=1;continue
        if not start<=settled_at.date()<=end:out_of_period+=1;continue
        sku=_text(row.get("canonical_sku"));cost=costs.get(sku);currency=_text(row.get("currency")).upper();fx_rate=fx.get(currency);quantity=_decimal(row.get("quantity"));settlement=_decimal(row.get("net_settlement_amount"));paid=_decimal(row.get("buyer_paid_product_amount"));invalid=False
        for missing,field,code in ((not sku or cost is None,"canonical_sku","missing_cost"),(not currency or fx_rate is None,"currency","missing_fx"),(quantity is None or quantity<=0,"quantity","invalid_quantity"),(settlement is None,"net_settlement_amount","missing_settlement"),(paid is None or paid<0,"buyer_paid_product_amount","missing_ad_basis")):
            if missing:issues.append(_issue(code,record_id,field));invalid=True
        if invalid:rejected+=1;continue
        if cost.version == "unspecified": issues.append(_issue("missing_cost_version",record_id,"cost.version"))
        if not _text(row.get("source_snapshot_id")): issues.append(_issue("missing_source_snapshot",record_id,"source_snapshot_id"))
        fees,external=_fees(row.get("fee_items"),currency,fx,issues,record_id)
        if fees is None:rejected+=1;continue
        prepared.append({"row":row,"record_id":record_id,"occurred":_datetime(row.get("occurred_at")),"settled_at":settled_at,"sku":sku,"cost":cost,"currency":currency,"fx_rate":fx_rate,"quantity":quantity,"settlement":settlement,"paid":paid,"paid_cny":paid*fx_rate,"fees":fees,"external":external,"fulfillment":_fulfillment(row,record_id,issues)})
    prepared.sort(key=_prepared_settlement_sort_key)
    ad=_actual_advertising(actual_advertising,issues);allocations=_allocate_ads(ad.get("total_cny") if ad else None,prepared);lines=[]
    if ad:
        for index,item in enumerate(prepared):
            row=item["row"];ad_cny=allocations[index];settlement_cny=item["settlement"]*item["fx_rate"];product_cost=item["cost"].unit_cost_cny*item["quantity"]
            lines.append({"identity":{"platform":PLATFORM,"shop_id":_text(row.get("shop_id")),"region":_text(row.get("region")).upper(),"order_id":_text(row.get("order_id")),"order_line_id":item["record_id"]},"product":{"platform_sku":_text(row.get("platform_sku")),"seller_sku":_text(row.get("seller_sku")),"canonical_sku":item["sku"],"product_name":_text(row.get("product_name")),"variant_name":_text(row.get("variant_name")),"image_url":_text(row.get("image_url")),"quantity":item["quantity"],"unit_weight_g":_decimal(row.get("unit_weight_g")),"package_weight_g":_decimal(row.get("package_weight_g")),"billable_weight_g":_decimal(row.get("billable_weight_g")),"weight_source":_text(row.get("weight_source"))},"occurred_at":item["occurred"],"settled_at":item["settled_at"],"settlement_status":"settled","fulfillment":dict(item["fulfillment"]),"settlement":{"currency":item["currency"],"net_amount_local":item["settlement"],"net_amount_cny":settlement_cny,"buyer_paid_product_amount_local":item["paid"]},"fx":{"rate_cny_per_local":item["fx_rate"],**fx.payload()},"cost":{"unit_cost_cny":item["cost"].unit_cost_cny,"quantity":item["quantity"],"total_cny":product_cost,"version":item["cost"].version,"effective_at":item["cost"].effective_at,"source":item["cost"].source,"snapshot_id":costs.snapshot_id},"advertising":{**ad,"basis":"buyer_paid_product_amount_cny","basis_amount_cny":item["paid_cny"],"amount_cny":ad_cny},"fee_items":item["fees"],"external_costs_cny":item["external"],"profit_cny":settlement_cny-product_cost-ad_cny-item["external"],"source_snapshot_id":_text(row.get("source_snapshot_id")),"source_settlement_facts":list(row.get("source_settlement_facts") or [])})
    else:rejected+=len(prepared)
    zero_settlement_order_count=_apply_zero_settlement_unshipped_policy(lines)
    charged_local_order_count=_apply_local_fulfillment_costs(lines,local_fulfillment,issues)
    fulfillment_policy=_fulfillment_policy(local_fulfillment)
    settlement_outcome_policy=_zero_settlement_policy()
    source_checksum=_checksum(sorted((_json_ready(row) for row in source_rows),key=_canonical));fingerprint=_checksum({"schema":SCHEMA_VERSION,"period_kind":"monthly","period":[start.isoformat(),end.isoformat()],"source":source_checksum,"costs":costs.snapshot_id,"fx":fx.snapshot_id,"advertising":ad or {},"fulfillment_policy":fulfillment_policy,"settlement_outcome_policy":settlement_outcome_policy,"code_version":code_version})
    return TikTokProfitReport(report_id=f"tiktok-profit-{fingerprint[:16]}",idempotency_key=f"{SCHEMA_VERSION}:{fingerprint}",calculation_kind="realized_settlement_with_actual_ads",period_kind="monthly",period={"start":start.isoformat(),"end":end.isoformat(),"timezone":"source_local_date"},status="ready" if not issues else "needs_review",totals=_totals(lines),order_lines=tuple(lines),quality_issues=tuple(issues),source={"input_checksum":source_checksum,"raw_row_count":len(source_rows),"calculated_row_count":len(lines),"rejected_row_count":rejected,"out_of_period_row_count":out_of_period,"unsettled_row_count":unsettled,"zero_settlement_unshipped_order_count":zero_settlement_order_count,"local_fulfillment_charged_order_count":charged_local_order_count,"fulfillment_order_counts":_fulfillment_order_counts(lines),"fulfillment_policy":fulfillment_policy,"settlement_outcome_policy":settlement_outcome_policy,"cost_snapshot":costs.payload(),"fx_snapshot":fx.payload()},assumptions={**(ad or {}),"fulfillment_policy":fulfillment_policy,"settlement_outcome_policy":settlement_outcome_policy},generated_at=generated_at or datetime.now(timezone.utc),code_version=code_version)


def _build_report(
    rows: Iterable[Mapping[str, object]],
    *,
    period_start: date | str,
    period_end: date | str,
    period_kind: str,
    costs: CostSnapshot,
    fx: FxSnapshot,
    ad_rate: Decimal,
    ad_rate_source: str,
    local_fulfillment_fee_cny: Decimal,
    generated_at: datetime | None,
    code_version: str,
) -> TikTokProfitReport:
    start, end = _period(period_start, period_end)
    source_rows = [dict(row) for row in rows]
    issues: list[TikTokQualityIssue] = []
    _audit_metadata(issues, fx, code_version)
    calculated: list[dict[str, Any]] = []
    rejected = out_of_period = unsettled = 0
    for index, row in enumerate(source_rows):
        record_id = _text(row.get("order_line_id") or row.get("order_id")) or str(index)
        if _text(row.get("settlement_status")).lower() != "settled":
            unsettled += 1
            continue
        settled_at = _datetime(row.get("settled_at"))
        if settled_at is None:
            issues.append(_issue("missing_settled_at", record_id, "settled_at"))
            rejected += 1
            continue
        if settled_at.date() < start or settled_at.date() > end:
            out_of_period += 1
            continue
        sku = _text(row.get("canonical_sku"))
        cost = costs.get(sku)
        currency = _text(row.get("currency")).upper()
        fx_rate = fx.get(currency)
        quantity = _decimal(row.get("quantity"))
        settlement = _decimal(row.get("net_settlement_amount"))
        paid = _decimal(row.get("buyer_paid_product_amount"))
        invalid = False
        if not sku or cost is None:
            issues.append(_issue("missing_cost", record_id, "canonical_sku"))
            invalid = True
        if not currency or fx_rate is None:
            issues.append(_issue("missing_fx", record_id, "currency"))
            invalid = True
        if quantity is None or quantity <= 0:
            issues.append(_issue("invalid_quantity", record_id, "quantity"))
            invalid = True
        if settlement is None:
            issues.append(_issue("missing_settlement", record_id, "net_settlement_amount"))
            invalid = True
        if paid is None:
            issues.append(_issue("missing_ad_basis", record_id, "buyer_paid_product_amount"))
            invalid = True
        if invalid:
            rejected += 1
            continue
        if cost.version == "unspecified":
            issues.append(_issue("missing_cost_version", record_id, "cost.version"))
        if not _text(row.get("source_snapshot_id")):
            issues.append(_issue("missing_source_snapshot", record_id, "source_snapshot_id"))
        fee_items, external_cost = _fees(row.get("fee_items"), currency, fx, issues, record_id)
        if fee_items is None:
            rejected += 1
            continue
        settlement_cny = settlement * fx_rate
        product_cost_cny = cost.unit_cost_cny * quantity
        advertising_local = paid * ad_rate
        advertising_cny = advertising_local * fx_rate
        profit_cny = settlement_cny - product_cost_cny - advertising_cny - external_cost
        calculated.append(
            {
                "identity": {
                    "platform": PLATFORM,
                    "shop_id": _text(row.get("shop_id")),
                    "region": _text(row.get("region")).upper(),
                    "order_id": _text(row.get("order_id")),
                    "order_line_id": record_id,
                },
                "product": {
                    "platform_sku": _text(row.get("platform_sku")),
                    "seller_sku": _text(row.get("seller_sku")),
                    "canonical_sku": sku,
                    "product_name": _text(row.get("product_name")),
                    "variant_name": _text(row.get("variant_name")),
                    "image_url": _text(row.get("image_url")),
                    "quantity": quantity,
                    "unit_weight_g": _decimal(row.get("unit_weight_g")),
                    "package_weight_g": _decimal(row.get("package_weight_g")),
                    "billable_weight_g": _decimal(row.get("billable_weight_g")),
                    "weight_source": _text(row.get("weight_source")),
                },
                "occurred_at": _datetime(row.get("occurred_at")),
                "settled_at": settled_at,
                "settlement_status": "settled",
                "fulfillment": _fulfillment(row, record_id, issues),
                "settlement": {
                    "currency": currency,
                    "net_amount_local": settlement,
                    "net_amount_cny": settlement_cny,
                    "buyer_paid_product_amount_local": paid,
                },
                "fx": {"rate_cny_per_local": fx_rate, **fx.payload()},
                "cost": {
                    "unit_cost_cny": cost.unit_cost_cny,
                    "quantity": quantity,
                    "total_cny": product_cost_cny,
                    "version": cost.version,
                    "effective_at": cost.effective_at,
                    "source": cost.source,
                    "snapshot_id": costs.snapshot_id,
                },
                "advertising": {
                    "mode": "estimated_rate",
                    "rate": ad_rate,
                    "input_source": ad_rate_source,
                    "policy_version": "operator-adjustable-ad-rate/v1",
                    "basis": "buyer_paid_product_amount",
                    "basis_amount_local": paid,
                    "amount_local": advertising_local,
                    "amount_cny": advertising_cny,
                },
                "fee_items": fee_items,
                "external_costs_cny": external_cost,
                "profit_cny": profit_cny,
                "source_snapshot_id": _text(row.get("source_snapshot_id")),
                "source_settlement_facts": list(row.get("source_settlement_facts") or []),
            }
        )
    zero_settlement_order_count = _apply_zero_settlement_unshipped_policy(calculated)
    charged_local_order_count = _apply_local_fulfillment_costs(calculated, local_fulfillment_fee_cny, issues)
    calculated.sort(key=_line_settlement_sort_key)
    totals = _totals(calculated)
    fulfillment_policy = _fulfillment_policy(local_fulfillment_fee_cny)
    settlement_outcome_policy = _zero_settlement_policy()
    source_fingerprint = _checksum(sorted((_json_ready(row) for row in source_rows), key=_canonical))
    fingerprint = _checksum(
        {
            "schema": SCHEMA_VERSION,
            "period": [start.isoformat(), end.isoformat()],
            "source": source_fingerprint,
            "costs": costs.snapshot_id,
            "fx": fx.snapshot_id,
            "ad_rate": str(ad_rate),
            "ad_rate_source": ad_rate_source,
            "fulfillment_policy": fulfillment_policy,
            "settlement_outcome_policy": settlement_outcome_policy,
            "code_version": code_version,
        }
    )
    now = generated_at or datetime.now(timezone.utc)
    return TikTokProfitReport(
        report_id=f"tiktok-profit-{fingerprint[:16]}",
        idempotency_key=f"{SCHEMA_VERSION}:{fingerprint}",
        calculation_kind="realized_settlement_with_estimated_ads",
        period_kind=period_kind,
        period={"start": start.isoformat(), "end": end.isoformat(), "timezone": "source_local_date"},
        status="ready" if not issues else "needs_review",
        totals=totals,
        order_lines=tuple(calculated),
        quality_issues=tuple(issues),
        source={
            "input_checksum": source_fingerprint,
            "raw_row_count": len(source_rows),
            "calculated_row_count": len(calculated),
            "rejected_row_count": rejected,
            "out_of_period_row_count": out_of_period,
            "unsettled_row_count": unsettled,
            "zero_settlement_unshipped_order_count": zero_settlement_order_count,
            "local_fulfillment_charged_order_count": charged_local_order_count,
            "fulfillment_order_counts": _fulfillment_order_counts(calculated),
            "fulfillment_policy": fulfillment_policy,
            "settlement_outcome_policy": settlement_outcome_policy,
            "cost_snapshot": costs.payload(),
            "fx_snapshot": fx.payload(),
        },
        assumptions={"advertising_basis": "buyer_paid_product_amount", "advertising_rate": ad_rate, "advertising_rate_source": ad_rate_source, "advertising_policy_version": "operator-adjustable-ad-rate/v1", "fulfillment_policy": fulfillment_policy, "settlement_outcome_policy": settlement_outcome_policy},
        generated_at=now,
        code_version=code_version,
    )


def _fees(raw: object, default_currency: str, fx: FxSnapshot, issues: list[TikTokQualityIssue], record_id: str):
    output = []
    external = Decimal("0")
    for index, item in enumerate(raw if isinstance(raw, (list, tuple)) else ()):
        if not isinstance(item, Mapping):
            issues.append(_issue("invalid_fee_item", record_id, f"fee_items[{index}]"))
            return None, external
        amount = _decimal(item.get("amount"))
        currency = _text(item.get("currency") or default_currency).upper()
        rate = fx.get(currency)
        if amount is None or rate is None:
            issues.append(_issue("invalid_fee_item", record_id, f"fee_items[{index}]"))
            return None, external
        amount_cny = amount * rate
        included = bool(item.get("included_in_net_settlement"))
        output.append({"code": _text(item.get("code")), "label": _text(item.get("label")), "amount": amount, "currency": currency, "amount_cny": amount_cny, "included_in_net_settlement": included})
        if not included:
            external += amount_cny
    return tuple(output), external


def _actual_advertising(value: Mapping[str, object] | None, issues: list[TikTokQualityIssue]) -> dict[str, Any] | None:
    item=dict(value or {});total=_decimal(item.get("total_cny"));source=_text(item.get("source"));as_of=_text(item.get("as_of"));snapshot=_text(item.get("snapshot_id"))
    if total is None or total<0 or not source or not as_of or not snapshot:
        issues.append(TikTokQualityIssue("missing_actual_advertising","report","actual_advertising","TikTok monthly profit requires auditable actual advertising spend"));return None
    return {"mode":"allocated_actual_ads","total_cny":total,"source":source,"as_of":as_of,"snapshot_id":snapshot,"allocation_basis":"buyer_paid_product_amount_cny","allocation_version":"gmv-pro-rata-v1"}


def _allocate_ads(total: Decimal | None, rows: list[dict[str, Any]]) -> list[Decimal]:
    if total is None or not rows:return []
    denominator=sum((row["paid_cny"] for row in rows),Decimal("0"))
    if denominator<=0:return [Decimal("0")]*len(rows)
    result=[];assigned=Decimal("0")
    for index,row in enumerate(rows):
        amount=total-assigned if index==len(rows)-1 else (total*row["paid_cny"]/denominator).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
        result.append(amount);assigned+=amount
    return result


def _fulfillment(row: Mapping[str, object], record_id: str, issues: list[TikTokQualityIssue]) -> dict[str, Any]:
    raw = row.get("fulfillment")
    item = dict(raw) if isinstance(raw, Mapping) else {}
    mode = _text(item.get("mode")).lower()
    if mode not in {"local", "cross_border"}:
        issues.append(_issue("missing_fulfillment_tax_evidence", record_id, "fulfillment.mode"))
        mode = "unknown"
    return {
        "mode": mode,
        "classification_rule": _text(item.get("classification_rule")) or "tiktok_th_import_tax_charged/v1",
        "import_vat_local": _decimal(item.get("import_vat_local")),
        "customs_duty_local": _decimal(
            item.get("customs_duty_local")
            if "customs_duty_local" in item
            else item.get("import_duty_local")
        ),
        "local_fulfillment_cost_cny": Decimal("0"),
        "allocation_method": "not_applicable" if mode != "local" else "pending",
    }


def _apply_local_fulfillment_costs(
    lines: list[dict[str, Any]],
    fee_per_order: Decimal,
    issues: list[TikTokQualityIssue],
) -> int:
    groups: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        if (line.get("settlement_outcome") or {}).get("classification") == "zero_settlement_unshipped":
            continue
        fulfillment = line.get("fulfillment") if isinstance(line.get("fulfillment"), Mapping) else {}
        if fulfillment.get("mode") != "local":
            continue
        order_id = _text((line.get("identity") or {}).get("order_id"))
        if not order_id:
            issues.append(_issue("missing_order_id_for_local_cost", "report", "identity.order_id"))
            continue
        groups.setdefault(order_id, []).append(line)
    for order_id, members in sorted(groups.items()):
        members.sort(key=lambda line: _text((line.get("identity") or {}).get("order_line_id")))
        allocations = _allocate_order_cost(fee_per_order, members)
        for line, allocation in zip(members, allocations):
            fulfillment = line["fulfillment"]
            fulfillment["local_fulfillment_cost_cny"] = allocation
            fulfillment["allocation_method"] = "product_sales_cny_pro_rata_with_exact_remainder/v1"
            fulfillment["order_cost_policy"] = _fulfillment_policy(fee_per_order)
            line["external_costs_cny"] += allocation
            line["profit_cny"] -= allocation
    return len(groups)


def _apply_zero_settlement_unshipped_policy(lines: list[dict[str, Any]]) -> int:
    groups: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        order_id = _text((line.get("identity") or {}).get("order_id"))
        if order_id:
            groups.setdefault(order_id, []).append(line)
    affected = 0
    policy = _zero_settlement_policy()
    for order_id, members in sorted(groups.items()):
        net_local = sum(
            (_decimal((line.get("settlement") or {}).get("net_amount_local")) or Decimal("0") for line in members),
            Decimal("0"),
        )
        if net_local != 0:
            continue
        affected += 1
        for line in members:
            cost = line["cost"]
            catalog_total = _decimal(cost.get("total_cny")) or Decimal("0")
            cost["catalog_total_cny"] = catalog_total
            cost["total_cny"] = Decimal("0")
            cost["recognition"] = "not_recognized_zero_settlement_unshipped"
            line["profit_cny"] = (
                (_decimal((line.get("settlement") or {}).get("net_amount_cny")) or Decimal("0"))
                - (_decimal((line.get("advertising") or {}).get("amount_cny")) or Decimal("0"))
                - (_decimal(line.get("external_costs_cny")) or Decimal("0"))
            )
            line["settlement_outcome"] = {
                "classification": "zero_settlement_unshipped",
                "parent_net_settlement_local": net_local,
                "product_cost_recognized": False,
                "local_fulfillment_cost_recognized": False,
                "advertising_cost_recognized": True,
                "policy_version": policy["policy_version"],
            }
    return affected


def _allocate_order_cost(total: Decimal, lines: list[dict[str, Any]]) -> list[Decimal]:
    if not lines:
        return []
    weights = [
        max(
            (_decimal((line.get("settlement") or {}).get("buyer_paid_product_amount_local")) or Decimal("0"))
            * (_decimal((line.get("fx") or {}).get("rate_cny_per_local")) or Decimal("0")),
            Decimal("0"),
        )
        for line in lines
    ]
    denominator = sum(weights, Decimal("0"))
    if denominator <= 0:
        weights = [Decimal("1")] * len(lines)
        denominator = Decimal(len(lines))
    result: list[Decimal] = []
    assigned = Decimal("0")
    for index, weight in enumerate(weights):
        amount = (
            total - assigned
            if index == len(lines) - 1
            else (total * weight / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
        result.append(amount)
        assigned += amount
    return result


def _fulfillment_policy(fee_per_order: Decimal) -> dict[str, Any]:
    return {
        "local_fulfillment_fee_cny_per_order": fee_per_order,
        "cost_components": ["local_fulfillment"],
        "classification_rule": "tiktok_th_import_tax_charged/v1",
    }


def _zero_settlement_policy() -> dict[str, Any]:
    return {
        "policy_version": "zero-settlement-unshipped-ads-only/v1",
        "scope": "tiktok_parent_order",
        "predicate": "sum(net_settlement_amount_local)==0",
        "product_cost_recognized": False,
        "local_fulfillment_cost_recognized": False,
        "advertising_cost_recognized": True,
    }


def _fulfillment_order_counts(lines: list[Mapping[str, Any]]) -> dict[str, int]:
    orders: dict[str, set[str]] = {"local": set(), "cross_border": set(), "unknown": set()}
    for line in lines:
        mode = _text((line.get("fulfillment") or {}).get("mode")) or "unknown"
        order_id = _text((line.get("identity") or {}).get("order_id"))
        orders.setdefault(mode, set()).add(order_id)
    return {key: len(value - {""}) for key, value in sorted(orders.items())}


def _totals(lines: list[Mapping[str, Any]]) -> dict[str, Decimal]:
    return {
        "settlement_cny": sum((line["settlement"]["net_amount_cny"] for line in lines), Decimal("0")),
        "product_cost_cny": sum((line["cost"]["total_cny"] for line in lines), Decimal("0")),
        "advertising_cny": sum((line["advertising"]["amount_cny"] for line in lines), Decimal("0")),
        "local_fulfillment_cost_cny": sum((_decimal((line.get("fulfillment") or {}).get("local_fulfillment_cost_cny")) or Decimal("0") for line in lines), Decimal("0")),
        "external_costs_cny": sum((line["external_costs_cny"] for line in lines), Decimal("0")),
        "profit_cny": sum((line["profit_cny"] for line in lines), Decimal("0")),
    }


def _period(start: date | str, end: date | str) -> tuple[date, date]:
    first = start if isinstance(start, date) else date.fromisoformat(str(start))
    last = end if isinstance(end, date) else date.fromisoformat(str(end))
    if last < first:
        raise ValueError("period_end must not precede period_start")
    return first, last


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _prepared_settlement_sort_key(item: Mapping[str, Any]) -> tuple[float, str, str]:
    settled_at = item["settled_at"]
    if settled_at.tzinfo is None:
        settled_at = settled_at.replace(tzinfo=timezone.utc)
    return (
        -settled_at.timestamp(),
        _text(item["row"].get("order_id")),
        _text(item.get("record_id")),
    )


def _line_settlement_sort_key(item: Mapping[str, Any]) -> tuple[float, str, str]:
    settled_at = item["settled_at"]
    if settled_at.tzinfo is None:
        settled_at = settled_at.replace(tzinfo=timezone.utc)
    identity = item["identity"]
    return (
        -settled_at.timestamp(),
        _text(identity.get("order_id")),
        _text(identity.get("order_line_id")),
    )


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _nonnegative_money(value: object, field: str) -> Decimal:
    amount = _decimal(value)
    if amount is None or amount < 0:
        raise ValueError(f"{field} must be a non-negative CNY amount")
    return amount


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _issue(code: str, record_id: str, field: str) -> TikTokQualityIssue:
    return TikTokQualityIssue(code, record_id, field, f"TikTok record {record_id} is missing or invalid {field}")


def _audit_metadata(issues: list[TikTokQualityIssue], fx: FxSnapshot, code_version: str) -> None:
    if not fx.source: issues.append(_issue("missing_fx_source", "report", "fx.source"))
    if not fx.as_of: issues.append(_issue("missing_fx_as_of", "report", "fx.as_of"))
    if not _text(code_version) or code_version == "unknown": issues.append(_issue("missing_code_version", "report", "code_version"))


def _json_ready(value: object) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_ready(getattr(value, key)) for key in value.__dataclass_fields__}
    return value


def _canonical(value: object) -> str:
    return json.dumps(_json_ready(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(value: object) -> str:
    return sha256(_canonical(value).encode()).hexdigest()
