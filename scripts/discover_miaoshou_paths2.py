"use strict";
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.miaoshou.client import post_open

B = {"platform": "tiktok", "site": "GB", "shopId": 10204699, "pageNo": 1, "pageSize": 5}
paths = [
    "/open/v1/product/tk/collectBox/getCollectBoxList",
    "/open/v1/product/tk/collectBox/getList",
    "/open/v1/product/tkCollectBox/getCollectBoxList",
    "/open/v1/product/tkCollectBox/getList",
    "/open/v1/product/common/collectBox/getList",
    "/open/v1/product/common/collectBox/getCollectBoxList",
    "/open/v1/product/commonCollectBox/getList",
    "/open/v1/product/online/product/getList",
    "/open/v1/product/online/product/get_list",
    "/open/v1/product/onlineProduct/getList",
    "/open/v1/product/shop/online/getList",
    "/open/v1/product/shop/onlineProduct/getList",
    "/open/v1/product/shop/product/getList",
    "/open/v1/product/shop/product/get_list",
    "/open/v1/product/tk/onlineProduct/getList",
    "/open/v1/product/tk/product/getList",
    "/open/v1/product/tk/product/get_online_list",
    "/open/v1/product/tk/shopProduct/getList",
    "/open/v1/product/tk/shopProduct/get_list",
    "/open/v1/product/tk/manage/getOnlineList",
]
for p in paths:
    r = post_open(p, B)
    c = r.get("code")
    if c != "routeNotFound":
        print("HIT", p, c, json.dumps(r, ensure_ascii=False)[:1000])
