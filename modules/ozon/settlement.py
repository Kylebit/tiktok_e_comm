"""Ozon settlement summary for the unified billing page."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from modules.catalog import listings as cat_mod
from modules.ozon.client import ozon_post

DEFAULT_RUB_PER_CNY = 191.0 / 18.0
DEFAULT_CNY_PER_RUB = 1.0 / DEFAULT_RUB_PER_CNY


def _month_ranges(start: datetime, end: datetime):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        range_start = datetime(y, m, 1)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        range_end = min(datetime(ny, nm, 1) - timedelta(seconds=1), end)
        yield max(range_start, start), range_end
        y, m = ny, nm


def _date_only(value: str | None) -> str:
    raw = value or ""
    if "T" in raw:
        return raw[:10]
    return raw.split(" ", 1)[0] if " " in raw else raw


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _round(value: float | int | None, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _image_from_product(info: dict[str, Any] | None) -> str:
    if not info:
        return ""
    for key in ("primary_image", "color_image", "images"):
        value = info.get(key)
        if isinstance(value, list) and value:
            return str(value[0] or "")
        if isinstance(value, str) and value:
            return value
    return ""


def _category_for_op(op: dict[str, Any]) -> str:
    op_type = str(op.get("operation_type") or "")
    op_name = str(op.get("operation_type_name") or "")
    hay = f"{op_type} {op_name}".lower()
    if "deliveredtocustomer" in hay:
        return "sale"
    if "acquiring" in hay or "эквайр" in hay:
        return "acquiring"
    if "delivery" in hay or "logistic" in hay or "достав" in hay or "логист" in hay:
        return "logistics"
    if "promotion" in hay or "advert" in hay or "реклам" in hay:
        return "advertising"
    if "agencyfee" in hay or "agent" in hay or "агент" in hay:
        return "agent_fee"
    if "storage" in hay or "warehouse" in hay or "хранен" in hay or "склад" in hay:
        return "storage"
    return "other"


def _fee_label(category: str) -> str:
    return {
        "sale": "商品销售价",
        "commission": "平台佣金",
        "logistics": "物流费",
        "storage": "仓储费",
        "advertising": "广告费",
        "acquiring": "收单费",
        "agent_fee": "代理服务费",
        "other": "其他扣款",
    }.get(category, category)


def _fx_payload(force_refresh: bool = False) -> dict[str, Any]:
    try:
        from modules.sourcing.fx_rates import get_exchange_rates

        fx = get_exchange_rates(force_refresh=force_refresh)
    except Exception as exc:  # noqa: BLE001 - settlement must degrade.
        fx = {"ok": True, "rates": {}, "live": False, "degraded": True, "error": str(exc)}
    rates = dict(fx.get("rates") or {})
    cny_per_rub = _num(rates.get("RUB")) or DEFAULT_CNY_PER_RUB
    rates["RUB"] = cny_per_rub
    fx["rates"] = rates
    fx["cny_per_rub"] = cny_per_rub
    fx["rub_per_cny"] = round(1.0 / cny_per_rub, 6) if cny_per_rub else DEFAULT_RUB_PER_CNY
    if not _num((fx.get("rates") or {}).get("RUB")):
        fx["degraded"] = True
    return fx


def fetch_transactions(date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
    all_ops: list[dict[str, Any]] = []
    for start, end in _month_ranges(date_from, date_to):
        page = 1
        while True:
            body = {
                "filter": {
                    "date": {
                        "from": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "to": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    },
                    "transaction_type": "all",
                },
                "page": page,
                "page_size": 1000,
            }
            res = ozon_post("/v3/finance/transaction/list", body)
            result = res.get("result") or {}
            ops = result.get("operations") or []
            all_ops.extend(ops)
            page_count = int(result.get("page_count") or 1)
            if not ops or page >= page_count:
                break
            page += 1
    return all_ops


def _fetch_products_by_sku(skus: set[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    ordered = [int(s) for s in sorted(skus) if str(s).isdigit()]
    for i in range(0, len(ordered), 50):
        batch = ordered[i : i + 50]
        if not batch:
            continue
        try:
            res = ozon_post("/v3/product/info/list", {"sku": batch, "offer_id": [], "product_id": []})
        except Exception:
            continue
        items = res.get("items") or (res.get("result") or {}).get("items") or []
        for item in items:
            sku = str(item.get("sku") or "")
            if sku:
                out[sku] = item
            for source in item.get("sources") or []:
                src_sku = str(source.get("sku") or "")
                if src_sku:
                    out[src_sku] = item
    return out


def _catalog_info(offer_id: str) -> dict[str, Any]:
    if not offer_id:
        return {"cost_cny": 0.0, "matched": False, "image_url": "", "match_key": ""}
    lookup = cat_mod.lookup_sku(offer_id)
    if not lookup or not lookup.get("found"):
        return {"cost_cny": 0.0, "matched": False, "image_url": "", "match_key": offer_id}
    item = lookup.get("item") or {}
    image_url = ""
    for block_name in ("ozon", "tiktok", "shopee"):
        block = item.get(block_name) or {}
        image_url = block.get("image_url") or image_url
        if image_url:
            break
    return {
        "cost_cny": _num(item.get("cost_cny")),
        "matched": item.get("cost_cny") is not None,
        "image_url": image_url,
        "match_key": item.get("match_key") or lookup.get("match_key") or offer_id,
    }


def summarize_transactions(
    ops: list[dict[str, Any]],
    *,
    only_settled: bool = True,
    fx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fx = fx or _fx_payload()
    cny_per_rub = _num(fx.get("cny_per_rub")) or DEFAULT_CNY_PER_RUB
    by_posting: dict[str, dict[str, Any]] = defaultdict(lambda: {"ops": [], "items": {}})
    fee_type_totals: dict[str, float] = defaultdict(float)
    fee_type_count: dict[str, int] = defaultdict(int)
    category_totals: dict[str, float] = defaultdict(float)
    all_skus: set[str] = set()

    for op in ops:
        posting = (op.get("posting") or {}).get("posting_number") or "(no posting)"
        by_posting[posting]["ops"].append(op)
        type_name = op.get("operation_type_name") or op.get("operation_type") or "未知"
        amount = _num(op.get("amount"))
        fee_type_totals[type_name] += amount
        fee_type_count[type_name] += 1
        category = _category_for_op(op)
        if category == "sale":
            category_totals["sale"] += _num(op.get("accruals_for_sale")) or amount
        else:
            category_totals[category] += amount
        if _num(op.get("sale_commission")):
            category_totals["commission"] += _num(op.get("sale_commission"))
        for item in op.get("items") or []:
            sku = str(item.get("sku") or "")
            if sku:
                all_skus.add(sku)
                by_posting[posting]["items"][sku] = item.get("name") or by_posting[posting]["items"].get(sku) or ""

    product_by_sku = _fetch_products_by_sku(all_skus)

    orders: list[dict[str, Any]] = []
    for posting, data in by_posting.items():
        order_ops = data["ops"]
        sale_ops = [op for op in order_ops if _category_for_op(op) == "sale" and _num(op.get("accruals_for_sale")) > 0]
        settled = bool(sale_ops)
        if only_settled and not settled:
            continue

        op_dates = [op.get("operation_date") for op in order_ops if op.get("operation_date")]
        post_dates = [(op.get("posting") or {}).get("order_date") for op in order_ops if (op.get("posting") or {}).get("order_date")]
        order_date = _date_only(min(post_dates or op_dates)) if (post_dates or op_dates) else ""
        settlement_date = _date_only(min(op_dates)) if op_dates else ""

        sale_price_rub = sum(_num(op.get("accruals_for_sale")) for op in sale_ops)
        commission_rub = sum(_num(op.get("sale_commission")) for op in sale_ops)
        fees: dict[str, float] = {
            "commission": commission_rub,
            "logistics": 0.0,
            "storage": 0.0,
            "advertising": 0.0,
            "acquiring": 0.0,
            "agent_fee": 0.0,
            "other": 0.0,
        }
        fee_rows: list[dict[str, Any]] = []
        if commission_rub:
            fee_rows.append({"category": "commission", "label": _fee_label("commission"), "amount_rub": _round(commission_rub), "count": len(sale_ops)})

        for op in order_ops:
            cat = _category_for_op(op)
            if cat == "sale":
                continue
            amount = _num(op.get("amount"))
            if cat not in fees:
                cat = "other"
            fees[cat] += amount
            fee_rows.append(
                {
                    "category": cat,
                    "label": _fee_label(cat),
                    "type_name": op.get("operation_type_name") or op.get("operation_type") or "",
                    "amount_rub": _round(amount),
                    "date": op.get("operation_date") or "",
                }
            )

        advertising_rub = -round(sale_price_rub * 0.22, 2)
        fees["advertising"] = advertising_rub
        category_totals["advertising"] = advertising_rub
        fee_rows = [row for row in fee_rows if row.get("category") != "advertising"]
        if advertising_rub:
            fee_rows.append(
                {
                    "category": "advertising",
                    "label": _fee_label("advertising"),
                    "type_name": "销售价×22%",
                    "amount_rub": _round(advertising_rub),
                    "count": len(sale_ops),
                }
            )

        skus = sorted(data["items"].keys())
        product_names: list[str] = []
        offer_ids: list[str] = []
        image_url = ""
        cost_cny = 0.0
        cost_matched = False
        match_keys: list[str] = []
        seller_skus: list[str] = []
        for sku in skus:
            raw_name = data["items"].get(sku) or ""
            info = product_by_sku.get(sku) or {}
            name = info.get("name") or raw_name
            if name:
                product_names.append(str(name))
            offer_id = str(info.get("offer_id") or "")
            if offer_id:
                offer_ids.append(offer_id)
                seller_skus.append(offer_id)
                cat_info = _catalog_info(offer_id)
                cost_cny += _num(cat_info.get("cost_cny"))
                cost_matched = bool(cat_info.get("matched")) or cost_matched
                if cat_info.get("match_key"):
                    match_keys.append(str(cat_info["match_key"]))
                image_url = image_url or _image_from_product(info) or str(cat_info.get("image_url") or "")
            else:
                image_url = image_url or _image_from_product(info)

        net_before_cost_rub = sale_price_rub + sum(fees.values())
        net_before_cost_cny = net_before_cost_rub * cny_per_rub
        profit_cny = net_before_cost_cny - cost_cny
        margin_pct = (profit_cny / (sale_price_rub * cny_per_rub) * 100) if sale_price_rub else None

        orders.append(
            {
                "posting_number": posting,
                "products": sorted(set(product_names)),
                "image_url": image_url,
                "skus": skus,
                "seller_skus": sorted(set(seller_skus)),
                "offer_ids": sorted(set(offer_ids)),
                "match_keys": sorted(set(match_keys)),
                "settled": settled,
                "order_date": order_date,
                "settlement_date": settlement_date,
                "sale_price_rub": _round(sale_price_rub),
                "commission_rub": _round(fees["commission"]),
                "logistics_rub": _round(fees["logistics"]),
                "storage_rub": _round(fees["storage"]),
                "advertising_rub": _round(fees["advertising"]),
                "acquiring_rub": _round(fees["acquiring"]),
                "agent_fee_rub": _round(fees["agent_fee"]),
                "other_fee_rub": _round(fees["other"]),
                "net_amount": _round(sum(_num(op.get("amount")) for op in order_ops)),
                "net_before_cost_rub": _round(net_before_cost_rub),
                "net_before_cost_cny": _round(net_before_cost_cny),
                "cost_cny": _round(cost_cny),
                "cost_matched": cost_matched,
                "profit_cny": _round(profit_cny),
                "margin_pct": round(margin_pct, 1) if margin_pct is not None else None,
                "fee_rows": fee_rows,
                "operations": [
                    {
                        "date": op.get("operation_date"),
                        "type": op.get("operation_type"),
                        "type_name": op.get("operation_type_name"),
                        "amount": op.get("amount"),
                    }
                    for op in order_ops
                ],
            }
        )

    orders.sort(key=lambda row: (row["order_date"], row["posting_number"]), reverse=True)
    settled_orders = [row for row in orders if row["settled"]]
    pending_count = len([posting for posting, data in by_posting.items() if not any(_category_for_op(op) == "sale" and _num(op.get("accruals_for_sale")) > 0 for op in data["ops"])])

    fee_breakdown = [
        {"type_name": name, "count": fee_type_count[name], "total": _round(total)}
        for name, total in sorted(fee_type_totals.items(), key=lambda item: item[1])
    ]
    category_breakdown = [
        {"category": cat, "label": _fee_label(cat), "total_rub": _round(total), "total_cny": _round(total * cny_per_rub)}
        for cat, total in sorted(category_totals.items(), key=lambda item: item[0])
    ]

    return {
        "orders": orders,
        "settled_count": len(settled_orders),
        "pending_count": pending_count,
        "fee_breakdown": fee_breakdown,
        "category_breakdown": category_breakdown,
        "grand_total": _round(sum(row["net_before_cost_rub"] for row in orders)),
        "settled_net_total": _round(sum(row["net_before_cost_rub"] for row in settled_orders)),
        "sale_total_rub": _round(sum(row["sale_price_rub"] for row in settled_orders)),
        "cost_total_cny": _round(sum(row["cost_cny"] for row in settled_orders)),
        "profit_total_cny": _round(sum(row["profit_cny"] for row in settled_orders)),
        "avg_margin_pct": round(
            sum(row["profit_cny"] for row in settled_orders)
            / max(sum(row["sale_price_rub"] * cny_per_rub for row in settled_orders), 0.000001)
            * 100,
            1,
        )
        if settled_orders
        else None,
        "missing_cost_count": sum(1 for row in settled_orders if not row["cost_matched"]),
        "fx": fx,
    }


def build_settlement_summary(
    months_back: int = 3,
    only_settled: bool = True,
    *,
    weeks_back: int | None = None,
    force_fx_refresh: bool = False,
) -> dict[str, Any]:
    now = datetime.utcnow()
    if weeks_back is not None:
        window_weeks = max(1, min(int(weeks_back or 1), 26))
        date_from = now - timedelta(days=7 * window_weeks)
        period_mode = "weeks"
        period_value = window_weeks
    else:
        window_months = max(1, min(int(months_back or 3), 12))
        date_from = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
        for _ in range(window_months - 1):
            date_from = (date_from - timedelta(days=1)).replace(day=1)
        period_mode = "months"
        period_value = window_months

    fx = _fx_payload(force_refresh=force_fx_refresh)
    ops = fetch_transactions(date_from, now)
    summary = summarize_transactions(ops, only_settled=only_settled, fx=fx)
    summary["date_from"] = date_from.strftime("%Y-%m-%d")
    summary["date_to"] = now.strftime("%Y-%m-%d")
    summary["only_settled"] = only_settled
    summary["period_mode"] = period_mode
    summary["period_value"] = period_value
    summary["raw_operation_count"] = len(ops)
    return summary
