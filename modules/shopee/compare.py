"""TikTok vs Shopee 商品对比（SKU 后四位对齐）。"""

from __future__ import annotations

from core.db import connect, init_db
from modules.catalog.sku_key import (
    SEA_REGIONS,
    shopee_match_key,
    tk_match_key,
    tk_region,
)


def compare_report() -> str:
    init_db()
    conn = connect()
    tk_by_reg: dict[str, dict[str, str]] = {r: {} for r in SEA_REGIONS}
    for r in conn.execute(
        """SELECT seller_sku, shop_cipher, product_name FROM products
           WHERE seller_sku != '' AND status = 'ACTIVATE'"""
    ):
        reg = tk_region(r["shop_cipher"])
        if reg not in tk_by_reg:
            continue
        key = tk_match_key(r["seller_sku"])
        if key:
            tk_by_reg[reg][key] = r["product_name"] or ""

    sp_by_reg: dict[str, dict[str, str]] = {r: {} for r in SEA_REGIONS}
    for r in conn.execute(
        """SELECT seller_sku, region, product_name FROM shopee_products
           WHERE seller_sku != '' AND status LIKE '%NORMAL%'"""
    ):
        reg = (r["region"] or "").upper()
        if reg not in sp_by_reg:
            continue
        key = shopee_match_key(r["seller_sku"])
        if key:
            sp_by_reg[reg][key] = r["product_name"] or ""
    conn.close()

    lines = ["══ TikTok vs Shopee SKU 对比（后四位对齐）══", ""]
    total_tk = total_sp = total_both = 0
    for reg in SEA_REGIONS:
        tk_set = set(tk_by_reg[reg])
        sp_set = set(sp_by_reg[reg])
        both = tk_set & sp_set
        lines.append(
            f"[{reg}] TK {len(tk_set)} · Shopee {len(sp_set)} · 已对齐 {len(both)} · "
            f"仅TK {len(tk_set - sp_set)} · 仅Shopee {len(sp_set - tk_set)}"
        )
        total_tk += len(tk_set)
        total_sp += len(sp_set)
        total_both += len(both)
    lines.extend(
        [
            "",
            f"合计对齐键: TK {total_tk} · Shopee {total_sp} · 交集 {total_both}",
            "",
            "规则：TK 660002 → 对齐码 0002 ↔ Shopee SKU 0002",
            "Web 目录: python3 main.py serve --page catalog",
        ]
    )
    return "\n".join(lines)
