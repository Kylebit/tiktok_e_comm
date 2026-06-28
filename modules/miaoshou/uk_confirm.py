"""UK 上架前用户确认（Web 收件箱 / Cursor 对话框）。"""
from __future__ import annotations

import json
import secrets
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.config import ROOT
from modules.catalog.sku_key import tk_match_key
from modules.miaoshou.uk_checks import quote_volumetric_dominates

CONFIRM_DIR = ROOT / "data" / "uk_confirm"
UK_EXIT_NEEDS_USER_CONFIRM = 5
CONFIRM_MARKER_BEGIN = "=== UK_CONFIRM_BEGIN ==="
CONFIRM_MARKER_END = "=== UK_CONFIRM_END ==="
CONFIRM_TOKEN_PREFIX = "UK_CONFIRM_TOKEN:"

_lock = threading.Lock()


@dataclass
class UkConfirmCard:
    token: str
    status: str  # pending | approved | rejected | published
    match_key: str
    seller_sku: str
    product_name: str
    main_image_url: str
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
    sale_price_gbp: float
    list_price_ceil_gbp: int
    stock: int
    volumetric_dominates: bool
    collect_box_detail_id: int
    master_region: str
    master_product_id: str
    cny_gbp: float
    net_profit_gbp: float
    cost_gbp: float = 0.0
    list_price_gbp: float = 0.0
    platform_commission_gbp: float = 0.0
    vat_gbp: float = 0.0
    smart_promotion_gbp: float = 0.0
    affiliate_gbp: float = 0.0
    ad_gbp: float = 0.0
    net_income_gbp: float = 0.0
    profit_margin_on_sale_pct: float = 0.0
    uk_category: str = ""
    uk_sub_category: str = ""
    commission_pct: float = 9.0
    commission_label: str = ""
    vat_rate_pct: float = 20.0
    created_at: float = field(default_factory=time.time)
    approved_at: float | None = None
    rejected_at: float | None = None


def _path(token: str) -> Path:
    safe = "".join(c for c in token if c.isalnum())
    return CONFIRM_DIR / f"{safe}.json"


def _group_path(token: str) -> Path:
    safe = "".join(c for c in token if c.isalnum())
    return CONFIRM_DIR / f"group_{safe}.json"


@dataclass
class UkGroupVariantLine:
    match_key: str
    seller_sku: str
    variant_label: str
    list_price_ceil_gbp: int
    sale_price_gbp: float
    weight_kg: float
    net_profit_gbp: float
    cargo: str = "puhuo"


@dataclass
class UkGroupConfirmCard:
    token: str
    status: str  # pending | approved | rejected | published
    match_keys: list[str]
    product_name: str
    main_image_url: str
    package_cm: str
    stock: int
    collect_box_detail_id: int
    master_region: str
    master_product_id: str
    variants: list[UkGroupVariantLine] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    approved_at: float | None = None
    rejected_at: float | None = None


def _write(card: UkConfirmCard) -> None:
    CONFIRM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(card.token).with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(card), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path(card.token))


def _read(token: str) -> UkConfirmCard | None:
    p = _path(token)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return UkConfirmCard(**data)


def new_token() -> str:
    return secrets.token_urlsafe(12)


