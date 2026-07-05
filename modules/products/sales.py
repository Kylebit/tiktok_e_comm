"""从订单 API 统计商品动销。"""

from __future__ import annotations

import time
from collections import defaultdict

from core import auth, shops
from core.api_client import post

ORDER_SEARCH = "/order/202309/orders/search"


def fetch_orders(access_token: str, shop_cipher: str, days: int = 30) -> list[dict]:
    now = int(time.time())
    start = now - days * 86400
    body = {"create_time_ge": start, "create_time_lt": now}
    orders: list[dict] = []
    page_token = ""
    while True:
        qp = {"shop_cipher": shop_cipher, "page_size": "100"}
        if page_token:
            qp["page_token"] = page_token
        result = post(ORDER_SEARCH, access_token, qp, body)
        if result.get("code") != 0:
            raise RuntimeError(result.get("message", "订单搜索失败"))
        data = result.get("data") or {}
        orders.extend(data.get("orders") or [])
        page_token = data.get("next_page_token") or ""
        if not page_token:
            break
        time.sleep(0.2)
    return orders


def aggregate_product_sales(
    access_token: str | None = None,
    days: int = 30,
    region: str | None = None,
) -> dict[tuple[str, str], dict]:
    """返回 (product_id, shop_cipher) -> {units, orders, region}."""
    token = access_token or auth.access_token()
    shop_list = shops.list_shops(token)
    stats: dict[tuple[str, str], dict] = defaultdict(lambda: {"units": 0, "orders": 0, "region": ""})

    for shop in shop_list:
        reg = (shop.get("region") or "").upper()
        if region and reg != region.upper():
            continue
        cipher = shop.get("cipher") or shop.get("shop_cipher", "")
        print(f"  拉订单 {shop.get('name')} [{reg}] {days}天...", end=" ", flush=True)
        try:
            orders = fetch_orders(token, cipher, days=days)
        except RuntimeError as e:
            print(f"失败: {e}")
            continue
        seen: dict[tuple[str, str], set] = defaultdict(set)
        for order in orders:
            oid = str(order.get("id", ""))
            for item in order.get("line_items") or []:
                pid = str(item.get("product_id") or "")
                if not pid:
                    continue
                key = (pid, cipher)
                stats[key]["units"] += 1
                stats[key]["region"] = reg
                seen[key].add(oid)
        for key, oids in seen.items():
            stats[key]["orders"] = len(oids)
        print(f"{len(orders)} 单")
        time.sleep(0.1)

    return dict(stats)
