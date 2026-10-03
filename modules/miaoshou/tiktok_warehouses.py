from __future__ import annotations

import re
from typing import Mapping


# Miaoshou exposes the HomeBloom China-mainland pickup warehouse under the
# operator-defined name below.  Keep the alias scoped to the exact HomeBloom
# shops whose official warehouse readback confirmed it; a same-named warehouse
# from another shop is not automatically trusted.
MAINLAND_PICKUP_ALIASES_BY_SHOP_ID = {
    "15173238": frozenset({"瓯江口"}),
    "16770639": frozenset({"瓯江口"}),
    "16770557": frozenset({"瓯江口"}),
    "16783702": frozenset({"瓯江口"}),
    "16265910": frozenset({"中国仓库"}),
    "10204699": frozenset({"自有仓库"}),
}

MAINLAND_PICKUP_WAREHOUSE_SEMANTIC_BY_TARGET = {
    "tiktok:LH_PH": "The Chinese mainland Pickup Warehouse",
    "tiktok:LH_MY": "The Chinese mainland Pickup Warehouse",
    "tiktok:LH_TH": "The Chinese mainland Pickup Warehouse",
    "tiktok:LH_VN": "The Chinese mainland Pickup Warehouse",
    "tiktok:HB_PH": "瓯江口",
    "tiktok:HB_MY": "瓯江口",
    "tiktok:HB_TH": "瓯江口",
    "tiktok:HB_VN": "瓯江口",
    "tiktok:MX": "中国仓库",
    "tiktok:GB": "自有仓库",
}


def mainland_pickup_warehouse_semantic(target_label: str) -> str:
    """Return the reviewed display semantic for one exact TikTok target.

    This is never a warehouse-ID resolver.  Execution must still read the
    current shop's warehouses and validate the returned ID before any write.
    """

    return MAINLAND_PICKUP_WAREHOUSE_SEMANTIC_BY_TARGET.get(
        str(target_label), ""
    )

# Compatibility export for callers that imported the earlier narrow registry.
HOMEBLOOM_MAINLAND_PICKUP_ALIASES_BY_SHOP_ID = {
    shop_id: aliases
    for shop_id, aliases in MAINLAND_PICKUP_ALIASES_BY_SHOP_ID.items()
    if shop_id in {"15173238", "16770639", "16770557", "16783702"}
}


def is_china_mainland_pickup_warehouse(
    row: Mapping[str, object], *, shop_id: str
) -> bool:
    name = str(row.get("warehouseName") or row.get("name") or "").strip()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", name.lower())
    standard_name = (
        "chinesemainland" in normalized and "pickupwarehouse" in normalized
    ) or ("中国大陆" in normalized and "揽收仓" in normalized)
    if standard_name:
        return True
    return name in MAINLAND_PICKUP_ALIASES_BY_SHOP_ID.get(
        str(shop_id), frozenset()
    )


__all__ = [
    "HOMEBLOOM_MAINLAND_PICKUP_ALIASES_BY_SHOP_ID",
    "MAINLAND_PICKUP_ALIASES_BY_SHOP_ID",
    "MAINLAND_PICKUP_WAREHOUSE_SEMANTIC_BY_TARGET",
    "is_china_mainland_pickup_warehouse",
    "mainland_pickup_warehouse_semantic",
]
