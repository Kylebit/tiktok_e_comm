"""Brute probe Miaoshou /open/v1/product/* paths for GB shop products."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open

SITE, PLATFORM = "GB", "tiktok"
SHOP_ID = 10204699
BODY = {"platform": PLATFORM, "site": SITE, "shopId": SHOP_ID, "pageNo": 1, "pageSize": 5}

modules = [
    "shop/shop", "shop/product", "shop/item", "shop/manage", "shop/online", "shop/data",
    "tk/collectBox", "tk/collect_box", "tkCollectBox", "collectBox/tk",
    "shop", "product/shop",
]
actions = [
    "get_list", "get_product_list", "get_shop_product_list", "get_shop_data_list",
    "get_data_list", "get_item_list", "get_goods_list", "get_sku_list",
    "get_online_list", "get_online_product_list", "get_manage_list",
    "get_collect_box_list", "get_publish_list", "get_published_list",
    "query_list", "search_list", "get_shop_item_list", "get_product_data_list",
    "get_shop_goods_list", "get_shop_sku_list", "get_shop_online_list",
]

hits = []
for mod in modules:
    for act in actions:
        path = f"/open/v1/product/{mod}/{act}"
        resp = post_open(path, BODY)
        code = resp.get("code")
        if code == "routeNotFound":
            continue
        hits.append((path, resp))
        print("HIT", path, json.dumps(resp, ensure_ascii=False)[:400])

print(f"\ntotal hits: {len(hits)}")
