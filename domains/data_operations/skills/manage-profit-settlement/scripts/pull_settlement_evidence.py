"""Pull one closed period of redacted, read-only settlement evidence.

This is stage 1 only: it does not calculate profit, write production data, or
retain raw API responses. Credential refresh is allowed only when the operator
explicitly opts in. TikTok refresh uses a disposable token copy; Shopee refresh
updates the configured token store because Shopee may rotate refresh tokens.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any, Mapping


SITE_TIMEZONES = {
    ("shopee", "MY"): timezone(timedelta(hours=8), name="Asia/Kuala_Lumpur"),
    ("shopee", "PH"): timezone(timedelta(hours=8), name="Asia/Manila"),
    ("shopee", "TH"): timezone(timedelta(hours=7), name="Asia/Bangkok"),
    ("shopee", "VN"): timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh"),
    ("tiktok", "MY"): timezone(timedelta(hours=8), name="Asia/Kuala_Lumpur"),
    ("tiktok", "PH"): timezone(timedelta(hours=8), name="Asia/Manila"),
    ("tiktok", "TH"): timezone(timedelta(hours=7), name="Asia/Bangkok"),
    ("tiktok", "VN"): timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh"),
    ("ozon", "RU"): timezone(timedelta(hours=3), name="Europe/Moscow"),
}


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "domains" / "data_operations" / "profit_settlement").is_dir():
            return parent
    raise RuntimeError("profit settlement repository root not found")


REPO_ROOT = _repo_root()
if str(REPO_ROOT) in sys.path:
    sys.path.remove(str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pull redacted settled-order evidence")
    parser.add_argument("--platform", required=True, choices=("tiktok", "shopee", "ozon"))
    parser.add_argument("--site", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--allow-credential-refresh",
        action="store_true",
        help=(
            "allow an explicitly authorized credential refresh; TikTok uses a "
            "disposable copy while Shopee updates its configured token store"
        ),
    )
    args = parser.parse_args(argv)
    if args.end < args.start:
        parser.error("--end must not be before --start")
    zone = SITE_TIMEZONES.get((args.platform, args.site.upper()))
    if zone is None:
        parser.error(f"unsupported platform/site timezone: {args.platform}/{args.site}")
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        if args.platform == "shopee":
            payload = pull_shopee(
                args.project_root,
                args.site.upper(),
                args.start,
                args.end,
                zone,
                allow_credential_refresh=args.allow_credential_refresh,
            )
        elif args.platform == "tiktok":
            payload = pull_tiktok(
                args.project_root,
                args.site.upper(),
                args.start,
                args.end,
                zone,
                allow_credential_refresh=args.allow_credential_refresh,
            )
        else:
            payload = pull_ozon(args.project_root, args.site.upper(), args.start, args.end, zone)
    except Exception as exc:  # fail closed while still retaining an audit receipt
        payload = failure_payload(args.platform, args.site.upper(), args.start, args.end, zone, exc)
    output = args.output / f"{args.platform}_{args.site.upper()}_{args.start}_{args.end}.settlement.json"
    output.write_text(json.dumps(_ready(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if payload.get("status") == "ready":
        html_path = output.with_suffix(".html")
        html_path.write_text(render_html(payload), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "platform": args.platform, "site": args.site.upper(), "output": str(output), "row_counts": payload.get("row_counts"), "issues": payload.get("issues", [])}, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "ready" else 2


def pull_shopee(
    root: Path,
    site: str,
    start: date,
    end: date,
    zone,
    *,
    allow_credential_refresh: bool = False,
) -> dict[str, Any]:
    from modules.shopee.orders_pull import fetch_escrow_detail, iter_escrow_list
    from modules.shopee.client import shop_get

    settings = _settings(root)
    shopee = settings.get("shopee") if isinstance(settings.get("shopee"), Mapping) else {}
    token_path = _resolve(root, shopee.get("token_file") or "shopee_tokens.json")
    store = _json_object(token_path)
    shop_id = (store.get("sync_shop_ids") or {}).get(site)
    entry = (store.get("shops") or {}).get(str(shop_id), {})
    token = str(entry.get("access_token") or "")
    expire_at = int(entry.get("expire_at") or 0)
    if not shop_id or not token:
        raise RuntimeError("missing authorized Shopee site token")
    credential_refresh_performed = False
    if expire_at < int(time.time()) + 120:
        if not allow_credential_refresh:
            raise RuntimeError(
                "Shopee access token is expired; rerun with explicit "
                "--allow-credential-refresh after operator authorization"
            )
        refreshed = _refresh_shopee_token(token_path, settings, int(shop_id))
        token = str(refreshed.get("access_token") or "")
        if not token:
            raise RuntimeError("Shopee credential refresh returned no access token")
        credential_refresh_performed = True
    period_start, period_end = _period_bounds(start, end, zone)
    list_rows = iter_escrow_list(int(shop_id), token, time_from=int(period_start.timestamp()), time_to=int(period_end.timestamp()))
    latest: dict[str, Mapping[str, Any]] = {}
    for row in list_rows:
        order_sn = str(row.get("order_sn") or "")
        if not order_sn:
            continue
        previous = latest.get(order_sn)
        if previous is None or int(row.get("escrow_release_time") or 0) >= int(previous.get("escrow_release_time") or 0):
            latest[order_sn] = row
    order_created_at, issues = _shopee_order_created_times(
        int(shop_id), token, sorted(latest), zone, request_get=shop_get
    )
    orders: list[dict[str, Any]] = []
    for order_sn, list_row in sorted(latest.items()):
        try:
            detail = fetch_escrow_detail(int(shop_id), token, order_sn)
            normalized = _shopee_order(
                site, order_sn, list_row, detail, zone,
                order_created_at=order_created_at.get(order_sn, ""),
            )
            if normalized is None:
                issues.append(_issue("out_of_reporting_period", order_sn, "escrow_release_time"))
            else:
                orders.append(normalized)
        except Exception as exc:
            issues.append(_issue("escrow_detail_read_failed", order_sn, "escrow_detail", f"{type(exc).__name__}: {exc}"))
        time.sleep(0.05)
    payload = _success_payload("shopee", site, start, end, zone, orders, len(list_rows), len(latest), issues, "payment/get_escrow_list + payment/get_escrow_detail + order/get_order_detail")
    payload["receipt"]["request_summary"] = {
        "escrow_list_page_size": 100,
        "escrow_list_page_count": (len(list_rows) + 99) // 100,
        "escrow_detail_request_count": len(latest),
        "order_detail_request_count": (len(latest) + 49) // 50,
    }
    payload["receipt"]["credential_refresh_performed"] = credential_refresh_performed
    payload["receipt"]["credential_refresh_scope"] = (
        "configured_shopee_token_store" if credential_refresh_performed else "none"
    )
    return payload


def _refresh_shopee_token(
    token_path: Path,
    settings: Mapping[str, Any],
    shop_id: int,
) -> Mapping[str, Any]:
    """Refresh exactly the configured project token store after opt-in.

    The settlement Skill lives in an isolated worktree, while credentials live
    under the explicitly supplied project root. Bind both settings and token
    path for the duration of the refresh so the worktree's stale token copy can
    never be selected accidentally.
    """
    import core.config as core_config
    from modules.shopee import auth as shopee_auth

    previous_cache = core_config._cache
    previous_token_path = shopee_auth.token_path
    try:
        core_config._cache = dict(settings)
        shopee_auth.token_path = lambda: token_path
        return shopee_auth.refresh_token(shop_id)
    finally:
        shopee_auth.token_path = previous_token_path
        core_config._cache = previous_cache


def pull_tiktok(
    root: Path,
    site: str,
    start: date,
    end: date,
    zone,
    *,
    allow_credential_refresh: bool = False,
) -> dict[str, Any]:
    from core.shops import list_shops
    from tiktok_settlement import collect_shop_rows, fetch_orders_batch

    settings = _settings(root)
    with _tiktok_credential_session(
        root, settings, allow_refresh=allow_credential_refresh
    ) as (token_data, credential_source, credential_refresh_performed):
        token = str(token_data.get("access_token") or "")
        if not token:
            raise RuntimeError("missing TikTok access token")
        shops = list_shops(token)
        shop = next((item for item in shops if str(item.get("region") or "").upper() == site), None)
        if shop is None:
            raise RuntimeError(f"no authorized TikTok shop for site {site}")
        period_start, period_end = _tiktok_period_bounds(start, end)
        region, rows, statements = collect_shop_rows(token, shop, int(period_start.timestamp()), int(period_end.timestamp()))
        item_expansion_order_ids = {
            str(row.get("Order/adjustment ID  ") or "")
            for row in rows
            if row.get("Type ") == "Order" and row.get("Order/adjustment ID  ")
        }
        statement_times = _tiktok_statement_times(statements, zone)
        normalized_rows = []
        issues = []
        out_of_period_rows = 0
        for index, row in enumerate(rows):
            statement_id = str(row.get("Statement ID") or "")
            settled_at = statement_times.get(statement_id)
            if not settled_at:
                issues.append(_issue("missing_settlement_time", statement_id or str(index), "statement_time"))
                continue
            if not start <= datetime.fromisoformat(settled_at).date() <= end:
                out_of_period_rows += 1
                continue
            normalized = _tiktok_row(region, row, index, settled_at)
            if normalized["transaction_type"] == "Order" and normalized["quantity"] is None:
                issues.append(_issue("missing_quantity", normalized["order_id"] or str(index), "quantity"))
            normalized_rows.append(normalized)
        orders = _aggregate_tiktok_records(normalized_rows)
        issues.extend(_tiktok_import_tax_issues(orders, site))
        order_ids = sorted({
            str(order.get("order_id") or "")
            for order in orders
            if order.get("transaction_type") == "Order" and order.get("order_id")
        })
        cipher = str(shop.get("cipher") or shop.get("shop_cipher") or "")
        order_facts, order_time_issues = _tiktok_order_facts(
            token,
            cipher,
            order_ids,
            zone,
            fetcher=fetch_orders_batch,
        )
        issues.extend(order_time_issues)
        for order in orders:
            if order.get("transaction_type") == "Order":
                facts = order_facts.get(str(order.get("order_id") or ""), {})
                order["order_created_at"] = str(facts.get("order_created_at") or "")
        payload = _success_payload("tiktok", site, start, end, zone, orders, len(rows), len(orders), issues, "finance/202309/statements + finance/202501 statement transactions + order/202309/orders") | {
            "statement_count": len(statements),
            "out_of_period_row_count": out_of_period_rows,
        }
        payload["row_counts"]["normalized_settled_orders"] = sum(
            order.get("transaction_type") == "Order" for order in orders
        )
        payload["row_counts"]["normalized_adjustments"] = sum(
            order.get("transaction_type") != "Order" for order in orders
        )
        payload["receipt"]["credential_source"] = credential_source
        payload["receipt"]["credential_refresh_performed"] = credential_refresh_performed
        item_expansion_requests = (len(item_expansion_order_ids) + 19) // 20
        time_enrichment_requests = (len(order_ids) + 19) // 20
        payload["receipt"]["request_summary"] = {
            "order_detail_batch_size": 20,
            "item_expansion_order_detail_request_count": item_expansion_requests,
            "time_enrichment_order_detail_request_count": time_enrichment_requests,
            "total_order_detail_request_count": (
                item_expansion_requests + time_enrichment_requests
            ),
        }
        return payload


def pull_ozon(root: Path, site: str, start: date, end: date, zone) -> dict[str, Any]:
    from modules.ozon import client as ozon_client
    from modules.ozon.settlement import fetch_transactions

    client_id, api_key, credential_source = _ozon_credentials(root)
    if not client_id or not api_key:
        raise RuntimeError("missing Ozon Client-Id/API-Key")
    # Inject the explicitly discovered read credential in memory only. The
    # platform client remains unchanged on disk and receives no write request.
    ozon_client.ozon_credentials = lambda: (client_id, api_key)
    period_start, period_end = _period_bounds(start, end, zone)
    operations = fetch_transactions(period_start.astimezone(timezone.utc).replace(tzinfo=None), period_end.astimezone(timezone.utc).replace(tzinfo=None))
    in_period_operations = []
    out_of_period_operations = 0
    for operation in operations:
        business_date = _date_value(operation.get("operation_date"))
        if business_date is None or not start <= business_date <= end:
            out_of_period_operations += 1
            continue
        in_period_operations.append(operation)
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for operation in in_period_operations:
        posting = str((operation.get("posting") or {}).get("posting_number") or "")
        groups[posting or "(no-posting)"].append(operation)
    orders = []
    excluded = 0
    for posting, rows in sorted(groups.items()):
        sale_rows = [row for row in rows if float(row.get("accruals_for_sale") or 0) > 0]
        if not sale_rows:
            excluded += 1
            continue
        orders.append(_ozon_order(posting, rows))
    payload = _success_payload("ozon", site, start, end, zone, orders, len(operations), len(groups), [], "finance/transaction/list") | {"excluded_unsettled_posting_count": excluded, "out_of_period_operation_count": out_of_period_operations}
    payload["receipt"]["credential_source"] = credential_source
    payload["receipt"]["request_summary"] = {
        "finance_page_size": 1000,
        "finance_page_count": "not_exposed_by_legacy_iterator",
        "returned_operation_count": len(operations),
    }
    return payload


def _shopee_order_created_times(shop_id, token, order_sns, zone, *, request_get):
    result = {}
    issues = []
    ordered = [str(value) for value in order_sns if str(value)]
    for offset in range(0, len(ordered), 50):
        batch = ordered[offset:offset + 50]
        response = request_get(
            "/api/v2/order/get_order_detail",
            shop_id,
            token,
            {
                "order_sn_list": ",".join(batch),
                "response_optional_fields": "create_time",
            },
        )
        if response.get("error"):
            raise RuntimeError(response.get("message") or response.get("error") or str(response))
        rows = (response.get("response") or {}).get("order_list") or []
        for row in rows:
            order_sn = str(row.get("order_sn") or "")
            timestamp = row.get("create_time")
            if order_sn and timestamp not in (None, ""):
                result[order_sn] = datetime.fromtimestamp(
                    int(timestamp), tz=timezone.utc
                ).astimezone(zone).isoformat()
    for order_sn in ordered:
        if order_sn not in result:
            issues.append(_issue("missing_order_created_at", order_sn, "create_time"))
    return result, issues


def _tiktok_order_created_times(token, cipher, order_ids, zone, *, fetcher):
    facts, issues = _tiktok_order_facts(
        token, cipher, order_ids, zone, fetcher=fetcher
    )
    return {
        order_id: str(value.get("order_created_at") or "")
        for order_id, value in facts.items()
        if value.get("order_created_at")
    }, issues


def _tiktok_order_facts(token, cipher, order_ids, zone, *, fetcher):
    ordered = sorted({str(value) for value in order_ids if str(value)})
    rows = fetcher(token, cipher, ordered)
    result = {}
    issues = []
    for order_id in ordered:
        row = rows.get(order_id) if isinstance(rows, Mapping) else None
        timestamp = row.get("create_time") if isinstance(row, Mapping) else None
        try:
            if timestamp in (None, ""):
                raise ValueError("missing create_time")
            order_created_at = datetime.fromtimestamp(
                int(timestamp), tz=timezone.utc
            ).astimezone(zone).isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            issues.append(_issue("missing_order_created_at", order_id, "create_time"))
            order_created_at = ""
        result[order_id] = {
            "order_created_at": order_created_at,
        }
    return result, issues


def _shopee_order(site, order_sn, list_row, detail, zone, *, order_created_at=""):
    release_ts = int(list_row.get("escrow_release_time") or 0)
    release_at = datetime.fromtimestamp(release_ts, tz=timezone.utc).astimezone(zone) if release_ts else None
    if release_at is None:
        return None
    income = detail.get("order_income") if isinstance(detail.get("order_income"), Mapping) else {}
    components = []
    for code, value in sorted(income.items()):
        amount = _decimal(value)
        if amount is None:
            continue
        components.append({"code": code, "amount": amount, "currency": _currency(site), "classification": _component_class(code), "included_in_net_settlement": "unknown"})
    items = []
    for item in income.get("items") or []:
        items.append({
            "line_item_id": str(item.get("line_item_id") or ""), "item_id": str(item.get("item_id") or ""),
            "model_id": str(item.get("model_id") or ""), "seller_sku": str(item.get("model_sku") or item.get("item_sku") or ""),
            "product_name": str(item.get("item_name") or ""), "variant_name": str(item.get("model_name") or ""),
            "quantity": _decimal(item.get("quantity_purchased")), "original_price": _decimal(item.get("original_price")),
            "selling_price": _decimal(item.get("selling_price")), "discounted_price": _decimal(item.get("discounted_price")),
            "seller_discount": _decimal(item.get("seller_discount")), "shopee_discount": _decimal(item.get("shopee_discount")),
            "ams_commission_fee": _decimal(item.get("ams_commission_fee")), "seller_order_processing_fee": _decimal(item.get("seller_order_processing_fee")),
        })
    settlement = _decimal(income.get("escrow_amount_after_adjustment")) or _decimal(income.get("escrow_amount")) or _decimal(list_row.get("payout_amount"))
    return {"order_id": order_sn, "order_created_at": str(order_created_at or ""), "settlement_status": "settled", "settled_at": release_at.isoformat(), "currency": _currency(site), "net_settlement_amount": settlement, "buyer_total_amount": _decimal(income.get("buyer_total_amount")), "financial_components": components, "items": items, "return_order_count": len(detail.get("return_order_sn_list") or [])}


def _tiktok_row(region, row, index, settled_at):
    platform_sku = str(row.get("SKU ID") or "")
    quantity = _decimal(row.get("Quantity"))
    product_name = str(row.get("Product name") or "")
    variant_name = str(row.get("SKU name") or "")
    items = []
    if platform_sku and platform_sku != "/":
        items.append({"platform_sku": platform_sku, "quantity": quantity, "product_name": product_name, "variant_name": variant_name})
    return {"order_id": str(row.get("Order/adjustment ID  ") or ""), "related_order_id": str(row.get("Related order ID") or ""), "statement_id": str(row.get("Statement ID") or ""), "transaction_type": str(row.get("Type ") or "Unknown"), "settlement_status": "settled", "settled_at": settled_at, "currency": str(row.get("Currency") or ""), "platform_sku": platform_sku, "quantity": quantity, "product_name": product_name, "variant_name": variant_name, "net_settlement_amount": _decimal(row.get("Total settlement amount")), "buyer_total_amount": _decimal(row.get("Subtotal after seller discounts")), "financial_components": [{"code": key.strip().lower().replace(" ", "_"), "amount": _decimal(value), "currency": str(row.get("Currency") or ""), "classification": _component_class(key), "included_in_net_settlement": "unknown"} for key, value in row.items() if key not in {"Order/adjustment ID  ", "Related order ID", "Statement ID", "Statement Date", "Currency", "SKU ID", "Quantity", "Product name", "SKU name", "Type "} and _decimal(value) is not None], "items": items, "source_row_index": index, "region": region}


def _tiktok_statement_times(statements, zone):
    result = {}
    for statement in statements:
        statement_id = str(statement.get("id") or "")
        timestamp = statement.get("statement_time")
        if not statement_id or timestamp in (None, ""):
            continue
        try:
            result[statement_id] = datetime.fromtimestamp(
                int(timestamp), tz=timezone.utc
            ).astimezone(zone).isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            continue
    return result


def _aggregate_tiktok_records(rows):
    grouped = {}
    for row in rows:
        key = (
            str(row.get("statement_id") or ""),
            str(row.get("transaction_type") or ""),
            str(row.get("order_id") or ""),
        )
        existing = grouped.get(key)
        if existing is None:
            existing = dict(row)
            existing["items"] = [dict(item) for item in row.get("items") or []]
            existing["financial_components"] = [
                dict(component) for component in row.get("financial_components") or []
            ]
            existing["source_row_indices"] = [row.get("source_row_index")]
            existing.pop("source_row_index", None)
            grouped[key] = existing
            continue

        for money_field in ("net_settlement_amount", "buyer_total_amount"):
            left = existing.get(money_field)
            right = row.get(money_field)
            if left is None:
                existing[money_field] = right
            elif right is not None:
                existing[money_field] = Decimal(str(left)) + Decimal(str(right))
        existing["items"].extend(dict(item) for item in row.get("items") or [])
        existing["source_row_indices"].append(row.get("source_row_index"))
        components = {
            (
                item.get("code"),
                item.get("currency"),
                item.get("classification"),
                item.get("included_in_net_settlement"),
            ): item
            for item in existing["financial_components"]
        }
        for component in row.get("financial_components") or []:
            component_key = (
                component.get("code"),
                component.get("currency"),
                component.get("classification"),
                component.get("included_in_net_settlement"),
            )
            current = components.get(component_key)
            if current is None:
                current = dict(component)
                existing["financial_components"].append(current)
                components[component_key] = current
            elif component.get("amount") is not None:
                current["amount"] = Decimal(str(current.get("amount") or "0")) + Decimal(
                    str(component["amount"])
                )
        existing["platform_sku"] = ""
        existing["quantity"] = None
        existing["product_name"] = ""
        existing["variant_name"] = ""
    return [grouped[key] for key in sorted(grouped)]


def _tiktok_import_tax_issues(orders, site="TH"):
    issues = []
    site = str(site).upper()
    required_by_site = {
        "TH": {"import_vat", "customs_duty"},
        "MY": {"sst"},
        "PH": {"merchant_shipping_fee"},
        "VN": {"import_vat"},
    }
    required = required_by_site.get(site)
    if required is None:
        return issues
    for order in orders:
        if order.get("transaction_type") != "Order":
            continue
        codes = {
            str(component.get("code") or "")
            for component in order.get("financial_components") or []
            if isinstance(component, Mapping)
            and component.get("amount") is not None
        }
        missing = sorted(required - codes)
        if missing:
            order_id = str(order.get("order_id") or "")
            issues.append(_issue(
                "missing_fulfillment_shipping_evidence" if site == "PH" else "missing_fulfillment_tax_evidence",
                order_id,
                "financial_components",
                f"{order_id} is missing TikTok {site} fulfillment components: {', '.join(missing)}",
            ))
    return issues


def _ozon_order(posting, rows):
    items = {}
    components = []
    dates = []
    for row in rows:
        dates.append(str(row.get("operation_date") or ""))
        components.append({"code": str(row.get("operation_type") or ""), "label": str(row.get("operation_type_name") or ""), "occurred_at": str(row.get("operation_date") or ""), "amount": _decimal(row.get("amount")), "currency": "RUB", "classification": _component_class(str(row.get("operation_type_name") or row.get("operation_type") or "")), "included_in_net_settlement": "unknown"})
        for item in row.get("items") or []:
            sku = str(item.get("sku") or "")
            items[sku] = {"platform_sku": sku, "product_name": str(item.get("name") or "")}
    return {"order_id": posting, "settlement_status": "settled", "settled_at": min((value for value in dates if value), default=""), "currency": "RUB", "net_settlement_amount": sum((_decimal(row.get("amount")) or Decimal("0") for row in rows), Decimal("0")), "financial_components": components, "items": list(items.values())}


def _success_payload(platform, site, start, end, zone, orders, raw_count, unique_count, issues, source):
    canonical = _ready(orders)
    checksum = sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for order in orders:
        for item in order.get("financial_components") or []:
            if item.get("amount") is not None:
                totals[str(item.get("code") or "unknown")] += Decimal(str(item["amount"]))
    net_total = sum((Decimal(str(order["net_settlement_amount"])) for order in orders if order.get("net_settlement_amount") is not None), Decimal("0"))
    item_line_count = sum(len(order.get("items") or []) for order in orders)
    return {"schema_version": "settlement-evidence/v1", "status": "ready" if not issues else "needs_review", "platform": platform, "site": site, "period": {"start": start.isoformat(), "end": end.isoformat(), "timezone": zone.tzname(None)}, "snapshot_id": f"{platform}-settlement:{checksum}", "checksum": checksum, "source": source, "pulled_at": datetime.now(timezone.utc).isoformat(), "row_counts": {"raw": raw_count, "unique_parent_records": unique_count, "normalized_settled_orders": len(orders), "normalized_item_lines": item_line_count, "rejected": len(issues)}, "net_settlement_total_local": net_total, "component_totals_local": totals, "orders": orders, "issues": issues, "receipt": {"external_reads_performed": [source], "external_writes_performed": [], "credential_refresh_performed": False, "raw_response_retained": False}}


def failure_payload(platform, site, start, end, zone, exc):
    return {"schema_version": "settlement-evidence/v1", "status": "blocked", "platform": platform, "site": site, "period": {"start": start.isoformat(), "end": end.isoformat(), "timezone": zone.tzname(None)}, "row_counts": {"raw": 0, "unique_parent_records": 0, "normalized_settled_orders": 0, "rejected": 0}, "orders": [], "issues": [_issue("source_read_blocked", "report", "credentials_or_api", f"{type(exc).__name__}: {exc}")], "receipt": {"external_reads_performed": [], "external_writes_performed": [], "credential_refresh_performed": False, "raw_response_retained": False}, "pulled_at": datetime.now(timezone.utc).isoformat()}


def render_html(payload):
    rows = []
    for order in payload.get("orders") or []:
        components = "".join(f"<li><code>{escape(str(item.get('code') or ''))}</code>: {escape(str(item.get('amount')))} {escape(str(item.get('currency') or ''))} <small>{escape(str(item.get('classification') or ''))}; net inclusion={escape(str(item.get('included_in_net_settlement') or ''))}</small></li>" for item in order.get("financial_components") or [])
        items = "".join(f"<li>{escape(str(item.get('seller_sku') or item.get('platform_sku') or ''))} × {escape(str(item.get('quantity') or ''))} — {escape(str(item.get('product_name') or ''))}</li>" for item in order.get("items") or [])
        rows.append(f"<tr><td>{escape(str(order.get('order_id') or ''))}</td><td>{escape(str(order.get('order_created_at') or ''))}</td><td>{escape(str(order.get('settled_at') or ''))}</td><td>{escape(str(order.get('net_settlement_amount') or ''))} {escape(str(order.get('currency') or ''))}</td><td><ul>{items}</ul></td><td><details><summary>{len(order.get('financial_components') or [])} 项</summary><ul>{components}</ul></details></td></tr>")
    return f"<!doctype html><meta charset='utf-8'><title>{escape(payload['platform'])} settlement evidence</title><style>body{{font-family:Arial,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;vertical-align:top}}th{{position:sticky;top:0;background:#f3f5f7}}small{{color:#666}}</style><h1>{escape(payload['platform'])} {escape(payload['site'])} 已结算证据</h1><p>{escape(payload['period']['start'])} 至 {escape(payload['period']['end'])} · {escape(payload['period']['timezone'])}</p><p>订单 {len(payload.get('orders') or [])} · snapshot {escape(str(payload.get('snapshot_id') or ''))}</p><table><thead><tr><th>订单</th><th>下单时间</th><th>结算时间</th><th>净结算</th><th>商品</th><th>全部数值组件</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _period_bounds(start, end, zone):
    return datetime.combine(start, datetime.min.time(), tzinfo=zone), datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=zone)


def _tiktok_period_bounds(start, end):
    return (
        datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
        datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc),
    )


def _component_class(code):
    value = str(code).lower()
    for word, label in (("refund", "refund"), ("tax", "tax"), ("shipping", "shipping"), ("commission", "commission"), ("fee", "fee"), ("discount", "discount"), ("voucher", "voucher"), ("coin", "coin"), ("amount", "amount"), ("price", "price")):
        if word in value:
            return label
    return "other"


def _currency(site):
    return {"TH": "THB", "MY": "MYR", "PH": "PHP", "VN": "VND", "RU": "RUB"}.get(site, "")


def _settings(root):
    return _json_object(root / "config" / "settings.json")


def _ozon_credentials(root):
    candidates = (
        (root / "config" / "ozon.local.json", "config/ozon.local.json"),
        (root / "modules" / "ozon" / "legacy_webapp" / "data" / "credentials.local.json", "legacy_webapp/data/credentials.local.json"),
    )
    for path, label in candidates:
        if not path.is_file() or path.stat().st_size == 0:
            continue
        value = _json_object(path)
        client_id = str(value.get("client_id") or "").strip()
        api_key = str(value.get("api_key") or "").strip()
        if client_id and api_key:
            return client_id, api_key, label
    return "", "", "missing"


def _tiktok_credentials(root, settings):
    path, label = _tiktok_credential_path(root, settings)
    return _json_object(path), label


def _tiktok_credential_path(root, settings):
    configured = _resolve(root, settings.get("token_file") or "tiktok_tokens.json")
    candidates = (
        (configured, configured.name),
        (root / "tiktok_tokens_livelyhive.json", "tiktok_tokens_livelyhive.json"),
    )
    seen = set()
    for path, label in candidates:
        resolved = path.resolve()
        if resolved in seen or not path.is_file() or path.stat().st_size == 0:
            continue
        seen.add(resolved)
        value = _json_object(path)
        if str(value.get("access_token") or "").strip():
            return path, label
    raise RuntimeError("TikTok token files are missing or empty; authorization is required")


@contextmanager
def _tiktok_credential_session(
    root,
    settings,
    *,
    allow_refresh=False,
    token_loader=None,
    auth_module=None,
    config_module=None,
):
    source_path, label = _tiktok_credential_path(root, settings)
    original = _json_object(source_path)
    expires = int(original.get("access_token_expire_in") or 0)
    if not expires or expires >= int(time.time()) + 120:
        yield original, label, False
        return
    if not allow_refresh:
        raise RuntimeError(
            "TikTok access token is expired; rerun with explicit "
            "--allow-credential-refresh approval"
        )
    if not str(original.get("refresh_token") or "").strip():
        raise RuntimeError("TikTok refresh token is missing")

    if auth_module is None:
        from core import auth as auth_module
    if config_module is None:
        from core import config as config_module
    if token_loader is None:
        from tiktok_settlement import load_token as token_loader

    previous_token_path = auth_module.token_path
    previous_settings = config_module._cache
    with tempfile.TemporaryDirectory(prefix="profit-settlement-tiktok-auth-") as temp_dir:
        temporary_path = Path(temp_dir) / source_path.name
        shutil.copyfile(source_path, temporary_path)
        try:
            auth_module.token_path = lambda: temporary_path
            config_module._cache = dict(settings)
            refreshed = token_loader()
            if not isinstance(refreshed, dict) or not str(refreshed.get("access_token") or "").strip():
                raise RuntimeError("TikTok credential refresh returned no access token")
            refresh_performed = any(
                refreshed.get(key) != original.get(key)
                for key in ("access_token", "refresh_token", "access_token_expire_in")
            )
            yield refreshed, label, refresh_performed
        finally:
            auth_module.token_path = previous_token_path
            config_module._cache = previous_settings


def _json_object(path):
    if not path.is_file():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _resolve(root, value):
    path = Path(str(value))
    return path if path.is_absolute() else root / path


def _decimal(value):
    if value is None or isinstance(value, bool) or isinstance(value, (dict, list, tuple)) or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _date_value(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _issue(code, record_id, field, message=""):
    return {"code": code, "record_id": record_id, "field": field, "message": message or f"{record_id} has invalid {field}"}


def _ready(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_ready(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
