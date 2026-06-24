"""TikTok 当地售价 → Shopee CNSC 全球价（人民币）。"""

from __future__ import annotations

from modules.finance.profit_engine import exchange_rate_for

REGION_CURRENCY = {"MY": "MYR", "VN": "VND", "TH": "THB", "PH": "PHP"}


def tk_local_to_cny(
    local_price: float,
    *,
    region: str = "",
    currency: str = "",
) -> float:
    """当地货币售价 × settings.exchange_rates → 全球商品人民币价。"""
    cur = (currency or REGION_CURRENCY.get((region or "").upper(), "")).upper()
    if not cur:
        raise RuntimeError(f"未知站点货币: {region}")
    rate = exchange_rate_for(cur)
    if rate <= 0:
        raise RuntimeError(f"请在 config/settings.json 配置 exchange_rates.{cur}")
    return round(float(local_price) * rate, 2)
