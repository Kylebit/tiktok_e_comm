"""Confirm MX publish status for collectBoxDetailId 2059296237 / seller_sku 770003."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open

TK_DETAIL_ID = 2059296237
MX_SHOP_ID = 16265910
PRODUCT_ID = "1732379849767749563"
TARGET_SKU = "770003"

LIST_PATH = "/open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list"
SHOP_DETAIL = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info"


def find_in_list(status: str) -> dict | None:
    for page in range(1, 30):
        r = post_open(
            LIST_PATH,
            {
                "pageNo": page,
                "pageSize": 50,
                "filter": {"status": status, "sourceItemIdKeyword": ""},
            },
        )
        for it in (r.get("data") or {}).get("detailList") or []:
            if str(it.get("collectBoxDetailId")) == str(TK_DETAIL_ID):
                return it
        if len((r.get("data") or {}).get("detailList") or []) < 50:
            break
    return None


def mx_shop_entry(item: dict) -> dict | None:
    for s in item.get("collectBoxDetailShopList") or []:
        if int(s.get("shopId") or 0) == MX_SHOP_ID:
            return s
    return None


def main() -> int:
    print(f"Checking TK collectBoxDetailId={TK_DETAIL_ID} MX shopId={MX_SHOP_ID}\n")

    for status in ("notPublished", "timingPublish", "published"):
        item = find_in_list(status)
        if item:
            mx = mx_shop_entry(item)
            print(f"=== list status={status} ===")
            print(f"title: {(item.get('title') or '')[:90]}")
            print(f"price: {item.get('price')} stock: {item.get('stock')}")
            print(f"copyType: {item.get('copyType')}")
            if mx:
                print("MX shop entry:", json.dumps(mx, ensure_ascii=False, indent=2))
            else:
                shops = [
                    f"{s.get('site')}:{s.get('shopId')}"
                    for s in (item.get("collectBoxDetailShopList") or [])
                ]
                print("linked shops:", shops)
            print()

    rd = post_open(SHOP_DETAIL, {"detailId": TK_DETAIL_ID, "shopId": MX_SHOP_ID})
    print("=== MX shop detail ===")
    print(f"api: result={rd.get('result')} code={rd.get('code')} message={rd.get('message') or ''}")
    data = rd.get("data") or {}
    info = data.get("shopCollectItemInfo") or {}
    print(f"claimToShopIds: {data.get('claimToShopIds')}")
    print(f"title: {(info.get('title') or '')[:100]}")
    print(f"cid: {info.get('cid')}")
    for k, v in (info.get("skuMap") or {}).items():
        print(
            f"sku key={k} price={v.get('price')} weight={v.get('weight')} "
            f"stock={v.get('stock')} wh={v.get('shopIdToWarehouseIdAndStockMap')}"
        )

    # keyword search by product id if supported
    for kw in [str(TK_DETAIL_ID), PRODUCT_ID, "Arch Niche"]:
        r = post_open(
            LIST_PATH,
            {
                "pageNo": 1,
                "pageSize": 10,
                "filter": {"status": "published", "sourceItemIdKeyword": kw},
            },
        )
        hits = (r.get("data") or {}).get("detailList") or []
        if hits:
            print(f"\nkeyword published search '{kw}': {len(hits)} hit(s)")
            for h in hits[:3]:
                print(
                    f"  id={h.get('collectBoxDetailId')} "
                    f"shops={[s.get('shopId') for s in (h.get('collectBoxDetailShopList') or [])]}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
