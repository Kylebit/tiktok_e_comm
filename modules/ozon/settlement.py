"""Ozon 结算汇总：拉取 /v3/finance/transaction/list，按订单和费用类型汇总。

Ozon 接口单次查询时间跨度不能超过一个月，所以按月分页拉取后再在本地合并。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from modules.ozon.client import ozon_post
from modules.ozon.profit_analysis import (
    ACQUIRING_RATE,
    AD_RATE,
    AGENT_FEE_RUB,
    COMMISSION_RATE,
    RUB_PER_CNY,
)

_FEE_KEYS = ("commission", "logistics", "acquiring", "ad", "agent_fee")


def _month_ranges(start: datetime, end: datetime):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        range_start = datetime(y, m, 1)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        range_end = min(datetime(ny, nm, 1) - timedelta(seconds=1), end)
        yield max(range_start, start), range_end
        y, m = ny, nm


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
            page_count = result.get("page_count", 1)
            if not ops or page >= page_count:
                break
            page += 1
    return all_ops


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _fee_bucket(name: str) -> str | None:
    text = (name or "").lower()
    if not text:
        return None
    if "комис" in text or "commission" in text:
        return "commission"
    if "логист" in text or "достав" in text or "delivery" in text or "logistic" in text:
        return "logistics"
    if "эквайр" in text or "acquiring" in text:
        return "acquiring"
    if "реклам" in text or "advert" in text or "cpo" in text or "трафар" in text:
        return "ad"
    if "услуг" in text or "service" in text:
        return "agent_fee"
    return None


def _item_offer_id(item: dict[str, Any]) -> str:
    for key in ("offer_id", "seller_sku"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _item_sku(item: dict[str, Any]) -> int | None:
    for key in ("sku", "product_sku"):
        try:
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return None


def _sale_price_from_ops(ops: list[dict[str, Any]]) -> float:
    candidates: list[float] = []
    for op in ops:
        for key in ("accruals_for_sale", "sale_price", "price"):
            value = _to_float(op.get(key))
            if value > 0:
                candidates.append(value)
        for item in op.get("items") or []:
            for key in ("price", "amount"):
                value = _to_float(item.get(key))
                if value > 0:
                    candidates.append(value)
    return round(max(candidates), 2) if candidates else 0.0


def _fees_from_ops(ops: list[dict[str, Any]], sale_price_rub: float) -> dict[str, float]:
    fees = {key: 0.0 for key in _FEE_KEYS}
    for op in ops:
        op_type = op.get("operation_type_name") or op.get("operation_type") or ""
        saw_service_fee = False
        for service in op.get("services") or []:
            bucket = _fee_bucket(str(service.get("name") or service.get("type") or op_type))
            if not bucket:
                continue
            fees[bucket] += abs(_to_float(service.get("price")))
            saw_service_fee = True

        if saw_service_fee:
            continue
        bucket = _fee_bucket(str(op_type))
        amount = _to_float(op.get("amount"))
        if bucket and amount < 0:
            fees[bucket] += abs(amount)

    if sale_price_rub > 0:
        fees["commission"] = fees["commission"] or round(sale_price_rub * COMMISSION_RATE, 2)
        fees["acquiring"] = fees["acquiring"] or round(sale_price_rub * ACQUIRING_RATE, 2)
        fees["ad"] = fees["ad"] or round(sale_price_rub * AD_RATE, 2)
    fees["agent_fee"] = fees["agent_fee"] or float(AGENT_FEE_RUB)
    return {key: round(value, 2) for key, value in fees.items()}


def _fetch_product_details(
    offer_ids: set[str],
    skus: set[int],
    *,
    fetcher=ozon_post,
) -> dict[str, dict[str, Any]]:
    if not offer_ids and not skus:
        return {}
    details: dict[str, dict[str, Any]] = {}
    offer_list = sorted(x for x in offer_ids if x)
    sku_list = sorted(x for x in skus if x)
    for i in range(0, max(len(offer_list), len(sku_list), 1), 100):
        body = {
            "offer_id": offer_list[i : i + 100],
            "sku": sku_list[i : i + 100],
            "product_id": [],
        }
        if not body["offer_id"] and not body["sku"]:
            continue
        try:
            res = fetcher("/v3/product/info/list", body)
        except Exception:
            continue
        items = res.get("items") or res.get("result", {}).get("items") or []
        for item in items:
            offer_id = str(item.get("offer_id") or "").strip()
            if offer_id:
                details[f"offer:{offer_id}"] = item
            sku = _item_sku(item)
            if sku:
                details[f"sku:{sku}"] = item
    return details


def _catalog_snapshot(match_key: str) -> dict[str, Any]:
    if not match_key:
        return {}
    try:
        from modules.catalog import listings as cat_mod

        lookup = cat_mod.lookup_sku(match_key)
    except Exception:
        return {}
    if not lookup or not lookup.get("found"):
        return {}
    item = lookup.get("item") or {}
    image = ""
    title = ""
    tiktok = item.get("tiktok") or {}
    shopee = item.get("shopee") or {}
    for block in (tiktok, shopee, item.get("ozon") or {}):
        if isinstance(block, dict):
            image = image or str(block.get("image_url") or "")
            title = title or str(block.get("product_name") or "")
    return {"cost_cny": item.get("cost_cny"), "image": image, "title": title}


def _match_key_from_offer_id(offer_id: str) -> str:
    digits = "".join(ch for ch in str(offer_id or "") if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(4)[-4:]


def _first_image(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                return item
    return ""


def summarize_transactions(
    ops: list[dict[str, Any]],
    *,
    product_detail_fetcher=ozon_post,
) -> dict[str, Any]:
    by_posting: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"ops": [], "items": set(), "offer_ids": set(), "skus": set()}
    )
    fee_type_totals: dict[str, float] = defaultdict(float)
    fee_type_count: dict[str, int] = defaultdict(int)

    for op in ops:
        posting = (op.get("posting") or {}).get("posting_number") or "(无订单关联/服务费)"
        by_posting[posting]["ops"].append(op)
        for item in op.get("items") or []:
            name = item.get("name")
            if name:
                by_posting[posting]["items"].add(name)
            offer_id = _item_offer_id(item)
            if offer_id:
                by_posting[posting]["offer_ids"].add(offer_id)
            sku = _item_sku(item)
            if sku:
                by_posting[posting]["skus"].add(sku)
        type_name = op.get("operation_type_name") or op.get("operation_type") or "未知"
        amount = float(op.get("amount") or 0)
        fee_type_totals[type_name] += amount
        fee_type_count[type_name] += 1

    all_offer_ids: set[str] = set()
    all_skus: set[int] = set()
    for data in by_posting.values():
        all_offer_ids.update(data["offer_ids"])
        all_skus.update(data["skus"])
    product_details = _fetch_product_details(all_offer_ids, all_skus, fetcher=product_detail_fetcher)

    orders = []
    for posting, data in by_posting.items():
        total = sum(float(o.get("amount") or 0) for o in data["ops"])
        delivered = any(
            (o.get("operation_type_name") or "").startswith("Доставка") for o in data["ops"]
        )
        offer_id = next(iter(data["offer_ids"]), "")
        sku = next(iter(data["skus"]), None)
        detail = product_details.get(f"offer:{offer_id}") if offer_id else None
        if not detail and sku:
            detail = product_details.get(f"sku:{sku}")
        detail = detail or {}
        match_key = _match_key_from_offer_id(offer_id or detail.get("offer_id") or "")
        catalog = _catalog_snapshot(match_key)
        product_image = _first_image(detail.get("primary_image")) or _first_image(detail.get("images")) or str(catalog.get("image") or "")
        products = sorted(data["items"])
        product_name = (detail.get("name") or catalog.get("title") or (products[0] if products else ""))
        sale_price_rub = _sale_price_from_ops(data["ops"])
        if not sale_price_rub:
            sale_price_rub = _to_float(detail.get("price"))
        fees_rub = _fees_from_ops(data["ops"], sale_price_rub)
        cost_cny = catalog.get("cost_cny")
        try:
            cost_cny = float(cost_cny) if cost_cny is not None else None
        except (TypeError, ValueError):
            cost_cny = None
        fee_cny = {f"{key}_cny": round(value / RUB_PER_CNY, 2) for key, value in fees_rub.items()}
        sale_price_cny = round(sale_price_rub / RUB_PER_CNY, 2) if sale_price_rub else 0.0
        if cost_cny is None:
            profit_cny = None
            margin_pct = None
        else:
            total_fee_cny = sum(fee_cny.values())
            profit_cny = round(sale_price_cny - cost_cny - total_fee_cny, 2)
            margin_pct = round(profit_cny / sale_price_cny * 100, 1) if sale_price_cny else None
        orders.append(
            {
                "posting_number": posting,
                "offer_id": offer_id,
                "sku": sku,
                "match_key": match_key,
                "products": products,
                "product_name": product_name,
                "product_image": product_image,
                "settled": delivered,
                "net_amount": round(total, 2),
                "sale_price_rub": sale_price_rub,
                "sale_price_cny": sale_price_cny,
                "cost_cny": round(cost_cny, 2) if cost_cny is not None else None,
                "commission": fees_rub["commission"],
                "logistics": fees_rub["logistics"],
                "acquiring": fees_rub["acquiring"],
                "ad": fees_rub["ad"],
                "agent_fee": fees_rub["agent_fee"],
                **fee_cny,
                "profit_cny": profit_cny,
                "margin_pct": margin_pct,
                "operations": [
                    {
                        "date": o.get("operation_date"),
                        "type_name": o.get("operation_type_name"),
                        "amount": o.get("amount"),
                    }
                    for o in data["ops"]
                ],
            }
        )
    orders.sort(key=lambda o: o["operations"][0]["date"] if o["operations"] else "")

    fee_breakdown = [
        {"type_name": name, "count": fee_type_count[name], "total": round(total, 2)}
        for name, total in sorted(fee_type_totals.items(), key=lambda x: x[1])
    ]

    settled_orders = [o for o in orders if o["settled"]]
    pending_orders = [o for o in orders if not o["settled"]]

    return {
        "orders": orders,
        "settled_count": len(settled_orders),
        "pending_count": len(pending_orders),
        "fee_breakdown": fee_breakdown,
        "grand_total": round(sum(fee_type_totals.values()), 2),
        "settled_net_total": round(sum(o["net_amount"] for o in settled_orders), 2),
    }


def build_settlement_summary(months_back: int = 3) -> dict[str, Any]:
    now = datetime.utcnow()
    date_from = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    for _ in range(months_back - 1):
        date_from = (date_from - timedelta(days=1)).replace(day=1)
    ops = fetch_transactions(date_from, now)
    summary = summarize_transactions(ops)
    summary["date_from"] = date_from.strftime("%Y-%m-%d")
    summary["date_to"] = now.strftime("%Y-%m-%d")
    return summary
