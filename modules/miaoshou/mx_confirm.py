"""MX 上架前用户确认（Web 收件箱 / Cursor 对话框；不在飞书群审批）。"""
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
from modules.miaoshou.mx_checks import quote_volumetric_dominates

CONFIRM_DIR = ROOT / "data" / "mx_confirm"
MX_EXIT_NEEDS_USER_CONFIRM = 5
CONFIRM_MARKER_BEGIN = "=== MX_CONFIRM_BEGIN ==="
CONFIRM_MARKER_END = "=== MX_CONFIRM_END ==="
CONFIRM_TOKEN_PREFIX = "MX_CONFIRM_TOKEN:"

_lock = threading.Lock()


@dataclass
class MxGroupVariantLine:
    match_key: str
    seller_sku: str
    variant_label: str
    list_price_ceil_mxn: int
    sale_price_mxn: float
    weight_kg: float
    net_profit_mxn: float


@dataclass
class MxGroupConfirmCard:
    token: str
    status: str  # pending | approved | rejected
    match_keys: list[str]
    product_name: str
    main_image_url: str
    package_cm: str
    stock: int
    collect_box_detail_id: int
    master_region: str
    master_product_id: str
    variants: list[MxGroupVariantLine] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    approved_at: float | None = None
    rejected_at: float | None = None


@dataclass
class MxConfirmCard:
    token: str
    status: str  # pending | approved | rejected
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
    logistics_hidden_mxn: float
    sale_price_mxn: float
    list_price_ceil_mxn: int
    stock: int
    volumetric_dominates: bool
    collect_box_detail_id: int
    master_region: str
    master_product_id: str
    cny_mxn: float
    net_profit_mxn: float
    sfp_adjustment: str | None = None
    # POP 明细（确认卡片展示）
    cost_mxn: float = 0.0
    pop_sale_mxn: float = 0.0
    list_price_mxn: float = 0.0
    import_tax_mxn: float = 0.0
    platform_commission_mxn: float = 0.0
    sfp_fee_mxn: float = 0.0
    per_item_fee_mxn: float = 0.0
    affiliate_mxn: float = 0.0
    ad_mxn: float = 0.0
    net_income_mxn: float = 0.0
    profit_margin_on_sale_pct: float = 0.0
    shipping_tier_kg: float = 0.0
    shipping_card_mxn: float = 0.0
    created_at: float = field(default_factory=time.time)
    approved_at: float | None = None
    rejected_at: float | None = None


def _path(token: str) -> Path:
    safe = "".join(c for c in token if c.isalnum())
    return CONFIRM_DIR / f"{safe}.json"


def _group_path(token: str) -> Path:
    safe = "".join(c for c in token if c.isalnum())
    return CONFIRM_DIR / f"group_{safe}.json"


def _write(card: MxConfirmCard) -> None:
    CONFIRM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(card.token).with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(card), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path(card.token))


def _read(token: str) -> MxConfirmCard | None:
    p = _path(token)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return MxConfirmCard(**data)


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
) -> MxConfirmCard:
    """创建待确认卡片并落盘。"""
    mk = tk_match_key(seller_sku)
    token = new_token()
    card = MxConfirmCard(
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
        logistics_hidden_mxn=float(pop_quote.logistics_hidden_mxn),
        sale_price_mxn=float(pop_quote.sale_price_mxn),
        list_price_ceil_mxn=int(pop_quote.list_price_ceil_mxn),
        stock=int(stock),
        volumetric_dominates=quote_volumetric_dominates(pop_quote),
        collect_box_detail_id=int(collect_box_detail_id),
        master_region=master_region,
        master_product_id=master_product_id,
        cny_mxn=float(pop_quote.cny_mxn),
        net_profit_mxn=float(pop_quote.net_profit_mxn),
        sfp_adjustment=getattr(pop_quote, "sfp_adjustment", None),
        cost_mxn=float(pop_quote.cost_mxn),
        pop_sale_mxn=float(pop_quote.pop_sale_mxn),
        list_price_mxn=float(pop_quote.list_price_mxn),
        import_tax_mxn=float(pop_quote.import_tax_mxn),
        platform_commission_mxn=float(pop_quote.platform_commission_mxn),
        sfp_fee_mxn=float(pop_quote.sfp_fee_mxn),
        per_item_fee_mxn=float(pop_quote.per_item_fee_mxn),
        affiliate_mxn=float(pop_quote.affiliate_mxn),
        ad_mxn=float(pop_quote.ad_mxn),
        net_income_mxn=float(pop_quote.net_income_mxn),
        profit_margin_on_sale_pct=float(pop_quote.profit_margin_on_sale_pct),
        shipping_tier_kg=float(pop_quote.shipping_tier_kg),
        shipping_card_mxn=float(pop_quote.shipping_card_mxn),
    )
    with _lock:
        _write(card)
    return card


def get_confirm(token: str) -> MxConfirmCard | None:
    with _lock:
        return _read(token)


