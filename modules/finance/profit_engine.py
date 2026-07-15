"""订单利润计算（TikTok CURSOR 规则；Shopee 订单复用同一公式）。

Shopee：settlement_local = escrow_amount，ad_cost_local = campaign_fee（无广告 API 时为 0）。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config import get


@dataclass
class ProfitLine:
    settlement_local: float
    revenue_local: float
    subtotal_local: float
    product_cost_cny: float
    ad_cost_local: float
    exchange_rate: float
    is_local_shipping: bool
    local_shipping_fee_cny: float

    @property
    def profit_local(self) -> float:
        cost_local = self.product_cost_cny / self.exchange_rate if self.exchange_rate else 0
        fee = self.local_shipping_fee_cny / self.exchange_rate if (
            self.is_local_shipping and self.exchange_rate
        ) else 0
        return self.settlement_local - cost_local - self.ad_cost_local - fee

    @property
    def profit_cny(self) -> float:
        return (
            self.settlement_local * self.exchange_rate
            - self.product_cost_cny
            - self.ad_cost_local * self.exchange_rate
            - (self.local_shipping_fee_cny if self.is_local_shipping else 0)
        )

    @property
    def margin_pct(self) -> float | None:
        if not self.subtotal_local:
            return None
        return self.profit_local / self.subtotal_local * 100


def is_local_shipping_row(seller_shipping_fee: float, sst: float) -> bool:
    """CURSOR 规则：Seller shipping fee 与 SST 同时为 0 → 本土发货。"""
    return seller_shipping_fee == 0 and sst == 0


def exchange_rate_for(currency: str) -> float:
    rates = get("exchange_rates", {}) or {}
    return float(rates.get(currency, 0) or 0)


def calc_line(
    *,
    settlement_local: float,
    revenue_local: float,
    subtotal_local: float,
    product_cost_cny: float,
    ad_cost_local: float,
    currency: str,
    seller_shipping_fee: float = 0,
    sst: float = 0,
) -> ProfitLine:
    return ProfitLine(
        settlement_local=settlement_local,
        revenue_local=revenue_local,
        subtotal_local=subtotal_local,
        product_cost_cny=product_cost_cny,
        ad_cost_local=ad_cost_local,
        exchange_rate=exchange_rate_for(currency),
        is_local_shipping=is_local_shipping_row(seller_shipping_fee, sst),
        local_shipping_fee_cny=float(get("profit.local_shipping_fee_cny", 0) or 0),
    )


def allocate_ad_cost_to_orders(
    total_ad_spend_local: float,
    order_subtotals: list[float],
) -> list[float]:
    """将当日/当店广告总消耗按卖家折扣后小计比例分摊到各订单行。"""
    if not order_subtotals or total_ad_spend_local <= 0:
        return [0.0] * len(order_subtotals)
    s = sum(max(x, 0) for x in order_subtotals)
    if s <= 0:
        n = len(order_subtotals)
        return [total_ad_spend_local / n] * n
    return [total_ad_spend_local * max(st, 0) / s for st in order_subtotals]
