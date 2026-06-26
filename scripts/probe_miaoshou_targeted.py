"""Targeted probe for Miaoshou shop product list API."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.miaoshou.client import post_open

B = {"platform": "tiktok", "site": "GB", "shopId": 10204699, "pageNo": 1, "pageSize": 5}
paths = [
    "/open/v1/product/shop/shop/get_item_list",
    "/open/v1/product/shop/shop/get_shop_item_list",
    "/open/v1/product/shop/shop/get_product_page",
    "/open/v1/product/shop/shop/get_product_page_list",
    "/open/v1/product/shop/shop/get_shop_product_page_list",
    "/open/v1/product/shop/shop/get_manage_product_page_list",
    "/open/v1/product/shop/product/get_shop_product_list",
    "/open/v1/product/shop/product/get_online_product_list",
    "/open/v1/product/shopData/get_list",
    "/open/v1/product/shopData/get_shop_product_list",
    "/open/v1/product/common/collectBox/get_collect_box_list",
    "/open/v1/product/commonCollectBox/get_collect_box_list",
    "/open/v1/product/tk/collectBox/get_collect_box_list",
    "/open/v1/product/tk/collect_box/get_collect_box_list",
]
for p in paths:
    r = post_open(p, B)
    if r.get("code") != "routeNotFound":
        print(p, json.dumps(r, ensure_ascii=False)[:500])
