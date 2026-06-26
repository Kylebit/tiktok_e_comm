"""Try likely paths for api-446814591 获取采集箱列表."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.miaoshou.client import post_open

B = {"platform": "tiktok", "site": "GB", "shopId": 10204699, "pageNo": 1, "pageSize": 5}
paths = [
    "/open/v1/product/tk/collectBox/get_list",
    "/open/v1/product/tk/collectBox/get_collect_box_list",
    "/open/v1/product/tk/collect_box/get_list",
    "/open/v1/product/tkCollectBox/get_list",
    "/open/v1/product/tkCollectBox/get_collect_box_list",
    "/open/v1/product/common/collectBox/get_list",
    "/open/v1/product/commonCollectBox/get_list",
    "/open/v1/product/common/collectBox/get_collect_box_list",
    "/open/v1/product/shop/shop/get_shop_data_list",
    "/open/v1/product/shop/data/get_list",
    "/open/v1/product/shop/data/get_shop_data_list",
]
for p in paths:
    r = post_open(p, B)
    if r.get("code") != "routeNotFound":
        print("HIT", p)
        print(json.dumps(r, ensure_ascii=False)[:1200])
