"""SKU 对齐键：TikTok 6 位码后四位 ↔ Shopee 4 位码。"""

from __future__ import annotations

import re

SEA_REGIONS = ("MY", "VN", "TH", "PH")

_CIPHER_PREFIX_REGION = (
    ("UK_IMPORT", "GB"),
    ("ROW_ssuS0w", "MY"),
    ("ROW_QTYxtw", "VN"),
    ("ROW_Ps2udQ", "TH"),
    ("ROW_hKlht", "PH"),
)


def tk_region(shop_cipher: str) -> str:
    for prefix, reg in _CIPHER_PREFIX_REGION:
        if (shop_cipher or "").startswith(prefix):
            return reg
    return "?"


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def tk_match_key(seller_sku: str) -> str:
    """TikTok 660961 → 0961（取数字串后四位）。"""
    d = _digits(seller_sku)
    if len(d) < 4:
        return d.zfill(4) if d else ""
    return d[-4:]


def shopee_match_key(seller_sku: str) -> str:
    """Shopee 0002 或 606xxx_规格 → 0002（4 位码；长码取后四位）。"""
    raw = (seller_sku or "").split("_", 1)[0].strip()
    d = _digits(raw)
    if not d:
        return ""
    if len(d) <= 4:
        return d.zfill(4)
    return d[-4:]


def shopee_sku_needs_edit(seller_sku: str) -> bool:
    """规格货号为空或非标准（非纯 4 位 / 非 66xxxx），需人工填写对齐码。"""
    raw = (seller_sku or "").strip()
    if not raw:
        return True
    base = raw.split("_", 1)[0].strip()
    d = _digits(base)
    if not d:
        return True
    if len(d) == 4:
        return False
    if len(d) == 6 and d.startswith("66"):
        return False
    return True


def parse_search_key(query: str) -> str:
    """用户输入 → 4 位匹配键（支持 660961 / 0002 / 0961）。"""
    q = (query or "").strip()
    if not q:
        return ""
    d = _digits(q)
    if not d:
        return ""
    if len(d) <= 4:
        return d.zfill(4)
    return d[-4:]
