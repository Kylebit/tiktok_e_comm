"""Pure Seller-SKU identity and normal new-product sequence policy.

Historical 9xxx keys remain occupied identities, not normal sequence evidence.
Only documented SEA country aliases may resolve to a four-digit internal key;
an arbitrary numeric product/platform ID must never be truncated into one.
"""
from __future__ import annotations

import re


# Existing catalogue convention: modules/catalog/logistics_weights.py.
REGIONAL_SKU_PREFIXES = ("66", "77", "88", "99")


def canonical_seller_sku(value: object) -> str:
    raw = str(value if value is not None else "").strip()
    if re.fullmatch(r"[0-9]{1,4}", raw):
        return raw.zfill(4)
    if re.fullmatch(r"[0-9]{6}", raw) and raw[:2] in REGIONAL_SKU_PREFIXES:
        return raw[2:]
    # Registered B links (for example 2000431) and unknown formats retain their
    # full raw identity. They are not inferred aliases of their last four digits.
    return raw


def next_available_seller_skus(
    catalog_skus: object,
    reserved_skus: object,
    *,
    requested_count: int,
    additional_occupied_skus: object = (),
) -> tuple[str, ...]:
    # Keep every raw/canonical identity for collisions. The high-water subset
    # is deliberately separate; excluding an outlier never releases it.
    raw_values = (*catalog_skus, *reserved_skus, *additional_occupied_skus)
    occupied = {str(value if value is not None else "").strip() for value in raw_values}
    occupied.update(canonical_seller_sku(value) for value in raw_values)
    normal_numbers = {
        int(value) for value in occupied
        if re.fullmatch(r"[0-8][0-9]{3}", value)
    }
    width = max(1, int(requested_count or 1))
    start = max(normal_numbers, default=0) + 1
    while start + width - 1 <= 8999:
        block = tuple(f"{number:04d}" for number in range(start, start + width))
        if all(value not in occupied for value in block):
            return block
        start += 1
    return ()