def create_confirm_card(
    *,
    pop_quote: Any,
    collect_box_detail_id: int,
    seller_sku: str,
    master_product_id: str,
    master_region: str = "PH",
    stock: int = 200,
    product_name: str = "",
    main_image_url: str = "",
) -> UkConfirmCard:
    mk = tk_match_key(seller_sku)
    token = new_token()
    card = UkConfirmCard(
        token=token,
        status="pending",
        match_key=mk,
        seller_sku=seller_sku,
        product_name=product_name or seller_sku,
        main_image_url=main_image_url,
        cost_cny=float(pop_quote.cost_cny),
        cost_source=str(pop_quote.cost_source),
        weight_kg=float(pop_quote.weight_kg),
        weight_source=str(pop_quote.weight_source),
        package_cm=str(pop_quote.package_cm),
        volumetric_kg=float(pop_quote.volumetric_kg),
        billable_kg=float(pop_quote.billable_kg),
        cargo=str(pop_quote.cargo),
        merchant_logistics_gbp=float(pop_quote.merchant_logistics_gbp),
        seller_ship_net_gbp=float(pop_quote.seller_ship_net_gbp),
        buyer_shipping_gbp=float(pop_quote.buyer_shipping_gbp),
        shipping_band_max_kg=float(pop_quote.shipping_band_max_kg),
        sale_price_gbp=float(pop_quote.sale_price_gbp),
        list_price_ceil_gbp=int(pop_quote.list_price_ceil_gbp),
        stock=int(stock),
        volumetric_dominates=quote_volumetric_dominates(pop_quote),
        collect_box_detail_id=int(collect_box_detail_id),
        master_region=master_region,
        master_product_id=master_product_id,
        cny_gbp=float(pop_quote.cny_gbp),
        net_profit_gbp=float(pop_quote.net_profit_gbp),
        cost_gbp=float(pop_quote.cost_gbp),
        list_price_gbp=float(pop_quote.list_price_gbp),
        platform_commission_gbp=float(pop_quote.platform_commission_gbp),
        vat_gbp=float(pop_quote.vat_gbp),
        smart_promotion_gbp=float(pop_quote.smart_promotion_gbp),
        affiliate_gbp=float(pop_quote.affiliate_gbp),
        ad_gbp=float(pop_quote.ad_gbp),
        net_income_gbp=float(pop_quote.net_income_gbp),
        profit_margin_on_sale_pct=float(pop_quote.profit_margin_on_sale_pct),
        uk_category=str(getattr(pop_quote, "uk_category", "") or ""),
        uk_sub_category=str(getattr(pop_quote, "uk_sub_category", "") or ""),
        commission_pct=float(getattr(pop_quote, "commission_pct", 9) or 9),
        commission_label=str(getattr(pop_quote, "commission_label", "") or ""),
        vat_rate_pct=float(getattr(pop_quote, "vat_rate_pct", 20) or 20),
    )
    with _lock:
        _write(card)
    return card


def get_confirm(token: str) -> UkConfirmCard | None:
    with _lock:
        return _read(token)


def _write_group(card: UkGroupConfirmCard) -> None:
    CONFIRM_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(card)
    data["variants"] = [asdict(v) for v in card.variants]
    tmp = _group_path(card.token).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_group_path(card.token))


def _read_group(token: str) -> UkGroupConfirmCard | None:
    p = _group_path(token)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    variants = [UkGroupVariantLine(**v) for v in data.pop("variants", [])]
    return UkGroupConfirmCard(**data, variants=variants)


def get_group_confirm(token: str) -> UkGroupConfirmCard | None:
    with _lock:
        return _read_group(token)


def create_group_confirm_card(
    *,
    match_keys: list[str],
    collect_box_detail_id: int,
    master_product_id: str,
    master_region: str = "PH",
    stock: int = 200,
    product_name: str = "",
    main_image_url: str = "",
    package_cm: str = "",
    variant_quotes: list[tuple[Any, str, str]],
) -> UkGroupConfirmCard:
    token = new_token()
    lines: list[UkGroupVariantLine] = []
    for pop_quote, seller_sku, variant_label in variant_quotes:
        lines.append(
            UkGroupVariantLine(
                match_key=tk_match_key(seller_sku),
                seller_sku=seller_sku,
                variant_label=variant_label,
                list_price_ceil_gbp=int(pop_quote.list_price_ceil_gbp),
                sale_price_gbp=float(pop_quote.sale_price_gbp),
                weight_kg=float(pop_quote.weight_kg),
                net_profit_gbp=float(pop_quote.net_profit_gbp),
                cargo=str(pop_quote.cargo),
            )
        )
    card = UkGroupConfirmCard(
        token=token,
        status="pending",
        match_keys=[
            tk_match_key(k) if not str(k).isdigit() else str(k).zfill(4)[-4:] for k in match_keys
        ],
        product_name=product_name or (variant_quotes[0][1] if variant_quotes else ""),
        main_image_url=main_image_url,
        package_cm=package_cm,
        stock=int(stock),
        collect_box_detail_id=int(collect_box_detail_id),
        master_region=master_region,
        master_product_id=master_product_id,
        variants=lines,
    )
    with _lock:
        _write_group(card)
    return card


