# -*- coding: utf-8 -*-
"""Seller SKU 对齐：只看最后四位（忽略前缀两位，如 99/66/77）。

例：990021 / 0021 / 21 → 统一键 0021
"""

from __future__ import annotations

import re


def digits_only(sku: str) -> str:
    return re.sub(r"\D+", "", str(sku or "").strip())


def seller_sku_tail4(sku: str) -> str:
    """数字 SKU 取末四位并零填充；非纯数字 SKU 保持原样。

    Shopee 的历史快照里存在 ``item_id_description_50cm`` 一类复合值。
    把其中所有数字拼接后取末四位会把尺寸、数量误当成 Seller SKU，
    甚至与真实四位 SKU 碰撞，因此只有纯数字值才能参与末四位对齐。
    """
    raw = str(sku or "").strip()
    if not raw or not re.fullmatch(r"\d+", raw):
        return raw
    return raw[-4:].zfill(4)


def sku_variants_for_lookup(sku: str) -> list[str]:
    """查询用候选：原始、末四位、去零、六位（前缀常见写法）。"""
    raw = str(sku or "").strip()
    if not raw:
        return []
    if not re.fullmatch(r"\d+", raw):
        return [raw]
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
    raw_a = str(a or "").strip()
    raw_b = str(b or "").strip()
    if re.fullmatch(r"\d+", raw_a) and re.fullmatch(r"\d+", raw_b):
        return seller_sku_tail4(raw_a) == seller_sku_tail4(raw_b)
    return raw_a == raw_b
