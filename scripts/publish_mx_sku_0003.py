"""Publish PH seller_sku 770003 (sku suffix 0003) to TikTok MX via Miaoshou."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open

MX_SHOP_ID = 16265910
TARGET_SELLER_SKU = "770003"
TARGET_PRODUCT_ID = "1732379849767749563"
TARGET_SKU_ID = "1732379849767815099"

COMMON_LIST = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list"
COMMON_DETAIL = "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
CLAIM_PLATFORM = "/open/v1/product/common_collect_box/common_collect_box/claimed"
TK_LIST = "/open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list"
TK_DETAIL = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info"
CLAIM_SHOP = "/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop"
PUBLISH = "/open/v1/product/collect_box/tiktok/collect_box/save_move_collect_task"
WAREHOUSE = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list"


def find_common_detail_id() -> int | None:
    for page in range(1, 20):
        r = post_open(
            COMMON_LIST,
            {
                "pageNo": page,
                "pageSize": 100,
                "filter": {
                    "tabPaneName": "all",
                    "sourceItemIdKeyword": TARGET_PRODUCT_ID,
                },
            },
        )
        for it in (r.get("data") or {}).get("detailList") or []:
            if (it.get("commonCollectBoxGroupName") or "") == "贴纸1":
                did = int(it["commonCollectBoxDetailId"])
                dr = post_open(COMMON_DETAIL, {"commonCollectBoxDetailId": did})
                if dr.get("result") != "success":
                    continue
                detail = (dr.get("data") or {}).get("editCommonCollectBoxDetail") or {}
                for sku in (detail.get("skuMap") or {}).values():
                    if str(sku.get("itemNum") or "") == TARGET_SELLER_SKU:
                        return did
                if str(TARGET_PRODUCT_ID) in json.dumps(detail.get("sourceList") or []):
                    return did
        if not (r.get("data") or {}).get("detailList"):
            break

    # fallback: scan 贴纸1 success items
    for page in range(1, 10):
        r = post_open(
            COMMON_LIST,
            {
                "pageNo": page,
                "pageSize": 100,
                "filter": {"tabPaneName": "all", "sourceItemIdKeyword": ""},
            },
        )
        items = (r.get("data") or {}).get("detailList") or []
        for it in items:
            if it.get("commonCollectBoxGroupName") != "贴纸1":
                continue
            if it.get("status") != "success":
                continue
            did = int(it["commonCollectBoxDetailId"])
            dr = post_open(COMMON_DETAIL, {"commonCollectBoxDetailId": did})
            if dr.get("result") != "success":
                continue
            detail = (dr.get("data") or {}).get("editCommonCollectBoxDetail") or {}
            for sku in (detail.get("skuMap") or {}).values():
                if str(sku.get("itemNum") or "") == TARGET_SELLER_SKU:
                    return did
        if len(items) < 100:
            break
    return None


def claim_to_tiktok(common_id: int) -> int | None:
    r = post_open(
        CLAIM_PLATFORM,
        {
            "detailSerialNumberPlatformList": [
                {"detailId": common_id, "platform": "tiktok", "serialNumber": 1}
            ]
        },
    )
    print("claim_to_tiktok:", json.dumps(r, ensure_ascii=False)[:1500])
    data = r.get("data") or {}
    mapping = data.get("platformCollectBoxDetailIdMap") or data.get("detailIdMap") or {}
    if mapping:
        for k, v in mapping.items():
            return int(v)
    # already claimed - search TK box by common id
    for status in ("notPublished", "published"):
        for page in range(1, 20):
            r2 = post_open(
                TK_LIST,
                {
                    "pageNo": page,
                    "pageSize": 50,
                    "filter": {"status": status, "sourceItemIdKeyword": ""},
                },
            )
            for it in (r2.get("data") or {}).get("detailList") or []:
                if str(it.get("commonCollectBoxDetailId") or "") == str(common_id):
                    return int(it["collectBoxDetailId"])
            if len((r2.get("data") or {}).get("detailList") or []) < 50:
                break
    return None


def readiness(tk_detail_id: int) -> dict:
    r = post_open(TK_DETAIL, {"detailId": tk_detail_id, "shopId": MX_SHOP_ID})
    data = r.get("data") or {}
    info = data.get("shopCollectItemInfo") or {}
    issues = []
    if not info.get("cid"):
        issues.append("missing_category")
    if not info.get("title"):
        issues.append("missing_title")
    sku_map = info.get("skuMap") or {}
    for k, sku in sku_map.items():
        if str(sku.get("itemNum") or "") == TARGET_SELLER_SKU or len(sku_map) == 1:
            if not sku.get("price"):
                issues.append("missing_price")
            if float(sku.get("weight") or 0) <= 0:
                issues.append("missing_weight")
            wh = sku.get("shopIdToWarehouseIdAndStockMap") or {}
            if not wh:
                issues.append("missing_warehouse_stock")
    return {
        "ready": not issues,
        "issues": issues,
        "claimToShopIds": data.get("claimToShopIds"),
        "title": info.get("title"),
        "cid": info.get("cid"),
        "sku_keys": list(sku_map.keys()),
        "raw": r,
    }


def main() -> int:
    print(f"Target seller_sku={TARGET_SELLER_SKU} product_id={TARGET_PRODUCT_ID} sku_id={TARGET_SKU_ID}")
    print(f"MX shopId={MX_SHOP_ID}\n")

    common_id = find_common_detail_id()
    if not common_id:
        print("ERROR: common collect box item not found for 770003")
        return 1
    print(f"commonCollectBoxDetailId={common_id}")

    tk_id = claim_to_tiktok(common_id)
    if not tk_id:
        print("ERROR: could not get TikTok collectBoxDetailId")
        return 1
    print(f"collectBoxDetailId={tk_id}")

    # claim to MX if needed
    pre = readiness(tk_id)
    claim_ids = [int(x) for x in (pre.get("claimToShopIds") or [])]
    if MX_SHOP_ID not in claim_ids:
        print("claiming to MX shop...")
        cr = post_open(
            CLAIM_SHOP,
            {"detailIds": [tk_id], "shopIds": [MX_SHOP_ID]},
        )
        print("claim_to_shop:", json.dumps(cr, ensure_ascii=False))
        time.sleep(1)
        pre = readiness(tk_id)

    print("\nreadiness:", json.dumps({k: pre[k] for k in ("ready", "issues", "title", "cid", "claimToShopIds", "sku_keys")}, ensure_ascii=False, indent=2))

    if not pre["ready"]:
        print("\nSTOP: not ready to publish. Issues:", pre["issues"])
        return 2

    print("\npublishing...")
    pr = post_open(PUBLISH, {"detailIds": [tk_id], "shopIds": [MX_SHOP_ID]})
    print("publish:", json.dumps(pr, ensure_ascii=False, indent=2))
    return 0 if pr.get("result") == "success" or pr.get("code") in ("200", "success", "0") else 3


if __name__ == "__main__":
    raise SystemExit(main())