def _write_group(card: MxGroupConfirmCard) -> None:
    CONFIRM_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(card)
    data["variants"] = [asdict(v) for v in card.variants]
    tmp = _group_path(card.token).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_group_path(card.token))


def _read_group(token: str) -> MxGroupConfirmCard | None:
    p = _group_path(token)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    variants = [MxGroupVariantLine(**v) for v in data.pop("variants", [])]
    return MxGroupConfirmCard(**data, variants=variants)


def get_group_confirm(token: str) -> MxGroupConfirmCard | None:
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
) -> MxGroupConfirmCard:
    """variant_quotes: [(pop_quote, seller_sku, variant_label), ...]"""
    token = new_token()
    lines: list[MxGroupVariantLine] = []
    for pop_quote, seller_sku, variant_label in variant_quotes:
        lines.append(
            MxGroupVariantLine(
                match_key=tk_match_key(seller_sku),
                seller_sku=seller_sku,
                variant_label=variant_label,
                list_price_ceil_mxn=int(pop_quote.list_price_ceil_mxn),
                sale_price_mxn=float(pop_quote.sale_price_mxn),
                weight_kg=float(pop_quote.weight_kg),
                net_profit_mxn=float(pop_quote.net_profit_mxn),
            )
        )
    card = MxGroupConfirmCard(
        token=token,
        status="pending",
        match_keys=[tk_match_key(k) if not str(k).isdigit() else str(k).zfill(4)[-4:] for k in match_keys],
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


def approve_group_confirm(token: str) -> MxGroupConfirmCard:
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


def format_group_confirm_card_dialog(card: MxGroupConfirmCard) -> str:
    primary = card.match_keys[0] if card.match_keys else "????"
    lines = [
        f"## MX 多规格上架确认 · {primary}–{card.match_keys[-1] if len(card.match_keys) > 1 else primary}",
        "",
        f"![主图]({card.main_image_url})" if card.main_image_url else "（无主图）",
        "",
        f"**标题** · {card.product_name[:120]}",
        f"**对齐码** · `{', '.join(card.match_keys)}` · **TK 采集箱** · `{card.collect_box_detail_id}`",
        f"**库存** · 每规格 {card.stock} · **包裹尺寸** · {card.package_cm}",
        "",
        "### 各规格上传原价（写入 price / priceIncludeVat）",
        "| 对齐码 | 规格 | 上传原价 MXN | POP折后 | 净利 |",
        "|--------|------|-------------|---------|------|",
    ]
    for v in card.variants:
        lines.append(
            f"| {v.match_key} | {v.variant_label} | **{v.list_price_ceil_mxn}** | "
            f"{v.sale_price_mxn:.0f} | {v.net_profit_mxn:.0f} |"
        )
    lines.extend(
        [
            "",
            "店铺折扣由你在 TikTok 后台自行设置；POP 折后价仅用于测算。",
            "",
            f"确认整组上架请回复：**确认 {primary}**",
            f"取消请回复：**取消 {primary}**",
        ]
    )
    return "\n".join(lines)


def prepare_mx_group_publish_confirm(
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
) -> tuple[MxGroupConfirmCard, str]:
    card = create_group_confirm_card(
        match_keys=match_keys,
        collect_box_detail_id=collect_box_detail_id,
        master_product_id=master_product_id,
        master_region=master_region,
        stock=stock,
        product_name=product_name,
        main_image_url=main_image_url,
        package_cm=package_cm,
        variant_quotes=variant_quotes,
    )
    text = format_group_confirm_card_dialog(card)
    out = sys.stdout
    print(CONFIRM_MARKER_BEGIN, file=out)
    print(text, file=out)
    print(CONFIRM_MARKER_END, file=out)
    print(f"{CONFIRM_TOKEN_PREFIX}{card.token}", file=out)
    return card, text


def confirm_status(token: str) -> str | None:
    card = get_confirm(token)
    return card.status if card else None


def approve_confirm(token: str) -> MxConfirmCard:
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


def reject_confirm(token: str) -> MxConfirmCard:
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


def reject_group_confirm(token: str) -> MxGroupConfirmCard:
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


def mark_published(token: str) -> MxConfirmCard:
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
    """用户尚未在对话框确认上架。"""


def format_confirm_card_dialog(card: MxConfirmCard) -> str:
    """供 Agent 对话框展示的确认卡片文本。"""
    lines = [
        f"## MX 上架确认 · {card.match_key}",
        "",
        f"![主图]({card.main_image_url})" if card.main_image_url else "（无主图）",
        "",
        f"**标题** · {card.product_name[:120]}",
        f"**对齐码** · `{card.match_key}` · **商家 SKU** · `{card.seller_sku}`",
        f"**库存** · {card.stock} · **TK 采集箱** · `{card.collect_box_detail_id}`",
        "",
        "### 物流",
        f"- 实重 **{card.weight_kg:.3f} kg**（{card.weight_source}）",
        f"- 体积重 **{card.volumetric_kg:.3f} kg** · 尺寸 **{card.package_cm}** · 计费 **{card.billable_kg:.3f} kg**",
        f"- 运费档 ≤{card.shipping_tier_kg:g} kg · 价卡 **{card.shipping_card_mxn:.0f} MXN** · 藏价 **{card.logistics_hidden_mxn:.2f} MXN**",
        "",
        "### 价格（上传 vs 测算）",
        f"- **上传原价 ceil** · **{card.list_price_ceil_mxn} MXN**（写入 price / priceIncludeVat）",
        f"- 折前原价（精确）· {card.list_price_mxn:.2f} MXN",
        f"- POP 测算折后 · {card.sale_price_mxn:.2f} MXN（**不上传**，折扣你在后台自设）",
        "",
        "### 成本与汇率",
        f"- 采购成本 · ¥{card.cost_cny:.2f} → **{card.cost_mxn:.2f} MXN**（{card.cost_source}）",
        f"- 汇率 CNY→MXN · **{card.cny_mxn}**",
        "",
        f"### POP 折后价支出明细（测算基准 {card.sale_price_mxn:.2f} MXN）",
        "| 项目 | MXN |",
        "|------|-----|",
        f"| 采购成本 | {card.cost_mxn:.2f} |",
        f"| 藏价物流 | {card.logistics_hidden_mxn:.2f} |",
        f"| 进口税 | {card.import_tax_mxn:.2f} |",
        f"| 平台佣金 6% | {card.platform_commission_mxn:.2f} |",
        f"| SFP 8% | {card.sfp_fee_mxn:.2f} |",
        f"| 达人 8% | {card.affiliate_mxn:.2f} |",
        f"| 广告 10% | {card.ad_mxn:.2f} |",
        f"| 每件费 | {card.per_item_fee_mxn:.2f} |",
        f"| **合计支出** | **{card.cost_mxn + card.logistics_hidden_mxn + card.import_tax_mxn + card.platform_commission_mxn + card.sfp_fee_mxn + card.affiliate_mxn + card.ad_mxn + card.per_item_fee_mxn:.2f}** |",
        "",
        "### 利润（按 POP 折后测算）",
        f"- 折后售价 · {card.sale_price_mxn:.2f} MXN",
        f"- 预估净利 · **{card.net_profit_mxn:.2f} MXN**（占折后售价 **{card.profit_margin_on_sale_pct:.1f}%**）",
    ]
    if card.sfp_adjustment:
        lines.append(f"- SFP 抬价 · {card.sfp_adjustment}")
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


def send_feishu_confirm(card: MxConfirmCard) -> bool:
    """飞书自建应用 / webhook：推送与对话框一致的富文本审批卡。"""
    try:
        from modules.hub.feishu_app import app_ready
        from modules.miaoshou.mx_feishu_approval import (
            build_single_mx_approval_card,
            default_chat_id,
            send_mx_approval_card,
        )

        if app_ready():
            interactive = build_single_mx_approval_card(
                card,
                task_id=f"MX_CONFIRM_{card.token}",
                title=f"MX 上架确认 · {card.match_key}",
                risk_note="批准后将执行 save + publish（西班牙语）",
            )
            send_mx_approval_card(interactive, chat_id=default_chat_id())
            return True
    except Exception:
        pass
    try:
        from core.config import get
        from modules.hub.feishu import send_post

        cfg = get("feishu") or {}
        if cfg.get("enabled") and cfg.get("webhook_url"):
            title = f"MX 上架确认 · {card.match_key}"
            body = format_confirm_card_dialog(card)
            rows = [[{"tag": "text", "text": body[:4000]}]]
            send_post(title, rows)
            return True
    except Exception:
        return False
    return False


def dispatch_confirm_card(card: MxConfirmCard, *, file: Any = None) -> str:
    """输出到对话框（stdout）；飞书打通后同时发飞书。"""
    text = format_confirm_card_dialog(card)
    out = file or sys.stdout
    print(CONFIRM_MARKER_BEGIN, file=out)
    print(text, file=out)
    print(CONFIRM_MARKER_END, file=out)
    print(f"{CONFIRM_TOKEN_PREFIX}{card.token}", file=out)
    send_feishu_confirm(card)
    return text


def prepare_mx_publish_confirm(
    *,
    pop_quote: Any,
    collect_box_detail_id: int,
    seller_sku: str,
    master_product_id: str,
    master_region: str = "PH",
    stock: int = 200,
    product_name: str = "",
    main_image_url: str = "",
) -> tuple[MxConfirmCard, str]:
    """创建确认单并在对话框展示，返回 (card, 展示文本)。"""
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


def wait_for_terminal_confirm(token: str) -> bool:
    """终端交互：输入 确认 / 取消。"""
    card = get_confirm(token)
    if not card:
        return False
    prompt = f"确认上架 {card.match_key}？输入 确认 / 取消: "
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return False
    if ans in ("确认", "y", "yes", "ok"):
        approve_confirm(token)
        return True
    if ans in ("取消", "n", "no"):
        reject_confirm(token)
    return False
