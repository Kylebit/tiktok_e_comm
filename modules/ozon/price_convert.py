"""TikTok 任意站点售价 → 人民币（config exchange_rates）。"""

from __future__ import annotations

from core.config import get

_DEFAULT_RATES = {
    "MYR": 1.75,
    "THB": 0.2218,
    "PHP": 0.118,
    "VND": 0.000266,
    "CNY": 1.0,
    "RMB": 1.0,
}


def exchange_rates() -> dict[str, float]:
    raw = get("exchange_rates") or {}
    out = dict(_DEFAULT_RATES)
    for k, v in raw.items():
        try:
            out[str(k).upper()] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def to_cny(amount: float | None, currency: str | None) -> float | None:
    if amount is None:
        return None
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    cur = (currency or "MYR").upper()
    rate = exchange_rates().get(cur)
    if not rate:
        return None
    return round(val * rate)


def old_price_cny(price_cny: float) -> int:
    return round(price_cny * 1.3)


def pick_tk_price(item: dict) -> dict | None:
    """取 TikTok 任意一国第一个有效价格（与站点无关）。"""
    tk = item.get("tiktok")
    if not tk:
        return None
    for row in tk.get("regions") or []:
        cny = to_cny(row.get("price"), row.get("currency"))
        if cny is None:
            continue
        reg = (row.get("region") or "?").upper()
        return {
            "amount": float(row["price"]),
            "currency": (row.get("currency") or reg).upper(),
            "cny": cny,
            "source": f"tiktok_{reg}",
            "label": f"TK {reg} {row['price']} {row.get('currency') or ''}".strip(),
        }
    return None


# 兼容旧名
pick_listing_price = pick_tk_price


# Ozon 20% 目标利润率定价（与 profit_analysis.py 结算验证一致）
OZON_COMMISSION_RATE = 0.12
OZON_ACQUIRING_RATE = 0.025
OZON_AD_RATE = 0.22
OZON_TARGET_MARGIN = 0.20
OZON_RUB_PER_CNY = 191.0 / 18.0
OZON_AGENT_FEE_RUB = 15.0


def ozon_logistics_cny(weight_g: int | float | None) -> float:
    agent_fee_cny = OZON_AGENT_FEE_RUB / OZON_RUB_PER_CNY
    if weight_g:
        delivery_cny = 3 + 0.045 * float(weight_g)
    else:
        delivery_cny = 3.0
    return round(delivery_cny + agent_fee_cny, 2)


def ozon_price_formula(
    *,
    cost_cny: float | None,
    weight_g: int | float | None = None,
    tk_price_cny: float | None = None,
    target_margin: float = OZON_TARGET_MARGIN,
) -> dict:
    """Ozon 售价：price_rub = round((cost+logistics)/0.435/RUB_PER_CNY)，返回完整定价链。"""
    cost = float(cost_cny) if cost_cny is not None else None
    if cost is None or cost <= 0:
        cost = float(tk_price_cny) if tk_price_cny else None
    logistics = ozon_logistics_cny(weight_g)
    denom = 1 - OZON_COMMISSION_RATE - OZON_ACQUIRING_RATE - OZON_AD_RATE - target_margin
    if not cost or cost <= 0 or denom <= 0:
        return {
            "cost_cny": cost,
            "logistics_cny": logistics,
            "commission_cny": None,
            "acquiring_cny": None,
            "ad_cny": None,
            "target_profit_cny": None,
            "price_cny": None,
            "price_rub": None,
            "margin_pct": round(target_margin * 100, 1),
            "old_price_cny": None,
            "formula_denom": round(denom, 4),
            "rub_per_cny": round(OZON_RUB_PER_CNY, 4),
            "tk_price_cny": tk_price_cny,
            "weight_g": weight_g,
            "source": "missing_cost",
        }

    price_cny = round((cost + logistics) / denom, 2)
    price_rub = round(price_cny / OZON_RUB_PER_CNY)
    min_price_cny = price_cny
    if tk_price_cny and tk_price_cny > price_cny:
        price_cny = round(float(tk_price_cny), 2)
        price_rub = round(price_cny / OZON_RUB_PER_CNY)

    commission_cny = round(price_cny * OZON_COMMISSION_RATE, 2)
    acquiring_cny = round(price_cny * OZON_ACQUIRING_RATE, 2)
    ad_cny = round(price_cny * OZON_AD_RATE, 2)
    target_profit_cny = round(price_cny * target_margin, 2)
    profit_cny = round(
        price_cny - cost - logistics - commission_cny - acquiring_cny - ad_cny,
        2,
    )
    margin_pct = round(profit_cny / price_cny * 100, 1) if price_cny else None

    return {
        "cost_cny": round(cost, 2),
        "logistics_cny": logistics,
        "commission_cny": commission_cny,
        "acquiring_cny": acquiring_cny,
        "ad_cny": ad_cny,
        "target_profit_cny": target_profit_cny,
        "profit_cny": profit_cny,
        "price_cny": price_cny,
        "price_rub": price_rub,
        "min_price_cny": min_price_cny,
        "margin_pct": margin_pct,
        "old_price_cny": old_price_cny(price_cny),
        "formula_denom": round(denom, 4),
        "rub_per_cny": round(OZON_RUB_PER_CNY, 4),
        "tk_price_cny": tk_price_cny,
        "weight_g": weight_g,
        "source": "ozon_formula",
    }