def approve_group_confirm(token: str) -> UkGroupConfirmCard:
    with _lock:
        card = _read_group(token)
        if not card:
            raise KeyError(f"多规格确认单不存在: {token}")
        if card.status != "pending":
            raise RuntimeError(f"确认单状态为 {card.status}，无法再次确认")
        card.status = "approved"
        card.approved_at = time.time()
        _write_group(card)
        return card


def reject_group_confirm(token: str) -> UkGroupConfirmCard:
    with _lock:
        card = _read_group(token)
        if not card:
            raise KeyError(f"多规格确认单不存在: {token}")
        if card.status != "pending":
            raise RuntimeError(f"确认单状态为 {card.status}，无法拒绝")
        card.status = "rejected"
        card.rejected_at = time.time()
        _write_group(card)
        return card


def mark_group_published(token: str) -> UkGroupConfirmCard:
    with _lock:
        card = _read_group(token)
        if not card:
            raise KeyError(f"多规格确认单不存在: {token}")
        card.status = "published"
        card.approved_at = card.approved_at or time.time()
        _write_group(card)
        return card


def approve_confirm(token: str) -> UkConfirmCard:
    with _lock:
        card = _read(token)
        if not card:
            raise KeyError(f"确认单不存在: {token}")
        if card.status != "pending":
            raise RuntimeError(f"确认单状态为 {card.status}，无法再次确认")
        card.status = "approved"
        card.approved_at = time.time()
        _write(card)
        return card


def reject_confirm(token: str) -> UkConfirmCard:
    with _lock:
        card = _read(token)
        if not card:
            raise KeyError(f"确认单不存在: {token}")
        if card.status != "pending":
            raise RuntimeError(f"确认单状态为 {card.status}，无法拒绝")
        card.status = "rejected"
        card.rejected_at = time.time()
        _write(card)
        return card


def mark_published(token: str) -> UkConfirmCard:
    with _lock:
        card = _read(token)
        if not card:
            raise KeyError(f"确认单不存在: {token}")
        card.status = "published"
        card.approved_at = card.approved_at or time.time()
        _write(card)
        return card


def assert_user_approved(token: str | None) -> None:
    if not token:
        raise UserConfirmRequired("缺少用户确认 token")
    card = get_confirm(token)
    if not card:
        raise UserConfirmRequired(f"确认单不存在: {token}")
    if card.status != "approved":
        raise UserConfirmRequired(f"确认单未通过（当前: {card.status}）")


class UserConfirmRequired(Exception):
    """用户尚未确认上架。"""


