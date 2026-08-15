"""Adapt audited stage-one settlement evidence into platform profit rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


RECONCILIATION_TOLERANCE_LOCAL = Decimal("0.000000000001")


@dataclass(frozen=True)
class EvidenceQualityIssue:
    code: str
    record_id: str
    field: str
    message: str


@dataclass(frozen=True)
class AdaptedSettlementEvidence:
    status: str
    platform: str
    site: str
    rows: tuple[Mapping[str, Any], ...]
    issues: tuple[EvidenceQualityIssue, ...]
    reconciliation: Mapping[str, Decimal]
    source: Mapping[str, Any]

    def payload(self) -> dict[str, Any]:
        return _ready({
            "schema_version": "profit-input-adaptation/v1",
            "status": self.status,
            "platform": self.platform,
            "site": self.site,
            "row_count": len(self.rows),
            "issues": self.issues,
            "reconciliation": self.reconciliation,
            "source": self.source,
        })


def adapt_settlement_evidence(
    evidence: Mapping[str, Any],
    catalog: object,
    *,
    period_kind: str,
    seller_sku_by_platform_sku: Mapping[str, str] | None = None,
    quantity_by_order_platform_sku: Mapping[str, object] | None = None,
) -> AdaptedSettlementEvidence:
    """Convert one platform evidence artifact without mixing platform state."""
    platform = _text(evidence.get("platform")).lower()
    site = _text(evidence.get("site")).upper()
    issues: list[EvidenceQualityIssue] = []
    source = {
        "schema_version": _text(evidence.get("schema_version")),
        "snapshot_id": _text(evidence.get("snapshot_id")),
        "checksum": _text(evidence.get("checksum")),
    }
    if source["schema_version"] != "settlement-evidence/v1":
        issues.append(_issue("invalid_evidence_schema", "report", "schema_version"))
    if _text(evidence.get("status")) != "ready":
        issues.append(_issue("evidence_not_ready", "report", "status"))
    if platform not in {"tiktok", "shopee", "ozon"}:
        issues.append(_issue("invalid_platform", "report", "platform"))
    if not source["snapshot_id"] or not source["checksum"]:
        issues.append(_issue("missing_evidence_identity", "report", "snapshot_id/checksum"))
    receipt = evidence.get("receipt") if isinstance(evidence.get("receipt"), Mapping) else {}
    if list(receipt.get("external_writes_performed") or []):
        issues.append(_issue("external_write_claimed", "report", "receipt.external_writes_performed"))
    if issues:
        return AdaptedSettlementEvidence(
            "blocked", platform, site, (), tuple(issues), _empty_reconciliation(), source
        )

    official_total = _decimal(evidence.get("net_settlement_total_local"))
    if official_total is None:
        official_total = Decimal("0")
        issues.append(_issue("missing_official_settlement_total", "report", "net_settlement_total_local"))
    rows: list[dict[str, Any]] = []
    excluded_actual_ads = Decimal("0")
    overrides = dict(seller_sku_by_platform_sku or {})
    quantity_overrides = dict(quantity_by_order_platform_sku or {})
    records = evidence.get("orders") if isinstance(evidence.get("orders"), list) else []
    for record_index, record in enumerate(records):
        if not isinstance(record, Mapping):
            issues.append(_issue("invalid_settlement_record", str(record_index), "orders"))
            continue
        record_id = _text(record.get("order_id")) or str(record_index)
        transaction_type = _text(record.get("transaction_type"))
        amount = _decimal(record.get("net_settlement_amount"))
        is_tiktok_weekly_ads = (
            platform == "tiktok"
            and period_kind == "weekly"
            and "gmv payment" in transaction_type.lower()
            and "ads" in transaction_type.lower()
        )
        if is_tiktok_weekly_ads:
            if amount is None:
                issues.append(_issue("invalid_actual_advertising_adjustment", record_id, "net_settlement_amount"))
            else:
                excluded_actual_ads += amount
            continue

        items = record.get("items") if isinstance(record.get("items"), list) else []
        if not items:
            issues.append(_issue("unsupported_settlement_adjustment", record_id, "items"))
            continue
        if amount is None:
            issues.append(_issue("missing_settlement", record_id, "net_settlement_amount"))
            amount = Decimal("0")
        buyer_paid = (
            _shopee_product_sales_total(record)
            if platform == "shopee"
            else _ozon_product_total(record)
            if platform == "ozon"
            else _decimal(record.get("buyer_total_amount"))
        )
        if buyer_paid is None:
            buyer_paid = Decimal("0")
            issues.append(_issue("missing_ad_basis", record_id, "buyer_paid_product_amount"))
        buyer_cash_paid = _shopee_buyer_cash_product_total(record) if platform == "shopee" else None
        if platform == "shopee" and buyer_cash_paid is None:
            issues.append(_issue("missing_buyer_cash_product_amount", record_id, "buyer_total_amount"))

        weights, allocation_basis = _allocation_weights(platform, items, record_id, issues, quantity_overrides)
        settlement_allocations = _allocate(amount, weights)
        paid_allocations = _allocate(buyer_paid, weights)
        buyer_cash_allocations = (
            _allocate(buyer_cash_paid, weights) if buyer_cash_paid is not None else [None] * len(items)
        )
        component_allocations = [
            _allocate(_decimal(component.get("amount")) or Decimal("0"), weights)
            for component in (record.get("financial_components") or [])
            if isinstance(component, Mapping)
        ]
        components = [
            component for component in (record.get("financial_components") or [])
            if isinstance(component, Mapping)
        ]
        fulfillment = None
        if platform == "shopee":
            fulfillment = _shopee_fulfillment(record, record_id, site, issues)
        elif platform == "tiktok" and transaction_type == "Order":
            fulfillment = _tiktok_fulfillment(record, record_id, issues)
        for item_index, item in enumerate(items):
            if not isinstance(item, Mapping):
                issues.append(_issue("invalid_item", f"{record_id}:{item_index}", "items"))
                continue
            source_sku = _text(item.get("platform_sku") or item.get("seller_sku"))
            seller_sku = _seller_sku(
                platform,
                source_sku,
                item,
                catalog,
                overrides,
            )
            if not seller_sku:
                code = "invalid_shopee_seller_sku" if platform == "shopee" else "missing_seller_sku_mapping"
                issues.append(_issue(code, f"{record_id}:{item_index}", "seller_sku"))
            quantity = _item_quantity(item, record_id, source_sku, quantity_overrides)
            if quantity is None or quantity <= 0:
                issues.append(_issue("invalid_quantity", f"{record_id}:{item_index}", "quantity"))
            if seller_sku and not getattr(catalog, "costs_by_sku", {}).get(seller_sku):
                issues.append(EvidenceQualityIssue(
                    "missing_cost",
                    f"{record_id}:{item_index}",
                    "seller_sku",
                    f"Settlement item maps to seller SKU {seller_sku}, but the cost snapshot has no positive unit cost",
                ))
            metadata = _metadata(catalog, source_sku, seller_sku)
            weight = dict(getattr(catalog, "weight_by_seller_sku", {}).get(seller_sku) or {})
            fee_items = []
            for component_index, component in enumerate(components):
                fee_items.append({
                    "code": _text(component.get("code")),
                    "label": _text(component.get("label") or component.get("code")),
                    "amount": component_allocations[component_index][item_index],
                    "currency": _text(component.get("currency") or record.get("currency")).upper(),
                    "included_in_net_settlement": True,
                })
            row = {
                "platform": platform,
                "region": site,
                "shop_id": _text(metadata.get("shop_id")) or site,
                "order_id": record_id,
                "order_line_id": f"{record_id}:{item_index + 1}",
                "settlement_status": "settled",
                "occurred_at": _text(record.get("order_created_at") or record.get("occurred_at")),
                "settled_at": _text(record.get("settled_at")),
                "currency": _text(record.get("currency")).upper(),
                "net_settlement_amount": settlement_allocations[item_index],
                "buyer_paid_product_amount": paid_allocations[item_index],
                "product_sales_amount": paid_allocations[item_index],
                "buyer_cash_paid_product_amount": buyer_cash_allocations[item_index],
                "platform_sku": source_sku,
                "seller_sku": seller_sku,
                "canonical_sku": seller_sku,
                "quantity": quantity,
                "product_name": _text(item.get("product_name") or metadata.get("product_name")),
                "variant_name": _text(item.get("variant_name") or metadata.get("variant_name")),
                "image_url": _text(item.get("image_url") or metadata.get("image_url")),
                "fee_items": fee_items,
                **({"fulfillment": fulfillment} if fulfillment is not None else {}),
                "source_snapshot_id": source["snapshot_id"],
                "source_settlement_record_id": record_id,
                "source_settlement_facts": [{
                    "fact_id": _text(record.get("statement_id")) or f"{record_id}:{record_index}",
                    "record_id": record_id,
                    "transaction_type": transaction_type,
                    "settled_at": _text(record.get("settled_at")),
                    "net_settlement_amount": settlement_allocations[item_index],
                    "buyer_paid_product_amount": paid_allocations[item_index],
                    "product_sales_amount": paid_allocations[item_index],
                    "buyer_cash_paid_product_amount": buyer_cash_allocations[item_index],
                }],
                "allocation_basis": allocation_basis,
                **weight,
            }
            rows.append(row)

    rows = _consolidate_repeated_order_lines(rows, issues)
    included_total = sum(
        (Decimal(str(row["net_settlement_amount"])) for row in rows), Decimal("0")
    )
    unallocated = official_total - included_total - excluded_actual_ads
    if abs(unallocated) > RECONCILIATION_TOLERANCE_LOCAL:
        issues.append(EvidenceQualityIssue(
            "settlement_reconciliation_mismatch",
            "report",
            "net_settlement_total_local",
            f"Settlement reconciliation difference {unallocated} exceeds local-currency tolerance {RECONCILIATION_TOLERANCE_LOCAL}",
        ))
    reconciliation = {
        "official_net_settlement_local": official_total,
        "included_order_net_settlement_local": included_total,
        "excluded_actual_advertising_local": excluded_actual_ads,
        "unallocated_local": unallocated,
        "tolerance_local": RECONCILIATION_TOLERANCE_LOCAL,
    }
    return AdaptedSettlementEvidence(
        "ready" if not issues else "needs_review",
        platform,
        site,
        tuple(rows),
        tuple(issues),
        reconciliation,
        source,
    )


def _shopee_fulfillment(record, record_id, site, issues):
    """Classify Shopee fulfillment from official import-tax evidence."""
    amounts = {
        _text(component.get("code")).lower(): _decimal(component.get("amount"))
        for component in (record.get("financial_components") or [])
        if isinstance(component, Mapping)
    }
    if site == "MY":
        lvg_sales_tax = amounts.get("sales_tax_on_lvg")
        if lvg_sales_tax is None:
            issues.append(EvidenceQualityIssue(
                "missing_fulfillment_tax_evidence",
                record_id,
                "financial_components",
                "Shopee MY fulfillment requires the low-value-goods sales-tax settlement field",
            ))
            mode = "unknown"
        else:
            mode = "cross_border" if lvg_sales_tax != 0 else "local"
        return {
            "mode": mode,
            "classification_rule": "my_lvg_sales_tax_charged/v1",
            "lvg_sales_tax_local": lvg_sales_tax,
            "import_vat_local": None,
            "import_duty_local": None,
        }

    if site == "PH":
        return {
            "mode": "cross_border",
            "classification_rule": "ph_all_orders_cross_border/v1",
            "lvg_sales_tax_local": None,
            "import_vat_local": None,
            "import_duty_local": None,
        }

    if site == "VN":
        import_vat = next(
            (amounts[code] for code in ("vat_on_imported_goods", "import_vat") if code in amounts),
            None,
        )
        if import_vat is None:
            issues.append(EvidenceQualityIssue(
                "missing_fulfillment_tax_evidence",
                record_id,
                "financial_components",
                "Shopee VN fulfillment requires the value-added-tax settlement field",
            ))
            mode = "unknown"
        else:
            mode = "cross_border" if import_vat != 0 else "local"
        return {
            "mode": mode,
            "classification_rule": "vn_import_vat_charged/v1",
            "lvg_sales_tax_local": None,
            "import_vat_local": import_vat,
            "import_duty_local": None,
        }

    import_vat = next(
        (amounts[code] for code in ("vat_on_imported_goods", "import_vat") if code in amounts),
        None,
    )
    import_duty = next(
        (amounts[code] for code in ("th_import_duty", "import_duty", "customs_duty") if code in amounts),
        None,
    )
    if import_vat is None or import_duty is None:
        issues.append(EvidenceQualityIssue(
            "missing_fulfillment_tax_evidence",
            record_id,
            "financial_components",
            "Shopee fulfillment requires VAT-on-imported-goods and import-duty settlement fields",
        ))
        mode = "unknown"
    elif import_vat != 0 and import_duty != 0:
        mode = "cross_border"
    else:
        mode = "local"
    return {
        "mode": mode,
        "classification_rule": "both_import_vat_and_duty_charged/v3",
        "import_vat_local": import_vat,
        "import_duty_local": import_duty,
    }


def _tiktok_fulfillment(record, record_id, issues):
    amounts = {
        _text(component.get("code")): _decimal(component.get("amount"))
        for component in (record.get("financial_components") or [])
        if isinstance(component, Mapping)
    }
    import_vat = amounts.get("import_vat")
    customs_duty = amounts.get("customs_duty")
    if import_vat is None or customs_duty is None:
        issues.append(EvidenceQualityIssue(
            "missing_fulfillment_tax_evidence",
            record_id,
            "financial_components",
            "TikTok Thailand fulfillment requires import VAT and customs-duty settlement fields",
        ))
        mode = "unknown"
    elif import_vat != 0 or customs_duty != 0:
        mode = "cross_border"
    else:
        mode = "local"
    return {
        "mode": mode,
        "classification_rule": "tiktok_th_import_tax_charged/v1",
        "import_vat_local": import_vat,
        "customs_duty_local": customs_duty,
        "evidence_source": "finance.statement_transactions.import_vat+customs_duty",
    }


def _consolidate_repeated_order_lines(rows, issues):
    groups = {}
    for row in rows:
        key = (
            _text(row.get("platform")),
            _text(row.get("region")),
            _text(row.get("shop_id")),
            _text(row.get("order_line_id")),
        )
        groups.setdefault(key, []).append(row)

    consolidated = []
    stable_fields = (
        "order_id", "order_line_id", "currency", "platform_sku",
        "seller_sku", "canonical_sku", "quantity",
    )
    for key in sorted(groups):
        members = sorted(
            groups[key],
            key=lambda row: (
                _text(row.get("settled_at")),
                _text((row.get("source_settlement_facts") or [{}])[0].get("fact_id")),
            ),
        )
        if len(members) == 1:
            consolidated.append(members[0])
            continue
        baseline = members[0]
        conflicts = [
            field for field in stable_fields
            if any(member.get(field) != baseline.get(field) for member in members[1:])
        ]
        positive_ad_bases = [
            member for member in members
            if (_decimal(member.get("buyer_paid_product_amount")) or Decimal("0")) > 0
        ]
        if conflicts or len(positive_ad_bases) > 1:
            detail = ",".join(conflicts) if conflicts else "buyer_paid_product_amount"
            issues.append(EvidenceQualityIssue(
                "conflicting_repeated_order_line",
                _text(baseline.get("order_line_id")),
                detail,
                "Repeated settlement facts for one order line disagree on product identity, quantity, or advertising basis",
            ))
            continue
        merged = dict(members[-1])
        merged["net_settlement_amount"] = sum(
            (_decimal(member.get("net_settlement_amount")) or Decimal("0") for member in members),
            Decimal("0"),
        )
        merged["buyer_paid_product_amount"] = sum(
            (_decimal(member.get("buyer_paid_product_amount")) or Decimal("0") for member in members),
            Decimal("0"),
        )
        merged["product_sales_amount"] = sum(
            (_decimal(member.get("product_sales_amount")) or Decimal("0") for member in members),
            Decimal("0"),
        )
        cash_values = [
            _decimal(member.get("buyer_cash_paid_product_amount")) for member in members
        ]
        merged["buyer_cash_paid_product_amount"] = (
            sum((value for value in cash_values if value is not None), Decimal("0"))
            if any(value is not None for value in cash_values) else None
        )
        merged["fee_items"] = [
            fee for member in members for fee in (member.get("fee_items") or [])
        ]
        merged["source_settlement_facts"] = [
            fact for member in members for fact in (member.get("source_settlement_facts") or [])
        ]
        consolidated.append(merged)
    return consolidated


def _allocation_weights(platform, items, record_id, issues, quantity_overrides):
    value_weights = []
    quantity_weights = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            value_weights.append(Decimal("0")); quantity_weights.append(Decimal("0")); continue
        source_sku = _text(item.get("platform_sku") or item.get("seller_sku"))
        quantity = _item_quantity(item, record_id, source_sku, quantity_overrides)
        quantity_weights.append(quantity if quantity is not None and quantity > 0 else Decimal("0"))
        price = _decimal(item.get("discounted_price") or item.get("selling_price"))
        value_weights.append(price if price is not None and price > 0 else Decimal("0"))
    if platform == "shopee" and all(value > 0 for value in value_weights):
        return value_weights, "buyer_paid_item_value_share"
    if all(value > 0 for value in quantity_weights):
        return quantity_weights, "quantity_share"
    issues.append(_issue("estimated_equal_item_allocation", record_id, "items"))
    return [Decimal("1") for _ in items], "equal_item_share"


def _item_quantity(item, record_id, source_sku, quantity_overrides):
    raw = item.get("quantity")
    if raw in (None, ""):
        raw = quantity_overrides.get(f"{record_id}|{source_sku}")
    return _decimal(raw)


def _shopee_product_sales_total(record):
    for component in record.get("financial_components") or []:
        if not isinstance(component, Mapping):
            continue
        if _text(component.get("code")) != "order_discounted_price":
            continue
        amount = _decimal(component.get("amount"))
        if amount is not None and amount >= 0:
            return amount
    values = []
    for item in record.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        amount = _decimal(item.get("discounted_price") or item.get("selling_price"))
        if amount is None or amount < 0:
            return None
        values.append(amount)
    return sum(values, Decimal("0")) if values else None


def _shopee_buyer_cash_product_total(record):
    buyer_total = _decimal(record.get("buyer_total_amount"))
    shipping = None
    for component in record.get("financial_components") or []:
        if isinstance(component, Mapping) and _text(component.get("code")) == "buyer_paid_shipping_fee":
            shipping = _decimal(component.get("amount"))
            break
    if buyer_total is None or buyer_total < 0 or shipping is None or shipping < 0:
        return None
    product_total = buyer_total - shipping
    return product_total if product_total >= 0 else None


def _ozon_product_total(record):
    values = []
    for component in record.get("financial_components") or []:
        if not isinstance(component, Mapping):
            continue
        if _text(component.get("code")) != "OperationAgentDeliveredToCustomer":
            continue
        amount = _decimal(component.get("amount"))
        if amount is not None and amount > 0:
            values.append(amount)
    return sum(values, Decimal("0")) if values else None


def _allocate(total: Decimal, weights: Sequence[Decimal]) -> list[Decimal]:
    if not weights:
        return []
    denominator = sum(weights, Decimal("0"))
    if denominator <= 0:
        weights = [Decimal("1") for _ in weights]
        denominator = Decimal(len(weights))
    output = []
    assigned = Decimal("0")
    for index, weight in enumerate(weights):
        value = total - assigned if index == len(weights) - 1 else total * weight / denominator
        output.append(value)
        assigned += value
    return output


def _seller_sku(platform, source_sku, item, catalog, overrides):
    if source_sku in overrides:
        return _canonical_sku(overrides[source_sku])
    explicit = _text(item.get("seller_sku"))
    if platform == "tiktok":
        return _canonical_sku(getattr(catalog, "seller_sku_by_platform_sku", {}).get(source_sku))
    if platform == "shopee":
        value = explicit or source_sku
        return _canonical_sku(value) if value.isdigit() else ""
    return _canonical_sku(explicit) if explicit else ""


def _metadata(catalog, platform_sku, seller_sku):
    return (
        getattr(catalog, "product_by_platform_sku", {}).get(platform_sku)
        or getattr(catalog, "product_by_seller_sku", {}).get(seller_sku)
        or {}
    )


def _canonical_sku(value: object) -> str:
    raw = _text(value)
    return raw[-4:].zfill(4) if raw.isdigit() else raw


def _empty_reconciliation():
    return {
        "official_net_settlement_local": Decimal("0"),
        "included_order_net_settlement_local": Decimal("0"),
        "excluded_actual_advertising_local": Decimal("0"),
        "unallocated_local": Decimal("0"),
        "tolerance_local": RECONCILIATION_TOLERANCE_LOCAL,
    }


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _issue(code: str, record_id: str, field: str) -> EvidenceQualityIssue:
    return EvidenceQualityIssue(code, record_id, field, f"Settlement evidence {record_id} is missing or invalid {field}")


def _ready(value: object) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_ready(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {key: _ready(getattr(value, key)) for key in value.__dataclass_fields__}
    return value
