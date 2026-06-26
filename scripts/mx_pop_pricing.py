"""MX POP 跨境直邮定价（对齐桌面定价表 Step2 模板一）。

用法:
  python scripts/mx_pop_pricing.py 770002 770003
  python scripts/mx_pop_pricing.py --all-ph
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db import connect, init_db
from modules.catalog.logistics_weights import lookup_stored, regional_seller_sku, weight_index_by_match_key
from modules.catalog.sku_key import tk_match_key

# 新运费表（26年6月15日生效）— 头程价卡 MXN；藏价 = 价卡 − 59
SHIPPING_RATES: list[tuple[float, float]] = [
    (0.1, 69),
    (0.2, 93),
    (0.3, 114),
    (0.4, 134),
    (0.5, 155),
    (0.6, 176),
    (0.7, 197),
    (0.8, 218),
    (0.9, 239),
    (1.0, 259),
    (1.5, 350),
    (2.0, 454),
    (3.0, 616),
    (4.0, 824),
    (5.0, 1033),
    (6.0, 1394),
    (7.0, 1603),
    (8.0, 1811),
    (9.0, 1825),
    (10.0, 2059),
    (11.0, 2293),
    (12.0, 2527),
    (13.0, 2762),
    (14.0, 2996),
    (30.0, 3230),
]

USD_MXN = 17.66  # 表格免责说明中的参考汇率
DE_MINIMIS_USD = 4.5
VOLUMETRIC_DIVISOR = 8000  # cm³ → kg（墙贴类，不计泡时仍展示对比）

# SFP 运费补贴券门槛（满减 59 MXN）；距门槛 10% 以内抬价至门槛
SFP_COUPON_THRESHOLDS: list[tuple[float, str]] = [
    (179.0, "老客满179减59"),
    (99.0, "新客满99减59"),
]
SFP_SUBSIDY_MXN = 59.0
SFP_NEAR_PCT = 0.10


@dataclass
class PopParams:
    qty: int = 1
    profit_pct: float = 20.0
    seller_discount_pct: float = 30.0
    new_buyer_discount_pct: float = 0.0
    affiliate_pct: float = 8.0
    ad_pct: float = 10.0
    sfp_pct: float = 8.0
    platform_commission_pct: float = 6.0
    per_item_fee_mxn: float = 6.0
    import_tax_rate: float = 0.335
    import_tax_divisor: float = 1.335
    import_tax_coeff: float = 0.1597  # 折后售价反推公式中的进口税系数


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
    shipping_tier_kg: float
    shipping_card_mxn: float
    logistics_hidden_mxn: float
    cny_mxn: float
    cost_mxn: float
    pop_sale_mxn: float
    sale_price_mxn: float
    list_price_mxn: float
    list_price_ceil_mxn: int
    sfp_adjustment: str | None
    import_tax_mxn: float
    platform_commission_mxn: float
    sfp_fee_mxn: float
    per_item_fee_mxn: float
    affiliate_mxn: float
    ad_mxn: float
    net_income_mxn: float
    net_profit_mxn: float
    profit_margin_on_sale_pct: float
    old_php_to_mxn: float | None
    steps: list[str]


def fetch_cny_mxn() -> float:
    """Frankfurter 免费汇率 API（失败则用 2.5459 兜底）。"""
    try:
        req = urllib.request.Request(
            "https://api.frankfurter.app/latest?from=CNY&to=MXN",
            headers={"User-Agent": "tiktok_e_comm/mx_pop_pricing"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return round(float(data["rates"]["MXN"]), 4)
    except Exception:
        return 2.5459


def shipping_lookup(weight_kg: float) -> tuple[float, float]:
    """XLOOKUP(..., match_mode=1)：精确或下一档更大重量。"""
    if weight_kg <= 0 or weight_kg > 30:
        raise ValueError(f"包裹重量须在 0–30kg，当前 {weight_kg}kg")
    for limit, fee in SHIPPING_RATES:
        if weight_kg <= limit:
            return limit, fee
    raise ValueError(f"无运费档: {weight_kg}kg")


def hidden_logistics_mxn(weight_kg: float) -> tuple[float, float, float]:
    tier, card = shipping_lookup(weight_kg)
    return tier, card, round(card - 59, 2)


def sale_price_from_cost(
    cost_mxn: float,
    logistics_mxn: float,
    p: PopParams,
) -> tuple[float, str]:
    """复刻 Excel B14：折后整单售价（含藏价物流）。"""
    b8 = p.new_buyer_discount_pct / 100
    b6 = p.profit_pct / 100
    b9 = p.affiliate_pct / 100
    b11 = p.ad_pct / 100
    base_num = p.qty * cost_mxn + logistics_mxn

    denom_high = (
        1
        - p.import_tax_coeff * (1 - b8)
        - p.platform_commission_pct / 100
        - b9 * (1 - b8)
        - b6
        - b11
    )
    branch = "high_price"
    sale = base_num / denom_high + 60

    if sale <= 1000:
        denom_low = (
            1
            - p.import_tax_coeff * (1 - b8)
            - p.platform_commission_pct / 100
            - p.sfp_pct / 100
            - b9 * (1 - b8)
            - b6
            - b11
        )
        sale = base_num / denom_low
        branch = "normal"

    return round(sale, 2), branch


def apply_sfp_coupon_floors(
    sale: float,
    *,
    near_pct: float = SFP_NEAR_PCT,
) -> tuple[float, str | None]:
    """距 SFP 满减门槛不足 near_pct 时，折后售价抬至门槛（优先匹配更高门槛）。"""
    for threshold, label in SFP_COUPON_THRESHOLDS:
        floor_min = threshold * (1 - near_pct)
        if floor_min <= sale < threshold:
            return threshold, (
                f"SFP {label}：POP 折后 {sale:.2f} MXN 落在 "
                f"[{floor_min:.2f}, {threshold})，抬至 {threshold:.0f} MXN "
                f"（买家可用 {SFP_SUBSIDY_MXN:.0f} MXN 运费券）"
            )
    return sale, None


def _verify_sale(
    sale: float,
    *,
    cost_mxn: float,
    logistics: float,
    qty: int,
    p: PopParams,
) -> dict:
    b8 = p.new_buyer_discount_pct / 100
    import_tax_raw = ((sale - DE_MINIMIS_USD * USD_MXN) * (1 - b8)) / p.import_tax_divisor * p.import_tax_rate
    import_tax = round(max(import_tax_raw, 0), 2)
    platform = round(sale * p.platform_commission_pct / 100, 2)
    sfp = round(min(sale * p.sfp_pct / 100, 60), 2)
    per_item = round(qty * p.per_item_fee_mxn, 2)
    affiliate = round(sale * (1 - b8) * p.affiliate_pct / 100, 2)
    ad = round(sale * p.ad_pct / 100, 2)
    income = round(sale - logistics - import_tax - platform - sfp - affiliate - ad - per_item, 2)
    profit = round(income - cost_mxn * qty, 2)
    margin = round(profit / sale * 100, 2) if sale else 0.0
    b7 = p.seller_discount_pct / 100
    list_price = round(sale / (1 - b7 - b8) / qty, 2) if (1 - b7 - b8) > 0 else sale
    list_price_ceil = int(math.ceil(list_price))
    return {
        "import_tax_raw": round(import_tax_raw, 2),
        "import_tax": import_tax,
        "platform": platform,
        "sfp": sfp,
        "per_item": per_item,
        "affiliate": affiliate,
        "ad": ad,
        "income": income,
        "profit": profit,
        "margin": margin,
        "list_price": list_price,
        "list_price_ceil": list_price_ceil,
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
    cny_mxn: float | None = None,
    ph_price_php: float | None = None,
    params: PopParams | None = None,
) -> PopResult:
    p = params or PopParams()
    rate = cny_mxn if cny_mxn is not None else fetch_cny_mxn()
    cost_mxn = round(cost_cny * rate, 2)

    vol_kg = 0.0
    pkg = "—"
    if length_cm and width_cm and height_cm:
        vol_kg = round(length_cm * width_cm * height_cm / VOLUMETRIC_DIVISOR, 4)
        pkg = f"{int(length_cm)}×{int(width_cm)}×{int(height_cm)} cm"
    billable = max(weight_kg, vol_kg)
    tier, card, logistics = hidden_logistics_mxn(billable)

    pop_sale, branch = sale_price_from_cost(cost_mxn, logistics, p)
    sale, sfp_note = apply_sfp_coupon_floors(pop_sale)
    v = _verify_sale(sale, cost_mxn=cost_mxn, logistics=logistics, qty=p.qty, p=p)

    old_mxn = None
    if ph_price_php and ph_price_php > 0:
        php_cny = 0.118
        mxn_cny_ref = 0.36
        old_mxn = round(ph_price_php * 1.2 * php_cny / mxn_cny_ref, 2)

    steps = [
        f"① 成本 {cost_cny} CNY × {rate} = {cost_mxn} MXN（{cost_source}）",
        f"② 计费重 max(实重 {weight_kg}kg, 体积重 {vol_kg}kg) = {billable}kg → 价卡档 ≤{tier}kg = {card} MXN，藏价 = {card}−59 = {logistics} MXN",
        (
            f"③ POP 折后售价 B14（{branch}）= ({p.qty}×{cost_mxn}+{logistics}) "
            f"/ (1−进口税系数−6%−{'8%SFP−' if branch=='normal' else ''}达人−利润−广告) = {pop_sale} MXN"
        ),
    ]
    if sfp_note:
        steps.append(f"④ {sfp_note}")
    else:
        steps.append("④ 未触发 SFP 抬价（未落在距 99/179 门槛 10% 的区间内）")
    steps.append(
        f"⑤ 折前原价 = {sale} / (1−{p.seller_discount_pct}%) = {v['list_price']} MXN"
        f" → 上架写入 ceil = {v['list_price_ceil']} MXN"
    )
    steps.append(
        f"⑥ 验算：进口税 MAX(0)={v['import_tax']}（原值 {v['import_tax_raw']}）"
        f" + 平台6% {v['platform']} + SFP费 {v['sfp']} + 达人 {v['affiliate']} + 广告 {v['ad']}"
        f" + 每件费 {v['per_item']} + 藏价 {logistics} + 成本 {cost_mxn}"
        f" → 净利 {v['profit']} MXN（{v['margin']}%）"
    )
    if old_mxn is not None:
        steps.append(f"⑦ 对比旧规则 PHP×1.2→MXN（PH 售价 {ph_price_php} PHP）= {old_mxn} MXN")

    return PopResult(
        seller_sku=seller_sku,
        cost_cny=cost_cny,
        cost_source=cost_source,
        weight_kg=weight_kg,
        weight_source=weight_source,
        package_cm=pkg,
        volumetric_kg=vol_kg,
        billable_kg=billable,
        shipping_tier_kg=tier,
        shipping_card_mxn=card,
        logistics_hidden_mxn=logistics,
        cny_mxn=rate,
        cost_mxn=cost_mxn,
        pop_sale_mxn=pop_sale,
        sale_price_mxn=sale,
        list_price_mxn=v["list_price"],
        list_price_ceil_mxn=v["list_price_ceil"],
        sfp_adjustment=sfp_note,
        import_tax_mxn=v["import_tax"],
        platform_commission_mxn=v["platform"],
        sfp_fee_mxn=v["sfp"],
        per_item_fee_mxn=v["per_item"],
        affiliate_mxn=v["affiliate"],
        ad_mxn=v["ad"],
        net_income_mxn=v["income"],
        net_profit_mxn=v["profit"],
        profit_margin_on_sale_pct=v["margin"],
        old_php_to_mxn=old_mxn,
        steps=steps,
    )


# 0002/0003 当前 MX 试点已知包裹与重量（PH 母版 / 上次 save 成功值）
# 手动包裹（优先于物流实测尺寸，避免异常大箱计泡）
# volumetric_confirmed=True 表示用户已确认该尺寸，可跳过「体积重>实重」拦截
KNOWN_BY_MATCH_KEY: dict[str, dict] = {
    "0001": {
        "weight_kg": 0.578,
        "l": 21,
        "w": 18,
        "h": 10,
        "source": "manual 21×18×10 cm",
        "volumetric_confirmed": True,
    },
    "0014": {
        "weight_kg": 0.18,
        "l": 30,
        "w": 5,
        "h": 5,
        "source": "manual 30×5×5 cm",
        "volumetric_confirmed": True,
    },
    "0015": {
        "weight_kg": 0.241,
        "l": 6,
        "w": 10,
        "h": 40,
        "source": "PH listing 6×10×40 cm",
        "volumetric_confirmed": True,
    },
    "0017": {
        "l": 52,
        "w": 13,
        "h": 4,
        "source": "manual 52×13×4 cm",
        "volumetric_confirmed": True,
    },
    "0018": {
        "l": 35,
        "w": 5,
        "h": 5,
        "source": "manual 35×5×5 cm",
        "volumetric_confirmed": True,
    },
    "0021": {
        "l": 35,
        "w": 6,
        "h": 6,
        "source": "manual 35×6×6 cm",
        "volumetric_confirmed": True,
    },
    "0022": {
        "l": 35,
        "w": 5,
        "h": 5,
        "source": "manual 35×5×5 cm",
        "volumetric_confirmed": True,
    },
    "0023": {
        "l": 35,
        "w": 6,
        "h": 6,
        "source": "manual 35×6×6 cm",
        "volumetric_confirmed": True,
    },
    "0025": {
        "l": 35,
        "w": 5,
        "h": 5,
        "source": "manual 35×5×5 cm",
        "volumetric_confirmed": True,
    },
    "0800": {
        "weight_kg": 0.2,
        "l": 10,
        "w": 10,
        "h": 10,
        "source": "manual 10×10×10 cm",
        "volumetric_confirmed": True,
    },
    "0801": {
        "weight_kg": 0.2,
        "l": 10,
        "w": 10,
        "h": 10,
        "source": "manual 10×10×10 cm",
        "volumetric_confirmed": True,
    },
    "0802": {
        "weight_kg": 0.2,
        "l": 10,
        "w": 10,
        "h": 10,
        "source": "manual 10×10×10 cm",
        "volumetric_confirmed": True,
    },
    "0805": {
        "l": 20,
        "w": 20,
        "h": 7,
        "source": "manual 20×20×7 cm",
        "volumetric_confirmed": True,
    },
    "0806": {
        "l": 20,
        "w": 20,
        "h": 7,
        "source": "manual 20×20×7 cm",
        "volumetric_confirmed": True,
    },
    "0807": {
        "l": 20,
        "w": 20,
        "h": 7,
        "source": "manual 20×20×7 cm",
        "volumetric_confirmed": True,
    },
    "0809": {
        "weight_kg": 0.2,
        "l": 5,
        "w": 5,
        "h": 5,
        "source": "manual 5×5×5 cm",
        "volumetric_confirmed": True,
    },
    "0810": {
        "l": 15,
        "w": 15,
        "h": 6,
        "source": "manual 15×15×6 cm",
        "volumetric_confirmed": True,
    },
    "0811": {
        "weight_kg": 0.3,
        "l": 20,
        "w": 15,
        "h": 10,
        "source": "manual 20×15×10 cm",
        "volumetric_confirmed": True,
    },
    "0812": {
        "weight_kg": 0.3,
        "l": 20,
        "w": 15,
        "h": 10,
        "source": "manual 20×15×10 cm (同0811)",
        "volumetric_confirmed": True,
    },
    "0813": {
        "weight_kg": 0.3,
        "l": 20,
        "w": 15,
        "h": 10,
        "source": "manual 20×15×10 cm (同0811)",
        "volumetric_confirmed": True,
    },
    "0827": {
        "l": 10,
        "w": 10,
        "h": 10,
        "source": "manual 10×10×10 cm",
        "volumetric_confirmed": True,
    },
}
KNOWN_LOGISTICS: dict[str, dict] = {
    "770002": {"weight_kg": 0.10, "l": 4, "w": 4, "h": 30, "source": "sku_logistics_weights / PH 实测"},
    "770003": {"weight_kg": 0.158, "l": 4, "w": 4, "h": 30, "source": "PH 母版 / MX save 成功记录"},
}


def load_catalog_row(seller_sku: str) -> dict:
    init_db()
    for sk in (seller_sku,):
        row = connect().execute(
            """
            SELECT p.seller_sku, p.sku_id, p.product_id, p.price, p.product_name, sc.cost_cny, sc.note, s.region
            FROM products p
            JOIN shops s ON p.shop_cipher = s.cipher
            LEFT JOIN sku_costs sc ON sc.sku_id = p.sku_id
            WHERE p.seller_sku = ?
            LIMIT 1
            """,
            (sk,),
        ).fetchone()
        if row:
            return dict(row)
    raise RuntimeError(f"shop.db 无商品: {seller_sku}")


def load_ph_row(seller_sku: str) -> dict:
    init_db()
    row = connect().execute(
        """
        SELECT p.seller_sku, p.sku_id, p.price, p.product_name, sc.cost_cny, sc.note
        FROM products p
        JOIN shops s ON p.shop_cipher = s.cipher
        LEFT JOIN sku_costs sc ON sc.sku_id = p.sku_id
        WHERE p.seller_sku = ? AND UPPER(s.region) = 'PH'
        LIMIT 1
        """,
        (seller_sku,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"shop.db 无 PH 商品: {seller_sku}")
    return dict(row)


def quote_match_key(match_key: str, *, cny_mxn: float | None = None) -> PopResult:
    """按对齐码报价：优先 PH 货号，无 PH 时回退 MY/TH/VN。"""
    mk = tk_match_key(match_key)
    last_err: Exception | None = None
    for region in ("PH", "MY", "TH", "VN"):
        sk = regional_seller_sku(mk, region)
        try:
            return quote_sku(sk, cny_mxn=cny_mxn)
        except RuntimeError as e:
            last_err = e
    raise RuntimeError(f"对齐码 {mk} 无法报价: {last_err}")


def _listing_package(row: dict) -> tuple[float | None, float | None, float | None, str]:
    """TikTok 原链接商品 package_dimensions（卖家填写，优先于物流实测尺寸）。"""
    pid = row.get("product_id")
    region = (row.get("region") or "PH").upper()
    if not pid:
        return None, None, None, ""
    try:
        from modules.miaoshou.mx_migrate import fetch_tiktok_product

        p = fetch_tiktok_product(str(pid), region=region)
        dim = p.get("package_dimensions") or {}
        l = float(dim["length"]) if dim.get("length") else None
        w = float(dim["width"]) if dim.get("width") else None
        h = float(dim["height"]) if dim.get("height") else None
        if l and w and h:
            return l, w, h, f"{region} listing {int(l)}×{int(w)}×{int(h)} cm"
    except Exception:
        pass
    return None, None, None, ""


def quote_sku(seller_sku: str, *, cny_mxn: float | None = None) -> PopResult:
    row = load_catalog_row(seller_sku)
    cost = row.get("cost_cny")
    if not cost or float(cost) <= 0:
        raise RuntimeError(f"{seller_sku} 无 sku_costs 成本，请先在目录填写")
    mk = tk_match_key(seller_sku)
    from modules.miaoshou.feishu_manual_overrides import load_overrides

    ov = load_overrides().get(mk, {})
    known = KNOWN_LOGISTICS.get(seller_sku) or {**KNOWN_BY_MATCH_KEY.get(mk, {}), **ov}
    merged = weight_index_by_match_key().get(mk) or {}
    lw = lookup_stored(seller_sku)
    listing_l, listing_w, listing_h, listing_src = _listing_package(row)
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
    return calc_pop_price(
        seller_sku=seller_sku,
        cost_cny=float(cost),
        cost_source=cost_source,
        weight_kg=float(weight_kg),
        weight_source=wsrc,
        length_cm=float(l) if l else None,
        width_cm=float(w) if w else None,
        height_cm=float(h) if h else None,
        cny_mxn=cny_mxn,
        ph_price_php=float(row["price"]) if row.get("price") else None,
    )


def print_report(results: list[PopResult], params: PopParams) -> None:
    print("=" * 72)
    print("MX POP 跨境直邮定价测算（Step2 模板一 · 26年7月6日每件费版）")
    print("=" * 72)
    print("固定参数：")
    print(f"  模式: 跨境直邮 + SFP 8% + 每件成交费 6 MXN")
    print(f"  达人佣金 {params.affiliate_pct}% | 广告 {params.ad_pct}% | 预期利润 {params.profit_pct}%")
    print(f"  商家自设折扣 {params.seller_discount_pct}% | 新客折扣 {params.new_buyer_discount_pct}%")
    print(
        f"  SFP 运费券：新客满 {SFP_COUPON_THRESHOLDS[1][0]:.0f} / "
        f"老客满 {SFP_COUPON_THRESHOLDS[0][0]:.0f} 减 {SFP_SUBSIDY_MXN:.0f} MXN；"
        f"距门槛 {SFP_NEAR_PCT:.0%} 内抬价"
    )
    if results:
        print(f"  汇率 1 CNY = {results[0].cny_mxn} MXN（实时 API，失败则 2.5459）")
    print()

    for r in results:
        print("-" * 72)
        print(f"SKU {r.seller_sku}  |  包裹 {r.package_cm}  |  实重 {r.weight_kg}kg ({r.weight_source})")
        print()
        for step in r.steps:
            print(f"  {step}")
        print()
        print("  ┌─────────────────────────────┬──────────────┐")
        print(f"  │ POP 测算折后价              │ {r.pop_sale_mxn:>10.2f} MXN │")
        if r.sfp_adjustment:
            print(f"  │ SFP 抬价后折后售价          │ {r.sale_price_mxn:>10.2f} MXN │")
        print(f"  │ 折前原价 list_price         │ {r.list_price_mxn:>10.2f} MXN │")
        print(f"  │ 上架写入价 ceil(list)       │ {r.list_price_ceil_mxn:>10} MXN │")
        print(f"  │ 藏价物流                    │ {r.logistics_hidden_mxn:>10.2f} MXN │")
        print(f"  │ 商家净利（验算）            │ {r.net_profit_mxn:>10.2f} MXN │")
        if r.old_php_to_mxn is not None:
            print(f"  │ 旧 PHP×1.2 规则（对比）     │ {r.old_php_to_mxn:>10.2f} MXN │")
        print("  └─────────────────────────────┴──────────────┘")
        print()

    print("公式摘要（与 Excel 一致）：")
    print("  藏价物流 = 新运费表.lookup(计费重) − 59")
    print("  折后售价 = (成本_MXN + 藏价) / (1 − 0.1597 − 6% − 8%SFP − 达人% − 利润% − 广告%)")
    print("  折前原价 = 折后售价 / (1 − 自设折扣% − 新客折扣%)")
    print("  妙手写入：price = priceIncludeVat = ceil(折前原价)；折扣在 TikTok 后台自设")
    print("  POP 折后售价仅用于测算/确认卡片，不上传")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MX POP 跨境直邮定价测算")
    parser.add_argument("skus", nargs="*", default=["770002", "770003"], help="PH seller_sku")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    rate = fetch_cny_mxn()
    params = PopParams()
    results = [quote_sku(sku, cny_mxn=rate) for sku in args.skus]

    if args.json:
        print(json.dumps({"params": asdict(params), "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2))
    else:
        print_report(results, params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
