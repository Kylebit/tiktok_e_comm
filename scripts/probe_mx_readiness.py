"""Find Miaoshou collect-box items linked to MX shop and check publish readiness."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open

MX_SHOP_ID = 16265910
LIST_PATH = "/open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list"
DETAIL_PATH = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info"
WAREHOUSE_PATH = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list"
CATEGORY_TREE_PATH = "/open/v1/product/collect_box/tiktok/collect_box/get_category_tree_by_site"


def _iter_status(status: str, max_pages: int = 20):
    for page in range(1, max_pages + 1):
        r = post_open(
            LIST_PATH,
            {
                "pageNo": page,
                "pageSize": 50,
                "filter": {"status": status, "sourceItemIdKeyword": ""},
            },
        )
        items = (r.get("data") or {}).get("detailList") or []
        if not items:
            break
        yield from items
        if len(items) < 50:
            break


def mx_linked_items():
    found = []
    for status in ("notPublished", "published"):
        for it in _iter_status(status):
            shops = it.get("collectBoxDetailShopList") or []
            if any(int(s.get("shopId") or 0) == MX_SHOP_ID for s in shops):
                found.append(
                    {
                        "status": status,
                        "collectBoxDetailId": it.get("collectBoxDetailId"),
                        "title": it.get("title"),
                        "price": it.get("price"),
                        "copyType": it.get("copyType"),
                        "shops": [
                            f"{s.get('site')}:{s.get('shopId')}" for s in shops
                        ],
                    }
                )
    return found


def readiness(detail_id: int) -> dict:
    r = post_open(
        DETAIL_PATH,
        {"detailId": detail_id, "shopId": MX_SHOP_ID},
    )
    data = r.get("data") or {}
    info = data.get("shopCollectItemInfo") or {}
    sku_map = info.get("skuMap") or {}
    issues = []
    if not info.get("cid"):
        issues.append("missing_category")
    if not info.get("title"):
        issues.append("missing_title")
    if not info.get("imgUrls"):
        issues.append("missing_images")
    for k, sku in sku_map.items():
        if not sku.get("price"):
            issues.append(f"sku_{k}_missing_price")
        if float(sku.get("weight") or 0) <= 0:
            issues.append(f"sku_{k}_missing_weight")
        wh = sku.get("shopIdToWarehouseIdAndStockMap") or {}
        if not wh:
            issues.append(f"sku_{k}_missing_warehouse_stock")
    return {
        "api_code": r.get("code"),
        "issues": issues,
        "title": info.get("title"),
        "cid": info.get("cid"),
        "sku_count": len(sku_map),
        "claimToShopIds": data.get("claimToShopIds"),
    }


def main() -> int:
    print(f"MX shopId={MX_SHOP_ID}\n")

    wh = post_open(WAREHOUSE_PATH, {"shopIds": [MX_SHOP_ID]})
    print("=== MX warehouses ===")
    print(json.dumps(wh, ensure_ascii=False, indent=2)[:2000])

    tree = post_open(CATEGORY_TREE_PATH, {"site": "MX"})
    print("\n=== MX category tree (top keys) ===")
    cate = (tree.get("data") or {}).get("cateTree") or {}
    print(f"root categories: {len(cate)} code={tree.get('code')}")

    linked = mx_linked_items()
    print(f"\n=== items linked to MX shop: {len(linked)} ===")
    for row in linked[:10]:
        print(json.dumps(row, ensure_ascii=False))

    if linked:
        did = int(linked[0]["collectBoxDetailId"])
        print(f"\n=== readiness for detailId={did} ===")
        print(json.dumps(readiness(did), ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