def format_confirm_card_dialog(card: UkConfirmCard) -> str:
    lines = [
        f"## UK 上架确认 · {card.match_key}",
        "",
        f"![主图]({card.main_image_url})" if card.main_image_url else "（无主图）",
        "",
        f"**标题** · {card.product_name[:120]}",
        f"**对齐码** · `{card.match_key}` · **商家 SKU** · `{card.seller_sku}`",
        f"**库存** · {card.stock} · **TK 采集箱** · `{card.collect_box_detail_id}`",
        f"**类目佣金** · {card.commission_label or f'default @ {card.commission_pct:g}%'} · **VAT** · {card.vat_rate_pct:g}%",
        "",
        "### 物流（4PL Standard · {cargo}）".format(cargo=card.cargo),
        f"- 实重 **{card.weight_kg:.3f} kg**（{card.weight_source}）",
        f"- 体积重 **{card.volumetric_kg:.3f} kg** · 尺寸 **{card.package_cm}** · 计费 **{card.billable_kg:.3f} kg**",
        f"- 商家 4PL **£{card.merchant_logistics_gbp:.2f}** · 卖家净运费 **£{card.seller_ship_net_gbp:.2f}**"
        + (
            f"（买家付 £{card.buyer_shipping_gbp:.2f}）"
            if card.buyer_shipping_gbp > 0
            else "（满额包邮 · 卖家承担）"
        ),
        "",
        "### 价格（上传 vs 测算）",
        f"- **上传原价 ceil** · **£{card.list_price_ceil_gbp}**（写入 price / priceIncludeVat）",
        f"- 折前原价（精确）· £{card.list_price_gbp:.2f}",
        f"- POP 测算折后 · £{card.sale_price_gbp:.2f} GBP（店铺后台 **25%** 卖家折扣 → 折后约此价）",
        "",
        "### 成本与汇率",
        f"- 采购成本 · ¥{card.cost_cny:.2f} → **£{card.cost_gbp:.2f}**（{card.cost_source}）",
        f"- 汇率 CNY/GBP · **{card.cny_gbp}**",
        "",
        f"### POP 折后价支出明细（测算基准 £{card.sale_price_gbp:.2f}）",
        "| 项目 | GBP |",
        "|------|-----|",
        f"| 采购成本 | {card.cost_gbp:.2f} |",
        f"| 卖家净运费 | {card.seller_ship_net_gbp:.2f} |",
        f"| VAT | {card.vat_gbp:.2f} |",
        f"| 平台佣金 | {card.platform_commission_gbp:.2f} |",
        f"| Smart Promotion | {card.smart_promotion_gbp:.2f} |",
        f"| 广告 | {card.ad_gbp:.2f} |",
        f"| **合计支出** | **{card.cost_gbp + card.seller_ship_net_gbp + card.vat_gbp + card.platform_commission_gbp + card.smart_promotion_gbp + card.ad_gbp:.2f}** |",
        "",
        "### 利润（按 POP 折后测算）",
        f"- 折后售价 · £{card.sale_price_gbp:.2f}",
        f"- 预估净利 · **£{card.net_profit_gbp:.2f}**（占折后售价 **{card.profit_margin_on_sale_pct:.1f}%**）",
    ]
    if card.volumetric_dominates:
        lines.append("")
        lines.append("⚠ **体积重 > 实重**，请核对包裹尺寸；当前 POP 测算可能偏高。")
    lines.extend(
        [
            "",
            f"确认上架请回复：**确认 {card.match_key}**",
            f"取消请回复：**取消 {card.match_key}**",
        ]
    )
    return "\n".join(lines)


def dispatch_confirm_card(card: UkConfirmCard, *, file: Any = None) -> str:
    text = format_confirm_card_dialog(card)
    out = file or sys.stdout
    print(CONFIRM_MARKER_BEGIN, file=out)
    print(text, file=out)
    print(CONFIRM_MARKER_END, file=out)
    print(f"{CONFIRM_TOKEN_PREFIX}{card.token}", file=out)
    return text


def prepare_uk_publish_confirm(
    *,
    pop_quote: Any,
    collect_box_detail_id: int,
    seller_sku: str,
    master_product_id: str,
    master_region: str = "PH",
    stock: int = 200,
    product_name: str = "",
    main_image_url: str = "",
) -> tuple[UkConfirmCard, str]:
    card = create_confirm_card(
        pop_quote=pop_quote,
        collect_box_detail_id=collect_box_detail_id,
        seller_sku=seller_sku,
        master_product_id=master_product_id,
        master_region=master_region,
        stock=stock,
        product_name=product_name,
        main_image_url=main_image_url,
    )
    text = dispatch_confirm_card(card)
    return card, text
