# -*- coding: utf-8 -*-
"""Seller SKU 对齐：只看最后四位（忽略前缀两位，如 99/66/77）。

例：990021 / 0021 / 21 → 统一键 0021
"""

from __future__ import annotations

import re


def digits_only(sku: str) -> str:
    return re.sub(r"\D+", "", str(sku or "").strip())


def seller_sku_tail4(sku: str) -> str:
    """数字 SKU 取末四位并零填充；非数字原样返回 strip。"""
    d = digits_only(sku)
    if not d:
        return str(sku or "").strip()
    return d[-4:].zfill(4)


def sku_variants_for_lookup(sku: str) -> list[str]:
    """查询用候选：原始、末四位、去零、六位（前缀常见写法）。"""
    raw = str(sku or "").strip()
    if not raw:
        return []
    tail = seller_sku_tail4(raw)
    out: list[str] = []
    for v in (
        raw,
        tail,
        tail.lstrip("0") or "0",
        f"99{tail}",
        f"66{tail}",
        f"77{tail}",
        digits_only(raw),
    ):
        if v and v not in out:
            out.append(v)
    return out


def same_seller_sku(a: str, b: str) -> bool:
    da, db = digits_only(a), digits_only(b)
    if da and db:
        return seller_sku_tail4(da) == seller_sku_tail4(db)
    return str(a or "").strip() == str(b or "").strip()
