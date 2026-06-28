"""UK 4PL 直邮价卡 — 商家跨境物流成本 + 买家标准运费。

数据来源：平台公告 + income_20260627083452 结算回测（2026-06-27）。
新品售价测算请优先 import 本模块，勿硬编码费率。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "uk_4pl_pricing.json"

CargoKind = Literal["puhuo", "tehuo", "minhuo"]


@dataclass(frozen=True)
class ShippingBand:
    max_kg: float
    per_parcel: float
    per_kg: float


@dataclass(frozen=True)
class MerchantShippingQuote:
    cargo: CargoKind
    actual_kg: float
    volumetric_kg: float
    chargeable_kg: float
    band_max_kg: float
    per_parcel: float
    per_kg: float
    merchant_cost_gbp: float


@dataclass(frozen=True)
class UkPopDefaults:
    default_commission_pct: float
    vat_rate_pct: float
    smart_promotion_pct: float
    affiliate_pct: float
    ad_pct: float
    profit_pct: float
    seller_discount_pct: float
    buyer_pays_shipping: bool
    buyer_shipping_gbp: float
    cny_gbp_fallback: float


@lru_cache(maxsize=1)
def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def buyer_standard_shipping_gbp() -> float:
    return float(load_config()["buyer_shipping"]["standard_gbp_inc_vat"])


def min_chargeable_kg() -> float:
    return float(load_config()["merchant_billing"]["min_chargeable_g"]) / 1000.0


def volumetric_divisor() -> int:
    return int(load_config()["merchant_billing"]["volumetric_divisor"])


def _bands_for(cargo: CargoKind) -> list[ShippingBand]:
    cfg = load_config()
    key = cargo
    if key == "minhuo" and "same_as" in cfg["cargo_rates"]["minhuo"]:
        key = cfg["cargo_rates"]["minhuo"]["same_as"]
    raw = cfg["cargo_rates"][key]["bands"]
    return [ShippingBand(float(b["max_kg"]), float(b["per_parcel"]), float(b["per_kg"])) for b in raw]


def _pick_band(weight_kg: float, bands: list[ShippingBand]) -> ShippingBand:
    w = max(weight_kg, min_chargeable_kg())
    if w > bands[-1].max_kg:
        w = bands[-1].max_kg
    for band in bands:
        if w <= band.max_kg:
            return band
    return bands[-1]


def volumetric_kg(length_cm: float, width_cm: float, height_cm: float) -> float:
    return round(length_cm * width_cm * height_cm / volumetric_divisor(), 4)


def chargeable_kg(
    weight_kg: float,
    *,
    length_cm: float | None = None,
    width_cm: float | None = None,
    height_cm: float | None = None,
) -> float:
    actual = max(float(weight_kg), 0.0)
    vol = 0.0
    if length_cm and width_cm and height_cm:
        vol = volumetric_kg(length_cm, width_cm, height_cm)
    return max(actual, vol, min_chargeable_kg())


def merchant_shipping_gbp(
    weight_kg: float,
    *,
    cargo: CargoKind = "puhuo",
    length_cm: float | None = None,
    width_cm: float | None = None,
    height_cm: float | None = None,
) -> MerchantShippingQuote:
    """商家支付跨境物流成本（Standard 渠道）。"""
    billable = chargeable_kg(
        weight_kg, length_cm=length_cm, width_cm=width_cm, height_cm=height_cm
    )
    band = _pick_band(billable, _bands_for(cargo))
    cost = round(band.per_parcel + band.per_kg * billable, 4)
    vol = 0.0
    if length_cm and width_cm and height_cm:
        vol = volumetric_kg(length_cm, width_cm, height_cm)
    return MerchantShippingQuote(
        cargo=cargo,
        actual_kg=round(float(weight_kg), 4),
        volumetric_kg=vol,
        chargeable_kg=round(billable, 4),
        band_max_kg=band.max_kg,
        per_parcel=band.per_parcel,
        per_kg=band.per_kg,
        merchant_cost_gbp=round(cost, 2),
    )


def seller_net_shipping_gbp(
    merchant_cost_gbp: float,
    *,
    buyer_pays_shipping: bool | None = None,
    buyer_shipping_gbp: float | None = None,
) -> float:
    """结算口径卖家净运费：买家付运费时 Actual + Customer；此处用成本近似。"""
    cfg = load_config()["pop_defaults"]
    pays = cfg["buyer_pays_shipping"] if buyer_pays_shipping is None else buyer_pays_shipping
    buyer = float(buyer_shipping_gbp if buyer_shipping_gbp is not None else cfg["buyer_shipping_gbp"])
    if pays:
        return round(merchant_cost_gbp - buyer, 2)
    return round(merchant_cost_gbp, 2)


def pop_defaults() -> UkPopDefaults:
    d = load_config()["pop_defaults"]
    return UkPopDefaults(
        default_commission_pct=float(d.get("default_commission_pct") or 9),
        vat_rate_pct=float(d.get("vat_rate_pct") or 20),
        smart_promotion_pct=float(d["smart_promotion_pct"]),
        affiliate_pct=float(d["affiliate_pct"]),
        ad_pct=float(d["ad_pct"]),
        profit_pct=float(d["profit_pct"]),
        seller_discount_pct=float(d["seller_discount_pct"]),
        buyer_pays_shipping=bool(d["buyer_pays_shipping"]),
        buyer_shipping_gbp=float(d["buyer_shipping_gbp"]),
        cny_gbp_fallback=float(d["cny_gbp_fallback"]),
    )


def sale_price_gbp_from_cost(
    cost_gbp: float,
    merchant_logistics_gbp: float,
    *,
    params: UkPopDefaults | None = None,
    buyer_pays_shipping: bool | None = None,
    platform_commission_pct: float | None = None,
) -> tuple[float, dict[str, float]]:
    """粗算 VAT-inclusive 折后售价（含目标利润率）。"""
    from modules.pricing.uk_commission import vat_from_gross_gbp

    p = params or pop_defaults()
    comm = float(platform_commission_pct if platform_commission_pct is not None else p.default_commission_pct)
    ship_cost = merchant_logistics_gbp
    denom = (
        1.0
        - comm / 100.0
        - p.vat_rate_pct / (100.0 + p.vat_rate_pct)
        - p.smart_promotion_pct / 100.0
        - p.affiliate_pct / 100.0
        - p.ad_pct / 100.0
        - p.profit_pct / 100.0
    )
    if denom <= 0:
        raise ValueError(f"费率合计过高，分母 <= 0: {denom}")
    sale = round((cost_gbp + ship_cost) / denom, 2)
    ship_net = seller_net_shipping_gbp(
        merchant_logistics_gbp,
        buyer_pays_shipping=p.buyer_pays_shipping if buyer_pays_shipping is None else buyer_pays_shipping,
        buyer_shipping_gbp=p.buyer_shipping_gbp,
    )
    breakdown = {
        "cost_gbp": round(cost_gbp, 2),
        "merchant_logistics_gbp": round(merchant_logistics_gbp, 2),
        "seller_ship_net_gbp": round(ship_net, 2),
        "platform_commission_pct": comm,
        "platform_commission_gbp": round(sale * comm / 100.0, 2),
        "vat_gbp": vat_from_gross_gbp(sale),
        "smart_promotion_gbp": round(sale * p.smart_promotion_pct / 100.0, 2),
        "affiliate_gbp": round(sale * p.affiliate_pct / 100.0, 2),
        "ad_gbp": round(sale * p.ad_pct / 100.0, 2),
        "profit_gbp": round(sale * p.profit_pct / 100.0, 2),
    }
    return sale, breakdown


def list_price_gbp(sale_gbp: float, seller_discount_pct: float | None = None) -> tuple[float, int]:
    pct = pop_defaults().seller_discount_pct if seller_discount_pct is None else seller_discount_pct
    if pct < 0 or pct >= 100:
        raise ValueError("seller_discount_pct 应使用 0–100 的百分数")
    denom = 1.0 - pct / 100.0
    if denom <= 0:
        raise ValueError("折扣过高")
    listing = round(sale_gbp / denom, 2)
    return listing, int(math.ceil(listing))


def free_shipping_min_subtotal_gbp() -> float:
    cfg = load_config().get("free_shipping") or {}
    return float(cfg.get("min_subtotal_gbp") or 10.0)


def apply_free_ship_price_floor(
    sale_gbp: float,
    seller_discount_pct: float | None = None,
) -> tuple[float, float, int, bool]:
    """若 ceil 原价 × (1−折扣) 低于包邮线，抬价使买家实付 ≥ 满额包邮门槛。"""
    pct = pop_defaults().seller_discount_pct if seller_discount_pct is None else seller_discount_pct
    listing, listing_ceil = list_price_gbp(sale_gbp, pct)
    thresh = free_shipping_min_subtotal_gbp()
    denom = 1.0 - pct / 100.0
    buyer_pay = round(listing_ceil * denom, 2)
    if buyer_pay + 1e-9 >= thresh:
        return sale_gbp, listing, listing_ceil, False
    min_ceil = int(math.ceil(thresh / denom))
    listing_ceil = min_ceil
    listing = float(listing_ceil)
    sale_adj = round(listing * denom, 2)
    if sale_adj + 1e-9 < thresh:
        listing_ceil += 1
        listing = float(listing_ceil)
        sale_adj = round(listing * denom, 2)
    return sale_adj, listing, listing_ceil, True
