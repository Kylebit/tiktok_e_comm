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
