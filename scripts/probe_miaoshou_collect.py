"""Probe TK collect box and shop list variants."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open

SITE, PLATFORM = "GB", "tiktok"
SHOP_ID = 10204699

paths_bodies = [
    ("/open/v1/product/commonCollectBox/get_list", {"platform": PLATFORM, "site": SITE, "pageNo": 1, "pageSize": 10}),
    ("/open/v1/product/common/collectBox/get_list", {"platform": PLATFORM, "site": SITE, "pageNo": 1, "pageSize": 10}),
    ("/open/v1/product/common_collect_box/get_list", {"platform": PLATFORM, "site": SITE, "pageNo": 1, "pageSize": 10}),
    ("/open/v1/product/tk/collectBox/get_collect_box_list", {"platform": PLATFORM, "site": SITE, "shopId": SHOP_ID, "pageNo": 1, "pageSize": 10}),
    ("/open/v1/product/tk/collectBox/get_list", {"platform": PLATFORM, "site": SITE, "shopId": SHOP_ID, "pageNo": 1, "pageSize": 10}),
    ("/open/v1/product/shop/shop/get_shop_item_data_list", {"platform": PLATFORM, "site": SITE, "shopId": SHOP_ID, "pageNo": 1, "pageSize": 10}),
    ("/open/v1/product/shop/shop/get_shop_sku_data_list", {"platform": PLATFORM, "site": SITE, "shopId": SHOP_ID, "pageNo": 1, "pageSize": 10}),
    ("/open/v1/product/shop/shop/get_shop_list", {"platform": PLATFORM, "site": SITE, "shopId": SHOP_ID, "pageNo": 1, "pageSize": 10}),
]

for path, body in paths_bodies:
    resp = post_open(path, body)
    if resp.get("code") == "routeNotFound":
        continue
    print(path, "->", json.dumps(resp, ensure_ascii=False)[:600])
