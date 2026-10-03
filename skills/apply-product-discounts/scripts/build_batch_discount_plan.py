from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS = (
    ROOT / "reports/product-discounts/uncovered-products-20260901.json",
    ROOT / "reports/product-discounts/miaoshou-uncovered-products-20260901.json",
    ROOT / "reports/product-discounts/shopee-uncovered-products-20260901.json",
)
DEFAULT_OUTPUT = ROOT / "reports/product-discounts/batch-discount-plan-20260901.json"
EXISTING_SKU_EVIDENCE = ROOT / "reports/product-discounts/shopee-existing-sku-discount-evidence-20260901.json"
MIAOSHOU_IDENTITY_EVIDENCE = ROOT / "reports/product-discounts/miaoshou-product-identity-evidence-20260901.json"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _walk(value: object) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _candidate_reference(path: Path, payload: Mapping[str, Any]) -> bool:
    """Select historical reference documents; this does not validate approval."""
    if "release-candidates" in path.parts:
        return payload.get("status") == "READY_FOR_FINAL_REVIEW" and not payload.get("blockers")
    return path.name in {
        "round1-approved-snapshot.json",
        "approved-snapshot.json",
        "first-review-approved.json",
    }


def _direct_price_evidence(path: Path, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in _walk(payload):
        target = node.get("target_label")
        prices = node.get("prices")
        if not isinstance(target, str) or not isinstance(prices, list):
            continue
        for price in prices:
            if not isinstance(price, Mapping):
                continue
            sku = price.get("model_sku") or price.get("seller_sku")
            calculation = price.get("calculation")
            inputs = calculation.get("inputs") if isinstance(calculation, Mapping) else None
            result = calculation.get("result") if isinstance(calculation, Mapping) else None
            percent = inputs.get("discount_reserve_pct") if isinstance(inputs, Mapping) else None
            list_price = result.get("list_price") if isinstance(result, Mapping) else price.get("amount")
            sale_price = result.get("sale_after_discount") if isinstance(result, Mapping) else None
            currency = result.get("currency") if isinstance(result, Mapping) else price.get("currency")
            number = _decimal(percent)
            listed = _decimal(list_price)
            sale = _decimal(sale_price)
            if (
                not isinstance(sku, str)
                or not sku.strip()
                or number is None
                or number != number.to_integral_value()
                or not Decimal("0") < number < Decimal("100")
                or listed is None
                or listed <= 0
                or sale is None
                or sale <= 0
                or not isinstance(currency, str)
                or not currency
            ):
                continue
            expected = listed * (Decimal("1") - number / Decimal("100"))
            tolerance = max(Decimal("1.01"), listed * Decimal("0.0001"))
            if abs(expected - sale) > tolerance:
                continue
            rows.append(
                {
                    "target_label": target,
                    "seller_sku": sku.strip(),
                    "discount_percent": int(number),
                    "list_price": str(listed.normalize()),
                    "sale_after_discount": str(sale.normalize()),
                    "currency": currency.upper(),
                    "source_path": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "source_kind": "REPORTED_TARGET_FORMULA_REFERENCE",
                }
            )
    return rows


def collect_evidence() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = ROOT / "reports/product-preparation"
    for path in base.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(payload, Mapping) or not _candidate_reference(path, payload):
            continue
        direct = _direct_price_evidence(path, payload)
        rows.extend(direct)
        by_key = {(row["target_label"], row["seller_sku"]): row for row in direct}
        # Approved Shopee rows intentionally reuse the same-region LivelyHive
        # list price. Bind that reuse only when SKU, currency and list price
        # match the formula-bearing TikTok row exactly.
        for node in _walk(payload):
            target = node.get("target_label")
            prices = node.get("prices")
            if not isinstance(target, str) or not target.startswith("shopee:") or not isinstance(prices, list):
                continue
            region = target.split(":", 1)[1]
            for price in prices:
                if not isinstance(price, Mapping):
                    continue
                sku = price.get("model_sku") or price.get("seller_sku")
                source = by_key.get((f"tiktok:LH_{region}", sku))
                listed = _decimal(price.get("amount"))
                currency = str(price.get("currency") or "").upper()
                if (
                    source is not None
                    and listed is not None
                    and str(listed.normalize()) == source["list_price"]
                    and currency == source["currency"]
                ):
                    rows.append(
                        {
                            **source,
                            "target_label": target,
                            "source_kind": "SAME_REGION_PRICE_REFERENCE",
                            "source_target_label": source["target_label"],
                        }
                    )
    if EXISTING_SKU_EVIDENCE.exists():
        try:
            observed = json.loads(EXISTING_SKU_EVIDENCE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            observed = {}
        for row in observed.get("evidence", []) if isinstance(observed, Mapping) else []:
            if not isinstance(row, Mapping):
                continue
            target = row.get("target_label")
            sku = row.get("seller_sku")
            percent = row.get("discount_percent")
            if isinstance(target, str) and isinstance(sku, str) and isinstance(percent, int) and 0 < percent < 100:
                rows.append({
                    "target_label": target,
                    "seller_sku": sku,
                    "discount_percent": percent,
                    "list_price": None,
                    "sale_after_discount": None,
                    "currency": None,
                    "source_path": str(EXISTING_SKU_EVIDENCE.relative_to(ROOT)).replace("\\", "/"),
                    "source_kind": "OFFICIAL_EXISTING_SAME_SKU_DISCOUNT",
                })
    unique: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["target_label"], row["seller_sku"], row["discount_percent"], row["source_path"])
        unique[key] = row
    return list(unique.values())


def _target_for_store(source: str, store: Mapping[str, Any]) -> str:
    region = str(store.get("region") or store.get("site") or "").upper()
    name = str(store.get("shop_name") or store.get("store_name") or store.get("shop") or "")
    if "shopee" in source.casefold():
        return f"shopee:{region}"
    if name.casefold().startswith("homebloom"):
        return f"tiktok:HB_{region}"
    if region in {"MX", "GB"}:
        return f"tiktok:{region}"
    return f"tiktok:LH_{region}"


def _store_rows(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = report.get("stores") or report.get("shops")
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def build_plan(
    report_paths: Iterable[Path],
    *,
    policy_discount: int | None = None,
    policy_reference: str | None = None,
) -> dict[str, Any]:
    if policy_discount is not None and not 1 <= policy_discount <= 99:
        raise ValueError("policy_discount must be between 1 and 99")
    if policy_discount is not None and not str(policy_reference or "").strip():
        raise ValueError("policy_reference is required for an operator policy override")
    evidence = collect_evidence()
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in evidence:
        indexed[(row["target_label"], row["seller_sku"])].append(row)
    recovered_identity: dict[tuple[str, str], Mapping[str, Any]] = {}
    if MIAOSHOU_IDENTITY_EVIDENCE.exists():
        try:
            identity_payload = json.loads(MIAOSHOU_IDENTITY_EVIDENCE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            identity_payload = {}
        for row in identity_payload.get("rows", []) if isinstance(identity_payload, Mapping) else []:
            if isinstance(row, Mapping) and row.get("status") == "EXACT_IDENTITY":
                recovered_identity[(str(row.get("target_label") or ""), str(row.get("product_id") or ""))] = row

    actions: list[dict[str, Any]] = []
    for report_path in report_paths:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        source = str(report.get("source") or report.get("definition") or report_path.name)
        for store in _store_rows(report):
            target = _target_for_store(source + " " + str(store.get("source") or ""), store)
            products = store.get("products")
            if not isinstance(products, list):
                continue
            for product in products:
                if not isinstance(product, Mapping):
                    continue
                product_id = str(product.get("product_id") or product.get("item_id") or product.get("id") or "")
                skus = product.get("seller_skus") or product.get("model_skus") or []
                skus = sorted({str(value).strip() for value in skus if str(value).strip()}) if isinstance(skus, list) else []
                identity = recovered_identity.get((target, product_id))
                if not skus and identity is not None:
                    skus = sorted({str(value).strip() for value in identity.get("seller_skus", []) if str(value).strip()})
                reasons: list[str] = []
                selected: dict[str, dict[str, Any]] = {}
                if not product_id:
                    reasons.append("MISSING_PRODUCT_ID")
                if not skus:
                    reasons.append("MISSING_SELLER_SKUS")
                if policy_discount is None:
                    for sku in skus:
                        candidates = indexed.get((target, sku), [])
                        percents = {row["discount_percent"] for row in candidates}
                        if len(percents) != 1:
                            reasons.append("MISSING_OR_AMBIGUOUS_FROZEN_DISCOUNT")
                            continue
                        percent = next(iter(percents))
                        matching = [row for row in candidates if row["discount_percent"] == percent]
                        selected[sku] = sorted(matching, key=lambda row: (row["source_path"], row["source_kind"]))[-1]
                    discounts = {row["discount_percent"] for row in selected.values()}
                    if skus and len(selected) != len(skus):
                        reasons.append("INCOMPLETE_SKU_DISCOUNT_EVIDENCE")
                    if len(discounts) > 1:
                        reasons.append("MIXED_PRODUCT_DISCOUNTS")
                    status = "READY" if not reasons else "BLOCKED_PRICE_EVIDENCE"
                    discount_percent = next(iter(discounts)) if status == "READY" else None
                    discount_evidence: list[dict[str, Any]] = [selected[sku] for sku in skus if sku in selected]
                else:
                    status = "READY" if not reasons else "BLOCKED_IDENTITY_EVIDENCE"
                    discount_percent = policy_discount if status == "READY" else None
                    discount_evidence = [{
                        "source_kind": "OPERATOR_POLICY_OVERRIDE",
                        "policy_reference": str(policy_reference),
                        "discount_percent": policy_discount,
                    }] if status == "READY" else []
                action = {
                    "target_label": target,
                    "product_id": product_id,
                    "title": str(product.get("title") or product.get("product_name") or ""),
                    "seller_skus": skus,
                    "discount_percent": discount_percent,
                    "activity_ids": [
                        str(row.get("activity_id") or row.get("discount_id"))
                        for row in store.get("activities", [])
                        if isinstance(row, Mapping)
                        and (row.get("activity_id") or row.get("discount_id"))
                        and row.get("excluded_from_single_product_discount_audit") is not True
                        and row.get("activity_type") not in {"SHIPPING_DISCOUNT", "BUY_MORE_SAVE_MORE"}
                    ],
                    "source_report": str(report_path.relative_to(ROOT)).replace("\\", "/"),
                    "evidence": discount_evidence,
                    "identity_evidence": identity,
                    "status": status,
                    "reasons": sorted(set(reasons)),
                }
                action["reference_discount_percent"] = action.pop("discount_percent")
                action["discount_percent"] = None
                action["approval_status"] = "NOT_APPROVED"
                action["reference_status"] = action["status"]
                action["status"] = "REFERENCE_REQUIRES_FROZEN_PLAN"
                action["reasons"].append("report observations and title/image/SKU similarity do not authorize a discount")
                action["action_digest"] = _digest({key: value for key, value in action.items() if key != "action_digest"})
                actions.append(action)
    counts = defaultdict(int)
    target_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for action in actions:
        counts[action["status"]] += 1
        target_counts[action["target_label"]][action["status"]] += 1
    root = {
        "schema_version": "batch-product-discount-reference/v2",
        "approval_status": "NOT_APPROVED",
        "external_writes": 0,
        "selection_rule": (
            "unapproved operator proposal requiring exact frozen target facts"
            if policy_discount is not None
            else "report references requiring exact frozen product and target verification"
        ),
        "operator_policy_override": ({
            "discount_percent": policy_discount,
            "policy_reference": str(policy_reference),
        } if policy_discount is not None else None),
        "actions": actions,
        "summary": {
            "total": len(actions),
            **dict(sorted(counts.items())),
            "by_target": {
                target: dict(sorted(values.items()))
                for target, values in sorted(target_counts.items())
            },
        },
    }
    return {**root, "plan_digest": _digest(root)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="append", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--policy-discount", type=int)
    parser.add_argument("--policy-reference")
    args = parser.parse_args()
    reports = tuple(args.report) if args.report else DEFAULT_REPORTS
    plan = build_plan(
        reports,
        policy_discount=args.policy_discount,
        policy_reference=args.policy_reference,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **plan["summary"], "plan_digest": plan["plan_digest"]}, ensure_ascii=False))
    return 0 if plan["summary"].get("READY") else 1


if __name__ == "__main__":
    raise SystemExit(main())
