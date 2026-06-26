"""Ozon 利润分析：真实生效价(含弹性提升折扣) + 保最低利润率的 min_price 草稿。

所有费率均来自真实结算数据验证(见 settlement.py 同期分析)：
- 佣金 12%（两单实测结算一致）
- 国际物流代理费 = 3 CNY + 0.045 CNY/克 (实测验证，误差<1.5%) + 固定 15 RUB 代理服务费
- 收单手续费(эквайринга) 约 2.5%（实测样本范围估算）
- CPO 按单付费推广 22%（用户后台截图实测：出价22% = 实际花费率22.0%，精确验证）
"""

from __future__ import annotations

from modules.catalog import listings as cat_mod
from modules.catalog.sku_key import tk_match_key
from modules.ozon.client import ozon_post

ELASTIC_BOOST_ACTION_ID = 1977747

RUB_PER_CNY = 191.0 / 18.0  # 来自2单已完整结算订单反推
COMMISSION_RATE = 0.12
ACQUIRING_RATE = 0.025
AD_RATE = 0.22
AGENT_FEE_RUB = 15.0
_FALLBACK_LOGISTICS_CNY = (15 + 78.42 + 15 + 79.37) / 2 / RUB_PER_CNY


def logistics_cny(weight_g: int | float | None) -> float:
    agent_fee_cny = AGENT_FEE_RUB / RUB_PER_CNY
    if weight_g:
        delivery_cny = 3 + 0.045 * float(weight_g)
    else:
        delivery_cny = _FALLBACK_LOGISTICS_CNY - agent_fee_cny
    return round(delivery_cny + agent_fee_cny, 2)


def _fetch_active_offers() -> list[dict]:
    all_offers = []
    last_id = ""
    while True:
        body = {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": 1000}
        res = ozon_post("/v3/product/list", body)
        items = res.get("result", {}).get("items", [])
        all_offers.extend(items)
        last_id = res.get("result", {}).get("last_id", "")
        if not last_id or not items:
            break
    return all_offers


def _fetch_item_details(offer_ids: list[str]) -> list[dict]:
    all_items: list[dict] = []
    for i in range(0, len(offer_ids), 50):
        batch = offer_ids[i : i + 50]
        res = ozon_post("/v3/product/info/list", {"offer_id": batch, "sku": [], "product_id": []})
        items = res.get("items") or res.get("result", {}).get("items") or []
        all_items.extend(items)
    return all_items


def _fetch_elastic_boost_active() -> dict[int, dict]:
    all_products: list[dict] = []
    offset = 0
    while True:
        res = ozon_post(
            "/v1/actions/products",
            {"action_id": ELASTIC_BOOST_ACTION_ID, "limit": 100, "offset": offset},
        )
        products = res.get("result", {}).get("products", [])
        all_products.extend(products)
        if len(products) < 100:
            break
        offset += 100
    return {p["id"]: p for p in all_products}


def build_profit_table(target_margin: float = 0.05, *, excluded_offer_ids: set[str] | None = None) -> dict:
    excluded = excluded_offer_ids or set()
    offers = _fetch_active_offers()
    offer_ids = [o["offer_id"] for o in offers]
    items = _fetch_item_details(offer_ids)
    elastic_by_id = _fetch_elastic_boost_active()
    weight_idx = cat_mod.weight_index_by_match_key()

    variable_rate_sum = COMMISSION_RATE + ACQUIRING_RATE + AD_RATE + target_margin
    denom = 1 - variable_rate_sum

    rows = []
    for it in items:
        offer_id = it.get("offer_id")
        if it.get("is_archived") or offer_id in excluded:
            continue
        list_price = float(it.get("price") or 0)
        if list_price <= 0:
            continue

        pid = it.get("id")
        ep = elastic_by_id.get(pid)
        if ep and ep.get("action_price"):
            real_price = float(ep["action_price"])
            in_boost = True
            boost_pct = ep.get("current_boost")
        else:
            real_price = list_price
            in_boost = False
            boost_pct = None

        mk = tk_match_key(offer_id) or offer_id
        lookup = cat_mod.lookup_sku(mk)
        cost_cny = None
        if lookup and lookup.get("found"):
            cost_cny = lookup["item"].get("cost_cny")
        wi = weight_idx.get(mk)
        weight_g = wi.get("weight_g") if wi else None
        weight_source = wi.get("weight_source") if wi else None
        log_cny = logistics_cny(weight_g)

        commission_cny = round(real_price * COMMISSION_RATE, 2)
        acquiring_cny = round(real_price * ACQUIRING_RATE, 2)
        ad_cny = round(real_price * AD_RATE, 2)

        if cost_cny is None:
            profit_cny = margin_pct = min_price_draft = None
            needs_increase = None
            gap = None
        else:
            profit_cny = round(real_price - cost_cny - commission_cny - log_cny - acquiring_cny - ad_cny, 2)
            margin_pct = round(profit_cny / real_price * 100, 1) if real_price else None
            min_price_draft = round((cost_cny + log_cny) / denom, 2)
            needs_increase = min_price_draft > list_price
            gap = round(min_price_draft - list_price, 2)

        image = (it.get("primary_image") or it.get("images") or [""])[0]

        rows.append(
            {
                "offer_id": offer_id,
                "name": it.get("name", ""),
                "image": image,
                "list_price_cny": list_price,
                "real_price_cny": real_price,
                "in_elastic_boost": in_boost,
                "boost_pct": boost_pct,
                "cost_cny": cost_cny,
                "weight_g": weight_g,
                "weight_source": weight_source,
                "commission_cny": commission_cny,
                "logistics_cny": log_cny,
                "acquiring_cny": acquiring_cny,
                "ad_cny": ad_cny,
                "profit_cny": profit_cny,
                "margin_pct": margin_pct,
                "min_price_draft": min_price_draft,
                "needs_increase": needs_increase,
                "price_gap": gap,
            }
        )

    rows.sort(key=lambda r: (r["profit_cny"] is None, r["profit_cny"] if r["profit_cny"] is not None else 0))

    priced = [r for r in rows if r["profit_cny"] is not None]
    losing = [r for r in priced if r["profit_cny"] <= 0]
    avg_margin = round(sum(r["margin_pct"] for r in priced) / len(priced), 1) if priced else None
    need_raise = [r for r in priced if r["needs_increase"]]

    return {
        "rows": rows,
        "target_margin_pct": round(target_margin * 100, 1),
        "rates": {
            "commission_pct": COMMISSION_RATE * 100,
            "acquiring_pct": ACQUIRING_RATE * 100,
            "ad_pct": AD_RATE * 100,
            "rub_per_cny": round(RUB_PER_CNY, 4),
        },
        "summary": {
            "total": len(priced),
            "losing_count": len(losing),
            "avg_margin_pct": avg_margin,
            "in_boost_count": sum(1 for r in priced if r["in_elastic_boost"]),
            "need_price_raise_count": len(need_raise),
            "missing_cost_count": len(rows) - len(priced),
        },
    }
