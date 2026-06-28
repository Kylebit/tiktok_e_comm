"""UK POP 跨境直邮定价（4PL 价卡 + 结算经验费率）。

用法:
  python scripts/uk_pop_pricing.py 0169 0003
  python scripts/uk_pop_pricing.py --all-ph
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import get
from core.db import connect, init_db
from modules.catalog.logistics_weights import lookup_stored, regional_seller_sku, weight_index_by_match_key
from modules.catalog.sku_key import tk_match_key
from modules.pricing.uk_4pl import (
    UkPopDefaults,
    apply_free_ship_price_floor,
    buyer_standard_shipping_gbp,
    list_price_gbp,
    merchant_shipping_gbp,
    pop_defaults,
    seller_net_shipping_gbp,
)
from modules.pricing.uk_commission import (
    commission_label,
    commission_pct,
    extract_category_from_product,
    vat_from_gross_gbp,
)
from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY, KNOWN_LOGISTICS

CargoKind = Literal["puhuo", "tehuo", "minhuo"]


@dataclass
class PopParams:
    qty: int = 1
    profit_pct: float = 17.0
    seller_discount_pct: float = 25.0
    affiliate_pct: float = 0.0
    ad_pct: float = 20.0
    platform_commission_pct: float = 9.0
    vat_rate_pct: float = 20.0
    smart_promotion_pct: float = 1.75
    buyer_pays_shipping: bool = False
    buyer_shipping_gbp: float = 3.99
    cargo: CargoKind = "puhuo"
    uk_category: str = ""
    uk_sub_category: str = ""

    @classmethod
    def from_defaults(cls, *, uk_category: str = "", uk_sub_category: str = "") -> PopParams:
        d = pop_defaults()
        comm = float(d.default_commission_pct)  # 固定 9%，不按类目表浮动
        return cls(
            profit_pct=d.profit_pct,
            seller_discount_pct=d.seller_discount_pct,
            affiliate_pct=d.affiliate_pct,
            ad_pct=d.ad_pct,
            platform_commission_pct=comm,
            vat_rate_pct=d.vat_rate_pct,
            smart_promotion_pct=d.smart_promotion_pct,
            buyer_pays_shipping=d.buyer_pays_shipping,
            buyer_shipping_gbp=d.buyer_shipping_gbp,
            uk_category=uk_category,
            uk_sub_category=uk_sub_category,
        )


@dataclass
class PopResult:
    seller_sku: str
    cost_cny: float
    cost_source: str
    weight_kg: float
    weight_source: str
    package_cm: str
    volumetric_kg: float
    billable_kg: float
    cargo: str
    merchant_logistics_gbp: float
    seller_ship_net_gbp: float
    buyer_shipping_gbp: float
    shipping_band_max_kg: float
    cny_gbp: float
    cost_gbp: float
    sale_price_gbp: float
    list_price_gbp: float
    list_price_ceil_gbp: int
    platform_commission_gbp: float
    vat_gbp: float
    smart_promotion_gbp: float
    affiliate_gbp: float
    ad_gbp: float
    net_income_gbp: float
    net_profit_gbp: float
    profit_margin_on_sale_pct: float
    steps: list[str]
    uk_category: str = ""
    uk_sub_category: str = ""
    commission_pct: float = 9.0
    commission_label: str = ""
    vat_rate_pct: float = 20.0


def fetch_cny_gbp() -> float:
    """返回 CNY/GBP（每 1 GBP 多少人民币），与 settings exchange_rates.GBP 一致。"""
    rates = get("exchange_rates") or {}
    if rates.get("GBP"):
        return float(rates["GBP"])
    try:
        req = urllib.request.Request(
            "https://api.frankfurter.app/latest?from=GBP&to=CNY",
            headers={"User-Agent": "tiktok_e_comm/uk_pop_pricing"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return round(float(data["rates"]["CNY"]), 4)
    except Exception:
        return 9.15


def _params_to_defaults(p: PopParams) -> UkPopDefaults:
    d = pop_defaults()
    return UkPopDefaults(
        default_commission_pct=p.platform_commission_pct,
        vat_rate_pct=p.vat_rate_pct,
        smart_promotion_pct=p.smart_promotion_pct,
        affiliate_pct=p.affiliate_pct,
        ad_pct=p.ad_pct,
        profit_pct=p.profit_pct,
        seller_discount_pct=p.seller_discount_pct,
        buyer_pays_shipping=p.buyer_pays_shipping,
        buyer_shipping_gbp=p.buyer_shipping_gbp,
        cny_gbp_fallback=fetch_cny_gbp(),
    )


def sale_price_from_cost(
    cost_gbp: float,
    merchant_logistics_gbp: float,
    p: PopParams,
) -> float:
    ship_cost = merchant_logistics_gbp
    vat_share = p.vat_rate_pct / (100.0 + p.vat_rate_pct)
    denom = (
        1.0
        - p.platform_commission_pct / 100.0
        - vat_share
        - p.smart_promotion_pct / 100.0
        - p.affiliate_pct / 100.0
        - p.ad_pct / 100.0
        - p.profit_pct / 100.0
    )
    if denom <= 0:
        raise ValueError(f"费率合计过高: {denom}")
    return round((p.qty * cost_gbp + ship_cost) / denom, 2)


def _verify_sale(
    sale: float,
    *,
    cost_gbp: float,
    merchant_logistics_gbp: float,
    p: PopParams,
) -> dict[str, float]:
    ship_net = seller_net_shipping_gbp(
        merchant_logistics_gbp,
        buyer_pays_shipping=p.buyer_pays_shipping,
        buyer_shipping_gbp=p.buyer_shipping_gbp,
    )
    ship_cost = merchant_logistics_gbp
    platform = round(sale * p.platform_commission_pct / 100.0, 2)
    vat = vat_from_gross_gbp(sale)
    smart = round(sale * p.smart_promotion_pct / 100.0, 2)
    affiliate = round(sale * p.affiliate_pct / 100.0, 2)
    ad = round(sale * p.ad_pct / 100.0, 2)
    income = round(
        sale - ship_cost - platform - vat - smart - affiliate - ad,
        2,
    )
    if p.buyer_pays_shipping:
        income = round(income + p.buyer_shipping_gbp, 2)
    profit = round(income - cost_gbp * p.qty, 2)
    margin = round(profit / sale * 100.0, 2) if sale else 0.0
    listing, listing_ceil = list_price_gbp(sale, p.seller_discount_pct)
    return {
        "seller_ship": round(ship_net, 2),
        "platform": platform,
        "vat": vat,
        "smart": smart,
        "affiliate": affiliate,
        "ad": ad,
        "income": income,
        "profit": profit,
        "margin": margin,
        "list_price": listing,
        "list_price_ceil": float(listing_ceil),
    }


def calc_pop_price(
    *,
    seller_sku: str,
    cost_cny: float,
    cost_source: str,
    weight_kg: float,
    weight_source: str,
    length_cm: float | None = None,
    width_cm: float | None = None,
    height_cm: float | None = None,
    cny_gbp: float | None = None,
    params: PopParams | None = None,
    cargo: CargoKind | None = None,
) -> PopResult:
    p = params or PopParams.from_defaults()
    if cargo:
        p.cargo = cargo
    rate = cny_gbp if cny_gbp is not None else fetch_cny_gbp()
    cost_gbp = round(cost_cny / rate, 2)

    pkg = "—"
    if length_cm and width_cm and height_cm:
        pkg = f"{int(length_cm)}×{int(width_cm)}×{int(height_cm)} cm"

    ship_q = merchant_shipping_gbp(
        weight_kg,
        cargo=p.cargo,
        length_cm=length_cm,
        width_cm=width_cm,
        height_cm=height_cm,
    )
    sale_raw = sale_price_from_cost(cost_gbp, ship_q.merchant_cost_gbp, p)
    sale, listing, listing_ceil, bumped = apply_free_ship_price_floor(sale_raw, p.seller_discount_pct)
    ship_cost = ship_q.merchant_cost_gbp
    v = _verify_sale(
        sale,
        cost_gbp=cost_gbp,
        merchant_logistics_gbp=ship_q.merchant_cost_gbp,
        p=p,
    )
    buyer = p.buyer_shipping_gbp if p.buyer_pays_shipping else 0.0
    comm_label = commission_label(category=p.uk_category, sub_category=p.uk_sub_category)
    if not p.uk_category:
        comm_label = f"flat {p.platform_commission_pct:g}%"

    steps = [
        f"① 成本 {cost_cny} CNY / {rate} = {cost_gbp} GBP（{cost_source}）",
        f"② 类目佣金 · {comm_label}",
        f"③ VAT {p.vat_rate_pct:g}%（含税售价）· 4PL {ship_q.merchant_cost_gbp} GBP · 计费 {ship_q.chargeable_kg}kg",
        (
            f"④ 卖家净运费 {v['seller_ship']} GBP（结算口径）"
            + (f"，买家付 GBP {buyer:.2f}" if p.buyer_pays_shipping else "（包邮）")
        ),
        (
            f"⑤ POP 折后 = ({p.qty}×{cost_gbp}+{ship_cost})"
            f" / (1-佣金-VAT-SmartPromo-达人-广告-利润) = {sale_raw} GBP"
        ),
    ]
    if bumped:
        steps.append(
            f"⑤b 包邮线抬价 · 折后 {sale_raw} → **{sale} GBP**"
            f"（ceil £{listing_ceil} × {100 - p.seller_discount_pct:g}% ≥ £10 包邮）"
        )
    steps.extend(
        [
        f"⑥ 折前原价 = {sale} / (1−{p.seller_discount_pct:g}%) = {listing} GBP → 上架 ceil = {listing_ceil} GBP",
        (
            f"⑦ 验算：4PL净 {v['seller_ship']} + VAT {v['vat']} + 佣金 {v['platform']}"
            f" + Smart {v['smart']} + 广告 {v['ad']} + 成本 {cost_gbp}"
            f" → 净利 {v['profit']} GBP（{v['margin']}%）"
        ),
        ]
    )

    return PopResult(
        seller_sku=seller_sku,
        cost_cny=cost_cny,
        cost_source=cost_source,
        weight_kg=weight_kg,
        weight_source=weight_source,
        package_cm=pkg,
        volumetric_kg=ship_q.volumetric_kg,
        billable_kg=ship_q.chargeable_kg,
        cargo=p.cargo,
        merchant_logistics_gbp=ship_q.merchant_cost_gbp,
        seller_ship_net_gbp=v["seller_ship"],
        buyer_shipping_gbp=buyer,
        shipping_band_max_kg=ship_q.band_max_kg,
        cny_gbp=rate,
        cost_gbp=cost_gbp,
        sale_price_gbp=sale,
        list_price_gbp=listing,
        list_price_ceil_gbp=int(listing_ceil),
        platform_commission_gbp=v["platform"],
        vat_gbp=v["vat"],
        smart_promotion_gbp=v["smart"],
        affiliate_gbp=v["affiliate"],
        ad_gbp=v["ad"],
        net_income_gbp=v["income"],
        net_profit_gbp=v["profit"],
        profit_margin_on_sale_pct=v["margin"],
        uk_category=p.uk_category,
        uk_sub_category=p.uk_sub_category,
        commission_pct=p.platform_commission_pct,
        commission_label=comm_label,
        vat_rate_pct=p.vat_rate_pct,
        steps=steps,
    )


def load_catalog_row(seller_sku: str) -> dict:
    init_db()
    row = connect().execute(
        """
        SELECT p.seller_sku, p.sku_id, p.product_id, p.price, p.product_name, sc.cost_cny, sc.note, s.region
        FROM products p
        JOIN shops s ON p.shop_cipher = s.cipher
        LEFT JOIN sku_costs sc ON sc.sku_id = p.sku_id
        WHERE p.seller_sku = ?
        LIMIT 1
        """,
        (seller_sku,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"shop.db 无商品: {seller_sku}")
    return dict(row)


def _fetch_product(row: dict) -> dict | None:
    pid = row.get("product_id")
    region = (row.get("region") or "PH").upper()
    if not pid:
        return None
    try:
        from modules.miaoshou.mx_migrate import fetch_tiktok_product

        return fetch_tiktok_product(str(pid), region=region)
    except Exception:
        return None


def _listing_package(row: dict, product: dict | None = None) -> tuple[float | None, float | None, float | None, str]:
    region = (row.get("region") or "PH").upper()
    prod = product
    if prod is None:
        prod = _fetch_product(row)
    if not prod:
        return None, None, None, ""
    dim = prod.get("package_dimensions") or {}
    l = float(dim["length"]) if dim.get("length") else None
    w = float(dim["width"]) if dim.get("width") else None
    h = float(dim["height"]) if dim.get("height") else None
    if l and w and h:
        return l, w, h, f"{region} listing {int(l)}x{int(w)}x{int(h)} cm"
    return None, None, None, ""


def quote_sku(
    seller_sku: str,
    *,
    cny_gbp: float | None = None,
    cargo: CargoKind = "puhuo",
    uk_category: str = "",
    uk_sub_category: str = "",
) -> PopResult:
    row = load_catalog_row(seller_sku)
    product = _fetch_product(row)
    if not uk_category and product:
        uk_category, uk_sub_category = extract_category_from_product(product)
    cost = row.get("cost_cny")
    if not cost or float(cost) <= 0:
        raise RuntimeError(f"{seller_sku} 无 sku_costs 成本，请先在目录填写")
    mk = tk_match_key(seller_sku)
    from modules.miaoshou.uk_manual_overrides import load_overrides

    ov = load_overrides().get(mk, {})
    known = KNOWN_LOGISTICS.get(seller_sku) or {**KNOWN_BY_MATCH_KEY.get(mk, {}), **ov}
    merged = weight_index_by_match_key().get(mk) or {}
    lw = lookup_stored(seller_sku)
    listing_l, listing_w, listing_h, listing_src = _listing_package(row, product)
    l = w = h = None
    weight_kg = None
    wsrc = ""
    if known.get("l"):
        l, w, h = known["l"], known["w"], known["h"]
        weight_kg = known.get("weight_kg")
        wsrc = known.get("source", "manual")
        if not weight_kg and merged.get("weight_g"):
            weight_kg = int(merged["weight_g"]) / 1000
            wsrc = f"{known.get('source', 'manual')} · 重量四国中位"
    elif listing_l and listing_w and listing_h:
        l, w, h = listing_l, listing_w, listing_h
        if merged.get("weight_g"):
            weight_kg = int(merged["weight_g"]) / 1000
            pkg_n = int(merged.get("package_count") or 0)
            wsrc = f"{listing_src} · 重量四国中位({pkg_n}包裹)"
        elif lw and lw.get("weight_g"):
            weight_kg = int(lw["weight_g"]) / 1000
            wsrc = f"{listing_src} · {lw.get('weight_source') or 'sku_logistics_weights'}"
        else:
            wsrc = listing_src
    elif merged.get("weight_g"):
        weight_kg = int(merged["weight_g"]) / 1000
        pkg_n = int(merged.get("package_count") or 0)
        wsrc = f"四国合并中位({pkg_n}包裹)"
        l = (merged.get("depth_mm") or 0) / 10 or known.get("l")
        w = (merged.get("width_mm") or 0) / 10 or known.get("w")
        h = (merged.get("height_mm") or 0) / 10 or known.get("h")
    elif lw and lw.get("weight_g"):
        weight_kg = int(lw["weight_g"]) / 1000
        wsrc = lw.get("weight_source") or "sku_logistics_weights"
        l = (int(lw["depth"]) / 10 if lw.get("depth") else 0) or known.get("l")
        w = (int(lw["width"]) / 10 if lw.get("width") else 0) or known.get("w")
        h = (int(lw["height"]) / 10 if lw.get("height") else 0) or known.get("h")
    else:
        weight_kg = known.get("weight_kg")
        wsrc = known.get("source", "manual")
        l, w, h = known.get("l"), known.get("w"), known.get("h")
    if not weight_kg:
        raise RuntimeError(f"{seller_sku} 缺少计费重量")
    if not (l and w and h):
        raise RuntimeError(f"{seller_sku} 缺少包裹尺寸（原链接无 package_dimensions 且物流无尺寸）")
    note = (row.get("note") or "").strip()
    cost_source = f"sku_costs{(' · ' + note) if note else ''}"
    p = PopParams.from_defaults(uk_category=uk_category, uk_sub_category=uk_sub_category)
    p.cargo = known.get("cargo") or cargo
    if known.get("commission_pct"):
        p.platform_commission_pct = float(known["commission_pct"])
    result = calc_pop_price(
        seller_sku=seller_sku,
        cost_cny=float(cost),
        cost_source=cost_source,
        weight_kg=float(weight_kg),
        weight_source=wsrc,
        length_cm=float(l),
        width_cm=float(w),
        height_cm=float(h),
        cny_gbp=cny_gbp,
        params=p,
        cargo=p.cargo,
    )
    if result.package_cm == "—":
        result.package_cm = f"{int(l)}×{int(w)}×{int(h)} cm"
    return result


def quote_match_key(
    match_key: str,
    *,
    cny_gbp: float | None = None,
    cargo: CargoKind = "puhuo",
    uk_category: str = "",
    uk_sub_category: str = "",
) -> PopResult:
    mk = tk_match_key(match_key)
    last_err: Exception | None = None
    for region in ("PH", "MY", "TH", "VN"):
        sk = regional_seller_sku(mk, region)
        try:
            return quote_sku(
                sk,
                cny_gbp=cny_gbp,
                cargo=cargo,
                uk_category=uk_category,
                uk_sub_category=uk_sub_category,
            )
        except RuntimeError as e:
            last_err = e
    raise RuntimeError(f"对齐码 {mk} 无法报价: {last_err}")


def main() -> int:
    ap = argparse.ArgumentParser(description="UK POP 定价")
    ap.add_argument("keys", nargs="*", help="对齐码或 seller_sku")
    ap.add_argument("--cargo", default="puhuo", choices=["puhuo", "tehuo", "minhuo"])
    ap.add_argument(
        "--buyer-pays-shipping",
        action="store_true",
        help="买家付 £3.99 模式（默认卖家包邮，全额 4PL）",
    )
    ap.add_argument("--profit", type=float, default=0, help="目标利润 %%（默认 17.5，建议 15–20）")
    ap.add_argument("--discount", type=float, default=0, help="店铺卖家折扣 %%（默认 25）")
    ap.add_argument("--category", default="", help="TikTok UK Category（覆盖自动识别）")
    ap.add_argument("--sub-category", default="", help="TikTok UK Sub-category")
    ap.add_argument("--commission", type=float, default=0, help="手动指定佣金 %（覆盖类目表）")
    args = ap.parse_args()
    d = pop_defaults()
    rate = fetch_cny_gbp()
    ship_mode = "buyer pays £3.99" if args.buyer_pays_shipping else "seller free ship (full 4PL)"
    profit = args.profit if args.profit > 0 else d.profit_pct
    discount = args.discount if args.discount > 0 else d.seller_discount_pct
    print(
        f"CNY/GBP rate: {rate} | ship: {ship_mode} | shop discount: {discount:g}% | "
        f"target profit: {profit:g}% | VAT 20%"
    )
    keys = args.keys or ["0003"]
    for raw in keys:
        mk = tk_match_key(raw)
        try:
            q = quote_match_key(
                mk,
                cny_gbp=rate,
                cargo=args.cargo,
                uk_category=args.category,
                uk_sub_category=args.sub_category,
            )
            p = PopParams.from_defaults(
                uk_category=args.category or q.uk_category,
                uk_sub_category=args.sub_category or q.uk_sub_category,
            )
            p.profit_pct = profit
            p.seller_discount_pct = discount
            p.buyer_pays_shipping = args.buyer_pays_shipping
            if args.commission > 0:
                p.platform_commission_pct = args.commission
            q = calc_pop_price(
                seller_sku=q.seller_sku,
                cost_cny=q.cost_cny,
                cost_source=q.cost_source,
                weight_kg=q.weight_kg,
                weight_source=q.weight_source,
                length_cm=float(q.package_cm.split("×")[0]) if "×" in q.package_cm else None,
                width_cm=float(q.package_cm.split("×")[1].split()[0]) if "×" in q.package_cm else None,
                height_cm=float(q.package_cm.split("×")[2].split()[0]) if "×" in q.package_cm and len(q.package_cm.split("×")) > 2 else None,
                cny_gbp=rate,
                params=p,
                cargo=args.cargo,
            )
            print(f"\n=== {mk} · {q.seller_sku} ===")
            print(f"commission: {q.commission_label}")
            print(
                f"upload ceil: GBP {q.list_price_ceil_gbp} | POP sale (after {discount:g}% off): "
                f"GBP {q.sale_price_gbp:.2f} | profit GBP {q.net_profit_gbp:.2f} ({q.profit_margin_on_sale_pct}%)"
            )
            for step in q.steps:
                print(step)
        except Exception as exc:
            print(f"{mk}: ERROR {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
